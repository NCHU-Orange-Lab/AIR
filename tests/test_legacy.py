"""層外的檔案分三類（legacy.py）：有邏輯、轉接檔、套件標記。status、批次紀錄、final 驗收用同一個判斷；
收尾（final）時層外不能有有邏輯的模組或轉接檔，除非在 migration.exclude。
試跑（csvkit）：17 個有邏輯的模組還在層外，phase done 照樣通過。"""

import json
import unittest
from pathlib import Path

from tests.helpers import TESTS_PASS, ProjectTestCase
from airkit import legacy, project


class TestKind(ProjectTestCase):
    def kind(self, rel: str, text: str) -> str:
        return legacy.kind(self.p.write(rel, text))

    def test_logic_shim_and_marker(self):
        self.assertEqual(self.kind("demo/a.py", "def f():\n    return 1\n"), legacy.LOGIC)
        self.assertEqual(self.kind("demo/b.py", "X = 1\n"), legacy.LOGIC)                                  # 一般的賦值是邏輯
        self.assertEqual(self.kind("demo/c.py", '"""搬走了。"""\nfrom demo.steps import x\n__all__ = ["x"]\n'), legacy.SHIM)
        self.assertEqual(self.kind("demo/d.py", "import os\n"), legacy.SHIM)
        self.assertEqual(self.kind("demo/sub/__init__.py", ""), legacy.MARKER)
        self.assertEqual(self.kind("demo/sub2/__init__.py", '"""子套件。"""\n'), legacy.MARKER)
        self.assertEqual(self.kind("demo/sub3/__init__.py", "pass\n"), legacy.MARKER)

    def test_init_with_imports_is_a_shim_and_empty_non_init_module_is_too(self):
        self.assertEqual(self.kind("demo/sub/__init__.py", "from demo.steps import x\n"), legacy.SHIM)
        self.assertEqual(self.kind("demo/empty.py", ""), legacy.SHIM)                                       # 空的非 __init__ 模組不是套件標記

    def test_syntax_only_so_small_wrappers_and_try_imports_count_as_logic(self):
        self.assertEqual(self.kind("demo/e.py", "from demo.steps import x\n\ndef f():\n    return x\n"), legacy.LOGIC)
        self.assertEqual(self.kind("demo/f.py", "try:\n    import yaml\nexcept ImportError:\n    yaml = None\n"), legacy.LOGIC)

    def test_unreadable_is_logic(self):
        self.assertEqual(self.kind("demo/broken.py", "def (:\n"), legacy.LOGIC)
        self.assertEqual(legacy.kind(self.p.root / "demo" / "missing.py"), legacy.LOGIC)

    def test_counts_and_describe(self):
        self.p.write("demo/a.py", "def f():\n    return 1\n")
        self.p.write("demo/b.py", "import os\n")
        self.p.write("demo/sub/__init__.py", "")
        c = legacy.counts([self.p.root / "demo" / n for n in ("a.py", "b.py", "sub/__init__.py", "__init__.py")])
        self.assertEqual(c, {legacy.LOGIC: 1, legacy.SHIM: 1, legacy.MARKER: 2})
        self.assertEqual(legacy.describe(c), "1 個有自己的程式、1 個轉接檔、2 個套件標記")


class FinalCase(ProjectTestCase):
    test_command = TESTS_PASS

    def setUp(self):
        super().setUp()
        self.p.layer("steps", ok="x = 1\n")

    def final(self):
        r = self.p.air("verify", "--scope", "final")
        check = next(c for c in self.p.receipt()["checks"] if c["name"] == "legacy-remaining")
        return r, check


