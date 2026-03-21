#!/usr/bin/env python3
"""
使用本地 Whisper 转写单个 MP3/M4A 音频并保存 Transcript Markdown。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


SUPPORTED_EXTENSIONS = {".mp3", ".m4a"}
DEFAULT_LOCAL_MODEL = "small"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="使用本地 Whisper 转写单个 MP3/M4A 文件并保存 Transcript.md"
    )
    parser.add_argument(
        "--audio",
        required=True,
        type=Path,
        help="输入音频文件路径（.mp3 或 .m4a）",
    )
    parser.add_argument(
        "--write-meta",
        action="store_true",
        help="额外写入 <audio_stem> - Meta.json",
    )
    return parser.parse_args()


def fail(message: str, exit_code: int = 1) -> None:
    print(f"错误: {message}", file=sys.stderr)
    raise SystemExit(exit_code)


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


def build_whisper_model():
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        fail("缺少依赖: faster-whisper。请先安装: pip install faster-whisper")

    model_name = os.environ.get("LOCAL_WHISPER_MODEL", DEFAULT_LOCAL_MODEL).strip()
    if not model_name:
        model_name = DEFAULT_LOCAL_MODEL

    try:
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
    except Exception as exc:
        fail(f"初始化 Whisper 模型失败（{model_name}）: {exc}")

    return model, model_name


def transcribe_audio_local(audio_path: Path) -> str:
    model, model_name = build_whisper_model()
    try:
        segments, _ = model.transcribe(
            str(audio_path),
            language="en",
            vad_filter=True,
        )
    except Exception as exc:
        fail(f"本地转写失败（模型 {model_name}）: {exc}")

    text_parts = [segment.text.strip() for segment in segments if segment.text.strip()]
    merged = " ".join(text_parts).strip()
    if not merged:
        fail("本地转写结果为空")
    return merged


def format_transcript_markdown(transcript_text: str) -> str:
    normalized = re.sub(r"\s+", " ", transcript_text).strip()
    if not normalized:
        fail("转写文本在规范化后为空")

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
            "转写排版改变了词元序列。"
            "排版仅允许调整布局，不允许改写内容。"
        )


def write_markdown(path: Path, content: str) -> None:
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def write_meta(path: Path, audio_path: Path, transcript_path: Path) -> None:
    payload = {
        "audio_file": str(audio_path),
        "transcript_file": str(transcript_path),
        "local_whisper_model": os.environ.get("LOCAL_WHISPER_MODEL", DEFAULT_LOCAL_MODEL) or DEFAULT_LOCAL_MODEL,
        "processed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    audio_path = validate_audio_path(args.audio)

    print(f"[1/3] 本地 Whisper 转写: {audio_path}")
    transcript_raw = transcribe_audio_local(audio_path)

    transcript_markdown = format_transcript_markdown(transcript_raw)
    assert_transcript_fidelity(transcript_raw, transcript_markdown)

    transcript_path = audio_path.with_name(f"{audio_path.stem} - Transcript.md")
    write_markdown(transcript_path, transcript_markdown)
    print(f"[2/3] 已保存转写 Markdown: {transcript_path}")

    if args.write_meta:
        meta_path = audio_path.with_name(f"{audio_path.stem} - Meta.json")
        write_meta(meta_path, audio_path, transcript_path)
        print(f"[3/3] 已保存元数据 JSON: {meta_path}")
    else:
        print("[3/3] 完成")


if __name__ == "__main__":
    main()
