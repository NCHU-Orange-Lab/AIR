"""宿主（AI 工具）的差異集中在這裡：hook 設定寫在哪、長什麼樣子、skill 裝在哪、payload 的欄位名稱、聽得懂哪些回應。

規則與檢查不分宿主（checks、protect、verify 都不知道宿主是誰）。hook 腳本把宿主名稱當第一個參數傳進來，
air.py 用這裡的 parse() 把 payload 正規化成 Event，處理完再用該宿主聽得懂的方式回應。

目前支援：
  claude  Claude Code：.claude/settings.json、.claude/skills/。Grok Build 讀同一份設定（相容 Claude Code 的 hook），
          欄位名稱的差異（stopHookActive）在 parse() 處理。
  codex   Codex：.codex/hooks.json、.agents/skills/。沒有 $CLAUDE_PROJECT_DIR、沒有 Read 工具、
          PreToolUse 不支援 permissionDecision: ask（改成提醒）；hook 要在 Codex 裡信任過才會執行。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# (事件, matcher, 腳本, timeout 秒)。matcher 用 Claude Code 的工具名稱；Codex 把 Edit、Write 當成 apply_patch 的別名。
HOOKS = [
    ("SessionStart", None, "session_start.sh", 60),
    ("PreToolUse", "Bash", "pre_bash.sh", 30),
    ("PreToolUse", "Read", "pre_read.sh", 30),
    ("PreToolUse", "Edit|Write|MultiEdit", "pre_edit.sh", 30),
    ("PostToolUse", "Edit|Write|MultiEdit", "post_edit.sh", 60),
    ("Stop", None, "stop.sh", 120),
]
SCRIPTS = tuple(script for _, _, script, _ in HOOKS)
LEGACY_SCRIPTS: tuple[str, ...] = ()  # 改過名的腳本：重新安裝時要認得舊設定裡的名稱


@dataclass(frozen=True)
class Host:
    name: str
    label: str
    hooks_file: Path          # 相對專案根目錄
    skills_dir: Path
    root_expr: str            # hook 指令裡取得專案根目錄的寫法（shell）
    supports_ask: bool        # PreToolUse 能不能回 permissionDecision: ask
    scripts: tuple[str, ...]  # 這個宿主會裝的 hook
    description: str = ""     # hook 設定檔新建時的 description（Codex 的 hooks.json 有這個欄位）
    note: str = ""            # 安裝後要提醒使用者的事

    def hooks(self) -> list[tuple[str, str | None, str, int]]:
        return [h for h in HOOKS if h[2] in self.scripts]


CLAUDE = Host(
    name="claude", label="Claude Code",
    hooks_file=Path(".claude") / "settings.json", skills_dir=Path(".claude") / "skills",
    root_expr='"$CLAUDE_PROJECT_DIR"', supports_ask=True, scripts=SCRIPTS,
)
CODEX = Host(
    name="codex", label="Codex",
    hooks_file=Path(".codex") / "hooks.json", skills_dir=Path(".agents") / "skills",
    # Codex 的 hook 沒有 $CLAUDE_PROJECT_DIR，工作目錄是 session 的 cwd：用 git 找根目錄，不是 git repo 就用 cwd
    root_expr='"$(git rev-parse --show-toplevel 2>/dev/null || pwd)"', supports_ask=False,
    scripts=tuple(s for s in SCRIPTS if s != "pre_read.sh"),  # Codex 用 shell 讀檔，沒有 Read 工具
    description="AIR 規範檢查（由 AIR/install.sh 產生；AIR 的項目請勿手動修改）",
    note="Codex 要在它的 hooks 信任介面裡信任這些 hook 才會執行；AIR 更新後 hook 設定有變動時要重新信任。",
)
HOSTS = {h.name: h for h in (CLAUDE, CODEX)}
DEFAULT = CLAUDE.name


def hook_command(host: Host, script: str) -> str:
    # 明確用 sh 執行：fresh checkout 的腳本沒有執行位元也能啟動。第一個參數告訴腳本是哪個宿主
    return f"sh {host.root_expr}/AIR/hooks/{script} {host.name}"


def _legacy_commands(script: str) -> list[str]:
    return [f'sh "$CLAUDE_PROJECT_DIR"/AIR/hooks/{script}', f'"$CLAUDE_PROJECT_DIR"/AIR/hooks/{script}']  # 2.0～2.2 的寫法


def air_commands() -> dict[str, tuple[Host | None, str]]:
    """AIR 曾經產生過的每一種 hook 指令 → (宿主, 腳本)；舊格式的宿主是 None。安裝器與檢查都用完全相同的字串比對。"""
    out: dict[str, tuple[Host | None, str]] = {}
    for host in HOSTS.values():
        for script in host.scripts:
            out[hook_command(host, script)] = (host, script)
    for script in (*SCRIPTS, *LEGACY_SCRIPTS):
        for command in _legacy_commands(script):
            out.setdefault(command, (None, script))
    return out


# ── payload 正規化 ──

# Codex apply_patch 的檔案標頭；Move to 是改名後的新路徑
PATCH_FILE = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$", re.M)


@dataclass(frozen=True)
class Event:
    host: Host
    session_id: str          # 沒有時是空字串（注入紀錄會用 unknown）
    tool: str
    files: tuple[Path, ...]  # 這次工具呼叫讀或改的檔案（絕對路徑）
    command: str             # Bash 的指令；Codex 的 apply_patch 是 patch 文字
    stop_active: bool        # Stop：已經因為 Stop hook 繼續過一次
    source: str              # SessionStart：startup、resume、clear、compact、fork


def parse(payload: dict, host_name: str = DEFAULT) -> Event:
    """把宿主的 payload 正規化。欄位缺少時用空值，不猜。

    Claude Code：tool_input.file_path；Codex：tool_input.command 是 apply_patch 的 patch 文字，被改的檔案在標頭裡；
    Grok Build：Stop 的旗標是 stopHookActive（其他欄位有 snake_case 別名）。相對路徑以 payload 的 cwd 為準。
    """
    if not isinstance(payload, dict):
        raise ValueError(f"payload 不是物件：{type(payload).__name__}")
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if not isinstance(tool_input, dict):
        raise ValueError(f"tool_input 不是物件：{type(tool_input).__name__}")
    command = tool_input.get("command", "")
    command = command if isinstance(command, str) else ""
    base = Path(payload.get("cwd") or ".")
    names = [str(tool_input["file_path"])] if "file_path" in tool_input else PATCH_FILE.findall(command)
    files = tuple((base / name.strip()).resolve() for name in names if name.strip())  # 絕對路徑時 base / name 就是 name
    return Event(
        host=HOSTS[host_name],
        session_id=str(payload.get("session_id") or payload.get("sessionId") or ""),
        tool=str(payload.get("tool_name") or payload.get("toolName") or ""),
        files=files,
        command=command,
        stop_active=bool(payload.get("stop_hook_active") or payload.get("stopHookActive")),
        source=str(payload.get("source") or ""),
    )
