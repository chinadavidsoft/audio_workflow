#!/usr/bin/env python3
"""
Shared Markdown -> Notion blocks converter for common syntax.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from markdown_it import MarkdownIt
from markdown_it.token import Token


DEFAULT_RICH_TEXT_LIMIT = 1800

_EMPTY_ANNOTATIONS = {
    "bold": False,
    "italic": False,
    "strikethrough": False,
    "underline": False,
    "code": False,
    "color": "default",
}

_PARSER = MarkdownIt("commonmark")


@dataclass(frozen=True)
class InlineState:
    bold_depth: int = 0

    def with_strong_open(self) -> "InlineState":
        return InlineState(bold_depth=self.bold_depth + 1)

    def with_strong_close(self) -> "InlineState":
        return InlineState(bold_depth=max(0, self.bold_depth - 1))

    def annotations(self, code: bool = False) -> dict:
        return {
            **_EMPTY_ANNOTATIONS,
            "bold": self.bold_depth > 0,
            "code": code,
        }


def _rich_text_chunks(text: str, rich_text_limit: int, annotations: dict) -> list[dict]:
    if not text:
        return []
    chunks = []
    for start in range(0, len(text), rich_text_limit):
        segment = text[start : start + rich_text_limit]
        chunks.append(
            {
                "type": "text",
                "text": {"content": segment},
                "annotations": annotations,
            }
        )
    return chunks


def _inline_token_to_rich_text(children: Iterable[Token], rich_text_limit: int) -> list[dict]:
    rich_text: list[dict] = []
    state = InlineState()

    for child in children:
        if child.type == "strong_open":
            state = state.with_strong_open()
            continue
        if child.type == "strong_close":
            state = state.with_strong_close()
            continue
        if child.type == "code_inline":
            rich_text.extend(_rich_text_chunks(child.content, rich_text_limit, state.annotations(code=True)))
            continue
        if child.type in {"softbreak", "hardbreak"}:
            rich_text.extend(_rich_text_chunks("\n", rich_text_limit, state.annotations()))
            continue

        content = child.content
        if not content:
            continue
        rich_text.extend(_rich_text_chunks(content, rich_text_limit, state.annotations()))

    if not rich_text:
        return [{"type": "text", "text": {"content": " "}, "annotations": _EMPTY_ANNOTATIONS}]
    return rich_text


def _build_block(block_type: str, rich_text: list[dict]) -> dict:
    return {
        "object": "block",
        "type": block_type,
        block_type: {"rich_text": rich_text},
    }


def _extract_inline_rich_text(tokens: list[Token], start: int, rich_text_limit: int) -> tuple[list[dict], int]:
    idx = start
    while idx < len(tokens):
        token = tokens[idx]
        if token.type == "inline":
            return _inline_token_to_rich_text(token.children or [], rich_text_limit), idx
        if token.nesting < 0:
            break
        idx += 1
    return [{"type": "text", "text": {"content": " "}, "annotations": _EMPTY_ANNOTATIONS}], start


def markdown_to_notion_blocks(markdown: str, rich_text_limit: int = DEFAULT_RICH_TEXT_LIMIT) -> list[dict]:
    cleaned = markdown.strip()
    if not cleaned:
        return [_build_block("paragraph", [{"type": "text", "text": {"content": "(empty)"}, "annotations": _EMPTY_ANNOTATIONS}])]

    try:
        tokens = _PARSER.parse(cleaned)
    except Exception:
        fallback = [{"type": "text", "text": {"content": cleaned[:rich_text_limit]}, "annotations": _EMPTY_ANNOTATIONS}]
        return [_build_block("paragraph", fallback)]

    blocks: list[dict] = []
    list_stack: list[str] = []
    list_item_depth = 0
    idx = 0

    while idx < len(tokens):
        token = tokens[idx]

        if token.type == "bullet_list_open":
            list_stack.append("bulleted_list_item")
        elif token.type == "bullet_list_close":
            if list_stack:
                list_stack.pop()
        elif token.type == "ordered_list_open":
            list_stack.append("numbered_list_item")
        elif token.type == "ordered_list_close":
            if list_stack:
                list_stack.pop()
        elif token.type == "list_item_open":
            list_item_depth += 1
        elif token.type == "list_item_close":
            list_item_depth = max(0, list_item_depth - 1)
        elif token.type in {"heading_open", "paragraph_open"}:
            rich_text, inline_idx = _extract_inline_rich_text(tokens, idx + 1, rich_text_limit)
            if token.type == "heading_open":
                level = token.tag.strip().lower()
                block_type = {"h1": "heading_1", "h2": "heading_2"}.get(level, "heading_3")
            else:
                if list_item_depth > 0 and list_stack:
                    block_type = list_stack[-1]
                else:
                    block_type = "paragraph"
            blocks.append(_build_block(block_type, rich_text))
            idx = inline_idx
        elif token.type in {"fence", "code_block"}:
            annotations = {**_EMPTY_ANNOTATIONS, "code": True}
            blocks.append(_build_block("paragraph", _rich_text_chunks(token.content, rich_text_limit, annotations)))

        idx += 1

    if not blocks:
        return [_build_block("paragraph", [{"type": "text", "text": {"content": "(empty)"}, "annotations": _EMPTY_ANNOTATIONS}])]
    return blocks
