"""測試共用：建立隔離的暫存專案，直接呼叫 airkit 或執行 air.py。

只用標準庫（unittest），跟套件一樣零依賴：
  python3 -m unittest discover -s tests          # 在 AIR/ 目錄下執行
"""

from __future__ import annotations

import json
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
test-command = {test_command}
test-timeout = {test_timeout}

[imports]
extra = [{extra}]

[migration]
phase = "{phase}"
allow-legacy = [{allow}]
"""

# 給 test-command 用的指令（用目前的直譯器，不依賴 PATH 上的 python3）
TESTS_PASS = f"{sys.executable} -c 'import sys; sys.exit(0)'"
TESTS_FAIL = f"{sys.executable} -c 'import sys; sys.exit(1)'"


class Project:
    """暫存目錄裡的範例專案：AIR/ 的複本、.air/project.toml、套件 demo/。"""

    def __init__(self, phase: str = "installed", allow_legacy: str = "", extra: str = "", git: bool = False,
                 test_command: str = "", test_timeout: int = 600):
        self.root = Path(tempfile.mkdtemp(prefix="air-test-")).resolve()
        shutil.copytree(KIT, self.root / "AIR", ignore=shutil.ignore_patterns(".git", "tests", "__pycache__"))
        self.write(".air/project.toml", PROJECT_TOML.format(
            phase=phase, allow=allow_legacy, extra=extra, test_command=json.dumps(test_command), test_timeout=test_timeout))
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

    def receipt(self) -> dict:
        return json.loads((self.root / ".air" / "verify.json").read_text(encoding="utf-8"))

    def history(self) -> list[dict]:
        path = self.root / ".air" / "history.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def commit(self, message: str = "x") -> str:
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", message], cwd=self.root, check=True, capture_output=True)  # 吃掉 pre-commit hook 的輸出
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root, capture_output=True, text=True, check=True).stdout.strip()

    def air(self, *args: str, stdin: str = "", env: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.root / "AIR" / "air.py"), *args],
            cwd=cwd or self.root, input=stdin, capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, **(env or {})},
        )

    def hook(self, event: str, payload: dict | str, host: str | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
        """執行 air.py hook <event>，payload 可以是 dict（會轉成 JSON）或原始字串。"""
        args = ["hook", event] + (["--host", host] if host else [])
        return self.air(*args, stdin=payload if isinstance(payload, str) else json.dumps(payload), cwd=cwd)

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
    test_command = ""
    test_timeout = 600

    def setUp(self) -> None:
        self.p = Project(phase=self.phase, allow_legacy=self.allow_legacy, extra=self.extra, git=self.git,
                         test_command=self.test_command, test_timeout=self.test_timeout)
        self.addCleanup(self.p.cleanup)
