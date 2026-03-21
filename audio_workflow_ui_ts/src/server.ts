import { spawn } from "node:child_process";
import { createServer, IncomingMessage, ServerResponse } from "node:http";
import { promises as fs } from "node:fs";
import path from "node:path";
import { URL } from "node:url";

type AppConfig = {
  audioInboxDir: string;
  notionDatabaseId: string;
  apiKey: string;
  apiBaseUrl: string;
  notionApiKey: string;
  reviewModel: string;
  scriptPath: string;
  recursiveScan: boolean;
};

type LegacyConfig = Partial<AppConfig> & {
  openaiApiKey?: string;
  notionParentPageId?: string;
};

type FileRunResult = {
  filePath: string;
  status: "success" | "failed" | "skipped";
  detail: string;
};

type RunReport = {
  startedAt: string;
  endedAt: string;
  summary: {
    total: number;
    success: number;
    failed: number;
    skipped: number;
  };
  message: string;
  items: FileRunResult[];
};

const PROJECT_ROOT = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(PROJECT_ROOT, "..");
const DATA_DIR = path.join(PROJECT_ROOT, "data");
const CONFIG_PATH = path.join(DATA_DIR, "config.json");
const REPORT_PATH = path.join(DATA_DIR, "last-run.json");

const DEFAULT_CONFIG: AppConfig = {
  audioInboxDir: path.join(REPO_ROOT, "audio_inbox"),
  notionDatabaseId: "",
  apiKey: "",
  apiBaseUrl: "",
  notionApiKey: "",
  reviewModel: "gpt-5-mini",
  scriptPath: path.join(
    REPO_ROOT,
    "audio_transcript_review_to_notion",
    "audio_transcript_review_to_notion.py"
  ),
  recursiveScan: false,
};

const PORT = Number(process.env.PORT ?? 4173);
const HOST = process.env.HOST ?? "127.0.0.1";
const MODEL_OPTIONS = ["gpt-5-mini", "gpt-5", "deepseek-chat"];

function escapeHtml(raw: string): string {
  return raw
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function ensureTrailingNewline(text: string): string {
  return text.endsWith("\n") ? text : `${text}\n`;
}

function sendHtml(res: ServerResponse, html: string): void {
  res.statusCode = 200;
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.end(ensureTrailingNewline(html));
}

function redirect(res: ServerResponse, location: string): void {
  res.statusCode = 303;
  res.setHeader("Location", location);
  res.end();
}

async function ensureDataDir(): Promise<void> {
  await fs.mkdir(DATA_DIR, { recursive: true });
}

async function loadConfig(): Promise<AppConfig> {
  try {
    const raw = await fs.readFile(CONFIG_PATH, "utf-8");
    const parsed = JSON.parse(raw) as LegacyConfig;
    return {
      ...DEFAULT_CONFIG,
      ...parsed,
      notionDatabaseId: (parsed.notionDatabaseId ?? parsed.notionParentPageId ?? "").trim(),
      apiKey: (parsed.apiKey ?? parsed.openaiApiKey ?? "").trim(),
    };
  } catch {
    return { ...DEFAULT_CONFIG };
  }
}

async function saveConfig(config: AppConfig): Promise<void> {
  await ensureDataDir();
  await fs.writeFile(CONFIG_PATH, JSON.stringify(config, null, 2), "utf-8");
}

async function loadLastRunReport(): Promise<RunReport | null> {
  try {
    const raw = await fs.readFile(REPORT_PATH, "utf-8");
    return JSON.parse(raw) as RunReport;
  } catch {
    return null;
  }
}

async function saveLastRunReport(report: RunReport): Promise<void> {
  await ensureDataDir();
  await fs.writeFile(REPORT_PATH, JSON.stringify(report, null, 2), "utf-8");
}

async function readBody(req: IncomingMessage): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  return Buffer.concat(chunks).toString("utf-8");
}

function parseForm(raw: string): URLSearchParams {
  return new URLSearchParams(raw);
}

function value(form: URLSearchParams, key: string): string {
  return (form.get(key) ?? "").trim();
}

