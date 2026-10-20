#!/bin/sh
# Claude Code hook：交給 air.py 處理（stdin 是 hook 的 JSON）
root="${CLAUDE_PROJECT_DIR:-$(pwd)}"
py=$(cat "$root/.air/python" 2>/dev/null || echo python3)
exec "$py" "$root/AIR/air.py" hook pre-bash
