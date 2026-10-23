"""層外的檔案（舊程式碼）分三類，status、批次紀錄、final 驗收都用這裡的判斷，數字才會一致。

- 有邏輯（logic）：有自己的程式。遷移的對象。
- 轉接檔（shim）：只有 import、`__all__`、docstring 或 `pass`，而且不是空的 `__init__.py`。搬走程式之後留在原地讓舊 import 路徑繼續可用；
  階段 6 第 1 步要移除，刻意保留的相容層要加進 migration.exclude 並在遷移報告寫明原因。
- 套件標記（marker）：沒有 import、只有 docstring 的 `__init__.py`。Python 要求的，永遠合法。

試跑（csvkit）：final 驗收把全部層外檔案混在一起算（23 個），batch close 把轉接檔分開（17 個加 6 個），而且空的 __init__.py 也被當成轉接檔。
判斷只看語法：轉接檔裡多一個小包裝函式，或用 try／except 包 import，都會被視為有邏輯。
"""

from __future__ import annotations

import ast
from pathlib import Path

from .pyast import is_reexport_shell

LOGIC, SHIM, MARKER = "logic", "shim", "marker"
KINDS = (LOGIC, SHIM, MARKER)
LABELS = {LOGIC: "有自己的程式", SHIM: "轉接檔", MARKER: "套件標記"}


def kind(path: Path) -> str:
    """這個層外檔案屬於哪一類。讀不了或語法錯誤就保守地當成有邏輯。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return LOGIC
    if not is_reexport_shell(tree):
        return LOGIC
    has_import = any(isinstance(n, (ast.Import, ast.ImportFrom)) for n in tree.body)
    return MARKER if path.name == "__init__.py" and not has_import else SHIM


def split(paths: list[Path]) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {k: [] for k in KINDS}
    for p in paths:
        out[kind(p)].append(p)
    return out


def counts(paths: list[Path]) -> dict[str, int]:
    return {k: len(v) for k, v in split(paths).items()}


def describe(c: dict[str, int]) -> str:
    """一行說明，例如「17 個有自己的程式、5 個轉接檔、1 個套件標記」。"""
    return "、".join(f"{c[k]} 個{LABELS[k]}" for k in KINDS)
