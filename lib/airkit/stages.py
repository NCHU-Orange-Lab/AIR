"""遷移階段的做法在哪一份檔案：START.md 只放共通原則與目錄，每個階段一份 AIR/start/<N>-<名稱>.md。

試跑（2026-10-24，4 個深度學習專案、Claude 與 Codex 各 16 次）：AI 一開始就把整份 START.md（6,255 字，約 9 千到 1.1 萬 token）讀進對話，
之後二十幾次呼叫都背著它，佔 AIR 多出的 token 將近一半。SessionStart 只附當下那一節原本就有，但 session 開始時還沒安裝
（或安裝失敗）就不會附；所以拆成檔案，status、install 的輸出指出下一份、phase 推進時直接印出下一份的全文，不靠 hook 也只讀得到需要的那一份。
"""

from __future__ import annotations

from pathlib import Path

from .config import KIT_DIR

START_MAX_CHARS = 1500  # START.md 只放共通原則與目錄；每次安裝、每個 session 都可能被整份讀進來，所以跟 AGENTS.md 一樣有上限
FILES = {1: "1-install.md", 2: "2-analyze.md", 3: "3-plan.md", 4: "4-layers.md", 5: "5-migrate.md", 6: "6-finish.md"}
STAGE_OF_PHASE = {"installed": 2, "analyzed": 3, "planned": 4, "layered": 5, "migrating": 5}  # 在這個階段該做的是第幾個階段；done 沒有


def path(stage: int) -> Path | None:
    name = FILES.get(stage)
    return KIT_DIR / "start" / name if name else None


def shown(stage: int) -> str:
    """給 AI 看的路徑（相對專案根目錄）。"""
    return f"AIR/start/{FILES[stage]}"


def text(stage: int) -> str:
    """那一份的全文；沒有這個階段是空字串。"""
    p = path(stage)
    return p.read_text(encoding="utf-8").strip() if p is not None and p.is_file() else ""


def for_phase(phase: str) -> int | None:
    return STAGE_OF_PHASE.get(phase)
