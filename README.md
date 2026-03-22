# audio_workflow

音频处理与 Notion 入库相关脚本集合，包含：
- 音频格式转换（`m4a` -> `mp3`）
- 本地 Whisper 转写
- AI 点评与改写生成
- Notion 数据库 upsert
- 一个独立的 TypeScript Web UI（用于配置并触发流程）

## 目录说明

- `convert_m4a_to_mp3/`：批量将 `.m4a` 转为 `.mp3`
- `audio_transcript_review_to_notion/`：单文件转写 + AI 点评 + 写入 Notion
- `audio_transcript_review_codex_automation/`：Codex 自动化版本（转写与 Notion upsert 分离）
- `audio_workflow_ui_ts/`：Web 配置台（Node.js + TypeScript）
- `notion_markdown_converter.py`：Markdown 到 Notion Block 转换
- `tests/`：`notion_markdown_converter.py` 的测试

## 环境准备

基础环境：
- Python 3.10+
- `ffmpeg`

按需安装 Python 依赖：

```bash
pip install openai faster-whisper markdown-it-py httpx
```

如果你使用 `audio_workflow_ui_ts/`，还需要：

```bash
cd audio_workflow_ui_ts
npm install
```

## 常用命令

1) 批量转换 m4a -> mp3

```bash
python3 convert_m4a_to_mp3/convert_m4a_to_mp3.py -r
```

2) 单音频转写点评并写入 Notion

```bash
export API_KEY="your_api_key"
export NOTION_API_KEY="your_notion_integration_token"
# 可选：export API_BASE_URL="https://your-openai-compatible-endpoint"
# 可选：export LOCAL_WHISPER_MODEL="small"

python3 audio_transcript_review_to_notion/audio_transcript_review_to_notion.py \
  --audio /absolute/path/to/recording.m4a \
  --database-id YOUR_MAIN_DATABASE_ID \
  --model gpt-5-mini
```

3) Codex 自动化版本（分步执行）

```bash
# step 1: 本地转写
python3 audio_transcript_review_codex_automation/transcribe_local_whisper.py \
  --audio /absolute/path/to/recording.m4a \
  --write-meta

# step 2: 使用模板生成 Feedback.md（参考目录内模板文件）
# audio_transcript_review_codex_automation/codex_feedback_prompt_template.md

# step 3: upsert 到 Notion
python3 audio_transcript_review_codex_automation/upsert_review_to_notion_db.py \
  --audio /absolute/path/to/recording.m4a \
  --transcript-md "/absolute/path/to/recording - Transcript.md" \
  --feedback-md "/absolute/path/to/recording - Feedback.md" \
  --database-id YOUR_DATABASE_ID
```

4) 启动 Web UI

```bash
cd audio_workflow_ui_ts
npm run dev
```

## 测试

```bash
python3 -m unittest tests/test_notion_markdown_converter.py
```

## 详细文档

各子模块目录下均有独立 README，可查看更完整的参数和行为说明。
