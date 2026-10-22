"""成本的代理指標（.air/metrics.jsonl）。

AIR 看不到真實的 token，但看得到流程裡的放大器：verify 跑了幾次、審查範圍印了多少字、審查交回幾次、
post-edit 與 Stop 擋了幾次、survey 印了多少。實戰中使用者因為「token 太多」喊停，之後任何省 token 的改動
都要能用這些數字證明有效，不能只靠估算。每筆一行 JSON（UTC 時間、種類、數字），不進版本控制，可以隨時刪。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

METRICS_FILE = Path(".air") / "metrics.jsonl"
KINDS = ("verify", "review-scope", "review-result", "post-edit-blocked", "stop-blocked", "survey")


def record(root: Path, kind: str, **fields) -> None:
    """追加一筆。寫不進去就算了：指標不能讓正事失敗。"""
    try:
        path = root / METRICS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "kind": kind, **fields}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError):
        pass


def load(root: Path) -> list[dict]:
    path = root / METRICS_FILE
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("kind") in KINDS:
            out.append(entry)
    return out


def summary(root: Path) -> dict:
    """各種類的次數，加上審查範圍與 survey 的累計字數。"""
    entries = load(root)
    out = {kind: sum(1 for e in entries if e["kind"] == kind) for kind in KINDS}
    out["review-scope-chars"] = sum(int(e.get("chars", 0)) for e in entries if e["kind"] == "review-scope")
    out["survey-chars"] = sum(int(e.get("chars", 0)) for e in entries if e["kind"] == "survey")
    return out


def describe(root: Path) -> str:
    """給 status 用的一行。"""
    s = summary(root)
    if not any(s[k] for k in KINDS):
        return "無"
    return (f"verify {s['verify']} 次、審查範圍 {s['review-scope']} 次（共 {s['review-scope-chars']:,} 字）、"
            f"審查交回 {s['review-result']} 次、post-edit 擋下 {s['post-edit-blocked']} 次、Stop 擋下 {s['stop-blocked']} 次、"
            f"survey {s['survey']} 次（共 {s['survey-chars']:,} 字）；見 {METRICS_FILE.as_posix()}")
