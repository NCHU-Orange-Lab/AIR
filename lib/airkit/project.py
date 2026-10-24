"""專案層級的操作：建立分層、盤點現有程式碼、狀態、遷移階段（受控的轉移與紀錄）。"""

from __future__ import annotations

import ast
import json
import sys
import os
import re
from datetime import datetime
from pathlib import Path

from . import batch, budget, golden, hooklog, install, legacy, review, stages, usage, verify
from . import history as _history
from .history import HISTORY_FILE
from .checks import Violation, call_violations, check, classify, layer_of, module_of, read_source, rel
from .config import KIT_DIR, PROJECT_FILE, REPORT_FILE, Config
from .pyast import import_targets, matches

STDLIB_IO = {"os", "io", "pathlib", "shutil", "sys", "subprocess", "socket", "logging", "random", "time",
             "urllib", "http", "sqlite3", "pickle", "csv", "tempfile", "glob", "argparse",
             "secrets", "uuid", "zipfile", "tarfile", "importlib", "signal", "ssl", "platform", "getpass", "select",
             "threading", "multiprocessing", "asyncio", "shelve", "dbm", "fileinput", "smtplib", "ftplib", "webbrowser"}

PHASES = ["installed", "analyzed", "planned", "layered", "migrating", "done"]
PAUSE_FILE = Path(".air") / "paused.json"      # 遷移暫停中：跟階段一樣屬於專案，進版本控制
PAUSE_SCHEMA = "air.paused.v1"


class TransitionError(ValueError):
    """階段轉移被拒絕（順序不對、前置條件沒滿足、缺少原因）。"""


class LayerCollision(ValueError):
    """建立分層會遮蔽專案原有的同名模組，或 --adopt-module 的條件不成立。"""


def _module_file(cfg: Config, name: str) -> Path:
    return cfg.package_dir / f"{name}.py"


def adoptable(cfg: Config, name: str) -> bool:
    """<層>.py 可以搬成 <層>/__init__.py：模組存在，而且層目錄還沒有、或只有空的 __init__.py。"""
    layer = cfg.layers[name].path
    if not _module_file(cfg, name).is_file():
        return False
    if not layer.exists():
        return True
    entries = [p for p in layer.iterdir() if p.name != "__pycache__"]
    return all(p.name == "__init__.py" and p.stat().st_size == 0 for p in entries)


def plan_layers(cfg: Config, names: list[str], adopt: list[str]) -> None:
    """建立之前先檢查，有問題就一個都不建：層目錄會遮蔽同名模組（試跑中 main/ 讓 cookiecutter.main 變成空的），--adopt-module 的條件。"""
    unknown = [a for a in adopt if a not in cfg.layers]
    if unknown:
        raise LayerCollision(f"--adopt-module 只能指定層的名稱（{'、'.join(cfg.layers)}），不是：{'、'.join(unknown)}")
    outside = [a for a in adopt if a not in names]
    if outside:
        raise LayerCollision(f"--adopt-module {'、'.join(outside)} 不在要建立的層裡。")
    for a in adopt:
        if not _module_file(cfg, a).is_file():
            raise LayerCollision(f"沒有同名模組 {_module_file(cfg, a).relative_to(cfg.root).as_posix()}，不需要 --adopt-module {a}。")
        if not adoptable(cfg, a):
            raise LayerCollision(f"{a}/ 已經有內容，不能自動採用 {a}.py：手動處理（把 {a}.py 的內容併進 {a}/__init__.py，或把 {a}.py 改名）。")
    blocked = [n for n in names if _module_file(cfg, n).is_file() and n not in adopt]
    if blocked:
        files = "、".join(_module_file(cfg, n).relative_to(cfg.root).as_posix() for n in blocked)
        flags = " ".join(f"--adopt-module {n}" for n in blocked)
        raise LayerCollision(
            f"建立 {'、'.join(f'{n}/' for n in blocked)} 會遮蔽專案原有的同名模組 {files}：Python 會載入目錄、忽略 .py，原本的程式 import 不到。沒有建立任何東西。\n"
            f"  這個模組就是這一層（例如 main.py 本來就是進入點）：python3 AIR/air.py layers {flags}，"
            "它把 .py 用 git mv 搬成 <層>/__init__.py（import 路徑不變），並列出它 import 的舊模組要加進 migration.allow-legacy 的項目。\n"
            "  不是的話，先把模組改名（並改掉 import 它的地方），再重跑 layers。")


