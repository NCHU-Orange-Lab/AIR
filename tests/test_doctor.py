"""air doctor：接線診斷。裝好的專案沒有問題；少了 hook、skill 過期、舊版規則檔、override 都要被指出；只讀。"""

import json
import os
import unittest

from tests.helpers import ProjectTestCase
from airkit import doctor, hosts, install


class TestDoctor(ProjectTestCase):
    git = True

    def setUp(self):
        super().setUp()
        self.p.layer("steps", ok="x = 1\n")
        self.assertEqual(self.p.air("install").returncode, 0)

    def levels(self) -> dict[str, list[str]]:
        items = doctor.diagnose(self.p.root)
        return {"fail": [i.text for i in items if i.level == doctor.FAIL], "warn": [i.text for i in items if i.level == doctor.WARN]}

    def test_fresh_install_has_no_problems(self):
        r = self.p.air("doctor")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.levels()["fail"], [])
        self.assertIn("[OK] Claude Code：.claude/settings.json 有 5 個 AIR hook", r.stdout)
        self.assertIn("[OK] Codex：.codex/hooks.json 有 5 個 AIR hook", r.stdout)
        self.assertIn("[OK] Claude Code：.claude/rules/ 有 7 份各層規則（讀寫該層的檔案時由 Claude Code 自動載入）", r.stdout)
        self.assertNotIn("Codex：.agents/rules", r.stdout)
        self.assertIn("[OK] Claude Code：hook 冒煙測試：5 個都能執行", r.stdout)
        self.assertIn("[OK] Codex：hook 冒煙測試：5 個都能執行", r.stdout)
        self.assertIn("[OK] commit 前檢查", r.stdout)
        self.assertTrue(any("信任" in w for w in self.levels()["warn"]))  # Codex 的 hook 要信任，AIR 無法確認

    def set_project(self, old: str, new: str) -> None:
        path = self.p.root / ".air" / "project.toml"
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def dir_warnings(self) -> list[str]:
        return [i.text for i in doctor.diagnose(self.p.root) if i.area == "測試目錄"]

    def test_tests_dir_missing_while_tests_live_in_the_package_is_a_warning(self):
        self.assertEqual(self.dir_warnings(), [])                      # 沒有測試、tests-dir 不存在：不吵
        self.p.write("demo/tests/test_a.py", "")
        (w,) = self.dir_warnings()
        self.assertIn("tests-dir = 'tests' 不存在，但 demo/tests/ 有測試", w)
        self.assertIn("改成 'demo/tests'", w)
        self.set_project('tests-dir = "tests"', 'tests-dir = "demo/tests"')
        self.assertEqual(self.dir_warnings(), [])

    def test_exclude_covering_tests_dir_is_a_warning(self):
        self.p.write("demo/tests/test_a.py", "")
        self.set_project('tests-dir = "tests"', 'tests-dir = "demo/tests"')
        self.set_project("exclude = [", 'exclude = ["demo/tests", ')
        (w,) = self.dir_warnings()
        self.assertIn("migration.exclude 的 'demo/tests' 蓋住了 tests-dir（demo/tests）", w)
        self.assertIn("不凍結、不進驗收指紋", w)
        self.set_project('exclude = ["demo/tests", ', 'exclude = ["demo/experiments", ')   # 排除別的路徑：不提醒
        self.assertEqual(self.dir_warnings(), [])
        self.set_project('exclude = ["demo/experiments", ', 'exclude = ["demo/test", ')    # demo/test 不是 demo/tests 的上層，只是前綴相同
        self.assertEqual(self.dir_warnings(), [])

    def test_json(self):
        data = json.loads(self.p.air("doctor", "--json").stdout)
        self.assertEqual((data["schema"], data["ok"], data["problems"]), ("air.doctor.v1", True, 0))
        self.assertTrue(all({"level", "area", "text"} <= set(i) for i in data["items"]))

    def test_removed_hook_is_a_problem_with_exit_1(self):
        settings = self.p.root / ".claude" / "settings.json"
        data = json.loads(settings.read_text(encoding="utf-8"))
        del data["hooks"]["Stop"]
        settings.write_text(json.dumps(data), encoding="utf-8")
        r = self.p.air("doctor")
        self.assertEqual(r.returncode, 1)
        self.assertIn("缺少 AIR 的 hook：Stop/stop.sh", r.stdout)

    def test_deleted_hooks_file_of_installed_host_is_a_problem(self):
        (self.p.root / ".codex" / "hooks.json").unlink()
        self.assertTrue(any("不見了" in f for f in self.levels()["fail"]))

    def test_host_not_installed_is_a_note_not_a_problem(self):
        self.p.air("uninstall")
        self.assertEqual(self.p.air("install", "--host", "claude").returncode, 0)
        levels = self.levels()
        self.assertEqual(levels["fail"], [])
        self.assertTrue(any("未安裝" in w and "codex" in w for w in levels["warn"]))

    def test_missing_rule_file_is_a_problem_stale_or_foreign_is_a_note(self):
        rules = self.p.root / ".claude" / "rules"
        (rules / "air-steps.md").unlink()
        levels = self.levels()
        self.assertTrue(any("缺少各層的規則檔：air-steps.md" in f for f in levels["fail"]))
        self.assertEqual(self.p.air("doctor").returncode, 1)
        self.assertEqual(self.p.air("install").returncode, 0)  # 修法就是重跑安裝
        self.assertEqual(self.levels()["fail"], [])
        flows = rules / "air-flows.md"
        flows.write_text(flows.read_text(encoding="utf-8").replace("FLOW-1", "FLOW-9"), encoding="utf-8")
        self.p.write(".claude/rules/air-main.md", "# 不是 AIR 的\n")
        levels = self.levels()
        self.assertEqual(levels["fail"], [])
        self.assertTrue(any("air-flows.md 不是目前版本" in w for w in levels["warn"]))
        self.assertTrue(any("air-main.md 不是 AIR 建立的" in w for w in levels["warn"]))

    def test_retired_read_hook_is_one_note_not_three_problems(self):
        # 2.6.1 以前的 settings.json 還有 pre_read.sh：腳本已經不在套件裡，不該變成「指向不存在的腳本」加冒煙測試失敗
        settings = self.p.root / ".claude" / "settings.json"
        data = json.loads(settings.read_text(encoding="utf-8"))
        data["hooks"]["PreToolUse"].append({"matcher": "Read", "hooks": [{"type": "command", "command": hosts.hook_command(hosts.CLAUDE, "pre_read.sh"), "timeout": 30}]})
        settings.write_text(json.dumps(data), encoding="utf-8")
        levels = self.levels()
        self.assertEqual(levels["fail"], [])
        notes = [w for w in levels["warn"] if "pre_read.sh" in w]
        self.assertEqual(len(notes), 1, levels["warn"])
        self.assertIn("這一版不再使用", notes[0])
        self.assertIn("會移除", notes[0])
        self.assertEqual(self.p.air("install").returncode, 0)
        self.assertFalse(any("pre_read.sh" in w for w in self.levels()["warn"]))

    def test_old_test_command_keys_are_a_note(self):
        self.assertFalse(any("test-command" in w for w in self.levels()["warn"]))
        toml = self.p.root / ".air" / "project.toml"
        toml.write_text(toml.read_text(encoding="utf-8").replace("[tests]\ncommand", "[tests]\n#command")
                        .replace('tests-dir = "tests"', 'tests-dir = "tests"\ntest-command = "pytest -q"'), encoding="utf-8")
        levels = self.levels()
        self.assertEqual(levels["fail"], [])
        self.assertTrue(any("project.test-command" in w and "[tests]" in w for w in levels["warn"]))

    def test_legacy_hook_form_is_a_note(self):
        settings = self.p.root / ".claude" / "settings.json"
        data = json.loads(settings.read_text(encoding="utf-8"))
        data["hooks"]["Stop"][0]["hooks"][0]["command"] = '"$CLAUDE_PROJECT_DIR"/AIR/hooks/stop.sh'
        settings.write_text(json.dumps(data), encoding="utf-8")
        levels = self.levels()
        self.assertEqual(levels["fail"], [])
        self.assertTrue(any("舊版" in w and "stop.sh" in w for w in levels["warn"]))

    def test_stale_skill_is_a_note(self):
        (self.p.root / ".claude" / "skills" / "air-migrate" / "SKILL.md").write_text("舊\n", encoding="utf-8")
        self.assertTrue(any("air-migrate" in w and "不是目前版本" in w for w in self.levels()["warn"]))

    def test_foreign_skill_is_a_note(self):
        self.p.air("uninstall")
        self.p.write(".claude/skills/air-migrate/SKILL.md", "mine\n")
        self.p.air("install")
        self.assertTrue(any("不是 AIR 建立的" in w for w in self.levels()["warn"]))

    def test_stale_agents_block_is_a_note(self):
        agents = self.p.root / "AGENTS.md"
        agents.write_text(agents.read_text(encoding="utf-8").replace("## 架構", "## 舊架構"), encoding="utf-8")
        self.assertTrue(any("AGENTS.md" in w and "不是目前版本" in w for w in self.levels()["warn"]))

    def test_agents_override_is_a_note(self):
        self.p.write("AGENTS.override.md", "# override\n")
        self.assertTrue(any("AGENTS.override.md" in w for w in self.levels()["warn"]))

    def test_broken_import_in_claude_md_is_a_problem(self):
        claude = self.p.root / "CLAUDE.md"
        claude.write_text(claude.read_text(encoding="utf-8").replace("@AGENTS.md", "@nowhere.md"), encoding="utf-8")
        self.assertTrue(any("匯入的檔案不存在" in f for f in self.levels()["fail"]))

    def test_legacy_rule_files_are_a_note(self):
        self.p.write("demo/steps/CLAUDE.md", f"<!-- {install.MANAGED} 這個檔案由 AIR 產生 -->\n# 舊規則\n")
        self.assertTrue(any("舊版留下的規則檔" in w and "demo/steps/CLAUDE.md" in w for w in self.levels()["warn"]))

    def test_missing_python_file_is_a_note(self):
        (self.p.root / ".air" / "python").unlink()
        self.assertTrue(any("還沒跑過 install.sh" in w for w in self.levels()["warn"]))

    def test_tampered_kit_is_a_problem(self):
        path = self.p.root / "AIR" / "config.toml"
        path.write_text(path.read_text(encoding="utf-8") + "\n# x\n", encoding="utf-8")
        self.assertTrue(any("AIR/config.toml" in f for f in self.levels()["fail"]))

    def test_broken_hook_script_is_caught_by_smoke_test(self):
        # 宿主對 0、2 以外的結束碼一律放行（Windows 找不到 Python 時就是 exit 49）：只看設定抓不到，要真的跑一次
        (self.p.root / "AIR" / "hooks" / "stop.sh").write_text("#!/bin/sh\nexit 49\n", encoding="utf-8")
        fails = self.levels()["fail"]
        self.assertTrue(any("hook stop.sh 跑不起來（exit 49" in f for f in fails), fails)

    def test_hook_output_that_is_not_utf8_json_is_caught_by_smoke_test(self):
        (self.p.root / "AIR" / "hooks" / "session_start.sh").write_text("#!/bin/sh\nprintf '{\"a\": \"\\260\\241\"}'\n", encoding="utf-8")
        fails = self.levels()["fail"]
        self.assertTrue(any("session_start.sh 的輸出不是合法的 UTF-8 JSON" in f for f in fails), fails)

    def test_recent_hook_errors_are_a_note(self):
        from airkit import hooklog
        hooklog.log(self.p.root, "hook stop", "ValueError：x")
        self.assertTrue(any("hook 失敗 1 次" in w and "hook stop：ValueError：x" in w for w in self.levels()["warn"]))

    def test_not_installed_project_reports_config_problem_but_runs(self):
        (self.p.root / ".air" / "project.toml").unlink()
        r = self.p.air("doctor")
        self.assertEqual(r.returncode, 1)
        self.assertIn("找不到 .air/project.toml", r.stdout)
        self.assertIn("commit 前檢查", r.stdout)

    def test_doctor_is_read_only(self):
        before = {p: p.stat().st_mtime for p in self.p.root.rglob("*") if p.is_file() and ".git" not in p.parts}
        self.p.air("doctor")
        after = {p: p.stat().st_mtime for p in self.p.root.rglob("*") if p.is_file() and ".git" not in p.parts}
        self.assertEqual(before, after)

    def test_precommit_config_without_pre_commit_install_is_a_note(self):
        self.p.air("uninstall")
        self.p.write(".pre-commit-config.yaml", "repos:\n  - repo: local\n    hooks: []\n")
        self.p.air("install")
        self.assertTrue(any("pre-commit install" in w for w in self.levels()["warn"]))


class TestDoctorHosts(unittest.TestCase):
    def test_all_hosts_are_diagnosed(self):
        from tests.helpers import Project
        p = Project(git=True)
        try:
            p.air("install")
            areas = {i.area for i in doctor.diagnose(p.root)}
            for host in hosts.HOSTS.values():
                self.assertIn(host.label, areas)
        finally:
            p.cleanup()


if __name__ == "__main__":
    unittest.main()
