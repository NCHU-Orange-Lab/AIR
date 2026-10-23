"""受保護路徑與唯讀階段：PreToolUse（Edit|Write|MultiEdit）hook 使用。

把「不要修改 AIR/」「分析、計畫階段不改程式」「改 .air/project.toml 要經使用者同意」從文字變成機制。
只攔得到宿主送來的編輯工具；用 Bash 改檔靠 MANIFEST（全專案檢查、commit 前）事後抓。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import install, project
from .config import PROJECT_FILE, REPORT_FILE, Config
from .hosts import HOSTS
from .install import SKILL_MARK

DENY, ASK, ALLOW = "deny", "ask", "allow"
# 還在做分析（installed）或計畫（analyzed）時，不能改程式
READ_ONLY_PHASES = {"installed": "分析", "analyzed": "計畫"}


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str = ""


def _managed_skill(parts: tuple[str, ...], root: Path) -> bool:
    """任一宿主的 skills 目錄底下、有 .air-managed 標記的 skill。"""
    for host in HOSTS.values():
        n = len(host.skills_dir.parts)
        if len(parts) > n and parts[:n] == host.skills_dir.parts and (root.joinpath(*parts[:n + 1]) / SKILL_MARK).exists():
            return True
    return False


def _managed_path_rule(parts: tuple[str, ...], root: Path) -> bool:
    """任一宿主的路徑規則目錄底下、帶 AIR:MANAGED 標記的規則檔（使用者自己放的規則不算）。"""
    for host in HOSTS.values():
        n = len(host.rules_dir.parts) if host.rules_dir else 0
        if n and len(parts) == n + 1 and parts[:n] == host.rules_dir.parts and install.is_managed_path_rule(root.joinpath(*parts)):
            return True
    return False


def frozen(path: Path, cfg: Config) -> bool:
    """分析／計畫階段凍結的範圍：套件目錄與測試目錄裡的 .py，減掉 migration.exclude。
    套件外的腳本、筆記本、實驗區不凍結（實戰中整個 repo 被凍住，所有 session 都動不了）。"""
    if path.suffix != ".py" or cfg.is_excluded(path):
        return False
    resolved = path.resolve()
    return any(resolved.is_relative_to(d.resolve()) for d in (cfg.package_dir, cfg.tests_dir))


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
    for host in HOSTS.values():
        if name == host.hooks_file.as_posix():
            return Decision(DENY, f"這裡放著 AIR 的 hook（{host.label}）。要停用 AIR，請使用者執行 sh AIR/uninstall.sh；不要自己拿掉 hook。")
    if parts[0] == ".git":
        return Decision(DENY, "不能修改 .git 裡的內容（含 hooks）。")
    if name == PROJECT_FILE.as_posix():
        return Decision(ASK, "修改 .air/project.toml（imports.extra、allow-legacy、遷移階段）需要使用者同意。")
    if parts[0] == ".air":
        return Decision(DENY, ".air/ 裡的其他檔案（紀錄、Python 路徑）由 AIR 的指令寫入，不要手動修改。")
    if _managed_skill(parts, cfg.root):
        return Decision(DENY, "這個 skill 由 AIR 複製，更新 AIR 時會覆蓋。要修改請在 AIR 的開發 repo 改。")
    if _managed_path_rule(parts, cfg.root):
        return Decision(DENY, "這份規則由 AIR 產生（AIR/rules/<層>.md 的複本），更新 AIR 時會覆蓋。要調整規範，照 skill air-maintain-rules 在 AIR 的開發 repo 改。")
    stage = READ_ONLY_PHASES.get(cfg.phase)
    if stage and frozen(path, cfg) and project.paused(cfg.root) is None:  # 暫停中解除凍結：照一般開發
        scope = f"{cfg.package_dir.relative_to(cfg.root).as_posix()}、{cfg.tests_dir.relative_to(cfg.root).as_posix()}"
        return Decision(DENY, f"目前在{stage}階段（phase = {cfg.phase}），不能修改套件與測試目錄裡的程式（{scope}；migration.exclude 的路徑除外）。"
                              f"先完成遷移報告 {REPORT_FILE.as_posix()}，等使用者核准進入下一階段。遷移要先擱置時，由使用者執行 python3 AIR/air.py pause --reason <原因>。")
    return Decision(ALLOW)
