"""規則檢查。只檢查位於「層」裡的檔案；還沒遷移的舊程式碼不檢查。

硬規則（違規就擋）：
  AIR000 無法解析        AIR001 禁用的內建函式    AIR002 外部 import 不在白名單
  AIR003 層內循環 import  AIR004 禁用的方法名稱    AIR005 缺少必要參數
  AIR006 禁用的函式      AIR007 跨層方向錯誤      AIR008 import 尚未遷移的舊模組
  AIR009 AIR 套件或接點被修改（只在全專案檢查時，見 integrity.py）
軟規則（只提醒）：
  AIR101 函式語句過多    AIR102 函式過於複雜
"""

from __future__ import annotations

import ast
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

from . import integrity
from .config import Config, Layer
from .metrics import complexity, functions, statements
from .pyast import call_name, has_required_arg, import_targets, matches, qualified, scoped_calls

GENERIC_EXTERNAL = (
    "只能 import 白名單模組（AIR/config.toml 的 imports.common，加上 .air/project.toml 的 imports.extra）。"
    "第三方工具請包成 adapter（見 skill air-wrap-tool）；純計算函式庫要先問使用者是否加入白名單。"
)


def rel(path: Path, root: Path) -> str:
    """相對專案根目錄的路徑，一律用 / 分隔：給人看、給機器比對（審查結果、JSON）都一樣，Windows 上也不會變成反斜線。"""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    col: int
    code: str
    msg: str

    def format(self, root: Path) -> str:
        return f"{rel(self.path, root)}:{self.line}:{self.col}: {self.code} {self.msg}"

    def to_dict(self, root: Path) -> dict:
        return {"path": rel(self.path, root), "line": self.line, "col": self.col, "code": self.code, "msg": self.msg}


# 指定了檔案但沒檢查的原因
MISSING, NOT_PY, OUTSIDE = "找不到檔案", "不是 .py", "不在任何層內"
# verdict → CLI 結束碼
EXIT = {"pass": 0, "not_applicable": 0, "violation": 1, "input_error": 2}


@dataclass
class Report:
    hard: list[Violation] = field(default_factory=list)
    soft: list[Violation] = field(default_factory=list)
    checked: list[Path] = field(default_factory=list)  # 實際檢查過的檔案
    skipped: list[tuple[Path, str]] = field(default_factory=list)  # 指定了但沒檢查的檔案與原因
    legacy: list[Path] = field(default_factory=list)  # 全專案檢查時，層外的 .py（不檢查，只讓範圍看得見）

    @property
    def verdict(self) -> str:
        """pass｜violation｜not_applicable（沒有檢查任何檔案）｜input_error（指定的檔案不存在）。

        「沒有違規」和「有檢查過」是兩件事：空層的 pass 是 not_applicable，不能當成合規的證據。
        """
        if any(why == MISSING for _, why in self.skipped):
            return "input_error"
        if self.hard:
            return "violation"
        return "pass" if self.checked else "not_applicable"

    def summary(self, root: Path) -> str:
        n = len(self.checked)
        if self.verdict == "input_error":
            missing = "、".join(rel(p, root) for p, why in self.skipped if why == MISSING)
            return f"錯誤：找不到檔案 {missing}"
        if self.verdict == "violation":
            return f"AIR 檢查未通過：{len(self.hard)} 個違規（檢查了 {n} 個檔案）"
        if self.verdict == "not_applicable":
            return "AIR 檢查：沒有檢查任何檔案（各層還是空的，或指定的檔案都不在層內）。這不代表合規。"
        legacy = f"；舊程式碼 {len(self.legacy)} 個模組不檢查" if self.legacy else ""
        return f"AIR 檢查通過（{n} 個檔案{legacy}）"

    def to_dict(self, root: Path) -> dict:
        return {
            "schema": "air.check.v1",
            "verdict": self.verdict,
            "checked": [rel(p, root) for p in self.checked],
            "skipped": [{"path": rel(p, root), "reason": why} for p, why in self.skipped],
            "legacy": [rel(p, root) for p in self.legacy],
            "hard": [v.to_dict(root) for v in self.hard],
            "soft": [v.to_dict(root) for v in self.soft],
        }


def read_source(path: Path) -> str | Violation:
    """依 Python 的 encoding cookie 讀原始碼；讀不了（編碼錯誤）就回 AIR000，不丟例外。"""
    try:
        with tokenize.open(path) as f:
            return f.read()
    except (SyntaxError, UnicodeDecodeError, LookupError) as e:
        return Violation(path, 1, 1, "AIR000", f"無法解析：{e}")


# ── 檔案與模組 ──

