#!/bin/sh
# 安裝 AIR：找一個 Python 3.11+，交給 air.py install。可以重複執行。
# 參數會原樣傳給 air.py install，例如：sh AIR/install.sh --package shop --package-dir src/shop
cd "$(dirname "$0")/.." || exit 1
for py in python3.14 python3.13 python3.12 python3.11 python3; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    exec "$py" AIR/air.py install "$@"
  fi
done
echo "AIR 需要 Python 3.11 以上，找不到可用的 python3。" >&2
exit 1
