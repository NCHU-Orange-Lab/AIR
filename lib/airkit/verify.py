"""正式驗收：架構檢查＋專案測試（收尾時再加暫時允許與層外模組的檢查），結果綁定這一版程式碼，寫成可追溯的紀錄。

`check` 是快速診斷；`verify` 回答「這一份程式碼有沒有通過全部必要的檢查」。
紀錄存在 .air/verify.json（本機，不進版本控制），帶著程式碼、設定與 AIR 版本的指紋：
任一項改變，紀錄就過期（stale），階段轉移時不能拿來用。

這是工作紀錄，不是防偽證明：有寫入權限的人（或 AI）可以改它。要可信的關卡，請在 CI 重新執行 verify。
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path

from .checks import Report, check, rel
from .config import KIT_DIR, PROJECT_FILE, Config

RECEIPT_FILE = Path(".air") / "verify.json"
SCHEMA = "air.verify.v1"
SCOPES = ("batch", "final")
OUTPUT_TAIL = 3000  # 測試輸出只保留結尾這麼多字
SKIP_DIRS = {"venv", ".venv", "node_modules", "build", "dist", "__pycache__"}
MARK = {"pass": "通過", "fail": "未通過", "skipped": "略過", "error": "錯誤"}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def git(root: Path, *args: str) -> str | None:
    """git 指令的 stdout；不是 git repo、沒有 commit 或沒有 git 時回傳 None。"""
    try:
        out = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return out.stdout.strip()


# ── 指紋 ──

def source_files(root: Path) -> list[Path]:
    """影響驗收結果的檔案：所有 .py（含 AIR 自己的 checker）、AIR 的規則設定與版本。"""
    listed = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if listed is None:
        files = [p for p in root.rglob("*.py")
                 if not any(part.startswith(".") or part in SKIP_DIRS for part in p.relative_to(root).parts)]
    else:
        files = [root / name for name in listed.split("\0") if name.endswith(".py")]
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
        "source": digest(source_files(root), root),
        "config": digest([root / PROJECT_FILE], root),
        "air": (KIT_DIR / "VERSION").read_text(encoding="utf-8").strip(),
    }


def same_fingerprint(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in ("source", "config", "air"))


# ── 各項檢查 ──

def tail(text: str) -> str:
    return text if len(text) <= OUTPUT_TAIL else "…（前面省略）\n" + text[-OUTPUT_TAIL:]


def _kill_tree(proc: subprocess.Popen) -> None:
    """結束整個程序群組（shell 與它啟動的測試），不只 shell 本身。"""
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except ProcessLookupError:
        pass


def run_tests(cfg: Config) -> dict:
    """執行專案的測試指令（shell 字串，在專案根目錄）。沒設定＝skipped；逾時或啟動失敗＝error；非 0＝fail。"""
    result = {"name": "project-tests", "required": True, "command": cfg.test_command}
    if not cfg.test_command:
        return {**result, "status": "skipped", "detail": "test-command 尚未設定（.air/project.toml 的 project.test-command）"}
    start = time.monotonic()
    try:
        proc = subprocess.Popen(cfg.test_command, shell=True, cwd=cfg.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", start_new_session=True)
    except OSError as e:
        return {**result, "status": "error", "detail": f"無法執行：{e}"}
    try:
        output, _ = proc.communicate(timeout=cfg.test_timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        output, _ = proc.communicate()
        return {**result, "status": "error", "output": tail(output or ""),
                "detail": f"逾時（超過 {cfg.test_timeout} 秒；可在 .air/project.toml 設定 project.test-timeout）"}
    ms = int((time.monotonic() - start) * 1000)
    return {**result, "status": "pass" if proc.returncode == 0 else "fail", "exit": proc.returncode, "duration_ms": ms,
            "detail": f"exit {proc.returncode}（{ms} ms）", "output": tail(output or "")}


def architecture(report: Report, root: Path, scope: str) -> dict:
    result = {"name": "architecture", "required": True, "detail": report.summary(root),
              "checked": len(report.checked), "violations": [v.format(root) for v in report.hard[:40]]}
    if report.hard:
        return {**result, "status": "fail"}
    if scope == "final" and not report.checked:
        return {**result, "status": "fail", "detail": "final 驗收至少要有一個層內的檔案；目前各層都是空的。"}
    return {**result, "status": "pass"}


def allowances(cfg: Config) -> dict:
    items = [f"{a.layer} → {a.module}（{a.reason or '沒寫原因'}）" for a in cfg.allow_legacy]
    detail = f"還有 {len(items)} 筆暫時允許的舊模組 import，收尾時必須清空：" + "；".join(items) if items else "已清空"
    return {"name": "allow-legacy", "required": True, "status": "fail" if items else "pass", "items": items, "detail": detail}


def legacy_remaining(report: Report, root: Path) -> dict:
    names = [rel(p, root) for p in report.legacy]
    detail = f"還有 {len(names)} 個模組在層外（留在外面的要在遷移報告寫明原因）" if names else "沒有層外的模組"
    return {"name": "legacy-remaining", "required": False, "status": "fail" if names else "pass", "items": names, "detail": detail}


# ── 驗收與紀錄 ──

def verify(cfg: Config, scope: str = "batch") -> dict:
    """執行全部檢查、寫下紀錄並回傳。accepted 只有在必要檢查都 pass、而且驗收期間程式碼沒變時才是 True。"""
    if scope not in SCOPES:
        raise ValueError(f"未知的 scope：{scope}（可用：{'、'.join(SCOPES)}）")
    before = fingerprint(cfg)
    report = check(cfg)
    checks = [architecture(report, cfg.root, scope), run_tests(cfg)]
    if scope == "final":
        checks += [allowances(cfg), legacy_remaining(report, cfg.root)]
    reasons = [f"{c['name']}：{c['detail']}" for c in checks if c["required"] and c["status"] != "pass"]
    after = fingerprint(cfg)
    if not same_fingerprint(before, after):
        reasons.append("驗收期間程式碼或設定有變更（測試會改到 .py 檔？），請重新執行")
    receipt = {"schema": SCHEMA, "time": now(), "scope": scope, "fingerprint": after,
               "checks": checks, "accepted": not reasons, "reasons": reasons}
    write_receipt(cfg.root, receipt)
    return receipt


def write_receipt(root: Path, receipt: dict) -> None:
    path = root / RECEIPT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)  # 原子替換：中斷也不會留下半份紀錄


def load_receipt(root: Path) -> dict | None:
    path = root / RECEIPT_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("schema") == SCHEMA else None


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
        if c["name"] == "project-tests" and c["status"] in ("fail", "error") and c.get("output"):
            lines += [f"      {line}" for line in c["output"].rstrip().splitlines()[-15:]]
    fp = receipt["fingerprint"]
    head = (fp["head"] or "沒有 commit")[:12] + ("（工作樹有未提交的修改）" if fp["dirty"] else "")
    lines.append(f"  版本：{head}")
    outcome = "通過，紀錄已寫入 .air/verify.json" if receipt["accepted"] else "未通過 — " + "；".join(receipt["reasons"])
    lines.append(f"結果：{outcome}")
    return "\n".join(lines)