def _adopt_module(cfg: Config, name: str) -> None:
    layer = cfg.layers[name].path
    mod, init = _module_file(cfg, name), layer / "__init__.py"
    layer.mkdir(parents=True, exist_ok=True)
    if init.exists():
        init.unlink()  # adoptable() 已確認它是空的
    if verify.git(cfg.root, "mv", "-f", rel(mod, cfg.root), rel(init, cfg.root)) is None:  # 不是 git repo、或檔案還沒追蹤
        mod.rename(init)


def _legacy_module(target: str, cfg: Config) -> str:
    """cookiecutter.config.get_user_config → cookiecutter.config（往回找到真的是模組檔案的那一段）。"""
    rest = target[len(cfg.package) + 1:].split(".")
    for i in range(len(rest), 0, -1):
        base = cfg.package_dir.joinpath(*rest[:i])
        if base.with_suffix(".py").is_file() or (base / "__init__.py").is_file():
            return f"{cfg.package}.{'.'.join(rest[:i])}"
    return target


def adopt_report(cfg: Config, name: str) -> list[str]:
    """採用 <層>.py 之後給使用者看的：它 import 的舊模組，要經同意加進 migration.allow-legacy（每筆要寫原因）。"""
    init = cfg.layers[name].path / "__init__.py"
    source = read_source(init)
    if isinstance(source, Violation):
        return [f"  注意：{rel(init, cfg.root)} 讀不出來，無法列出它 import 的舊模組。"]
    module, is_package = module_of(init, cfg)
    package = module if is_package else module.rpartition(".")[0]
    legacy = sorted({_legacy_module(t, cfg) for _, t in import_targets(ast.parse(source), package) if classify(t, cfg)[0] == "legacy"})
    if not legacy:
        return [f"  {name} 層沒有 import 任何尚未遷移的舊模組，不需要 allow-legacy。"]
    lines = [f"  {name} 層 import 了 {len(legacy)} 個尚未遷移的舊模組。經使用者同意後，加進 .air/project.toml 的 migration.allow-legacy（原因欄要改成真正的原因）："]
    lines += [f'    {{ from = "{name}", module = "{m}", reason = "{name}.py 原本就是這一層，搬成 {name}/__init__.py；<待填：為什麼暫時還要用它>" }},' for m in legacy]
    return lines


def create_layers(cfg: Config, names: list[str] | None = None, adopt: list[str] | None = None) -> list[str]:
    names, adopt = list(names or cfg.layers), list(adopt or [])
    plan_layers(cfg, names, adopt)
    for a in adopt:
        _adopt_module(cfg, a)
    created = []
    for name in names:
        layer = cfg.layers[name]
        layer.path.mkdir(parents=True, exist_ok=True)
        init = layer.path / "__init__.py"
        if not init.exists():
            init.write_text("", encoding="utf-8")
            created.append(init.relative_to(cfg.root).as_posix())
        if name == "contracts":
            for stub in ("types.py", "ports.py"):
                dest = layer.path / stub
                if not dest.exists():
                    dest.write_text((KIT_DIR / "templates" / f"contracts_{stub}").read_text(encoding="utf-8"), encoding="utf-8")
                    created.append(dest.relative_to(cfg.root).as_posix())
    return created


def set_phase(cfg: Config, phase: str) -> None:
    """直接改寫 .air/project.toml 的 phase，不檢查順序與前置條件；一般請用 transition()。
    寫入前比對檔案裡的階段跟 cfg 讀到的一樣（兩個 session 同時推進時不互相覆蓋），寫暫存檔再 os.replace（中斷不留半份）。"""
    if phase not in PHASES:
        raise ValueError(f"未知的階段：{phase}（可用：{'、'.join(PHASES)}）")
    path = cfg.root / PROJECT_FILE
    text = path.read_text(encoding="utf-8")
    m = re.search(r'^phase\s*=\s*"([^"]*)"', text, re.M)
    if m is None:
        raise ValueError(".air/project.toml 裡找不到 migration.phase。")
    if m.group(1) != cfg.phase:
        raise TransitionError(f".air/project.toml 的階段已經是 {m.group(1)}，跟這次讀到的 {cfg.phase} 不同（別的 session 改過？）。"
                              "重新執行 python3 AIR/air.py status 看目前的階段再決定。")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text[:m.start(1)] + phase + text[m.end(1):], encoding="utf-8")
    os.replace(tmp, path)


