#!/bin/sh
# 移除 AIR 的所有接點。加 --purge 會一併刪除 .air/ 專案設定。
cd "$(dirname "$0")/.." || exit 1
py=$(cat .air/python 2>/dev/null || echo python3)
exec "$py" AIR/air.py uninstall "$@"
