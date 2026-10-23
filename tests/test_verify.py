"""正式驗收（verify）：架構＋測試一起看、結果綁定程式碼、過期會被發現、驗收本身出錯不能算通過。"""

import json
import sys
import time
import unittest

from tests.helpers import TESTS_FAIL, TESTS_PASS, ProjectTestCase
from airkit import verify

TESTS_SLOW = f'"{sys.executable}" -c "import time; time.sleep(30)"'
FAKE_TESTS = f'"{sys.executable}" fake_tests.py'  # 假的測試程式：依 fail.txt 寫出 JUnit XML（見 FAKE_TESTS_PY）
FAKE_TESTS_PY = """import sys
from pathlib import Path
fails = Path("fail.txt").read_text(encoding="utf-8").split() if Path("fail.txt").exists() else []
tests = ["tests/test_a.py::test_one", "tests/test_a.py::test_two", "tests/test_b.py::test_three"]
cases = ""
for t in tests:
    cls, name = t.split("::")
    cases += f'<testcase classname="{cls}" name="{name}">' + ('<failure message="x"/>' if t in fails else "") + "</testcase>"
if "collect" in fails:
    cases += '<testcase name="tests/test_broken.py"><error message="ImportError"/></testcase>'
out = Path(sys.argv[1] if len(sys.argv) > 1 else ".air/test-results.xml")
if "noresults" not in fails:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f'<testsuites><testsuite name="pytest">{cases}</testsuite></testsuites>', encoding="utf-8")
real = [f for f in fails if f not in ("noresults", "exit2")]
sys.exit(2 if "exit2" in fails else (1 if real else 0))
"""
GEN_PY = """import sys
from pathlib import Path
value = Path("value.txt").read_text(encoding="utf-8").strip() if Path("value.txt").exists() else "42"
if value == "crash":
    sys.exit(3)
if value != "skip":
    Path("out").mkdir(exist_ok=True)
    Path("out/metrics.json").write_text(value, encoding="utf-8")
"""
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
        self.p.write(f"{verify.records_dir(self.p.root).relative_to(self.p.root).as_posix()}/verify.json", "{not json")
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
        self.assertEqual(self.p.air("install").returncode, 0)  # 會把 .air/branches 加進 .gitignore
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


