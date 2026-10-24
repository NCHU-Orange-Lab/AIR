"""語意審查：補上沒有程式檢查的規則（skill air-review 的程式部分）。

36 條規則裡只有 10 條有程式檢查（7 條完整、3 條部分），其餘靠審查。程式負責能機械判斷的部分：
  範圍  用 git 界定「這次的變更」：改了哪些層內的 .py、改到哪些行；每一層要對照哪些規則（沒有〔hook 檢查〕標記的）與出處。
        規則每層只列一次；上次審過、內容沒變的檔案只列名字，沿用上次的發現，不必重審
  驗證  模型交回的結果：規則存在、適用該層、不是 check 負責的、行號落在改到的行附近、信心與優先級在範圍內、
        結論與發現一致（含沿用的發現）
  紀錄  通過驗證的結果寫進 .air/branches/<分支>/review.json：每個檔案一筆，綁定該檔內容的 sha256；之後哪個檔案又改了，只有那個檔案要重審
判斷本身由模型做（做法在 skill air-review）。結果只是參考：不擋 commit，也不能推翻 check 或 verify 的失敗。

為什麼每個檔案一筆：原本整批一個指紋，修掉一個發現就整批作廢、全部重讀，是實戰中最大的 token 放大器之一。
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from . import rules, usage, verify
from .checks import rel
from .config import Config

RECORD_NAME = "review.json"  # 放在 verify.records_dir（依分支分開）
SCHEMA = "air.review.v2"
SCOPE_SCHEMA = "air.review-scope.v2"
MIN_CONFIDENCE = 0.5   # 不到這個信心的發現不收（不確定就不報）
NEAR = 3               # 發現的行號離改到的行最多差這麼多行，才算「這次變更引入的」
BLOCKING = (0, 1)      # 必須處理（或向使用者說明）的 priority
TOP_KEYS = ("findings", "overall", "explanation")
OPTIONAL_KEYS = ("rereviewed",)  # 沿用的檔案如果這次真的重審過（例如上次是誤判），列在這裡，它的發現就以這次交回的為準
FINDING_KEYS = ("rule", "title", "body", "confidence", "priority", "path", "lines")
OVERALL = ("pass", "fail")
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.M)
STATUS = {"added": "新增", "modified": "修改", "untracked": "新檔案"}
OUTLINE_LINE = 160     # 大綱裡每一行最多印這麼多字


class ReviewError(ValueError):
    """無法界定這次的變更（不是 git repo、找不到 --base）。"""


@dataclass(frozen=True)
class ChangedFile:
    path: Path                                # 絕對路徑
    status: str                               # added | modified | untracked
    lines: tuple[tuple[int, int], ...] | None  # 改到的行（新內容的行號）；None＝整個檔案都是新的
    name: str | None                          # 適用的規則檔（層或 tests）；None＝不在範圍
    why: str = ""                             # 不在範圍的原因
    sha: str = ""                             # 內容的 sha256（在範圍的檔案才算）


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


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
            files.append(ChangedFile(path, status, lines, rule_name, sha=file_sha(path)))
    return ref, files


def _fingerprint(files: list[ChangedFile], root: Path, ref: str | None) -> str:
    h = hashlib.sha256((ref or "").encode())
    h.update(verify.digest([f.path for f in files], root).encode())
    return h.hexdigest()


def scope(cfg: Config, base: str | None = None) -> dict:
    """這次變更的審查範圍：每層的規則（只列一次）、層內的 .py（內容沒變的沿用上次審查）、不在範圍的檔案。"""
    ref, files = changed_files(cfg, base)
    in_scope = [f for f in files if f.name]
    kept = (load_record(cfg.root) or {}).get("files", {})
    order = {name: i for i, name in enumerate([*cfg.layers, "tests"])}
    entries = []
    for f in in_scope:
        path = rel(f.path, cfg.root)
        old = kept.get(path)
        reused = bool(old) and old.get("sha") == f.sha
        entries.append({
            "path": path, "layer": f.name, "status": f.status, "sha": f.sha,
            "changed_lines": None if f.lines is None else [list(r) for r in f.lines],
            "reused": reused,
            "findings": len(old["findings"]) if reused else None,
            "blocking": sum(x["priority"] in BLOCKING for x in old["findings"]) if reused else None,
        })
    layers = sorted({e["layer"] for e in entries if not e["reused"]}, key=lambda n: order.get(n, 99))  # 只列有待審檔案的層
    return {
        "schema": SCOPE_SCHEMA,
        "base": base,
        "ref": ref,
        "rules": {name: [r.to_dict() for r in rules.for_review(name)] for name in layers},
        "files": entries,
        "out_of_scope": [{"path": rel(f.path, cfg.root), "why": f.why} for f in files if not f.name],
        "fingerprint": _fingerprint(in_scope, cfg.root, ref),
    }


def moves(cfg: Config, ref: str | None) -> dict[str, tuple[str, int]]:
    """這次變更裡搬移過來的檔案：新路徑 → (舊路徑, 相似度 %)。git 認得的改名與複製（git mv、git add 過的搬移；
    舊路徑留著轉接檔時 git 看到的是「複製＋舊檔修改」，所以用 -C），加上沒有 git add 的搬移：還沒追蹤的新檔，
    內容與 ref 裡某個這次刪除或修改了的檔案完全相同。"""
    if ref is None:
        return {}
    root = cfg.root
    out: dict[str, tuple[str, int]] = {}
    items = (verify.git(root, "diff", "--relative", "-C", "--name-status", "-z", ref) or "").split("\0")
    i, sources = 0, []
    while i < len(items) and items[i]:
        status = items[i]
        if status[:1] in ("R", "C"):
            out[items[i + 2]] = (items[i + 1], int(status[1:] or 100))
            i += 3
            continue
        if status[:1] in ("D", "M"):
            sources.append(items[i + 1])
        i += 2
    if sources:
        by_blob = {}
        for name in sources:
            blob = verify.git(root, "rev-parse", f"{ref}:./{name}")
            if blob:
                by_blob[blob] = name
        for name in (verify.git(root, "ls-files", "-z", "--others", "--exclude-standard") or "").split("\0"):
            if name and name not in out and (blob := verify.git(root, "hash-object", "--", name)) in by_blob:
                out[name] = (by_blob[blob], 100)
    return out


def outline(text: str) -> list[str]:
    """檔案的骨架（給搬移過來的檔案用）：import、模組層級的敘述、def／class 的簽名與行號範圍，方法縮排列在 class 底下。
    import 時就執行的敘述（設定 log、下載）照樣列出：那是 ADP-3 這類規則要看的。解析不了就回傳空清單（呼叫端改附全文）。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines()

    def head(node: ast.AST, indent: str) -> str:
        first = lines[node.lineno - 1].strip()
        if len(first) > OUTLINE_LINE:
            first = first[:OUTLINE_LINE] + "…"
        span = f"{node.lineno}" if node.end_lineno == node.lineno else f"{node.lineno}–{node.end_lineno}"
        return f"{span:>9}│{indent}{first}"

    out = []
    for node in tree.body:
        out.append(head(node, ""))
        if isinstance(node, ast.ClassDef):
            out += [head(n, "    ") for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    return out


def _numbered(text: str) -> str:
    return "\n".join(f"{i:4d}│{line}" for i, line in enumerate(text.splitlines(), 1))


def bundle(cfg: Config, sc: dict, full: bool = False) -> dict:
    """把每個待審檔案的差異附進範圍（files[i]["diff"]）：改過的檔案是相對 ref 的 git diff，新檔案是整個內容加行號。

    一次拿齊，審查者（自己或子 agent）不用再逐檔跑 git diff、讀檔；交給子 agent 時整段貼給它，它就不用重跑 review。
    沿用的檔案不附（本來就不必重審）。
    搬移過來的檔案（moves）不重印全文：附上「搬自哪裡、相似度」、對舊路徑的差異（內容沒變就是空的）與大綱（outline）。
    試跑（nanoGPT）：model.py 搬進 adapters，--bundle 把 330 行整份印出（19,482 字）；Claude 用 head 截掉、等於沒看完就審，
    Codex 整份讀進對話。搬移的檔案內容 AI 剛剛才處理過，要的是「改了什麼」與「現在要對照哪些規則」。full=True 照舊附全文。
    """
    moved = {} if full else moves(cfg, sc["ref"])
    for f in sc["files"]:
        if f["reused"]:
            continue
        path = cfg.root / f["path"]
        if f["path"] in moved:
            old, similarity = moved[f["path"]]
            text = path.read_text(encoding="utf-8", errors="replace")
            skeleton = outline(text)
            f["moved_from"], f["similarity"] = old, similarity
            f["diff"] = "" if similarity == 100 else (
                verify.git(cfg.root, "diff", "--relative", "-C", sc["ref"], "--", old, f["path"]) or "")
            if skeleton:
                f["outline"] = skeleton
            else:
                f["diff"] = _numbered(text)  # 解析不了：附全文
        elif f["status"] == "untracked" or sc["ref"] is None:
            f["diff"] = _numbered(path.read_text(encoding="utf-8", errors="replace"))
        else:
            f["diff"] = verify.git(cfg.root, "diff", "--relative", "--no-renames", sc["ref"], "--", f["path"]) or ""
    return sc


def _ranges(lines: list[list[int]] | None) -> str:
    return "整個檔案" if lines is None else "、".join(f"{a}–{b}" if a != b else str(a) for a, b in lines)


def format_scope(sc: dict) -> str:
    against = f"相對 {sc['base']}（merge-base {sc['ref'][:12]}）" if sc["base"] else (
        f"相對 HEAD {sc['ref'][:12]}" if sc["ref"] else "還沒有 commit，所有檔案都算新的")
    lines = [f"AIR 語意審查範圍：{against}，含未 commit 的修改"]
    todo = [f for f in sc["files"] if not f["reused"]]
    reused = [f for f in sc["files"] if f["reused"]]
    if not sc["files"]:
        lines.append("  沒有要審查的變更（層內沒有改到的 .py）。")
    if reused:
        lines.append("  沿用上次審查（內容沒變，不必重審）：" + "、".join(f"{f['path']}（{f['findings']} 個發現）" for f in reused))
    for layer, rs in sc["rules"].items():
        mine = [f for f in todo if f["layer"] == layer]
        if not mine:
            continue
        lines.append(f"  【{layer} 的規則】對照底下 {len(mine)} 個檔案（規則只列一次）")
        for r in rs:
            gap = f"　← 只審程式抓不到的部分：{r['gap']}" if r["gap"] else ""
            lines.append(f"    {r['id']}【{r['level']}】{r['text']}　（{r['source']}:{r['line']}）{gap}")
        for f in mine:
            lines.append(f"    檔案：{f['path']}（{STATUS[f['status']]}；改到的行：{_ranges(f['changed_lines'])}）")
    if sc["out_of_scope"]:
        shown = sc["out_of_scope"][:15]
        more = f"…等 {len(sc['out_of_scope'])} 個" if len(sc["out_of_scope"]) > 15 else ""
        lines.append("  不在範圍：" + "、".join(f"{f['path']}（{f['why']}）" for f in shown) + more)
    tracked = [f["path"] for f in todo if f["status"] != "untracked"]
    fresh = [f["path"] for f in todo if f["status"] == "untracked"]
    bundled = [f for f in todo if "diff" in f]
    if bundled:
        lines.append(f"── 差異（--bundle：底下 {len(bundled)} 個待審檔案的差異都在這裡，不用再跑 git diff 或讀檔；新檔案是整個內容加行號，搬移過來的是差異加大綱）")
        for f in bundled:
            if "moved_from" in f:
                lines.append(f"=== {f['path']}（{STATUS[f['status']]}；搬自 {f['moved_from']}，相似度 {f['similarity']}%）")
                lines.append(f["diff"] or "（內容與搬移前相同）")
                if "outline" in f:
                    lines.append("── 大綱（整個檔案都在審查範圍：照大綱對照這一層的規則，要看哪一段就讀那幾行；要全文加 --full）")
                    lines += f["outline"]
            else:
                lines.append(f"=== {f['path']}（{STATUS[f['status']]}）")
                lines.append(f["diff"])
    else:
        if tracked and sc["ref"]:
            lines.append(f"看差異：git diff --no-renames {sc['ref'][:12]} -- " + " ".join(tracked))
        if fresh:
            lines.append("新檔案（整個檔案都在範圍，直接讀）：" + " ".join(fresh))
        if todo:
            lines.append("一次拿齊範圍與差異：python3 AIR/air.py review --bundle")
    if todo:
        lines.append("審完交回：python3 AIR/air.py review --result - <<'EOF' … EOF（JSON 格式見 skill air-review；只交這次審的檔案，沿用的不用再列）")
    elif reused:
        lines.append("全部沿用，沒有要重審的檔案；紀錄與目前的變更一致。")
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
    missing, extra = set(TOP_KEYS) - set(data), set(data) - set(TOP_KEYS) - set(OPTIONAL_KEYS)
    if missing:
        errors.append(f"缺少欄位：{'、'.join(sorted(missing))}")
    if extra:
        errors.append(f"不認得的欄位：{'、'.join(sorted(extra))}（只能有 {'、'.join(TOP_KEYS)}，選用 {'、'.join(OPTIONAL_KEYS)}）")
    findings = data.get("findings", [])
    if not isinstance(findings, list):
        errors.append("findings 要是陣列（沒有發現就 []）")
        findings = []
    if data.get("overall") not in OVERALL:
        errors.append("overall 只能是 pass 或 fail")
    if not isinstance(data.get("explanation"), str):
        errors.append("explanation 要是字串")
    files = {f["path"]: f for f in sc["files"]}
    rereviewed = data.get("rereviewed", [])
    if not isinstance(rereviewed, list) or not all(isinstance(p, str) for p in rereviewed):
        errors.append("rereviewed 要是路徑的陣列")
        rereviewed = []
    rereviewed = [p.replace("\\", "/") for p in rereviewed]
    for p in rereviewed:
        if p not in files:
            errors.append(f"rereviewed：{p!r} 不在這次變更的範圍")
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
        submitted = {f["path"] for f in out} | set(rereviewed)
        # 沿用的檔案如果這次沒有重新交回，它們上次的發現仍然有效，結論要把它們算進去
        kept_blocking = any(f["reused"] and f["blocking"] and f["path"] not in submitted for f in sc["files"])
        blocking = kept_blocking or any(f["priority"] in BLOCKING for f in out)
        expected = "fail" if blocking else "pass"
        if data["overall"] != expected:
            why = "有 priority 0 或 1 的發現" + ("（含沿用上次審查的檔案）" if kept_blocking else "") if blocking else "沒有 priority 0 或 1 的發現"
            errors.append(f"overall 應該是 {expected}：{why}")
    return errors, out


def submit(cfg: Config, data, base: str | None = None) -> tuple[list[str], dict | None]:
    """交回審查結果：驗證，通過才記錄。回傳 (錯誤, 紀錄)；有錯誤時紀錄是 None。review --result 與 batch finish 共用。"""
    sc = scope(cfg, base)
    errors, findings = validate(sc, data)
    usage.record(cfg.root, "review-result", valid=not errors, findings=len(findings))
    if errors:
        return errors, None
    return [], record(cfg, sc, data, findings)


# ── 紀錄 ──

def record(cfg: Config, sc: dict, data: dict, findings: list[dict]) -> dict:
    """每個檔案一筆：這次交回的檔案換成新的發現；沿用的檔案保留上次的；不在範圍的檔案不留。"""
    kept = (load_record(cfg.root) or {}).get("files", {})
    by_path: dict[str, list[dict]] = {}
    for f in findings:
        by_path.setdefault(f["path"], []).append(f)
    rereviewed = {p.replace("\\", "/") for p in data.get("rereviewed", [])}
    now = verify.now()
    files = {}
    for f in sc["files"]:
        path = f["path"]
        if f["reused"] and path not in by_path and path not in rereviewed:
            files[path] = kept[path]
        else:
            files[path] = {"sha": f["sha"], "layer": f["layer"], "findings": by_path.get(path, []), "time": now}
    flat = [x for path in files for x in files[path]["findings"]]
    blocking = sum(x["priority"] in BLOCKING for x in flat)
    rec = {
        "schema": SCHEMA, "time": now, "base": sc["base"], "ref": sc["ref"],
        "files": files, "findings": flat, "blocking": blocking,
        "overall": "fail" if blocking else "pass", "explanation": data["explanation"],
        "reused": sum(1 for f in sc["files"] if f["reused"] and f["path"] not in by_path and f["path"] not in rereviewed),
    }
    verify.write_json(verify.records_dir(cfg.root) / RECORD_NAME, rec)
    return rec


def load_record(root: Path) -> dict | None:
    rec = verify.load_json(verify.records_dir(root) / RECORD_NAME, SCHEMA)
    return rec if rec and isinstance(rec.get("files"), dict) else None


def _state(cfg: Config, rec: dict | None) -> tuple[str, int]:
    """(none｜fresh｜stale｜clean｜unknown, 要重審的檔案數)。"""
    if rec is None:
        return "none", 0
    try:
        sc = scope(cfg, rec.get("base"))
    except ReviewError:
        return "unknown", 0
    if not sc["files"]:
        return "clean", 0
    todo = sum(1 for f in sc["files"] if not f["reused"])
    return ("fresh" if todo == 0 else "stale"), todo


def state(cfg: Config, rec: dict | None) -> str:
    """none｜fresh（每個檔案都與審過的內容一致）｜stale（有檔案改過，只有那些要重審）｜clean（目前沒有要審的變更）｜unknown（不是 git repo）"""
    return _state(cfg, rec)[0]


def verdict(rec: dict) -> str:
    n = rec["blocking"]
    return f"通過（{len(rec['findings'])} 個發現，沒有 priority 0／1）" if n == 0 else f"{n} 個 priority 0／1 的發現待處理"


def describe(cfg: Config, rec: dict | None) -> str:
    """給 status 用的一行說明。"""
    st, todo = _state(cfg, rec)
    if st == "none":
        return "無（每批 commit 前照 skill air-review 做）"
    if st == "unknown":
        return "無法判斷（不是 git repo）"
    when = rec["time"][:16].replace("T", " ")
    note = {"fresh": "與目前的變更一致", "stale": f"已過期：{todo} 個檔案改過，只有它們要重審（其餘沿用）", "clean": "目前沒有要審查的變更"}[st]
    return f"{verdict(rec)}（{when}；{note}）"


def exit_code(rec: dict) -> int:
    return 1 if rec["blocking"] else 0


def format_result(rec: dict) -> str:
    lines = [f"AIR 語意審查：{verdict(rec)}"]
    for f in sorted(rec["findings"], key=lambda f: (f["priority"], f["path"], f["lines"]["start"])):
        where = f"{f['path']}:{_ranges([[f['lines']['start'], f['lines']['end']]])}"
        lines.append(f"  [P{f['priority']}] {f['rule']}【{f['level']}】{where}　{f['title']}"
                     f"（信心 {f['confidence']}；{f['rule_source']['path']}:{f['rule_source']['line']}）")
    if rec.get("reused"):
        lines.append(f"  （{rec['reused']} 個檔案沿用上次審查的發現）")
    if rec["blocking"]:
        lines.append("priority 0／1 的發現必須處理，或向使用者說明為什麼不處理；處理後只有改過的檔案要重審。")
    lines.append(f"紀錄已寫入 {verify.BRANCHES_DIR.as_posix()}/<分支>/{RECORD_NAME}（每個檔案綁定它的內容；之後哪個檔案又改了，只有那個檔案要重審）")
    return "\n".join(lines)
