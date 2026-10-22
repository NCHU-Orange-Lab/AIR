#!/bin/sh
# commit 前的檢查（git hook 或 pre-commit 呼叫）。找不到 Python 時擋下 commit（這是關卡，不能 fail-open）。
root=$(cd "$(dirname "$0")/../.." && pwd)
. "$root/AIR/hooks/_python.sh"
py=$(find_python "$root") || { echo "AIR：找不到 Python 3.11 以上（試過 .air/python、python3、python、py -3），commit 前檢查無法執行，commit 已擋下。安裝 Python 或執行 sh AIR/install.sh 後再試。" >&2; exit 1; }
run_python "$py" "$root/AIR/air.py" check