def module_of(file: Path, cfg: Config) -> tuple[str, bool] | None:
    """檔案的完整模組名稱與是否為套件（__init__.py）；不在套件內回傳 None。"""
    try:
        rel = file.resolve().relative_to(cfg.package_dir.resolve())
    except ValueError:
        return None
    parts = [cfg.package, *rel.with_suffix("").parts]
    if parts[-1] == "__init__":
        return ".".join(parts[:-1]), True
    return ".".join(parts), False


def layer_of(file: Path, cfg: Config) -> Layer | None:
    resolved = file.resolve()
    return next((la for la in cfg.layers.values() if resolved.is_relative_to(la.path.resolve())), None)


def classify(target: str, cfg: Config) -> tuple[str, str]:
    """('external', 名稱) | ('layer', 相對於套件的名稱，例如 contracts.types.X) | ('legacy', 完整名稱)"""
    pkg = cfg.package
    if target != pkg and not target.startswith(pkg + "."):
        return "external", target
    rest = target[len(pkg) + 1:]
    if rest.split(".")[0] in cfg.layers:
        return "layer", rest
    return "legacy", target


# ── import ──

def _import_violation(node, target: str, layer: Layer, cfg: Config) -> tuple[str, str] | None:
    kind, name = classify(target, cfg)
    if kind == "external":
        if not layer.pure or matches(name, cfg.common_imports):
            return None
        hint = matches(name, [k for k in cfg.hints if not k.startswith("layer:")])
        return "AIR002", f"{layer.rule}：{cfg.hints[hint]}" if hint else f"{layer.rule}：{GENERIC_EXTERNAL}"
    if kind == "layer":
        if matches(name, layer.internal):
            return None
        dst = name.split(".")[0]
        hint_key = matches(name, [k[len("layer:"):] for k in cfg.hints if k.startswith("layer:")])
        hint = cfg.hints[f"layer:{hint_key}"] if hint_key else ""
        allowed = "、".join(layer.internal)
        return "AIR007", f"{layer.rule}：{layer.name} 不能 import {dst}（可以 import：{allowed}）。{hint}"
    if any(a.layer == layer.name and matches(name, [a.module]) for a in cfg.allow_legacy):
        return None
    return "AIR008", (
        f"{layer.rule}：`{name}` 是尚未遷移的舊模組。先把它搬進對應的層；遷移期間一定要用的話，"
        "經使用者同意後加進 .air/project.toml 的 migration.allow-legacy（要寫原因）。"
    )


def import_violations(tree: ast.AST, package: str, layer: Layer, cfg: Config):
    for node, target in import_targets(tree, package):
        found = _import_violation(node, target, layer, cfg)
        if found:
            code, msg = found
            yield node, code, f"import `{target}` is not allowed: {msg}"


# ── 呼叫（只檢查純的層）──

def call_violations(tree: ast.AST, cfg: Config):
    for node, aliases, bound in scoped_calls(tree):
        name, is_method = call_name(node)
        full = qualified(node.func, aliases)
        if full in cfg.banned_calls:
            yield node, "AIR006", f"`{full}()` is banned: {cfg.banned_calls[full]}"
        elif not is_method and name in cfg.banned_builtins and name not in bound:
            yield node, "AIR001", f"`{name}()` is banned: {cfg.banned_builtins[name]}"
        elif is_method and name in cfg.banned_methods and not (full and full.startswith(cfg.package + ".")):
            # 等價寫法 pd.DataFrame.to_csv(df, …) 也擋；只有解析到自己套件的模組函式才放行
            yield node, "AIR004", f"method `.{name}()` is banned: {cfg.banned_methods_msg}"
        key = full.rsplit(".", 1)[-1] if full else name  # 別名過的建構式（rng = default_rng）也要對應回規則
        rule = cfg.required_args.get(key)
        # 同名的區域函式（def sample）不套用 pandas 的規則
        if rule and (is_method or full or name not in bound) and not has_required_arg(node, rule["keyword"], rule["position"]):
            yield node, "AIR005", f"`{key}()` requires `{rule['keyword']}`: {rule['msg']}"


# ── 軟規則 ──

def soft_violations(tree: ast.AST, cfg: Config):
    for fn in functions(tree):
        n = statements(fn)
        if n > cfg.max_statements:
            yield fn, "AIR101", f"`{fn.name}` 有 {n} 個語句（> {cfg.max_statements}）。僅供參考；是否拆分依 skill air-split-function 判斷。"
        c = complexity(fn)
        if c > cfg.max_complexity:
            yield fn, "AIR102", f"`{fn.name}` 的複雜度是 {c}（> {cfg.max_complexity}）。僅供參考；是否拆分依 skill air-split-function 判斷。"


# ── 單一檔案 ──

