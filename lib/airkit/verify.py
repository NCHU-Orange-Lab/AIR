"""正式驗收：架構檢查＋專案測試＋黃金重播（收尾時再加暫時允許與層外模組的檢查），結果綁定這一版程式碼，寫成可追溯的紀錄。

`check` 是快速診斷；`verify` 回答「這一份程式碼有沒有通過全部必要的檢查」。
測試有設 [tests].results（JUnit XML）時，用既有失敗基準判斷：失敗減基準為空就通過，並列出新增的失敗與基準裡已修好的
（研究型專案本來就有環境性失敗，只認 exit 0 永遠拿不到驗收）；沒設就只看結束碼。收尾可以另設 [tests.final] 跑完整套件。
紀錄存在 .air/branches/<分支>/verify.json（本機，不進版本控制；依分支分開，切換分支不會互相覆蓋或誤判），
帶著程式碼、設定與 AIR 版本的指紋：任一項改變，紀錄就過期（stale），階段轉移時不能拿來用。

這是工作紀錄，不是防偽證明：有寫入權限的人（或 AI）可以改它。要可信的關卡，請在 CI 重新執行 verify。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from . import buildcheck, golden, junit, legacy, runner, usage
from .checks import Report, check, rel
from .config import KIT_DIR, PROJECT_FILE, Config, Suite

BRANCHES_DIR = Path(".air") / "branches"  # 本機紀錄依分支分開：verify.json、review.json、last-check.json
SCHEMA = "air.verify.v1"
BASELINE_SCHEMA = "air.test-baseline.v1"
SCOPES = ("batch", "final")
SKIP_DIRS = {"venv", ".venv", "node_modules", "build", "dist", "__pycache__"}
MARK = {"pass": "通過", "fail": "未通過", "skipped": "略過", "error": "錯誤"}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def git(root: Path, *args: str) -> str | None:
    """git 指令的 stdout；不是 git repo、沒有 commit 或沒有 git 時回傳 None。"""
    try:
        out = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return out.stdout.strip()


# ── 分支與紀錄位置 ──

def branch_name(root: Path) -> str:
    """目前的分支名稱；detached 是 detached-<sha12>，不是 git repo 是 no-git。"""
    if git(root, "rev-parse", "--show-toplevel") is None:
        return "no-git"
    name = git(root, "symbolic-ref", "--short", "-q", "HEAD")  # 還沒有 commit 的新 repo 也答得出來
    if name:
        return name
    head = git(root, "rev-parse", "HEAD")
    return f"detached-{head[:12]}" if head else "no-git"


def branch_slug(name: str) -> str:
    return re.sub(r"[^\w.-]", "_", name)[:80] or "_"


def records_dir(root: Path) -> Path:
    """這個分支的本機紀錄目錄（實戰中 A 的驗收與審查紀錄會被 B 蓋掉；切分支之後紀錄也對不上）。"""
    return root / BRANCHES_DIR / branch_slug(branch_name(root))


# ── 指紋 ──

def _under(path: Path, bases: list[Path]) -> bool:
    return any(path == b or b in path.parents for b in bases)


def source_files(cfg: Config) -> list[Path]:
    """影響驗收結果的檔案：套件目錄與測試目錄的 .py、verify.fingerprint-extra 列的路徑（整個檔案或目錄）、AIR 自己的程式與設定。
    migration.exclude 的路徑不算。只看這些：實戰中另一個 session 在 scripts/ 加一個檔案，驗收紀錄就過期，final 驗收幾乎拿不到。"""
    root = cfg.root
    scopes = [cfg.package_dir.resolve(), cfg.tests_dir.resolve()]
    extra = [(root / e).resolve() for e in cfg.fingerprint_extra]
    wanted = [p for p in scopes + extra if p.exists()]
    listed = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *[rel(p, root) for p in wanted]) if wanted else ""
    if listed is None:
        files = []
        for base in wanted:
            if base.is_file():
                files.append(base)
            else:
                files += [p for p in base.rglob("*") if p.is_file() and not any(part.startswith(".") or part in SKIP_DIRS for part in p.relative_to(base).parts)]
    else:
        files = [(root / name).resolve() for name in listed.split("\0") if name]
    files = [f for f in files if (f.suffix == ".py" or _under(f, extra)) and not cfg.is_excluded(f)]
    files += [p for p in KIT_DIR.rglob("*.py") if "tests" not in p.relative_to(KIT_DIR).parts and "__pycache__" not in p.parts]  # AIR 自己的 checker
    files += [KIT_DIR / "config.toml", KIT_DIR / "VERSION"]
    return sorted({f for f in files if f.is_file()})


def digest(paths: list[Path], root: Path) -> str:
    h = hashlib.sha256()
    for p in paths:
        h.update(rel(p, root).encode("utf-8", "surrogateescape"))
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def fingerprint(cfg: Config) -> dict:
    """目前程式碼與設定的指紋。source／config／air 任一項改變，驗收紀錄就過期；head 與 dirty 只是給人看的。"""
    root = cfg.root
    status = git(root, "status", "--porcelain")
    return {
        "head": git(root, "rev-parse", "HEAD"),
        "dirty": None if status is None else bool(status),
        "source": digest(source_files(cfg), root),
        "config": digest([root / PROJECT_FILE], root),
        "air": (KIT_DIR / "VERSION").read_text(encoding="utf-8").strip(),
    }


def same_fingerprint(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in ("source", "config", "air"))


# ── 既有失敗基準 ──

def load_baseline(cfg: Config) -> dict | None:
    return load_json(cfg.root / cfg.test_baseline, BASELINE_SCHEMA)


def baseline_ids(cfg: Config, suite: str) -> set[str]:
    """這個套件的既有失敗清單（沒有基準就是空集合：任何失敗都算新的）。"""
    data = load_baseline(cfg) or {}
    return set(data.get("suites", {}).get(suite, {}).get("failed", []))


def baseline_summary(cfg: Config) -> dict[str, int]:
    data = load_baseline(cfg) or {}
    return {name: len(s.get("failed", [])) for name, s in data.get("suites", {}).items()}


def _run_suite(cfg: Config, suite: Suite) -> tuple[dict, dict[str, str] | None]:
    """跑一個套件：回傳 (runner 的結果加 detail, 測試結果；沒設 results 或跑不起來是 None)。results 有設時，先刪掉舊檔，才分得出這次有沒有產生。"""
    hint = f"可在 .air/project.toml 的 [tests{'.final' if suite.name == 'final' else ''}] 設定 timeout"
    results_path = cfg.root / suite.results if suite.results else None
    if results_path is not None and results_path.exists():
        results_path.unlink()
    r = runner.run_command(suite.command, cfg.root, suite.timeout, hint)
    if r["status"] == "error" or results_path is None:
        return r, None
    if not results_path.exists():
        return {**r, "status": "error", "detail": f"測試指令沒有產生 {suite.results}（[tests] results 要指向指令寫出的 JUnit XML，例如 pytest --junit-xml={suite.results}）"}, None
    try:
        return r, junit.parse(results_path)
    except ValueError as e:
        return {**r, "status": "error", "detail": f"{suite.results} 讀不出測試結果：{e}"}, None


def run_tests(cfg: Config, scope: str = "batch") -> dict:
    """執行這個 scope 的測試套件。沒設定＝skipped；逾時或啟動失敗＝error。有 results 時依既有失敗基準判斷，沒有就只看結束碼。"""
    suite = cfg.suite(scope)
    result = {"name": "project-tests", "required": True, "suite": suite.name, "command": suite.command}
    if not suite.command:
        return {**result, "status": "skipped", "detail": "測試指令尚未設定（.air/project.toml 的 [tests] command）"}
    r, cases = _run_suite(cfg, suite)
    if r["status"] == "error":
        return {**result, **r}
    if cases is None:  # 只看結束碼
        return {**result, **r, "status": "pass" if r["exit"] == 0 else "fail"}
    failed = {t for t, st in cases.items() if st == junit.FAILED}
    if r["exit"] not in (0, 1) or (r["exit"] != 0 and not failed):
        return {**result, **r, "status": "error", "detail": f"{r['detail']}：不是 0（通過）或 1（有失敗），可能是用法錯誤、中斷或沒有收集到測試"}
    baseline = baseline_ids(cfg, suite.name)
    new = sorted(failed - baseline)
    fixed = sorted(t for t in baseline if cases.get(t) == junit.PASSED)
    detail = (f"{r['detail']}；{len(cases)} 個測試，失敗 {len(failed)}（基準內 {len(failed & baseline)}，新增 {len(new)}）"
              + (f"；基準裡已修好 {len(fixed)}，可以重跑 verify --record-baseline 縮小基準" if fixed else ""))
    return {**result, **r, "status": "pass" if not new else "fail", "detail": detail,
            "failed": sorted(failed), "new_failures": new, "fixed": fixed, "baseline": len(baseline)}


def record_baseline(cfg: Config, scope: str = "batch") -> dict:
    """跑這個 scope 的套件，把目前失敗的測試記成基準（.air/test-baseline.json，進版本控制；保留別的套件的基準）。
    分析階段做一次：之後只要沒有新的失敗就算通過。需要 [tests] results。"""
    suite = cfg.suite(scope)
    if not suite.command:
        raise ValueError("測試指令尚未設定（.air/project.toml 的 [tests] command）。")
    if not suite.results:
        raise ValueError("要記基準，[tests] 要設 results（指令寫出的 JUnit XML，例如 pytest --junit-xml=.air/test-results.xml）。")
    r, cases = _run_suite(cfg, suite)
    if cases is None:
        raise ValueError(f"測試跑不起來，沒有記基準：{r['detail']}")
    if r["exit"] not in (0, 1):
        raise ValueError(f"測試 {r['detail']}，不是 0 或 1，沒有記基準（可能是用法錯誤、中斷或沒有收集到測試）。")
    failed = sorted(t for t, st in cases.items() if st == junit.FAILED)
    data = load_baseline(cfg) or {"schema": BASELINE_SCHEMA, "suites": {}}
    data["suites"][suite.name] = {"failed": failed, "time": now(), "head": git(cfg.root, "rev-parse", "HEAD"), "command": suite.command}
    data["time"] = now()
    write_json(cfg.root / cfg.test_baseline, data)
    return {"suite": suite.name, "failed": failed, "total": len(cases), "file": cfg.test_baseline, **{k: r[k] for k in ("exit", "duration_ms")}}


# ── 各項檢查 ──


def architecture(report: Report, root: Path, scope: str) -> dict:
    result = {"name": "architecture", "required": True, "detail": report.summary(root),
              "checked": len(report.checked), "violations": [v.format(root) for v in report.hard[:40]]}
    if report.hard:
        return {**result, "status": "fail"}
    if scope == "final" and not report.checked:
        return {**result, "status": "fail", "detail": "final 驗收至少要有一個層內的檔案；目前各層都是空的。"}
    return {**result, "status": "pass"}


def packaging(cfg: Config, scope: str) -> dict | None:
    """setuptools 的明確 packages 清單有沒有列到各層的子套件（沒有明確清單的專案不出現這一項）。批次只顯示，final 必須通過。"""
    declared = buildcheck.declared(cfg.root)
    if not any(d.packages is not None for d in declared) and not buildcheck.notes(cfg):
        return None
    result = {"name": "packaging", "required": scope == "final"}
    probs = buildcheck.problems(cfg)
    if probs:
        return {**result, "status": "fail", "detail": "；".join(p.hint() for p in probs), "items": [m for p in probs for m in p.missing]}
    notes = buildcheck.notes(cfg)
    return {**result, "status": "pass", "detail": "各層的子套件都在 packages 清單裡" + ("；" + "；".join(notes) if notes else "")}


def allowances(cfg: Config) -> dict:
    items = [f"{a.layer} → {a.module}（{a.reason or '沒寫原因'}）" for a in cfg.allow_legacy]
    detail = f"還有 {len(items)} 筆暫時允許的舊模組 import，收尾時必須清空：" + "；".join(items) if items else "已清空"
    return {"name": "allow-legacy", "required": True, "status": "fail" if items else "pass", "items": items, "detail": detail}


def legacy_remaining(report: Report, root: Path) -> dict:
    """收尾（final）時層外不能留有邏輯的模組或轉接檔，除非在 migration.exclude；只剩套件標記（空的 __init__.py）才算乾淨。

    試跑（csvkit）：17 個有邏輯的模組還在層外，phase done 照樣通過，階段 6 第 1 步（移除轉接檔）也沒有任何檢查。
    刻意保留的（給外掛用的舊 import 路徑、不打算遷移的腳本）要明確寫進 exclude 並在報告寫明原因，不是靠自覺。"""
    groups = legacy.split(report.legacy)
    left = {k: groups[k] for k in (legacy.LOGIC, legacy.SHIM) if groups[k]}
    items = [f"{rel(p, root)}（{legacy.LABELS[k]}）" for k, paths in left.items() for p in paths]
    if not left:
        n = len(groups[legacy.MARKER])
        detail = f"層外只剩 {n} 個套件標記用的 __init__.py" if n else "沒有層外的模組"
        return {"name": "legacy-remaining", "required": True, "status": "pass", "items": [], "detail": detail}
    detail = (f"層外還有 {legacy.describe({k: len(groups[k]) for k in legacy.KINDS})}：搬進層，或移除轉接檔（呼叫端改成直接 import 新的層）；"
              "刻意保留的要經使用者同意加進 .air/project.toml 的 migration.exclude，並在遷移報告寫明原因")
    return {"name": "legacy-remaining", "required": True, "status": "fail", "items": items, "detail": detail}


# ── 驗收與紀錄 ──

def verify(cfg: Config, scope: str = "batch") -> dict:
    """執行全部檢查、寫下紀錄並回傳。accepted 只有在必要檢查都 pass、而且驗收期間程式碼沒變時才是 True。"""
    if scope not in SCOPES:
        raise ValueError(f"未知的 scope：{scope}（可用：{'、'.join(SCOPES)}）")
    before = fingerprint(cfg)
    report = check(cfg)
    checks = [architecture(report, cfg.root, scope), run_tests(cfg, scope), *golden.checks(cfg, scope)]
    if (pack := packaging(cfg, scope)) is not None:
        checks.append(pack)
    if scope == "final":
        checks += [allowances(cfg), legacy_remaining(report, cfg.root)]
    reasons = [f"{c['name']}：{c['detail']}" for c in checks if c["required"] and c["status"] != "pass"]
    after = fingerprint(cfg)
    if not same_fingerprint(before, after):
        reasons.append("驗收期間程式碼或設定有變更（測試會改到 .py 檔？），請重新執行")
    receipt = {"schema": SCHEMA, "time": now(), "scope": scope, "branch": branch_name(cfg.root), "fingerprint": after,
               "checks": checks, "accepted": not reasons, "reasons": reasons}
    write_receipt(cfg.root, receipt)
    tests = next((c for c in checks if c["name"] == "project-tests"), {})
    usage.record(cfg.root, "verify", scope=scope, accepted=receipt["accepted"], checked=len(report.checked),
                 violations=len(report.hard), ms=tests.get("duration_ms"))
    return receipt


def write_json(path: Path, data: dict) -> None:
    """寫 .air/ 裡的紀錄：原子替換，中斷也不會留下半份。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_json(path: Path, schema: str) -> dict | None:
    """讀 .air/ 裡的紀錄；不存在、壞掉或 schema 不對都當成沒有。"""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("schema") == schema else None


