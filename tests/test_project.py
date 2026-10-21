"""專案操作：建立分層、盤點、狀態、遷移階段，以及 check 指令的結論。"""

import unittest

from tests.helpers import ProjectTestCase
from airkit import project


class TestLayers(ProjectTestCase):
    def test_create_layers_and_contract_stubs(self):
        created = project.create_layers(self.p.cfg())
        self.assertIn("demo/contracts/types.py", created)
        self.assertIn("demo/contracts/ports.py", created)
        for name in ("contracts", "steps", "flows", "adapters", "storage", "main"):
            self.assertTrue((self.p.root / "demo" / name / "__init__.py").exists())
        self.assertEqual(self.p.check_all(), [])  # 剛建立的層與樣板都要合規


class TestPhase(ProjectTestCase):
    def test_set_phase(self):
        project.set_phase(self.p.cfg(), "analyzed")
        self.assertEqual(self.p.cfg().phase, "analyzed")

    def test_unknown_phase_rejected(self):
        with self.assertRaises(ValueError):
            project.set_phase(self.p.cfg(), "finished")

    @unittest.expectedFailure  # 第 2 步：階段轉移要有前置條件（F02）
    def test_done_requires_clean_check_and_no_allowances(self):
        self.p.layer("steps", bad='print("x")\n')
        r = self.p.air("phase", "done")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.p.cfg().phase, "installed")


class TestSurveyAndStatus(ProjectTestCase):
    def test_survey_lists_legacy_signals(self):
        self.p.write("demo/legacy.py", 'import requests\n\ndef load(p):\n    return open(p).read()\n')
        out = project.survey(self.p.cfg())
        self.assertIn("`demo.legacy`", out)
        self.assertIn("requests", out)
        self.assertIn("AIR001", out)

    def test_status_summary(self):
        self.p.layer("steps", bad='print("x")\n')
        out = project.status(self.p.cfg())
        self.assertIn("遷移階段：installed", out)
        self.assertIn("違規：1", out)


class TestCheckCommand(ProjectTestCase):
    def test_pass_and_fail_exit_codes(self):
        self.p.layer("steps", ok="x = 1\n")
        self.assertEqual(self.p.air("check").returncode, 0)
        self.p.write("demo/steps/bad.py", 'print("x")\n')
        r = self.p.air("check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("AIR001", r.stdout)

    def test_soft_rules_never_fail(self):
        body = "\n".join(f"    x{i} = {i}" for i in range(45))
        self.p.layer("steps", long=f"def f():\n{body}\n    return x0\n")
        r = self.p.air("check")
        self.assertEqual(r.returncode, 0)
        self.assertIn("AIR101", r.stdout)

    @unittest.expectedFailure  # 第 2 步：打錯的路徑要回報輸入錯誤，不是「通過」（F01）
    def test_nonexistent_explicit_path_is_an_error(self):
        r = self.p.air("check", "does-not-exist.py")
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