class TestFinalGate(FinalCase):
    def test_logic_left_outside_the_layers_blocks_final_and_says_how_to_get_out(self):
        self.p.write("demo/cli.py", "def main():\n    return 1\n")
        r, c = self.final()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertEqual((c["required"], c["status"]), (True, "fail"))
        self.assertEqual(c["items"], ["demo/cli.py（有自己的程式）"])
        for text in ("層外還有 1 個有自己的程式、0 個轉接檔、1 個套件標記", "migration.exclude", "遷移報告"):
            self.assertIn(text, r.stdout)

    def test_shims_block_final_too(self):
        self.p.write("demo/old.py", "from demo.steps.ok import x  # noqa\n")
        r, c = self.final()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertEqual(c["items"], ["demo/old.py（轉接檔）"])
        self.assertIn("移除轉接檔", r.stdout)

    def test_only_package_markers_left_is_clean(self):
        self.p.write("demo/sub/__init__.py", "")
        r, c = self.final()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual((c["required"], c["status"], c["items"]), (True, "pass", []))
        self.assertIn("層外只剩 2 個套件標記用的 __init__.py", c["detail"])

    def test_nothing_left_at_all(self):
        (self.p.root / "demo" / "__init__.py").unlink()
        r, c = self.final()
        self.assertEqual(c["detail"], "沒有層外的模組")

    def test_batch_scope_does_not_have_the_item(self):
        self.p.write("demo/cli.py", "def main():\n    return 1\n")
        self.assertEqual(self.p.air("verify").returncode, 0)
        self.assertNotIn("legacy-remaining", [c["name"] for c in self.p.receipt()["checks"]])


class TestFinalWithExclude(FinalCase):
    exclude = '"demo/cli.py", "demo/compat"'

    def test_excluded_paths_are_the_official_way_to_leave_things_outside(self):
        self.p.write("demo/cli.py", "def main():\n    return 1\n")
        self.p.write("demo/compat/old.py", "from demo.steps.ok import x  # noqa\n")
        r, c = self.final()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(c["status"], "pass")


class TestPhaseDone(ProjectTestCase):
    phase = "migrating"
    git = True
    test_command = TESTS_PASS

    def test_done_is_refused_until_the_leftovers_are_gone_or_excluded(self):
        self.p.layer("steps", ok="x = 1\n")
        self.p.write("docs/AIR-遷移報告.md", "# 報告\n")
        self.p.write("demo/cli.py", "def main():\n    return 1\n")
        self.p.commit("base")
        self.assertEqual(self.p.air("verify", "--scope", "final").returncode, 1)
        r = self.p.air("phase", "done")
        self.assertEqual(r.returncode, 1)
        self.assertIn("legacy-remaining", r.stderr + r.stdout)
        (self.p.root / "demo" / "cli.py").unlink()
        self.assertEqual(self.p.air("verify", "--scope", "final").returncode, 0)
        self.assertEqual(self.p.air("phase", "done").returncode, 0)


class TestStatusNumbers(ProjectTestCase):
    def test_status_text_and_json_use_the_same_three_classes(self):
        self.p.layer("steps", ok="x = 1\n")
        self.p.write("demo/a.py", "def f():\n    return 1\n")
        self.p.write("demo/b.py", "import os\n")
        self.p.write("demo/sub/__init__.py", "")
        out = self.p.air("status").stdout
        self.assertIn("舊程式碼模組 4 個（不檢查；1 個有自己的程式、1 個轉接檔、2 個套件標記）", out)
        d = json.loads(self.p.air("status", "--json").stdout)
        self.assertEqual(d["legacy_modules"], 4)
        self.assertEqual(d["legacy_kinds"], {"logic": 1, "shim": 1, "marker": 2})
        self.assertEqual(sum(d["legacy_kinds"].values()), d["legacy_modules"])


class TestBatchNumbers(ProjectTestCase):
    phase = "migrating"
    git = True
    test_command = TESTS_PASS

    def test_batch_close_records_the_same_classes(self):
        self.p.layer("steps", ok="x = 1\n")
        self.p.write("demo/a.py", "def f():\n    return 1\n")
        self.p.write("demo/b.py", "import os\n")
        self.p.write("demo/sub/__init__.py", "")        # 套件標記有 2 個（再加 demo/__init__.py），轉接檔 1 個：數字不同才分得出欄位有沒有放錯
        self.p.commit("base")
        self.assertEqual(self.p.air("verify").returncode, 0)
        r = self.p.air("batch", "close")
        self.assertEqual(r.returncode, 0, r.stderr)
        e = self.p.history()[-1]
        self.assertEqual((e["legacy_remaining"], e["legacy_shims"], e["legacy_markers"]), (1, 1, 2))
        self.assertIn("舊模組剩 1（另有 1 個轉接檔）", r.stdout)       # 套件標記不列：它永遠合法
        self.assertEqual(project.status_data(self.p.cfg())["legacy_kinds"], {"logic": 1, "shim": 1, "marker": 2})


if __name__ == "__main__":
    unittest.main()
