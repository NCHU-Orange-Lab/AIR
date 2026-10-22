"""安裝與移除：接點正確、可重複執行、只動 AIR 自己的東西、失敗不留下誤導的狀態。"""

import json
import os
import stat
import unittest

from tests.helpers import ProjectTestCase
from airkit import install

HOOK_SCRIPTS = ["pre_bash.sh", "pre_edit.sh", "post_edit.sh", "stop.sh"]
EXPECTED_HOOKS = [("PreToolUse", "Bash"), ("PreToolUse", "Edit|Write|MultiEdit"), ("PostToolUse", "Edit|Write|MultiEdit"), ("Stop", None)]


class TestInstructions(ProjectTestCase):
    def test_root_claude_md_imports_air_agents(self):
        install.ensure_instructions(self.p.root)
        text = (self.p.root / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("@AIR/AGENTS.md", text)
        self.assertIn("先讀 `AIR/AGENTS.md`", (self.p.root / "AGENTS.md").read_text(encoding="utf-8"))

    def test_existing_agents_md_is_imported_when_claude_md_is_new(self):
        self.p.write("AGENTS.md", "# mine\n")
        install.ensure_instructions(self.p.root)
        text = (self.p.root / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertLess(text.index("@AGENTS.md"), text.index("@AIR/AGENTS.md"))
        self.assertTrue((self.p.root / "AGENTS.md").read_text(encoding="utf-8").startswith("# mine\n"))

    def test_dot_claude_claude_md_uses_relative_path_from_its_own_directory(self):
        # @ 匯入是相對於「包含它的檔案」解析（F07）
        self.p.write(".claude/CLAUDE.md", "# mine\n")
        install.ensure_instructions(self.p.root)
        text = (self.p.root / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("@../AIR/AGENTS.md", text)
        self.assertNotIn("@AIR/AGENTS.md", text)
        self.assertFalse((self.p.root / "CLAUDE.md").exists())

    def test_dot_claude_claude_md_with_existing_agents_md(self):
        self.p.write(".claude/CLAUDE.md", "# mine\n")
        self.p.write("AGENTS.md", "# theirs\n")
        install.ensure_instructions(self.p.root)
        text = (self.p.root / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertNotIn("@AGENTS.md\n", text)  # 既有 CLAUDE.md，不需要匯入 AGENTS.md
        self.assertIn("@../AIR/AGENTS.md", text)

    def test_idempotent_and_removable(self):
        self.p.write("CLAUDE.md", "# mine\n")
        install.ensure_instructions(self.p.root)
        before = (self.p.root / "CLAUDE.md").read_text(encoding="utf-8")
        install.ensure_instructions(self.p.root)
        self.assertEqual(before, (self.p.root / "CLAUDE.md").read_text(encoding="utf-8"))
        install.uninstall(self.p.root, purge=False)
        self.assertEqual((self.p.root / "CLAUDE.md").read_text(encoding="utf-8"), "# mine\n")


class TestSettings(ProjectTestCase):
    def settings(self) -> dict:
        return json.loads((self.p.root / ".claude" / "settings.json").read_text(encoding="utf-8"))

    def air_hooks(self, data: dict) -> list[tuple[str, str | None, str]]:
        return [(event, entry.get("matcher"), h["command"]) for event, entries in data.get("hooks", {}).items()
                for entry in entries for h in entry["hooks"] if "AIR/hooks/" in h["command"]]

    def test_four_hooks_with_matchers_and_timeouts(self):
        install.ensure_settings(self.p.root)
        hooks = self.air_hooks(self.settings())
        self.assertEqual([(e, m) for e, m, _ in hooks], EXPECTED_HOOKS)
        for entries in self.settings()["hooks"].values():
            for entry in entries:
                for h in entry["hooks"]:
                    self.assertIn("timeout", h)

    def test_idempotent(self):
        self.assertEqual(install.ensure_settings(self.p.root), "updated")
        self.assertEqual(install.ensure_settings(self.p.root), "unchanged")
        self.assertEqual(len(self.air_hooks(self.settings())), len(EXPECTED_HOOKS))

    def test_user_hook_mentioning_air_is_not_mistaken_for_ours(self):
        # 子字串比對會把使用者的 echo 當成 AIR 的 hook（F09）
        user = {"matcher": "Read", "hooks": [{"type": "command", "command": "echo AIR/hooks/pre_bash.sh"}]}
        self.p.write(".claude/settings.json", json.dumps({"other": True, "hooks": {"PreToolUse": [user]}}))
        install.ensure_settings(self.p.root)
        pre = self.settings()["hooks"]["PreToolUse"]
        self.assertIn("Bash", [e.get("matcher") for e in pre])
        self.assertIn("Read", [e.get("matcher") for e in pre])
        install.remove_settings(self.p.root)
        after = self.settings()
        self.assertEqual(after["hooks"]["PreToolUse"], [user])
        self.assertTrue(after["other"])

    def test_old_format_entry_is_upgraded_not_duplicated(self):
        old = {"matcher": "Bash", "hooks": [{"type": "command", "command": '"$CLAUDE_PROJECT_DIR"/AIR/hooks/pre_bash.sh'}]}
        self.p.write(".claude/settings.json", json.dumps({"hooks": {"PreToolUse": [old]}}))
        install.ensure_settings(self.p.root)
        pre = [h for e in self.settings()["hooks"]["PreToolUse"] for h in e["hooks"] if "pre_bash" in h["command"]]
        self.assertEqual(len(pre), 1)
        self.assertTrue(pre[0]["command"].startswith("sh "))

    def test_remove_cleans_up_empty_structures(self):
        install.ensure_settings(self.p.root)
        install.remove_settings(self.p.root)
        self.assertFalse((self.p.root / ".claude" / "settings.json").exists())


class TestHookScripts(ProjectTestCase):
    git = True

    def setUp(self):
        super().setUp()
        self.p.layer("steps", bad='print("x")\n')
        r = self.p.air("install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for s in HOOK_SCRIPTS:  # 模擬 fresh checkout：腳本沒有執行位元（F08）
            (self.p.root / "AIR" / "hooks" / s).chmod(0o644)

    def test_generated_commands_run_without_exec_bit(self):
        data = json.loads((self.p.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        commands = [h["command"] for entries in data["hooks"].values() for e in entries for h in e["hooks"]]
        self.assertEqual(len(commands), len(EXPECTED_HOOKS))
        for cmd in commands:
            with self.subTest(cmd=cmd):
                r = self.p.sh(cmd, stdin="{}", env={"CLAUDE_PROJECT_DIR": str(self.p.root)})
                self.assertNotIn("Permission denied", r.stderr)
                self.assertNotIn("not found", r.stderr)
                self.assertIn(r.returncode, (0, 2))

    def test_scripts_find_project_root_without_env_and_from_subdirectory(self):
        # 不依賴 $CLAUDE_PROJECT_DIR，也不依賴 cwd（A3）
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        import subprocess
        r = subprocess.run(["sh", str(self.p.root / "AIR" / "hooks" / "stop.sh")], cwd=self.p.root / "demo",
                           input="{}", capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("AIR001", r.stderr)

    def test_commit_check_blocks_commit(self):
        import subprocess
        subprocess.run(["git", "add", "-A"], cwd=self.p.root, check=True)
        r = subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=self.p.root, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("AIR001", r.stdout + r.stderr)


class TestFullInstall(ProjectTestCase):
    git = True

    def test_install_reports_and_is_idempotent(self):
        r = self.p.air("install")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("安裝完成", r.stdout)
        self.assertTrue((self.p.root / ".air" / "python").exists())
        self.assertTrue((self.p.root / ".claude" / "skills" / "air-migrate" / ".air-managed").exists())
        self.assertTrue(os.access(self.p.root / ".git" / "hooks" / "pre-commit", os.X_OK))
        r2 = self.p.air("install")
        self.assertEqual(r2.returncode, 0)
        self.assertIn("unchanged", r2.stdout)

    def test_invalid_settings_json_does_not_abort_the_rest(self):
        # 中途失敗不能留下「看起來裝好一半」的狀態（F10）：其他接點照做，失敗的列出來
        self.p.write(".claude/settings.json", "{invalid json")
        r = self.p.air("install")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("失敗", r.stdout)
        self.assertIn("settings.json", r.stdout)
        self.assertNotIn("安裝完成", r.stdout)
        self.assertTrue((self.p.root / ".git" / "hooks" / "pre-commit").exists())
        self.assertTrue((self.p.root / "CLAUDE.md").exists())
        self.assertNotIn("Traceback", r.stderr)

    @unittest.skipIf(os.geteuid() == 0, "root 不受檔案權限限制")
    def test_permission_error_is_reported_not_crashed(self):
        hooks_dir = self.p.root / ".git" / "hooks"
        hooks_dir.mkdir(exist_ok=True)
        hooks_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(hooks_dir.chmod, 0o755)
        r = self.p.air("install")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("失敗", r.stdout)
        self.assertIn("權限", r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        self.assertTrue((self.p.root / ".claude" / "settings.json").exists())

    def test_uninstall_restores(self):
        self.p.write("CLAUDE.md", "# mine\n")
        self.p.air("install")
        r = self.p.air("uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.p.root / "CLAUDE.md").read_text(encoding="utf-8"), "# mine\n")
        self.assertFalse((self.p.root / ".claude" / "settings.json").exists())
        self.assertFalse((self.p.root / ".git" / "hooks" / "pre-commit").exists())
        self.assertTrue((self.p.root / ".air" / "project.toml").exists())


class TestRules(ProjectTestCase):
    def test_rules_copied_with_managed_header_and_user_files_skipped(self):
        self.p.layer("steps")
        self.p.layer("flows")
        self.p.write("demo/flows/CLAUDE.md", "# user's own\n")
        out = install.refresh_rules(self.p.cfg())
        self.assertTrue((self.p.root / "demo" / "steps" / "CLAUDE.md").read_text(encoding="utf-8").startswith("<!-- AIR:MANAGED"))
        self.assertEqual((self.p.root / "demo" / "flows" / "CLAUDE.md").read_text(encoding="utf-8"), "# user's own\n")
        self.assertTrue(any("略過" in line and "flows" in line for line in out))


if __name__ == "__main__":
    unittest.main()
