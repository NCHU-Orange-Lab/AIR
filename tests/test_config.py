"""專案設定（config.py，schema v3）：[tests] 取代 project.test-command 但舊鍵相容；migration.exclude 與 verify.fingerprint-extra
是路徑前綴、不是 glob；hooks.stop-scope 只能是 session 或 all。"""

import unittest
from pathlib import Path

from tests.helpers import ProjectTestCase
from airkit import config

OLD_LAYOUT = """[project]
package = "demo"
package-dir = "demo"
test-command = "echo old"
test-timeout = 7

[migration]
phase = "installed"
"""


class TestBuild(unittest.TestCase):
    def setUp(self):
        self.kit = config.read_kit()
        self.root = Path("/tmp/air-config-test")

    def build(self, project: dict) -> config.Config:
        return config.build(self.kit, {"project": {"package": "demo"}, **project}, self.root)

    def test_tests_table_wins_over_old_keys_and_old_keys_are_remembered(self):
        c = self.build({"project": {"package": "demo", "test-command": "old", "test-timeout": 5}})
        self.assertEqual((c.test_command, c.test_timeout, c.old_test_keys), ("old", 5, ("test-command", "test-timeout")))
        c = self.build({"project": {"package": "demo", "test-command": "old"}, "tests": {"command": "new", "timeout": 9}})
        self.assertEqual((c.test_command, c.test_timeout, c.old_test_keys), ("new", 9, ("test-command",)))
        c = self.build({"tests": {"command": "new"}})
        self.assertEqual((c.test_command, c.test_timeout, c.old_test_keys), ("new", 600, ()))

    def test_exclude_and_fingerprint_extra_are_normalised_path_prefixes(self):
        c = self.build({"migration": {"exclude": ["demo/experiments/**", "scripts/", "notes.py", "a\\b/*"]},
                        "verify": {"fingerprint-extra": ["conf", "pyproject.toml"]}})
        self.assertEqual(c.exclude, ("demo/experiments", "scripts", "notes.py", "a/b"))
        self.assertEqual(c.fingerprint_extra, ("conf", "pyproject.toml"))
        self.assertTrue(c.is_excluded(self.root / "demo" / "experiments" / "x.py"))
        self.assertTrue(c.is_excluded(self.root / "notes.py"))
        self.assertFalse(c.is_excluded(self.root / "demo" / "experiments_old" / "x.py"))  # 前綴要在目錄邊界
        self.assertFalse(c.is_excluded(self.root / "demo" / "steps" / "x.py"))
        self.assertFalse(c.is_excluded(Path("/elsewhere/notes.py")))

    def test_tests_results_baseline_final_and_golden(self):
        c = self.build({"tests": {"command": "pytest", "results": ".air/test-results.xml",
                                  "final": {"command": "pytest -q", "timeout": 50, "results": ".air/final.xml"},
                                  "golden": [{"name": "smoke", "command": "python x.py", "outputs": ["out/a.json", "out/b.csv"]},
                                             {"name": "nightly", "command": "python y.py", "outputs": ["out/c.json"], "timeout": 9, "scope": ["final"]}]}})
        self.assertEqual((c.test_results, c.test_baseline), (".air/test-results.xml", ".air/test-baseline.json"))
        self.assertEqual(c.suite("batch"), config.Suite("batch", "pytest", 600, ".air/test-results.xml"))
        self.assertEqual(c.suite("final"), config.Suite("final", "pytest -q", 50, ".air/final.xml"))
        self.assertEqual([(g.name, g.outputs, g.timeout, g.scope) for g in c.golden],
                         [("smoke", ("out/a.json", "out/b.csv"), 600, ("batch", "final")), ("nightly", ("out/c.json",), 9, ("final",))])
        plain = self.build({"tests": {"command": "pytest", "baseline": "tests/baseline.json"}})
        self.assertEqual((plain.test_results, plain.test_baseline, plain.final, plain.golden), ("", "tests/baseline.json", None, ()))
        self.assertEqual(plain.suite("final").name, "batch")  # 沒設 final 就用 [tests]

    def test_bad_tests_tables_are_config_errors(self):
        for bad in ({"final": {"timeout": 3}}, {"results": "/abs.xml"}, {"results": ""},
                    {"golden": [{"command": "x", "outputs": ["o"]}]}, {"golden": [{"name": "a", "outputs": ["o"]}]},
                    {"golden": [{"name": "a", "command": "x"}]}, {"golden": [{"name": "a", "command": "x", "outputs": ["o"]}, {"name": "a", "command": "y", "outputs": ["o"]}]},
                    {"golden": [{"name": "a", "command": "x", "outputs": ["o"], "scope": ["weekly"]}]}, {"golden": "nope"}, "nope"):
            with self.subTest(bad=bad):
                with self.assertRaises(config.ConfigError):
                    self.build({"tests": bad})

    def test_bad_paths_and_bad_stop_scope_are_config_errors(self):
        for bad in (["/abs"], ["../up"], ["demo/*.py"], ["demo/[ab]"], ["C:/x"], "notalist", [1]):
            with self.subTest(bad=bad):
                with self.assertRaises(config.ConfigError):
                    self.build({"migration": {"exclude": bad}})
        with self.assertRaises(config.ConfigError):
            self.build({"verify": {"fingerprint-extra": ["x/?"]}})
        with self.assertRaises(config.ConfigError):
            self.build({"hooks": {"stop-scope": "none"}})
        self.assertEqual(self.build({"hooks": {"stop-scope": "all"}}).stop_scope, "all")
        self.assertEqual(self.build({}).stop_scope, "session")