async function pathExists(filePath: string): Promise<boolean> {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

async function resolvePythonExecutable(): Promise<string> {
  const venvPython = path.join(REPO_ROOT, ".venv", "bin", "python");
  if (await pathExists(venvPython)) {
    return venvPython;
  }
  return "python3";
}

function isAudioFile(filePath: string): boolean {
  const ext = path.extname(filePath).toLowerCase();
  return ext === ".mp3" || ext === ".m4a";
}

async function listAudioFiles(rootDir: string, recursive: boolean): Promise<string[]> {
  const collected: Array<{ filePath: string; mtimeMs: number }> = [];
  const queue = [rootDir];

  while (queue.length > 0) {
    const current = queue.shift();
    if (!current) {
      continue;
    }

    let entries;
    try {
      entries = await fs.readdir(current, { withFileTypes: true });
    } catch {
      continue;
    }

    for (const entry of entries) {
      const fullPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        if (recursive) {
          queue.push(fullPath);
        }
        continue;
      }

      if (!entry.isFile() || !isAudioFile(fullPath)) {
        continue;
      }

      const stat = await fs.stat(fullPath);
      collected.push({ filePath: fullPath, mtimeMs: stat.mtimeMs });
    }
  }

  collected.sort((a, b) => a.mtimeMs - b.mtimeMs);
  return collected.map((item) => item.filePath);
}

async function runCommand(
  command: string,
  args: string[],
  env: NodeJS.ProcessEnv
): Promise<{ code: number; output: string }> {
  return new Promise((resolve) => {
    const child = spawn(command, args, { env });
    const stdoutChunks: Buffer[] = [];
    const stderrChunks: Buffer[] = [];

    child.stdout.on("data", (chunk) => {
      stdoutChunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    });
    child.stderr.on("data", (chunk) => {
      stderrChunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    });
    child.on("error", (error) => {
      resolve({
        code: 1,
        output: `进程启动失败: ${error.message}`,
      });
    });
    child.on("close", (code) => {
      const output = `${Buffer.concat(stdoutChunks).toString("utf-8")}\n${Buffer.concat(
        stderrChunks
      ).toString("utf-8")}`.trim();
      resolve({
        code: code ?? 1,
        output: output || "(无输出)",
      });
    });
  });
}

function buildChildEnv(overrides: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env };
  for (const key of [
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
  ]) {
    delete env[key];
  }
  return { ...env, ...overrides };
}

async function runWorkflow(config: AppConfig): Promise<RunReport> {
  const started = new Date();
  const items: FileRunResult[] = [];

  const requiredProblems: string[] = [];
  if (!config.audioInboxDir) {
    requiredProblems.push("音频目录未配置");
  }
  if (!config.notionDatabaseId) {
    requiredProblems.push("NOTION_DATABASE_ID 未配置");
  }
  if (!config.apiKey) {
    requiredProblems.push("API_KEY 未配置");
  }
  if (!config.notionApiKey) {
    requiredProblems.push("NOTION_API_KEY 未配置");
  }
  if (!config.scriptPath) {
    requiredProblems.push("Python 脚本路径未配置");
  }

  if (requiredProblems.length > 0) {
    return {
      startedAt: started.toISOString(),
      endedAt: new Date().toISOString(),
      summary: { total: 0, success: 0, failed: 0, skipped: 0 },
      message: `执行前校验失败：${requiredProblems.join("；")}`,
      items,
    };
  }

  if (!(await pathExists(config.audioInboxDir))) {
    return {
      startedAt: started.toISOString(),
      endedAt: new Date().toISOString(),
      summary: { total: 0, success: 0, failed: 0, skipped: 0 },
      message: `音频目录不存在：${config.audioInboxDir}`,
      items,
    };
  }

  if (!(await pathExists(config.scriptPath))) {
    return {
      startedAt: started.toISOString(),
      endedAt: new Date().toISOString(),
      summary: { total: 0, success: 0, failed: 0, skipped: 0 },
      message: `Python 脚本不存在：${config.scriptPath}`,
      items,
    };
  }

  const files = await listAudioFiles(config.audioInboxDir, config.recursiveScan);
  const pythonExecutable = await resolvePythonExecutable();
  for (const audioFile of files) {
    const result = await runCommand(
      pythonExecutable,
      [
        config.scriptPath,
        "--audio",
        audioFile,
        "--database-id",
        config.notionDatabaseId,
        "--model",
        config.reviewModel || "gpt-5-mini",
      ],
      buildChildEnv({
        API_KEY: config.apiKey,
        API_BASE_URL: config.apiBaseUrl,
        NOTION_API_KEY: config.notionApiKey,
      })
    );

    items.push({
      filePath: audioFile,
      status: result.code === 0 ? "success" : "failed",
      detail: result.output,
    });
  }

  const success = items.filter((item) => item.status === "success").length;
  const failed = items.filter((item) => item.status === "failed").length;
  const skipped = items.filter((item) => item.status === "skipped").length;

  return {
    startedAt: started.toISOString(),
    endedAt: new Date().toISOString(),
    summary: {
      total: items.length,
      success,
      failed,
      skipped,
    },
    message:
      items.length === 0
        ? "目录中没有可处理的 mp3/m4a 文件"
        : "执行完成（失败项已保留，需人工重跑）",
    items,
  };
}

