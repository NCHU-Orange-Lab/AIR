"""受保護路徑與唯讀階段：PreToolUse（Edit|Write|MultiEdit）hook 使用。

把「不要修改 AIR/」「分析、計畫階段不改程式」「改 .air/project.toml 要經使用者同意」從文字變成機制。
只攔得到宿主送來的編輯工具；用 Bash 改檔靠 MANIFEST（全專案檢查、commit 前）事後抓。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import PROJECT_FILE, REPORT_FILE, Config
from .install import MANAGED, SKILL_MARK

DENY, ASK, ALLOW = "deny", "ask", "allow"
# 還在做分析（installed）或計畫（analyzed）時，不能改程式
READ_ONLY_PHASES = {"installed": "分析", "analyzed": "計畫"}


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str = ""


def _managed(path: Path) -> bool:
    """AIR 產生的規則檔：第一行有 AIR:MANAGED。"""
    try:
        return MANAGED in path.read_text(encoding="utf-8").split("\n", 1)[0]
    except (OSError, UnicodeDecodeError):
        return False


def classify(path: Path, cfg: Config) -> Decision:
    """這個檔案能不能被編輯工具修改。只看路徑與階段，不看內容合不合規（那是 post-edit 的事）。"""
    try:
        rel = path.resolve().relative_to(cfg.root.resolve())
    except ValueError:
        return Decision(ALLOW)  # 專案外的檔案不歸 AIR 管
    parts, name = rel.parts, rel.as_posix()
    if not parts:
        return Decision(ALLOW)
    if parts[0] == "AIR":
        return Decision(DENY, "AIR/ 是套件，專案裡不修改（更新時會整包換掉）。要調整規範，照 skill air-maintain-rules 在 AIR 的開發 repo 改。")
    if name == ".claude/settings.json":
        return Decision(DENY, "這裡放著 AIR 的 hook。要停用 AIR，請使用者執行 sh AIR/uninstall.sh；不要自己拿掉 hook。")
    if parts[0] == ".git":
        return Decision(DENY, "不能修改 .git 裡的內容（含 hooks）。")
    if name == PROJECT_FILE.as_posix():
        return Decision(ASK, "修改 .air/project.toml（imports.extra、allow-legacy、遷移階段）需要使用者同意。")
    if parts[0] == ".air":
        return Decision(DENY, ".air/ 裡的其他檔案（紀錄、Python 路徑）由 AIR 的指令寫入，不要手動修改。")
    if rel.name == "CLAUDE.md" and _managed(path):
        return Decision(DENY, "這份 CLAUDE.md 是 AIR 產生的規則檔，更新 AIR 時會覆蓋。規則請在 AIR 的開發 repo 改。")
    if len(parts) >= 3 and parts[:2] == (".claude", "skills") and (cfg.root / parts[0] / parts[1] / parts[2] / SKILL_MARK).exists():
        return Decision(DENY, "這個 skill 由 AIR 複製，更新 AIR 時會覆蓋。")
    stage = READ_ONLY_PHASES.get(cfg.phase)
    if stage and rel.suffix == ".py":
        return Decision(DENY, f"目前在{stage}階段（phase = {cfg.phase}），不能修改程式。先完成遷移報告 {REPORT_FILE.as_posix()}，等使用者核准進入下一階段。")
    return Decision(ALLOW)