def preconditions(cfg: Config, phase: str) -> list[str]:
    """進入 phase 之前必須滿足的條件；回傳沒滿足的項目（空＝可以進入）。能機械判斷的才列在這裡。"""
    missing = []
    report_path = cfg.root / REPORT_FILE
    if phase in ("analyzed", "planned") and not report_path.exists():
        missing.append(f"找不到遷移報告 {REPORT_FILE}（從 AIR/templates/遷移報告.md 複製）")
    if phase == "analyzed" and not cfg.test_command:
        missing.append("測試指令尚未設定（.air/project.toml 的 [tests] command）")
    if phase == "layered":
        absent = [la.name for la in cfg.layers.values() if not la.path.is_dir()]
        if absent:
            missing.append(f"層目錄還沒建立：{'、'.join(absent)}（執行 python3 AIR/air.py layers）")
        report = check(cfg)
        if report.hard:
            missing.append(f"架構檢查有 {len(report.hard)} 個違規（執行 python3 AIR/air.py check）")
        receipt = verify.load_receipt(cfg.root)  # 建立分層可能弄壞專案（層目錄遮蔽同名模組），check 看不到，要跑測試才知道
        if receipt is None or not receipt["accepted"] or verify.receipt_state(cfg, receipt) != "fresh":
            missing.append("建立分層之後還沒有通過的驗收：執行 python3 AIR/air.py verify（紀錄要與目前的程式碼、設定一致）")
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
    if (p := paused(cfg.root)) is not None:
        raise TransitionError(f"遷移暫停中（{describe_pause(p)}）：先執行 python3 AIR/air.py resume，再推進階段。")
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


# ── 暫停與繼續 ──
# 實戰中遷移停在分析階段好幾週，整個 repo 的 .py 都被凍結，使用者只能把 AIR 拆掉。暫停是正式的出口：
# 保留階段，解除分析／計畫階段的凍結，session 開始時改附「暫停中」而不是該階段的做法；架構檢查照常。

def paused(root: Path) -> dict | None:
    """暫停中的紀錄（time、phase、reason）；沒有暫停是 None。"""
    return verify.load_json(root / PAUSE_FILE, PAUSE_SCHEMA)


def describe_pause(entry: dict) -> str:
    return f"{entry['time'][:16].replace('T', ' ')} 起，原因：{entry['reason']}"


def pause(cfg: Config, reason: str) -> dict:
    """暫停遷移：只由使用者要求時執行。要寫原因（之後每個 session 開始都會看到），並記進 history。"""
    if not reason.strip():
        raise TransitionError("pause 必須加 --reason 說明原因（之後每個 session 開始都會附上，並記錄在 .air/history.jsonl）。")
    if cfg.phase == "done":
        raise TransitionError("遷移已經完成（done），沒有要暫停的。")
    if (p := paused(cfg.root)) is not None:
        raise TransitionError(f"遷移已經在暫停中（{describe_pause(p)}）。要繼續請執行 python3 AIR/air.py resume。")
    entry = {"schema": PAUSE_SCHEMA, "time": verify.now(), "phase": cfg.phase, "reason": reason.strip()}
    verify.write_json(cfg.root / PAUSE_FILE, entry)
    record(cfg.root, {"time": entry["time"], "from": cfg.phase, "to": cfg.phase, "head": verify.git(cfg.root, "rev-parse", "HEAD"),
                      "how": "pause", "reason": entry["reason"]})
    return entry


def resume(cfg: Config) -> dict:
    """繼續遷移：移除暫停紀錄，階段回到暫停前的那一格，並記進 history。回傳原本的暫停紀錄。"""
    p = paused(cfg.root)
    if p is None:
        raise TransitionError("遷移沒有在暫停中。")
    (cfg.root / PAUSE_FILE).unlink()
    record(cfg.root, {"time": verify.now(), "from": cfg.phase, "to": cfg.phase, "head": verify.git(cfg.root, "rev-parse", "HEAD"),
                      "how": "resume", "reason": None})
    return p


def record(root: Path, entry: dict) -> None:
    path = root / HISTORY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


