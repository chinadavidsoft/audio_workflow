# M4A 转 MP3 脚本

批量将当前目录（或递归子目录）中的 `.m4a` 文件转换为 `.mp3`，依赖 `ffmpeg`。

## 功能

- 自动扫描 `.m4a` 文件
- 使用 `libmp3lame` 转码（默认质量 `-q 2`）
- 支持递归扫描子目录（`-r`）
- 已存在同名 `.mp3` 时自动跳过
- 支持 `--dry-run` 预览转换计划

## 前置条件

1. 安装 Python 3
2. 安装 ffmpeg

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg
```

## 推荐用法：任意目录直接执行 `m4a2mp3`

如果你已经按全局命令方案配置过 `~/bin/m4a2mp3`，可直接在任意目录使用：

```bash
m4a2mp3
m4a2mp3 -r
m4a2mp3 --dry-run
m4a2mp3 -q 0
m4a2mp3 -o ./mp3_output
```

查看帮助：

```bash
m4a2mp3 --help
```

## 首次安装全局命令（仅当前用户）

> 下面命令会创建 `~/bin/m4a2mp3` 并确保 `~/bin` 在 `PATH`。

```bash
mkdir -p "$HOME/bin"

cat > "$HOME/bin/m4a2mp3" <<'SCRIPT'
#!/usr/bin/env zsh
set -euo pipefail

SCRIPT_PATH="/Users/david/projects/custom-python-script/convert_m4a_to_mp3/convert_m4a_to_mp3.py"

if [[ ! -f "$SCRIPT_PATH" ]]; then
  echo "Error: source script not found: $SCRIPT_PATH" >&2
  exit 1
fi

exec python3 "$SCRIPT_PATH" "$@"
SCRIPT

chmod +x "$HOME/bin/m4a2mp3"

if ! grep -q '^export PATH="$HOME/bin:$PATH"$' "$HOME/.zshrc"; then
  printf '\nexport PATH="$HOME/bin:$PATH"\n' >> "$HOME/.zshrc"
fi

source "$HOME/.zshrc"
command -v m4a2mp3
```

预期输出应包含：

```text
/Users/david/bin/m4a2mp3
```

## 回退方式（卸载全局命令）

如果你想撤销 `m4a2mp3` 全局命令安装，执行：

```bash
rm -f "$HOME/bin/m4a2mp3"
```

然后从 `~/.zshrc` 删除这行（如果有）：

```bash
export PATH="$HOME/bin:$PATH"
```

重新加载 shell：

```bash
source "$HOME/.zshrc"
```

验证回退成功：

```bash
command -v m4a2mp3
```

如果没有任何输出，表示全局命令已移除。

## 参数说明

- `-r, --recursive`：递归扫描子目录
- `-q, --quality`：MP3 质量，范围 `0-9`（`0` 最好，`9` 最差）
- `-o, --output-dir`：指定输出目录
- `-d, --dry-run`：仅打印将执行的转换，不实际写入文件

## 示例

```bash
# 仅转换当前目录
m4a2mp3

# 递归转换
m4a2mp3 -r

# 高质量输出
m4a2mp3 -q 0

# 输出到指定目录
m4a2mp3 -o ./mp3_output

# 预览转换计划
m4a2mp3 --dry-run
```

## 常见问题

### 1) 提示 `ffmpeg is not installed or not in PATH`

请先安装 ffmpeg，并确认命令可用：

```bash
ffmpeg -version
```

### 2) 提示 `source script not found`

表示 `~/bin/m4a2mp3` 里配置的 `SCRIPT_PATH` 与实际项目路径不一致。请更新该路径后重试。

### 3) 为什么没有找到任何文件？

脚本默认只扫描“当前目录”下的 `.m4a` 文件。若文件在子目录，请使用 `-r`。
