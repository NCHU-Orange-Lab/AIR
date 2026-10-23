"""常駐內容的預算（doctor --context）：依宿主盤點一開場就會載入的檔案，對有上限的算佔比，80% 警告。

實戰（報告 §4.4）：記憶索引 52 行、用條數看很健康，卻有 26,073 bytes，超過 Claude Code 自動記憶的 25KB 上限，
最後幾行（正好是最新的指令）沒有被載入，沒有人收到警告。AIR 自己的預算原本也只算條數。

各宿主一開場載入的東西與上限（查官方文件，2026-10）：
  Claude Code  專案 CLAUDE.md（或 .claude/CLAUDE.md）與它 @ 匯入的檔案（最多 4 層，相對於匯入它的檔案）；
               .claude/rules/*.md 裡沒有 paths 前言的；使用者的 ~/.claude/CLAUDE.md 與 ~/.claude/rules/；
               沒有 CLAUDE.md 時改讀 AGENTS.md。CLAUDE.md 沒有硬上限，文件建議 200 行以內
  自動記憶     ~/.claude/projects/<專案路徑的 slug>/memory/MEMORY.md：前 200 行或 25KB，尾端被截掉
  Codex        $CODEX_HOME（預設 ~/.codex）的 AGENTS.override.md 或 AGENTS.md，再從 repo 根目錄往下每層一個檔，
               從根到深串接；合計到 project_doc_max_bytes（預設 32 KiB）就停止加入，後面的截掉。
               AIR 的區塊附在根目錄 AGENTS.md 的尾端，最先受影響
AIR 只讀這些檔案，不改、不擋：記憶衛生的守門不做（那會管到專案外的檔案），AIR 區塊也不自動搬到開頭，落在上限外時指出來。
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from .config import Config
from .install import BEGIN, END, claude_md_path

OK, WARN, FAIL = "ok", "warn", "fail"
LONG_LINE = 200                 # 超過這麼多字的行另外數：報告 F2，52 行裡 43 行超過 200 字
MEMORY_LIMIT_LINES = 200        # Claude Code 自動記憶：前 200 行或 25KB（文件沒寫 25,000 還是 25,600，取小的）
MEMORY_LIMIT_BYTES = 25_000
CLAUDE_MD_GUIDE_LINES = 200     # CLAUDE.md 的文件建議值，不是截斷上限：超過只提醒
CODEX_DOC_MAX_BYTES = 32 * 1024  # Codex 的 project_doc_max_bytes 預設值
WARN_RATIO = 0.8
IMPORT_DEPTH = 4
IMPORT_RE = re.compile(r"(?<![\w@/.-])@([\w./~-]+)")  # @path 匯入；前面接著字母的（email）不算


@dataclass(frozen=True)
class Item:
    host: str                      # Claude Code | Codex | AIR
    kind: str                      # 專案 CLAUDE.md、@ 匯入、規則、使用者 CLAUDE.md、記憶索引、AGENTS.md、SessionStart 注入…
    path: str                      # 顯示用
    bytes: int
    lines: int
    long_lines: int
    limit_bytes: int | None = None
    limit_lines: int | None = None
    note: str = ""
    level: str = OK

    @property
    def ratio(self) -> float | None:
        parts = []
        if self.limit_bytes:
            parts.append(self.bytes / self.limit_bytes)
        if self.limit_lines:
            parts.append(self.lines / self.limit_lines)
        return max(parts) if parts else None

    def to_dict(self) -> dict:
        return {"host": self.host, "kind": self.kind, "path": self.path, "bytes": self.bytes, "lines": self.lines, "long_lines": self.long_lines,
                "limit_bytes": self.limit_bytes, "limit_lines": self.limit_lines, "ratio": None if self.ratio is None else round(self.ratio, 3),
                "level": self.level, "note": self.note}


# ── 共用 ──

def _stats(path: Path) -> tuple[int, int, int]:
    data = path.read_bytes()
    lines = data.decode("utf-8", "replace").splitlines()
    return len(data), len(lines), sum(len(line) > LONG_LINE for line in lines)


def _show(path: Path, root: Path, home: Path) -> str:
    for base, prefix in ((root, ""), (home, "~/")):
        try:
            return prefix + path.resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            continue
    return path.as_posix()


def _item(host: str, kind: str, path: Path, root: Path, home: Path, **kw) -> Item:
    b, n, long = _stats(path)
    return Item(host, kind, _show(path, root, home), b, n, long, **kw)


def memory_slug(root: Path) -> str:
    """Claude Code 的專案目錄名稱：絕對路徑裡非英數字的字元都換成 -（由現有目錄反推：/home/x/AIR_project → -home-x-AIR-project）。
    只做字元替換，不 resolve：呼叫端給絕對路徑（Windows 上 resolve 會把 /home/x 變成 D:/home/x 這種帶磁碟代號的路徑）。"""
    return re.sub(r"[^A-Za-z0-9-]", "-", str(root))


def memory_file(root: Path, home: Path) -> Path:
    return home / ".claude" / "projects" / memory_slug(root.resolve()) / "memory" / "MEMORY.md"


def first_line_beyond(path: Path, limit_bytes: int, limit_lines: int) -> int | None:
    """從第幾行起載不到（1-based）；都載得到是 None。"""
    total = 0
    for i, raw in enumerate(path.read_bytes().split(b"\n"), 1):
        if i > limit_lines:
            return i
        total += len(raw) + 1
        if total > limit_bytes:
            return i
    return None


# ── Claude Code ──

def imports_of(path: Path, home: Path, depth: int = 0, seen: set[Path] | None = None) -> list[Path]:
    """CLAUDE.md 的 @ 匯入（遞迴，最多 IMPORT_DEPTH 層，相對於匯入它的檔案）；找不到的略過。"""
    seen = seen if seen is not None else set()
    out = []
    if depth >= IMPORT_DEPTH:
        return out
    in_code = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        for ref in IMPORT_RE.findall(line):
            target = (home / ref[2:]) if ref.startswith("~/") else (path.parent / ref)
            try:
                target = target.resolve()
            except OSError:
                continue
            if target in seen or not target.is_file():
                continue
            seen.add(target)
            out.append(target)
            out += imports_of(target, home, depth + 1, seen)
    return out


def is_path_scoped(path: Path) -> bool:
    """規則檔有 paths 前言：讀寫符合的檔案時才載入，不算開場。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return False
    body = text.split("---", 2)
    return len(body) >= 3 and re.search(r"^paths\s*:", body[1], re.M) is not None


