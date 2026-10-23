"""AIR 規範套件的指令入口。只需要 Python 3.11+。

  python3 AIR/air.py check [檔案 ...] [--json]      快速檢查（不給檔案時檢查所有層）
  python3 AIR/air.py verify [--scope batch|final] [--json]
                                             正式驗收：架構檢查＋測試指令；結果綁定這一版程式碼，寫進 .air/branches/<分支>/verify.json
  python3 AIR/air.py review [--base 分支] [--bundle] [--json]
                                             語意審查的範圍：這次變更改到的層內檔案、各要對照哪些沒有程式檢查的規則（做法見 skill air-review）
                                             --bundle 把每個待審檔案的差異一起印出（新檔案整個列出），一次拿齊
  python3 AIR/air.py review --result <檔案|-> [--base 分支]
                                             交回審查結果（JSON）：驗證後寫進 .air/branches/<分支>/review.json；有 priority 0／1 的發現結束碼 1
  python3 AIR/air.py install [--package P --package-dir D] [--host claude|codex ...]
                                             安裝接點（不指定 --host 時裝所有支援的宿主；最後列出要 commit 的路徑，工作樹不用先清乾淨）
  python3 AIR/air.py uninstall [--purge]
  python3 AIR/air.py doctor [--json]         診斷接線：Python、設定、套件完整性、指引、每個宿主的 hook 與 skill、commit 前檢查
  python3 AIR/air.py layers [層 ...]         建立分層目錄（預設全部）
  python3 AIR/air.py survey [--out 檔案]     盤點現有程式碼（Markdown；--out 寫進檔案，畫面只印摘要）
  python3 AIR/air.py status [--json]         套件、遷移階段、檢查範圍、違規數、最近驗收
  python3 AIR/air.py phase <階段> [--rollback|--force] [--reason 原因]
                                             推進遷移階段（一次一格，會檢查前置條件；退回或強制要寫原因）
  python3 AIR/air.py pause --reason 原因      暫停遷移（只由使用者要求時執行）：階段保留，分析／計畫階段的 .py 凍結解除，
                                             session 開始改附「暫停中」；架構檢查照常。python3 AIR/air.py resume 繼續
  python3 AIR/air.py hook <session-start|pre-edit|pre-bash|post-edit|stop> [--host claude|codex]
                                             給 hook 使用（從 stdin 讀 JSON；宿主的差異見 lib/airkit/hosts.py）
  python3 AIR/air.py manifest                重新產生 AIR/MANIFEST.sha256（維護 AIR 時在開發 repo 用；裝好的專案裡不能用）

結束碼：0 通過；1 沒通過（違規、驗收未通過、階段轉移被拒、doctor 有問題、審查有待處理的發現）；
        2 操作錯誤（找不到檔案、沒安裝、設定錯、測試指令跑不起來、審查結果無效）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if sys.version_info < (3, 11):
    sys.exit("AIR 需要 Python 3.11 以上。")

sys.dont_write_bytecode = True  # 不在使用者專案的 AIR/ 裡產生 __pycache__
# 輸出入一律 UTF-8，不看系統語系：hook 的 payload、回應與檢查訊息都含中文。Windows 的 cp950 會把規則變亂碼，
# 還會讓含中文的 payload 解析失敗而放行（guard 時有時無）。errors="replace" 讓壞掉的位元組不會變成例外。
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from airkit import context, doctor, guard, hooklog, hosts, install, integrity, project, protect, review, usage, verify  # noqa: E402
from airkit.checks import EXIT, check, format_grouped, grouped, integrity_violations, rel  # noqa: E402
from airkit.config import PROJECT_FILE, Config, ConfigError, load, project_root  # noqa: E402

FIX_HINT = "依訊息中的規則編號讀對應層的規則（AIR/rules/<層>.md；前綴對照見 AIR/AGENTS.md）後修正。"
STOP_GROUPS = 10  # 結束前的檢查最多印幾組違規（同一種違規算一組）；其餘寫進 .air/branches/<分支>/last-check.json


def print_report(report, root: Path, show_soft: bool = True) -> None:
    for line in format_grouped(report.hard, root):  # 同一種違規只印一次修法
        print(line)
    if show_soft and report.soft:
        print("── 僅供參考（不影響結果）")
        for v in report.soft:
            print(v.format(root))


def cmd_check(args) -> int:
    cfg = load()
    paths = [Path(p).resolve() for p in args.paths] or None
    report = check(cfg, paths)
    if args.json:
        print(json.dumps(report.to_dict(cfg.root), ensure_ascii=False, indent=2))
    else:
        print_report(report, cfg.root)
        print(report.summary(cfg.root))
    return EXIT[report.verdict]


def cmd_verify(args) -> int:
    receipt = verify.verify(load(), args.scope)
    print(json.dumps(receipt, ensure_ascii=False, indent=2) if args.json else verify.format_receipt(receipt))
    return verify.exit_code(receipt)


def cmd_review(args) -> int:
    """沒有 --result：印出審查範圍（--bundle 連差異一起）。有 --result：驗證交回的結果，通過才記錄（無效＝2，有待處理的發現＝1）。"""
    cfg = load()
    sc = review.scope(cfg, args.base)
    if args.result is None:
        if args.bundle:
            review.bundle(cfg, sc)
        text = json.dumps(sc, ensure_ascii=False, indent=2) if args.json else review.format_scope(sc)
        print(text)
        usage.record(cfg.root, "review-scope", chars=len(text), files=sum(1 for f in sc["files"] if not f["reused"]),
                     reused=sum(1 for f in sc["files"] if f["reused"]), bundle=args.bundle)
        return 0
    if args.bundle:
        print("錯誤：--bundle 只用在印範圍，不能跟 --result 一起用。", file=sys.stderr)
        return 2
    text = sys.stdin.read() if args.result == "-" else Path(args.result).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except ValueError as e:
        print(f"錯誤：審查結果不是 JSON（{e}）。格式見 skill air-review。", file=sys.stderr)
        return 2
    errors, findings = review.validate(sc, data)
    usage.record(cfg.root, "review-result", valid=not errors, findings=len(findings))
    if errors:
        print("審查結果無效，沒有記錄：", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        print("修正後重新交回（格式見 skill air-review；範圍與規則編號用 python3 AIR/air.py review 查）。", file=sys.stderr)
        return 2
    rec = review.record(cfg, sc, data, findings)
    print(json.dumps(rec, ensure_ascii=False, indent=2) if args.json else review.format_result(rec))
    return review.exit_code(rec)


def cmd_install(args) -> int:
    lines = install.install(project_root(), args.package, args.package_dir, args.host)
    for line in lines:
        print(f"  {line}")
    failed = [line for line in lines if line.startswith(install.FAILED)]
    if failed:
        print(f"AIR 安裝未完成：{len(failed)} 個接點失敗（見上面「失敗」的項目）。處理後再執行一次 sh AIR/install.sh，已完成的部分不會重做。", file=sys.stderr)
        return 1
    print("AIR 安裝完成。下一步：照 AIR/START.md 進入分析階段（python3 AIR/air.py doctor 可以隨時檢查接線）。")
    return 0


def cmd_uninstall(args) -> int:
    for line in install.uninstall(project_root(), args.purge):
        print(f"  {line}")
    print("AIR 已移除。" + ("" if args.purge else "專案設定 .air/ 與遷移報告保留；要一併刪除請加 --purge。"))
    return 0


def cmd_doctor(args) -> int:
    root = project_root()
    items = doctor.diagnose(root)
    print(json.dumps(doctor.to_dict(root, items), ensure_ascii=False, indent=2) if args.json else doctor.format_report(root, items))
    return doctor.exit_code(items)


def cmd_layers(args) -> int:
    for f in project.create_layers(load(), args.names or None):
        print(f"  建立 {f}")
    return 0


def cmd_survey(args) -> int:
    cfg = load()
    rows = project.survey_rows(cfg)
    table = project.survey(cfg, rows)
    if args.out:
        out = (cfg.root / args.out).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(table, encoding="utf-8")
        text = project.survey_summary(cfg, rows, out)
    else:
        text = table
    print(text, end="")
    usage.record(cfg.root, "survey", modules=len(rows), chars=len(text), out=args.out)
    return 0


def cmd_status(args) -> int:
    cfg = load()
    print(json.dumps(project.status_data(cfg), ensure_ascii=False, indent=2) if args.json else project.status(cfg))
    return 0


def cmd_manifest(args) -> int:
    if (project_root() / PROJECT_FILE).exists():
        print("錯誤：manifest 是維護 AIR 時用的指令（在 AIR 的開發 repo 執行）。裝好的專案裡不能重新產生，否則完整性檢查就沒有意義。", file=sys.stderr)
        return 2
    n = integrity.write_manifest()
    print(f"已寫入 AIR/MANIFEST.sha256（{n} 個檔案）")
    return 0


def cmd_phase(args) -> int:
    try:
        lines = project.transition(load(), args.phase, rollback=args.rollback, force=args.force, reason=args.reason)
    except project.TransitionError as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 1
    for line in lines:
        print(f"  {line}")
    print(f"遷移階段：{args.phase}")
    return 0


def cmd_pause(args) -> int:
    cfg = load()
    try:
        entry = project.pause(cfg, args.reason)
    except project.TransitionError as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 1
    print(f"遷移已暫停（階段 {entry['phase']} 保留；原因：{entry['reason']}）。暫停期間照一般開發：分析／計畫階段的 .py 凍結解除，"
          "新程式仍寫進對應的層，架構檢查照常；session 開始改附「暫停中」。要繼續遷移：python3 AIR/air.py resume")
    return 0


def cmd_resume(args) -> int:
    cfg = load()
    try:
        entry = project.resume(cfg)
    except project.TransitionError as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 1
    print(f"遷移已繼續（階段 {cfg.phase}；暫停了 {project.describe_pause(entry)}）。下一步照 python3 AIR/air.py status 的指示。")
    return 0


# ── hooks ──
# payload 由 hosts.parse() 正規化；回應用 exit code（2＝擋下／駁回／要求繼續，stderr 是理由）或 hookSpecificOutput 的 JSON。

def hook_output(event_name: str, **fields) -> None:
    # ensure_ascii=True：回應只含 ASCII，跟宿主用什麼編碼讀都無關（\uXXXX 由宿主的 JSON 解析還原）
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event_name, **fields}}, ensure_ascii=True))


def hook_session_start(event: hosts.Event, cfg: Config) -> int:
    """session 開始、壓縮或恢復後：清掉這個 session 的注入紀錄（Codex 下次碰到某層會重新附規則），第一次開始時拍下各層 .py 的快照
    （結束前的檢查用），附上目前狀態。"""
    context.reset(cfg.root, event.session_id)
    context.take_snapshot(cfg, event.session_id)
    hook_output("SessionStart", additionalContext=context.session_context(cfg, event.host))
    return 0


def hook_pre_edit(event: hosts.Event, cfg: Config) -> int:
    """編輯前：受保護路徑擋下（exit 2，檔案還沒被改到）；.air/project.toml 改成請使用者確認；
    沒有路徑規則的宿主（Codex）第一次改某一層時附上規則（Claude Code 由 .claude/rules 自己載入，不重複附）。"""
    if not event.files:
        return 0
    decisions = [(p, protect.classify(p, cfg)) for p in event.files]
    denied = [(p, d) for p, d in decisions if d.action == protect.DENY]
    if denied:
        print("【AIR 擋下這次修改，檔案沒有被改到】", file=sys.stderr)
        for p, d in denied:
            print(f"{rel(p, cfg.root)}：{d.reason}", file=sys.stderr)
        return 2
    fields: dict = {}
    asks = [(p, d) for p, d in decisions if d.action == protect.ASK]
    if asks:
        reason = "；".join(f"{rel(p, cfg.root)}：{d.reason}" for p, d in asks)
        if event.host.supports_ask:
            fields.update(permissionDecision="ask", permissionDecisionReason=reason)
        else:  # Codex 的 PreToolUse 不支援 ask（會被當成無效輸出）：改成提醒，同意與否仍由使用者的訊息決定
            fields["additionalContext"] = (f"【AIR：需要使用者同意】{reason} {event.host.label} 沒有確認機制："
                                           "使用者還沒同意的話，先停下來問；同意的原話記進遷移報告的「調整紀錄」。")
    rules = "" if event.host.rules_dir else context.rules_to_inject(cfg, event.session_id, event.files)
    if rules:
        fields["additionalContext"] = "\n\n".join(filter(None, [fields.get("additionalContext"), rules]))
    if fields:
        hook_output("PreToolUse", **fields)
    return 0


def hook_post_edit(event: hosts.Event, cfg: Config) -> int:
    context.remember_touched(cfg.root, event.session_id, event.files)  # 結束前的檢查用：這個 session 動過哪些檔案
    paths = [p for p in event.files if p.suffix == ".py"]
    if not paths:
        return 0
    report = check(cfg, paths)  # 已刪除的檔案 check() 會略過
    if report.hard:
        usage.record(cfg.root, "post-edit-blocked", files=len(paths), violations=len(report.hard))
        print("【這次修改已經寫入檔案，但違反規則，必須修正】", file=sys.stderr)
        for v in report.hard:
            print(v.format(cfg.root), file=sys.stderr)
        print(f"\n{FIX_HINT}", file=sys.stderr)
        return 2
    if report.soft:
        text = "\n".join(v.format(cfg.root) for v in report.soft)
        hook_output("PostToolUse", additionalContext=f"【僅供參考】\n{text}")
    return 0


def hook_pre_bash(event: hosts.Event, cfg: Config) -> int:
    reason = guard.check(event.command)
    if reason:
        print(f"{reason}請修正 `python3 AIR/air.py check` 回報的問題後再 commit。", file=sys.stderr)
        return 2
    return 0


def hook_stop(event: hosts.Event, cfg: Config) -> int:
    """結束前的檢查。預設（hooks.stop-scope = session）只看這個 session 動過的檔案：編輯工具記下的，加上 session 開始後內容變了的
    層內 .py（Bash 改的靠快照抓）；別的 session 的半成品不會要這個 session 去修。AIR009 不分 session。stop-scope = all 則檢查所有層。"""
    if event.stop_active:
        return 0  # 已經因為 Stop hook 繼續過一次，放行，避免無限迴圈
    if cfg.stop_scope == "all":
        report, scope_note = check(cfg), "所有層（hooks.stop-scope = all）"
    else:
        edited, changed, has_snapshot = context.stop_targets(cfg, event.session_id)
        report = check(cfg, sorted({*edited, *changed}))
        report.hard += integrity_violations(cfg.root)
        scope_note = (f"這個 session 動過的 {len(edited)} 個檔案＋開始後內容變了的 {len(changed)} 個層內檔案"
                      + ("" if has_snapshot else "；沒有 session 開始時的快照，只看編輯工具記下的"))
    if not report.hard:
        return 0
    groups = grouped(report.hard)
    last_check = verify.records_dir(cfg.root) / "last-check.json"
    verify.write_json(last_check, report.to_dict(cfg.root))  # 完整清單在檔案裡，畫面只印前 10 組
    usage.record(cfg.root, "stop-blocked", violations=len(report.hard), groups=len(groups), scope=cfg.stop_scope)
    print(f"結束前的檢查未通過（範圍：{scope_note}；其他違規由 commit 前檢查把關，python3 AIR/air.py check 可以先看）：", file=sys.stderr)
    for line in format_grouped(report.hard, cfg.root, limit=STOP_GROUPS):
        print(line, file=sys.stderr)
    if len(groups) > STOP_GROUPS:
        print(f"…另有 {len(groups) - STOP_GROUPS} 組（共 {len(report.hard)} 個違規），完整清單在 {rel(last_check, cfg.root)}", file=sys.stderr)
    print(f"\n{FIX_HINT}這是最後一次提醒，沒修好的違規會在 commit 時被擋下。", file=sys.stderr)
    return 2


HOOK_HANDLERS = {
    "session-start": hook_session_start, "pre-edit": hook_pre_edit,
    "pre-bash": hook_pre_bash, "post-edit": hook_post_edit, "stop": hook_stop,
}


def cmd_hook(args) -> int:
    try:
        event = hosts.parse(json.loads(sys.stdin.read() or "{}"), args.host)
        return HOOK_HANDLERS[args.event](event, load())
    except ConfigError:
        return 0  # 還沒安裝完成（沒有 .air/project.toml）時，hook 不做任何事
    except Exception as e:  # noqa: BLE001 — hook 自己的錯誤不能變成 AI 的程式違規，也不擋住工作；commit 前檢查仍會把關
        hooklog.log(project_root(), f"hook {args.event}", f"{type(e).__name__}：{e}")  # fail-open 但不能靜默：status、doctor 會顯示
        print(f"AIR hook 內部錯誤（{type(e).__name__}：{e}）。這不是你的程式違規；commit 前檢查仍會把關。", file=sys.stderr)
        return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="air.py", description="AIR 規範套件")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check")
    p.add_argument("paths", nargs="*")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("verify")
    p.add_argument("--scope", choices=verify.SCOPES, default="batch")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("review")
    p.add_argument("--base", help="審整個分支：相對這個分支（或 commit）的 merge-base；預設相對 HEAD")
    p.add_argument("--result", help="交回審查結果的 JSON 檔案；- 表示從 stdin 讀")
    p.add_argument("--bundle", action="store_true", help="把每個待審檔案的差異一起印出（新檔案整個列出），一次拿齊，不用再逐檔 git diff")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("install")
    p.add_argument("--package")
    p.add_argument("--package-dir")
    p.add_argument("--host", action="append", choices=list(hosts.HOSTS), help="只裝這些宿主的接點（可重複；預設全部）")
    p = sub.add_parser("uninstall")
    p.add_argument("--purge", action="store_true")
    p = sub.add_parser("doctor")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("layers")
    p.add_argument("names", nargs="*")
    p = sub.add_parser("survey")
    p.add_argument("--out", help="把完整的盤點表寫進這個檔案（相對專案根目錄），畫面只印摘要")
    p = sub.add_parser("status")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("phase")
    p.add_argument("phase")
    p.add_argument("--rollback", action="store_true", help="退回較早的階段（要加 --reason）")
    p.add_argument("--force", action="store_true", help="略過前置條件（要加 --reason；會記錄）")
    p.add_argument("--reason", default="")
    p = sub.add_parser("pause")
    p.add_argument("--reason", default="", help="為什麼暫停（之後每個 session 開始都會附上；必填）")
    sub.add_parser("resume")
    p = sub.add_parser("hook")
    p.add_argument("event", choices=list(HOOK_HANDLERS))
    p.add_argument("--host", choices=list(hosts.HOSTS), default=hosts.DEFAULT)
    sub.add_parser("manifest")
    args = parser.parse_args(argv)
    handlers = {
        "check": cmd_check, "verify": cmd_verify, "review": cmd_review, "install": cmd_install, "uninstall": cmd_uninstall, "doctor": cmd_doctor,
        "layers": cmd_layers, "survey": cmd_survey, "status": cmd_status, "phase": cmd_phase, "pause": cmd_pause, "resume": cmd_resume,
        "hook": cmd_hook, "manifest": cmd_manifest,
    }
    try:
        return handlers[args.cmd](args)
    except (ConfigError, install.InstallError, ValueError, OSError) as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
