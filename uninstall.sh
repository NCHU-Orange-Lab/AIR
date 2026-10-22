#!/bin/sh
# 移除 AIR 的所有接點。加 --purge 會一併刪除 .air/ 專案設定。
cd "$(dirname "$0")/.." || exit 1
. AIR/hooks/_python.sh
py=$(find_python "$(pwd)") || { echo "AIR 需要 Python 3.11 以上，找不到可用的 python3、python 或 py -3。" >&2; exit 1; }
run_python "$py" AIR/air.py uninstall "$@"
