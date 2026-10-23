"""安裝與移除。可以重複執行；只動 AIR 自己建立或標記的內容。

建立的接點：
  CLAUDE.md                      標記區塊：AIR 自己建立的 CLAUDE.md 匯入 AGENTS.md；使用者原有的 CLAUDE.md 匯入 AIR/AGENTS.md
  AGENTS.md                      標記區塊：AIR/AGENTS.md 的全文（Codex 等工具只讀本文，不處理 @ 匯入）
  每個宿主的 hook 設定與 skills    Claude Code：.claude/settings.json、.claude/skills/；Codex：.codex/hooks.json、.agents/skills/（見 hosts.py）
  路徑規則（有 rules_dir 的宿主）   Claude Code：.claude/rules/air-<層>.md，AIR/rules/<層>.md 的複本加 paths 前言；讀寫該層時由宿主自己載入
  commit 前檢查                  .pre-commit-config.yaml 加區塊，或 .git/hooks/pre-commit
  .air/project.toml、.air/python 專案設定（移除時保留，除非 --purge）
舊版放在各層目錄的 CLAUDE.md 會被移除；2.6.1 以前 Claude Code 讀檔前的 hook 會被換掉：各層規則改由 .claude/rules 載入（Codex 仍由 hook 注入，見 context.py）。
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import hosts
from .config import KIT_DIR, PROJECT_FILE, Config, ConfigError

BEGIN, END = "AIR:BEGIN", "AIR:END"
MANAGED = "AIR:MANAGED"
SKILL_MARK = ".air-managed"
FAILED = "失敗："  # 安裝結果中失敗項目的前綴（air.py 用它判斷安裝是否完成）
SKIP_DIRS = {"tests", "test", "docs", "scripts", "AIR", ".air", "build", "dist", "examples", "venv", ".venv"}


class InstallError(Exception):
    pass


def describe_error(e: Exception) -> str:
    if isinstance(e, PermissionError):
        return f"沒有寫入權限（{e.filename}）。可能是 AI 工具的沙箱保護：請核准後重跑，或在自己的終端機執行 sh AIR/install.sh"
    if isinstance(e, json.JSONDecodeError):
        return f"JSON 格式錯誤（{e}）。修正後重跑 sh AIR/install.sh"
    return str(e)


# ── 標記區塊（Markdown、YAML）──

def md_block(body: str) -> str:
    return f"<!-- {BEGIN} 這個區塊由 AIR 管理，請勿手動修改 -->\n{body}\n<!-- {END} -->"


def _block_re(begin: str, end: str) -> re.Pattern:
    return re.compile(rf"(\n?){re.escape(begin)}[^\n]*\n.*?{re.escape(end)}[^\n]*\n?", re.S)


def ensure_block(path: Path, block: str, begin_token: str) -> str:
    """加入或更新標記區塊。回傳 'created' | 'updated' | 'unchanged'。"""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    begin = block.split("\n", 1)[0].split(BEGIN)[0] + BEGIN
    pattern = _block_re(begin, begin.replace(BEGIN, END))
    if begin_token in text:
        new = pattern.sub(lambda m: m.group(1) + block + "\n", text, count=1)
        status = "unchanged" if new == text else "updated"
    else:
        sep = "" if not text else ("\n" if text.endswith("\n") else "\n\n")
        new = text + sep + block + "\n"
        status = "created"
    if new != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8")
    return status


def remove_block(path: Path, begin: str, end: str) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    new = _block_re(begin, end).sub("", text, count=1)
    if new == text:
        return False
    if new.strip():
        path.write_text(new, encoding="utf-8")
    else:
        path.unlink()
    return True


# ── 專案偵測 ──

def detect_packages(root: Path) -> list[tuple[str, str]]:
    """回傳可能的（套件名稱, 套件目錄）。src 版面優先。"""
    found = []
    for base in (root / "src", root):
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            if d.is_dir() and d.name not in SKIP_DIRS and not d.name.startswith(".") and (d / "__init__.py").exists():
                found.append((d.name, str(d.relative_to(root))))
        if found:
            break
    return found


def git_toplevel(root: Path) -> Path | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
        return Path(out.stdout.strip()).resolve()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def preflight(root: Path) -> list[str]:
    if (KIT_DIR / ".git").exists():
        raise InstallError("AIR/ 裡面有 .git。專案的 git 會把它當成內嵌 repo、不會追蹤裡面的檔案。請改用不含 .git 的 AIR 套件。")
    if KIT_DIR.parent.resolve() != root.resolve():
        raise InstallError("AIR/ 必須直接放在專案根目錄。")
    top = git_toplevel(root)
    warnings = []
    if top is None:
        warnings.append("這裡不是 git repo：會略過 commit 前檢查的安裝。")
    elif top != root.resolve():
        raise InstallError(f"專案根目錄應該是 git repo 的根目錄（{top}），請把 AIR/ 放在那裡。")
    return warnings


# ── 專案設定 ──

def write_project_file(root: Path, package: str, package_dir: str) -> str:
    path = root / PROJECT_FILE
    if path.exists():
        return "unchanged"
    template = (KIT_DIR / "templates" / "project.toml").read_text(encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template.replace("{package}", package).replace("{package_dir}", package_dir), encoding="utf-8")
    return "created"


AIR_IGNORE = ["python", "hosts", "verify.json", "review.json", "sessions", "hook-errors.log", "last-check.json", "metrics.jsonl"]  # 這台電腦的 Python 路徑與裝了哪些宿主、本機的驗收與審查紀錄、hook 的注入與錯誤紀錄、最近一次檢查、成本指標：都不進版本控制
HOSTS_FILE = Path(".air") / "hosts"


def write_local_state(root: Path, selected: list[hosts.Host]) -> None:
    """記下這台電腦上可用的 Python 3.11+，以及這次裝了哪些宿主的接點。兩者因機器而異，所以不進版本控制。"""
    air = root / ".air"
    (air / "python").write_text(sys.executable + "\n", encoding="utf-8")
    (root / HOSTS_FILE).write_text("".join(f"{h.name}\n" for h in selected), encoding="utf-8")
    ignore = air / ".gitignore"
    existing = ignore.read_text(encoding="utf-8").splitlines() if ignore.exists() else []
    missing = [name for name in AIR_IGNORE if name not in existing]
    if missing:
        header = [] if existing else ["# 由 AIR/install.sh 產生：這台電腦的 Python 路徑與宿主、本機的驗收與審查紀錄、hook 的注入與錯誤紀錄"]
        ignore.write_text("\n".join(existing + header + missing) + "\n", encoding="utf-8")


def installed_hosts(root: Path) -> list[hosts.Host] | None:
    """這台電腦上 install.sh 裝了哪些宿主；沒跑過 install.sh（或是 2.2 以前裝的）回傳 None。"""
    path = root / HOSTS_FILE
    if not path.exists():
        return None
    names = path.read_text(encoding="utf-8").split()
    return [hosts.HOSTS[n] for n in names if n in hosts.HOSTS]


# ── 指引：CLAUDE.md、AGENTS.md ──

def claude_md_path(root: Path) -> Path:
    if not (root / "CLAUDE.md").exists() and (root / ".claude" / "CLAUDE.md").exists():
        return root / ".claude" / "CLAUDE.md"
    return root / "CLAUDE.md"


def _owned(path: Path) -> bool:
    """這個檔案是 AIR 建立的：不存在，或除了 AIR 的區塊沒有別的內容。"""
    if not path.exists():
        return True
    return not _block_re(f"<!-- {BEGIN}", f"<!-- {END}").sub("", path.read_text(encoding="utf-8")).strip()


def agents_body() -> str:
    """AGENTS.md 區塊的內容：AIR/AGENTS.md 的全文。規則只有一個來源，安裝時複製過去，所以 Codex 這類不處理 @ 匯入的工具也讀得到。"""
    return (KIT_DIR / "AGENTS.md").read_text(encoding="utf-8").strip()


def ensure_instructions(root: Path) -> list[str]:
    claude = claude_md_path(root)

    def rel(target: Path) -> str:  # @ 匯入相對於「包含它的檔案」解析：.claude/CLAUDE.md 要寫 ../
        return os.path.relpath(target, claude.parent).replace(os.sep, "/")

    # AIR 自己建立的 CLAUDE.md 只匯入 AGENTS.md（常駐規則全文在那裡，使用者原有的 AGENTS.md 內容也一起讀到）。
    # 使用者自己的 CLAUDE.md 匯入 AIR/AGENTS.md：有了 CLAUDE.md 之後 Claude Code 不會再讀 AGENTS.md，不會讀到兩份。
    target = root / "AGENTS.md" if _owned(claude) else root / "AIR" / "AGENTS.md"
    out = [f"{claude.relative_to(root).as_posix()}：{ensure_block(claude, md_block('@' + rel(target)), BEGIN)}"]
    out.append(f"AGENTS.md：{ensure_block(root / 'AGENTS.md', md_block(agents_body()), BEGIN)}")
    if (root / "AGENTS.override.md").exists():
        out.append("注意：根目錄有 AGENTS.override.md，Codex 只讀它、不讀 AGENTS.md。請把 AGENTS.md 的 AIR 區塊搬進去，或移除 override")
    return out


# ── hook 設定（每個宿主一個檔案，結構相同：hooks.<事件>[].hooks[]）──

def read_hooks(path: Path) -> dict:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    data = json.loads(text) if text.strip() else {}
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} 的最外層不是物件")
    return data


def is_air_hook(hook: dict) -> bool:
    """只認 AIR 自己產生過的指令（完全相同，含舊版格式），不用子字串比對，才不會誤認使用者的 hook。"""
    return isinstance(hook, dict) and hook.get("type") == "command" and hook.get("command", "") in hosts.air_commands()


def air_hooks(data: dict) -> list[tuple[str, str, str]]:
    """設定裡 AIR 的項目：(事件, 指令, 腳本)。"""
    known = hosts.air_commands()
    return [(event, h["command"], known[h["command"]][1]) for event, entries in data.get("hooks", {}).items()
            for entry in entries for h in entry.get("hooks", []) if is_air_hook(h)]


def missing_hooks(data: dict, host: hosts.Host) -> list[str]:
    """這個宿主應該有、設定裡卻沒有的 hook（不計較指令是哪一版的寫法）。"""
    found = {(event, script) for event, _, script in air_hooks(data)}
    return [f"{event}/{script}" for event, _, script, _ in host.hooks() if (event, script) not in found]


def stale_hooks(data: dict, host: hosts.Host) -> list[str]:
    """指令不是這個宿主目前的寫法（舊格式、別的宿主的格式）：重新安裝會換掉。"""
    current = {hosts.hook_command(host, script) for script in host.scripts}
    return sorted({script for _, command, script in air_hooks(data) if command not in current})


def _strip_air_hooks(data: dict) -> None:
    table = data.get("hooks", {})
    for event in list(table):
        kept = []
        for entry in table[event]:
            entry["hooks"] = [h for h in entry.get("hooks", []) if not is_air_hook(h)]
            if entry["hooks"]:
                kept.append(entry)
        if kept:
            table[event] = kept
        else:
            del table[event]
    if not table:
        data.pop("hooks", None)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def ensure_hooks(root: Path, host: hosts.Host) -> str:
    """先移除 AIR 的項目（含舊格式）再重新加入，所以 matcher、timeout 與指令一定是目前的版本。"""
    path = root / host.hooks_file
    original = read_hooks(path)
    data = copy.deepcopy(original)
    _strip_air_hooks(data)
    if host.description and "description" not in data:
        data = {"description": host.description, **data}
    table = data.setdefault("hooks", {})
    for event, matcher, script, timeout in host.hooks():
        entry = {"hooks": [{"type": "command", "command": hosts.hook_command(host, script), "timeout": timeout}]}
        if matcher:
            entry = {"matcher": matcher, **entry}
        table.setdefault(event, []).append(entry)
    if data == original:
        return "unchanged"
    _write_json(path, data)
    return "updated"


def _prune_empty_dirs(path: Path, root: Path) -> None:
    """移除空掉的目錄（例如 .agents/skills、.agents），到專案根目錄為止。"""
    while path != root and path.is_dir() and not any(path.iterdir()):
        path.rmdir()
        path = path.parent


def remove_hooks(root: Path, host: hosts.Host) -> bool:
    path = root / host.hooks_file
    if not path.exists():
        return False
    original = read_hooks(path)
    data = copy.deepcopy(original)
    _strip_air_hooks(data)
    if "hooks" not in data and data.get("description") == host.description:
        data.pop("description")  # 整個檔案是 AIR 建立的
    if data == original:
        return False
    if data:
        _write_json(path, data)
    else:
        path.unlink()
        _prune_empty_dirs(path.parent, root)
    return True


# ── skills ──

def skill_names() -> list[str]:
    return sorted(d.name for d in (KIT_DIR / "skills").iterdir() if d.is_dir())


def _listing(base: Path) -> dict[str, bytes]:
    return {p.relative_to(base).as_posix(): p.read_bytes()
            for p in base.rglob("*") if p.is_file() and p.name != SKILL_MARK and "__pycache__" not in p.parts}


def skill_differs(src: Path, dest: Path) -> bool:
    """安裝的 skill 跟套件裡的不一樣（更新 AIR 後還沒重新安裝）。"""
    return _listing(src) != _listing(dest)


def ensure_skills(root: Path, host: hosts.Host) -> tuple[list[str], list[str]]:
    """回傳 (安裝的 skill, 略過的說明)。"""
    installed, skipped = [], []
    for name in skill_names():
        src, dest = KIT_DIR / "skills" / name, root / host.skills_dir / name
        if dest.exists() and not (dest / SKILL_MARK).exists():
            skipped.append(f"略過 {dest.relative_to(root).as_posix()}：已經存在，而且不是 AIR 建立的")
            continue
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__"))
        (dest / SKILL_MARK).write_text("這個 skill 由 AIR 複製，更新 AIR 時會覆蓋。\n", encoding="utf-8")
        installed.append(name)
    return installed, skipped


def remove_skills(root: Path, host: hosts.Host) -> list[str]:
    base = root / host.skills_dir
    removed = []
    for d in sorted(base.iterdir()) if base.is_dir() else []:
        if (d / SKILL_MARK).exists():
            shutil.rmtree(d)
            removed.append(d.relative_to(root).as_posix())
    _prune_empty_dirs(base, root)
    return removed


# ── 路徑規則（宿主內建的按路徑載入；目前是 Claude Code 的 .claude/rules）──
# 規則只有 AIR/rules/ 一個來源：安裝時複製成 air-<層>.md，前面加 paths 前言（宿主只在讀寫符合的檔案時載入）。
# 取代原本每次 Read 都啟動一個 Python 的 pre_read hook。檔案帶 AIR:MANAGED 標記，只動自己建立的。

PATH_RULES_PREFIX = "air-"


def rule_names() -> list[str]:
    """規則檔的名稱：各層與 tests（AIR/rules/*.md）。"""
    return sorted(p.stem for p in (KIT_DIR / "rules").glob("*.md"))


def rule_globs(cfg: Config, name: str) -> list[str]:
    """這份規則適用的檔案：層的目錄（含子套件）或測試目錄底下的 .py，相對專案根目錄的 glob。"""
    base = cfg.tests_dir if name == "tests" else cfg.layers[name].path
    return [f"{base.relative_to(cfg.root).as_posix()}/**/*.py"]


