"""打包檢查：setuptools 的「明確 packages 清單」有沒有列到各層的子套件。

試跑（cookiecutter）：pyproject.toml 寫 `packages = ["cookiecutter"]`，建立分層後新的 cookiecutter.steps 等子套件不在清單裡。
editable 安裝照常運作（直接指到原始碼目錄），發行的 wheel 卻缺少所有層，使用者 import 時才爆。AIR 只做靜態檢查、不建 wheel、不改專案的檔案：
指出缺哪些，印出可以貼進清單的內容。

只有 setuptools 的「明確清單」有這個問題：`[tool.setuptools.packages.find]`、`find:`、hatch、poetry、flit 本來就含子套件，不報。
讀的來源：pyproject.toml 的 [tool.setuptools] packages、setup.cfg 的 [options] packages、setup.py 裡字面的 setup(packages=[...])；
setup.py 的值不是字面的清單時只說「無法判斷」。
"""

from __future__ import annotations

import ast
import configparser
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .config import Config


@dataclass(frozen=True)
class Declared:
    source: str                         # 給人看：哪個檔案的哪一段
    packages: tuple[str, ...] | None    # 明確清單；None＝讀不出來
    unknown: str = ""                   # 讀不出來的原因


@dataclass(frozen=True)
class Problem:
    source: str
    missing: tuple[str, ...]            # 清單裡缺少的子套件（點分名稱）
    has_code: bool                      # 缺少的子套件裡已經有實際程式（不只是空殼）：wheel 現在就是壞的

    def hint(self) -> str:
        names = "、".join(self.missing)
        add = ", ".join(f'"{m}"' for m in self.missing)
        return (f"{self.source} 是明確清單，沒有列到：{names}。wheel 不會包含它們（editable 安裝看不出來）；"
                f"在清單加上：{add}（或改用 [tool.setuptools.packages.find]）")


def _pyproject(root: Path) -> list[Declared]:
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    pk = data.get("tool", {}).get("setuptools", {}).get("packages")
    if isinstance(pk, list) and all(isinstance(x, str) for x in pk):
        return [Declared("pyproject.toml 的 [tool.setuptools] packages", tuple(pk))]
    return []


def _setup_cfg(root: Path) -> list[Declared]:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        if not parser.read(root / "setup.cfg", encoding="utf-8"):
            return []
        value = parser.get("options", "packages", fallback="").strip()
    except (OSError, configparser.Error):
        return []
    if not value or value.startswith("find"):
        return []
    return [Declared("setup.cfg 的 [options] packages", tuple(x.strip() for x in re.split(r"[,\n]", value) if x.strip()))]


def _setup_py(root: Path) -> list[Declared]:
    try:
        tree = ast.parse((root / "setup.py").read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not ((isinstance(func, ast.Name) and func.id == "setup") or (isinstance(func, ast.Attribute) and func.attr == "setup")):
            continue
        for kw in node.keywords:
            if kw.arg != "packages":
                continue
            if isinstance(kw.value, (ast.List, ast.Tuple)) and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in kw.value.elts):
                return [Declared("setup.py 的 setup(packages=...)", tuple(e.value for e in kw.value.elts))]
            return [Declared("setup.py 的 setup(packages=...)", None, "不是字面的清單（可能是 find_packages() 或變數），無法判斷")]
    return []


def declared(root: Path) -> list[Declared]:
    return _pyproject(root) + _setup_cfg(root) + _setup_py(root)


def _has_code(path: Path) -> bool:
    """模組裡有 docstring 以外的東西。層的 __init__.py 與 contracts 的樣板只有 docstring 或空白：算空殼。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return True  # 讀不了就保守地當成有程式
    return any(not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)) for n in tree.body)


def expected(cfg: Config) -> dict[str, bool]:
    """該列進 packages 的子套件（點分名稱）→ 裡面有沒有實際程式：每個已建立的層，加上層底下含 __init__.py 的巢狀目錄（例如 adapters/torch/）。"""
    out: dict[str, bool] = {}
    for layer in cfg.layers.values():
        if not layer.path.is_dir():
            continue
        dirs = [layer.path] + sorted(d.parent for d in layer.path.rglob("__init__.py") if d.parent != layer.path)
        for d in dirs:
            rel = d.relative_to(layer.path).parts
            name = ".".join((layer.module, *rel))
            out[name] = any(_has_code(f) for f in d.glob("*.py"))
    return out


def problems(cfg: Config) -> list[Problem]:
    want = expected(cfg)
    out = []
    for d in declared(cfg.root):
        if d.packages is None:
            continue
        missing = tuple(n for n in want if n not in d.packages)
        if missing:
            out.append(Problem(d.source, missing, any(want[n] for n in missing)))
    return out


def notes(cfg: Config) -> list[str]:
    """讀不出來的清單：只說明，不算問題。"""
    return [f"{d.source}：{d.unknown}" for d in declared(cfg.root) if d.packages is None and d.unknown]
