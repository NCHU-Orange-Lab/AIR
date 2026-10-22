#!/bin/sh
# 安裝 AIR：找一個 Python 3.11+（.air/python、python3.x、python3、python、py -3），交給 air.py install。可以重複執行。
# 參數會原樣傳給 air.py install，例如：sh AIR/install.sh --package shop --package-dir src/shop
cd "$(dirname "$0")/.." || exit 1
. AIR/hooks/_python.sh
py=$(find_python "$(pwd)") || { echo "AIR 需要 Python 3.11 以上，找不到可用的 python3、python 或 py -3。" >&2; exit 1; }
run_python "$py" AIR/air.py install "$@"
