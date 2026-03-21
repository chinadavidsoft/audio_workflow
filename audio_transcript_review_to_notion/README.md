# 音频转写 + 点评写入 Notion

该模块用于处理单个 `.mp3` 或 `.m4a` 音频文件，并执行以下流程：

1. 语音转写（以英语为主）
2. 转写文本排版为 Markdown（仅整理格式，不改写内容）
3. 生成中英双语表达点评
4. 保存两份本地 Markdown 文件
5. 在同一 Notion 父页面下创建两篇子页面并写入内容

## 功能特性

- 与现有脚本模块完全分离
- 校验输入后缀（仅支持 `.mp3` / `.m4a`）
- 本地输出文件：
  - `<audio_stem> - Transcript.md`
  - `<audio_stem> - Feedback.md`
- Notion 子页面：
  - `<audio_stem> - Transcript`
  - `<audio_stem> - Feedback`
- 若 Notion 页面重名，自动追加时间戳后缀，避免覆盖

## 前置条件

1. Python 3.10+
2. 已安装 `openai` Python 包
3. 通用 API Key（变量名：`API_KEY`）
4. Notion Integration Token，且对目标父页面有写权限

安装依赖：

```bash
pip install openai
```

若你使用不支持音频转写的网关（例如仅支持文本模型），建议额外安装本地兜底转写：

```bash
pip install faster-whisper
```

配置环境变量：

```bash
export NOTION_API_KEY="your_notion_integration_token"
export API_KEY="your_api_key"
```

可选（使用非默认 OpenAI 兼容网关时）：

```bash
export API_BASE_URL="https://your-provider-compatible-base-url"
```

## 使用方式

```bash
python3 /Users/david/projects/custom-python-script/audio_transcript_review_to_notion/audio_transcript_review_to_notion.py \
  --audio /absolute/path/to/recording.m4a \
  --parent-page-id YOUR_NOTION_PARENT_PAGE_ID
```

可选：覆盖点评模型（默认 `gpt-5-mini`）：

```bash
python3 /Users/david/projects/custom-python-script/audio_transcript_review_to_notion/audio_transcript_review_to_notion.py \
  --audio /absolute/path/to/recording.mp3 \
  --parent-page-id YOUR_NOTION_PARENT_PAGE_ID \
  --model gpt-5
```

## 说明

- 当前版本面向“单人、清晰、以英语为主”的录音场景。
- 若 Notion 上传失败，本地 Markdown 文件会保留，可后续重试上传。
- 当远程音频转写接口失败时，脚本会自动尝试本地 Whisper 转写（可通过 `LOCAL_WHISPER_MODEL` 指定模型，默认 `small`）。
