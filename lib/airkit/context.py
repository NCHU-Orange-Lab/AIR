"""按需注入（沒有路徑規則的宿主，目前是 Codex）：第一次編輯某一層的檔案時，把那一層的規則附給 AI（additionalContext）；
session 開始與壓縮後重置，並附上目前狀態。

Claude Code 不走這裡：安裝時把 AIR/rules/<層>.md 複製成 .claude/rules/air-<層>.md（帶 paths 前言，見 install.py），
讀寫該層的檔案時由宿主自己載入，不啟動 Python（實測每次 Read 多一個 Python 行程，Windows 上約 0.36 秒）。
規則仍只有 AIR/rules/ 一個來源。注入紀錄放在 .air/sessions/<session_id>.json（不進版本控制）。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import project
from .config import KIT_DIR, Config
from .hosts import Host
from .rules import name_for as rule_name  # noqa: F401 — 哪個檔案適用哪份規則，跟語意審查共用同一個判斷

SESSIONS_DIR = Path(".air") / "sessions"
STAGE_OF_PHASE = {"installed": 2, "analyzed": 3, "planned": 4, "layered": 5, "migrating": 5}  # 這個階段該讀 START.md 的哪一節
KEEP_DAYS = 7  # 超過這麼久沒動的注入紀錄，session 開始時順便清掉
PHASE_GUIDE = {
    "installed": "分析階段（AIR/START.md 階段 2）：只讀程式、寫遷移報告，不修改 .py（會被擋下）。",
    "analyzed": "計畫階段（AIR/START.md 階段 3）：在遷移報告寫計畫，不修改 .py（會被擋下）。",
    "planned": "建立分層（AIR/START.md 階段 4）：python3 AIR/air.py layers，然後 check、phase layered。",
    "layered": "準備逐批遷移（AIR/START.md 階段 5）：第一批開始時執行 phase migrating。",
    "migrating": "逐批遷移中（AIR/START.md 階段 5）：每批先補行為保護測試，verify 通過才 commit；每批結束停下來等核准。",
    "done": "遷移完成：新程式一律寫進對應的層，改到哪一層就照那一層的規則。",
}


def rule_text(name: str) -> str:
    return (KIT_DIR / "rules" / f"{name}.md").read_text(encoding="utf-8").strip()


def _state_file(root: Path, session_id: str) -> Path:
    safe = re.sub(r"[^\w.-]", "_", session_id or "unknown")[:80]
    return root / SESSIONS_DIR / f"{safe}.json"


def injected(root: Path, session_id: str) -> set[str]:
    try:
        return set(json.loads(_state_file(root, session_id).read_text(encoding="utf-8")).get("rules", []))
    except (OSError, ValueError, AttributeError):
        return set()


def remember(root: Path, session_id: str, names: set[str]) -> None:
    path = _state_file(root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"rules": sorted(injected(root, session_id) | names)}, ensure_ascii=False) + "\n", encoding="utf-8")


def reset(root: Path, session_id: str) -> None:
    """session 開始（含壓縮、恢復）：清掉這個 session 的紀錄，順便清掉太久沒動的。"""
    _state_file(root, session_id).unlink(missing_ok=True)
    cutoff = time.time() - KEEP_DAYS * 86400
    for old in (root / SESSIONS_DIR).glob("*.json") if (root / SESSIONS_DIR).is_dir() else []:
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass


def rules_to_inject(cfg: Config, session_id: str, paths) -> str:
    """這些檔案所屬的層，這個 session 還沒附過規則的，附上並記住。回傳要給 AI 的文字（空＝不用附）。"""
    names = {n for n in (rule_name(p, cfg) for p in paths) if n} - injected(cfg.root, session_id)
    if not names:
        return ""
    remember(cfg.root, session_id, names)
    return "\n\n".join(
        f"【AIR：你正要修改 {name} 的檔案，這是它的規則（AIR/rules/{name}.md；這個 session 只附一次）】\n{rule_text(name)}"
        for name in sorted(names)
    )


def stage_text(stage: int) -> str:
    """START.md 裡「## 階段 N」那一節（到下一個 ## 或 --- 為止）。只附當下的階段：整份 START.md 近 4,000 字，5/6 跟當下無關。"""
    text = (KIT_DIR / "START.md").read_text(encoding="utf-8")
    m = re.search(rf"^## 階段 {stage}：.*?(?=^## |^---)", text, re.M | re.S)
    return m.group(0).strip() if m else ""


def session_context(cfg: Config, host: Host | None = None) -> str:
    """session 開始、壓縮後附上的狀態摘要：由目前的設定、檢查結果與驗收紀錄重新算，不信任任何摘要。"""
    if host is not None and host.rules_dir:
        how = f"讀寫某一層的檔案時，{host.label} 會自動載入 {host.rules_dir.as_posix()}/air-<層>.md（AIR/rules/<層>.md 的複本）"
    else:
        how = "第一次修改某一層的檔案時，hook 會附上那一層的規則（AIR/rules/<層>.md）"
    pause = project.paused(cfg.root)
    if pause is not None:  # 暫停中：不催該階段的事，也不附 START.md；新程式仍要寫進對應的層
        guide = (f"遷移暫停中（{project.describe_pause(pause)}；階段 {cfg.phase} 保留）：照一般開發進行，新程式仍寫進對應的層，"
                 "改到哪一層就照那一層的規則；分析／計畫階段的 .py 凍結已解除。要繼續遷移，由使用者執行 python3 AIR/air.py resume，不要自己執行。")
    else:
        guide = PHASE_GUIDE.get(cfg.phase, f"未知的階段 {cfg.phase}：請使用者檢查 .air/project.toml")
    lines = [
        "【AIR 目前狀態（由 hook 在 session 開始與壓縮後附上）】",
        project.status(cfg),
        f"現在該做的事：{guide}",
        f"規則：常駐規則在 AIR/AGENTS.md；{how}。",
    ]
    stage = STAGE_OF_PHASE.get(cfg.phase) if pause is None else None
    section = stage_text(stage) if stage else ""
    if section:
        lines += ["", f"【AIR/START.md 階段 {stage}（已附上這一節，不用再讀整份 START.md）】", section]
    return "\n".join(lines)
