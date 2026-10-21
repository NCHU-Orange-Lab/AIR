"""測試共用：建立隔離的暫存專案，直接呼叫 airkit 或執行 air.py。

只用標準庫（unittest），跟套件一樣零依賴：
  python3 -m unittest discover -s tests          # 在 AIR/ 目錄下執行
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]  # AIR/
sys.path.insert(0, str(KIT / "lib"))

from airkit.checks import check, check_source  # noqa: E402
from airkit.config import Config, load  # noqa: E402

PROJECT_TOML = """[project]
package = "demo"
package-dir = "demo"
tests-dir = "tests"
test-command = ""

[imports]
extra = [{extra}]

[migration]
phase = "{phase}"
allow-legacy = [{allow}]
"""


class Project:
    """暫存目錄裡的範例專案：AIR/ 的複本、.air/project.toml、套件 demo/。"""

    def __init__(self, phase: str = "installed", allow_legacy: str = "", extra: str = "", git: bool = False):
        self.root = Path(tempfile.mkdtemp(prefix="air-test-")).resolve()
        shutil.copytree(KIT, self.root / "AIR", ignore=shutil.ignore_patterns(".git", "tests", "__pycache__"))
        self.write(".air/project.toml", PROJECT_TOML.format(phase=phase, allow=allow_legacy, extra=extra))
        (self.root / "demo").mkdir()
        (self.root / "demo" / "__init__.py").write_text("", encoding="utf-8")
        if git:
            subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
            subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=self.root, check=True)
            subprocess.run(["git", "config", "user.name", "t"], cwd=self.root, check=True)

    def cleanup(self) -> None:
        for d in self.root.rglob("*"):
            if d.is_dir() and not os.access(d, os.W_OK):
                d.chmod(0o755)
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rel: str, text: str | bytes) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
        return path

    def layer(self, name: str, **files: str) -> Path:
        """建立一層（含 __init__.py），並寫入 files（模組名 → 原始碼）。"""
        directory = self.root / "demo" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "__init__.py").write_text("", encoding="utf-8")
        for module, source in files.items():
            (directory / f"{module}.py").write_text(source, encoding="utf-8")
        return directory

    def cfg(self) -> Config:
        return load(self.root)

    def codes(self, source: str, layer: str = "steps", module: str = "m") -> list[str]:
        """對一段原始碼做單檔檢查，回傳硬規則代碼（依行號排序）。"""
        cfg = self.cfg()
        path = self.root / "demo" / layer / f"{module}.py"
        report = check_source(source, f"demo.{layer}.{module}", False, cfg.layers[layer], cfg, path)
        return [v.code for v in report.hard]

    def soft_codes(self, source: str, layer: str = "steps") -> list[str]:
        cfg = self.cfg()
        path = self.root / "demo" / layer / "m.py"
        report = check_source(source, f"demo.{layer}.m", False, cfg.layers[layer], cfg, path)
        return [v.code for v in report.soft]

    def check_all(self) -> list[str]:
        return [v.code for v in check(self.cfg()).hard]

    def air(self, *args: str, stdin: str = "", env: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.root / "AIR" / "air.py"), *args],
            cwd=cwd or self.root, input=stdin, capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, **(env or {})},
        )

    def sh(self, command: str, stdin: str = "", env: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            command, shell=True, cwd=cwd or self.root, input=stdin, capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, **(env or {})},
        )


class ProjectTestCase(unittest.TestCase):
    phase = "installed"
    allow_legacy = ""
    extra = ""
    git = False

    def setUp(self) -> None:
        self.p = Project(phase=self.phase, allow_legacy=self.allow_legacy, extra=self.extra, git=self.git)
        self.addCleanup(self.p.cleanup)