function renderReport(report: RunReport | null): string {
  if (!report) {
    return "<p>暂无执行记录。</p>";
  }

  const rows =
    report.items.length === 0
      ? "<tr><td colspan='3'>无</td></tr>"
      : report.items
          .map(
            (item) =>
              `<tr>
  <td>${escapeHtml(item.filePath)}</td>
  <td>${escapeHtml(item.status)}</td>
  <td><pre>${escapeHtml(item.detail)}</pre></td>
</tr>`
          )
          .join("\n");

  return `
<p><strong>开始：</strong> ${escapeHtml(report.startedAt)}</p>
<p><strong>结束：</strong> ${escapeHtml(report.endedAt)}</p>
<p><strong>结果：</strong> ${escapeHtml(report.message)}</p>
<p><strong>统计：</strong> total=${report.summary.total}, success=${report.summary.success}, failed=${report.summary.failed}, skipped=${report.summary.skipped}</p>
<table border="1" cellspacing="0" cellpadding="8" style="width:100%;border-collapse:collapse">
  <thead>
    <tr>
      <th>文件</th>
      <th>状态</th>
      <th>详情</th>
    </tr>
  </thead>
  <tbody>
    ${rows}
  </tbody>
</table>
`;
}

function renderPage(config: AppConfig, report: RunReport | null, tip: string): string {
  const options = MODEL_OPTIONS.includes(config.reviewModel)
    ? MODEL_OPTIONS
    : [...MODEL_OPTIONS, config.reviewModel];
  const modelOptionsHtml = options.map((item) => {
    const selected = item === config.reviewModel ? "selected" : "";
    return `<option value="${escapeHtml(item)}" ${selected}>${escapeHtml(item)}</option>`;
  }).join("\n");

  return `<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>音频工作流控制台</title>
    <style>
      body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; line-height: 1.5; color: #111; }
      .box { border: 1px solid #ddd; border-radius: 10px; padding: 16px; margin-bottom: 16px; background: #fff; }
      label { display: block; font-weight: 600; margin-top: 10px; }
      input[type="text"], input[type="password"], select { width: 100%; padding: 8px; border: 1px solid #bbb; border-radius: 8px; }
      .actions { margin-top: 14px; display: flex; gap: 8px; }
      button { border: 1px solid #333; background: #111; color: #fff; border-radius: 8px; padding: 8px 14px; cursor: pointer; }
      button.secondary { background: #fff; color: #111; }
      .tip { margin-bottom: 12px; color: #0b5; font-weight: 600; }
      pre { white-space: pre-wrap; word-break: break-word; margin: 0; }
      table th { background: #f6f6f6; text-align: left; }
    </style>
  </head>
  <body>
    <h1>音频转写工作流控制台（TypeScript）</h1>
    <p>用途：可视化配置变量并手动执行一次处理。</p>
    ${tip ? `<div class="tip">${escapeHtml(tip)}</div>` : ""}

    <div class="box">
      <h2>配置</h2>
      <p>配置将保存在本地文件：<code>${escapeHtml(CONFIG_PATH)}</code></p>
      <form method="post" action="/save-config">
        <label for="audioInboxDir">音频目录（audio inbox）</label>
        <input id="audioInboxDir" name="audioInboxDir" type="text" value="${escapeHtml(config.audioInboxDir)}" />

        <label for="notionDatabaseId">NOTION_DATABASE_ID</label>
        <input id="notionDatabaseId" name="notionDatabaseId" type="text" value="${escapeHtml(config.notionDatabaseId)}" />

        <label for="apiKey">API_KEY</label>
        <input id="apiKey" name="apiKey" type="password" value="${escapeHtml(config.apiKey)}" />

        <label for="apiBaseUrl">API_BASE_URL（可选）</label>
        <input id="apiBaseUrl" name="apiBaseUrl" type="text" placeholder="例如 https://api.openai.com/v1" value="${escapeHtml(config.apiBaseUrl)}" />

        <label for="notionApiKey">NOTION_API_KEY</label>
        <input id="notionApiKey" name="notionApiKey" type="password" value="${escapeHtml(config.notionApiKey)}" />

        <label for="reviewModel">点评模型</label>
        <select id="reviewModel" name="reviewModel">
          ${modelOptionsHtml}
        </select>

        <label for="scriptPath">Python 脚本路径</label>
        <input id="scriptPath" name="scriptPath" type="text" value="${escapeHtml(config.scriptPath)}" />

        <label>
          <input type="checkbox" name="recursiveScan" ${config.recursiveScan ? "checked" : ""} />
          递归扫描子目录
        </label>

        <div class="actions">
          <button type="submit">保存配置</button>
        </div>
      </form>
    </div>

    <div class="box">
      <h2>执行</h2>
      <form method="post" action="/run-once">
        <div class="actions">
          <button type="submit">立即执行一次</button>
          <button class="secondary" type="button" onclick="location.reload()">刷新页面</button>
        </div>
      </form>
    </div>

    <div class="box">
      <h2>最近一次执行记录</h2>
      ${renderReport(report)}
    </div>
  </body>
</html>`;
}

