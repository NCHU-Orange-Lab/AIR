"""軟規則：函式的語句數與循環複雜度（粗略計算，只用於提醒）。"""

from __future__ import annotations

import ast
from collections.abc import Iterator

FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _walk_own(fn: ast.AST) -> Iterator[ast.AST]:
    """走訪函式本體，但不進入巢狀的函式或類別（它們各自計算）。"""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, SCOPES):
            stack.extend(ast.iter_child_nodes(node))


def statements(fn: ast.AST) -> int:
    return sum(1 for n in _walk_own(fn) if isinstance(n, ast.stmt))


def complexity(fn: ast.AST) -> int:
    score = 1
    for n in _walk_own(fn):
        if isinstance(n, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp, ast.ExceptHandler, ast.match_case)):
            score += 1
        elif isinstance(n, ast.BoolOp):
            score += len(n.values) - 1
        elif isinstance(n, ast.comprehension):
            score += 1 + len(n.ifs)
    return score


def functions(tree: ast.AST) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, FUNCS):
            yield node