def unscoped_rules(directory: Path) -> list[Path]:
    return sorted(p for p in directory.rglob("*.md") if p.is_file() and not is_path_scoped(p)) if directory.is_dir() else []


def claude_items(root: Path, home: Path) -> list[Item]:
    items = []
    claude = claude_md_path(root)
    seen: set[Path] = set()
    if claude.exists():
        items.append(_item("Claude Code", "專案 CLAUDE.md", claude, root, home, limit_lines=None))
        seen.add(claude.resolve())
        for p in imports_of(claude, home, seen=seen):
            items.append(_item("Claude Code", "@ 匯入", p, root, home))
    elif (root / "AGENTS.md").exists():
        items.append(_item("Claude Code", "AGENTS.md（沒有 CLAUDE.md 時代替）", root / "AGENTS.md", root, home))
    for p in unscoped_rules(root / ".claude" / "rules"):
        items.append(_item("Claude Code", "規則（沒有 paths，每次載入）", p, root, home))
    user = home / ".claude" / "CLAUDE.md"
    if user.is_file():
        items.append(_item("Claude Code", "使用者 CLAUDE.md", user, root, home))
        for p in imports_of(user, home, seen={user.resolve()}):
            items.append(_item("Claude Code", "使用者 @ 匯入", p, root, home))
    for p in unscoped_rules(home / ".claude" / "rules"):
        items.append(_item("Claude Code", "使用者規則（沒有 paths）", p, root, home))
    mem = memory_file(root, home)
    if mem.is_file():
        it = _item("Claude Code", "自動記憶索引", mem, root, home, limit_bytes=MEMORY_LIMIT_BYTES, limit_lines=MEMORY_LIMIT_LINES)
        beyond = first_line_beyond(mem, MEMORY_LIMIT_BYTES, MEMORY_LIMIT_LINES)
        if beyond:
            it = replace(it, note=f"第 {beyond} 行起載不到（前 {MEMORY_LIMIT_LINES} 行或 {MEMORY_LIMIT_BYTES:,} bytes 以外），越新的指令越先消失")
        items.append(it)
    else:
        items.append(Item("Claude Code", "自動記憶索引", _show(mem, root, home), 0, 0, 0, MEMORY_LIMIT_BYTES, MEMORY_LIMIT_LINES, note="找不到（還沒有記憶，或目錄的命名規則不同）"))
    return items


