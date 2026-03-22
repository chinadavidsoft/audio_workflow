# 音频工作流可视化配置台（TypeScript）

这是一个独立项目，用于在网页界面中配置并触发现有 Python 工作流：

- 音频目录
- `NOTION_DATABASE_ID`
- `API_KEY`
- `API_BASE_URL`（可选）
- `NOTION_API_KEY`
- 点评模型（下拉框，内置 `gpt-5-mini` / `gpt-5` / `deepseek-chat`）
- Python 脚本路径

## 功能

- 网页表单保存配置（本地落盘）
- 手动执行“一次处理”
- 处理策略：
  - 扫描目录中的 `.mp3/.m4a`
  - 默认去重：同一路径且文件未变化（基于 `size + mtime`）会自动跳过
  - 文件有变化或首次出现时，才会执行脚本并 upsert Notion 记录
  - 单个文件失败时只记录失败，不自动重试
- 页面展示最近一次执行记录

## 启动

```bash
cd /Users/david/projects/custom-python-script/audio_workflow_ui_ts
npm install
npm run dev
```

打开：`http://localhost:4173`

## 配置文件位置

- 配置文件：`/Users/david/projects/custom-python-script/audio_workflow_ui_ts/data/config.json`
- 最近执行记录：`/Users/david/projects/custom-python-script/audio_workflow_ui_ts/data/last-run.json`
- 去重索引：`/Users/david/projects/custom-python-script/audio_workflow_ui_ts/data/processed-files.json`

## 说明

- 该 UI 不会改动现有 Python 模块代码，只是通过子进程调用：
  - `/Users/david/projects/custom-python-script/audio_transcript_review_to_notion/audio_transcript_review_to_notion.py`
- 当前流程会把每个音频写入主库 `录音总表`，并自动维护 `转录 / ai语法点评 / ai重写` 这 3 个 relation。
- `ai口语建议` 列不会被脚本触碰，适合手工填写。
- API Key 会以明文保存在本地 `config.json`，请仅在可信环境使用。
