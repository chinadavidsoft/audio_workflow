#!/usr/bin/env python3
"""
将英语口语音频转写为文本，生成中英双语点评，并把两份结果保存到本地 Markdown
以及 Notion 子页面。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
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
DEFAULT_TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"
NOTION_VERSION = "2022-06-28"
NOTION_BASE_URL = "https://api.notion.com/v1"
NOTION_RICH_TEXT_LIMIT = 1800
NOTION_BLOCKS_PER_REQUEST = 100


class NotionAPIError(RuntimeError):
    """Notion API 返回错误时抛出。"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Transcribe an MP3/M4A file, generate bilingual English-expression "
            "feedback, save local Markdown files, and upload both to Notion."
        )
    )
    parser.add_argument(
        "--audio",
        required=True,
        type=Path,
        help="Path to input audio file (.mp3 or .m4a)",
    )
    parser.add_argument(
        "--parent-page-id",
        required=True,
        help="Notion parent page ID where two child pages will be created",
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
    value = os.environ.get(name)
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


def transcribe_audio(client: OpenAI, audio_path: Path) -> str:
    try:
        with audio_path.open("rb") as audio_file:
            result = client.audio.transcriptions.create(
                model=DEFAULT_TRANSCRIBE_MODEL,
                file=audio_file,
                language="en",
                response_format="text",
            )
    except Exception as exc:
        raise RuntimeError(f"Audio transcription failed: {exc}") from exc

    if isinstance(result, str):
        text = result.strip()
    elif isinstance(result, dict):
        text = str(result.get("text", "")).strip()
    elif hasattr(result, "text"):
        text = str(result.text).strip()
    else:
        text = str(result).strip()

    if not text:
        raise RuntimeError("Transcription returned empty text")
    return text


def transcribe_audio_local(audio_path: Path) -> str:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "Local fallback requires faster-whisper. Install with: pip install faster-whisper"
        ) from exc

    model_name = os.environ.get("LOCAL_WHISPER_MODEL", "small")
    try:
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        segments, _ = model.transcribe(
            str(audio_path),
            language="en",
            vad_filter=True,
        )
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

    # 仅做可读性排版，不改写词句内容。
    sentences = re.split(r"(?<=[.!?])\s+", normalized)
    if len(sentences) <= 1:
        return normalized

    paragraphs: list[str] = []
    step = 3
    for index in range(0, len(sentences), step):
        group = " ".join(sentences[index : index + step]).strip()
        if group:
            paragraphs.append(group)
    return "\n\n".join(paragraphs) if paragraphs else normalized