# ── Codex ──

def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def codex_doc_limit(root: Path, codex_dir: Path) -> int:
    """project_doc_max_bytes：專案的 .codex/config.toml 優先，再看 $CODEX_HOME/config.toml，都沒有就是預設 32 KiB。"""
    for cfg_path in (root / ".codex" / "config.toml", codex_dir / "config.toml"):
        try:
            value = tomllib.loads(cfg_path.read_text(encoding="utf-8")).get("project_doc_max_bytes")
        except (OSError, ValueError):
            continue
        if isinstance(value, int) and value > 0:
            return value
    return CODEX_DOC_MAX_BYTES


def _agents_in(directory: Path) -> Path | None:
    for name in ("AGENTS.override.md", "AGENTS.md"):
        p = directory / name
        if p.is_file() and p.stat().st_size > 0:
            return p
    return None


def codex_items(root: Path, home: Path, codex_dir: Path | None = None) -> list[Item]:
    codex_dir = codex_dir or codex_home()
    limit = codex_doc_limit(root, codex_dir)
    chain = [p for p in (_agents_in(codex_dir), _agents_in(root)) if p]
    items = [_item("Codex", "AGENTS.md 鏈（$CODEX_HOME 的在前）", p, root, home) for p in chain]
    total = sum(it.bytes for it in items) + 2 * max(len(items) - 1, 0)  # 檔案之間用空行接起來
    note = "只算 $CODEX_HOME 與根目錄；工作目錄更深的 AGENTS.md 在後面，更容易落在上限外"
    air_end = None
    offset = 0
    for p, it in zip(chain, items):
        data = p.read_bytes()
        begin = data.find(BEGIN.encode())
        if begin >= 0:
            end = data.find(END.encode(), begin)
            air_end = offset + (data.find(b"\n", end) if end >= 0 else len(data)) + 1
        offset += it.bytes + 2
    level = OK
    if air_end is not None:
        if air_end > limit:
            note = f"AIR 區塊的結尾在第 {air_end:,} byte，超過 {limit:,}：Codex 讀不到它（把區塊搬到檔案開頭，或縮短前面的內容）；" + note
            level = FAIL
        else:
            note = f"AIR 區塊的結尾在第 {air_end:,} byte；" + note
    items.append(Item("Codex", "AGENTS.md 鏈合計", "（合計）", total, sum(it.lines for it in items), sum(it.long_lines for it in items),
                      limit_bytes=limit, note=note, level=level))
    return items


# ── AIR 自己 ──