history = _history.load  # 讀取在 history.py（verify 也要用，放這裡會循環）


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
        "third_party": sorted(m for m in external if not matches(m, cfg.common_imports) and m not in STDLIB_IO and m not in sys.stdlib_module_names),
        "stdlib_io": sorted(m for m in external if m in STDLIB_IO),
        "internal": sorted(internal),
        "signals": signals,
    }


def survey_rows(cfg: Config) -> list[dict]:
    files = sorted(f for f in cfg.package_dir.rglob("*.py") if not cfg.is_excluded(f) and not cfg.is_test_file(f)) if cfg.package_dir.is_dir() else []
    return [_module_facts(f, cfg) for f in files]


def tests_in_package(cfg: Config) -> int:
    """套件目錄裡屬於 tests-dir 的 .py 檔數（survey 不盤點它們，只說明有幾個）。"""
    if not cfg.package_dir.is_dir():
        return 0
    return sum(1 for f in cfg.package_dir.rglob("*.py") if cfg.is_test_file(f) and not cfg.is_excluded(f))


def _tests_note(cfg: Config) -> str | None:
    n = tests_in_package(cfg)
    return f"測試 {n} 個模組在 tests-dir（{cfg.tests_dir.relative_to(cfg.root).as_posix()}）：不盤點，也不算舊模組" if n else None


