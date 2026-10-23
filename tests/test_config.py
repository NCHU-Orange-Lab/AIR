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


class TestOldLayoutStillLoads(ProjectTestCase):
    def test_old_project_toml_works_and_status_runs(self):
        self.p.write(".air/project.toml", OLD_LAYOUT)
        cfg = self.p.cfg()
        self.assertEqual((cfg.test_command, cfg.test_timeout, cfg.exclude), ("echo old", 7, ()))
        self.assertEqual(self.p.air("status").returncode, 0)


if __name__ == "__main__":
    unittest.main()
