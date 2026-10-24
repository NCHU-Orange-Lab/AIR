"""allow-legacy 過期偵測與防倒退（legacy.py、verify.legacy_alerts）。別人怎麼做：import-linter 預設把對不上任何 import 的例外當錯誤，
tach --exact 對宣告了卻沒用到的依賴報錯；Google 的大規模變更需要防止再引入舊東西的機制。
verify.legacy-alerting：warn（預設，只提醒）｜error（批次驗收不通過）｜none。"""

import json
import subprocess
import unittest
from pathlib import Path

from tests.helpers import TESTS_PASS, ProjectTestCase
from airkit import config, doctor, legacy, project

ALLOW = '{ from = "steps", module = "demo.old", reason = "第 2 批才搬" }'


class TestAlertingSetting(unittest.TestCase):
    def build(self, project_toml: dict):
        return config.build(config.read_kit(), {"project": {"package": "demo"}, **project_toml}, Path("/tmp/air-config-test"))

    def test_default_and_values(self):
        self.assertEqual(self.build({}).legacy_alerting, "warn")
        for v in ("warn", "error", "none"):
            self.assertEqual(self.build({"verify": {"legacy-alerting": v}}).legacy_alerting, v)

    def test_bad_value_is_a_config_error(self):
        with self.assertRaises(config.ConfigError) as ctx:
            self.build({"verify": {"legacy-alerting": "loud"}})
        self.assertIn("warn、error、none", str(ctx.exception))


class TestStaleAllowances(ProjectTestCase):
    allow_legacy = ALLOW + ', { from = "steps", module = "demo.gone", reason = "已刪的模組" }'

    def stale(self):
        return [(a.module, why) for a, why in legacy.stale_allowances(self.p.cfg())]

    def test_used_unused_and_missing(self):
        self.p.write("demo/old.py", "x = 1\n")
        self.p.layer("steps", uses="from demo.old import x\n")
        self.assertEqual(self.stale(), [("demo.gone", legacy.NO_MODULE)])          # old 有 import 用到；gone 沒有 import、檔案也不見了
        (self.p.root / "demo" / "steps" / "uses.py").write_text("y = 2\n", encoding="utf-8")
        self.assertEqual(self.stale(), [("demo.old", legacy.NO_IMPORT), ("demo.gone", legacy.NO_MODULE)])  # 搬完了：old.py 還在但沒人 import

    def test_prefix_import_counts_as_used(self):
        self.p.write("demo/old/__init__.py", "")
        self.p.write("demo/old/deep.py", "z = 1\n")
        self.p.layer("steps", uses="from demo.old.deep import z\n")
        self.assertNotIn("demo.old", [m for m, _ in self.stale()])

    def test_import_from_another_layer_does_not_count_and_missing_layer_is_stale(self):
        self.p.write("demo/old.py", "x = 1\n")
        self.p.layer("storage", uses="from demo.old import x\n")                   # 允許的是 steps，不是 storage
        self.assertIn(("demo.old", legacy.NO_IMPORT), self.stale())

    def test_module_outside_the_package_is_never_called_missing(self):
        cfg = self.p.cfg()
        self.assertTrue(legacy._module_exists("other.pkg.mod", cfg))               # 判斷不了就不當成過期的理由


class TestStaleSurfaces(ProjectTestCase):
    allow_legacy = ALLOW
    test_command = TESTS_PASS

    def setUp(self):
        super().setUp()
        self.p.write("demo/old.py", "x = 1\n")                                    # 模組還在，但沒有任何 import 用到它
        self.p.layer("steps", ok="x = 1\n")

    def alert(self):
        return next((c for c in self.p.receipt()["checks"] if c["name"] == "allow-legacy-stale"), None)

    def test_verify_shows_it_but_does_not_block_by_default(self):
        r = self.p.air("verify")
        self.assertEqual(r.returncode, 0, r.stdout)
        c = self.alert()
        self.assertEqual((c["required"], c["status"], c["items"]), (False, "fail", ["steps → demo.old（沒有任何 import 用到）"]))
        self.assertIn("經使用者同意後從 .air/project.toml 移除", r.stdout)

    def test_status_text_and_json(self):
        self.assertIn("暫時允許的舊模組 import：1（其中 1 筆已經不需要）", self.p.air("status").stdout)
        self.assertEqual(json.loads(self.p.air("status", "--json").stdout)["allow_legacy_stale"], 1)

    def test_doctor_warns(self):
        warns = [i.text for i in doctor.diagnose(self.p.root) if i.area == "allow-legacy"]
        self.assertEqual(len(warns), 1)
        self.assertIn("steps → demo.old（沒有任何 import 用到）：已經不需要", warns[0])

    def test_final_scope_only_has_the_required_empty_list_check(self):
        r = self.p.air("verify", "--scope", "final")
        names = [c["name"] for c in self.p.receipt()["checks"]]
        self.assertIn("allow-legacy", names)
        self.assertNotIn("allow-legacy-stale", names)
        self.assertEqual(r.returncode, 1)


class TestStaleErrorMode(TestStaleSurfaces):
    extra_toml = '[verify]\nlegacy-alerting = "error"'

    def test_verify_shows_it_but_does_not_block_by_default(self):
        r = self.p.air("verify")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertTrue(self.alert()["required"])
        self.assertIn("allow-legacy-stale", self.p.receipt()["reasons"][0])