def survey_summary(cfg: Config, rows: list[dict], out: Path | None) -> str:
    """給畫面看的摘要：完整的表寫在檔案裡，不要一次印進對話（實戰中每個模組約 166 字，幾百個模組就是幾萬 token）。"""
    ok = [r for r in rows if "error" not in r]
    third: dict[str, int] = {}
    for r in ok:
        for m in r["third_party"]:
            third[m] = third.get(m, 0) + 1
    top_third = sorted(third.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    noisy = sorted((r for r in ok if r["signals"]), key=lambda r: (-len(r["signals"]), r["module"]))[:10]
    lines = [
        f"AIR survey：{cfg.package}",
        f"- 模組數：{len(rows)}（已在層內：{sum(1 for r in rows if r['layer'])}；無法解析：{len(rows) - len(ok)}）",
        "- 第三方套件（用到的模組數）：" + ("、".join(f"{m}（{n}）" for m, n in top_third) or "無"),
        "- 訊號最多的模組（需要拆開的候選）：" + ("、".join(f"`{r['module']}`（{len(r['signals'])} 處）" for r in noisy) or "無"),
    ]
    if note := _tests_note(cfg):
        lines.insert(2, f"- {note}")
    if out is not None:
        lines.append(f"- 完整的盤點表已寫入 {rel(out, cfg.root)}（{len(rows)} 列）；分析時只查需要的列，不要整份讀進對話")
    return "\n".join(lines) + "\n"


def survey(cfg: Config, rows: list[dict] | None = None) -> str:
    """盤點套件內所有模組，輸出 Markdown，作為分析報告的素材。"""
    rows = survey_rows(cfg) if rows is None else rows
    out = [
        f"# AIR survey：{cfg.package}",
        "",
        f"- 套件目錄：`{cfg.package_dir.relative_to(cfg.root).as_posix()}`",
        f"- 模組數：{len(rows)}（已在層內：{sum(1 for r in rows if r['layer'])}）",
        *([f"- {note}"] if (note := _tests_note(cfg)) else []),
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


def local_time() -> str:
    """本機時間，給 status 與 session 開始時的狀態用：AI 寫日期的依據（實戰中 AI 手寫的日期曾比時鐘超前 1–2 天）。"""
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %z")


def status_data(cfg: Config) -> dict:
    """狀態的結構化版本（status --json）；由目前的設定、檢查結果與驗收紀錄重新算，不信任任何摘要。"""
    report = check(cfg)
    receipt = verify.load_receipt(cfg.root)
    reviewed = review.load_record(cfg.root)
    index = PHASES.index(cfg.phase) if cfg.phase in PHASES else -1
    pause_entry = paused(cfg.root)
    stage = stages.for_phase(cfg.phase) if pause_entry is None else None
    return {
        "schema": "air.status.v1",
        "time": local_time(),
        "package": cfg.package,
        "package_dir": cfg.package_dir.relative_to(cfg.root).as_posix(),
        "phase": cfg.phase,
        "next_phase": PHASES[index + 1] if 0 <= index < len(PHASES) - 1 else None,
        "guide": stages.shown(stage) if stage else None,  # 現在該讀的那一份階段檔；暫停中與 done 沒有
        "paused": None if pause_entry is None else {k: pause_entry[k] for k in ("time", "phase", "reason")},
        "exclude": list(cfg.exclude),
        "paused_text": None if pause_entry is None else describe_pause(pause_entry),
        "branch": verify.branch_name(cfg.root),
        "records_dir": rel(verify.records_dir(cfg.root), cfg.root),
        "stop_scope": cfg.stop_scope,
        "layers": [la.name for la in cfg.layers.values() if la.path.is_dir()],
        "checked_files": len(report.checked),
        "legacy_modules": len(report.legacy),
        "legacy_kinds": legacy.counts(report.legacy),  # logic（有自己的程式）／shim（轉接檔）／marker（套件標記）
        "allow_legacy": len(cfg.allow_legacy),
        "allow_legacy_stale": 0 if cfg.legacy_alerting == "none" or not cfg.allow_legacy else len(legacy.stale_allowances(cfg)),
        "violations": len(report.hard),
        "warnings": len(report.soft),
        "integrity": [v.format(cfg.root) for v in report.hard if v.code == "AIR009"],
        "commit_check": install.commit_check_status(cfg.root),
        "hook_errors": len(hooklog.recent(cfg.root)),
        "hook_errors_text": hooklog.describe(cfg.root),
        "usage": usage.summary(cfg.root),
        "usage_text": usage.describe(cfg.root),
        "context_budget": budget.status_line(cfg.root),  # 只有到 80% 以上才有值
        "last_batch": (batch.batches(cfg.root) or [None])[-1],
        "test_command": cfg.test_command,
        "test_results": cfg.test_results,
        "final_command": cfg.final.command if cfg.final else None,
        "baseline": verify.baseline_summary(cfg),
        "golden": golden.summary(cfg),
        "golden_text": golden.describe(cfg),
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
        f"本機時間：{d['time']}",
        f"套件：{d['package']}（{d['package_dir']}）",
        f"遷移階段：{d['phase']}（{' → '.join(PHASES)}）",
        *([f"現在讀：{d['guide']}（只讀這一份，其他階段不用讀）"] if d["guide"] else []),
        *([f"遷移暫停中：{d['paused_text']}（python3 AIR/air.py resume 繼續；暫停期間照一般開發，架構檢查照常）"] if d["paused"] else []),
        f"分支：{d['branch']}（本機紀錄在 {d['records_dir']}/）",
        f"已建立的層：{'、'.join(d['layers']) or '無'}",
        f"檢查範圍：層內 {d['checked_files']} 個檔案；舊程式碼模組 {d['legacy_modules']} 個（不檢查；{legacy.describe(d['legacy_kinds'])}）",
        *([f"不歸 AIR 管的路徑（migration.exclude）：{'、'.join(d['exclude'])}"] if d["exclude"] else []),
        f"暫時允許的舊模組 import：{d['allow_legacy']}" + (f"（其中 {d['allow_legacy_stale']} 筆已經不需要）" if d["allow_legacy_stale"] else ""),
        f"違規：{d['violations']}　提醒：{d['warnings']}",
        f"套件完整性：{integrity_line}",
        f"commit 前檢查：{d['commit_check']}",
        f"hook 錯誤（24 小時內）：{d['hook_errors_text']}",
        f"成本指標（本機累計）：{d['usage_text']}",
        *([f"常駐內容：{d['context_budget']}"] if d["context_budget"] else []),
        f"測試指令：{d['test_command'] or '（尚未設定）'}" + (f"；收尾：{d['final_command']}" if d["final_command"] else ""),
        *([f"既有失敗基準：{'、'.join(f'{k} {v} 個' for k, v in d['baseline'].items()) or '還沒記錄（python3 AIR/air.py verify --record-baseline）'}"] if d["test_results"] else []),
        *([f"黃金重播：{d['golden_text']}"] if d["golden_text"] else []),
        f"最近驗收：{d['last_verify_text']}",
        f"語意審查：{d['last_review_text']}",
        *([f"最近一批：{batch.describe(d['last_batch'])}"] if d["last_batch"] else []),
    ]
    return "\n".join(lines)

