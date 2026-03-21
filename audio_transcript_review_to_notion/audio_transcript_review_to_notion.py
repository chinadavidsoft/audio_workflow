#!/usr/bin/env python3
"""
将英语口语音频转写为文本，生成 AI 语法点评与重写，并写入 Notion 主库 relation 列。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - 运行时依赖检查
    OpenAI = None  # type: ignore[assignment]


SUPPORTED_EXTENSIONS = {".mp3", ".m4a"}
DEFAULT_REVIEW_MODEL = "gpt-5-mini"
NOTION_VERSION = "2022-06-28"
NOTION_BASE_URL = "https://api.notion.com/v1"
NOTION_RICH_TEXT_LIMIT = 1800

MAIN_TRANSCRIPT_PROPERTY = "转录"
MAIN_GRAMMAR_RELATION_PROPERTY = "ai语法点评"
MAIN_REWRITE_RELATION_PROPERTY = "ai重写"
MAIN_SPEAKING_RELATION_PROPERTY = "ai口语建议"
DETAIL_CONTENT_PROPERTY = "内容"
UPDATED_AT_PROPERTY = "更新时间"

GRAMMAR_TITLE = "AI语法点评"
REWRITE_TITLE = "AI重写"


class NotionAPIError(RuntimeError):
    """Notion API 返回错误时抛出。"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Transcribe an MP3/M4A file with local Whisper, generate AI grammar feedback "
            "and rewrite, save local Markdown files, and upsert Notion relations."
        )
    )
    parser.add_argument("--audio", required=True, type=Path, help="Path to input audio file")
    parser.add_argument(
        "--database-id",
        required=True,
        help="Notion main database ID for the audio records table",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_REVIEW_MODEL,
        help=f"Model used for feedback generation (default: {DEFAULT_REVIEW_MODEL})",
    )
    return parser.parse_args()


def fail(message: str, exit_code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(exit_code)


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        fail(f"Missing required environment variable: {name}")
    return value


def validate_audio_path(audio_path: Path) -> Path:
    candidate = audio_path.expanduser().resolve()
    if not candidate.exists():
        fail(f"Audio file does not exist: {candidate}")
    if not candidate.is_file():
        fail(f"Audio path is not a file: {candidate}")
    if candidate.suffix.lower() not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        fail(f"Unsupported file extension: {candidate.suffix}. Supported: {supported}")
    return candidate


def build_openai_client(api_key: str, base_url: str | None = None) -> OpenAI:
    if OpenAI is None:
        fail("Python package 'openai' is not installed. Install with: pip install openai")
    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=api_key)


def transcribe_audio_local(audio_path: Path) -> str:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "Local transcription requires faster-whisper. Install with: pip install faster-whisper"
        ) from exc

    model_name = os.environ.get("LOCAL_WHISPER_MODEL", "small")
    try:
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        segments, _ = model.transcribe(str(audio_path), language="en", vad_filter=True)
    except Exception as exc:
        raise RuntimeError(f"Local transcription failed: {exc}") from exc

    text_parts = [segment.text.strip() for segment in segments if segment.text.strip()]
    merged = " ".join(text_parts).strip()
    if not merged:
        raise RuntimeError("Local transcription returned empty text")
    return merged


def format_transcript_markdown(transcript_text: str) -> str:
    normalized = re.sub(r"\s+", " ", transcript_text).strip()
    if not normalized:
        fail("Transcription text is empty after normalization")

    sentences = re.split(r"(?<=[.!?])\s+", normalized)
    if len(sentences) <= 1:
        return normalized

    paragraphs: list[str] = []
    for index in range(0, len(sentences), 3):
        group = " ".join(sentences[index : index + 3]).strip()
        if group:
            paragraphs.append(group)
    return "\n\n".join(paragraphs) if paragraphs else normalized