function tipFromQuery(url: URL): string {
  if (url.searchParams.get("saved") === "1") {
    return "配置已保存";
  }
  if (url.searchParams.get("ran") === "1") {
    return "工作流已执行一次";
  }
  return "";
}

async function handleSaveConfig(req: IncomingMessage, res: ServerResponse): Promise<void> {
  const body = await readBody(req);
  const form = parseForm(body);
  const current = await loadConfig();
  const next: AppConfig = {
    ...current,
    audioInboxDir: value(form, "audioInboxDir"),
    notionDatabaseId: value(form, "notionDatabaseId"),
    apiKey: value(form, "apiKey"),
    apiBaseUrl: value(form, "apiBaseUrl"),
    notionApiKey: value(form, "notionApiKey"),
    reviewModel: value(form, "reviewModel") || DEFAULT_CONFIG.reviewModel,
    scriptPath: value(form, "scriptPath"),
    recursiveScan: form.get("recursiveScan") === "on",
  };
  await saveConfig(next);
  redirect(res, "/?saved=1");
}

async function handleRunOnce(_req: IncomingMessage, res: ServerResponse): Promise<void> {
  const config = await loadConfig();
  const report = await runWorkflow(config);
  await saveLastRunReport(report);
  redirect(res, "/?ran=1");
}

const server = createServer(async (req, res) => {
  try {
    const method = req.method ?? "GET";
    const parsedUrl = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);
    const pathname = parsedUrl.pathname;

    if (pathname === "/favicon.ico") {
      res.statusCode = 204;
      res.end();
      return;
    }

    if (method === "GET" && pathname === "/") {
      const [config, report] = await Promise.all([loadConfig(), loadLastRunReport()]);
      sendHtml(res, renderPage(config, report, tipFromQuery(parsedUrl)));
      return;
    }

    if (method === "POST" && pathname === "/save-config") {
      await handleSaveConfig(req, res);
      return;
    }

    if (method === "POST" && pathname === "/run-once") {
      await handleRunOnce(req, res);
      return;
    }

    res.statusCode = 404;
    res.setHeader("Content-Type", "text/plain; charset=utf-8");
    res.end("Not Found\n");
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    res.statusCode = 500;
    res.setHeader("Content-Type", "text/plain; charset=utf-8");
    res.end(`Server Error: ${detail}\n`);
  }
});

server.listen(PORT, HOST, () => {
  // eslint-disable-next-line no-console
  console.log(`UI running at http://${HOST}:${PORT}`);
});
