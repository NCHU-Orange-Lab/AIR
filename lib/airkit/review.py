"""語意審查：補上沒有程式檢查的規則（skill air-review 的程式部分）。

36 條規則裡只有 10 條有程式檢查（7 條完整、3 條部分），其餘靠審查。程式負責能機械判斷的部分：
  範圍  用 git 界定「這次的變更」：改了哪些層內的 .py、改到哪些行；每個檔案要對照哪些規則（沒有〔hook 檢查〕標記的）與出處
  驗證  模型交回的結果：規則存在、適用該層、不是 check 負責的、行號落在改到的行附近、信心與優先級在範圍內、結論與發現一致
  紀錄  通過驗證的結果寫進 .air/review.json，綁定這次變更的指紋；之後變更的內容又變了，紀錄就過期
判斷本身由模型做（做法在 skill air-review）。結果只是參考：不擋 commit，也不能推翻 check 或 verify 的失敗。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from . import rules, verify
from .checks import rel
from .config import Config

RECORD_FILE = Path(".air") / "review.json"
SCHEMA = "air.review.v1"
SCOPE_SCHEMA = "air.review-scope.v1"
MIN_CONFIDENCE = 0.5   # 不到這個信心的發現不收（不確定就不報）
NEAR = 3               # 發現的行號離改到的行最多差這麼多行，才算「這次變更引入的」
BLOCKING = (0, 1)      # 必須處理（或向使用者說明）的 priority
TOP_KEYS = ("findings", "overall", "explanation")
FINDING_KEYS = ("rule", "title", "body", "confidence", "priority", "path", "lines")
OVERALL = ("pass", "fail")
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.M)


class ReviewError(ValueError):
    """無法界定這次的變更（不是 git repo、找不到 --base）。"""


@dataclass(frozen=True)
class ChangedFile:
    path: Path                                # 絕對路徑
    status: str                               # added | modified | untracked
    lines: tuple[tuple[int, int], ...] | None  # 改到的行（新內容的行號）；None＝整個檔案都是新的
    name: str | None                          # 適用的規則檔（層或 tests）；None＝不在範圍
    why: str = ""                             # 不在範圍的原因


# ── 用 git 界定這次的變更 ──

def _ref(root: Path, base: str | None) -> str | None:
    """比對的基準 commit：--base 時是它與 HEAD 的 merge-base，否則是 HEAD；還沒有 commit 時是 None。"""
    if verify.git(root, "rev-parse", "--show-toplevel") is None:
        raise ReviewError("語意審查需要 git 來界定這次的變更：這裡不是 git repo，或沒有安裝 git。")
    head = verify.git(root, "rev-parse", "--verify", "-q", "HEAD")
    if base is None:
        return head or None
    if not head:
        raise ReviewError("還沒有任何 commit，不能用 --base。")
    merge_base = verify.git(root, "merge-base", base, "HEAD")
    if not merge_base:
        raise ReviewError(f"找不到 {base}（--base 要是分支名稱、tag 或 commit）。")
    return merge_base


def _changes(root: Path, ref: str | None) -> dict[str, str]:
    """相對專案根目錄的路徑 → added | modified | untracked。已刪除的檔案不列（沒有東西可審）。"""
    out: dict[str, str] = {}
    if ref is None:
        for name in (verify.git(root, "ls-files", "-z", "--cached") or "").split("\0"):
            if name:
                out[name] = "added"
    else:
        items = (verify.git(root, "diff", "--relative", "--no-renames", "--name-status", "-z", ref) or "").split("\0")
        for status, name in zip(items[::2], items[1::2]):
            if status[:1] == "A":
                out[name] = "added"
            elif status[:1] in ("M", "T"):
                out[name] = "modified"
    for name in (verify.git(root, "ls-files", "-z", "--others", "--exclude-standard") or "").split("\0"):
        if name:
            out[name] = "untracked"
    return out


def _hunks(root: Path, ref: str, name: str) -> tuple[tuple[int, int], ...]:
    """這個檔案改到的行（新內容的行號）。只有刪除的 hunk 記成它所在的那一行。"""
    diff = verify.git(root, "diff", "--relative", "--no-renames", "-U0", ref, "--", name) or ""
    ranges = []
    for start, count in HUNK_RE.findall(diff):
        start, count = max(int(start), 1), int(count) if count else 1
        ranges.append((start, start + count - 1 if count else start))
    return tuple(ranges)


def changed_files(cfg: Config, base: str | None = None) -> tuple[str | None, list[ChangedFile]]:
    root, ref = cfg.root, _ref(cfg.root, base)
    files = []
    for name, status in sorted(_changes(root, ref).items()):
        path = (root / name).resolve()
        if not path.is_file():
            continue
        rule_name = rules.name_for(path, cfg)
        if path.suffix != ".py":
            files.append(ChangedFile(path, status, None, None, "不是 Python 檔"))
        elif rule_name is None:
            files.append(ChangedFile(path, status, None, None, "不在任何層（舊程式碼或其他檔案）"))
        else:
            lines = _hunks(root, ref, name) if status == "modified" and ref else None
            files.append(ChangedFile(path, status, lines, rule_name))
    return ref, files


def _fingerprint(files: list[ChangedFile], root: Path, ref: str | None) -> str:
    h = hashlib.sha256((ref or "").encode())
    h.update(verify.digest([f.path for f in files], root).encode())
    return h.hexdigest()


def scope(cfg: Config, base: str | None = None) -> dict:
    """這次變更的審查範圍：層內的 .py（各自要對照哪些規則）、不在範圍的檔案、綁定這批內容的指紋。"""
    ref, files = changed_files(cfg, base)
    in_scope = [f for f in files if f.name]
    return {
        "schema": SCOPE_SCHEMA,
        "base": base,
        "ref": ref,
        "files": [{
            "path": rel(f.path, cfg.root), "layer": f.name, "status": f.status,
            "changed_lines": None if f.lines is None else [list(r) for r in f.lines],
            "rules": [r.to_dict() for r in rules.for_review(f.name)],
        } for f in in_scope],
        "out_of_scope": [{"path": rel(f.path, cfg.root), "why": f.why} for f in files if not f.name],
        "fingerprint": _fingerprint(in_scope, cfg.root, ref),
    }


def _ranges(lines: list[list[int]] | None) -> str:
    return "整個檔案" if lines is None else "、".join(f"{a}–{b}" if a != b else str(a) for a, b in lines)


def format_scope(sc: dict) -> str:
    against = f"相對 {sc['base']}（merge-base {sc['ref'][:12]}）" if sc["base"] else (
        f"相對 HEAD {sc['ref'][:12]}" if sc["ref"] else "還沒有 commit，所有檔案都算新的")
    lines = [f"AIR 語意審查範圍：{against}，含未 commit 的修改"]
    if not sc["files"]:
        lines.append("  沒有要審查的變更（層內沒有改到的 .py）。")
    status = {"added": "新增", "modified": "修改", "untracked": "新檔案"}
    for f in sc["files"]:
        lines.append(f"  {f['path']}（{f['layer']}，{status[f['status']]}；改到的行：{_ranges(f['changed_lines'])}）")
        for r in f["rules"]:
            gap = f"　← 只審程式抓不到的部分：{r['gap']}" if r["gap"] else ""
            lines.append(f"    {r['id']}【{r['level']}】{r['text']}　（{r['source']}:{r['line']}）{gap}")
    if sc["out_of_scope"]:
        shown = sc["out_of_scope"][:15]
        more = f"…等 {len(sc['out_of_scope'])} 個" if len(sc["out_of_scope"]) > 15 else ""
        lines.append("  不在範圍：" + "、".join(f"{f['path']}（{f['why']}）" for f in shown) + more)
    tracked = [f["path"] for f in sc["files"] if f["status"] != "untracked"]
    fresh = [f["path"] for f in sc["files"] if f["status"] == "untracked"]
    if tracked and sc["ref"]:
        lines.append(f"看差異：git diff --no-renames {sc['ref'][:12]} -- " + " ".join(tracked))
    if fresh:
        lines.append("新檔案（整個檔案都在範圍，直接讀）：" + " ".join(fresh))
    if sc["files"]:
        lines.append("審完交回：python3 AIR/air.py review --result - <<'EOF' … EOF（JSON 格式見 skill air-review）")
    return "\n".join(lines)


# ── 驗證交回的結果 ──

def _line_range(value) -> tuple[int, int] | None:
    if not isinstance(value, dict) or set(value) != {"start", "end"}:
        return None
    start, end = value["start"], value["end"]
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (start, end)) or not 1 <= start <= end:
        return None
    return start, end


def _near(rng: tuple[int, int], changed: list[list[int]]) -> bool:
    start, end = rng
    return any(start <= b + NEAR and end >= a - NEAR for a, b in changed)


def _number(value, lo, hi, integer: bool) -> bool:
    if isinstance(value, bool) or not isinstance(value, int if integer else (int, float)):
        return False
    return lo <= value <= hi


def validate(sc: dict, data) -> tuple[list[str], list[dict]]:
    """回傳 (錯誤, 補上規則出處的發現)。有任何錯誤就不能記錄。"""
    if not isinstance(data, dict):
        return ["結果要是 JSON 物件（{\"findings\": [...], \"overall\": ..., \"explanation\": ...}）"], []
    errors = []
    missing, extra = set(TOP_KEYS) - set(data), set(data) - set(TOP_KEYS)
    if missing:
        errors.append(f"缺少欄位：{'、'.join(sorted(missing))}")
    if extra:
        errors.append(f"不認得的欄位：{'、'.join(sorted(extra))}（只能有 {'、'.join(TOP_KEYS)}）")
    findings = data.get("findings", [])
    if not isinstance(findings, list):
        errors.append("findings 要是陣列（沒有發現就 []）")
        findings = []
    if data.get("overall") not in OVERALL:
        errors.append("overall 只能是 pass 或 fail")
    if not isinstance(data.get("explanation"), str):
        errors.append("explanation 要是字串")
    files = {f["path"]: f for f in sc["files"]}
    known = rules.by_id()
    out = []
    for i, f in enumerate(findings, 1):
        tag = f"findings[{i}]"
        if not isinstance(f, dict):
            errors.append(f"{tag}：要是物件")
            continue
        before = len(errors)
        missing, extra = set(FINDING_KEYS) - set(f), set(f) - set(FINDING_KEYS)
        if missing:
            errors.append(f"{tag}：缺少欄位 {'、'.join(sorted(missing))}")
        if extra:
            errors.append(f"{tag}：不認得的欄位 {'、'.join(sorted(extra))}（只能有 {'、'.join(FINDING_KEYS)}）")
        rule = known.get(f.get("rule")) if isinstance(f.get("rule"), str) else None
        path_key = f["path"].replace("\\", "/") if isinstance(f.get("path"), str) else None  # Windows 上的審查者可能交回反斜線
        file = files.get(path_key) if path_key else None
        if rule is None:
            errors.append(f"{tag}：沒有規則 {f.get('rule')!r}（只能引用 review 列出的規則編號）")
        if file is None:
            errors.append(f"{tag}：{f.get('path')!r} 不在這次變更的範圍（只報 review 列出的檔案）")
        if rule and file:
            if rule.name != file["layer"]:
                errors.append(f"{tag}：{rule.id} 是 {rule.name} 的規則，{file['path']} 屬於 {file['layer']}")
            elif rule.checked == rules.FULL:
                errors.append(f"{tag}：{rule.id} 由 python3 AIR/air.py check 負責，不在語意審查的範圍")
        for key in ("title", "body"):
            if not isinstance(f.get(key), str) or not f[key].strip():
                errors.append(f"{tag}：{key} 要是非空字串")
        confidence = f.get("confidence")
        if not _number(confidence, 0, 1, integer=False):
            errors.append(f"{tag}：confidence 要是 0–1 的數字")
        elif confidence < MIN_CONFIDENCE:
            errors.append(f"{tag}：confidence {confidence} 不到 {MIN_CONFIDENCE}：不確定就不報")
        if not _number(f.get("priority"), 0, 3, integer=True):
            errors.append(f"{tag}：priority 要是 0–3 的整數")
        rng = _line_range(f.get("lines"))
        if rng is None:
            errors.append(f"{tag}：lines 要是 {{\"start\": n, \"end\": m}}，1 ≤ start ≤ end")
        elif file and file["changed_lines"] is not None and not _near(rng, file["changed_lines"]):
            errors.append(f"{tag}：第 {_ranges([list(rng)])} 行不在這次改到的行（{_ranges(file['changed_lines'])}）附近；"
                          "既有問題不報，確實由這次變更引入的，把行號指到改到的行")
        if len(errors) == before:
            out.append({**f, "path": path_key, "level": rule.level, "rule_text": rule.text, "rule_source": {"path": rule.source, "line": rule.line}})
    if not errors:
        blocking = any(f["priority"] in BLOCKING for f in out)
        expected = "fail" if blocking else "pass"
        if data["overall"] != expected:
            errors.append(f"overall 應該是 {expected}：{'有' if blocking else '沒有'} priority 0 或 1 的發現")
    return errors, out


# ── 紀錄 ──

def record(cfg: Config, sc: dict, data: dict, findings: list[dict]) -> dict:
    rec = {
        "schema": SCHEMA, "time": verify.now(), "base": sc["base"], "ref": sc["ref"], "fingerprint": sc["fingerprint"],
        "files": [f["path"] for f in sc["files"]], "findings": findings,
        "blocking": sum(f["priority"] in BLOCKING for f in findings),
        "overall": data["overall"], "explanation": data["explanation"],
    }
    verify.write_json(cfg.root / RECORD_FILE, rec)
    return rec


def load_record(root: Path) -> dict | None:
    return verify.load_json(root / RECORD_FILE, SCHEMA)


def state(cfg: Config, rec: dict | None) -> str:
    """none｜fresh（與目前的變更一致）｜stale（審查後變更的內容又變了）｜clean（目前沒有要審的變更）｜unknown（不是 git repo）"""
    if rec is None:
        return "none"
    try:
        sc = scope(cfg, rec.get("base"))
    except ReviewError:
        return "unknown"
    if not sc["files"]:
        return "clean"
    return "fresh" if sc["fingerprint"] == rec.get("fingerprint") else "stale"


def verdict(rec: dict) -> str:
    n = rec["blocking"]
    return f"通過（{len(rec['findings'])} 個發現，沒有 priority 0／1）" if n == 0 else f"{n} 個 priority 0／1 的發現待處理"


def describe(cfg: Config, rec: dict | None) -> str:
    """給 status 用的一行說明。"""
    st = state(cfg, rec)
    if st == "none":
        return "無（每批 commit 前照 skill air-review 做）"
    if st == "unknown":
        return "無法判斷（不是 git repo）"
    when = rec["time"][:16].replace("T", " ")
    note = {"fresh": "與目前的變更一致", "stale": "已過期：之後變更的內容又變了，要重新審查", "clean": "目前沒有要審查的變更"}[st]
    return f"{verdict(rec)}（{when}；{note}）"


def exit_code(rec: dict) -> int:
    return 1 if rec["blocking"] else 0


def format_result(rec: dict) -> str:
    lines = [f"AIR 語意審查：{verdict(rec)}"]
    for f in sorted(rec["findings"], key=lambda f: (f["priority"], f["path"], f["lines"]["start"])):
        where = f"{f['path']}:{_ranges([[f['lines']['start'], f['lines']['end']]])}"
        lines.append(f"  [P{f['priority']}] {f['rule']}【{f['level']}】{where}　{f['title']}"
                     f"（信心 {f['confidence']}；{f['rule_source']['path']}:{f['rule_source']['line']}）")
    if rec["blocking"]:
        lines.append("priority 0／1 的發現必須處理，或向使用者說明為什麼不處理；處理後要重新審查。")
    lines.append(f"紀錄已寫入 {RECORD_FILE.as_posix()}（綁定這次變更的內容，之後又改了就過期）")
    return "\n".join(lines)
