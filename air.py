"""AIR 規範套件的指令入口。只需要 Python 3.11+。

  python3 AIR/air.py check [檔案 ...] [--json]      快速檢查（不給檔案時檢查所有層）
  python3 AIR/air.py verify [--scope batch|final] [--json]
                                             正式驗收：架構檢查＋測試指令；結果綁定這一版程式碼，寫進 .air/verify.json
  python3 AIR/air.py install [--package P --package-dir D]
  python3 AIR/air.py uninstall [--purge]
  python3 AIR/air.py layers [層 ...]         建立分層目錄（預設全部）
  python3 AIR/air.py survey                  盤點現有程式碼（Markdown）
  python3 AIR/air.py status [--json]         套件、遷移階段、檢查範圍、違規數、最近驗收
  python3 AIR/air.py phase <階段> [--rollback|--force] [--reason 原因]
                                             推進遷移階段（一次一格，會檢查前置條件；退回或強制要寫原因）
  python3 AIR/air.py hook <pre-edit|post-edit|pre-bash|stop>
                                             給 hook 使用（從 stdin 讀 JSON；接受 Claude Code 與 Codex 的 payload）
  python3 AIR/air.py manifest                重新產生 AIR/MANIFEST.sha256（維護 AIR 時在開發 repo 用；裝好的專案裡不能用）

結束碼：0 通過；1 沒通過（違規、驗收未通過、階段轉移被拒）；2 操作錯誤（找不到檔案、沒安裝、設定錯、測試指令跑不起來）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if sys.version_info < (3, 11):
    sys.exit("AIR 需要 Python 3.11 以上。")

sys.dont_write_bytecode = True  # 不在使用者專案的 AIR/ 裡產生 __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from airkit import guard, install, integrity, project, protect, verify  # noqa: E402
from airkit.checks import EXIT, check, rel  # noqa: E402
from airkit.config import PROJECT_FILE, ConfigError, load, project_root  # noqa: E402

FIX_HINT = "依訊息中的規則編號讀對應層的 CLAUDE.md 後修正（前綴對照見 AIR/AGENTS.md）。"


def print_report(report, root: Path, show_soft: bool = True) -> None:
    for v in report.hard:
        print(v.format(root))
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


def cmd_install(args) -> int:
    lines = install.install(project_root(), args.package, args.package_dir)
    for line in lines:
        print(f"  {line}")
    failed = [line for line in lines if line.startswith(install.FAILED)]
    if failed:
        print(f"AIR 安裝未完成：{len(failed)} 個接點失敗（見上面「失敗」的項目）。處理後再執行一次 sh AIR/install.sh，已完成的部分不會重做。", file=sys.stderr)
        return 1
    print("AIR 安裝完成。下一步：照 AIR/START.md 進入分析階段。")
    return 0


def cmd_uninstall(args) -> int:
    for line in install.uninstall(project_root(), args.purge):
        print(f"  {line}")
    print("AIR 已移除。" + ("" if args.purge else "專案設定 .air/ 與遷移報告保留；要一併刪除請加 --purge。"))
    return 0


def cmd_layers(args) -> int:
    cfg = load()
    for f in project.create_layers(cfg, args.names or None):
        print(f"  建立 {f}")
    for r in install.refresh_rules(cfg):
        print(f"  規則：{r}")
    return 0


def cmd_survey(args) -> int:
    print(project.survey(load()), end="")
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


# ── hooks（Claude Code、Codex）──

# Codex apply_patch 的檔案標頭；Move to 是改名後的新路徑
PATCH_FILE = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$", re.M)


def edited_files(payload: dict) -> list[Path]:
    """這次工具呼叫改到的檔案。Claude Code：tool_input.file_path；Codex：tool_input.command 是 patch 文字。"""
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        raise ValueError(f"tool_input 不是物件：{type(tool_input).__name__}")
    base = Path(payload.get("cwd") or ".")
    if "file_path" in tool_input:
        names = [tool_input["file_path"]]
    else:
        names = PATCH_FILE.findall(tool_input.get("command", ""))
    return [(base / name.strip()).resolve() for name in names]  # 絕對路徑時 base / name 就是 name


def hook_pre_edit(payload: dict) -> int:
    """編輯前：受保護路徑擋下（exit 2，檔案還沒被改到）；.air/project.toml 改成請使用者確認。"""
    paths = edited_files(payload)
    if not paths:
        return 0
    cfg = load()
    decisions = [(p, protect.classify(p, cfg)) for p in paths]
    denied = [(p, d) for p, d in decisions if d.action == protect.DENY]
    if denied:
        print("【AIR 擋下這次修改，檔案沒有被改到】", file=sys.stderr)
        for p, d in denied:
            print(f"{rel(p, cfg.root)}：{d.reason}", file=sys.stderr)
        return 2
    asks = [(p, d) for p, d in decisions if d.action == protect.ASK]
    if asks:
        reason = "；".join(f"{rel(p, cfg.root)}：{d.reason}" for p, d in asks)
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                                 "permissionDecisionReason": reason}}, ensure_ascii=False))
    return 0


def hook_post_edit(payload: dict) -> int:
    paths = [p for p in edited_files(payload) if p.suffix == ".py"]
    if not paths:
        return 0
    cfg = load()
    report = check(cfg, paths)  # 已刪除的檔案 check() 會略過
    if report.hard:
        print("【這次修改已經寫入檔案，但違反規則，必須修正】", file=sys.stderr)
        for v in report.hard:
            print(v.format(cfg.root), file=sys.stderr)
        print(f"\n{FIX_HINT}", file=sys.stderr)
        return 2
    if report.soft:
        text = "\n".join(v.format(cfg.root) for v in report.soft)
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": f"【僅供參考】\n{text}"}}, ensure_ascii=False))
    return 0


def hook_pre_bash(payload: dict) -> int:
    reason = guard.check(payload.get("tool_input", {}).get("command", ""))
    if reason:
        print(f"{reason}請修正 `python3 AIR/air.py check` 回報的問題後再 commit。", file=sys.stderr)
        return 2
    return 0


def hook_stop(payload: dict) -> int:
    if payload.get("stop_hook_active") or payload.get("stopHookActive"):  # 後者是 Grok 的欄位名
        return 0  # 已經因為 Stop hook 繼續過一次，放行，避免無限迴圈
    cfg = load()
    report = check(cfg)
    if not report.hard:
        return 0
    print("結束前的全專案檢查未通過（可能包含用 Bash 修改、編輯時沒被檢查到的檔案）：", file=sys.stderr)
    for v in report.hard[:40]:
        print(v.format(cfg.root), file=sys.stderr)
    print(f"\n{FIX_HINT}這是最後一次提醒，沒修好的違規會在 commit 時被擋下。", file=sys.stderr)
    return 2


def cmd_hook(args) -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        handlers = {"pre-edit": hook_pre_edit, "post-edit": hook_post_edit, "pre-bash": hook_pre_bash, "stop": hook_stop}
        return handlers[args.event](payload)
    except ConfigError:
        return 0  # 還沒安裝完成（沒有 .air/project.toml）時，hook 不做任何事
    except Exception as e:  # noqa: BLE001 — hook 自己的錯誤不能變成 AI 的程式違規，也不擋住工作；commit 前檢查仍會把關
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
    p = sub.add_parser("install")
    p.add_argument("--package")
    p.add_argument("--package-dir")
    p = sub.add_parser("uninstall")
    p.add_argument("--purge", action="store_true")
    p = sub.add_parser("layers")
    p.add_argument("names", nargs="*")
    sub.add_parser("survey")
    p = sub.add_parser("status")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("phase")
    p.add_argument("phase")
    p.add_argument("--rollback", action="store_true", help="退回較早的階段（要加 --reason）")
    p.add_argument("--force", action="store_true", help="略過前置條件（要加 --reason；會記錄）")
    p.add_argument("--reason", default="")
    p = sub.add_parser("hook")
    p.add_argument("event", choices=["pre-edit", "post-edit", "pre-bash", "stop"])
    sub.add_parser("manifest")
    args = parser.parse_args(argv)
    handlers = {
        "check": cmd_check, "verify": cmd_verify, "install": cmd_install, "uninstall": cmd_uninstall,
        "layers": cmd_layers, "survey": cmd_survey, "status": cmd_status, "phase": cmd_phase, "hook": cmd_hook,
        "manifest": cmd_manifest,
    }
    try:
        return handlers[args.cmd](args)
    except (ConfigError, install.InstallError, ValueError, OSError) as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
