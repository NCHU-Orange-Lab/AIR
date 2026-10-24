"""AIR 規範套件的指令入口。只需要 Python 3.11+。

  python3 AIR/air.py check [檔案 ...] [--json]      快速檢查（不給檔案時檢查所有層）
  python3 AIR/air.py verify [--scope batch|final] [--json]
                                             正式驗收：架構檢查＋測試指令＋黃金重播；結果綁定這一版程式碼，寫進 .air/branches/<分支>/verify.json
                                             [tests] 有設 results（JUnit XML）時依既有失敗基準判斷：失敗減基準為空就通過
  python3 AIR/air.py verify --record-baseline [--scope batch|final]
                                             跑測試，把目前失敗的測試記成基準（.air/test-baseline.json，進版本控制；分析階段做一次）
  python3 AIR/air.py golden record [名稱 ...]  跑黃金重播的指令，把輸出檔的 sha256 記進 .air/golden.json（只由使用者決定何時重錄）
  python3 AIR/air.py batch finish [--note 說明] [--review 檔案|-] [--ack 理由]
                                             結束這一批（commit 之前，一個指令做完）：驗收紀錄新鮮就沿用、否則重跑 verify；有給 --review 就驗證並記錄審查結果；
                                             審查還有沒處理的 P1 時要 --ack；把這批的代理指標記進 .air/history.jsonl，印出 commit 指令（history.jsonl 一起 commit）
  python3 AIR/air.py batch close [--note 說明] [--ack 理由]  舊的做法（commit 之後）：同上但不跑 verify、不收審查結果，要先通過驗收
  python3 AIR/air.py batch list              列出每一批的指標，改版前後可以比較
  python3 AIR/air.py review [--base 分支] [--bundle [--full]] [--json]
                                             語意審查的範圍：這次變更改到的層內檔案、各要對照哪些沒有程式檢查的規則（做法見 skill air-review）
                                             --bundle 把每個待審檔案的差異一起印出（新檔案整個列出；搬移過來的只附差異與大綱，--full 也整個列出），一次拿齊
  python3 AIR/air.py review --result <檔案|-> [--base 分支]
                                             交回審查結果（JSON）：驗證後寫進 .air/branches/<分支>/review.json；有 priority 0／1 的發現結束碼 1
  python3 AIR/air.py install [--package P --package-dir D] [--host claude|codex ...]
                                             安裝接點（不指定 --host 時裝所有支援的宿主；最後列出要 commit 的路徑，工作樹不用先清乾淨）
  python3 AIR/air.py uninstall [--purge]
  python3 AIR/air.py doctor [--json]         診斷接線：Python、設定、套件完整性、指引、每個宿主的 hook 與 skill、commit 前檢查，
                                             以及常駐內容到 80% 以上的警告
  python3 AIR/air.py doctor --context [--json] 常駐內容的完整盤點：各宿主一開場載入的檔案、大小、上限與佔比、AIR 區塊的位置
  python3 AIR/air.py layers [層 ...] [--adopt-module 層]
                                             建立分層目錄（預設全部）；專案已有同名的 <層>.py 會拒絕，--adopt-module 把它搬成 <層>/__init__.py
  python3 AIR/air.py survey [--out 檔案]     盤點現有程式碼（Markdown；--out 寫進檔案，畫面只印摘要）
  python3 AIR/air.py status [--json]         套件、遷移階段與現在該讀的階段檔、檢查範圍、違規數、最近驗收
  python3 AIR/air.py phase <階段> [--rollback|--force] [--reason 原因]
                                             推進遷移階段（一次一格，會檢查前置條件；退回或強制要寫原因）
  python3 AIR/air.py pause --reason 原因      暫停遷移（只由使用者要求時執行）：階段保留，分析／計畫階段的 .py 凍結解除，
                                             session 開始改附「暫停中」；架構檢查照常。python3 AIR/air.py resume 繼續
  python3 AIR/air.py hook <session-start|pre-edit|pre-bash|post-edit|stop> [--host claude|codex]
                                             給 hook 使用（從 stdin 讀 JSON；宿主的差異見 lib/airkit/hosts.py）
  python3 AIR/air.py manifest                重新產生 AIR/MANIFEST.sha256（維護 AIR 時在開發 repo 用；裝好的專案裡不能用）

輸出超過 .air/project.toml 的 [output] max-chars（預設 4,000 字）時，完整內容寫進 .air/out/<指令>.txt，畫面只印開頭（--json 不截）。

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

from airkit import batch, budget, buildcheck, context, doctor, golden, guard, hooklog, hosts, install, integrity, out, project, protect, review, stages, usage, verify  # noqa: E402
from airkit.checks import EXIT, check, format_grouped, grouped, rel, structure_violations  # noqa: E402
from airkit.config import PROJECT_FILE, Config, ConfigError, load, project_root  # noqa: E402

FIX_HINT = "依訊息中的規則編號讀對應層的規則（AIR/rules/<層>.md；前綴對照見 AIR/AGENTS.md）後修正。"
STOP_GROUPS = 10  # 結束前的檢查最多印幾組違規（同一種違規算一組）；其餘寫進 .air/branches/<分支>/last-check.json


def report_lines(report, root: Path, show_soft: bool = True) -> list[str]:
    lines = list(format_grouped(report.hard, root))  # 同一種違規只印一次修法
    if show_soft and report.soft:
        lines.append("── 僅供參考（不影響結果）")
        lines += [v.format(root) for v in report.soft]
    return lines


def cmd_check(args) -> int:
    cfg = load()
    paths = [Path(p).resolve() for p in args.paths] or None
    report = check(cfg, paths)
    if args.json:
        print(json.dumps(report.to_dict(cfg.root), ensure_ascii=False, indent=2))
    else:
        print(out.emit(cfg, "check", "\n".join([*report_lines(report, cfg.root), report.summary(cfg.root)])))
    return EXIT[report.verdict]


def cmd_verify(args) -> int:
    cfg = load()
    if args.record_baseline:
        info = verify.record_baseline(cfg, args.scope)  # 跑不起來會 raise ValueError → 2
        if args.json:
            print(json.dumps(info, ensure_ascii=False, indent=2))
        else:
            print(f"既有失敗基準已寫入 {info['file']}（{info['suite']} 套件：{info['total']} 個測試，失敗 {len(info['failed'])} 個；exit {info['exit']}，{info['duration_ms']} ms）")
            for t in info["failed"][:20]:
                print(f"  - {t}")
            if len(info["failed"]) > 20:
                print(f"  …共 {len(info['failed'])} 個")
            print("之後 verify 只要失敗都在基準裡就算通過；基準裡的測試修好了會列出來，重跑這個指令可以縮小基準。")
        return 0
    receipt = verify.verify(cfg, args.scope)
    print(json.dumps(receipt, ensure_ascii=False, indent=2) if args.json else out.emit(cfg, "verify", verify.format_receipt(receipt)))
    return verify.exit_code(receipt)


def cmd_batch(args) -> int:
    cfg = load()
    if args.action == "list":
        print(out.emit(cfg, "batch-list", batch.format_list(batch.batches(cfg.root))))
        return 0
    if args.action == "finish":
        return _batch_finish(cfg, args)
    if args.review:
        print("錯誤：--review 只用在 batch finish（batch close 之前先用 review --result 交回）。", file=sys.stderr)
        return 2
    try:
        entry, warnings = batch.close(cfg, args.note, args.ack)
    except batch.BatchError as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 1
    print(f"這一批已記進 .air/history.jsonl：{batch.describe(entry)}")
    for w in warnings:
        print(f"  注意：{w}")
    print(FINISH_HINT)
    print("停下來向使用者報告這批改了什麼、測試結果與上面的指標，等核准後才進行下一批。")
    return 0


# 舊的兩步（review --result、commit、batch close）仍然可用；第二輪試跑 Codex 8 次裡只有 1 次用 batch finish，
# 其餘去讀 batch.py 的原始碼找 close 怎麼用。走舊路時提示一次新的一步
FINISH_HINT = ("下一批改用 batch finish：在 commit 之前一個指令做完驗收、交回審查與紀錄"
               """（python3 AIR/air.py batch finish --note "第 N 批：…" --review - <<'EOF' … EOF），不用再分開跑 review --result 與 batch close。""")


def _batch_finish(cfg: Config, args) -> int:
    data = read_json_arg(args.review) if args.review else None
    try:
        entry, warnings, receipt = batch.finish(cfg, args.note, args.ack, data)
    except batch.ReviewInvalid as e:
        print_invalid_review(e.errors)
        print("這一批沒有結束（驗收紀錄已經寫好，修正審查結果後重跑 batch finish 會沿用）。", file=sys.stderr)
        return 2
    except batch.BatchError as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 1
    print(f"驗收：沿用 {verify.describe(cfg, receipt)}" if receipt["reused"] else out.emit(cfg, "verify", verify.format_receipt(receipt)))
    if data is not None:
        print(f"語意審查：{review.describe(cfg, review.load_record(cfg.root))}")
    print(f"這一批已記進 .air/history.jsonl：{batch.describe(entry)}")
    for w in warnings:
        print(f"  注意：{w}")
    print(f"下一步：commit，把 .air/history.jsonl 一起加進去：{batch.commit_hint(cfg, entry)}")
    print("commit 之後停下來向使用者報告這批改了什麼、測試結果與上面的指標，等核准後才進行下一批。")
    return 0


def cmd_golden(args) -> int:
    cfg = load()
    items = golden.record(cfg, args.names or None)  # 失敗會 raise GoldenError（ValueError）→ 2
    print(f"黃金輸出已記錄到 {golden.GOLDEN_FILE.as_posix()}：{len(items)} 筆")
    for name, item in items.items():
        print(f"  {name}：{len(item['outputs'])} 個輸出檔")
    print("之後 verify 會重跑這些指令比對輸出；行為真的要改才重錄，由使用者決定。")
    return 0


def print_invalid_review(errors: list[str]) -> None:
    print("審查結果無效，沒有記錄：", file=sys.stderr)
    for err in errors:
        print(f"  - {err}", file=sys.stderr)
    print("修正後重新交回（格式見 skill air-review；範圍與規則編號用 python3 AIR/air.py review 查）。", file=sys.stderr)


def read_json_arg(value: str):
    """--result／--review 的內容：- 是 stdin，否則是檔案。不是 JSON 時 raise ValueError（訊息給使用者看）。"""
    text = sys.stdin.read() if value == "-" else Path(value).read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except ValueError as e:
        raise ValueError(f"審查結果不是 JSON（{e}）。格式見 skill air-review。") from None


def cmd_review(args) -> int:
    """沒有 --result：印出審查範圍（--bundle 連差異一起）。有 --result：驗證交回的結果，通過才記錄（無效＝2，有待處理的發現＝1）。"""
    cfg = load()
    if args.result is not None:
        return _review_result(cfg, args)
    sc = review.scope(cfg, args.base)
    if args.bundle:
        review.bundle(cfg, sc, full=args.full)
    text = json.dumps(sc, ensure_ascii=False, indent=2) if args.json else out.emit(cfg, "review", review.format_scope(sc, migrating=cfg.phase == "migrating"))
    print(text)
    usage.record(cfg.root, "review-scope", chars=len(text), files=sum(1 for f in sc["files"] if not f["reused"]),
                 reused=sum(1 for f in sc["files"] if f["reused"]), bundle=args.bundle)
    return 0


def _review_result(cfg: Config, args) -> int:
    if args.bundle or args.full:
        print("錯誤：--bundle、--full 只用在印範圍，不能跟 --result 一起用。", file=sys.stderr)
        return 2
    errors, rec = review.submit(cfg, read_json_arg(args.result), args.base)
    if errors:
        print_invalid_review(errors)
        return 2
    print(json.dumps(rec, ensure_ascii=False, indent=2) if args.json else review.format_result(rec))
    if cfg.phase == "migrating" and not args.json:
        print(FINISH_HINT)
    return review.exit_code(rec)


def cmd_install(args) -> int:
    lines = install.install(project_root(), args.package, args.package_dir, args.host)
    for line in lines:
        print(f"  {line}")
    failed = [line for line in lines if line.startswith(install.FAILED)]
    if failed:
        print(f"AIR 安裝未完成：{len(failed)} 個接點失敗（見上面「失敗」的項目）。處理後再執行一次 sh AIR/install.sh，已完成的部分不會重做。", file=sys.stderr)
        return 1
    print(f"AIR 安裝完成。下一步：使用者核准後進入分析階段，讀 {stages.shown(2)}（只讀這一份；python3 AIR/air.py doctor 可以隨時檢查接線）。")
    return 0


def cmd_uninstall(args) -> int:
    for line in install.uninstall(project_root(), args.purge):
        print(f"  {line}")
    print("AIR 已移除。" + ("" if args.purge else "專案設定 .air/ 與遷移報告保留；要一併刪除請加 --purge。"))
    return 0


def cmd_doctor(args) -> int:
    root = project_root()
    if args.context:
        try:
            cfg = load()
        except ConfigError:
            cfg = None  # 沒安裝也能盤點宿主的檔案，只是少了 AIR 自己的注入
        items = doctor.context_report(root, cfg)
        print(json.dumps({"schema": "air.context.v1", "items": [it.to_dict() for it in items]}, ensure_ascii=False, indent=2) if args.json
              else budget.format_table(items))
        return 1 if any(it.level == budget.FAIL for it in items) else 0
    items = doctor.diagnose(root)
    print(json.dumps(doctor.to_dict(root, items), ensure_ascii=False, indent=2) if args.json else doctor.format_report(root, items))
    return doctor.exit_code(items)


def cmd_layers(args) -> int:
    cfg = load()
    try:
        created = project.create_layers(cfg, args.names or None, args.adopt_module)
    except project.LayerCollision as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 1
    for name in args.adopt_module:
        print(f"  搬移 {cfg.package_dir.name}/{name}.py → {cfg.package_dir.name}/{name}/__init__.py（import 路徑不變）")
    for f in created:
        print(f"  建立 {f}")
    for name in args.adopt_module:
        for line in project.adopt_report(cfg, name):
            print(line)
    for p in buildcheck.problems(cfg):
        print(f"  打包：{p.hint()}")
    for note in buildcheck.notes(cfg):
        print(f"  打包：{note}")
    return 0


def cmd_survey(args) -> int:
    cfg = load()
    rows = project.survey_rows(cfg)
    table = project.survey(cfg, rows)
    if args.out:
        dest = (cfg.root / args.out).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(table, encoding="utf-8")
        text = project.survey_summary(cfg, rows, dest)
    else:
        text = out.emit(cfg, "survey", table)
    print(text if text.endswith("\n") else text + "\n", end="")
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
    """轉移成功後直接印出新階段的做法全文：session 中途換階段時 SessionStart 不會再附，只給檔名的話
    Codex 常常不讀（第二輪試跑 16 次裡 4-layers.md、5-migrate.md 幾乎沒讀），改去讀 AIR 的原始碼找做法，反而更貴。"""
    cfg = load()
    try:
        lines = project.transition(cfg, args.phase, rollback=args.rollback, force=args.force, reason=args.reason)
    except project.TransitionError as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 1
    for line in lines:
        print(f"  {line}")
    stage = stages.for_phase(args.phase)
    if not stage:
        print(f"遷移階段：{args.phase}")
    elif stage == stages.for_phase(cfg.phase):  # layered → migrating 都是階段 5：剛才已經印過
        print(f"遷移階段：{args.phase}。做法同上一個階段：{stages.shown(stage)}（已經讀過就不用再讀）")
    else:
        print(f"遷移階段：{args.phase}\n\n【{stages.shown(stage)}（階段 {stage} 的做法，已附上全文，不用再讀）】\n{stages.text(stage)}")
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
    context.mark_seen(cfg, event.session_id)  # Bash 前的比對從這裡開始算；之前就有的半成品不算這個 session 的
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
    context.mark_seen(cfg, event.session_id, event.files)              # 這裡查過了，下一次 Bash 前不再當成 Bash 改的
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
    """執行 Bash 前：擋下略過 commit 前檢查的指令；再看上一個 Bash 改了哪些層內的檔案（AI 常用 Bash 改檔，編輯工具的 hook 不會觸發）：
    有違規就附上（修改已經寫入，不能擋，只能要求修正），第一次碰到的層附上規則。Codex 沒有路徑規則，指令裡提到的層也先附規則；
    Claude Code 讀檔（含 Bash 的 cat）時由 .claude/rules 自己載入，只補「用 Bash 改了」的那一層。不多開程序：沿用這個 hook 本來的那一個。"""
    reason = guard.check(event.command)
    if reason:
        print(f"{reason}請修正 `python3 AIR/air.py check` 回報的問題後再 commit。", file=sys.stderr)
        return 2
    changed = context.bash_changes(cfg, event.session_id)
    texts = []
    report = check(cfg, [p for p in changed if p.suffix == ".py"])
    if report.hard:
        texts.append(f"【AIR：上一個指令改的檔案違反規則（修改已經寫入，必須修正）】\n"
                     + "\n".join(format_grouped(report.hard, cfg.root, limit=STOP_GROUPS)) + f"\n{FIX_HINT}")
    texts.append(context.rules_to_inject(cfg, event.session_id, [p for p in changed if context.has_logic(p)], lead="你剛用指令改了"))
    if not event.host.rules_dir:
        texts.append(context.rules_to_inject(cfg, event.session_id, context.paths_in_command(cfg, event.command), lead="這個指令會動到"))
    texts = [t for t in texts if t]
    if texts:
        hook_output("PreToolUse", additionalContext="\n\n".join(texts))
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
        report.hard += structure_violations(cfg)
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
        cfg = load()
        context.count_fired(cfg.root, event.session_id, args.event)  # status、doctor 看得到 hook 有沒有真的在跑
        return HOOK_HANDLERS[args.event](event, cfg)
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
    p.add_argument("--record-baseline", action="store_true", help="跑測試並把目前失敗的測試記成既有失敗基準（需要 [tests] results）")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("golden")
    p.add_argument("action", choices=["record"])
    p.add_argument("names", nargs="*", help="只記這些（預設全部）")
    p = sub.add_parser("batch")
    p.add_argument("action", choices=["finish", "close", "list"])
    p.add_argument("--note", default="", help="這一批做了什麼（例如「第 3 批：搬 storage」）")
    p.add_argument("--ack", default="", help="審查紀錄裡還有 priority 0／1 的發現卻要結束這一批時，寫明不處理的理由（要先經使用者同意）")
    p.add_argument("--review", help="batch finish：同時交回審查結果的 JSON 檔案；- 表示從 stdin 讀（跟 review --result 一樣驗證）")
    p = sub.add_parser("review")
    p.add_argument("--base", help="審整個分支：相對這個分支（或 commit）的 merge-base；預設相對 HEAD")
    p.add_argument("--result", help="交回審查結果的 JSON 檔案；- 表示從 stdin 讀")
    p.add_argument("--bundle", action="store_true", help="把每個待審檔案的差異一起印出（新檔案整個列出；搬移過來的檔案只附差異與大綱），一次拿齊，不用再逐檔 git diff")
    p.add_argument("--full", action="store_true", help="跟 --bundle 一起用：搬移過來的檔案也整個列出")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("install")
    p.add_argument("--package")
    p.add_argument("--package-dir")
    p.add_argument("--host", action="append", choices=list(hosts.HOSTS), help="只裝這些宿主的接點（可重複；預設全部）")
    p = sub.add_parser("uninstall")
    p.add_argument("--purge", action="store_true")
    p = sub.add_parser("doctor")
    p.add_argument("--context", action="store_true", help="常駐內容的完整盤點（一開場載入的檔案、上限、佔比、AIR 區塊的位置）")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("layers")
    p.add_argument("names", nargs="*")
    p.add_argument("--adopt-module", action="append", default=[], metavar="層",
                   help="專案已經有同名的 <層>.py 時，把它 git mv 成 <層>/__init__.py 當作這一層（目錄只能有空的 __init__.py）；可重複")
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
        "hook": cmd_hook, "manifest": cmd_manifest, "golden": cmd_golden, "batch": cmd_batch,
    }
    try:
        return handlers[args.cmd](args)
    except (ConfigError, install.InstallError, ValueError, OSError) as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
