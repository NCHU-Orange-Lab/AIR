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

from .checks import classify, layer_files, module_of
from .config import Allowance, Config
from .pyast import import_targets, is_reexport_shell, matches

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


# ── allow-legacy 過期 ──

NO_IMPORT, NO_MODULE = "沒有任何 import 用到", "模組已不存在"


def _module_exists(module: str, cfg: Config) -> bool:
    rest = module.split(".")[1:] if module.split(".")[0] == cfg.package else None
    if rest is None:
        return True  # 不是這個套件的模組：我們判斷不了，不當成過期
    base = cfg.package_dir.joinpath(*rest)
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def stale_allowances(cfg: Config) -> list[tuple[Allowance, str]]:
    """allow-legacy 裡已經不需要的條目：那一層已經沒有任何 import 符合它（搬完了、改成直接 import 新的層了）。
    回傳 (條目, 原因)。別人怎麼做：import-linter 預設把對不上任何 import 的例外當錯誤（unmatched_ignore_imports_alerting），
    tach 的 --exact 對宣告了卻沒用到的依賴也報錯。AIR 預設只提醒，因為移除條目要改 .air/project.toml，要使用者同意。"""
    out = []
    for a in cfg.allow_legacy:
        layer = cfg.layers.get(a.layer)
        used = False
        for f in (layer_files(layer) if layer else []):
            facts = module_of(f, cfg)
            try:
                tree = ast.parse(f.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, ValueError):
                continue
            package = facts[0] if facts and facts[1] else (facts[0].rpartition(".")[0] if facts else "")
            if any(classify(t, cfg)[0] == "legacy" and matches(classify(t, cfg)[1], [a.module]) for _, t in import_targets(tree, package)):
                used = True
                break
        if not used:
            out.append((a, NO_IMPORT if _module_exists(a.module, cfg) else NO_MODULE))
    return out


def describe_stale(stale: list[tuple[Allowance, str]]) -> list[str]:
    return [f"{a.layer} → {a.module}（{why}）" for a, why in stale]


# ── 防倒退 ──

def new_logic_files(cfg: Config, legacy_paths: list[Path], added: set[str]) -> list[Path]:
    """起點之後新增、現在還在層外、而且有自己程式的檔案。added 是相對根目錄的 posix 路徑（git 看到的新增，改名不算）。
    轉接檔與套件標記不算：搬移時留下的轉接檔不是倒退。Google 的大規模變更說「幾乎都需要一個防止再引入舊東西的機制」。"""
    root = cfg.root.resolve()
    out = []
    for p in legacy_paths:
        try:
            rel = p.resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        if rel in added and kind(p) == LOGIC:
            out.append(p)
    return out
