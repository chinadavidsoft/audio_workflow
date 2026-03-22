# 音频转写 + 点评写入 Notion

该模块用于处理单个 `.mp3` 或 `.m4a` 音频文件，并执行以下流程：

1. 使用本地 Whisper 做语音转写（以英语为主）
2. 转写文本排版为 Markdown（仅整理格式，不改写内容）
3. 生成 `AI语法点评` 与 `AI重写`
4. 写入 Notion 主库 relation 列，并分别 upsert 到 `转录 / AI语法点评 / AI重写` 详情库
5. （可选）保存两份本地 Markdown 文件

## 功能特性

- 与现有脚本模块完全分离
- 校验输入后缀（仅支持 `.mp3` / `.m4a`）
- 默认不落地本地 Markdown 文件。
- 仅在传入 `--write-local-md` 时，才会输出：
  - `<audio_stem> - Transcript.md`
  - `<audio_stem> - Feedback.md`
- Notion 数据结构：
  - 主库 `录音总表`
  - 详情库 `转录`
  - 详情库 `AI语法点评`
  - 详情库 `AI重写`
  - 详情库 `AI口语建议`
  - 主库中的 `转录 / ai语法点评 / ai重写 / ai口语建议` 都是 relation
  - 脚本只自动维护 `转录 / ai语法点评 / ai重写`
  - `ai口语建议` 留空，由人工填写

## 前置条件

1. Python 3.10+
2. 已安装 `openai` Python 包
3. 通用 API Key（变量名：`API_KEY`）
4. Notion Integration Token，且对主库与 4 个详情库都有写权限

安装依赖：

```bash
pip install openai
```

如果当前网络环境依赖 SOCKS 代理，还需要：

```bash
pip install socksio
```

必须安装本地转写依赖：

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
  --database-id YOUR_MAIN_DATABASE_ID
```

可选：覆盖点评模型（默认 `gpt-5-mini`）：

```bash
python3 /Users/david/projects/custom-python-script/audio_transcript_review_to_notion/audio_transcript_review_to_notion.py \
  --audio /absolute/path/to/recording.mp3 \
  --database-id YOUR_MAIN_DATABASE_ID \
  --model gpt-5
```

可选：把 transcript/feedback 同时保存到音频同目录：

```bash
python3 /Users/david/projects/custom-python-script/audio_transcript_review_to_notion/audio_transcript_review_to_notion.py \
  --audio /absolute/path/to/recording.mp3 \
  --database-id YOUR_MAIN_DATABASE_ID \
  --write-local-md
```

## 说明

- 当前版本面向“单人、清晰、以英语为主”的录音场景。
- 若启用了 `--write-local-md`，Notion 入库失败时本地 Markdown 文件会保留，可后续重试上传。
- 主库必须至少包含这些属性：
  - `录音名`（title）
  - `转录`（relation）
  - `ai语法点评`（relation）
  - `ai重写`（relation）
  - `ai口语建议`（relation）
- `转录 / AI语法点评 / AI重写 / AI口语建议` 4 个详情库都必须至少包含：
  - `录音名`（title）
  - `内容`（rich_text）
- 若主库和详情库存在 `更新时间`（date）属性，脚本会自动写入。
- 本地 `Feedback.md` 会被固定整理成 2 段：
  - `AI语法点评`
  - `AI重写`
- 脚本不会写主记录正文，也不会写详情页正文，只写数据库属性。
- `ai口语建议` relation 会被校验存在，但不会被脚本创建、更新或清空。
