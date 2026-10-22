#!/bin/sh
# Claude Code hook：交給 air.py 處理（stdin 是 hook 的 JSON）。
# 用腳本自己的位置找專案根目錄，不依賴 $CLAUDE_PROJECT_DIR 或目前的工作目錄。
root=$(cd "$(dirname "$0")/../.." && pwd)
py=$(cat "$root/.air/python" 2>/dev/null || echo python3)
exec "$py" "$root/AIR/air.py" hook pre-edit
