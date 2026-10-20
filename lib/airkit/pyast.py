"""AST 工具：import 解析、別名解析、前綴比對。"""

from __future__ import annotations

import ast
from collections.abc import Iterator


def matches(name: str, prefixes) -> str | None:
    """回傳 name 符合的最長前綴（完全相同，或是其子模組／屬性）。"""
    hits = [p for p in prefixes if name == p or name.startswith(p + ".")]
    return max(hits, key=len) if hits else None


def import_targets(tree: ast.AST, package: str) -> Iterator[tuple[ast.stmt, str]]:
    """列出每個 import 指向的完整名稱。

    `from a.b import c` 會得到 `a.b.c`（c 可能是子模組，也可能是屬性；前綴比對兩者都適用）。
    相對 import 依 package（目前檔案所在的套件）換算成完整名稱。
    """
    parts = package.split(".") if package else []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = parts[: max(len(parts) - (node.level - 1), 0)]
                prefix = ".".join(base + ([node.module] if node.module else []))
            else:
                prefix = node.module or ""
            for alias in node.names:
                if alias.name == "*" or not prefix:
                    yield node, prefix or alias.name
                else:
                    yield node, f"{prefix}.{alias.name}"


def alias_map(tree: ast.AST) -> dict[str, str]:
    """區域名稱 → 完整名稱，例如 pd → pandas、datetime → datetime.datetime。"""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:
                    head = alias.name.split(".")[0]
                    aliases[head] = head
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                if alias.name != "*":
                    aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def dotted(expr: ast.expr) -> str | None:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        base = dotted(expr.value)
        return f"{base}.{expr.attr}" if base else None
    return None


def qualified(expr: ast.expr, aliases: dict[str, str]) -> str | None:
    """把 `pd.read_csv` 這類表達式換算成 `pandas.read_csv`；不是來自 import 的名稱回傳 None。"""
    name = dotted(expr)
    if not name:
        return None
    head, _, rest = name.partition(".")
    base = aliases.get(head)
    if base is None:
        return None
    return f"{base}.{rest}" if rest else base


def call_name(node: ast.Call) -> tuple[str | None, bool]:
    """回傳（名稱, 是否為方法呼叫）。"""
    if isinstance(node.func, ast.Name):
        return node.func.id, False
    if isinstance(node.func, ast.Attribute):
        return node.func.attr, True
    return None, False


def has_required_arg(node: ast.Call, keyword: str, position: int) -> bool:
    if any(kw.arg is None or kw.arg == keyword for kw in node.keywords):  # kw.arg is None：**kwargs
        return True
    if any(isinstance(a, ast.Starred) for a in node.args):
        return True
    return len(node.args) > position
