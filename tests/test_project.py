"""專案操作：建立分層、盤點、狀態、受控的遷移階段轉移，以及 check 指令的結論與結束碼。"""

import json
import unittest

from tests.helpers import TESTS_PASS, ProjectTestCase
from airkit import project

REPORT = "docs/AIR-遷移報告.md"


class TestLayers(ProjectTestCase):
    def test_create_layers_and_contract_stubs(self):
        created = project.create_layers(self.p.cfg())
        self.assertIn("demo/contracts/types.py", created)
        self.assertIn("demo/contracts/ports.py", created)
        for name in ("contracts", "steps", "flows", "adapters", "storage", "main"):
            self.assertTrue((self.p.root / "demo" / name / "__init__.py").exists())
        self.assertEqual(self.p.check_all(), [])  # 剛建立的層與樣板都要合規


class TestSetPhase(ProjectTestCase):
    def test_set_phase_writes_without_checks(self):
        project.set_phase(self.p.cfg(), "done")
        self.assertEqual(self.p.cfg().phase, "done")

    def test_unknown_phase_rejected(self):
        with self.assertRaises(ValueError):
            project.set_phase(self.p.cfg(), "finished")


class TestTransition(ProjectTestCase):
    """phase 指令：一次前進一格、檢查前置條件、退回與強制都要原因、每次都記錄。"""
    test_command = TESTS_PASS

    def test_forward_one_step_and_record(self):
        self.p.write(REPORT, "# 報告\n")
        r = self.p.air("phase", "analyzed")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.p.cfg().phase, "analyzed")
        last = self.p.history()[-1]
        self.assertEqual((last["from"], last["to"], last["how"], last["reason"]), ("installed", "analyzed", "normal", None))
        self.assertIn("time", last)

    def test_cannot_skip_a_phase(self):
        self.p.write(REPORT, "# 報告\n")
        r = self.p.air("phase", "planned")
        self.assertEqual(r.returncode, 1)
        self.assertIn("一次前進一格", r.stderr)
        self.assertEqual(self.p.cfg().phase, "installed")
        self.assertEqual(self.p.history(), [])

    def test_done_cannot_be_set_from_installed(self):
        self.p.layer("steps", bad='print("x")\n')
        r = self.p.air("phase", "done")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.p.cfg().phase, "installed")

    def test_same_phase_rejected(self):
        r = self.p.air("phase", "installed")
        self.assertEqual(r.returncode, 1)
        self.assertIn("已經在", r.stderr)

    def test_unknown_phase_is_an_error(self):
        r = self.p.air("phase", "finished")
        self.assertEqual(r.returncode, 1)
        self.assertIn("未知的階段", r.stderr)

    def test_analyzed_requires_report(self):
        r = self.p.air("phase", "analyzed")
        self.assertEqual(r.returncode, 1)
        self.assertIn("遷移報告", r.stderr)
        self.assertEqual(self.p.cfg().phase, "installed")

    def test_layered_requires_layers_and_clean_check(self):
        project.set_phase(self.p.cfg(), "planned")
        r = self.p.air("phase", "layered")
        self.assertEqual(r.returncode, 1)
        self.assertIn("層目錄還沒建立", r.stderr)
        self.p.air("layers")
        bad = self.p.write("demo/steps/bad.py", 'print("x")\n')
        r = self.p.air("phase", "layered")
        self.assertEqual(r.returncode, 1)
        self.assertIn("違規", r.stderr)
        bad.unlink()
        r = self.p.air("phase", "layered")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_rollback_requires_reason_and_only_goes_backward(self):
        project.set_phase(self.p.cfg(), "planned")
        r = self.p.air("phase", "analyzed", "--rollback")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--reason", r.stderr)
        r = self.p.air("phase", "layered", "--rollback", "--reason", "x")
        self.assertEqual(r.returncode, 1)
        self.assertIn("退回較早的階段", r.stderr)
        r = self.p.air("phase", "analyzed", "--rollback", "--reason", "分類要重做")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.p.cfg().phase, "analyzed")
        last = self.p.history()[-1]
        self.assertEqual((last["how"], last["reason"]), ("rollback", "分類要重做"))

    def test_force_requires_reason_and_is_recorded(self):
        r = self.p.air("phase", "analyzed", "--force")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.p.cfg().phase, "installed")
        r = self.p.air("phase", "analyzed", "--force", "--reason", "使用者說這個專案不寫報告")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("強制跳過", r.stdout)
        self.assertIn("遷移報告", r.stdout)
        last = self.p.history()[-1]
        self.assertEqual(last["how"], "forced")
        self.assertIn("不寫報告", last["reason"])

    def test_done_requires_fresh_accepted_final_receipt(self):
        project.set_phase(self.p.cfg(), "migrating")
        self.p.air("layers")
        self.p.write("demo/steps/ok.py", "x = 1\n")
        r = self.p.air("phase", "done")
        self.assertEqual(r.returncode, 1)
        self.assertIn("沒有驗收紀錄", r.stderr)
        self.assertEqual(self.p.air("verify").returncode, 0)
        r = self.p.air("phase", "done")
        self.assertEqual(r.returncode, 1)
        self.assertIn("不是 final", r.stderr)
        self.assertEqual(self.p.air("verify", "--scope", "final").returncode, 0)
        self.p.write("demo/steps/more.py", "y = 2\n")  # 驗收之後又改程式
        r = self.p.air("phase", "done")
        self.assertEqual(r.returncode, 1)
        self.assertIn("已過期", r.stderr)
        self.assertEqual(self.p.air("verify", "--scope", "final").returncode, 0)
        r = self.p.air("phase", "done")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.p.cfg().phase, "done")

    def test_done_rejects_failed_receipt(self):
        project.set_phase(self.p.cfg(), "migrating")
        self.p.air("layers")
        self.p.write("demo/steps/bad.py", 'print("x")\n')
        self.assertEqual(self.p.air("verify", "--scope", "final").returncode, 1)
        r = self.p.air("phase", "done")
        self.assertEqual(r.returncode, 1)
        self.assertIn("未通過", r.stderr)


