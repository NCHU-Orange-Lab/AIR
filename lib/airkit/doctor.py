"""air doctor：診斷接線。Python、設定、套件完整性、指引、每個宿主的 hook、skill 與路徑規則（含真的跑一次的冒煙測試）、
舊版留下的檔案、commit 前檢查、最近的 hook 錯誤。

只讀，不修改任何東西；修法都是明確的指令（多半是 sh AIR/install.sh）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import hooklog, hosts, install, integrity
from .config import Config, ConfigError, load

OK, WARN, FAIL = "ok", "warn", "fail"
MARK = {OK: "[OK]", WARN: "[注意]", FAIL: "[問題]"}
REINSTALL = "執行 sh AIR/install.sh"
SMOKE_TIMEOUT = 30  # 冒煙測試：每個 hook 最多等這麼久（秒）


@dataclass(frozen=True)
class Item:
    level: str
    area: str
    text: str

    def format(self) -> str:
        return f"{MARK[self.level]} {self.area}：{self.text}"


def _python(root: Path) -> Item:
    path = root / ".air" / "python"
    if not path.exists():
        return Item(WARN, "Python", f"這台電腦還沒跑過 install.sh（沒有 .air/python）：hook 會退回用 PATH 上的 python3。{REINSTALL}")
    exe = path.read_text(encoding="utf-8").strip()
    try:
        out = subprocess.run([exe, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        return Item(FAIL, "Python", f".air/python 指向 {exe}，但執行不了（{type(e).__name__}）。{REINSTALL}")
    version = out.stdout.strip()
    if out.returncode != 0 or not re.fullmatch(r"\d+\.\d+\.\d+", version) or tuple(map(int, version.split("."))) < (3, 11):
        return Item(FAIL, "Python", f".air/python 指向 {exe}（{version or '無法取得版本'}），AIR 需要 3.11 以上。{REINSTALL}")
    return Item(OK, "Python", f"{exe}（{version}）")


def _config(root: Path) -> tuple[Item, Config | None]:
    try:
        cfg = load(root)
    except ConfigError as e:
        return Item(FAIL, "設定", str(e)), None
    except ValueError as e:  # TOML 解析失敗
        return Item(FAIL, "設定", f".air/project.toml 無法解析：{e}"), None
    if not cfg.package_dir.is_dir():
        return Item(FAIL, "設定", f"套件目錄 {cfg.package_dir.relative_to(root)} 不存在（.air/project.toml 的 project.package-dir）"), cfg
    return Item(OK, "設定", f".air/project.toml（套件 {cfg.package}，階段 {cfg.phase}）"), cfg


def _integrity(kit: Path) -> Item:
    problems = integrity.manifest_problems(kit)
    if problems:
        shown = "、".join(f"AIR/{p.relative_to(kit)}（{why}）" for p, why in problems[:5])
        more = f"…共 {len(problems)} 個" if len(problems) > 5 else ""
        return Item(FAIL, "套件完整性", f"{shown}{more}。{integrity.FIX}")
    return Item(OK, "套件完整性", f"AIR/ 與 MANIFEST.sha256 一致（{len(integrity.kit_files(kit))} 個檔案）")


def _air_block(text: str) -> str:
    m = re.search(rf"{install.BEGIN}.*?{install.END}", text, re.S)
    return m.group(0) if m else ""


def _instructions(root: Path) -> list[Item]:
    items = []
    claude = install.claude_md_path(root)
    text = claude.read_text(encoding="utf-8") if claude.exists() else ""
    name = claude.relative_to(root).as_posix()
    if install.BEGIN not in text:
        items.append(Item(FAIL, "指引", f"{name} 沒有 AIR 的區塊。{REINSTALL}"))
    else:
        broken = [line for line in _air_block(text).splitlines() if line.startswith("@") and not (claude.parent / line[1:]).exists()]
        if broken:
            items.append(Item(FAIL, "指引", f"{name} 匯入的檔案不存在：{'、'.join(broken)}。{REINSTALL}"))
        else:
            items.append(Item(OK, "指引", f"{name} 有 AIR 的區塊"))
    agents = root / "AGENTS.md"
    text = agents.read_text(encoding="utf-8") if agents.exists() else ""
    if install.BEGIN not in text:
        items.append(Item(FAIL, "指引", f"AGENTS.md 沒有 AIR 的區塊。{REINSTALL}"))
    elif install.agents_body() not in text:
        items.append(Item(WARN, "指引", f"AGENTS.md 的 AIR 區塊不是目前版本的內容（更新 AIR 後還沒重新安裝）。{REINSTALL}"))
    else:
        items.append(Item(OK, "指引", "AGENTS.md 含常駐規則全文"))
    if (root / "AGENTS.override.md").exists():
        items.append(Item(WARN, "指引", "根目錄有 AGENTS.override.md：Codex 只讀它，不會讀 AGENTS.md 的 AIR 區塊。把區塊搬進去，或移除 override"))
    return items


def _host(root: Path, host: hosts.Host, expected: bool, cfg: Config | None) -> list[Item]:
    kit = root / "AIR"
    hooks_file = root / host.hooks_file
    if not hooks_file.exists():
        if expected:
            return [Item(FAIL, host.label, f"這台電腦裝過 {host.label} 的接點，但 {host.hooks_file.as_posix()} 不見了。{REINSTALL}")]
        return [Item(WARN, host.label, f"未安裝（沒有 {host.hooks_file.as_posix()}）。要在 {host.label} 使用 AIR：sh AIR/install.sh --host {host.name}")]
    try:
        data = install.read_hooks(hooks_file)
    except ValueError as e:
        return [Item(FAIL, host.label, f"{host.hooks_file.as_posix()} 無法解析（{e}）。修正後{REINSTALL}")]
    items = []
    missing = install.missing_hooks(data, host)
    if missing:
        items.append(Item(FAIL, host.label, f"{host.hooks_file.as_posix()} 缺少 AIR 的 hook：{'、'.join(missing)}。{REINSTALL}"))
    else:
        items.append(Item(OK, host.label, f"{host.hooks_file.as_posix()} 有 {len(host.scripts)} 個 AIR hook"))
    present = {script for _, _, script in install.air_hooks(data)}
    retired = sorted(present & set(hosts.LEGACY_SCRIPTS))  # 舊版裝的、這一版已經不用的 hook：一個注意就好，重新安裝會移除
    if retired:
        items.append(Item(WARN, host.label, f"{host.hooks_file.as_posix()} 還有舊版的 AIR hook（{'、'.join(retired)}；這一版不再使用）。{REINSTALL} 會移除"))
    stale = [s for s in install.stale_hooks(data, host) if s not in hosts.LEGACY_SCRIPTS]
    if stale:
        items.append(Item(WARN, host.label, f"{host.hooks_file.as_posix()} 有舊版或別的宿主格式的 AIR hook（{'、'.join(stale)}）。{REINSTALL}"))
    for script in present - set(hosts.LEGACY_SCRIPTS):
        if not (kit / "hooks" / script).exists():
            items.append(Item(FAIL, host.label, f"{host.hooks_file.as_posix()} 指向不存在的腳本 AIR/hooks/{script}。{REINSTALL}"))
    missing_skills, foreign, stale_skills = [], [], []
    for name in install.skill_names():
        dest = root / host.skills_dir / name
        if not dest.exists():
            missing_skills.append(name)
        elif not (dest / install.SKILL_MARK).exists():
            foreign.append(name)
        elif install.skill_differs(kit / "skills" / name, dest):
            stale_skills.append(name)
    if missing_skills:
        items.append(Item(WARN, host.label, f"{host.skills_dir.as_posix()}/ 缺少 skill：{'、'.join(missing_skills)}。{REINSTALL}"))
    if foreign:
        items.append(Item(WARN, host.label, f"{host.skills_dir.as_posix()}/ 裡的 {'、'.join(foreign)} 不是 AIR 建立的，AIR 的版本沒有安裝"))
    if stale_skills:
        items.append(Item(WARN, host.label, f"{host.skills_dir.as_posix()}/ 的 {'、'.join(stale_skills)} 不是目前版本。{REINSTALL}"))
    if not (missing_skills or foreign or stale_skills):
        items.append(Item(OK, host.label, f"{host.skills_dir.as_posix()}/ 有 {len(install.skill_names())} 個 skill"))
    if host.rules_dir and cfg is not None:
        d = host.rules_dir.as_posix()
        missing_rules, foreign_rules, stale_rules = install.path_rules_status(root, host, cfg)
        if missing_rules:
            items.append(Item(FAIL, host.label, f"{d}/ 缺少各層的規則檔：{'、'.join(missing_rules)}（{host.label} 從這裡載入規則）。{REINSTALL}"))
        if foreign_rules:
            items.append(Item(WARN, host.label, f"{d}/ 裡的 {'、'.join(foreign_rules)} 不是 AIR 建立的，AIR 的版本沒有安裝"))
        if stale_rules:
            items.append(Item(WARN, host.label, f"{d}/ 的 {'、'.join(stale_rules)} 不是目前版本（更新 AIR 或改了套件位置後還沒重新安裝）。{REINSTALL}"))
        if not (missing_rules or foreign_rules or stale_rules):
            items.append(Item(OK, host.label, f"{d}/ 有 {len(install.rule_names())} 份各層規則（讀寫該層的檔案時由 {host.label} 自動載入）"))
    if host.note:
        items.append(Item(WARN, host.label, f"{host.note}（AIR 無法從外部確認是否已信任）"))
    return items


def _stale_rules(root: Path, cfg: Config | None) -> list[Item]:
    if cfg is None:
        return []
    stale = []
    for directory in [la.path for la in cfg.layers.values()] + [cfg.tests_dir]:
        dest = directory / "CLAUDE.md"
        if dest.exists() and install.MANAGED in dest.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]:
            stale.append(dest.relative_to(root).as_posix())
    if not stale:
        return []
    return [Item(WARN, "規則", f"舊版留下的規則檔 {'、'.join(stale)}（規則改由 hook 注入）。{REINSTALL} 會移除")]


def _smoke(root: Path, host: hosts.Host) -> list[Item]:
    """真的把每個 hook 跑一次：用設定檔裡同一條指令、送最小的 payload。只看「設定在不在」抓不到「跑不起來」
    （Windows 找不到 Python 時 exit 49、cp950 讓輸出不是 UTF-8），而宿主對這些失敗一律放行、不告訴任何人。
    合格：結束碼是 0 或 2、stdout 若有內容就是合法的 UTF-8 JSON。"""
    try:
        data = install.read_hooks(root / host.hooks_file)
    except (OSError, ValueError):
        return []
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root)}
    items, ran = [], []
    for _event, command, script in install.air_hooks(data):
        if script in hosts.LEGACY_SCRIPTS:
            continue  # 退休的 hook 腳本已經不在套件裡，上面已經提醒重新安裝
        start = time.monotonic()
        try:
            r = subprocess.run(["sh", "-c", command], cwd=root, input=b"{}", capture_output=True, timeout=SMOKE_TIMEOUT, env=env)
        except FileNotFoundError:
            return [Item(FAIL, host.label, "找不到 sh：hook 要用 POSIX shell 執行（Windows 請安裝 Git for Windows）")]
        except subprocess.TimeoutExpired:
            items.append(Item(FAIL, host.label, f"hook {script} 超過 {SMOKE_TIMEOUT} 秒沒有結束"))
            continue
        ms = int((time.monotonic() - start) * 1000)
        err = r.stderr.decode("utf-8", "replace").strip().splitlines()
        if r.returncode not in (0, 2):
            items.append(Item(FAIL, host.label, f"hook {script} 跑不起來（exit {r.returncode}：{err[-1] if err else '沒有輸出'}）；宿主會當成非阻擋錯誤放行。{REINSTALL}"))
            continue
        if r.stdout.strip():
            try:
                json.loads(r.stdout.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as e:
                items.append(Item(FAIL, host.label, f"hook {script} 的輸出不是合法的 UTF-8 JSON（{type(e).__name__}）：宿主會忽略它"))
                continue
        ran.append(ms)
    if not items:
        items.append(Item(OK, host.label, f"hook 冒煙測試：{len(ran)} 個都能執行（共 {sum(ran)} ms）"))
    return items


def _hook_errors(root: Path) -> list[Item]:
    items = hooklog.recent(root)
    if not items:
        return []
    _, where, reason = items[-1]
    return [Item(WARN, "hook", f"最近 24 小時 hook 失敗 {len(items)} 次（{hooklog.LOG_FILE.as_posix()}）；最近一次 {where}：{reason}")]


def _commit_check(root: Path) -> Item:
    status = install.commit_check_status(root)
    if status == "不是 git repo":
        return Item(WARN, "commit 前檢查", "不是 git repo，沒有 commit 前檢查")
    if status.startswith("未安裝"):
        return Item(FAIL, "commit 前檢查", status)
    if status.startswith("pre-commit"):
        hook = install.git_hook_path(root)
        if not hook.exists() or "pre-commit" not in hook.read_text(encoding="utf-8", errors="replace"):
            return Item(WARN, "commit 前檢查", "已加進 .pre-commit-config.yaml，但 git hook 不是 pre-commit 裝的：執行 pre-commit install")
    return Item(OK, "commit 前檢查", status)


def diagnose(root: Path) -> list[Item]:
    """診斷 root 這個專案（它的 AIR/ 是 root/AIR；在裝好的專案裡執行時就是 KIT_DIR）。"""
    config_item, cfg = _config(root)
    items = [_python(root), config_item, _integrity(root / "AIR"), *_instructions(root)]
    installed = install.installed_hosts(root) or []
    for host in hosts.HOSTS.values():
        items += _host(root, host, host in installed, cfg)
        if host in installed and (root / host.hooks_file).exists():
            items += _smoke(root, host)
    items += _stale_rules(root, cfg)
    items.append(_commit_check(root))
    items += _hook_errors(root)
    return items


def counts(items: list[Item]) -> tuple[int, int]:
    return sum(i.level == FAIL for i in items), sum(i.level == WARN for i in items)


def format_report(root: Path, items: list[Item]) -> str:
    fails, warns = counts(items)
    verdict = "接線正常。" if not (fails or warns) else f"{fails} 個問題、{warns} 個注意。"
    return "\n".join([f"AIR doctor（{root}）", *(i.format() for i in items), f"結論：{verdict}"])


def to_dict(root: Path, items: list[Item]) -> dict:
    fails, warns = counts(items)
    return {"schema": "air.doctor.v1", "root": str(root), "ok": fails == 0, "problems": fails, "warnings": warns,
            "items": [asdict(i) for i in items]}


def exit_code(items: list[Item]) -> int:
    return 1 if counts(items)[0] else 0
