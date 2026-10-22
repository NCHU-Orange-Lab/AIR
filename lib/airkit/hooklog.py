"""hook 自己壞掉時的紀錄（.air/hook-errors.log）。

hook 的內部錯誤（payload 解析失敗、找不到 Python、程式例外）一律 fail-open：不擋 AI 的工作，commit 前檢查仍會把關。
但 fail-open 不能是靜默的：實戰中 Windows 上的 hook 每次都壞（找不到 Python、cp950 把 payload 弄壞），強制機制整個消失卻沒人知道。
所以每一次失敗都寫一行進這個檔（UTC 時間、哪個 hook、原因，tab 分隔；hooks/_python.sh 也寫同一個格式），
status 與 doctor 顯示最近 24 小時的次數。檔案不進版本控制，可以隨時刪掉。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

LOG_FILE = Path(".air") / "hook-errors.log"
STAMP = "%Y-%m-%dT%H:%M:%SZ"


def log(root: Path, where: str, reason: str) -> None:
    """寫一筆。寫不進去就算了：這裡再丟例外，hook 就真的壞到底了。"""
    try:
        path = root / LOG_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        clean = " ".join(reason.split())
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now(timezone.utc).strftime(STAMP)}\t{where}\t{clean}\n")
    except OSError:
        pass


def recent(root: Path, hours: int = 24) -> list[tuple[str, str, str]]:
    """最近 hours 小時內的紀錄：(時間, 哪個 hook, 原因)。格式不對的行略過。"""
    path = root / LOG_FILE
    if not path.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stamp, _, rest = line.partition("\t")
        where, _, reason = rest.partition("\t")
        try:
            when = datetime.strptime(stamp, STAMP).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if when >= cutoff:
            out.append((stamp, where, reason))
    return out


def describe(root: Path) -> str:
    """給 status 用的一行。"""
    items = recent(root)
    if not items:
        return "無"
    _, where, reason = items[-1]
    return f"{len(items)} 次（最近一次 {where}：{reason}；見 {LOG_FILE.as_posix()}）"
