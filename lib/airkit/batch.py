"""批次的代理指標（air.py batch close／list）：每批結束時把這批的成本寫進 .air/history.jsonl，改版前後可以比較。

AIR 看不到真實的 token，但看得到流程裡的放大器（報告 §4.1.4 第 10 點）：搬了幾檔幾行、審查範圍印了幾字、verify 跑了幾次多久、
post-edit 與 Stop 擋了幾次、審查交回幾次，再加上剩餘的舊模組數（進度由程式算）。原始紀錄在本機的 .air/metrics.jsonl；
批次的一筆摘要進 history.jsonl（進版本控制、跟階段轉移同一份、跟著分支）。指標是這台機器累計的，別台機器的 session 不在裡面。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from . import project, review, usage, verify
from .checks import check, layer_of, rel
from .config import KIT_DIR, Config

HOW = "batch"  # history.jsonl 的 how


class BatchError(ValueError):
    """不能結束這一批（階段不對、驗收沒通過或已過期）。"""


def _when(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        t = datetime.fromisoformat(value)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def batches(root: Path) -> list[dict]:
    return [e for e in project.history(root) if e.get("how") == HOW]


def window_start(root: Path) -> tuple[str | None, str | None]:
    """這一批的起點 (時間, HEAD)：上一批結束時；沒有上一批就是進入 migrating 那次轉移；都沒有就 (None, None)（算全部）。"""
    hist = project.history(root)
    for e in reversed(hist):
        if e.get("how") == HOW:
            return e.get("time"), e.get("head")
    for e in reversed(hist):
        if e.get("to") == "migrating" and e.get("how") != HOW:
            return e.get("time"), e.get("head")
    return None, None


def metrics_between(root: Path, since: str | None) -> dict:
    """metrics.jsonl 在 since 之後的統計。"""
    start = _when(since)
    entries = [e for e in usage.load(root) if start is None or (_when(e.get("time")) or start) > start]
    kind = lambda k: [e for e in entries if e["kind"] == k]  # noqa: E731
    return {
        "verify_runs": len(kind("verify")),
        "verify_accepted": sum(1 for e in kind("verify") if e.get("accepted")),
        "verify_ms": sum(int(e.get("ms") or 0) for e in kind("verify")),
        "review_scope_runs": len(kind("review-scope")),
        "review_scope_chars": sum(int(e.get("chars") or 0) for e in kind("review-scope")),
        "review_results": len(kind("review-result")),
        "review_findings": sum(int(e.get("findings") or 0) for e in kind("review-result") if e.get("valid")),
        "post_edit_blocked": len(kind("post-edit-blocked")),
        "stop_blocked": len(kind("stop-blocked")),
        "survey_runs": len(kind("survey")),
    }


def git_stats(cfg: Config, since_head: str | None) -> dict | None:
    """起點到 HEAD 的變更，只算套件目錄與測試目錄；沒有起點或起點不在這個 repo 裡是 None。"""
    root = cfg.root
    if not since_head or verify.git(root, "rev-parse", "--verify", "-q", f"{since_head}^{{commit}}") is None:
        return None
    paths = [rel(p, root) for p in (cfg.package_dir, cfg.tests_dir) if p.exists()]
    if not paths:
        return {"files": 0, "insertions": 0, "deletions": 0, "layer_files_added": 0, "layer_insertions": 0}
    numstat = verify.git(root, "diff", "--numstat", "--no-renames", since_head, "HEAD", "--", *paths) or ""
    status = verify.git(root, "diff", "--name-status", "--no-renames", since_head, "HEAD", "--", *paths) or ""
    files = ins = dels = layer_ins = 0
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        files += 1
        a, d = (int(x) if x.isdigit() else 0 for x in parts[:2])
        ins += a
        dels += d
        if layer_of(root / parts[2], cfg) is not None:
            layer_ins += a
    added = sum(1 for line in status.splitlines() if line.startswith("A\t") and layer_of(root / line.split("\t", 1)[1], cfg) is not None)
    return {"files": files, "insertions": ins, "deletions": dels, "layer_files_added": added, "layer_insertions": layer_ins}


def close(cfg: Config, note: str = "") -> tuple[dict, list[str]]:
    """結束這一批：前置是階段 migrating、驗收紀錄新鮮且通過；審查紀錄缺少或過期只提醒。回傳 (寫進 history 的那一筆, 提醒)。"""
    if cfg.phase != "migrating":
        raise BatchError(f"只有在逐批遷移（phase = migrating）時才有批次可以結束；目前是 {cfg.phase}。")
    receipt = verify.load_receipt(cfg.root)
    if receipt is None or not receipt.get("accepted") or verify.receipt_state(cfg, receipt) != "fresh":
        raise BatchError(f"這一批還沒有通過驗收：{verify.describe(cfg, receipt)}。先讓 python3 AIR/air.py verify 通過（紀錄要與目前的程式碼一致）。")
    warnings = []
    rec = review.load_record(cfg.root)
    state = review.state(cfg, rec)
    if state == "none":
        warnings.append("沒有語意審查紀錄：每批 commit 前照 skill air-review 審一次（審查不擋批次，只提醒）")
    elif state == "stale":
        warnings.append("語意審查紀錄已過期：" + review.describe(cfg, rec))
    since_time, since_head = window_start(cfg.root)
    entry = {
        "time": verify.now(), "from": cfg.phase, "to": cfg.phase, "head": verify.git(cfg.root, "rev-parse", "HEAD"), "how": HOW,
        "n": len(batches(cfg.root)) + 1, "note": note.strip() or None, "air": (KIT_DIR / "VERSION").read_text(encoding="utf-8").strip(),
        "since": {"time": since_time, "head": since_head},
        "metrics": metrics_between(cfg.root, since_time),
        "git": git_stats(cfg, since_head),
        "legacy_remaining": len(check(cfg).legacy),
        "review": state,
    }
    project.record(cfg.root, entry)
    return entry, warnings


def describe(e: dict) -> str:
    """一行摘要（status、close 的輸出）。"""
    m, g = e.get("metrics", {}), e.get("git")
    moved = f"搬進層 {g['layer_files_added']} 檔 {g['layer_insertions']} 行、共改 {g['files']} 檔" if g else "git 統計不可用"
    when = (e.get("time") or "")[:16].replace("T", " ")
    note = f"「{e['note']}」" if e.get("note") else ""
    return (f"第 {e.get('n')} 批{note}（{when}，AIR {e.get('air')}）：{moved}；審查範圍 {m.get('review_scope_runs', 0)} 次共 {m.get('review_scope_chars', 0):,} 字、"
            f"交回 {m.get('review_results', 0)} 次；verify {m.get('verify_runs', 0)} 次 {m.get('verify_ms', 0) / 1000:.0f} 秒；"
            f"post-edit 擋 {m.get('post_edit_blocked', 0)} 次、Stop 擋 {m.get('stop_blocked', 0)} 次；舊模組剩 {e.get('legacy_remaining')}")


def format_list(entries: list[dict]) -> str:
    if not entries:
        return "還沒有結束過任何一批（每批 commit 之後執行 python3 AIR/air.py batch close）。"
    lines = ["批次 | 時間 | AIR | 搬進層（檔／行） | 改檔 | 審查範圍（次／字） | 交回 | verify（次／秒） | 擋下（post-edit／Stop） | 舊模組剩 | 備註",
             "---|---|---|---|---|---|---|---|---|---|---"]
    for e in entries:
        m, g = e.get("metrics", {}), e.get("git") or {}
        lines.append(" | ".join(str(x) for x in [
            e.get("n"), (e.get("time") or "")[:16].replace("T", " "), e.get("air"),
            f"{g.get('layer_files_added', '-')}／{g.get('layer_insertions', '-')}", g.get("files", "-"),
            f"{m.get('review_scope_runs', 0)}／{m.get('review_scope_chars', 0):,}", m.get("review_results", 0),
            f"{m.get('verify_runs', 0)}／{m.get('verify_ms', 0) / 1000:.0f}", f"{m.get('post_edit_blocked', 0)}／{m.get('stop_blocked', 0)}",
            e.get("legacy_remaining"), e.get("note") or "",
        ]))
    lines.append("（指標是這台機器 .air/metrics.jsonl 累計的；別台機器的 session 不在裡面）")
    return "\n".join(lines)
