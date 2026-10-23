"""AST 工具：import 解析、作用域內的名稱綁定與別名、前綴比對。"""

from __future__ import annotations

import ast
from collections.abc import Iterator

SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


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


# ── 作用域 ──

def iter_own(scope: ast.AST) -> Iterator[ast.AST]:
    """走訪 scope 的子孫，但不進入巢狀的作用域（它們各自處理）。"""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, SCOPES):
            stack.extend(ast.iter_child_nodes(node))


def bindings(scope: ast.AST) -> tuple[dict[str, str], set[str]]:
    """scope 本體直接綁定的名稱：(import 別名 → 完整名稱, 所有綁定的名稱)。

    別名例如 pd → pandas、datetime → datetime.datetime。綁定的名稱還包括參數、賦值、def、class，
    用來判斷 print、sample 這類名稱是不是已經被區域定義遮蔽。
    """
    aliases: dict[str, str] = {}
    bound: set[str] = set()
    for node in iter_own(scope):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:
                    head = alias.name.split(".")[0]
                    aliases[head] = head
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                if node.level == 0 and node.module:
                    aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
                else:
                    bound.add(alias.asname or alias.name)  # 相對 import：名稱已綁定，但不是外部模組
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
    return aliases, bound | set(aliases)


def scoped_calls(tree: ast.AST) -> Iterator[tuple[ast.Call, dict[str, str], set[str]]]:
    """每個 Call，加上它所在位置看得到的別名與綁定：本作用域、外層函式、模組（class 本體不會洩漏給方法）。"""

    def visit(scope: ast.AST, chain: list[tuple[dict[str, str], set[str], bool]]) -> Iterator:
        own = (*bindings(scope), isinstance(scope, ast.ClassDef))
        aliases: dict[str, str] = {}
        bound: set[str] = set()
        for a, b, _ in [s for s in chain if not s[2]] + [own]:
            aliases.update(a)
            bound |= b
        for node in iter_own(scope):
            if isinstance(node, ast.Call):
                yield node, aliases, bound
            elif isinstance(node, SCOPES):
                yield from visit(node, chain + [own])

    yield from visit(tree, [])


# ── 呼叫 ──

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


def _is_none(expr: ast.expr) -> bool:
    return isinstance(expr, ast.Constant) and expr.value is None


def has_required_arg(node: ast.Call, keyword: str, position: int) -> bool:
    """keyword=None、位置上的 None、空的 **{} 都算沒給；無法靜態判斷的 *args、**kwargs 視為已提供。"""
    for kw in node.keywords:
        if kw.arg == keyword:
            return not _is_none(kw.value)
        if kw.arg is None and not (isinstance(kw.value, ast.Dict) and not kw.value.keys):
            return True
    if any(isinstance(a, ast.Starred) for a in node.args):
        return True
    return len(node.args) > position and not _is_none(node.args[position])


def is_reexport_shell(tree: ast.Module) -> bool:
    """模組裡只有 docstring、import、pass 與 __all__：搬進層之後留在原地的轉接檔，或空的 __init__.py。沒有任何自己的邏輯。"""
    for n in tree.body:
        if isinstance(n, (ast.Import, ast.ImportFrom, ast.Pass)):
            continue
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
            continue
        targets = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, (ast.AugAssign, ast.AnnAssign)) else []
        if targets and all(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
            continue
        return False
    return True