class TestTransitionNoTestCommand(ProjectTestCase):
    def test_analyzed_requires_test_command(self):
        self.p.write(REPORT, "# 報告\n")
        r = self.p.air("phase", "analyzed")
        self.assertEqual(r.returncode, 1)
        self.assertIn("test-command", r.stderr)


class TestFullFlow(ProjectTestCase):
    """從安裝走到完成，每一步都用 CLI。"""
    test_command = TESTS_PASS
    git = True

    def test_installed_to_done(self):
        self.assertEqual(self.p.air("install").returncode, 0)
        self.p.write(REPORT, "# 報告\n")
        for phase in ("analyzed", "planned"):
            self.assertEqual(self.p.air("phase", phase).returncode, 0, phase)
        self.assertEqual(self.p.air("layers").returncode, 0)
        for phase in ("layered", "migrating"):
            self.assertEqual(self.p.air("phase", phase).returncode, 0, phase)
        self.p.write("demo/steps/clean.py", "def clean(x):\n    return x\n")
        self.p.commit("batch 1")
        self.assertEqual(self.p.air("verify", "--scope", "final").returncode, 0)
        self.assertEqual(self.p.air("phase", "done").returncode, 0)
        self.assertEqual([h["to"] for h in self.p.history()], ["analyzed", "planned", "layered", "migrating", "done"])
        self.assertTrue(all(h["head"] for h in self.p.history()[-1:]))