def write_receipt(root: Path, receipt: dict) -> None:
    write_json(records_dir(root) / "verify.json", receipt)


def load_receipt(root: Path) -> dict | None:
    return load_json(records_dir(root) / "verify.json", SCHEMA)


def receipt_state(cfg: Config, receipt: dict | None) -> str:
    """none｜fresh（與目前程式碼一致）｜stale（驗收後程式碼或設定變了）"""
    if receipt is None:
        return "none"
    return "fresh" if same_fingerprint(receipt.get("fingerprint", {}), fingerprint(cfg)) else "stale"


def describe(cfg: Config, receipt: dict | None) -> str:
    """給 status 用的一行說明。"""
    state = receipt_state(cfg, receipt)
    if state == "none":
        return "無（python3 AIR/air.py verify）"
    verdict = "通過" if receipt["accepted"] else "未通過"
    when = receipt["time"][:16].replace("T", " ")
    note = "與目前程式碼一致" if state == "fresh" else "已過期：之後程式碼或設定有變更，要重新 verify"
    return f"{verdict}（{receipt['scope']}，{when}；{note}）"


def exit_code(receipt: dict) -> int:
    """0 通過；1 未通過；2 驗收本身出錯（測試無法執行、逾時）。"""
    if receipt["accepted"]:
        return 0
    return 2 if any(c["status"] == "error" for c in receipt["checks"] if c["required"]) else 1


