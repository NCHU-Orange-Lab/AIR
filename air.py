"""AIR 規範套件的指令入口。只需要 Python 3.11+。

  python3 AIR/air.py check [檔案 ...]       檢查（不給檔案時檢查所有層）；違規時 exit 1
  python3 AIR/air.py install [--package P --package-dir D]
  python3 AIR/air.py uninstall [--purge]
  python3 AIR/air.py layers [層 ...]         建立分層目錄（預設全部）
  python3 AIR/air.py survey                  盤點現有程式碼（Markdown）
  python3 AIR/air.py status                  顯示套件、遷移階段、違規數
  python3 AIR/air.py phase <階段>            設定遷移階段
  python3 AIR/air.py hook <post-edit|pre-bash|stop>   給 Claude Code hook 使用（從 stdin 讀 JSON）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if sys.version_info < (3, 11):
    sys.exit("AIR 需要 Python 3.11 以上。")

sys.dont_write_bytecode = True  # 不在使用者專案的 AIR/ 裡產生 __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from airkit import guard, install, project  # noqa: E402
from airkit.checks import check  # noqa: E402
from airkit.config import ConfigError, load, project_root  # noqa: E402

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
    print_report(report, cfg.root)
    if not report.hard:
        print("AIR 檢查通過")
    return 1 if report.hard else 0


def cmd_install(args) -> int:
    for line in install.install(project_root(), args.package, args.package_dir):
        print(f"  {line}")
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
    print(project.status(load()))
    return 0


def cmd_phase(args) -> int:
    project.set_phase(load(), args.phase)
    print(f"遷移階段：{args.phase}")
    return 0


# ── Claude Code hooks ──

def hook_post_edit(payload: dict) -> int:
    path = Path(payload.get("tool_input", {}).get("file_path", ""))
    if path.suffix != ".py":
        return 0
    cfg = load()
    report = check(cfg, [path])
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
    if payload.get("stop_hook_active"):
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
        return {"post-edit": hook_post_edit, "pre-bash": hook_pre_bash, "stop": hook_stop}[args.event](payload)
    except ConfigError:
        return 0  # 還沒安裝完成（沒有 .air/project.toml）時，hook 不做任何事


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="air.py", description="AIR 規範套件")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check")
    p.add_argument("paths", nargs="*")
    p = sub.add_parser("install")
    p.add_argument("--package")
    p.add_argument("--package-dir")
    p = sub.add_parser("uninstall")
    p.add_argument("--purge", action="store_true")
    p = sub.add_parser("layers")
    p.add_argument("names", nargs="*")
    sub.add_parser("survey")
    sub.add_parser("status")
    p = sub.add_parser("phase")
    p.add_argument("phase")
    p = sub.add_parser("hook")
    p.add_argument("event", choices=["post-edit", "pre-bash", "stop"])
    args = parser.parse_args(argv)
    handlers = {
        "check": cmd_check, "install": cmd_install, "uninstall": cmd_uninstall, "layers": cmd_layers,
        "survey": cmd_survey, "status": cmd_status, "phase": cmd_phase, "hook": cmd_hook,
    }
    try:
        return handlers[args.cmd](args)
    except (ConfigError, install.InstallError, ValueError) as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