def check_source(source: str, module: str, is_package: bool, layer: Layer, cfg: Config, path: Path) -> Report:
    report = Report()
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        report.hard.append(Violation(path, e.lineno or 1, e.offset or 1, "AIR000", f"無法解析：{e.msg}"))
        return report
    package = module if is_package else module.rpartition(".")[0]
    hard = list(import_violations(tree, package, layer, cfg))
    if layer.pure:
        hard += list(call_violations(tree, cfg))
    report.hard = [Violation(path, n.lineno, n.col_offset + 1, c, m) for n, c, m in hard]
    report.soft = [Violation(path, n.lineno, n.col_offset + 1, c, m) for n, c, m in soft_violations(tree, cfg)]
    report.hard.sort(key=lambda v: (v.line, v.col))
    return report


# ── 層內循環 ──

def find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """回傳圖中的循環（例如 [a, b, a]），同一個循環只回報一次。"""
    cycles, seen, done = [], set(), set()

    def visit(node: str, stack: list[str]):
        if node in stack:
            cycle = stack[stack.index(node):] + [node]
            if frozenset(cycle) not in seen:
                seen.add(frozenset(cycle))
                cycles.append(cycle)
            return
        if node in done:
            return
        for nxt in sorted(graph.get(node, ())):
            visit(nxt, stack + [node])
        done.add(node)

    for start in sorted(graph):
        visit(start, [])
    return cycles


def import_graph(sources: dict[str, tuple[str, bool]]) -> dict[str, set[str]]:
    """sources：模組名稱 → (原始碼, 是否為套件)。邊指向 sources 中最長的前綴模組。"""
    graph = {}
    for module, (source, is_package) in sources.items():
        package = module if is_package else module.rpartition(".")[0]
        try:
            tree = ast.parse(source)
        except SyntaxError:
            graph[module] = set()
            continue
        edges = {matches(t, sources) for _, t in import_targets(tree, package)}
        graph[module] = {e for e in edges if e and e != module}
    return graph


def layer_files(layer: Layer) -> list[Path]:
    return sorted(layer.path.rglob("*.py")) if layer.path.is_dir() else []


def cycle_violations(layer: Layer, cfg: Config, touched: set[Path] | None) -> list[Violation]:
    files = {}
    for f in layer_files(layer):
        mod = module_of(f, cfg)
        if mod:
            files[mod[0]] = (f, mod[1])
    sources = {}
    for m, (f, pkg) in files.items():
        source = read_source(f)
        sources[m] = ("" if isinstance(source, Violation) else source, pkg)  # 讀不了的檔案在單檔檢查已報 AIR000
    out = []
    for cycle in find_cycles(import_graph(sources)):
        cycle_files = {files[m][0].resolve() for m in cycle}
        if touched is not None and not cycle_files & touched:
            continue
        out.append(Violation(files[cycle[0]][0], 1, 1, "AIR003",
                             f"cycle `{' → '.join(cycle)}`: {layer.cycle_rule}：層內的模組不能循環 import。把共用的部分抽出來。"))
    return out


# ── 整體 ──

def check(cfg: Config, paths: list[Path] | None = None) -> Report:
    """paths 為 None 時檢查所有層；否則只檢查給定檔案中位於層內的 .py。沒檢查的檔案與原因記在 report.skipped。"""
    report, touched = Report(), {}
    if paths is None:
        targets = [f for la in cfg.layers.values() for f in layer_files(la)]
        if cfg.package_dir.is_dir():
            report.legacy = [f for f in sorted(cfg.package_dir.rglob("*.py")) if layer_of(f, cfg) is None]
        for path, detail in integrity.problems(cfg.root):  # 套件與接點被動過，最晚在這裡（結束前、commit 前）抓到
            report.hard.append(Violation(path, 1, 1, "AIR009", f"AIR 套件或接點被修改：{detail}。{integrity.FIX}"))
    else:
        targets = []
        for p in paths:
            if p.suffix != ".py":
                report.skipped.append((p, NOT_PY))
            elif not p.exists():
                report.skipped.append((p, MISSING))
            else:
                targets.append(p)
    for f in targets:
        layer = layer_of(f, cfg)
        mod = module_of(f, cfg) if layer else None
        if not mod:
            report.skipped.append((f, OUTSIDE))
            continue
        source = read_source(f)
        if isinstance(source, Violation):
            report.hard.append(source)
        else:
            r = check_source(source, mod[0], mod[1], layer, cfg, f)
            report.hard += r.hard
            report.soft += r.soft
        report.checked.append(f)
        touched.setdefault(layer.name, set()).add(f.resolve())
    for layer in cfg.layers.values():
        if layer.check_cycles and layer.name in touched:
            report.hard += cycle_violations(layer, cfg, None if paths is None else touched[layer.name])
    return report
