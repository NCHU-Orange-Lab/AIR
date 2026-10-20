"""安裝與移除。可以重複執行；只動 AIR 自己建立或標記的內容。

建立的接點：
  CLAUDE.md、AGENTS.md          加入一個標記區塊
  .claude/settings.json          加入三個 hook
  .claude/skills/air-*/          從 AIR/skills 複製（含 .air-managed 標記）
  commit 前檢查                  .pre-commit-config.yaml 加區塊，或 .git/hooks/pre-commit
  .air/project.toml、.air/python 專案設定（移除時保留，除非 --purge）
  各層與 tests 的 CLAUDE.md      從 AIR/rules 複製（含標記）
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .config import KIT_DIR, PROJECT_FILE, Config

BEGIN, END = "AIR:BEGIN", "AIR:END"
MANAGED = "AIR:MANAGED"
SKILL_MARK = ".air-managed"
HOOKS = [
    ("PreToolUse", "Bash", "pre_bash.sh"),
    ("PostToolUse", "Edit|Write|MultiEdit", "post_edit.sh"),
    ("Stop", None, "stop.sh"),
]
RULE_HEADER = f"<!-- {MANAGED} 這個檔案由 AIR 產生，更新 AIR 時會覆蓋。要修改規則，請改 AIR 的開發 repo。 -->\n"
SKIP_DIRS = {"tests", "test", "docs", "scripts", "AIR", ".air", "build", "dist", "examples", "venv", ".venv"}


class InstallError(Exception):
    pass


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
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root, capture_output=True, text=True, check=True)
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


# ── 各個接點 ──

def write_project_file(root: Path, package: str, package_dir: str) -> str:
    path = root / PROJECT_FILE
    if path.exists():
        return "unchanged"
    template = (KIT_DIR / "templates" / "project.toml").read_text(encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template.replace("{package}", package).replace("{package_dir}", package_dir), encoding="utf-8")
    return "created"


def write_python_file(root: Path) -> None:
    """記下這台電腦上可用的 Python 3.11+。路徑因機器而異，所以不進版本控制。"""
    air = root / ".air"
    (air / "python").write_text(sys.executable + "\n", encoding="utf-8")
    ignore = air / ".gitignore"
    if not ignore.exists():
        ignore.write_text("# 每台電腦各自的 Python 路徑，由 AIR/install.sh 產生\npython\n", encoding="utf-8")


def claude_md_path(root: Path) -> Path:
    if not (root / "CLAUDE.md").exists() and (root / ".claude" / "CLAUDE.md").exists():
        return root / ".claude" / "CLAUDE.md"
    return root / "CLAUDE.md"


def ensure_instructions(root: Path) -> list[str]:
    claude = claude_md_path(root)
    imports = ["@AIR/AGENTS.md"]
    # 新建 CLAUDE.md 後，Claude Code 就不會再讀 AGENTS.md，所以要一併匯入
    if not claude.exists() and (root / "AGENTS.md").exists():
        imports.insert(0, "@AGENTS.md")
    out = [f"{claude.relative_to(root)}：{ensure_block(claude, md_block(chr(10).join(imports)), BEGIN)}"]
    pointer = "本專案採用 AIR 規範。開始任何工作之前，先讀 `AIR/AGENTS.md`。"
    out.append(f"AGENTS.md：{ensure_block(root / 'AGENTS.md', md_block(pointer), BEGIN)}")
    return out


def hook_command(script: str) -> str:
    return f'"$CLAUDE_PROJECT_DIR"/AIR/hooks/{script}'


def ensure_settings(root: Path) -> str:
    path = root / ".claude" / "settings.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    hooks = data.setdefault("hooks", {})
    changed = False
    for event, matcher, script in HOOKS:
        entries = hooks.setdefault(event, [])
        if any(script in h.get("command", "") and "AIR/hooks/" in h.get("command", "") for e in entries for h in e.get("hooks", [])):
            continue
        entry = {"hooks": [{"type": "command", "command": hook_command(script)}]}
        if matcher:
            entry = {"matcher": matcher, **entry}
        entries.append(entry)
        changed = True
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return "updated" if changed else "unchanged"


def remove_settings(root: Path) -> bool:
    path = root / ".claude" / "settings.json"
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    hooks = data.get("hooks", {})
    for event in list(hooks):
        kept = []
        for entry in hooks[event]:
            entry["hooks"] = [h for h in entry.get("hooks", []) if "AIR/hooks/" not in h.get("command", "")]
            if entry["hooks"]:
                kept.append(entry)
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not hooks:
        data.pop("hooks", None)
    if data:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        path.unlink()
    return True


def ensure_skills(root: Path) -> list[str]:
    out = []
    for src in sorted((KIT_DIR / "skills").iterdir()):
        dest = root / ".claude" / "skills" / src.name
        if dest.exists() and not (dest / SKILL_MARK).exists():
            out.append(f"略過 {dest.relative_to(root)}：已經存在，而且不是 AIR 建立的")
            continue
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        (dest / SKILL_MARK).write_text("這個 skill 由 AIR 複製，更新 AIR 時會覆蓋。\n", encoding="utf-8")
        out.append(str(dest.relative_to(root)))
    return out


def remove_skills(root: Path) -> list[str]:
    base = root / ".claude" / "skills"
    removed = []
    for d in sorted(base.iterdir()) if base.is_dir() else []:
        if (d / SKILL_MARK).exists():
            shutil.rmtree(d)
            removed.append(d.name)
    return removed


def refresh_rules(cfg: Config) -> list[str]:
    """把 AIR/rules 複製成各層（與 tests）的 CLAUDE.md；只處理已經存在的目錄。"""
    targets = [(la.path, la.name) for la in cfg.layers.values()] + [(cfg.tests_dir, "tests")]
    out = []
    for directory, name in targets:
        src = KIT_DIR / "rules" / f"{name}.md"
        dest = directory / "CLAUDE.md"
        if not directory.is_dir() or not src.exists():
            continue
        if dest.exists() and MANAGED not in dest.read_text(encoding="utf-8").split("\n", 1)[0]:
            out.append(f"略過 {dest.relative_to(cfg.root)}：已經存在，而且不是 AIR 建立的")
            continue
        content = RULE_HEADER + src.read_text(encoding="utf-8")
        if not dest.exists() or dest.read_text(encoding="utf-8") != content:
            dest.write_text(content, encoding="utf-8")
        out.append(str(dest.relative_to(cfg.root)))
    return out


def remove_rules(cfg: Config) -> list[str]:
    removed = []
    for directory in [la.path for la in cfg.layers.values()] + [cfg.tests_dir]:
        dest = directory / "CLAUDE.md"
        if dest.exists() and MANAGED in dest.read_text(encoding="utf-8").split("\n", 1)[0]:
            dest.unlink()
            removed.append(str(dest.relative_to(cfg.root)))
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
    out = subprocess.run(["git", "rev-parse", "--git-path", "hooks/pre-commit"], cwd=root, capture_output=True, text=True, check=True)
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

def install(root: Path, package: str | None, package_dir: str | None) -> list[str]:
    from .config import load

    lines = [f"注意：{w}" for w in preflight(root)]
    if not (root / PROJECT_FILE).exists():
        if not package:
            candidates = detect_packages(root)
            if len(candidates) != 1:
                shown = "、".join(f"{n}（{d}）" for n, d in candidates) or "找不到"
                raise InstallError(f"無法確定套件。候選：{shown}。請用 --package 與 --package-dir 指定。")
            package, package_dir = candidates[0]
        package_dir = package_dir or package
    lines.append(f".air/project.toml：{write_project_file(root, package or '', package_dir or '')}")
    write_python_file(root)
    lines += ensure_instructions(root)
    lines.append(f".claude/settings.json：{ensure_settings(root)}")
    lines += [f"skill：{s}" for s in ensure_skills(root)]
    if git_toplevel(root):
        lines.append(ensure_commit_check(root))
    lines += [f"規則：{r}" for r in refresh_rules(load(root))]
    return lines


def uninstall(root: Path, purge: bool) -> list[str]:
    from .config import ConfigError, load

    lines = []
    try:
        lines += [f"移除 {r}" for r in remove_rules(load(root))]
    except ConfigError:
        pass
    for path in (claude_md_path(root), root / "AGENTS.md"):
        if remove_block(path, f"<!-- {BEGIN}", f"<!-- {END}"):
            lines.append(f"移除 {path.relative_to(root)} 的區塊")
    if remove_settings(root):
        lines.append("移除 .claude/settings.json 的 hook")
    lines += [f"移除 skill {s}" for s in remove_skills(root)]
    removed = remove_commit_check(root)
    if removed:
        lines.append(f"移除 {removed} 的 commit 前檢查")
    if purge and (root / ".air").exists():
        shutil.rmtree(root / ".air")
        lines.append("刪除 .air/")
    return lines
