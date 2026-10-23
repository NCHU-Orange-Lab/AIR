"""跑專案的 shell 指令（測試套件、黃金重播）：在專案根目錄、有逾時、逾時殺整個程序樹、輸出只留結尾。

verify 與 golden 共用；不知道指令是做什麼的，只回報結束碼、時間與輸出。
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

OUTPUT_TAIL = 3000  # 輸出只保留結尾這麼多字


def tail(text: str) -> str:
    return text if len(text) <= OUTPUT_TAIL else "…（前面省略）\n" + text[-OUTPUT_TAIL:]


def _kill_tree(proc: subprocess.Popen) -> None:
    """結束整個程序樹（shell 與它啟動的程式），不只 shell 本身。POSIX 用程序群組；Windows 沒有 killpg，
    只殺 shell 的話測試會繼續跑、communicate() 一直等，所以用 taskkill /T。"""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)
        elif hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, OSError):
        pass


def run_command(command: str, root: Path, timeout: int, timeout_hint: str) -> dict:
    """回傳 {status: ok|error, exit, duration_ms, output, detail}。status 只分「跑完了」與「跑不起來／逾時」，成敗由呼叫端依結束碼或結果檔判斷。"""
    start = time.monotonic()
    try:
        proc = subprocess.Popen(command, shell=True, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", start_new_session=True)
    except OSError as e:
        return {"status": "error", "detail": f"無法執行：{e}"}
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        output, _ = proc.communicate()
        return {"status": "error", "output": tail(output or ""), "detail": f"逾時（超過 {timeout} 秒；{timeout_hint}）"}
    ms = int((time.monotonic() - start) * 1000)
    return {"status": "ok", "exit": proc.returncode, "duration_ms": ms, "output": tail(output or ""), "detail": f"exit {proc.returncode}（{ms} ms）"}