def air_items(cfg: Config) -> list[Item]:
    """SessionStart 注入的大小：沒有上限，但每次開始、恢復、壓縮後都會進對話。"""
    from . import context
    from .hosts import HOSTS
    items = []
    for host in HOSTS.values():
        text = context.session_context(cfg, host)
        items.append(Item("AIR", f"SessionStart 注入（{host.label}）", "hook", len(text.encode("utf-8")), text.count("\n") + 1,
                          sum(len(line) > LONG_LINE for line in text.splitlines()), note="每次開始、恢復、壓縮後；內容由 status 與 START.md 當下階段組成"))
    return items


# ── 判定與輸出 ──

def evaluate(items: list[Item]) -> list[Item]:
    out = []
    for it in items:
        level, note = it.level, it.note
        r = it.ratio
        if r is not None and r >= 1.0:
            level = FAIL
        elif r is not None and r >= WARN_RATIO and level == OK:
            level = WARN
        if it.kind == "專案 CLAUDE.md" and it.lines > CLAUDE_MD_GUIDE_LINES and level == OK:
            level, note = WARN, f"超過文件建議的 {CLAUDE_MD_GUIDE_LINES} 行（不會截斷，但吃 token、降低遵守率）"
        out.append(replace(it, level=level, note=note))
    return out


def inventory(root: Path, cfg: Config | None, home: Path | None = None, codex_dir: Path | None = None, with_air: bool = True) -> list[Item]:
    home = home or Path.home()
    items = claude_items(root, home) + codex_items(root, home, codex_dir)
    if with_air and cfg is not None:
        items += air_items(cfg)
    return evaluate(items)


def problems(items: list[Item]) -> list[Item]:
    return [it for it in items if it.level != OK]


def _kb(n: int) -> str:
    return f"{n / 1000:.1f} KB"


def describe(it: Item) -> str:
    """一行說明（給 doctor 與 status）。"""
    if it.limit_bytes and it.ratio is not None:
        return f"{it.host}：{it.path}（{it.kind}）{_kb(it.bytes)}／{_kb(it.limit_bytes)}（{it.ratio:.0%}）" + (f"：{it.note}" if it.note else "")
    return f"{it.host}：{it.path}（{it.kind}）{it.bytes:,} bytes、{it.lines} 行" + (f"：{it.note}" if it.note else "")


def status_line(root: Path, home: Path | None = None) -> str | None:
    """status 用：有項目到 80% 以上才回一行，其餘回 None（不要讓每個 session 開始都多讀一行）。"""
    worst = problems(inventory(root, None, home, with_air=False))
    if not worst:
        return None
    it = max(worst, key=lambda i: (i.level == FAIL, i.ratio or 0))
    return describe(it) + "（python3 AIR/air.py doctor --context）"


def format_table(items: list[Item]) -> str:
    lines = ["AIR 常駐內容（一開場就載入的東西；80% 注意、超過上限或 AIR 區塊落在外面是問題）"]
    for host in ("Claude Code", "Codex", "AIR"):
        mine = [it for it in items if it.host == host]
        if not mine:
            continue
        lines.append(f"[{host}]")
        for it in mine:
            mark = {OK: "  ", WARN: "注意", FAIL: "問題"}[it.level]
            limit = f"／{_kb(it.limit_bytes)}" if it.limit_bytes else ""
            ratio = f"（{it.ratio:.0%}）" if it.ratio is not None else ""
            lines.append(f"  {mark} {it.path}　{it.kind}　{it.bytes:,} bytes{limit}{ratio}、{it.lines} 行、超過 {LONG_LINE} 字的行 {it.long_lines}" + (f"　← {it.note}" if it.note else ""))
    total = sum(it.bytes for it in items if it.host == "Claude Code")
    lines.append(f"Claude Code 開場合計 {total:,} bytes（不含依路徑載入的規則與 skill）；自動記憶上限 {MEMORY_LIMIT_LINES} 行或 {MEMORY_LIMIT_BYTES:,} bytes；Codex 鏈上限 project_doc_max_bytes")
    return "\n".join(lines)