class TestTestsDir(unittest.TestCase):
    def setUp(self):
        self.kit = config.read_kit()
        self.root = Path("/tmp/air-config-test")

    def build(self, project: dict) -> config.Config:
        return config.build(self.kit, {"project": {"package": "demo"}, **project}, self.root)

    def test_is_test_file_only_inside_tests_dir(self):
        c = self.build({"project": {"package": "demo", "package-dir": "demo", "tests-dir": "demo/tests"}})
        self.assertTrue(c.is_test_file(self.root / "demo" / "tests" / "test_a.py"))
        self.assertTrue(c.is_test_file(self.root / "demo" / "tests" / "sub" / "conftest.py"))
        self.assertFalse(c.is_test_file(self.root / "demo" / "testsuite.py"))     # 名字像但不在目錄底下
        self.assertFalse(c.is_test_file(self.root / "demo" / "core.py"))
        self.assertFalse(self.build({}).is_test_file(self.root / "demo" / "tests" / "test_a.py"))  # 預設的 tests-dir 是根目錄的 tests

    def test_tests_dir_cannot_equal_or_contain_package_dir(self):
        for tests_dir in ("demo", ".", "src"):
            with self.subTest(tests_dir=tests_dir):
                with self.assertRaises(config.ConfigError) as ctx:
                    self.build({"project": {"package": "demo", "package-dir": "src/demo" if tests_dir == "src" else "demo", "tests-dir": tests_dir}})
                self.assertIn("不能等於或包住 package-dir", str(ctx.exception))
        self.build({"project": {"package": "demo", "package-dir": "demo", "tests-dir": "demo/tests"}})   # 在套件裡面是可以的
        self.build({"project": {"package": "demo", "package-dir": "src/demo", "tests-dir": "tests"}})


class TestOldLayoutStillLoads(ProjectTestCase):
    def test_old_project_toml_works_and_status_runs(self):
        self.p.write(".air/project.toml", OLD_LAYOUT)
        cfg = self.p.cfg()
        self.assertEqual((cfg.test_command, cfg.test_timeout, cfg.exclude), ("echo old", 7, ()))
        self.assertEqual(self.p.air("status").returncode, 0)


if __name__ == "__main__":
    unittest.main()
