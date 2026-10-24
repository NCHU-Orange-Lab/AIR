"""按需注入（沒有路徑規則的宿主，目前是 Codex）：第一次在某一層寫程式時，把那一層的規則附給 AI（additionalContext）；
session 開始與壓縮後重置，並附上目前狀態。

Claude Code 不走這裡：安裝時把 AIR/rules/<層>.md 複製成 .claude/rules/air-<層>.md（帶 paths 前言，見 install.py），
讀寫該層的檔案時由宿主自己載入，不啟動 Python（實測每次 Read 多一個 Python 行程，Windows 上約 0.36 秒）。
規則仍只有 AIR/rules/ 一個來源。

.air/sessions/<session_id>.json（不進版本控制）記三件事：
  rules     這個 session 已附過哪些層的規則（Codex）；session 開始與壓縮後清掉
  touched   編輯工具動過的檔案（post-edit 記下）
  snapshot  session 第一次開始時各層 .py 的內容雜湊：結束前用它找出「這個 session 開始後才變的檔案」，
            不管是誰用什麼方式改的；別的 session 在這之前留下的半成品不算（實戰中 session B 什麼都沒改卻被擋下）
  seen      套件與測試目錄裡 git 看得到變更的檔案與它們的 (mtime, 大小)：每次 Bash 前比對，找出上一個 Bash 改了哪些檔案
            （試跑中 AI 幾乎都用 Bash 改檔，編輯工具的 hook 不會觸發：規則送不到、改完也沒有檢查）
  fired     每個 hook 觸發的次數與最後一次的時間（status、doctor 顯示），分得出「沒有違規」與「hook 沒跑」

規則只在那一層第一次出現自己的程式時附（has_logic）：第二輪試跑（2026-10-24，v3.2.2）AI 在建立分層時用一個指令建好六層空的
__init__.py，hook 就一次附上六份規則（4,521 字），8 次 Claude 試跑都是，之後每次呼叫都背著；空的套件標記與轉接檔用不到規則。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from . import hooklog, legacy, project, stages, verify
from .checks import layer_files, rel
from .config import KIT_DIR, Config
from .hosts import Host
from .rules import name_for as rule_name  # noqa: F401 — 哪個檔案適用哪份規則，跟語意審查共用同一個判斷

SESSIONS_DIR = hooklog.SESSIONS_DIR
STAGE_OF_PHASE = stages.STAGE_OF_PHASE  # 這個階段該讀 AIR/start/ 的哪一份
KEEP_DAYS = 7  # 超過這麼久沒動的注入紀錄，session 開始時順便清掉
PHASE_GUIDE = {
    "installed": "分析階段（AIR/start/2-analyze.md）：只讀程式、寫遷移報告，不修改套件與測試目錄裡的 .py（會被擋下；exclude 的路徑與套件外的腳本不受限）。",
    "analyzed": "計畫階段（AIR/start/3-plan.md）：在遷移報告寫計畫，不修改套件與測試目錄裡的 .py（會被擋下；exclude 的路徑與套件外的腳本不受限）。",
    "planned": "建立分層（AIR/start/4-layers.md）：python3 AIR/air.py layers，然後 check、phase layered。",
    "layered": "準備逐批遷移（AIR/start/5-migrate.md）：第一批開始時執行 phase migrating。",
    "migrating": "逐批遷移中（AIR/start/5-migrate.md；批次都做完後是 AIR/start/6-finish.md）：每批先補行為保護測試，verify 通過才 commit；每批結束停下來等核准。",
    "done": "遷移完成：新程式一律寫進對應的層，改到哪一層就照那一層的規則。",
}


def rule_text(name: str) -> str:
    return (KIT_DIR / "rules" / f"{name}.md").read_text(encoding="utf-8").strip()


def _state_file(root: Path, session_id: str) -> Path:
    safe = re.sub(r"[^\w.-]", "_", session_id or "unknown")[:80]
    return root / SESSIONS_DIR / f"{safe}.json"


def _load(root: Path, session_id: str) -> dict:
    try:
        data = json.loads(_state_file(root, session_id).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(root: Path, session_id: str, state: dict) -> None:
    path = _state_file(root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")


def _rel(root: Path, path: Path) -> str | None:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None  # 專案外的檔案不記


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def injected(root: Path, session_id: str) -> set[str]:
    return set(_load(root, session_id).get("rules", []))


def remember(root: Path, session_id: str, names: set[str]) -> None:
    state = _load(root, session_id)
    state["rules"] = sorted(set(state.get("rules", [])) | set(names))
    _save(root, session_id, state)


def touched(root: Path, session_id: str) -> list[str]:
    return list(_load(root, session_id).get("touched", []))


def remember_touched(root: Path, session_id: str, paths) -> None:
    """post-edit：記下這個 session 用編輯工具動過的檔案（相對根目錄）。"""
    rels = {r for r in (_rel(root, Path(p)) for p in paths) if r}
    if not rels:
        return
    state = _load(root, session_id)
    state["touched"] = sorted(set(state.get("touched", [])) | rels)
    _save(root, session_id, state)


def take_snapshot(cfg: Config, session_id: str) -> bool:
    """session 第一次開始時記下各層 .py 的內容雜湊；已經有就不重拍（壓縮、恢復都是同一個 session）。回傳有沒有拍。"""
    state = _load(cfg.root, session_id)
    if "snapshot" in state:
        return False
    state["snapshot"] = {_rel(cfg.root, f): _sha(f) for la in cfg.layers.values() for f in layer_files(la)}
    _save(cfg.root, session_id, state)
    return True


def stop_targets(cfg: Config, session_id: str) -> tuple[list[Path], list[Path], bool]:
    """結束前要檢查的檔案：(編輯工具記下的, session 開始後內容變了的層內 .py, 有沒有快照)。
    用 Bash 改的檔案靠快照抓；別的 session 在這個 session 開始前留下的半成品不在裡面。"""
    state = _load(cfg.root, session_id)
    edited = [cfg.root / r for r in state.get("touched", [])]
    snapshot = state.get("snapshot")
    changed = []
    if isinstance(snapshot, dict):
        for la in cfg.layers.values():
            for f in layer_files(la):
                if snapshot.get(_rel(cfg.root, f)) != _sha(f):
                    changed.append(f)
    return edited, changed, isinstance(snapshot, dict)


def count_fired(root: Path, session_id: str, event: str) -> None:
    """這個 hook 又觸發了一次（cmd_hook 在處理之前呼叫）。"""
    state = _load(root, session_id)
    counts = state.get("fired") if isinstance(state.get("fired"), dict) else {}
    counts[event] = int(counts.get(event, 0)) + 1
    state["fired"], state["last"] = counts, project.local_iso()
    _save(root, session_id, state)


def _dirty(cfg: Config) -> dict[str, list[int]] | None:
    """套件與測試目錄裡 git 看得到變更的檔案（含未追蹤的）→ [mtime_ns, 大小]。不是 git repo 是 None。"""
    paths = [rel(p, cfg.root) for p in (cfg.package_dir, cfg.tests_dir) if p.exists()]
    if not paths:
        return {}
    listed = verify.git(cfg.root, "-c", "core.quotepath=off", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *paths)
    if listed is None:
        return None
    out, items, i = {}, listed.split("\0"), 0
    while i < len(items):
        entry = items[i]
        i += 2 if entry[:1] in ("R", "C") else 1  # 改名與複製的下一個欄位是原本的路徑
        name = entry[3:]
        path = cfg.root / name
        if name and path.is_file():
            st = path.stat()
            out[name] = [st.st_mtime_ns, st.st_size]
    return out


def mark_seen(cfg: Config, session_id: str, paths=None) -> None:
    """session 開始：記下目前的變更當基準（之前就有的半成品不算這個 session 的）。post-edit：只更新編輯工具剛改的檔案，
    下一次 Bash 前就不會把它們當成 Bash 改的再查一次。"""
    state = _load(cfg.root, session_id)
    if paths is None:
        now = _dirty(cfg)
        if now is None:
            return
        state["seen"] = now
    else:
        seen = state.get("seen")
        if not isinstance(seen, dict):
            return
        for p in paths:
            r = _rel(cfg.root, Path(p))
            if r and Path(p).is_file():
                st = Path(p).stat()
                seen[r] = [st.st_mtime_ns, st.st_size]
    _save(cfg.root, session_id, state)


def bash_changes(cfg: Config, session_id: str) -> list[Path]:
    """上一次 Bash（或 session 開始）之後，套件與測試目錄裡內容變了的檔案；更新基準。沒有基準（session 開始時還沒裝好）時只記基準。"""
    now = _dirty(cfg)
    if now is None:
        return []
    state = _load(cfg.root, session_id)
    before = state.get("seen")
    state["seen"] = now
    _save(cfg.root, session_id, state)
    if not isinstance(before, dict):
        return []
    return [cfg.root / name for name, st in sorted(now.items()) if before.get(name) != st]


def has_logic(path: Path) -> bool:
    """這個檔案有自己的程式（不是空的套件標記、也不是只有 import 的轉接檔）：只有這種檔案才需要那一層的規則。"""
    return path.suffix == ".py" and path.is_file() and legacy.kind(path) == legacy.LOGIC


def paths_in_command(cfg: Config, command: str) -> list[Path]:
    """指令裡提到的層目錄與測試目錄（相對根目錄的寫法，例如 demo/steps/x.py）：Codex 沒有路徑規則，碰到之前先附那一層的規則。
    每個提到的目錄給一個代表路徑，交給 rules_to_inject 判斷是哪一份規則。只建目錄或 __init__.py 的指令（建立分層）不算：
    指令點名了那一層裡 __init__.py 以外的 .py，或那一層已經有自己的程式，才附。"""
    out = []
    for base in [*(la.path for la in cfg.layers.values()), cfg.tests_dir]:
        r = re.escape(rel(base, cfg.root))
        if not re.search(rf"(?<![\w.-]){r}(?![\w-])", command):
            continue
        named = re.findall(rf"(?<![\w.-]){r}/((?:[\w.-]+/)*[\w.-]+\.py)(?![\w-])", command)
        if any(Path(n).name != "__init__.py" for n in named) or any(has_logic(p) for p in sorted(base.rglob("*.py"))):
            out.append(base / "__init__.py")
    return out


def reset(root: Path, session_id: str) -> None:
    """session 開始（含壓縮、恢復）：清掉規則注入的紀錄（下次碰到某層會重新附），保留動過的檔案與快照；順便清掉太久沒動的。"""
    state = _load(root, session_id)
    state.pop("rules", None)
    if state:
        _save(root, session_id, state)
    else:
        _state_file(root, session_id).unlink(missing_ok=True)
    cutoff = time.time() - KEEP_DAYS * 86400
    for old in (root / SESSIONS_DIR).glob("*.json") if (root / SESSIONS_DIR).is_dir() else []:
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass


def rules_to_inject(cfg: Config, session_id: str, paths, lead: str = "你正要修改") -> str:
    """這些檔案所屬的層，這個 session 還沒附過規則的，附上並記住。回傳要給 AI 的文字（空＝不用附）。lead 是開頭的動詞。"""
    names = {n for n in (rule_name(p, cfg) for p in paths) if n} - injected(cfg.root, session_id)
    if not names:
        return ""
    remember(cfg.root, session_id, names)
    return "\n\n".join(
        f"【AIR：{lead} {name} 的檔案，這是它的規則（AIR/rules/{name}.md；這個 session 只附一次）】\n{rule_text(name)}"
        for name in sorted(names)
    )


def stage_text(stage: int) -> str:
    """AIR/start/ 裡那一個階段的全文。只附當下的階段：六份合計約 6,100 字，其他五份跟當下無關。"""
    return stages.text(stage)


def session_context(cfg: Config, host: Host | None = None) -> str:
    """session 開始、壓縮後附上的狀態摘要：由目前的設定、檢查結果與驗收紀錄重新算，不信任任何摘要。"""
    if host is not None and host.rules_dir:
        how = f"讀寫某一層的檔案時，{host.label} 會自動載入 {host.rules_dir.as_posix()}/air-<層>.md（AIR/rules/<層>.md 的複本）"
    else:
        how = "第一次在某一層寫程式時（只建空的 __init__.py 不算），hook 會附上那一層的規則（AIR/rules/<層>.md）"
    pause = project.paused(cfg.root)
    if pause is not None:  # 暫停中：不催該階段的事，也不附階段的做法；新程式仍要寫進對應的層
        guide = (f"遷移暫停中（{project.describe_pause(pause)}；階段 {cfg.phase} 保留）：照一般開發進行，新程式仍寫進對應的層，"
                 "改到哪一層就照那一層的規則；分析／計畫階段的 .py 凍結已解除。要繼續遷移，由使用者執行 python3 AIR/air.py resume，不要自己執行。")
    else:
        guide = PHASE_GUIDE.get(cfg.phase, f"未知的階段 {cfg.phase}：請使用者檢查 .air/project.toml")
    lines = [
        "【AIR 目前狀態（由 hook 在 session 開始與壓縮後附上）】",
        project.status(cfg),
        f"現在該做的事：{guide}",
        f"規則：常駐規則在 AIR/AGENTS.md；{how}。",
    ]
    stage = STAGE_OF_PHASE.get(cfg.phase) if pause is None else None
    section = stage_text(stage) if stage else ""
    if section:
        lines += ["", f"【{stages.shown(stage)}（階段 {stage} 的做法，已附上全文，不用再讀）】", section]
    return "\n".join(lines)
