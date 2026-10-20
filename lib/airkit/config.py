"""讀取設定：套件預設值（AIR/config.toml）＋專案設定（.air/project.toml）。

套件預設值會隨著更新整包換掉；專案設定屬於專案，更新時不會被覆蓋。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

KIT_DIR = Path(__file__).resolve().parents[2]  # AIR/
PROJECT_FILE = Path(".air") / "project.toml"


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


def build(kit: dict, project: dict, root: Path) -> Config:
    """由兩份設定的內容組出 Config（測試會直接呼叫）。"""
    proj = project.get("project", {})
    package = proj.get("package", "")
    if not package:
        raise ConfigError(".air/project.toml 的 project.package 還沒設定。")
    package_dir = root / proj.get("package-dir", package)
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
        tests_dir=root / proj.get("tests-dir", "tests"),
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
        test_command=proj.get("test-command", ""),
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