def token_sequence(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9']+", text.lower())


def assert_transcript_fidelity(original_text: str, formatted_text: str) -> None:
    if token_sequence(original_text) != token_sequence(formatted_text):
        fail("Transcript formatting changed token sequence. Formatting may adjust layout only.")


def request_markdown_text(client: OpenAI, model: str, system_prompt: str, user_prompt: str) -> str:
    response_error: Exception | None = None
    try:
        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
            ],
        )
        text = getattr(response, "output_text", "")
        if isinstance(text, str) and text.strip():
            return text.strip()
        parts = flatten_response_text(getattr(response, "output", []) or [])
        if parts:
            return "\n\n".join(parts)
    except Exception as exc:
        response_error = exc

    try:
        chat_response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = chat_response.choices[0].message.content
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text", "")
                    if text:
                        parts.append(str(text).strip())
            if parts:
                return "\n\n".join(parts)
    except Exception as chat_error:
        if response_error is not None:
            fail(f"Feedback generation failed: responses={response_error}; chat={chat_error}")
        fail(f"Feedback generation failed: {chat_error}")

    if response_error is not None:
        fail(f"Feedback generation returned empty content after fallback: {response_error}")
    fail("Feedback generation returned empty content")


def request_grammar_review(client: OpenAI, model: str, transcript_markdown: str) -> str:
    system_prompt = "You are an English speaking coach. Be candid, direct, and useful."
    user_prompt = (
        "Review the learner's spoken English transcript.\n"
        "Output Markdown only.\n"
        "Cover grammar, word choice, naturalness, and clarity.\n"
        "Quote concrete examples from the transcript.\n"
        "Each key point must be English first, then Chinese.\n\n"
        "Transcript:\n"
        f"{transcript_markdown}"
    )
    return request_markdown_text(client, model, system_prompt, user_prompt)


def request_rewrite(client: OpenAI, model: str, transcript_markdown: str) -> str:
    system_prompt = "You are an English speaking coach. Write concise, natural spoken English."
    user_prompt = (
        "Rewrite the learner's spoken English transcript.\n"
        "Output Markdown only.\n"
        "Provide one full improved rewrite in natural spoken English.\n"
        "After the rewrite, add a concise Chinese explanation of the main improvements.\n\n"
        "Transcript:\n"
        f"{transcript_markdown}"
    )
    return request_markdown_text(client, model, system_prompt, user_prompt)


def flatten_response_text(response_output: list) -> list[str]:
    parts: list[str] = []
    for item in response_output:
        contents = item.get("content", []) if isinstance(item, dict) else getattr(item, "content", [])
        for content in contents or []:
            candidate = ""
            if isinstance(content, dict):
                text_field = content.get("text", "")
                if isinstance(text_field, str):
                    candidate = text_field
                elif isinstance(text_field, dict):
                    candidate = str(text_field.get("value", "")).strip()
            else:
                text_field = getattr(content, "text", "")
                if isinstance(text_field, str):
                    candidate = text_field
                elif hasattr(text_field, "value"):
                    candidate = str(text_field.value).strip()
            if candidate:
                parts.append(candidate.strip())
    return parts


def build_feedback_markdown(grammar_review: str, rewrite: str) -> str:
    return f"# {GRAMMAR_TITLE}\n\n{grammar_review.strip()}\n\n# {REWRITE_TITLE}\n\n{rewrite.strip()}"


def write_markdown(path: Path, content: str) -> None:
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def notion_request(method: str, endpoint: str, token: str, payload: dict | None = None) -> dict:
    url = f"{NOTION_BASE_URL}{endpoint}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(url=url, method=method, headers=headers, data=data)

    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise NotionAPIError(f"HTTP {exc.code} on {endpoint}: {body}") from exc
    except URLError as exc:
        raise NotionAPIError(f"Network error on {endpoint}: {exc}") from exc


