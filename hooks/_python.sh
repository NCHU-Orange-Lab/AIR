#!/bin/sh
# AIR 共用：找一個可用的 Python 3.11+，給 hooks、commit_check.sh、install.sh、uninstall.sh 用（用 . 載入，不直接執行）。
#
# find_python <專案根目錄>
#   先用 install.sh 記下的 .air/python（新的 git worktree 沒有這個檔；Windows 的 CRLF 會先去掉），
#   不能用就依序試 python3.14…python3.11、python3、python、py -3（Windows 的 Python 啟動器）。
#   每個候選都真的執行一次版本檢查，所以 Microsoft Store 的 python3 假指令會被排除。
#   印出可用的指令；都找不到時回傳 1。
# run_python <指令> <參數…>
#   用找到的 Python 執行，一律 UTF-8 模式（-X utf8）。"py -3" 是兩個字；其他指令可能是含空白的路徑，要整個引住。
# air_hook_failed <專案根目錄> <腳本> <原因>
#   hook 自己壞掉（例如找不到 Python）時留下痕跡：寫進 .air/hook-errors.log（status、doctor 會顯示），
#   訊息印到 stderr，然後由呼叫端 fail-open。沒有這一筆，Windows 上的 hook 會靜默失效，沒有人知道。
air_py_ok() { "$@" -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1; }

find_python() {
  # 先測檔案在不在：dash 對 < 的重導向失敗會先印錯誤再處理 2>/dev/null，新 worktree 或第一次安裝會吐「cannot open .air/python」
  saved=$([ -r "$1/.air/python" ] && tr -d '\r' < "$1/.air/python")
  if [ -n "$saved" ] && air_py_ok "$saved"; then echo "$saved"; return 0; fi
  for c in python3.14 python3.13 python3.12 python3.11 python3 python; do
    if command -v "$c" >/dev/null 2>&1 && air_py_ok "$c"; then echo "$c"; return 0; fi
  done
  if command -v py >/dev/null 2>&1 && air_py_ok py -3; then echo "py -3"; return 0; fi
  return 1
}

run_python() {
  py=$1; shift
  if [ "$py" = "py -3" ]; then exec py -3 -X utf8 "$@"; else exec "$py" -X utf8 "$@"; fi
}

air_hook_failed() {
  mkdir -p "$1/.air" 2>/dev/null
  printf '%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$2" "$3" >> "$1/.air/hook-errors.log" 2>/dev/null
  echo "AIR hook 內部錯誤（$2）：$3。這不是你的程式違規；commit 前檢查仍會把關。" >&2
}
