#!/bin/sh
# commit 前的檢查（git hook 或 pre-commit 呼叫）
root=$(cd "$(dirname "$0")/../.." && pwd)
py=$(cat "$root/.air/python" 2>/dev/null || echo python3)
exec "$py" "$root/AIR/air.py" check
