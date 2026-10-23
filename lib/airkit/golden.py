"""黃金重播：固定輸入跑一次、記下輸出檔的 sha256，之後每次驗收重跑比對。沒有測試的程式也能鎖住行為（TEST-5 的另一種做法）。

.air/golden.json（進版本控制）只存雜湊，不存輸出檔。逐位元比對，所以輸出必須是確定的（seed 固定、不含時間戳）。
重錄（golden record）只能由使用者決定：為了讓驗收過而重錄，等於改掉測試的預期結果。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import runner
from .config import Config, Golden

GOLDEN_FILE = Path(".air") / "golden.json"
SCHEMA = "air.golden.v1"


class GoldenError(ValueError):
    """記錄失敗（指令跑不起來、沒寫出輸出、名稱不存在）。"""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(cfg: Config, g: Golden) -> tuple[dict, dict[str, str | None]]:
    """跑一筆：回傳 (runner 的結果, 每個輸出檔的雜湊；缺檔是 None)。"""
    r = runner.run_command(g.command, cfg.root, g.timeout, f"可在 [[tests.golden]] {g.name} 設定 timeout")
    hashes = {}
    for out in g.outputs:
        p = cfg.root / out
        hashes[out] = _sha(p) if p.is_file() else None
    return r, hashes


def load(root: Path) -> dict | None:
    from . import verify
    return verify.load_json(root / GOLDEN_FILE, SCHEMA)


def record(cfg: Config, names: list[str] | None = None) -> dict:
    """跑指定（或全部）的黃金重播，把輸出的雜湊寫進 .air/golden.json（保留其他筆）。指令失敗或缺輸出就不寫，raise GoldenError。"""
    from . import verify
    by_name = {g.name: g for g in cfg.golden}
    if not by_name:
        raise GoldenError("沒有設定黃金重播：在 .air/project.toml 加 [[tests.golden]]（name、command、outputs）。")
    unknown = [n for n in (names or []) if n not in by_name]
    if unknown:
        raise GoldenError(f"沒有這些黃金重播：{'、'.join(unknown)}（有：{'、'.join(by_name)}）")
    chosen = [by_name[n] for n in names] if names else list(by_name.values())
    existing = load(cfg.root) or {"schema": SCHEMA, "items": {}}
    items, problems = dict(existing.get("items", {})), []
    for g in chosen:
        r, hashes = _run(cfg, g)
        if r["status"] == "error" or r.get("exit") != 0:
            problems.append(f"{g.name}：{r['detail']}")
            continue
        missing = [o for o, h in hashes.items() if h is None]
        if missing:
            problems.append(f"{g.name}：指令跑完了，但沒有寫出 {'、'.join(missing)}")
            continue
        items[g.name] = {"command": g.command, "outputs": hashes, "time": verify.now(), "head": verify.git(cfg.root, "rev-parse", "HEAD")}
    if problems:
        raise GoldenError("黃金重播沒有記錄：\n  - " + "\n  - ".join(problems))
    data = {"schema": SCHEMA, "time": verify.now(), "items": items}
    verify.write_json(cfg.root / GOLDEN_FILE, data)
    return {name: items[name] for name in (g.name for g in chosen)}


def checks(cfg: Config, scope: str) -> list[dict]:
    """verify 用：這個 scope 要重播的每一筆各一個檢查項。沒記錄＝error；輸出變了或缺了＝fail。"""
    recorded = (load(cfg.root) or {}).get("items", {})
    out = []
    for g in cfg.golden:
        if scope not in g.scope:
            continue
        item = {"name": f"golden:{g.name}", "required": True, "command": g.command}
        if g.name not in recorded:
            out.append({**item, "status": "error", "detail": "還沒記錄黃金輸出：python3 AIR/air.py golden record（由使用者決定）"})
            continue
        r, hashes = _run(cfg, g)
        if r["status"] == "error":
            out.append({**item, **r})
            continue
        if r["exit"] != 0:
            out.append({**item, **r, "status": "error", "detail": f"重播指令失敗：{r['detail']}"})
            continue
        expected = recorded[g.name]["outputs"]
        missing = sorted(o for o in g.outputs if hashes.get(o) is None)
        changed = sorted(o for o in g.outputs if hashes.get(o) is not None and hashes[o] != expected.get(o))
        if missing or changed:
            detail = "輸出跟黃金版本不同：" + "；".join(filter(None, [f"變了 {'、'.join(changed)}" if changed else "", f"缺少 {'、'.join(missing)}" if missing else ""]))
            out.append({**item, **r, "status": "fail", "detail": detail + "（輸出留在原處可以 diff；行為真的要改才重錄，由使用者決定）",
                        "changed": changed, "missing": missing})
        else:
            out.append({**item, **r, "status": "pass", "detail": f"{len(g.outputs)} 個輸出都一樣（{r['duration_ms']} ms）"})
    return out


def summary(cfg: Config) -> dict:
    recorded = (load(cfg.root) or {}).get("items", {})
    return {"configured": [g.name for g in cfg.golden], "recorded": [n for n in (g.name for g in cfg.golden) if n in recorded]}


def describe(cfg: Config) -> str:
    s = summary(cfg)
    if not s["configured"]:
        return ""
    pending = [n for n in s["configured"] if n not in s["recorded"]]
    note = f"；還沒記錄：{'、'.join(pending)}（python3 AIR/air.py golden record）" if pending else ""
    return f"{len(s['configured'])} 筆，已記錄 {len(s['recorded'])} 筆（{GOLDEN_FILE.as_posix()}）{note}"
