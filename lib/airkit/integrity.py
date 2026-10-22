"""套件與接點的完整性（AIR009）：AIR/MANIFEST.sha256 與 hook、指引的接點。

pre-edit hook 攔得到編輯工具；用 Bash 或其他程式改了 AIR/、拿掉 hook，就靠這裡在全專案檢查（結束前、commit 前）抓到。
MANIFEST 由 AIR 的開發 repo 產生（python3 AIR/air.py manifest），列出套件裡每個檔案的 SHA-256（tests/ 與 .github/ 除外）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import hosts, install
from .config import KIT_DIR

MANIFEST = "MANIFEST.sha256"
EXCLUDE_DIRS = {"tests", ".github", "__pycache__", ".git", ".pytest_cache"}  # 不影響行為的目錄不列入：測試、開發 repo 的 CI 設定、快取
EXCLUDE_SUFFIXES = {".pyc"}
FIX = "在專案裡不要修改 AIR/，要調整規範請照 skill air-maintain-rules；接點用 sh AIR/install.sh 重新安裝。"


def kit_files(kit: Path = KIT_DIR) -> list[Path]:
    out = []
    for p in sorted(kit.rglob("*")):
        rel = p.relative_to(kit)
        if not p.is_file() or rel.name == MANIFEST or p.suffix in EXCLUDE_SUFFIXES or EXCLUDE_DIRS & set(rel.parts):
            continue
        out.append(p)
    return out


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()  # 不受 checkout 的換行設定影響


def write_manifest(kit: Path = KIT_DIR) -> int:
    files = kit_files(kit)
    lines = [f"{file_digest(p)}  {p.relative_to(kit).as_posix()}" for p in files]
    (kit / MANIFEST).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(files)


def read_manifest(kit: Path = KIT_DIR) -> dict[str, str] | None:
    path = kit / MANIFEST
    if not path.exists():
        return None
    expected = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, _, name = line.partition("  ")
            expected[name.strip()] = digest.strip()
    return expected


def manifest_problems(kit: Path = KIT_DIR) -> list[tuple[Path, str]]:
    """(檔案, 問題)。空＝套件與 MANIFEST 一致。"""
    expected = read_manifest(kit)
    if expected is None:
        return [(kit / MANIFEST, "找不到 MANIFEST.sha256，套件不完整；請用完整的 AIR/ 重新複製")]
    actual = {p.relative_to(kit).as_posix(): file_digest(p) for p in kit_files(kit)}
    problems = []
    for name in sorted(set(expected) | set(actual)):
        if name not in actual:
            problems.append((kit / name, "檔案被刪除"))
        elif name not in expected:
            problems.append((kit / name, "多出不屬於套件的檔案"))
        elif expected[name] != actual[name]:
            problems.append((kit / name, "內容被修改"))
    return problems


def wiring_problems(root: Path) -> list[tuple[Path, str]]:
    """接點是否還在。只在這台電腦跑過 install.sh（有 .air/hosts）時檢查，CI 的乾淨 checkout 不會誤報。

    install.sh 裝了哪些宿主就查哪些：設定檔要在、AIR 的 hook 要齊全。沒裝的宿主（例如只用 Claude Code）不算問題。
    """
    installed = install.installed_hosts(root)
    if installed is None:
        return []
    problems = []
    for host in installed:
        path = root / host.hooks_file
        if not path.exists():
            problems.append((path, f"{host.label} 的 hook 設定不見了"))
            continue
        try:
            data = install.read_hooks(path)
        except ValueError:
            data = {}
        missing = install.missing_hooks(data, host)
        if missing:
            problems.append((path, f"缺少 AIR 的 hook：{'、'.join(missing)}"))
    for path in (install.claude_md_path(root), root / "AGENTS.md"):
        if not path.exists() or install.BEGIN not in path.read_text(encoding="utf-8"):
            problems.append((path, "缺少 AIR 的指引區塊"))
    return problems


def problems(root: Path) -> list[tuple[Path, str]]:
    return manifest_problems() + wiring_problems(root)