class TestBaseline(ProjectTestCase):
    """報告 §4.6（F10）：全套測試本來就有 3 個環境性失敗、6 個收集錯誤，只認 exit 0 永遠拿不到驗收。
    有 [tests] results（JUnit XML）就記既有失敗基準：失敗減基準為空就通過，並列出新增與已修好的。"""
    test_command = FAKE_TESTS
    test_results = ".air/test-results.xml"

    def setUp(self):
        super().setUp()
        self.p.layer("steps", ok="x = 1\n")
        self.p.write("fake_tests.py", FAKE_TESTS_PY)

    def tests_check(self, *args) -> dict:
        self.p.air("verify", *args)
        return next(c for c in self.p.receipt()["checks"] if c["name"] == "project-tests")

    def test_existing_failures_block_until_recorded_as_baseline(self):
        self.p.write("fail.txt", "tests/test_a.py::test_one collect")  # 一個環境性失敗、一個收集錯誤
        c = self.tests_check()
        self.assertEqual((c["status"], c["new_failures"], c["baseline"]), ("fail", ["tests/test_a.py::test_one", "tests/test_broken.py"], 0))
        self.assertIn("既有失敗基準：還沒記錄", self.p.air("status").stdout)
        r = self.p.air("verify", "--record-baseline")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("失敗 2 個", r.stdout)
        self.assertIn("  - tests/test_broken.py", r.stdout)
        baseline = json.loads((self.p.root / ".air" / "test-baseline.json").read_text(encoding="utf-8"))
        self.assertEqual((baseline["schema"], baseline["suites"]["batch"]["failed"]), ("air.test-baseline.v1", ["tests/test_a.py::test_one", "tests/test_broken.py"]))
        r = self.p.air("verify")
        self.assertEqual(r.returncode, 0, r.stdout)  # 失敗都在基準裡：通過（exit 1 也算）
        c = self.tests_check()
        self.assertEqual((c["status"], c["exit"], c["new_failures"], c["fixed"], c["baseline"]), ("pass", 1, [], [], 2))
        self.assertIn("失敗 2（基準內 2，新增 0）", c["detail"])
        self.assertIn("既有失敗基準：batch 2 個", self.p.air("status").stdout)

    def test_new_failure_fails_and_fixed_ones_are_listed(self):
        self.p.write("fail.txt", "tests/test_a.py::test_one collect")
        self.assertEqual(self.p.air("verify", "--record-baseline").returncode, 0)
        self.p.write("fail.txt", "tests/test_b.py::test_three")  # test_one 修好了、test_three 新壞了、收集錯誤的模組沒再出現
        r = self.p.air("verify")
        self.assertEqual(r.returncode, 1)
        c = self.tests_check()
        self.assertEqual((c["status"], c["new_failures"], c["fixed"]), ("fail", ["tests/test_b.py::test_three"], ["tests/test_a.py::test_one"]))
        self.assertIn("新增的失敗：tests/test_b.py::test_three", r.stdout)
        self.assertIn("基準裡已修好：tests/test_a.py::test_one", r.stdout)
        self.assertIn("縮小基準", c["detail"])
        self.assertEqual(self.p.air("verify", "--record-baseline").returncode, 0)  # 重記：基準變成只有 test_three
        self.assertEqual(json.loads((self.p.root / ".air" / "test-baseline.json").read_text(encoding="utf-8"))["suites"]["batch"]["failed"], ["tests/test_b.py::test_three"])
        self.assertEqual(self.p.air("verify").returncode, 0)

    def test_no_baseline_and_no_failures_passes(self):
        self.assertEqual(self.p.air("verify").returncode, 0)
        c = self.tests_check()
        self.assertEqual((c["status"], c["exit"], c["failed"]), ("pass", 0, []))

    def test_exit_code_outside_0_and_1_is_an_error_even_with_results(self):
        self.p.write("fail.txt", "exit2")
        r = self.p.air("verify")
        self.assertEqual(r.returncode, 2)
        self.assertIn("不是 0（通過）或 1（有失敗）", self.tests_check()["detail"])
        r = self.p.air("verify", "--record-baseline")
        self.assertEqual(r.returncode, 2)
        self.assertIn("沒有記基準", r.stderr)

    def test_missing_results_file_is_an_error_and_a_stale_one_is_not_reused(self):
        self.p.write(".air/test-results.xml", "<testsuites><testsuite><testcase name='old'/></testsuite></testsuites>")  # 上次留下的
        self.p.write("fail.txt", "noresults")
        r = self.p.air("verify")
        self.assertEqual(r.returncode, 2)
        self.assertIn("沒有產生 .air/test-results.xml", self.tests_check()["detail"])
        self.assertFalse((self.p.root / ".air" / "test-results.xml").exists())  # 跑之前先刪掉舊檔

    def test_results_file_is_ignored_by_git(self):
        from airkit import install
        self.assertIn("*.xml", install.AIR_IGNORE)


class TestBaselineNeedsResults(ProjectTestCase):
    test_command = TESTS_FAIL

    def test_record_baseline_without_results_is_an_error_and_verify_keeps_exit_code_semantics(self):
        self.p.layer("steps", ok="x = 1\n")
        r = self.p.air("verify", "--record-baseline")
        self.assertEqual(r.returncode, 2)
        self.assertIn("要設 results", r.stderr)
        self.assertEqual(self.p.air("verify").returncode, 1)  # 沒有 results：只看結束碼，跟以前一樣
        self.assertNotIn("既有失敗基準", self.p.air("status").stdout)


