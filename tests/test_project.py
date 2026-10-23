"""專案操作：建立分層、盤點、狀態、受控的遷移階段轉移，以及 check 指令的結論與結束碼。"""

import json
import subprocess
import unittest

from tests.helpers import TESTS_FAIL, TESTS_PASS, ProjectTestCase
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


class TestTransitionFailingTests(ProjectTestCase):
    test_command = TESTS_FAIL

    def test_layered_is_refused_while_verify_fails(self):
        project.set_phase(self.p.cfg(), "planned")
        self.p.air("layers")
        self.assertEqual(self.p.air("verify").returncode, 1)
        r = self.p.air("phase", "layered")
        self.assertEqual(r.returncode, 1)
        self.assertIn("還沒有通過的驗收", r.stderr)
        self.assertEqual(self.p.cfg().phase, "planned")


class TestLayersCollision(ProjectTestCase):
    """試跑（cookiecutter）：專案原本就有 cookiecutter/main.py，layers 建了 main/ 之後 cookiecutter.main 指到空的 __init__.py。
    layers 現在拒絕；專案的模組就是這一層時用 --adopt-module 搬成 <層>/__init__.py。"""
    phase = "planned"
    git = True
    MAIN = "import os\nfrom demo.config import get_user_config\nfrom demo.config import other\nfrom demo.replay import dump\n\n\ndef cookiecutter():\n    return os.getcwd()\n"

    def setUp(self):
        super().setUp()
        self.p.write("demo/main.py", self.MAIN)
        self.p.write("demo/config.py", "def get_user_config():\n    return {}\n\n\ndef other():\n    return 1\n")
        self.p.write("demo/replay.py", "def dump():\n    return 1\n")
        self.p.commit("base")

    def test_collision_is_refused_and_nothing_is_created(self):
        r = self.p.air("layers")
        self.assertEqual(r.returncode, 1)
        self.assertIn("遮蔽專案原有的同名模組 demo/main.py", r.stderr)
        self.assertIn("python3 AIR/air.py layers --adopt-module main", r.stderr)
        self.assertIn("沒有建立任何東西", r.stderr)
        self.assertEqual(sorted(x.name for x in (self.p.root / "demo").iterdir() if x.name != "__pycache__"),
                         ["__init__.py", "config.py", "main.py", "replay.py"])  # 一個層目錄都沒建：全有或全無
        self.assertEqual(self.p.air("layers", "steps", "flows").returncode, 0)  # 不碰 main 的話可以建
        self.assertFalse((self.p.root / "demo" / "main").exists())

    def test_two_collisions_are_listed_together(self):
        self.p.write("demo/steps.py", "x = 1\n")
        r = self.p.air("layers")
        self.assertEqual(r.returncode, 1)
        self.assertIn("steps/、main/", r.stderr)  # 依層的順序
        self.assertIn("--adopt-module steps --adopt-module main", r.stderr)

    def test_adopt_moves_the_module_with_git_and_lists_allow_legacy(self):
        r = self.p.air("layers", "--adopt-module", "main")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("搬移 demo/main.py → demo/main/__init__.py（import 路徑不變）", r.stdout)
        self.assertFalse((self.p.root / "demo" / "main.py").exists())
        self.assertEqual((self.p.root / "demo" / "main" / "__init__.py").read_text(encoding="utf-8"), self.MAIN)
        for mod in ("demo.config", "demo.replay"):
            self.assertIn(f'{{ from = "main", module = "{mod}", reason = "main.py 原本就是這一層', r.stdout)
        self.assertEqual(r.stdout.count('module = "demo.config"'), 1)  # 同一個模組只列一次
        self.assertNotIn('module = "os"', r.stdout)
        staged = subprocess.run(["git", "status", "--porcelain"], cwd=self.p.root, capture_output=True, text=True, encoding="utf-8").stdout
        self.assertIn("R  demo/main.py -> demo/main/__init__.py", staged)  # git 看得出是搬移
        self.assertTrue((self.p.root / "demo" / "steps" / "__init__.py").exists())  # 其他層照建
        self.assertIn("AIR008", self.p.air("check").stdout)  # allow-legacy 還沒加：check 要它加

    def test_adopt_works_when_main_dir_has_only_the_empty_init_from_an_earlier_layers_run(self):
        # 試跑的實際順序：先跑了 layers（建出空的 main/__init__.py）才發現撞名
        (self.p.root / "demo" / "main").mkdir()
        (self.p.root / "demo" / "main" / "__init__.py").write_text("", encoding="utf-8")
        self.assertIn("AIR010", self.p.air("check").stdout)  # 這個狀態 check 就會報，layers --adopt-module 是它的修法
        r = self.p.air("layers", "--adopt-module", "main")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.p.root / "demo" / "main" / "__init__.py").read_text(encoding="utf-8"), self.MAIN)

    def test_adopt_without_git_falls_back_to_rename(self):
        import shutil
        shutil.rmtree(self.p.root / ".git")
        r = self.p.air("layers", "--adopt-module", "main")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.p.root / "demo" / "main" / "__init__.py").exists())
        self.assertFalse((self.p.root / "demo" / "main.py").exists())

    def test_adopt_without_git_replaces_an_existing_empty_init(self):
        # Windows 的 Path.rename 遇到既有檔案會失敗，所以採用之前要先刪掉空的 __init__.py（POSIX 上 rename 會直接覆蓋，看不出差別）
        import shutil
        shutil.rmtree(self.p.root / ".git")
        (self.p.root / "demo" / "main").mkdir()
        (self.p.root / "demo" / "main" / "__init__.py").write_text("", encoding="utf-8")
        r = self.p.air("layers", "--adopt-module", "main")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.p.root / "demo" / "main" / "__init__.py").read_text(encoding="utf-8"), self.MAIN)

    def test_adopt_conditions(self):
        r = self.p.air("layers", "--adopt-module", "steps")  # 沒有 steps.py
        self.assertEqual(r.returncode, 1)
        self.assertIn("沒有同名模組 demo/steps.py，不需要 --adopt-module steps", r.stderr)
        r = self.p.air("layers", "--adopt-module", "nope")
        self.assertEqual(r.returncode, 1)
        self.assertIn("只能指定層的名稱", r.stderr)
        r = self.p.air("layers", "flows", "--adopt-module", "main")
        self.assertEqual(r.returncode, 1)
        self.assertIn("main 不在要建立的層裡", r.stderr)
        self.p.write("demo/main/helper.py", "x = 1\n")  # main/ 已經有內容：不能自動併
        r = self.p.air("layers", "--adopt-module", "main")
        self.assertEqual(r.returncode, 1)
        self.assertIn("main/ 已經有內容", r.stderr)
        self.assertTrue((self.p.root / "demo" / "main.py").exists())  # 失敗什麼都沒動

    def test_adopt_of_a_module_without_legacy_imports_says_so(self):
        self.p.write("demo/main.py", "import os\n\n\ndef run():\n    return os.getcwd()\n")
        r = self.p.air("layers", "--adopt-module", "main")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("沒有 import 任何尚未遷移的舊模組，不需要 allow-legacy", r.stdout)

    def test_air010_when_both_exist_and_the_hook_stop_reports_it_without_a_snapshot(self):
        (self.p.root / "demo" / "main").mkdir()
        (self.p.root / "demo" / "main" / "__init__.py").write_text("", encoding="utf-8")
        r = self.p.air("check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("AIR010", r.stdout)
        self.assertIn("層目錄 main/ 遮蔽了同名模組 main.py", r.stdout)
        self.assertIn("--adopt-module main", r.stdout)
        self.assertIn("MAIN-1", r.stdout)
        self.assertIn("demo/main.py", r.stdout)
        stop = self.p.air("hook", "stop", stdin='{"session_id": "x"}')
        self.assertEqual(stop.returncode, 2)  # Stop 的 session 範圍也不放過（跟 AIR009 一樣不分 session）
        self.assertIn("AIR010", stop.stderr)
        single = self.p.air("check", "demo/main/__init__.py")
        self.assertNotIn("AIR010", single.stdout)  # 編輯後的單檔檢查不報（它不是這個檔案的問題）
        self.p.air("layers", "--adopt-module", "main")
        self.assertNotIn("AIR010", self.p.air("check").stdout)

    def test_dir_only_or_module_only_is_not_a_shadow(self):
        from airkit import checks
        self.assertEqual(checks.layer_shadows(self.p.cfg()), [])  # 只有 main.py
        (self.p.root / "demo" / "main.py").unlink()
        (self.p.root / "demo" / "main").mkdir()
        self.assertEqual(checks.layer_shadows(self.p.cfg()), [])  # 只有 main/


class TestSetPhase(ProjectTestCase):
    def test_set_phase_writes_without_checks(self):
        project.set_phase(self.p.cfg(), "done")
        self.assertEqual(self.p.cfg().phase, "done")

    def test_unknown_phase_rejected(self):
        with self.assertRaises(ValueError):
            project.set_phase(self.p.cfg(), "finished")

    def test_set_phase_refuses_when_the_file_changed_under_it_and_leaves_no_tmp(self):
        # 報告 §4.3 e：兩個 session 同時推進階段，讀－改－寫會互相覆蓋
        stale = self.p.cfg()
        project.set_phase(self.p.cfg(), "analyzed")  # 另一個 session 先推進了
        with self.assertRaises(project.TransitionError) as cm:
            project.set_phase(stale, "analyzed")
        self.assertIn("別的 session", str(cm.exception))
        self.assertEqual(self.p.cfg().phase, "analyzed")
        self.assertFalse((self.p.root / ".air" / "project.toml.tmp").exists())
        text = (self.p.root / ".air" / "project.toml").read_text(encoding="utf-8")
        self.assertIn("[tests]", text)  # 其他內容原封不動


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
        self.assertEqual(r.returncode, 1)  # check 過了，但還沒有驗收
        self.assertIn("還沒有通過的驗收", r.stderr)
        self.assertEqual(self.p.air("verify").returncode, 0)
        r = self.p.air("phase", "layered")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_layered_needs_a_fresh_accepted_verify_not_just_a_clean_check(self):
        # 試跑（cookiecutter）：建立 main/ 讓 3 個測試檔收集失敗，但 check 看不到，只有跑測試才知道
        project.set_phase(self.p.cfg(), "planned")
        self.p.air("layers")
        r = self.p.air("phase", "layered")
        self.assertEqual((r.returncode, "沒有通過的驗收" in r.stderr or "還沒有通過的驗收" in r.stderr), (1, True))
        self.assertEqual(self.p.air("verify").returncode, 0)
        self.p.write("demo/steps/a.py", "x = 1\n")  # 驗收之後程式又變了：紀錄過期
        r = self.p.air("phase", "layered")
        self.assertEqual(r.returncode, 1)
        self.assertIn("還沒有通過的驗收", r.stderr)
        self.assertEqual(self.p.air("verify").returncode, 0)
        self.assertEqual(self.p.air("phase", "layered").returncode, 0)

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


class TestPause(ProjectTestCase):
    """pause／resume：遷移擱置時的正式出口（實戰中遷移停在分析階段幾週，整個 repo 的 .py 都被凍結）。
    階段保留、記進 history、status 顯示；暫停中不能推進階段。"""
    git = True

    def test_pause_needs_reason_and_is_recorded(self):
        r = self.p.air("pause")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--reason", r.stderr)
        self.assertIsNone(project.paused(self.p.root))
        r = self.p.air("pause", "--reason", "先趕比賽")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("階段 installed 保留", r.stdout)
        self.assertIn("resume", r.stdout)
        entry = project.paused(self.p.root)
        self.assertEqual((entry["phase"], entry["reason"]), ("installed", "先趕比賽"))
        last = self.p.history()[-1]
        self.assertEqual((last["from"], last["to"], last["how"], last["reason"]), ("installed", "installed", "pause", "先趕比賽"))
        self.assertEqual(self.p.cfg().phase, "installed")  # 階段不動

    def test_status_shows_the_pause_and_json_has_it(self):
        self.p.air("pause", "--reason", "先趕比賽")
        out = self.p.air("status").stdout
        self.assertIn("遷移暫停中：", out)
        self.assertIn("原因：先趕比賽", out)
        self.assertIn("python3 AIR/air.py resume", out)
        d = json.loads(self.p.air("status", "--json").stdout)
        self.assertEqual((d["paused"]["phase"], d["paused"]["reason"]), ("installed", "先趕比賽"))
        self.assertIn("先趕比賽", d["paused_text"])
        self.assertEqual(self.p.air("resume").returncode, 0)
        self.assertNotIn("遷移暫停中", self.p.air("status").stdout)
        self.assertIsNone(json.loads(self.p.air("status", "--json").stdout)["paused"])

    def test_phase_cannot_move_while_paused(self):
        self.p.write(REPORT, "# 報告\n")
        self.p.air("pause", "--reason", "x")
        r = self.p.air("phase", "analyzed")
        self.assertEqual(r.returncode, 1)
        self.assertIn("暫停中", r.stderr)
        self.assertIn("resume", r.stderr)
        self.assertEqual(self.p.cfg().phase, "installed")
        with self.assertRaises(project.TransitionError):
            project.transition(self.p.cfg(), "installed", rollback=True, reason="r")

    def test_pause_twice_resume_twice_and_done_are_rejected(self):
        self.assertEqual(self.p.air("pause", "--reason", "x").returncode, 0)
        r = self.p.air("pause", "--reason", "y")
        self.assertEqual(r.returncode, 1)
        self.assertIn("已經在暫停中", r.stderr)
        self.assertEqual(project.paused(self.p.root)["reason"], "x")  # 第二次沒有覆蓋
        r = self.p.air("resume")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("階段 installed", r.stdout)
        self.assertFalse((self.p.root / ".air" / "paused.json").exists())
        self.assertEqual([h["how"] for h in self.p.history()], ["pause", "resume"])
        r = self.p.air("resume")
        self.assertEqual(r.returncode, 1)
        self.assertIn("沒有在暫停中", r.stderr)
        project.set_phase(self.p.cfg(), "done")
        r = self.p.air("pause", "--reason", "x")
        self.assertEqual(r.returncode, 1)
        self.assertIn("done", r.stderr)

    def test_pause_record_is_versioned_but_not_hand_editable(self):
        from airkit import install, protect
        self.assertNotIn("paused.json", install.AIR_IGNORE)  # 跟階段一樣屬於專案，進版本控制
        self.p.air("pause", "--reason", "x")
        self.assertEqual(protect.classify(self.p.root / ".air" / "paused.json", self.p.cfg()).action, protect.DENY)
        self.assertEqual(project.paused(self.p.root)["schema"], project.PAUSE_SCHEMA)


class TestTransitionNoTestCommand(ProjectTestCase):
    def test_analyzed_requires_test_command(self):
        self.p.write(REPORT, "# 報告\n")
        r = self.p.air("phase", "analyzed")
        self.assertEqual(r.returncode, 1)
        self.assertIn("測試指令尚未設定", r.stderr)


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
        self.assertEqual(self.p.air("verify").returncode, 0)  # layered 要有新鮮且通過的驗收
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


class TestExclude(ProjectTestCase):
    exclude = '"demo/experiments", "demo/scratch.py"'

    def test_survey_check_and_status_skip_excluded_paths(self):
        self.p.layer("steps", ok="x = 1\n")
        self.p.write("demo/legacy.py", "y = 2\n")
        self.p.write("demo/experiments/try.py", 'print("x")\n')
        self.p.write("demo/scratch.py", "z = 3\n")
        out = project.survey(self.p.cfg())
        self.assertIn("`demo.legacy`", out)
        self.assertNotIn("experiments", out)
        self.assertNotIn("scratch", out)
        status = self.p.air("status").stdout
        self.assertIn("舊程式碼模組 2 個", status)  # demo/__init__.py 與 legacy.py；experiments 與 scratch 不算
        self.assertIn("不歸 AIR 管的路徑（migration.exclude）：demo/experiments、demo/scratch.py", status)
        r = self.p.air("check", "--json")
        self.assertNotIn("experiments", r.stdout)
        self.assertEqual(json.loads(self.p.air("status", "--json").stdout)["exclude"], ["demo/experiments", "demo/scratch.py"])


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
