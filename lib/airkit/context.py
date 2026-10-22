"""按需注入：第一次讀寫某一層的檔案時，把那一層的規則附給 AI（additionalContext）；session 開始與壓縮後重置，並附上目前狀態。

取代「各層目錄放 CLAUDE.md、靠宿主自動載入」：hook 在 Claude Code 與 Codex 都會觸發，規則只有一個來源（AIR/rules/），
而且可以在測試裡驗證。注入紀錄放在 .air/sessions/<session_id>.json（不進版本控制）。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import project
from .checks import layer_of
from .config import KIT_DIR, Config

SESSIONS_DIR = Path(".air") / "sessions"
KEEP_DAYS = 7  # 超過這麼久沒動的注入紀錄，session 開始時順便清掉
PHASE_GUIDE = {
    "installed": "分析階段（AIR/START.md 階段 2）：只讀程式、寫遷移報告，不修改 .py（會被擋下）。",
    "analyzed": "計畫階段（AIR/START.md 階段 3）：在遷移報告寫計畫，不修改 .py（會被擋下）。",
    "planned": "建立分層（AIR/START.md 階段 4）：python3 AIR/air.py layers，然後 check、phase layered。",
    "layered": "準備逐批遷移（AIR/START.md 階段 5）：第一批開始時執行 phase migrating。",
    "migrating": "逐批遷移中（AIR/START.md 階段 5）：每批先補行為保護測試，verify 通過才 commit；每批結束停下來等核准。",
    "done": "遷移完成：新程式一律寫進對應的層，改到哪一層就照那一層的規則。",
}


def rule_name(path: Path, cfg: Config) -> str | None:
    """這個檔案適用哪一份規則：層的名稱、tests，或 None（不在任何層）。"""
    layer = layer_of(path, cfg)
    if layer:
        return layer.name
    return "tests" if path.resolve().is_relative_to(cfg.tests_dir.resolve()) else None


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
        f"【AIR：你正要讀寫 {name} 的檔案，這是它的規則（AIR/rules/{name}.md；這個 session 只附一次）】\n{rule_text(name)}"
        for name in sorted(names)
    )


def session_context(cfg: Config) -> str:
    """session 開始、壓縮後附上的狀態摘要：由目前的設定、檢查結果與驗收紀錄重新算，不信任任何摘要。"""
    guide = PHASE_GUIDE.get(cfg.phase, f"未知的階段 {cfg.phase}：請使用者檢查 .air/project.toml")
    return "\n".join([
        "【AIR 目前狀態（由 hook 在 session 開始與壓縮後附上）】",
        project.status(cfg),
        f"現在該做的事：{guide}",
        "規則：常駐規則在 AIR/AGENTS.md；第一次讀寫某一層的檔案時，hook 會附上那一層的規則（AIR/rules/<層>.md）。",
    ])