class TestFinalSuite(ProjectTestCase):
    """[tests.final]：收尾跑完整套件；每批只跑 [tests]。基準依套件分開記。"""
    test_command = FAKE_TESTS
    test_results = ".air/test-results.xml"
    extra_toml = f"""[tests.final]
command = '{FAKE_TESTS} .air/final.xml'
results = ".air/final.xml"
timeout = 30
"""

    def setUp(self):
        super().setUp()
        self.p.layer("steps", ok="x = 1\n")
        self.p.write("fake_tests.py", FAKE_TESTS_PY)

    def test_final_scope_runs_the_final_suite_with_its_own_baseline(self):
        self.p.write("fail.txt", "tests/test_a.py::test_two")
        self.assertEqual(self.p.air("verify", "--record-baseline", "--scope", "final").returncode, 0)
        baseline = json.loads((self.p.root / ".air" / "test-baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(list(baseline["suites"]), ["final"])
        self.assertEqual(self.p.air("verify", "--scope", "final").returncode, 0)  # final 用 final 的基準
        c = next(c for c in self.p.receipt()["checks"] if c["name"] == "project-tests")
        self.assertEqual((c["suite"], c["command"].endswith(".air/final.xml"), c["baseline"]), ("final", True, 1))
        self.assertTrue((self.p.root / ".air" / "final.xml").exists())
        self.assertEqual(self.p.air("verify").returncode, 1)  # batch 套件沒有基準：同一個失敗算新的
        self.assertEqual(next(c for c in self.p.receipt()["checks"] if c["name"] == "project-tests")["suite"], "batch")
        self.assertIn("收尾：", self.p.air("status").stdout)


class TestGolden(ProjectTestCase):
    """黃金重播：固定輸入跑一次記下輸出的 sha256，之後驗收重跑比對；沒有測試的程式也能鎖住行為（TEST-5）。"""
    test_command = TESTS_PASS
    extra_toml = f"""[[tests.golden]]
name = "smoke"
command = '"{sys.executable}" gen.py'
outputs = ["out/metrics.json"]

[[tests.golden]]
name = "final-only"
command = '"{sys.executable}" gen.py'
outputs = ["out/metrics.json"]
scope = ["final"]
"""

    def setUp(self):
        super().setUp()
        self.p.layer("steps", ok="x = 1\n")
        self.p.write("gen.py", GEN_PY)

    def golden_checks(self, *args) -> dict:
        self.p.air("verify", *args)
        return {c["name"]: c for c in self.p.receipt()["checks"] if c["name"].startswith("golden:")}

    def test_not_recorded_is_an_error_then_record_then_pass(self):
        self.assertIn("黃金重播：2 筆，已記錄 0 筆（.air/golden.json）；還沒記錄：smoke、final-only", self.p.air("status").stdout)
        r = self.p.air("verify")
        self.assertEqual(r.returncode, 2)
        checks = self.golden_checks()
        self.assertEqual(list(checks), ["golden:smoke"])  # batch 不跑 scope = final 的
        self.assertIn("還沒記錄", checks["golden:smoke"]["detail"])
        r = self.p.air("golden", "record")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("2 筆", r.stdout)
        data = json.loads((self.p.root / ".air" / "golden.json").read_text(encoding="utf-8"))
        import hashlib
        self.assertEqual(data["items"]["smoke"]["outputs"]["out/metrics.json"], hashlib.sha256(b"42").hexdigest())
        self.assertEqual(self.p.air("verify").returncode, 0)
        self.assertEqual(self.golden_checks()["golden:smoke"]["status"], "pass")
        self.assertEqual(sorted(self.golden_checks("--scope", "final")), ["golden:final-only", "golden:smoke"])
        self.assertIn("黃金重播：2 筆，已記錄 2 筆", self.p.air("status").stdout)

    def test_changed_or_missing_output_fails_verify(self):
        self.assertEqual(self.p.air("golden", "record").returncode, 0)
        self.p.write("value.txt", "43")  # 行為變了
        r = self.p.air("verify")
        self.assertEqual(r.returncode, 1)
        c = self.golden_checks()["golden:smoke"]
        self.assertEqual((c["status"], c["changed"], c["missing"]), ("fail", ["out/metrics.json"], []))
        self.assertIn("變了 out/metrics.json", r.stdout)
        self.assertEqual((self.p.root / "out" / "metrics.json").read_text(encoding="utf-8"), "43")  # 輸出留在原處
        self.p.write("value.txt", "skip")
        (self.p.root / "out" / "metrics.json").unlink()
        c = self.golden_checks()["golden:smoke"]
        self.assertEqual((c["status"], c["missing"]), ("fail", ["out/metrics.json"]))
        self.p.write("value.txt", "crash")
        self.assertEqual(self.p.air("verify").returncode, 2)
        self.assertEqual(self.golden_checks()["golden:smoke"]["status"], "error")

    def test_record_refuses_failures_and_unknown_names_and_keeps_other_entries(self):
        self.p.write("value.txt", "crash")
        r = self.p.air("golden", "record")
        self.assertEqual(r.returncode, 2)
        self.assertIn("黃金重播沒有記錄", r.stderr)
        self.assertFalse((self.p.root / ".air" / "golden.json").exists())
        self.p.write("value.txt", "skip")
        r = self.p.air("golden", "record", "smoke")
        self.assertEqual(r.returncode, 2)
        self.assertIn("沒有寫出 out/metrics.json", r.stderr)
        (self.p.root / "value.txt").unlink()
        self.assertEqual(self.p.air("golden", "record", "smoke").returncode, 0)  # 只記一筆
        self.assertEqual(list(json.loads((self.p.root / ".air" / "golden.json").read_text(encoding="utf-8"))["items"]), ["smoke"])
        self.assertEqual(self.p.air("golden", "record", "final-only").returncode, 0)  # 另一筆加進去，smoke 保留
        self.assertEqual(sorted(json.loads((self.p.root / ".air" / "golden.json").read_text(encoding="utf-8"))["items"]), ["final-only", "smoke"])
        r = self.p.air("golden", "record", "nope")
        self.assertEqual(r.returncode, 2)
        self.assertIn("沒有這些黃金重播：nope", r.stderr)


class TestRecordsPerBranch(ProjectTestCase):
    """報告 §4.3 d：verify.json、review.json 各只有一份，後寫的蓋掉先寫的；切分支之後紀錄也對不上。"""
    test_command = TESTS_PASS
    git = True

    def git(self, *args) -> str:
        import subprocess
        return subprocess.run(["git", *args], cwd=self.p.root, capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()

    def test_each_branch_keeps_its_own_receipt(self):
        self.p.layer("steps", ok="x = 1\n")
        self.p.commit("base")
        main = verify.branch_name(self.p.root)
        self.assertEqual(self.p.air("verify").returncode, 0)
        self.assertEqual(self.p.receipt()["branch"], main)
        self.assertTrue((self.p.root / ".air" / "branches" / main / "verify.json").exists())
        status = self.p.air("status").stdout
        self.assertIn(f"分支：{main}（本機紀錄在 .air/branches/{main}/）", status)
        self.assertIn("最近驗收：通過", status)
        self.git("checkout", "-q", "-b", "feature/x")
        status = self.p.air("status").stdout
        self.assertIn("分支：feature/x（本機紀錄在 .air/branches/feature_x/）", status)
        self.assertIn("最近驗收：無", status)                       # 別的分支的紀錄不算
        self.assertEqual(self.p.air("verify").returncode, 0)
        self.assertTrue((self.p.root / ".air" / "branches" / "feature_x" / "verify.json").exists())
        self.assertIn("紀錄已寫入 .air/branches/feature_x/verify.json", self.p.air("verify").stdout)
        self.git("checkout", "-q", main)
        self.assertIn("最近驗收：通過", self.p.air("status").stdout)  # 回到 main，main 自己的紀錄還在而且仍然一致
        self.assertEqual(json.loads(self.p.air("status", "--json").stdout)["branch"], main)

    def test_detached_head_and_no_git_have_their_own_names(self):
        self.p.layer("steps", ok="x = 1\n")
        head = self.p.commit("base")
        self.git("checkout", "-q", "--detach")
        self.assertEqual(verify.branch_name(self.p.root), f"detached-{head[:12]}")
        self.assertEqual(verify.branch_slug("feat/x y:z"), "feat_x_y_z")
        from tests.helpers import Project
        plain = Project()  # 沒有 git 的專案（Windows 上刪 .git 會碰到唯讀的物件檔，所以另開一個）
        self.addCleanup(plain.cleanup)
        self.assertEqual(verify.branch_name(plain.root), "no-git")
        self.assertEqual(verify.records_dir(plain.root), plain.root / ".air" / "branches" / "no-git")


if __name__ == "__main__":
    unittest.main()