def path_rule_text(cfg: Config, name: str) -> str:
    paths = "\n".join(f'  - "{g}"' for g in rule_globs(cfg, name))
    body = (KIT_DIR / "rules" / f"{name}.md").read_text(encoding="utf-8").strip()
    return (f"---\npaths:\n{paths}\n---\n"
            f"<!-- {MANAGED} 由 AIR/install.sh 產生（AIR/rules/{name}.md 的複本，paths 指向該層），請勿手動修改；更新 AIR 時會覆蓋 -->\n"
            f"{body}\n")


def path_rule_file(root: Path, host: hosts.Host, name: str) -> Path:
    return root / host.rules_dir / f"{PATH_RULES_PREFIX}{name}.md"


def is_managed_path_rule(path: Path) -> bool:
    """AIR 產生的路徑規則檔：存在而且帶標記（使用者自己放在同一個目錄的檔案不算）。"""
    return path.is_file() and MANAGED in path.read_text(encoding="utf-8", errors="replace")


def ensure_path_rules(root: Path, host: hosts.Host, cfg: Config) -> list[str]:
    """寫入或更新每一份規則檔；不是 AIR 建立的同名檔案略過。回傳安裝結果的說明。"""
    if not host.rules_dir:
        return []
    done: dict[str, list[str]] = {"created": [], "updated": [], "unchanged": []}
    skipped = []
    for name in rule_names():
        dest = path_rule_file(root, host, name)
        if dest.exists() and not is_managed_path_rule(dest):
            skipped.append(f"略過 {dest.relative_to(root).as_posix()}：已經存在，而且不是 AIR 建立的")
            continue
        text = path_rule_text(cfg, name)
        if not dest.exists():
            status = "created"
        else:
            status = "unchanged" if dest.read_text(encoding="utf-8") == text else "updated"
        if status != "unchanged":
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
        done[status].append(dest.name)
    summary = "、".join(f"{k} {len(v)}" for k, v in done.items() if v) or "無"
    return [f"{host.rules_dir.as_posix()}/：各層規則 {summary}（讀寫該層的檔案時由 {host.label} 自動載入）"] + skipped


