#!/bin/sh
# AIR hook：交給 air.py 處理（stdin 是宿主送來的 JSON；第一個參數是宿主名稱，省略時當成 claude）。
# 用腳本自己的位置找專案根目錄，不依賴 $CLAUDE_PROJECT_DIR 或目前的工作目錄。
# Python 由 _python.sh 找（.air/python、python3、python、py -3）；找不到就記進 .air/hook-errors.log 後放行。
root=$(cd "$(dirname "$0")/../.." && pwd)
. "$root/AIR/hooks/_python.sh"
py=$(find_python "$root") || { air_hook_failed "$root" "pre_edit.sh" "找不到 Python 3.11 以上（試過 .air/python、python3、python、py -3）"; exit 0; }
run_python "$py" "$root/AIR/air.py" hook pre-edit --host "${1:-claude}"