def get_database_schema(database_id: str, notion_token: str) -> dict[str, dict]:
    data = notion_request("GET", f"/databases/{quote(database_id)}", notion_token)
    properties = data.get("properties")
    if not isinstance(properties, dict) or not properties:
        raise NotionAPIError("Database schema is empty or unavailable")
    return properties


def get_title_property_name(database_properties: dict[str, dict]) -> str:
    for name, meta in database_properties.items():
        if isinstance(meta, dict) and meta.get("type") == "title":
            return name
    raise NotionAPIError("Database is missing a title property")


def require_property(database_properties: dict[str, dict], property_name: str, expected_types: set[str]) -> dict:
    meta = database_properties.get(property_name)
    if not isinstance(meta, dict):
        fail(f"Notion database is missing required property: {property_name}")
    actual_type = str(meta.get("type", "")).strip()
    if actual_type not in expected_types:
        expected = ", ".join(sorted(expected_types))
        fail(
            f"Notion property {property_name} has unsupported type {actual_type}. "
            f"Expected one of: {expected}"
        )
    return meta


def get_relation_database_id(database_properties: dict[str, dict], property_name: str) -> str:
    meta = require_property(database_properties, property_name, {"relation"})
    relation_meta = meta.get("relation")
    if not isinstance(relation_meta, dict):
        fail(f"Relation property {property_name} is missing relation metadata")
    database_id = str(relation_meta.get("database_id", "")).strip()
    if not database_id:
        fail(f"Relation property {property_name} is missing target database id")
    return database_id


def validate_detail_database_schema(database_properties: dict[str, dict], label: str) -> None:
    get_title_property_name(database_properties)
    require_property(database_properties, DETAIL_CONTENT_PROPERTY, {"rich_text"})
    updated_at_meta = database_properties.get(UPDATED_AT_PROPERTY)
    if updated_at_meta is not None and str(updated_at_meta.get("type", "")).strip() != "date":
        fail(f"{label} database property {UPDATED_AT_PROPERTY} must be a date if present")


def rich_text_chunks(text: str) -> list[dict]:
    if not text:
        return [{"type": "text", "text": {"content": " "}}]
    chunks = []
    for start in range(0, len(text), NOTION_RICH_TEXT_LIMIT):
        segment = text[start : start + NOTION_RICH_TEXT_LIMIT]
        chunks.append({"type": "text", "text": {"content": segment}})
    return chunks


def query_existing_pages(
    database_id: str,
    title_property: str,
    title_value: str,
    notion_token: str,
) -> list[dict]:
    endpoint = f"/databases/{quote(database_id)}/query"
    start_cursor: str | None = None
    results: list[dict] = []

    while True:
        payload = {
            "filter": {
                "property": title_property,
                "title": {"equals": title_value},
            },
            "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}],
            "page_size": 100,
        }
        if start_cursor:
            payload["start_cursor"] = start_cursor

        data = notion_request("POST", endpoint, notion_token, payload=payload)
        results.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
        if not start_cursor:
            break

    return results


def create_database_page(database_id: str, properties: dict, notion_token: str) -> dict:
    return notion_request(
        "POST",
        "/pages",
        notion_token,
        payload={"parent": {"database_id": database_id}, "properties": properties},
    )


def update_database_page(page_id: str, properties: dict, notion_token: str) -> dict:
    return notion_request("PATCH", f"/pages/{quote(page_id)}", notion_token, payload={"properties": properties})


def make_detail_database_properties(
    database_properties: dict[str, dict],
    audio_path: Path,
    content_markdown: str,
    processed_at_iso: str,
) -> dict:
    title_property = get_title_property_name(database_properties)
    properties: dict[str, dict] = {
        title_property: {
            "title": [{"type": "text", "text": {"content": audio_path.name}}],
        },
        DETAIL_CONTENT_PROPERTY: {"rich_text": rich_text_chunks(content_markdown)},
    }
    updated_at_meta = database_properties.get(UPDATED_AT_PROPERTY)
    if isinstance(updated_at_meta, dict) and str(updated_at_meta.get("type", "")).strip() == "date":
        properties[UPDATED_AT_PROPERTY] = {"date": {"start": processed_at_iso}}
    return properties