class TestStaleNoneMode(TestStaleSurfaces):
    extra_toml = '[verify]\nlegacy-alerting = "none"'

    def test_verify_shows_it_but_does_not_block_by_default(self):
        self.assertEqual(self.p.air("verify").returncode, 0)
        self.assertIsNone(self.alert())

    def test_status_text_and_json(self):
        self.assertNotIn("已經不需要", self.p.air("status").stdout)
        self.assertEqual(json.loads(self.p.air("status", "--json").stdout)["allow_legacy_stale"], 0)

    def test_doctor_warns(self):
        self.assertEqual([i for i in doctor.diagnose(self.p.root) if i.area == "allow-legacy"], [])


class GrowthCase(ProjectTestCase):
    phase = "migrating"
    git = True
    test_command = TESTS_PASS

    def setUp(self):
        super().setUp()
        self.p.layer("steps", ok="x = 1\n")
        self.p.write("demo/old.py", "def f():\n    return 1\n")
        self.p.commit("base")
        self.assertEqual(self.p.air("verify").returncode, 0)
        self.assertEqual(self.p.air("batch", "close").returncode, 0)             # 第 1 批：之後的新增才算倒退
        self.p.commit("batch 1 record")

    def growth(self):
        self.p.air("verify")
        return next(c for c in self.p.receipt()["checks"] if c["name"] == "legacy-growth")


class TestGrowth(GrowthCase):
    def test_a_new_logic_file_outside_the_layers_is_reported_not_blocking(self):
        self.p.write("demo/newmod.py", "def g():\n    return 2\n")                # 還沒追蹤
        c = self.growth()
        self.assertEqual((c["required"], c["status"], c["items"]), (False, "fail", ["demo/newmod.py"]))
        self.assertEqual(self.p.air("verify").returncode, 0)
        self.assertIn("新程式一律寫進對應的層", c["detail"])
        self.assertIn("migration.exclude", c["detail"])

    def test_committed_new_file_still_counts_but_old_ones_do_not(self):
        self.p.write("demo/newmod.py", "def g():\n    return 2\n")
        self.p.commit("add")
        self.assertEqual(self.growth()["items"], ["demo/newmod.py"])             # demo/old.py 是起點就有的

    def test_rename_shim_marker_and_layer_files_are_not_growth(self):
        subprocess.run(["git", "mv", "demo/old.py", "demo/renamed.py"], cwd=self.p.root, check=True)                        # 改名不算新增
        self.p.write("demo/shim.py", "from demo.steps.ok import x  # noqa\n")
        self.p.write("demo/sub/__init__.py", "")
        self.p.write("demo/steps/more.py", "y = 2\n")                              # 在層內
        c = self.growth()
        self.assertEqual((c["status"], c["items"]), ("pass", []))

    def test_without_a_baseline_commit_it_says_so(self):
        (self.p.root / ".air" / "history.jsonl").write_text("", encoding="utf-8")  # 沒有批次、沒有轉移：起點不明
        c = self.growth()
        self.assertEqual((c["status"], c["detail"]), ("pass", "無法比對（沒有起點 commit）"))

    def test_only_while_migrating(self):
        self.p.write("demo/newmod.py", "def g():\n    return 2\n")
        path = self.p.root / ".air" / "project.toml"
        path.write_text(path.read_text(encoding="utf-8").replace('phase = "migrating"', 'phase = "layered"'), encoding="utf-8")
        self.p.air("verify")
        self.assertNotIn("legacy-growth", [c["name"] for c in self.p.receipt()["checks"]])

    def test_batch_close_warns_and_records_the_numbers(self):
        self.p.write("demo/newmod.py", "def g():\n    return 2\n")
        self.assertEqual(self.p.air("verify").returncode, 0)
        r = self.p.air("batch", "close")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("注意：起點之後層外新增了 1 個有自己程式的檔案", r.stdout)
        e = self.p.history()[-1]
        self.assertEqual((e["legacy_new"], e["allow_stale"]), (1, None))


class TestGrowthExcluded(GrowthCase):
    exclude = '"demo/scratch"'

    def test_excluded_paths_are_not_growth(self):
        self.p.write("demo/scratch/try.py", "def g():\n    return 2\n")
        self.assertEqual(self.growth()["items"], [])


class TestGrowthErrorMode(GrowthCase):
    extra_toml = '[verify]\nlegacy-alerting = "error"'

    def test_blocks_the_batch_verify(self):
        self.p.write("demo/newmod.py", "def g():\n    return 2\n")
        r = self.p.air("verify")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertTrue(self.growth()["required"])
        self.assertIn("legacy-growth", self.p.receipt()["reasons"][0])


class TestGrowthNoneMode(GrowthCase):
    extra_toml = '[verify]\nlegacy-alerting = "none"'

    def test_not_shown(self):
        self.p.write("demo/newmod.py", "def g():\n    return 2\n")
        self.p.air("verify")
        self.assertNotIn("legacy-growth", [c["name"] for c in self.p.receipt()["checks"]])
        r = self.p.air("batch", "close")
        self.assertNotIn("起點之後層外新增", r.stdout)
        self.assertEqual(self.p.history()[-1]["legacy_new"], None)


if __name__ == "__main__":
    unittest.main()