def format_receipt(receipt: dict) -> str:
    lines = [f"AIR 驗收（{receipt['scope']}）"]
    for c in receipt["checks"]:
        note = "" if c["required"] else "（參考，不影響結果）"
        lines.append(f"  [{MARK[c['status']]}] {c['name']}{note}：{c['detail']}")
        lines += [f"      {v}" for v in c.get("violations", [])[:10]]
        if c.get("new_failures"):
            lines.append("      新增的失敗：" + "、".join(c["new_failures"][:10]) + (f"…共 {len(c['new_failures'])} 個" if len(c["new_failures"]) > 10 else ""))
        if c.get("fixed"):
            lines.append("      基準裡已修好：" + "、".join(c["fixed"][:10]))
        if c["status"] in ("fail", "error") and c.get("output") and not c.get("new_failures"):
            lines += [f"      {line}" for line in c["output"].rstrip().splitlines()[-15:]]
    fp = receipt["fingerprint"]
    head = (fp["head"] or "沒有 commit")[:12] + ("（工作樹有未提交的修改）" if fp["dirty"] else "")
    lines.append(f"  版本：{head}")
    where = f"{BRANCHES_DIR.as_posix()}/{branch_slug(receipt.get('branch', ''))}/verify.json"
    outcome = f"通過，紀錄已寫入 {where}" if receipt["accepted"] else "未通過 — " + "；".join(receipt["reasons"])
    lines.append(f"結果：{outcome}")
    return "\n".join(lines)
