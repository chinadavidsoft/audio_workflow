#!/usr/bin/env python3
"""
将 Transcript + Feedback 的 Markdown 结果 upsert 到 Notion 数据库。
去重键：Name 属性中的音频文件名。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from notion_markdown_converter import markdown_to_notion_blocks as convert_markdown_to_notion_blocks
except ImportError:
    convert_markdown_to_notion_blocks = None  # type: ignore[assignment]


NOTION_VERSION = "2022-06-28"
NOTION_BASE_URL = "https://api.notion.com/v1"
NOTION_RICH_TEXT_LIMIT = 1800
NOTION_BLOCKS_PER_REQUEST = 100
SUPPORTED_EXTENSIONS = {".mp3", ".m4a"}
ENGINE_NAME = "local_whisper+codex"
STATUS_NAME = "Done"


class NotionAPIError(RuntimeError):
    """Notion API 返回错误时抛出。"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将单个音频的 Transcript/Feedback 写入或更新到 Notion 数据库"
    )
    parser.add_argument("--audio", required=True, type=Path, help="输入音频文件路径")
    parser.add_argument(
        "--transcript-md",
        required=True,
        type=Path,
        help="Transcript Markdown 文件路径",
    )
    parser.add_argument(
        "--feedback-md",
        required=True,
        type=Path,
        help="Feedback Markdown 文件路径",
    )
    parser.add_argument("--database-id", required=True, help="Notion 数据库 ID")
    return parser.parse_args()


def fail(message: str, exit_code: int = 1) -> None:
    print(f"错误: {message}", file=sys.stderr)
    raise SystemExit(exit_code)


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        fail(f"缺少必填环境变量: {name}")
    return value


def validate_audio_path(audio_path: Path) -> Path:
    candidate = audio_path.expanduser().resolve()
    if not candidate.exists():
        fail(f"音频文件不存在: {candidate}")
    if not candidate.is_file():
        fail(f"音频路径不是文件: {candidate}")
    if candidate.suffix.lower() not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        fail(f"不支持的文件扩展名: {candidate.suffix}。支持: {supported}")
    return candidate


def read_markdown(path: Path, label: str) -> str:
    candidate = path.expanduser().resolve()
    if not candidate.exists():
        fail(f"{label} 文件不存在: {candidate}")
    if not candidate.is_file():
        fail(f"{label} 路径不是文件: {candidate}")
    content = candidate.read_text(encoding="utf-8").strip()
    if not content:
        fail(f"{label} 文件为空: {candidate}")
    return content


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


def rich_text_chunks(text: str) -> list[dict]:
    if not text:
        return [{"type": "text", "text": {"content": " "}}]
    chunks = []
    for start in range(0, len(text), NOTION_RICH_TEXT_LIMIT):
        segment = text[start : start + NOTION_RICH_TEXT_LIMIT]
        chunks.append({"type": "text", "text": {"content": segment}})
    return chunks


def make_properties(audio_path: Path, processed_at_iso: str) -> dict:
    return {
        "Name": {
            "title": [
                {"type": "text", "text": {"content": audio_path.name}},
            ]
        },
        "Audio Filename": {
            "rich_text": rich_text_chunks(audio_path.name),
        },
        "Audio Path": {
            "rich_text": rich_text_chunks(str(audio_path)),
        },
        "Processed At": {
            "date": {"start": processed_at_iso},
        },
        "Status": {
            "status": {"name": STATUS_NAME},
        },
        "Engine": {
            "select": {"name": ENGINE_NAME},
        },
    }


def markdown_to_notion_blocks(markdown: str) -> list[dict]:
    if convert_markdown_to_notion_blocks is None:
        fail("缺少 Markdown 解析依赖，请先安装: pip install markdown-it-py")
    return convert_markdown_to_notion_blocks(markdown, rich_text_limit=NOTION_RICH_TEXT_LIMIT)


