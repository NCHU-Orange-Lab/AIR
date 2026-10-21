"""專案層級的操作：建立分層、盤點現有程式碼、狀態、遷移階段。"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from .checks import Violation, call_violations, check, classify, layer_of, module_of, read_source
from .config import KIT_DIR, PROJECT_FILE, Config
from .pyast import import_targets, matches

STDLIB_IO = {"os", "io", "pathlib", "shutil", "sys", "subprocess", "socket", "logging", "random", "time",
             "urllib", "http", "sqlite3", "pickle", "csv", "tempfile", "glob", "argparse"}

PHASES = ["installed", "analyzed", "planned", "layered", "migrating", "done"]


def create_layers(cfg: Config, names: list[str] | None = None) -> list[str]:
    created = []
    for name in names or list(cfg.layers):
        layer = cfg.layers[name]
        layer.path.mkdir(parents=True, exist_ok=True)
        init = layer.path / "__init__.py"
        if not init.exists():
            init.write_text("", encoding="utf-8")
            created.append(str(init.relative_to(cfg.root)))
        if name == "contracts":
            for stub in ("types.py", "ports.py"):
                dest = layer.path / stub
                if not dest.exists():
                    dest.write_text((KIT_DIR / "templates" / f"contracts_{stub}").read_text(encoding="utf-8"), encoding="utf-8")
                    created.append(str(dest.relative_to(cfg.root)))
    return created


def set_phase(cfg: Config, phase: str) -> None:
    if phase not in PHASES:
        raise ValueError(f"未知的階段：{phase}（可用：{'、'.join(PHASES)}）")
    path = cfg.root / PROJECT_FILE
    text = path.read_text(encoding="utf-8")
    new, n = re.subn(r'^(phase\s*=\s*)"[^"]*"', rf'\g<1>"{phase}"', text, count=1, flags=re.M)
    if n == 0:
        raise ValueError(".air/project.toml 裡找不到 migration.phase。")
    path.write_text(new, encoding="utf-8")


def _module_facts(file: Path, cfg: Config) -> dict:
    module, is_package = module_of(file, cfg)
    source = read_source(file)
    facts = {"module": module, "layer": (layer_of(file, cfg) or None), "lines": file.read_bytes().count(b"\n") + 1}
    if isinstance(source, Violation):
        return {**facts, "error": "無法解析"}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {**facts, "error": "無法解析"}
    package = module if is_package else module.rpartition(".")[0]
    external, internal = set(), set()
    for _, target in import_targets(tree, package):
        kind, name = classify(target, cfg)
        (external if kind == "external" else internal).add(name.split(".")[0] if kind == "external" else name)
    # 把整個專案當成「純的層」檢查一次，找出 I/O、時間、亂數等訊號
    signals = sorted({f"{n.lineno}:{c}" for n, c, _ in call_violations(tree, cfg)})
    funcs = sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in ast.walk(tree))
    return {
        **facts,
        "functions": funcs,
        "external": sorted(external),
        "third_party": sorted(m for m in external if not matches(m, cfg.common_imports) and m not in STDLIB_IO),
        "stdlib_io": sorted(m for m in external if m in STDLIB_IO),
        "internal": sorted(internal),
        "signals": signals,
    }


def survey(cfg: Config) -> str:
    """盤點套件內所有模組，輸出 Markdown，作為分析報告的素材。"""
    files = sorted(cfg.package_dir.rglob("*.py")) if cfg.package_dir.is_dir() else []
    rows = [_module_facts(f, cfg) for f in files]
    out = [
        f"# AIR survey：{cfg.package}",
        "",
        f"- 套件目錄：`{cfg.package_dir.relative_to(cfg.root)}`",
        f"- 模組數：{len(rows)}（已在層內：{sum(1 for r in rows if r['layer'])}）",
        "",
        "訊號代碼：AIR001 內建 I/O（open、print…）、AIR004 讀寫方法、AIR005 缺少 seed、AIR006 禁用函式（讀檔、時間、亂數）",
        "",
        "| 模組 | 目前位置 | 行數 | 函式 | 第三方 | 標準庫 I/O | 內部依賴 | 訊號（行:代碼） |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        where = r["layer"].name if r["layer"] else "舊程式碼"
        if "error" in r:
            out.append(f"| `{r['module']}` | {where} | {r['lines']} | — | — | — | — | {r['error']} |")
            continue
        out.append("| `{}` | {} | {} | {} | {} | {} | {} | {} |".format(
            r["module"], where, r["lines"], r["functions"],
            "、".join(r["third_party"]) or "—", "、".join(r["stdlib_io"]) or "—",
            "、".join(f"`{m}`" for m in r["internal"]) or "—", "、".join(r["signals"]) or "—",
        ))
    return "\n".join(out) + "\n"


def status(cfg: Config) -> str:
    layers = [la for la in cfg.layers.values() if la.path.is_dir()]
    files = sorted(cfg.package_dir.rglob("*.py")) if cfg.package_dir.is_dir() else []
    legacy = [f for f in files if layer_of(f, cfg) is None]
    report = check(cfg)
    lines = [
        f"套件：{cfg.package}（{cfg.package_dir.relative_to(cfg.root)}）",
        f"遷移階段：{cfg.phase}（{' → '.join(PHASES)}）",
        f"已建立的層：{'、'.join(la.name for la in layers) or '無'}",
        f"舊程式碼模組：{len(legacy)}",
        f"暫時允許的舊模組 import：{len(cfg.allow_legacy)}",
        f"違規：{len(report.hard)}　提醒：{len(report.soft)}",
        f"測試指令：{cfg.test_command or '（尚未設定）'}",
    ]
    return "\n".join(lines)

