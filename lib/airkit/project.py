"""專案層級的操作：建立分層、盤點現有程式碼、狀態、遷移階段（受控的轉移與紀錄）。"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from . import install, review, verify
from .checks import Violation, call_violations, check, classify, layer_of, module_of, read_source
from .config import KIT_DIR, PROJECT_FILE, REPORT_FILE, Config
from .pyast import import_targets, matches

STDLIB_IO = {"os", "io", "pathlib", "shutil", "sys", "subprocess", "socket", "logging", "random", "time",
             "urllib", "http", "sqlite3", "pickle", "csv", "tempfile", "glob", "argparse"}

PHASES = ["installed", "analyzed", "planned", "layered", "migrating", "done"]
HISTORY_FILE = Path(".air") / "history.jsonl"  # 每次階段轉移一行：時間、從哪到哪、commit、方式、原因


class TransitionError(ValueError):
    """階段轉移被拒絕（順序不對、前置條件沒滿足、缺少原因）。"""


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
    """直接改寫 .air/project.toml 的 phase，不檢查順序與前置條件；一般請用 transition()。"""
    if phase not in PHASES:
        raise ValueError(f"未知的階段：{phase}（可用：{'、'.join(PHASES)}）")
    path = cfg.root / PROJECT_FILE
    text = path.read_text(encoding="utf-8")
    new, n = re.subn(r'^(phase\s*=\s*)"[^"]*"', rf'\g<1>"{phase}"', text, count=1, flags=re.M)
    if n == 0:
        raise ValueError(".air/project.toml 裡找不到 migration.phase。")
    path.write_text(new, encoding="utf-8")


def preconditions(cfg: Config, phase: str) -> list[str]:
    """進入 phase 之前必須滿足的條件；回傳沒滿足的項目（空＝可以進入）。能機械判斷的才列在這裡。"""
    missing = []
    report_path = cfg.root / REPORT_FILE
    if phase in ("analyzed", "planned") and not report_path.exists():
        missing.append(f"找不到遷移報告 {REPORT_FILE}（從 AIR/templates/遷移報告.md 複製）")
    if phase == "analyzed" and not cfg.test_command:
        missing.append("test-command 尚未設定（.air/project.toml 的 project.test-command）")
    if phase == "layered":
        absent = [la.name for la in cfg.layers.values() if not la.path.is_dir()]
        if absent:
            missing.append(f"層目錄還沒建立：{'、'.join(absent)}（執行 python3 AIR/air.py layers）")
        report = check(cfg)
        if report.hard:
            missing.append(f"架構檢查有 {len(report.hard)} 個違規（執行 python3 AIR/air.py check）")
    if phase == "done":
        receipt = verify.load_receipt(cfg.root)
        if receipt is None:
            missing.append("沒有驗收紀錄：先執行 python3 AIR/air.py verify --scope final")
        elif receipt["scope"] != "final":
            missing.append("最近一次驗收不是 final：執行 python3 AIR/air.py verify --scope final")
        elif not receipt["accepted"]:
            missing.append("最近一次驗收未通過：" + "；".join(receipt["reasons"]))
        elif verify.receipt_state(cfg, receipt) == "stale":
            missing.append("驗收紀錄已過期（驗收之後程式碼或設定有變更）：重新執行 python3 AIR/air.py verify --scope final")
    return missing


def transition(cfg: Config, phase: str, *, rollback: bool = False, force: bool = False, reason: str = "") -> list[str]:
    """受控的階段轉移：一次只能前進一格，前置條件沒滿足就拒絕；退回或強制都要寫原因，並記進 history。回傳給使用者看的說明。"""
    if phase not in PHASES:
        raise TransitionError(f"未知的階段：{phase}（可用：{'、'.join(PHASES)}）")
    if cfg.phase not in PHASES:
        raise TransitionError(f".air/project.toml 的 migration.phase 是未知的值：{cfg.phase}")
    if (rollback or force) and not reason.strip():
        raise TransitionError("--rollback / --force 必須加 --reason 說明原因（會記錄在 .air/history.jsonl）。")
    cur, new = PHASES.index(cfg.phase), PHASES.index(phase)
    if rollback:
        if new >= cur:
            raise TransitionError(f"--rollback 只能退回較早的階段（目前：{cfg.phase}）。")
    elif new == cur:
        raise TransitionError(f"已經在 {phase} 階段。")
    elif new != cur + 1:
        hint = f"{cfg.phase} 之後是 {PHASES[cur + 1]}" if cur + 1 < len(PHASES) else f"{cfg.phase} 已經是最後一個階段"
        raise TransitionError(f"階段只能一次前進一格：{hint}。退回請用 --rollback --reason。")
    lines, missing = [], []
    if not rollback:
        missing = preconditions(cfg, phase)
        if missing and not force:
            raise TransitionError("還不能進入 " + phase + "：\n" + "\n".join(f"  - {m}" for m in missing)
                                  + "\n（特殊情況可加 --force --reason，會記錄在 .air/history.jsonl；請先問使用者）")
        lines += [f"強制跳過：{m}" for m in missing]
    set_phase(cfg, phase)
    record(cfg.root, {"time": verify.now(), "from": cfg.phase, "to": phase, "head": verify.git(cfg.root, "rev-parse", "HEAD"),
                      "how": "rollback" if rollback else ("forced" if missing else "normal"), "reason": reason.strip() or None})
    return lines


def record(root: Path, entry: dict) -> None:
    path = root / HISTORY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def history(root: Path) -> list[dict]:
    path = root / HISTORY_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


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


def status_data(cfg: Config) -> dict:
    """狀態的結構化版本（status --json）；由目前的設定、檢查結果與驗收紀錄重新算，不信任任何摘要。"""
    report = check(cfg)
    receipt = verify.load_receipt(cfg.root)
    reviewed = review.load_record(cfg.root)
    index = PHASES.index(cfg.phase) if cfg.phase in PHASES else -1
    return {
        "schema": "air.status.v1",
        "package": cfg.package,
        "package_dir": str(cfg.package_dir.relative_to(cfg.root)),
        "phase": cfg.phase,
        "next_phase": PHASES[index + 1] if 0 <= index < len(PHASES) - 1 else None,
        "layers": [la.name for la in cfg.layers.values() if la.path.is_dir()],
        "checked_files": len(report.checked),
        "legacy_modules": len(report.legacy),
        "allow_legacy": len(cfg.allow_legacy),
        "violations": len(report.hard),
        "warnings": len(report.soft),
        "integrity": [v.format(cfg.root) for v in report.hard if v.code == "AIR009"],
        "commit_check": install.commit_check_status(cfg.root),
        "test_command": cfg.test_command,
        "last_verify": None if receipt is None else {
            "state": verify.receipt_state(cfg, receipt), "accepted": receipt["accepted"],
            "scope": receipt["scope"], "time": receipt["time"],
        },
        "last_verify_text": verify.describe(cfg, receipt),
        "last_review": None if reviewed is None else {
            "state": review.state(cfg, reviewed), "blocking": reviewed["blocking"], "findings": len(reviewed["findings"]),
            "time": reviewed["time"],
        },
        "last_review_text": review.describe(cfg, reviewed),
    }


def status(cfg: Config) -> str:
    d = status_data(cfg)
    problems = d["integrity"]
    integrity_line = "正常" if not problems else f"{len(problems)} 個問題（AIR009，見 python3 AIR/air.py check）"
    lines = [
        f"套件：{d['package']}（{d['package_dir']}）",
        f"遷移階段：{d['phase']}（{' → '.join(PHASES)}）",
        f"已建立的層：{'、'.join(d['layers']) or '無'}",
        f"檢查範圍：層內 {d['checked_files']} 個檔案；舊程式碼模組 {d['legacy_modules']} 個（不檢查）",
        f"暫時允許的舊模組 import：{d['allow_legacy']}",
        f"違規：{d['violations']}　提醒：{d['warnings']}",
        f"套件完整性：{integrity_line}",
        f"commit 前檢查：{d['commit_check']}",
        f"測試指令：{d['test_command'] or '（尚未設定）'}",
        f"最近驗收：{d['last_verify_text']}",
        f"語意審查：{d['last_review_text']}",
    ]
    return "\n".join(lines)

