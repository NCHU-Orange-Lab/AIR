"""讀取設定：套件預設值（AIR/config.toml）＋專案設定（.air/project.toml）。

套件預設值會隨著更新整包換掉；專案設定屬於專案，更新時不會被覆蓋。

專案設定（schema v3，2.7 起）：
  [project]    package、package-dir、tests-dir（舊鍵 test-command、test-timeout 仍可用，等同 [tests] 的 command、timeout）
  [imports]    extra：純的層額外允許的外部模組
  [migration]  phase、allow-legacy、exclude（不歸 AIR 管的路徑：不凍結、不進驗收指紋、不列進 survey 與層外清單；層內的檔案不受影響）
  [tests]      command、timeout（每批 verify 跑的測試指令）；results（指令寫出的 JUnit XML，有它才算得出「新的失敗」）；
               baseline（既有失敗的清單，預設 .air/test-baseline.json，由 verify --record-baseline 產生，進版本控制）
  [tests.final] 收尾（verify --scope final）跑的套件：command、timeout、results；沒設就用 [tests]
  [[tests.golden]] 黃金重播：name、command、outputs（指令寫出的檔案）、timeout、scope（batch／final，預設兩者）；
               golden record 記下輸出的 sha256 到 .air/golden.json（進版本控制），verify 重跑比對
  [verify]     fingerprint-extra：驗收指紋除了套件、tests、設定之外還要看的路徑
  [hooks]      stop-scope：session（預設；結束前只查這個 session 動過的檔案）或 all（整個專案）
exclude 與 fingerprint-extra 都是「相對專案根目錄的目錄或檔案」，可以寫成 dir/**（意思一樣），不支援其他萬用字元。
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

KIT_DIR = Path(__file__).resolve().parents[2]  # AIR/
PROJECT_FILE = Path(".air") / "project.toml"
REPORT_FILE = Path("docs") / "AIR-遷移報告.md"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Layer:
    name: str
    path: Path
    module: str  # 完整模組名稱，例如 shop.steps
    pure: bool
    internal: tuple[str, ...]  # 可以 import 的層（可指定到模組，例如 contracts.types）
    rule: str
    check_cycles: bool = False
    cycle_rule: str = ""


@dataclass(frozen=True)
class Allowance:
    layer: str
    module: str
    reason: str


@dataclass(frozen=True)
class Suite:
    name: str          # batch | final
    command: str
    timeout: int
    results: str       # JUnit XML 的相對路徑；空字串＝只看結束碼


@dataclass(frozen=True)
class Golden:
    name: str
    command: str
    outputs: tuple[str, ...]  # 指令寫出的檔案（相對根目錄）
    timeout: int
    scope: tuple[str, ...]    # batch、final


VERIFY_SCOPES = ("batch", "final")
DEFAULT_BASELINE = ".air/test-baseline.json"


@dataclass(frozen=True)
class Config:
    root: Path
    package: str
    package_dir: Path
    tests_dir: Path
    layers: dict[str, Layer]
    common_imports: tuple[str, ...]
    hints: dict[str, str]
    banned_builtins: dict[str, str]
    banned_methods: tuple[str, ...]
    banned_methods_msg: str
    required_args: dict[str, dict]
    banned_calls: dict[str, str]
    max_statements: int
    max_complexity: int
    allow_legacy: tuple[Allowance, ...]
    phase: str
    test_command: str
    test_timeout: int  # 秒；verify 執行測試指令的上限
    exclude: tuple[str, ...] = ()           # migration.exclude：相對根目錄的 posix 路徑，沒有結尾斜線
    fingerprint_extra: tuple[str, ...] = () # verify.fingerprint-extra：同上
    stop_scope: str = "session"             # hooks.stop-scope：session | all
    old_test_keys: tuple[str, ...] = ()     # 還在用 project.test-command／test-timeout（doctor 提醒搬到 [tests]）
    test_results: str = ""                  # tests.results
    test_baseline: str = DEFAULT_BASELINE   # tests.baseline
    final: Suite | None = None              # tests.final
    golden: tuple[Golden, ...] = ()         # tests.golden

    def suite(self, scope: str) -> Suite:
        """這個 scope 要跑的測試套件：final 有另外設就用它，否則都是 [tests]。"""
        if scope == "final" and self.final is not None:
            return self.final
        return Suite("batch", self.test_command, self.test_timeout, self.test_results)

    def is_test_file(self, path: Path) -> bool:
        """這個檔案在 tests-dir 底下。測試放在套件裡面（例如 pelican/tests）時，它們不是待遷移的舊模組：
        不列進舊程式碼的清單、survey 與批次的剩餘數；但仍在驗收指紋與凍結的範圍內（那兩處本來就涵蓋 tests-dir）。"""
        return path.resolve().is_relative_to(self.tests_dir.resolve())

    def is_excluded(self, path: Path) -> bool:
        """這個路徑在 migration.exclude 裡（或底下）。只給層外的檔案用：層內的檔案一律檢查。"""
        try:
            rel = path.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return False
        return any(rel == e or rel.startswith(e + "/") for e in self.exclude)


STOP_SCOPES = ("session", "all")


def _paths(values, field: str) -> tuple[str, ...]:
    """exclude、fingerprint-extra 的清單：目錄或檔案的相對路徑。dir/** 與 dir/ 都當成 dir；不接受絕對路徑、..、萬用字元。"""
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise ConfigError(f".air/project.toml 的 {field} 要是路徑字串的清單。")
    out = []
    for raw in values:
        p = raw.strip().replace("\\", "/")
        p = re.sub(r"/(\*\*|\*)?$", "", p)  # 去掉結尾的 /、/*、/**
        if not p or p.startswith("/") or re.match(r"^[A-Za-z]:", p) or ".." in p.split("/") or any(c in p for c in "*?["):
            raise ConfigError(f".air/project.toml 的 {field} 只能是相對根目錄的目錄或檔案（可以寫成 dir/**），不是：{raw!r}")
        out.append(p.rstrip("/"))
    return tuple(out)


def _relpath(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f".air/project.toml 的 {field} 要是相對根目錄的檔案路徑。")
    return _paths([value], field)[0]


def _final(tests: dict) -> Suite | None:
    final = tests.get("final")
    if final is None:
        return None
    if not isinstance(final, dict) or not isinstance(final.get("command"), str) or not final["command"].strip():
        raise ConfigError(".air/project.toml 的 tests.final 要有 command（收尾跑的測試指令）。")
    results = _relpath(final["results"], "tests.final.results") if "results" in final else ""
    return Suite("final", final["command"], int(final.get("timeout", tests.get("timeout", 600))), results)


def _golden(tests: dict) -> tuple[Golden, ...]:
    items = tests.get("golden", [])
    if not isinstance(items, list):
        raise ConfigError(".air/project.toml 的 tests.golden 要是 [[tests.golden]] 表格清單。")
    out, names = [], set()
    for i, g in enumerate(items, 1):
        where = f"tests.golden[{i}]"
        if not isinstance(g, dict) or not isinstance(g.get("name"), str) or not g["name"].strip():
            raise ConfigError(f".air/project.toml 的 {where} 要有 name。")
        name = g["name"].strip()
        if name in names:
            raise ConfigError(f".air/project.toml 的 tests.golden 名稱重複：{name}")
        names.add(name)
        if not isinstance(g.get("command"), str) or not g["command"].strip():
            raise ConfigError(f".air/project.toml 的 {where}（{name}）要有 command。")
        outputs = _paths(g.get("outputs", []), f"{where}.outputs")
        if not outputs:
            raise ConfigError(f".air/project.toml 的 {where}（{name}）要列 outputs（指令寫出的檔案）。")
        scope = tuple(g.get("scope", list(VERIFY_SCOPES)))
        if not scope or any(s not in VERIFY_SCOPES for s in scope):
            raise ConfigError(f".air/project.toml 的 {where}（{name}）的 scope 只能是 {'、'.join(VERIFY_SCOPES)}。")
        out.append(Golden(name, g["command"], outputs, int(g.get("timeout", 600)), scope))
    return tuple(out)


def build(kit: dict, project: dict, root: Path) -> Config:
    """由兩份設定的內容組出 Config（測試會直接呼叫）。"""
    proj = project.get("project", {})
    package = proj.get("package", "")
    if not package:
        raise ConfigError(".air/project.toml 的 project.package 還沒設定。")
    tests = project.get("tests", {})
    if not isinstance(tests, dict):
        raise ConfigError(".air/project.toml 的 [tests] 要是表格。")
    stop_scope = project.get("hooks", {}).get("stop-scope", "session")
    if stop_scope not in STOP_SCOPES:
        raise ConfigError(f".air/project.toml 的 hooks.stop-scope 只能是 {'、'.join(STOP_SCOPES)}，不是：{stop_scope!r}")
    package_dir = root / proj.get("package-dir", package)
    tests_dir = root / proj.get("tests-dir", "tests")
    if package_dir.resolve().is_relative_to(tests_dir.resolve()):
        raise ConfigError(f".air/project.toml 的 tests-dir（{proj.get('tests-dir', 'tests')}）不能等於或包住 package-dir（{proj.get('package-dir', package)}）："
                          "這樣整個套件都會被當成測試。測試放在套件裡面時，tests-dir 指向那個子目錄（例如 pelican/tests）。")
    layers = {
        name: Layer(
            name=name,
            path=package_dir / name,
            module=f"{package}.{name}",
            pure=spec.get("pure", False),
            internal=tuple(spec.get("internal", [])),
            rule=spec.get("rule", ""),
            check_cycles=spec.get("check-cycles", False),
            cycle_rule=spec.get("cycle-rule", ""),
        )
        for name, spec in kit["layers"].items()
    }
    methods = kit.get("banned-methods", {})
    migration = project.get("migration", {})
    allow = tuple(
        Allowance(a["from"], a["module"], a.get("reason", "")) for a in migration.get("allow-legacy", [])
    )
    return Config(
        root=root,
        package=package,
        package_dir=package_dir,
        tests_dir=tests_dir,
        layers=layers,
        common_imports=tuple(kit["imports"]["common"]) + tuple(project.get("imports", {}).get("extra", [])),
        hints=kit.get("hints", {}),
        banned_builtins=kit.get("banned-builtins", {}),
        banned_methods=tuple(methods.get("names", [])),
        banned_methods_msg=methods.get("msg", ""),
        required_args=kit.get("required-args", {}),
        banned_calls=kit.get("banned-calls", {}),
        max_statements=kit.get("soft", {}).get("max-statements", 40),
        max_complexity=kit.get("soft", {}).get("max-complexity", 10),
        allow_legacy=allow,
        phase=migration.get("phase", "installed"),
        test_command=tests.get("command", proj.get("test-command", "")),      # 兩邊都有時以 [tests] 為準
        test_timeout=int(tests.get("timeout", proj.get("test-timeout", 600))),
        exclude=_paths(migration.get("exclude", []), "migration.exclude"),
        fingerprint_extra=_paths(project.get("verify", {}).get("fingerprint-extra", []), "verify.fingerprint-extra"),
        stop_scope=stop_scope,
        old_test_keys=tuple(k for k in ("test-command", "test-timeout") if k in proj),
        test_results=_relpath(tests["results"], "tests.results") if "results" in tests else "",
        test_baseline=_relpath(tests["baseline"], "tests.baseline") if "baseline" in tests else DEFAULT_BASELINE,
        final=_final(tests),
        golden=_golden(tests),
    )


def read_kit() -> dict:
    return tomllib.loads((KIT_DIR / "config.toml").read_text(encoding="utf-8"))


def project_root() -> Path:
    return KIT_DIR.parent


def load(root: Path | None = None) -> Config:
    root = root or project_root()
    path = root / PROJECT_FILE
    if not path.exists():
        raise ConfigError("找不到 .air/project.toml：AIR 還沒安裝。請執行 AIR/install.sh。")
    return build(read_kit(), tomllib.loads(path.read_text(encoding="utf-8")), root)
