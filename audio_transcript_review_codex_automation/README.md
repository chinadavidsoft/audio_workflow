# 音频转写点评入 Notion（Codex Automation V2）

该模块与旧版实现完全隔离：
`/Users/david/projects/custom-python-script/audio_transcript_review_to_notion`

业务流程保持一致：
1. 音频转写
2. 基于转写内容生成中英双语点评
3. 本地保存 Markdown
4. 写入/更新 Notion 数据库

技术方案变更为：
- 转写仅使用本地 Whisper。
- 点评由 Codex automation 流程生成。
- 最终写入 Notion 数据库记录（不再创建两篇子页面）。

## 目录说明
- `transcribe_local_whisper.py`：本地转写脚本（仅转写）
- `upsert_review_to_notion_db.py`：Notion 入库脚本（仅 upsert）
- `codex_feedback_prompt_template.md`：Codex 点评提示词模板

## 前置条件
- Python 3.10+
- 已安装 `faster-whisper`
- Notion Integration Token（对目标数据库有写权限）

安装依赖：

```bash
pip install faster-whisper markdown-it-py
```

环境变量：

```bash
export NOTION_API_KEY="your_notion_integration_token"
export LOCAL_WHISPER_MODEL="small"  # 可选，默认 small
```

## Notion 数据库信息
- 名称：`Audio Transcript Reviews (Codex)`
- URL：`https://www.notion.so/a01d9482672d48d087bb27587671b65d`
- `database_id`：`a01d9482-672d-48d0-87bb-27587671b65d`

字段：
- `Name`（TITLE）
- `Audio Filename`（RICH_TEXT）
- `Audio Path`（RICH_TEXT）
- `Processed At`（DATE）
- `Status`（STATUS）
- `Engine`（SELECT，固定值 `local_whisper+codex`）

去重规则：
- 按 `Name == 音频文件名` 查询。
- 命中则更新属性并替换正文。
- 未命中则创建新记录。

## 使用步骤

### 1）本地 Whisper 转写

```bash
python3 /Users/david/projects/custom-python-script/audio_transcript_review_codex_automation/transcribe_local_whisper.py \
  --audio /absolute/path/to/recording.m4a \
  --write-meta
```

输出到音频同目录：
- `<audio_stem> - Transcript.md`
- `<audio_stem> - Meta.json`（仅在 `--write-meta` 时生成）

### 2）Codex 生成点评

使用 `codex_feedback_prompt_template.md`，填入实际路径后让 Codex 生成：
- `<audio_stem> - Feedback.md`

点评契约：
- 必须覆盖 grammar / word choice / naturalness / clarity
- 必须引用 transcript 里的具体片段
- 必须给出替代表达和一版完整改写
- 必须中英双语（每个要点先英文后中文）

### 3）写入/更新 Notion 数据库

```bash
python3 /Users/david/projects/custom-python-script/audio_transcript_review_codex_automation/upsert_review_to_notion_db.py \
  --audio /absolute/path/to/recording.m4a \
  --transcript-md "/absolute/path/to/recording - Transcript.md" \
  --feedback-md "/absolute/path/to/recording - Feedback.md" \
  --database-id a01d9482-672d-48d0-87bb-27587671b65d
```

页面正文结构固定为：
- `# Transcript`
- `# Feedback`

## 失败行为
- 本地 Whisper 失败：流程中止，不会写 Notion。
- Notion 写入失败：本地 Transcript/Feedback 会保留，便于重试。
