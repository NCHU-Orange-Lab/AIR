"""批次的代理指標（air.py batch finish／close／list）：每批結束時把這批的成本寫進 .air/history.jsonl，改版前後可以比較。

finish（commit 之前）一次做完一批的收尾：沿用或重跑 verify、驗證並記錄審查結果、擋下沒處理的 P0／P1、寫這一筆，
最後印出 commit 指令（history.jsonl 跟這一批一起 commit）。原本要分開跑 verify、review --result、commit、batch close，
試跑（2026-10-24）裡 AIR 組比對照組多出的呼叫一半是這些流程步驟，每多一次呼叫就要重讀一次整個上下文。
close（commit 之後）保留給舊的做法：紀錄留在工作樹，跟下一批一起 commit。

AIR 看不到真實的 token，但看得到流程裡的放大器（報告 §4.1.4 第 10 點）：搬了幾檔幾行、審查範圍印了幾字、verify 跑了幾次多久、
post-edit 與 Stop 擋了幾次、審查交回幾次，再加上剩餘的舊模組數（進度由程式算）。原始紀錄在本機的 .air/metrics.jsonl；
批次的一筆摘要進 history.jsonl（進版本控制、跟階段轉移同一份、跟著分支）。指標是這台機器累計的，別台機器的 session 不在裡面。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from . import history, legacy, project, review, usage, verify
from .checks import check, layer_of, rel
from .config import KIT_DIR, REPORT_FILE, Config

HOW = history.BATCH  # history.jsonl 的 how
TRANSITIONS = history.TRANSITIONS
window_start = history.window_start  # 搬到 history.py：verify 也要用


class BatchError(ValueError):
    """不能結束這一批（階段不對、驗收沒通過或已過期、審查還有沒處理的 priority 0／1 又沒有確認）。"""


def _when(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        t = datetime.fromisoformat(value)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def batches(root: Path) -> list[dict]:
    return [e for e in history.load(root) if e.get("how") == HOW]


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


def git_stats(cfg: Config, since_head: str | None, worktree: bool = False) -> dict | None:
    """起點到 HEAD 的變更，只算套件目錄與測試目錄；沒有起點或起點不在這個 repo 裡是 None。
    worktree=True（batch finish，還沒 commit）：比的是工作樹，還沒追蹤的新檔整個算新增。"""
    root = cfg.root
    if not since_head or verify.git(root, "rev-parse", "--verify", "-q", f"{since_head}^{{commit}}") is None:
        return None
    paths = [rel(p, root) for p in (cfg.package_dir, cfg.tests_dir) if p.exists()]
    if not paths:
        return {"files": 0, "insertions": 0, "deletions": 0, "layer_files_added": 0, "layer_insertions": 0}
    to = [] if worktree else ["HEAD"]
    numstat = (verify.git(root, "-c", "core.quotepath=off", "diff", "--numstat", "--no-renames", since_head, *to, "--", *paths) or "").splitlines()
    status = (verify.git(root, "-c", "core.quotepath=off", "diff", "--name-status", "--no-renames", since_head, *to, "--", *paths) or "").splitlines()
    if worktree:
        for name in (verify.git(root, "-c", "core.quotepath=off", "ls-files", "--others", "--exclude-standard", "--", *paths) or "").splitlines():
            if (root / name).is_file():
                n = (root / name).read_bytes().count(b"\n")
                numstat.append(f"{n}\t0\t{name}")
                status.append(f"A\t{name}")
    files = ins = dels = layer_ins = 0
    for line in numstat:
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        files += 1
        a, d = (int(x) if x.isdigit() else 0 for x in parts[:2])
        ins += a
        dels += d
        if layer_of(root / parts[2], cfg) is not None:
            layer_ins += a
    added = sum(1 for line in status if line.startswith("A\t") and layer_of(root / line.split("\t", 1)[1], cfg) is not None)
    return {"files": files, "insertions": ins, "deletions": dels, "layer_files_added": added, "layer_insertions": layer_ins}


def _unresolved(rec: dict | None) -> list[dict]:
    """審查紀錄裡 priority 0／1 的發現（紀錄本身記著，不看目前的狀態：改過的檔案還沒重審就仍然算沒處理）。"""
    return [f for f in (rec or {}).get("findings", []) if f.get("priority") in review.BLOCKING]


class ReviewInvalid(ValueError):
    """batch finish 交回的審查結果沒通過驗證（沒有記錄，也沒有結束這一批）。errors 是逐條的原因。"""

    def __init__(self, errors: list[str]):
        super().__init__("審查結果無效")
        self.errors = errors


def _can_batch(cfg: Config) -> None:
    if cfg.phase != "migrating":
        raise BatchError(f"只有在逐批遷移（phase = migrating）時才有批次可以結束；目前是 {cfg.phase}。")
    if (p := project.paused(cfg.root)) is not None:
        raise BatchError(f"遷移暫停中（{project.describe_pause(p)}）：暫停期間沒有批次；要繼續請使用者執行 python3 AIR/air.py resume。")


def _fresh(cfg: Config, receipt: dict | None) -> bool:
    return receipt is not None and bool(receipt.get("accepted")) and verify.receipt_state(cfg, receipt) == "fresh"


def close(cfg: Config, note: str = "", ack: str = "") -> tuple[dict, list[str]]:
    """結束這一批（commit 之後）：前置是階段 migrating、驗收紀錄新鮮且通過、審查紀錄裡沒有沒處理的 priority 0／1（或用 ack 寫明理由）；
    審查紀錄缺少或過期只提醒。回傳 (寫進 history 的那一筆, 提醒)。"""
    _can_batch(cfg)
    receipt = verify.load_receipt(cfg.root)
    if not _fresh(cfg, receipt):
        raise BatchError(f"這一批還沒有通過驗收：{verify.describe(cfg, receipt)}。先讓 python3 AIR/air.py verify 通過（紀錄要與目前的程式碼一致）。")
    return _record(cfg, note, ack, before_commit=False)


def finish(cfg: Config, note: str = "", ack: str = "", review_data=None) -> tuple[dict, list[str], dict]:
    """結束這一批（commit 之前，一個指令做完）：驗收紀錄新鮮且通過就沿用，否則重跑 verify；有交審查結果就驗證並記錄；
    其餘同 close。這一筆的 head 是 commit 之前的 HEAD，標上 before_commit：下一批的起點改用「把這一筆 commit 進去的那個 commit」
    （history.window_start）。回傳 (這一筆, 提醒, 驗收紀錄)。驗收沒通過、審查結果無效、有沒處理的 P0／P1 都不寫紀錄。"""
    _can_batch(cfg)
    receipt = verify.load_receipt(cfg.root)
    reused = _fresh(cfg, receipt)
    if not reused:
        receipt = verify.verify(cfg, "batch")
        if not receipt["accepted"]:
            raise BatchError("驗收沒有通過，這一批還不能結束：" + "；".join(receipt["reasons"]))
    if review_data is not None:
        errors, _ = review.submit(cfg, review_data)
        if errors:
            raise ReviewInvalid(errors)
    entry, warnings = _record(cfg, note, ack, before_commit=True)
    return entry, warnings, {**receipt, "reused": reused}


def _record(cfg: Config, note: str, ack: str, before_commit: bool) -> tuple[dict, list[str]]:
    warnings = []
    rec = review.load_record(cfg.root)
    state = review.state(cfg, rec)
    open_p = _unresolved(rec)
    acked = None
    if open_p and not ack.strip():
        raise BatchError(f"審查紀錄裡還有 {len(open_p)} 個 priority 0／1 的發現沒有處理：{_where(open_p)}。"
                         "處理之後重跑 air.py review（只有改過的檔案要重審）；確定不處理就向使用者說明，經同意後用 --ack \"理由\" 結束這一批。")
    if open_p:
        acked = {"reason": ack.strip(), "findings": [f"P{f['priority']} {f['rule']} {f['path']}:{f['lines']['start']}" for f in open_p]}
    elif ack.strip():
        warnings.append("--ack 沒有用到：審查紀錄裡沒有 priority 0／1 的發現")
    if state == "none":
        warnings.append("沒有語意審查紀錄：每批 commit 前照 skill air-review 審一次（審查不擋批次，只提醒）")
    elif state == "stale":
        warnings.append("語意審查紀錄已過期：" + review.describe(cfg, rec))
    since_time, since_head = window_start(cfg.root)
    report = check(cfg)
    left = legacy.counts(report.legacy)
    alerts = {c["name"]: c for c in verify.legacy_alerts(cfg, report)}
    stale, grown = alerts.get("allow-legacy-stale"), alerts.get("legacy-growth")
    for c in (stale, grown):
        if c and c["status"] == "fail":
            warnings.append(c["detail"])
    entry = {
        "time": verify.now(), "from": cfg.phase, "to": cfg.phase, "head": verify.git(cfg.root, "rev-parse", "HEAD"), "how": HOW,
        "n": len(batches(cfg.root)) + 1, "note": note.strip() or None, "air": (KIT_DIR / "VERSION").read_text(encoding="utf-8").strip(),
        "since": {"time": since_time, "head": since_head},
        "metrics": metrics_between(cfg.root, since_time),
        "git": git_stats(cfg, since_head, worktree=before_commit),
        "legacy_remaining": left[legacy.LOGIC], "legacy_shims": left[legacy.SHIM], "legacy_markers": left[legacy.MARKER],
        "allow_stale": len(stale["items"]) if stale else None,
        "legacy_new": len(grown["items"]) if grown and grown["detail"] != verify.NO_BASELINE else None,
        "review": state, "review_blocking": len(open_p) if rec else None, "ack": acked,
        **({"before_commit": True} if before_commit else {}),
    }
    project.record(cfg.root, entry)
    return entry, warnings


def commit_hint(cfg: Config, entry: dict) -> str:
    """batch finish 之後要執行的 commit 指令：套件、測試、遷移報告與 history.jsonl；這一批改到的其他檔案也要加上。"""
    paths = [rel(p, cfg.root) for p in (cfg.package_dir, cfg.tests_dir, cfg.root / REPORT_FILE) if p.exists()]
    note = (entry.get("note") or "").replace('"', "＂")
    message = f"AIR 遷移{note}" if note.startswith("第") else f"AIR 遷移第 {entry['n']} 批：{note or '<內容>'}"
    return f'git add {" ".join(paths)} .air/history.jsonl && git commit -m "{message}"（這一批改到的其他檔案也一起加）'


def _where(findings: list[dict], limit: int = 3) -> str:
    shown = [f"P{f['priority']} {f['path']}:{f['lines']['start']}" for f in findings[:limit]]
    return "、".join(shown) + (f" 等 {len(findings)} 個" if len(findings) > limit else "")


def describe(e: dict) -> str:
    """一行摘要（status、close 的輸出）。"""
    m, g = e.get("metrics", {}), e.get("git")
    moved = f"搬進層 {g['layer_files_added']} 檔 {g['layer_insertions']} 行、共改 {g['files']} 檔" if g else "git 統計不可用"
    when = (e.get("time") or "")[:16].replace("T", " ")
    note = f"「{e['note']}」" if e.get("note") else ""
    return (f"第 {e.get('n')} 批{note}（{when}，AIR {e.get('air')}）：{moved}；審查範圍 {m.get('review_scope_runs', 0)} 次共 {m.get('review_scope_chars', 0):,} 字、"
            f"交回 {m.get('review_results', 0)} 次；verify {m.get('verify_runs', 0)} 次 {m.get('verify_ms', 0) / 1000:.0f} 秒；"
            f"post-edit 擋 {m.get('post_edit_blocked', 0)} 次、Stop 擋 {m.get('stop_blocked', 0)} 次；舊模組剩 {_legacy(e)}"
            + (f"；確認不處理 {len(e['ack']['findings'])} 個 P1（{e['ack']['reason']}）" if e.get("ack") else ""))


def _legacy(e: dict) -> str:
    shims = e.get("legacy_shims")
    return f"{e.get('legacy_remaining')}" + (f"（另有 {shims} 個轉接檔）" if shims else "")


def _p1(e: dict) -> str:
    """批次表的「未解 P1」欄：沒有審查紀錄是 -；有確認不處理的是「N（已確認）」。"""
    n = e.get("review_blocking")
    return "-" if n is None else f"{n}（已確認）" if e.get("ack") else str(n)


def format_list(entries: list[dict]) -> str:
    if not entries:
        return "還沒有結束過任何一批（每批 commit 之前執行 python3 AIR/air.py batch finish）。"
    lines = ["批次 | 時間 | AIR | 搬進層（檔／行） | 改檔 | 審查範圍（次／字） | 交回 | verify（次／秒） | 擋下（post-edit／Stop） | 舊模組剩 | 未解 P1 | 備註",
             "---|---|---|---|---|---|---|---|---|---|---|---"]
    for e in entries:
        m, g = e.get("metrics", {}), e.get("git") or {}
        lines.append(" | ".join(str(x) for x in [
            e.get("n"), (e.get("time") or "")[:16].replace("T", " "), e.get("air"),
            f"{g.get('layer_files_added', '-')}／{g.get('layer_insertions', '-')}", g.get("files", "-"),
            f"{m.get('review_scope_runs', 0)}／{m.get('review_scope_chars', 0):,}", m.get("review_results", 0),
            f"{m.get('verify_runs', 0)}／{m.get('verify_ms', 0) / 1000:.0f}", f"{m.get('post_edit_blocked', 0)}／{m.get('stop_blocked', 0)}",
            _legacy(e), _p1(e), e.get("note") or "",
        ]))
    lines.append("（指標是這台機器 .air/metrics.jsonl 累計的；別台機器的 session 不在裡面）")
    return "\n".join(lines)