def append_blocks(page_id: str, blocks: list[dict], notion_token: str) -> None:
    for index in range(0, len(blocks), NOTION_BLOCKS_PER_REQUEST):
        batch = blocks[index : index + NOTION_BLOCKS_PER_REQUEST]
        payload = {"children": batch}
        notion_request("PATCH", f"/blocks/{quote(page_id)}/children", notion_token, payload=payload)


def iter_block_children(parent_id: str, notion_token: str) -> Iterable[dict]:
    start_cursor: str | None = None
    while True:
        endpoint = f"/blocks/{quote(parent_id)}/children?page_size=100"
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


def clear_page_content(page_id: str, notion_token: str) -> int:
    archived_count = 0
    for block in iter_block_children(page_id, notion_token):
        block_id = str(block.get("id", "")).strip()
        if not block_id:
            continue
        notion_request("PATCH", f"/blocks/{quote(block_id)}", notion_token, payload={"archived": True})
        archived_count += 1
    return archived_count


def query_existing_pages(database_id: str, file_name: str, notion_token: str) -> list[dict]:
    endpoint = f"/databases/{quote(database_id)}/query"
    start_cursor: str | None = None
    results: list[dict] = []

    while True:
        payload = {
            "filter": {
                "property": "Name",
                "title": {"equals": file_name},
            },
            "sorts": [
                {
                    "timestamp": "last_edited_time",
                    "direction": "descending",
                }
            ],
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
    payload = {
        "parent": {"database_id": database_id},
        "properties": properties,
    }
    return notion_request("POST", "/pages", notion_token, payload=payload)


def update_database_page(page_id: str, properties: dict, notion_token: str) -> dict:
    return notion_request("PATCH", f"/pages/{quote(page_id)}", notion_token, payload={"properties": properties})


def build_combined_markdown(transcript: str, feedback: str) -> str:
    return (
        "# Transcript\n\n"
        f"{transcript.strip()}\n\n"
        "# Feedback\n\n"
        f"{feedback.strip()}"
    )


def main() -> None:
    args = parse_args()
    audio_path = validate_audio_path(args.audio)
    transcript_markdown = read_markdown(args.transcript_md, "Transcript")
    feedback_markdown = read_markdown(args.feedback_md, "Feedback")
    notion_token = require_env("NOTION_API_KEY")

    processed_at_iso = (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    properties = make_properties(audio_path, processed_at_iso)
    combined_markdown = build_combined_markdown(transcript_markdown, feedback_markdown)
    combined_blocks = markdown_to_notion_blocks(combined_markdown)

    try:
        existing_pages = query_existing_pages(args.database_id, audio_path.name, notion_token)
    except NotionAPIError as exc:
        fail(f"查询数据库现有记录失败: {exc}")

    page_id = ""
    mode = "create"

    if existing_pages:
        page_id = str(existing_pages[0].get("id", "")).strip()
        if not page_id:
            fail("命中已有记录，但 Notion 返回中缺少 page id")
        mode = "update"
        try:
            update_database_page(page_id, properties, notion_token)
        except NotionAPIError as exc:
            fail(f"更新已有页面属性失败: {exc}")
    else:
        try:
            created = create_database_page(args.database_id, properties, notion_token)
        except NotionAPIError as exc:
            fail(f"创建数据库页面失败: {exc}")
        page_id = str(created.get("id", "")).strip()
        if not page_id:
            fail("创建记录成功，但 Notion 返回中缺少 page id")

    try:
        removed_blocks = clear_page_content(page_id, notion_token)
        append_blocks(page_id, combined_blocks, notion_token)
    except NotionAPIError as exc:
        fail(f"替换页面正文失败: {exc}")

    if len(existing_pages) > 1:
        print(
            f"[警告] Name={audio_path.name} 命中 {len(existing_pages)} 条重复记录，"
            "已更新最后编辑的一条。"
        )

    print(f"[完成] 模式: {mode}")
    print(f"[完成] 页面 ID: {page_id}")
    print(f"[完成] 已归档正文块数: {removed_blocks}")
    print(f"[完成] 已追加正文块数: {len(combined_blocks)}")


if __name__ == "__main__":
    main()