class TestSurveyAndStatus(ProjectTestCase):
    def test_survey_lists_legacy_signals(self):
        self.p.write("demo/legacy.py", 'import requests\n\ndef load(p):\n    return open(p).read()\n')
        out = project.survey(self.p.cfg())
        self.assertIn("`demo.legacy`", out)
        self.assertIn("requests", out)
        self.assertIn("AIR001", out)

    def test_status_summary(self):
        self.p.layer("steps", bad='print("x")\n')
        self.p.write("demo/legacy.py", "y = 2\n")
        out = project.status(self.p.cfg())
        self.assertIn("遷移階段：installed", out)
        self.assertIn("違規：1", out)
        self.assertIn("層內 2 個檔案", out)
        self.assertIn("舊程式碼模組 2 個", out)
        self.assertIn("最近驗收：無", out)

    def test_status_has_local_time_and_hook_errors(self):
        self.p.layer("steps", ok="x = 1\n")
        out = project.status(self.p.cfg())
        self.assertRegex(out, r"本機時間：\d{4}-\d{2}-\d{2} \d{2}:\d{2} [+-]\d{4}")  # AI 寫日期的依據
        self.assertIn("hook 錯誤（24 小時內）：無", out)
        self.assertIn("成本指標（本機累計）：無", out)
        d = project.status_data(self.p.cfg())
        self.assertEqual(d["hook_errors"], 0)
        self.assertRegex(d["time"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2} [+-]\d{4}$")

    def test_survey_out_writes_table_and_prints_summary(self):
        self.p.write("demo/legacy.py", 'import requests\n\ndef load(p):\n    return open(p).read()\n')
        r = self.p.air("survey", "--out", "docs/air-survey.md")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("模組數：2", r.stdout)
        self.assertIn("requests（1）", r.stdout)
        self.assertIn("`demo.legacy`（1 處）", r.stdout)
        self.assertIn("完整的盤點表已寫入 docs/air-survey.md（2 列）", r.stdout)
        self.assertNotIn("| 模組 |", r.stdout)  # 表不印進對話
        table = (self.p.root / "docs" / "air-survey.md").read_text(encoding="utf-8")
        self.assertIn("| `demo.legacy` |", table)
        self.assertIn("| 模組 |", self.p.air("survey").stdout)  # 沒有 --out 維持原樣
        self.assertIn("survey 2 次", self.p.air("status").stdout)

    def test_status_json(self):
        self.p.layer("steps", ok="x = 1\n")
        r = self.p.air("status", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        self.assertEqual((d["phase"], d["next_phase"], d["checked_files"], d["violations"], d["last_verify"]),
                         ("installed", "analyzed", 2, 0, None))


class TestUsage(ProjectTestCase):
    def test_summary_counts_and_chars_and_skips_bad_lines(self):
        from airkit import usage
        usage.record(self.p.root, "review-scope", chars=100, files=2)
        usage.record(self.p.root, "review-scope", chars=50, files=1)
        usage.record(self.p.root, "bogus", x=1)
        path = self.p.root / ".air" / "metrics.jsonl"
        path.write_text(path.read_text(encoding="utf-8") + "not json\n", encoding="utf-8")
        s = usage.summary(self.p.root)
        self.assertEqual((s["review-scope"], s["review-scope-chars"], s["verify"]), (2, 150, 0))
        self.assertIn("審查範圍 2 次（共 150 字）", usage.describe(self.p.root))
        self.assertEqual(usage.describe(self.p.root / "nowhere"), "無")


class TestCheckCommand(ProjectTestCase):
    def test_pass_and_fail_exit_codes(self):
        self.p.layer("steps", ok="x = 1\n")
        r = self.p.air("check")
        self.assertEqual(r.returncode, 0)
        self.assertIn("AIR 檢查通過（2 個檔案", r.stdout)
        self.p.write("demo/steps/bad.py", 'print("x")\n')
        r = self.p.air("check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("AIR001", r.stdout)
        self.assertIn("未通過：1 個違規", r.stdout)

    def test_repeated_violations_are_grouped(self):
        # 實測一批 29 個違規有 49% 是重複的修法文字：同一種違規只印一次修法，底下列位置
        self.p.layer("steps", a='print("x")\n', b='print("y")\n', c='print("z")\n')
        r = self.p.air("check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("AIR001 ×3：", r.stdout)
        self.assertEqual(r.stdout.count("不能 print"), 1)
        for name in ("a.py", "b.py", "c.py"):
            self.assertIn(f"demo/steps/{name}:1:1", r.stdout)
        self.assertEqual(len(json.loads(self.p.air("check", "--json").stdout)["hard"]), 3)  # --json 不分組

    def test_soft_rules_never_fail(self):
        body = "\n".join(f"    x{i} = {i}" for i in range(45))
        self.p.layer("steps", long=f"def f():\n{body}\n    return x0\n")
        r = self.p.air("check")
        self.assertEqual(r.returncode, 0)
        self.assertIn("AIR101", r.stdout)

    def test_no_layers_is_not_applicable_but_says_so(self):
        r = self.p.air("check")
        self.assertEqual(r.returncode, 0)
        self.assertIn("沒有檢查任何檔案", r.stdout)
        self.assertNotIn("檢查通過", r.stdout)

    def test_nonexistent_explicit_path_is_an_error(self):
        r = self.p.air("check", "does-not-exist.py")
        self.assertEqual(r.returncode, 2)
        self.assertIn("找不到檔案", r.stdout)

    def test_json(self):
        self.p.layer("steps", bad='print("x")\n')
        r = self.p.air("check", "--json")
        self.assertEqual(r.returncode, 1)
        d = json.loads(r.stdout)
        self.assertEqual(d["verdict"], "violation")
        self.assertEqual(d["hard"][0]["code"], "AIR001")

    def test_not_installed_is_an_operation_error(self):
        (self.p.root / ".air" / "project.toml").unlink()
        r = self.p.air("check")
        self.assertEqual(r.returncode, 2)
        self.assertIn("還沒安裝", r.stderr)


if __name__ == "__main__":
    unittest.main()