def remove_path_rules(root: Path, host: hosts.Host) -> list[str]:
    if not host.rules_dir:
        return []
    base = root / host.rules_dir
    removed = []
    for p in sorted(base.glob(f"{PATH_RULES_PREFIX}*.md")) if base.is_dir() else []:
        if is_managed_path_rule(p):
            p.unlink()
            removed.append(p.relative_to(root).as_posix())
    _prune_empty_dirs(base, root)
    return removed


def path_rules_status(root: Path, host: hosts.Host, cfg: Config) -> tuple[list[str], list[str], list[str]]:
    """(缺少, 不是 AIR 建立的, 不是目前版本) 的規則檔名稱；給 doctor 用。"""
    missing, foreign, stale = [], [], []
    for name in rule_names():
        dest = path_rule_file(root, host, name)
        if not dest.exists():
            missing.append(dest.name)
        elif not is_managed_path_rule(dest):
            foreign.append(dest.name)
        elif dest.read_text(encoding="utf-8") != path_rule_text(cfg, name):
            stale.append(dest.name)
    return missing, foreign, stale


# ── 舊版的規則檔 ──

def remove_rules(cfg: Config) -> list[str]:
    """移除 2.2 以前放在各層與 tests 目錄的 CLAUDE.md（只動有 AIR:MANAGED 標記的）。"""
    removed = []
    for directory in [la.path for la in cfg.layers.values()] + [cfg.tests_dir]:
        dest = directory / "CLAUDE.md"
        if dest.exists() and MANAGED in dest.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]:
            dest.unlink()
            removed.append(dest.relative_to(cfg.root).as_posix())
    return removed


