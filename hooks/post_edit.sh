#!/bin/sh
# AIR hook：交給 air.py 處理（stdin 是宿主送來的 JSON；第一個參數是宿主名稱，省略時當成 claude）。
# 用腳本自己的位置找專案根目錄，不依賴 $CLAUDE_PROJECT_DIR 或目前的工作目錄。
root=$(cd "$(dirname "$0")/../.." && pwd)
py=$(cat "$root/.air/python" 2>/dev/null || echo python3)
exec "$py" "$root/AIR/air.py" hook post-edit --host "${1:-claude}"