def make_main_database_properties(
    database_properties: dict[str, dict],
    audio_path: Path,
    processed_at_iso: str,
    transcript_page_id: str,
    grammar_page_id: str,
    rewrite_page_id: str,
) -> dict:
    title_property = get_title_property_name(database_properties)
    require_property(database_properties, MAIN_TRANSCRIPT_PROPERTY, {"relation"})
    require_property(database_properties, MAIN_GRAMMAR_RELATION_PROPERTY, {"relation"})
    require_property(database_properties, MAIN_REWRITE_RELATION_PROPERTY, {"relation"})
    require_property(database_properties, MAIN_SPEAKING_RELATION_PROPERTY, {"relation"})

    properties: dict[str, dict] = {
        title_property: {
            "title": [{"type": "text", "text": {"content": audio_path.name}}],
        },
        MAIN_TRANSCRIPT_PROPERTY: {"relation": [{"id": transcript_page_id}]},
        MAIN_GRAMMAR_RELATION_PROPERTY: {"relation": [{"id": grammar_page_id}]},
        MAIN_REWRITE_RELATION_PROPERTY: {"relation": [{"id": rewrite_page_id}]},
    }
    updated_at_meta = database_properties.get(UPDATED_AT_PROPERTY)
    if isinstance(updated_at_meta, dict) and str(updated_at_meta.get("type", "")).strip() == "date":
        properties[UPDATED_AT_PROPERTY] = {"date": {"start": processed_at_iso}}
    return properties


def upsert_database_page(
    database_id: str,
    title_value: str,
    properties: dict,
    notion_token: str,
    database_properties: dict[str, dict] | None = None,
) -> tuple[str, str, int]:
    schema = database_properties or get_database_schema(database_id, notion_token)
    title_property = get_title_property_name(schema)
    existing_pages = query_existing_pages(database_id, title_property, title_value, notion_token)

    page_id = ""
    mode = "create"
    if existing_pages:
        page_id = str(existing_pages[0].get("id", "")).strip()
        if not page_id:
            fail("Matched existing database entry but Notion response is missing page id")
        mode = "update"
        update_database_page(page_id, properties, notion_token)
    else:
        created = create_database_page(database_id, properties, notion_token)
        page_id = str(created.get("id", "")).strip()
        if not page_id:
            fail("Created database entry but Notion response is missing page id")

    return page_id, mode, len(existing_pages)