# ── commit 前檢查 ──

def precommit_block(indent: str) -> str:
    body = "\n".join(indent + line for line in [
        "- repo: local",
        "  hooks:",
        "    - id: air-check",
        "      name: AIR 規範檢查",
        "      entry: sh AIR/hooks/commit_check.sh",
        "      language: system",
        "      pass_filenames: false",
        "      always_run: true",
    ])
    return f"# {BEGIN} 這個區塊由 AIR 管理，請勿手動修改\n{body}\n# {END}"


def git_hook_path(root: Path) -> Path:
    out = subprocess.run(["git", "rev-parse", "--git-path", "hooks/pre-commit"], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    return (root / out.stdout.strip()).resolve()


def ensure_commit_check(root: Path) -> str:
    config = root / ".pre-commit-config.yaml"
    if config.exists():
        text = config.read_text(encoding="utf-8")
        if BEGIN in text:
            return ".pre-commit-config.yaml：unchanged"
        top_keys = re.findall(r"^([A-Za-z_][\w-]*):", text, re.M)
        if not top_keys or top_keys[-1] != "repos":
            return ".pre-commit-config.yaml：無法自動加入（repos 不是最後一個頂層設定）。請手動加入 AIR/templates/pre-commit-block.yaml 的內容"
        item = re.search(r"^(\s*)- repo:", text, re.M)
        ensure_block(config, precommit_block(item.group(1) if item else "  "), BEGIN)
        return ".pre-commit-config.yaml：加入 air-check（記得執行 pre-commit install）"
    hook = git_hook_path(root)
    if hook.exists() and MANAGED not in hook.read_text(encoding="utf-8"):
        return f"{hook.name}：已經有別的 git hook，沒有覆蓋。請在那個 hook 裡加上：sh AIR/hooks/commit_check.sh"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(f"#!/bin/sh\n# {MANAGED} 由 AIR 安裝；移除請執行 AIR/uninstall.sh\nexec sh AIR/hooks/commit_check.sh\n", encoding="utf-8")
    hook.chmod(0o755)
    return ".git/hooks/pre-commit：已安裝（git hook 不會進版本控制，每個 clone 都要執行一次 AIR/install.sh）"


def commit_check_status(root: Path) -> str:
    """給 status、doctor 看：commit 前檢查裝在哪裡。"""
    config = root / ".pre-commit-config.yaml"
    if config.exists() and BEGIN in config.read_text(encoding="utf-8", errors="replace"):
        return "pre-commit（.pre-commit-config.yaml）"
    if git_toplevel(root) is None:
        return "不是 git repo"
    hook = git_hook_path(root)
    if hook.exists() and MANAGED in hook.read_text(encoding="utf-8", errors="replace"):
        return "git hook（.git/hooks/pre-commit）"
    return "未安裝（執行 sh AIR/install.sh）"


def remove_commit_check(root: Path) -> str:
    config = root / ".pre-commit-config.yaml"
    if config.exists() and remove_block(config, f"# {BEGIN}", f"# {END}"):
        return ".pre-commit-config.yaml"
    if git_toplevel(root) is None:
        return ""
    hook = git_hook_path(root)
    if hook.exists() and MANAGED in hook.read_text(encoding="utf-8"):
        hook.unlink()
        return ".git/hooks/pre-commit"
    return ""


# ── 對外 ──

def _skill_lines(root: Path, host: hosts.Host) -> list[str]:
    installed, skipped = ensure_skills(root, host)
    return ([f"{host.skills_dir.as_posix()}/：{'、'.join(installed)}"] if installed else []) + skipped


def install(root: Path, package: str | None, package_dir: str | None, host_names: list[str] | None = None) -> list[str]:
    from .config import load

    selected = [hosts.HOSTS[name] for name in (host_names or list(hosts.HOSTS))]
    lines = [f"注意：{w}" for w in preflight(root)]
    if not (root / PROJECT_FILE).exists():
        if not package:
            candidates = detect_packages(root)
            if len(candidates) != 1:
                shown = "、".join(f"{n}（{d}）" for n, d in candidates) or "找不到"
                raise InstallError(f"無法確定套件。候選：{shown}。請用 --package 與 --package-dir 指定。")
            package, package_dir = candidates[0]
        package_dir = package_dir or package
    # 每個接點分開做：一個失敗（沙箱擋下寫入、設定檔壞掉）不影響其他接點，最後列出失敗的項目。重跑是安全的。
    steps = [
        (".air/project.toml", lambda: [f".air/project.toml：{write_project_file(root, package or '', package_dir or '')}"]),
        (".air/python、.air/hosts", lambda: write_local_state(root, selected) or []),
        ("CLAUDE.md、AGENTS.md", lambda: ensure_instructions(root)),
    ]
    for host in selected:
        steps.append((host.hooks_file.as_posix(), lambda h=host: [f"{h.hooks_file.as_posix()}：{ensure_hooks(root, h)}"] + ([f"注意：{h.note}"] if h.note else [])))
        steps.append((host.skills_dir.as_posix(), lambda h=host: _skill_lines(root, h)))
        if host.rules_dir:
            steps.append((host.rules_dir.as_posix(), lambda h=host: ensure_path_rules(root, h, load(root))))
    steps += [
        ("commit 前檢查", lambda: [ensure_commit_check(root)] if git_toplevel(root) else []),
        ("舊版規則檔", lambda: [f"移除 {r}（舊版的規則檔；規則改由 hook 注入）" for r in remove_rules(load(root))]),
    ]
    for label, run in steps:
        try:
            lines += run()
        except (OSError, ValueError, ConfigError) as e:
            lines.append(f"{FAILED}{label}：{describe_error(e)}")
    return lines


def uninstall(root: Path, purge: bool) -> list[str]:
    from .config import load

    lines = []
    try:
        lines += [f"移除 {r}" for r in remove_rules(load(root))]
    except ConfigError:
        pass
    for path in (claude_md_path(root), root / "AGENTS.md"):
        if remove_block(path, f"<!-- {BEGIN}", f"<!-- {END}"):
            lines.append(f"移除 {path.relative_to(root).as_posix()} 的區塊")
    for host in hosts.HOSTS.values():
        if remove_hooks(root, host):
            lines.append(f"移除 {host.hooks_file.as_posix()} 的 hook")
        lines += [f"移除 skill {s}" for s in remove_skills(root, host)]
        lines += [f"移除 {r}" for r in remove_path_rules(root, host)]
    removed = remove_commit_check(root)
    if removed:
        lines.append(f"移除 {removed} 的 commit 前檢查")
    (root / HOSTS_FILE).unlink(missing_ok=True)  # 接點都拿掉了，完整性檢查不該再要求它們存在
    if purge and (root / ".air").exists():
        shutil.rmtree(root / ".air")
        lines.append("刪除 .air/")
    return lines
