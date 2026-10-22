"""規則檔（AIR/rules/<層>.md）的解析：規則只有這一個來源，程式用它判斷哪些規則有程式檢查、哪些要靠語意審查。

格式（見 docs/規範說明.md「維護規範」）：
  - **<前綴>-<編號>【必須|建議】** <一句指令>。〔hook 檢查〕 | 〔hook 部分檢查：抓不到的情形〕 | （沒有標記）
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .checks import layer_of
from .config import KIT_DIR, Config

RULE_RE = re.compile(r"^- \*\*(?P<id>(?P<prefix>[A-Z]+)-(?P<number>\d+))【(?P<level>必須|建議)】\*\* (?P<body>.+?)\s*$")
MARK_RE = re.compile(r"〔(?P<mark>[^〕]+)〕\s*$")
FULL, PARTIAL, NONE = "full", "partial", "none"  # 程式檢查的涵蓋程度
MAX_PER_FILE = 6  # 規範說明：每層最多 6 條


@dataclass(frozen=True)
class Rule:
    id: str
    prefix: str
    number: int
    level: str        # 必須 | 建議
    text: str         # 規則本文（不含標記）
    mark: str         # 〔〕裡的文字；沒有標記是空字串
    name: str         # 規則檔的名稱：層的名稱或 tests
    source: str       # 相對專案根目錄的路徑（AIR/rules/<name>.md）
    line: int         # 1-based

    @property
    def checked(self) -> str:
        if self.mark == "hook 檢查":
            return FULL
        return PARTIAL if self.mark.startswith("hook 部分檢查") else NONE

    @property
    def gap(self) -> str:
        """〔hook 部分檢查：…〕冒號後面的說明：程式抓不到、要靠審查的部分。"""
        _, sep, rest = self.mark.partition("：")
        return rest.strip() if self.checked == PARTIAL and sep else ""

    def to_dict(self) -> dict:
        return {"id": self.id, "level": self.level, "text": self.text, "checked": self.checked, "gap": self.gap,
                "source": self.source, "line": self.line}


def parse(text: str, name: str, source: str) -> list[Rule]:
    rules = []
    for lineno, line in enumerate(text.splitlines(), 1):
        m = RULE_RE.match(line)
        if not m:
            continue
        body = m.group("body")
        mark = MARK_RE.search(body)
        rules.append(Rule(
            id=m.group("id"), prefix=m.group("prefix"), number=int(m.group("number")), level=m.group("level"),
            text=body[:mark.start()].rstrip() if mark else body.rstrip(), mark=mark.group("mark").strip() if mark else "",
            name=name, source=source, line=lineno,
        ))
    return rules


def load(kit: Path = KIT_DIR) -> dict[str, list[Rule]]:
    """規則檔名稱（層或 tests）→ 規則。"""
    out = {}
    for path in sorted((kit / "rules").glob("*.md")):
        out[path.stem] = parse(path.read_text(encoding="utf-8"), path.stem, f"AIR/rules/{path.name}")
    return out


def by_id(kit: Path = KIT_DIR) -> dict[str, Rule]:
    return {r.id: r for rules in load(kit).values() for r in rules}


def for_review(name: str, kit: Path = KIT_DIR) -> list[Rule]:
    """這個規則檔裡要靠語意審查的規則：沒有程式檢查的，加上只有部分檢查的（審它抓不到的部分）。"""
    return [r for r in load(kit).get(name, []) if r.checked != FULL]


def name_for(path: Path, cfg: Config) -> str | None:
    """這個檔案適用哪一份規則：層的名稱、tests，或 None（不在任何層）。"""
    layer = layer_of(path, cfg)
    if layer:
        return layer.name
    return "tests" if path.resolve().is_relative_to(cfg.tests_dir.resolve()) else None
