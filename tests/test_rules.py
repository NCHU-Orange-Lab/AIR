"""規則檔的解析（rules.py）：規則只有 AIR/rules/*.md 一個來源，程式用它決定哪些規則有程式檢查、哪些要靠語意審查。

這裡的數字就是現行規則的清單：改規則檔時要一起改，標記誇大（把沒檢查的寫成〔hook 檢查〕）會讓那條規則從審查消失。
"""

import re
import tomllib
import unittest

from tests.helpers import KIT, ProjectTestCase
from airkit import rules

FULLY_CHECKED = {"CTR-1", "CTR-3", "STEP-1", "FLOW-1", "FLOW-5", "ADP-6", "IO-4"}
PARTIALLY_CHECKED = {"STEP-2", "STEP-3", "FLOW-2"}
COUNTS = {"contracts": 5, "steps": 6, "flows": 5, "adapters": 6, "storage": 5, "main": 4, "tests": 5}


class TestCurrentRules(unittest.TestCase):
    def setUp(self):
        self.by_file = rules.load()
        self.all = [r for rs in self.by_file.values() for r in rs]

    def test_counts_and_coverage(self):
        self.assertEqual({name: len(rs) for name, rs in self.by_file.items()}, COUNTS)
        self.assertEqual(len(self.all), 36)
        self.assertEqual({r.id for r in self.all if r.checked == rules.FULL}, FULLY_CHECKED)
        self.assertEqual({r.id for r in self.all if r.checked == rules.PARTIAL}, PARTIALLY_CHECKED)
        self.assertEqual(sum(r.checked == rules.NONE for r in self.all), 26)
        self.assertEqual(sum(len(rules.for_review(name)) for name in COUNTS), 29)  # 26 條沒檢查＋3 條部分檢查

    def test_docs_quote_current_counts(self):
        # 設計說明與 review.py 的 docstring 引用規則的數量：改規則檔時容易忘了同步，所以從規則檔算出來核對
        total = len(self.all)
        full = sum(r.checked == rules.FULL for r in self.all)
        partial = sum(r.checked == rules.PARTIAL for r in self.all)
        none = total - full - partial
        sentence = f"{total} 條規則裡只有 {full + partial} 條有程式檢查（{full} 條完整、{partial} 條部分）"
        design = (KIT / "docs" / "設計說明.md").read_text(encoding="utf-8")
        self.assertIn(sentence, design)
        self.assertIn(sentence, (KIT / "lib" / "airkit" / "review.py").read_text(encoding="utf-8"))
        self.assertEqual(re.findall(r"(\d+) 條規則沒有程式檢查", design), [str(none)])

    def test_budget_of_agents_md_and_each_rule(self):
        # AGENTS.md 每一輪對話都在、規則逐層附進對話：有字數上限，超過就先合併、刪除或改成程式檢查（規範說明 §6），不要放寬上限
        from airkit import install
        agents = install.agents_body()
        self.assertLessEqual(len(agents), rules.AGENTS_MAX_CHARS, f"AGENTS.md 有 {len(agents)} 字")
        for prefix, name in (("CTR", "contracts"), ("STEP", "steps"), ("FLOW", "flows"), ("ADP", "adapters"),
                             ("IO", "storage"), ("MAIN", "main"), ("TEST", "tests")):
            self.assertIn(f"`{prefix}`→{name}", agents)  # 違規訊息說「前綴對照見 AIR/AGENTS.md」，對照表不能被瘦身砍掉
        self.assertIn("air-review", agents)  # 常駐規則 1 指向語意審查
        for r in self.all:
            self.assertLessEqual(len(r.text), rules.MAX_RULE_CHARS, f"{r.id} 有 {len(r.text)} 字")

    def test_same_layer_import_rules_match_config(self):
        # ADP-6、IO-4 說「同層可以、不能循環」：config 要真的允許同層 import，而且開著循環檢查、訊息指回這條規則
        kit = tomllib.loads((KIT / "config.toml").read_text(encoding="utf-8"))
        by_id = rules.by_id()
        for layer, rule_id in (("adapters", "ADP-6"), ("storage", "IO-4")):
            spec = kit["layers"][layer]
            self.assertIn(layer, spec["internal"], layer)
            self.assertEqual((spec["check-cycles"], spec["cycle-rule"]), (True, rule_id), layer)
            self.assertIn("同層", by_id[rule_id].text)
            self.assertIn("循環", by_id[rule_id].text)
        self.assertIn("子套件", by_id["ADP-1"].text)
        self.assertIn("imports.extra", by_id["CTR-4"].text)

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
        self.assertIn("黃金重播", by_id["TEST-5"].text)  # 行為保護測試或黃金重播，兩種都能鎖住行為


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