def token_sequence(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9']+", text.lower())


def assert_transcript_fidelity(original_text: str, formatted_text: str) -> None:
    original_tokens = token_sequence(original_text)
    formatted_tokens = token_sequence(formatted_text)
    if original_tokens != formatted_tokens:
        fail(
            "Transcript formatting changed token sequence. "
            "Formatting is allowed to adjust layout only."
        )


def generate_feedback_markdown(client: OpenAI, model: str, transcript_markdown: str) -> str:
    system_prompt = (
        "You are an English speaking coach. Be candid and direct, while staying "
        "constructive and actionable."
    )
    user_prompt = (
        "Review the learner's spoken English transcript.\n"
        "Requirements:\n"
        "1) Cover grammar, word choice, naturalness, and clarity.\n"
        "2) Point out concrete issues with examples from the transcript.\n"
        "3) Provide better alternatives and one improved rewrite.\n"
        "4) Output in bilingual format: each key point in English first, then Chinese.\n"
        "5) Return Markdown.\n\n"
        "Transcript:\n"
        f"{transcript_markdown}"
    )

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

        output_parts: list[str] = []
        response_output = getattr(response, "output", []) or []
        for item in response_output:
            if isinstance(item, dict):
                contents = item.get("content", []) or []
            else:
                contents = getattr(item, "content", []) or []

            for content in contents:
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
                    output_parts.append(candidate.strip())

        merged = "\n\n".join(part for part in output_parts if part)
        if merged:
            return merged
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
            merged = "\n\n".join(part for part in parts if part)
            if merged:
                return merged
    except Exception as chat_error:
        if response_error is not None:
            fail(f"Feedback generation failed: responses={response_error}; chat={chat_error}")
        fail(f"Feedback generation failed: {chat_error}")

    if response_error is not None:
        fail(f"Feedback generation returned empty content after fallback: {response_error}")
    fail("Feedback generation returned empty content")


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


def iter_notion_children(parent_page_id: str, notion_token: str) -> Iterable[dict]:
    start_cursor: str | None = None
    while True:
        endpoint = f"/blocks/{quote(parent_page_id)}/children?page_size=100"
        if start_cursor:
            endpoint += f"&start_cursor={quote(start_cursor)}"
        data = notion_request("GET", endpoint, notion_token)
        for item in data.get("results", []):
            yield item
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
        if not start_cursor:
            break


def list_child_page_titles(parent_page_id: str, notion_token: str) -> set[str]:
    titles: set[str] = set()
    for item in iter_notion_children(parent_page_id, notion_token):
        if item.get("type") == "child_page":
            title = item.get("child_page", {}).get("title", "").strip()
            if title:
                titles.add(title)
    return titles


def make_unique_title(base_title: str, existing_titles: set[str]) -> str:
    if base_title not in existing_titles:
        return base_title
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = f"{base_title} ({timestamp})"
    counter = 1
    while candidate in existing_titles:
        counter += 1
        candidate = f"{base_title} ({timestamp}-{counter})"
    return candidate


def create_child_page(parent_page_id: str, title: str, notion_token: str) -> str:
    payload = {
        "parent": {"page_id": parent_page_id},
        "properties": {
            "title": {
                "title": [
                    {"type": "text", "text": {"content": title}},
                ]
            }
        },
    }
    data = notion_request("POST", "/pages", notion_token, payload=payload)
    page_id = data.get("id")
    if not page_id:
        raise NotionAPIError(f"Could not create Notion page for title: {title}")
    return page_id


def markdown_to_notion_blocks(markdown: str) -> list[dict]:
    blocks: list[dict] = []
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("### "):
            blocks.append(build_text_block("heading_3", stripped[4:]))
        elif stripped.startswith("## "):
            blocks.append(build_text_block("heading_2", stripped[3:]))
        elif stripped.startswith("# "):
            blocks.append(build_text_block("heading_1", stripped[2:]))
        elif stripped.startswith("- "):
            blocks.append(build_text_block("bulleted_list_item", stripped[2:]))
        else:
            blocks.append(build_text_block("paragraph", line))

    if not blocks:
        blocks.append(build_text_block("paragraph", "(empty)"))
    return blocks


def build_text_block(block_type: str, text: str) -> dict:
    return {
        "object": "block",
        "type": block_type,
        block_type: {"rich_text": rich_text_chunks(text)},
    }


def rich_text_chunks(text: str) -> list[dict]:
    if not text:
        return [{"type": "text", "text": {"content": " "}}]
    chunks = []
    for start in range(0, len(text), NOTION_RICH_TEXT_LIMIT):
        segment = text[start : start + NOTION_RICH_TEXT_LIMIT]
        chunks.append({"type": "text", "text": {"content": segment}})
    return chunks


def append_blocks(page_id: str, blocks: list[dict], notion_token: str) -> None:
    for index in range(0, len(blocks), NOTION_BLOCKS_PER_REQUEST):
        batch = blocks[index : index + NOTION_BLOCKS_PER_REQUEST]
        payload = {"children": batch}
        notion_request("PATCH", f"/blocks/{quote(page_id)}/children", notion_token, payload=payload)


def main() -> None:
    args = parse_args()
    audio_path = validate_audio_path(args.audio)

    api_key = require_env("API_KEY")
    api_base_url = os.environ.get("API_BASE_URL", "").strip() or None
    notion_api_key = require_env("NOTION_API_KEY")
    client = build_openai_client(api_key, api_base_url)

    print(f"[1/6] Transcribing audio: {audio_path}")
    try:
        transcript_raw = transcribe_audio(client, audio_path)
    except RuntimeError as remote_error:
        print(f"[1/6] Remote transcription failed, fallback to local Whisper: {remote_error}")
        try:
            transcript_raw = transcribe_audio_local(audio_path)
        except RuntimeError as local_error:
            fail(f"{remote_error}; {local_error}")

    print("[2/6] Formatting transcript Markdown")
    transcript_markdown = format_transcript_markdown(transcript_raw)
    assert_transcript_fidelity(transcript_raw, transcript_markdown)

    transcript_path = audio_path.with_name(f"{audio_path.stem} - Transcript.md")
    write_markdown(transcript_path, transcript_markdown)
    print(f"[3/6] Saved transcript Markdown: {transcript_path}")

    print(f"[4/6] Generating bilingual feedback with model: {args.model}")
    feedback_markdown = generate_feedback_markdown(client, args.model, transcript_markdown)
    feedback_path = audio_path.with_name(f"{audio_path.stem} - Feedback.md")
    write_markdown(feedback_path, feedback_markdown)
    print(f"[5/6] Saved feedback Markdown: {feedback_path}")

    stem = audio_path.stem
    transcript_title_base = f"{stem} - Transcript"
    feedback_title_base = f"{stem} - Feedback"

    try:
        try:
            existing_titles = list_child_page_titles(args.parent_page_id, notion_api_key)
        except NotionAPIError as list_error:
            print(f"[WARN] Could not list existing child pages, continue without dedupe: {list_error}")
            existing_titles = set()
        transcript_title = make_unique_title(transcript_title_base, existing_titles)
        existing_titles.add(transcript_title)
        feedback_title = make_unique_title(feedback_title_base, existing_titles)

        transcript_page_id = create_child_page(args.parent_page_id, transcript_title, notion_api_key)
        append_blocks(
            transcript_page_id,
            markdown_to_notion_blocks(transcript_markdown),
            notion_api_key,
        )

        feedback_page_id = create_child_page(args.parent_page_id, feedback_title, notion_api_key)
        append_blocks(
            feedback_page_id,
            markdown_to_notion_blocks(feedback_markdown),
            notion_api_key,
        )
    except NotionAPIError as exc:
        fail(
            "Notion upload failed after local Markdown files were saved. "
            f"Retry upload later. Details: {exc}"
        )

    print("[6/6] Notion upload completed")
    print(f"  Transcript page: {transcript_title}")
    print(f"  Feedback page: {feedback_title}")


if __name__ == "__main__":
    main()