def main() -> None:
    args = parse_args()
    audio_path = validate_audio_path(args.audio)

    api_key = require_env("API_KEY")
    api_base_url = os.environ.get("API_BASE_URL", "").strip() or None
    notion_api_key = require_env("NOTION_API_KEY")
    client = build_openai_client(api_key, api_base_url)

    print(f"[1/6] Transcribing audio locally: {audio_path}")
    try:
        transcript_raw = transcribe_audio_local(audio_path)
    except RuntimeError as exc:
        fail(str(exc))

    print("[2/6] Formatting transcript Markdown")
    transcript_markdown = format_transcript_markdown(transcript_raw)
    assert_transcript_fidelity(transcript_raw, transcript_markdown)

    transcript_path = audio_path.with_name(f"{audio_path.stem} - Transcript.md")
    write_markdown(transcript_path, transcript_markdown)
    print(f"[3/6] Saved transcript Markdown: {transcript_path}")

    print(f"[4/6] Generating grammar feedback and rewrite with model: {args.model}")
    grammar_review = request_grammar_review(client, args.model, transcript_markdown)
    rewrite = request_rewrite(client, args.model, transcript_markdown)
    feedback_markdown = build_feedback_markdown(grammar_review, rewrite)
    feedback_path = audio_path.with_name(f"{audio_path.stem} - Feedback.md")
    write_markdown(feedback_path, feedback_markdown)
    print(f"[5/6] Saved feedback Markdown: {feedback_path}")

    processed_at_iso = (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )

    try:
        main_database_properties = get_database_schema(args.database_id, notion_api_key)
        transcript_database_id = get_relation_database_id(main_database_properties, MAIN_TRANSCRIPT_PROPERTY)
        grammar_database_id = get_relation_database_id(main_database_properties, MAIN_GRAMMAR_RELATION_PROPERTY)
        rewrite_database_id = get_relation_database_id(main_database_properties, MAIN_REWRITE_RELATION_PROPERTY)
        speaking_database_id = get_relation_database_id(main_database_properties, MAIN_SPEAKING_RELATION_PROPERTY)

        transcript_database_properties = get_database_schema(transcript_database_id, notion_api_key)
        grammar_database_properties = get_database_schema(grammar_database_id, notion_api_key)
        rewrite_database_properties = get_database_schema(rewrite_database_id, notion_api_key)
        speaking_database_properties = get_database_schema(speaking_database_id, notion_api_key)

        validate_detail_database_schema(transcript_database_properties, "转录")
        validate_detail_database_schema(grammar_database_properties, "AI语法点评")
        validate_detail_database_schema(rewrite_database_properties, "AI重写")
        validate_detail_database_schema(speaking_database_properties, "AI口语建议")

        transcript_properties = make_detail_database_properties(
            transcript_database_properties,
            audio_path,
            transcript_markdown,
            processed_at_iso,
        )
        transcript_page_id, transcript_mode, transcript_duplicates = upsert_database_page(
            transcript_database_id,
            audio_path.name,
            transcript_properties,
            notion_api_key,
            transcript_database_properties,
        )

        grammar_properties = make_detail_database_properties(
            grammar_database_properties,
            audio_path,
            grammar_review,
            processed_at_iso,
        )
        grammar_page_id, grammar_mode, grammar_duplicates = upsert_database_page(
            grammar_database_id,
            audio_path.name,
            grammar_properties,
            notion_api_key,
            grammar_database_properties,
        )

        rewrite_properties = make_detail_database_properties(
            rewrite_database_properties,
            audio_path,
            rewrite,
            processed_at_iso,
        )
        rewrite_page_id, rewrite_mode, rewrite_duplicates = upsert_database_page(
            rewrite_database_id,
            audio_path.name,
            rewrite_properties,
            notion_api_key,
            rewrite_database_properties,
        )

        main_properties = make_main_database_properties(
            main_database_properties,
            audio_path,
            processed_at_iso,
            transcript_page_id,
            grammar_page_id,
            rewrite_page_id,
        )
        main_page_id, main_mode, main_duplicates = upsert_database_page(
            args.database_id,
            audio_path.name,
            main_properties,
            notion_api_key,
            main_database_properties,
        )
    except NotionAPIError as exc:
        fail(
            "Notion database upsert failed after local Markdown files were saved. "
            f"Retry upload later. Details: {exc}"
        )

    if main_duplicates > 1:
        print(f"[WARN] Main record matched {main_duplicates} duplicate records.")
    if transcript_duplicates > 1:
        print(f"[WARN] Transcript detail matched {transcript_duplicates} duplicate records.")
    if grammar_duplicates > 1:
        print(f"[WARN] Grammar detail matched {grammar_duplicates} duplicate records.")
    if rewrite_duplicates > 1:
        print(f"[WARN] Rewrite detail matched {rewrite_duplicates} duplicate records.")

    print("[6/6] Notion relation upsert completed")
    print(f"  Main record: {main_mode} {main_page_id}")
    print(f"  Transcript detail: {transcript_mode} {transcript_page_id}")
    print(f"  Grammar detail: {grammar_mode} {grammar_page_id}")
    print(f"  Rewrite detail: {rewrite_mode} {rewrite_page_id}")


if __name__ == "__main__":
    main()
