"""正式驗收（verify）：架構＋測試一起看、結果綁定程式碼、過期會被發現、驗收本身出錯不能算通過。"""

import json
import sys
import time
import unittest

from tests.helpers import TESTS_FAIL, TESTS_PASS, ProjectTestCase
from airkit import verify

TESTS_SLOW = f'"{sys.executable}" -c "import time; time.sleep(30)"'
TESTS_MUTATE = f'"{sys.executable}" -c "open(\'demo/generated.py\', \'w\').write(\'x = 1\')"'  # 測試期間改到 .py


class TestAccepted(ProjectTestCase):
    test_command = TESTS_PASS

    def setUp(self):
        super().setUp()
        self.p.layer("steps", ok="x = 1\n")

    def test_accepted_writes_receipt(self):
        r = self.p.air("verify")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("結果：通過", r.stdout)
        receipt = self.p.receipt()
        self.assertEqual((receipt["schema"], receipt["scope"], receipt["accepted"], receipt["reasons"]), ("air.verify.v1", "batch", True, []))
        self.assertEqual([(c["name"], c["status"]) for c in receipt["checks"]], [("architecture", "pass"), ("project-tests", "pass")])
        self.assertEqual(receipt["checks"][1]["exit"], 0)
        for key in ("head", "dirty", "source", "config", "air"):
            self.assertIn(key, receipt["fingerprint"])
        self.assertIn("成本指標（本機累計）：verify 1 次", self.p.air("status").stdout)

    def test_json_output(self):
        r = self.p.air("verify", "--json")
        self.assertEqual(r.returncode, 0)
        self.assertTrue(json.loads(r.stdout)["accepted"])

    def test_architecture_violation_fails_even_if_tests_pass(self):
        self.p.write("demo/steps/bad.py", 'print("x")\n')
        r = self.p.air("verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("AIR001", r.stdout)
        receipt = self.p.receipt()
        self.assertFalse(receipt["accepted"])
        self.assertEqual([c["status"] for c in receipt["checks"]], ["fail", "pass"])

    def state(self) -> str:
        return json.loads(self.p.air("status", "--json").stdout)["last_verify"]["state"]

    def test_receipt_goes_stale_when_source_changes(self):
        self.p.air("verify")
        self.assertEqual(self.state(), "fresh")
        self.p.write("demo/steps/more.py", "y = 2\n")
        self.assertEqual(self.state(), "stale")
        self.assertIn("已過期", self.p.air("status").stdout)

    def test_receipt_goes_stale_when_config_changes(self):
        self.p.air("verify")
        toml = self.p.root / ".air" / "project.toml"
        toml.write_text(toml.read_text(encoding="utf-8") + "\n# 改了設定\n", encoding="utf-8")
        self.assertEqual(self.state(), "stale")

    def test_receipt_goes_stale_when_air_version_changes(self):
        self.p.air("verify")
        (self.p.root / "AIR" / "VERSION").write_text("9.9.9\n", encoding="utf-8")
        self.assertEqual(self.state(), "stale")

    def test_receipt_goes_stale_when_checker_changes(self):
        self.p.air("verify")
        checker = self.p.root / "AIR" / "lib" / "airkit" / "checks.py"
        checker.write_text(checker.read_text(encoding="utf-8") + "\n# 改了 checker\n", encoding="utf-8")
        self.assertEqual(self.state(), "stale")

    def test_receipt_stays_fresh_when_nothing_changes(self):
        self.p.air("verify")
        self.p.air("status")  # 讀狀態不會動到指紋
        self.assertEqual(self.state(), "fresh")

    def test_corrupt_receipt_is_treated_as_none(self):
        self.p.write(".air/verify.json", "{not json")
        self.assertIsNone(verify.load_receipt(self.p.root))
        self.assertIn("最近驗收：無", self.p.air("status").stdout)


class TestNoTestCommand(ProjectTestCase):
    def test_missing_test_command_is_not_accepted(self):
        self.p.layer("steps", ok="x = 1\n")
        r = self.p.air("verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("測試指令尚未設定", r.stdout)
        receipt = self.p.receipt()
        self.assertFalse(receipt["accepted"])
        self.assertEqual(receipt["checks"][1]["status"], "skipped")


class TestFailingTests(ProjectTestCase):
    test_command = TESTS_FAIL

    def test_failing_tests_are_not_accepted(self):
        self.p.layer("steps", ok="x = 1\n")
        r = self.p.air("verify")
        self.assertEqual(r.returncode, 1)
        receipt = self.p.receipt()
        self.assertEqual([c["status"] for c in receipt["checks"]], ["pass", "fail"])
        self.assertEqual(receipt["checks"][1]["exit"], 1)
        self.assertTrue(any(reason.startswith("project-tests") for reason in receipt["reasons"]))


class TestTimeout(ProjectTestCase):
    test_command = TESTS_SLOW
    test_timeout = 1

    def test_timeout_is_an_error_not_a_pass(self):
        self.p.layer("steps", ok="x = 1\n")
        start = time.monotonic()
        r = self.p.air("verify")
        self.assertLess(time.monotonic() - start, 15)
        self.assertEqual(r.returncode, 2)
        self.assertIn("逾時", r.stdout)
        self.assertEqual(self.p.receipt()["checks"][1]["status"], "error")


class TestMutatingTests(ProjectTestCase):
    test_command = TESTS_MUTATE

    def test_source_change_during_verification_is_rejected(self):
        self.p.layer("steps", ok="x = 1\n")
        r = self.p.air("verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("驗收期間", r.stdout)
        self.assertFalse(self.p.receipt()["accepted"])


class TestFinalScope(ProjectTestCase):
    test_command = TESTS_PASS

    def test_empty_layers_fail_final_but_not_batch(self):
        self.assertEqual(self.p.air("verify").returncode, 0)
        r = self.p.air("verify", "--scope", "final")
        self.assertEqual(r.returncode, 1)
        self.assertIn("至少要有一個層內的檔案", r.stdout)

    def test_legacy_remaining_is_reported_but_not_blocking(self):
        self.p.layer("steps", ok="x = 1\n")
        self.p.write("demo/cli.py", "x = 1\n")
        r = self.p.air("verify", "--scope", "final")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("demo/cli.py", json.dumps(self.p.receipt(), ensure_ascii=False))
        legacy = next(c for c in self.p.receipt()["checks"] if c["name"] == "legacy-remaining")
        self.assertEqual((legacy["required"], legacy["status"]), (False, "fail"))
        self.assertIn("參考", r.stdout)


class TestFinalWithAllowance(ProjectTestCase):
    test_command = TESTS_PASS
    allow_legacy = '{ from = "steps", module = "demo.legacy", reason = "第 2 批" }'

    def test_allow_legacy_blocks_final_only(self):
        self.p.layer("steps", ok="x = 1\n")
        self.assertEqual(self.p.air("verify").returncode, 0)
        r = self.p.air("verify", "--scope", "final")
        self.assertEqual(r.returncode, 1)
        self.assertIn("demo.legacy", r.stdout)
        self.assertIn("必須清空", r.stdout)


class TestFingerprint(ProjectTestCase):
    test_command = TESTS_PASS
    git = True

    def test_head_and_dirty(self):
        self.assertEqual(self.p.air("install").returncode, 0)  # 會把 .air/verify.json 加進 .gitignore
        self.p.layer("steps", ok="x = 1\n")
        head = self.p.commit("clean")
        self.assertEqual(self.p.air("verify").returncode, 0)
        fp = self.p.receipt()["fingerprint"]
        self.assertEqual((fp["head"], fp["dirty"]), (head, False))
        self.p.write("demo/steps/more.py", "y = 2\n")
        self.p.air("verify")
        self.assertTrue(self.p.receipt()["fingerprint"]["dirty"])

    def test_untracked_python_files_count(self):
        self.p.layer("steps", ok="x = 1\n")
        self.p.commit("base")
        before = verify.fingerprint(self.p.cfg())["source"]
        self.p.write("demo/steps/untracked.py", "z = 3\n")
        self.assertNotEqual(verify.fingerprint(self.p.cfg())["source"], before)

    def test_scripts_outside_the_package_do_not_touch_the_fingerprint(self):
        # 報告 §4.3 b：verify 剛通過，另一個 session 新增 scripts/exp_hotspot_probe.py，紀錄立刻過期；730 個未追蹤檔一直在變，final 驗收幾乎拿不到
        self.p.layer("steps", ok="x = 1\n")
        self.p.commit("base")
        self.assertEqual(self.p.air("verify").returncode, 0)
        self.p.write("scripts/exp_hotspot_probe.py", "print(1)\n")
        self.p.write("notebooks/x.py", "y = 2\n")
        self.assertIn("最近驗收：通過（batch", self.p.air("status").stdout)
        self.assertIn("與目前程式碼一致", self.p.air("status").stdout)
        self.p.write("tests/test_new.py", "def test_x():\n    assert 1\n")  # 測試目錄算
        self.assertIn("已過期", self.p.air("status").stdout)

    def test_fingerprint_extra_counts_and_excluded_paths_do_not(self):
        self.p.layer("steps", ok="x = 1\n")
        self.p.write("demo/experiments/try.py", "a = 1\n")
        self.p.write("conftest.py", "import os\n")
        self.p.write("conf/base.yml", "a: 1\n")
        toml = self.p.root / ".air" / "project.toml"
        toml.write_text(toml.read_text(encoding="utf-8").replace("exclude = []", 'exclude = ["demo/experiments"]')
                        + '\n[verify]\nfingerprint-extra = ["conftest.py", "conf"]\n', encoding="utf-8")
        self.p.commit("base")
        before = verify.fingerprint(self.p.cfg())["source"]
        self.p.write("demo/experiments/try.py", "a = 2\n")
        self.assertEqual(verify.fingerprint(self.p.cfg())["source"], before)  # exclude 的路徑不算
        self.p.write("conftest.py", "import sys\n")
        after_conftest = verify.fingerprint(self.p.cfg())["source"]
        self.assertNotEqual(after_conftest, before)
        self.p.write("conf/base.yml", "a: 2\n")                                 # 目錄裡不是 .py 的檔案也算
        self.assertNotEqual(verify.fingerprint(self.p.cfg())["source"], after_conftest)
        files = [f.as_posix() for f in verify.source_files(self.p.cfg())]  # Windows 的分隔符是反斜線，比對用 posix
        self.assertTrue(any(f.endswith("conf/base.yml") for f in files))
        self.assertFalse(any("experiments" in f for f in files))
        self.assertTrue(any(f.endswith("airkit/checks.py") for f in files))  # AIR 自己的 checker 仍在指紋裡


if __name__ == "__main__":
    unittest.main()
