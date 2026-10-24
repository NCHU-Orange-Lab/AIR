"""`.air/history.jsonl` 的讀取與批次起點。階段轉移、暫停、繼續與每批結束都追加一行（寫入在 project.record）。

放在這裡而不是 project／batch：verify 也要知道「這一批從哪個 commit 開始」（防倒退要比對起點之後新增的檔案），
而 project 與 batch 都 import verify，放在它們裡面會循環。
"""

from __future__ import annotations

import json
from pathlib import Path

HISTORY_FILE = Path(".air") / "history.jsonl"  # 每次階段轉移一行：時間、從哪到哪、commit、方式、原因
BATCH = "batch"  # history.jsonl 的 how：每批結束
TRANSITIONS = ("normal", "forced", "rollback")  # 真正的階段轉移；pause／resume 的紀錄 to 也是 migrating，不算起點


def load(root: Path) -> list[dict]:
    path = root / HISTORY_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def window_start(root: Path) -> tuple[str | None, str | None]:
    """這一批的起點 (時間, HEAD)：上一批結束時；沒有上一批就是進入 migrating 那次轉移；都沒有就 (None, None)（算全部）。"""
    hist = load(root)
    for e in reversed(hist):
        if e.get("how") == BATCH:
            return e.get("time"), e.get("head")
    for e in reversed(hist):
        if e.get("to") == "migrating" and e.get("how") in TRANSITIONS:
            return e.get("time"), e.get("head")
    return None, None
