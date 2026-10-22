"""規則檔的解析（rules.py）：規則只有 AIR/rules/*.md 一個來源，程式用它決定哪些規則有程式檢查、哪些要靠語意審查。

這裡的數字就是現行規則的清單：改規則檔時要一起改，標記誇大（把沒檢查的寫成〔hook 檢查〕）會讓那條規則從審查消失。
"""

import tomllib
import unittest

from tests.helpers import KIT, ProjectTestCase
from airkit import rules

FULLY_CHECKED = {"CTR-1", "CTR-3", "STEP-1", "FLOW-1", "FLOW-5", "ADP-6", "IO-4"}
PARTIALLY_CHECKED = {"STEP-2", "STEP-3", "FLOW-2"}
COUNTS = {"contracts": 5, "steps": 6, "flows": 5, "adapters": 6, "storage": 4, "main": 4, "tests": 5}


class TestCurrentRules(unittest.TestCase):
    def setUp(self):
        self.by_file = rules.load()
        self.all = [r for rs in self.by_file.values() for r in rs]

    def test_counts_and_coverage(self):
        self.assertEqual({name: len(rs) for name, rs in self.by_file.items()}, COUNTS)
        self.assertEqual(len(self.all), 35)
        self.assertEqual({r.id for r in self.all if r.checked == rules.FULL}, FULLY_CHECKED)
        self.assertEqual({r.id for r in self.all if r.checked == rules.PARTIAL}, PARTIALLY_CHECKED)
        self.assertEqual(sum(r.checked == rules.NONE for r in self.all), 25)
        self.assertEqual(sum(len(rules.for_review(name)) for name in COUNTS), 28)  # 25 條沒檢查＋3 條部分檢查

    def test_ids_unique_and_numbered_from_one(self):
        ids = [r.id for r in self.all]
        self.assertEqual(len(ids), len(set(ids)))
        for name, rs in self.by_file.items():
            self.assertEqual([r.number for r in rs], list(range(1, len(rs) + 1)), name)
            self.assertEqual(len({r.prefix for r in rs}), 1, name)
            self.assertLessEqual(len(rs), rules.MAX_PER_FILE, name)

    def test_prefix_matches_config(self):
        kit = tomllib.loads((KIT / "config.toml").read_text(encoding="utf-8"))
        for name, spec in kit["layers"].items():
            self.assertEqual(self.by_file[name][0].prefix, spec["rule"].split("-")[0], name)
        self.assertEqual(self.by_file["tests"][0].prefix, "TEST")

    def test_source_line_points_at_the_rule(self):
        for r in self.all:
            text = (KIT / "rules" / f"{r.name}.md").read_text(encoding="utf-8").splitlines()
            self.assertIn(f"**{r.id}【{r.level}】**", text[r.line - 1])
            self.assertEqual(r.source, f"AIR/rules/{r.name}.md")

    def test_partial_marks_describe_the_gap(self):
        by_id = rules.by_id()
        self.assertIn("型別標註", by_id["STEP-2"].gap)
        self.assertIn("取時間、取亂數", by_id["STEP-3"].gap)
        self.assertEqual(by_id["FLOW-2"].gap, "")  # 只有〔hook 部分檢查〕、沒寫說明
        self.assertEqual(by_id["CTR-1"].gap, "")
        self.assertNotIn("〔", by_id["STEP-2"].text)

    def test_levels(self):
        by_id = rules.by_id()
        self.assertEqual((by_id["FLOW-3"].level, by_id["FLOW-4"].level), ("必須", "建議"))


class TestParse(unittest.TestCase):
    def test_parses_marks_and_ignores_other_lines(self):
        text = "# x\n\n- **ZZ-1【必須】** 不做 A。〔hook 檢查〕\n- **ZZ-2【建議】** 做 B。\n- **ZZ-3【必須】** 不做 C。〔hook 部分檢查：D 抓不到〕\n- 不是規則的項目\n**ZZ-4【必須】** 少了破折號\n"
        got = rules.parse(text, "zz", "AIR/rules/zz.md")
        self.assertEqual([(r.id, r.level, r.text, r.checked, r.gap, r.line) for r in got], [
            ("ZZ-1", "必須", "不做 A。", rules.FULL, "", 3),
            ("ZZ-2", "建議", "做 B。", rules.NONE, "", 4),
            ("ZZ-3", "必須", "不做 C。", rules.PARTIAL, "D 抓不到", 5),
        ])

    def test_to_dict(self):
        r = rules.parse("- **ZZ-1【必須】** 不做 A。〔hook 部分檢查：B〕\n", "zz", "AIR/rules/zz.md")[0]
        self.assertEqual(r.to_dict(), {"id": "ZZ-1", "level": "必須", "text": "不做 A。", "checked": "partial", "gap": "B",
                                       "source": "AIR/rules/zz.md", "line": 1})


class TestNameFor(ProjectTestCase):
    def test_layer_tests_or_none(self):
        self.p.layer("steps", a="x = 1\n")
        self.p.write("tests/test_a.py", "")
        self.p.write("demo/legacy.py", "")
        self.p.write("README.md", "")
        cfg = self.p.cfg()
        names = {name: rules.name_for(self.p.root / name, cfg)
                 for name in ("demo/steps/a.py", "tests/test_a.py", "demo/legacy.py", "README.md")}
        self.assertEqual(names, {"demo/steps/a.py": "steps", "tests/test_a.py": "tests", "demo/legacy.py": None, "README.md": None})


if __name__ == "__main__":
    unittest.main()
