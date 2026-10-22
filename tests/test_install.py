"""安裝與移除：接點正確、可重複執行、只動 AIR 自己的東西、失敗不留下誤導的狀態。"""

import json
import os
import stat
import unittest

from tests.helpers import ProjectTestCase
from airkit import hosts, install

HOOK_SCRIPTS = ["session_start.sh", "pre_bash.sh", "pre_read.sh", "pre_edit.sh", "post_edit.sh", "stop.sh"]
EXPECTED_HOOKS = [("SessionStart", None), ("PreToolUse", "Bash"), ("PreToolUse", "Read"), ("PreToolUse", "Edit|Write|MultiEdit"),
                  ("PostToolUse", "Edit|Write|MultiEdit"), ("Stop", None)]


class TestInstructions(ProjectTestCase):
    def test_new_claude_md_imports_agents_md_which_holds_the_full_rules(self):
        install.ensure_instructions(self.p.root)
        text = (self.p.root / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("@AGENTS.md", text)
        self.assertNotIn("@AIR/AGENTS.md", text)  # 常駐規則全文在 AGENTS.md，不匯入兩份
        agents = (self.p.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("# AIR 規範（常駐）", agents)
        self.assertIn(install.agents_body(), agents)

    def test_existing_agents_md_keeps_its_content_and_gets_the_block(self):
        self.p.write("AGENTS.md", "# mine\n")
        install.ensure_instructions(self.p.root)
        self.assertIn("@AGENTS.md", (self.p.root / "CLAUDE.md").read_text(encoding="utf-8"))
        agents = (self.p.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertTrue(agents.startswith("# mine\n"))
        self.assertIn("AIR:BEGIN", agents)

    def test_users_own_claude_md_imports_air_agents_directly(self):
        self.p.write("CLAUDE.md", "# mine\n")
        install.ensure_instructions(self.p.root)
        text = (self.p.root / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# mine\n"))
        self.assertIn("@AIR/AGENTS.md", text)
        self.assertNotIn("@AGENTS.md\n", text)

    def test_dot_claude_claude_md_uses_relative_path_from_its_own_directory(self):
        # @ 匯入是相對於「包含它的檔案」解析（F07）
        self.p.write(".claude/CLAUDE.md", "# mine\n")
        install.ensure_instructions(self.p.root)
        text = (self.p.root / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("@../AIR/AGENTS.md", text)
        self.assertNotIn("@AIR/AGENTS.md", text)
        self.assertFalse((self.p.root / "CLAUDE.md").exists())

    def test_agents_block_is_refreshed_when_kit_text_changes(self):
        install.ensure_instructions(self.p.root)
        agents = self.p.root / "AGENTS.md"
        agents.write_text(agents.read_text(encoding="utf-8").replace("## 架構", "## 舊架構"), encoding="utf-8")
        out = install.ensure_instructions(self.p.root)
        self.assertIn("AGENTS.md：updated", out)
        self.assertIn("## 架構", agents.read_text(encoding="utf-8"))

    def test_agents_override_warns(self):
        self.p.write("AGENTS.override.md", "# o\n")
        self.assertTrue(any("AGENTS.override.md" in line for line in install.ensure_instructions(self.p.root)))

    def test_idempotent_and_removable(self):
        self.p.write("CLAUDE.md", "# mine\n")
        self.p.write("AGENTS.md", "# theirs\n")
        install.ensure_instructions(self.p.root)
        before = [(p.name, p.read_text(encoding="utf-8")) for p in (self.p.root / "CLAUDE.md", self.p.root / "AGENTS.md")]
        self.assertTrue(all(s.endswith("unchanged") for s in install.ensure_instructions(self.p.root)))
        self.assertEqual(before, [(p.name, p.read_text(encoding="utf-8")) for p in (self.p.root / "CLAUDE.md", self.p.root / "AGENTS.md")])
        install.uninstall(self.p.root, purge=False)
        self.assertEqual((self.p.root / "CLAUDE.md").read_text(encoding="utf-8"), "# mine\n")
        self.assertEqual((self.p.root / "AGENTS.md").read_text(encoding="utf-8"), "# theirs\n")

    def test_air_created_files_are_removed_entirely(self):
        install.ensure_instructions(self.p.root)
        install.uninstall(self.p.root, purge=False)
        self.assertFalse((self.p.root / "CLAUDE.md").exists())
        self.assertFalse((self.p.root / "AGENTS.md").exists())


class TestSettings(ProjectTestCase):
    def settings(self) -> dict:
        return json.loads((self.p.root / ".claude" / "settings.json").read_text(encoding="utf-8"))

    def air_hooks(self, data: dict) -> list[tuple[str, str | None, str]]:
        return [(event, entry.get("matcher"), h["command"]) for event, entries in data.get("hooks", {}).items()
                for entry in entries for h in entry["hooks"] if "AIR/hooks/" in h["command"]]

    def test_six_hooks_with_matchers_timeouts_and_host_argument(self):
        install.ensure_hooks(self.p.root, hosts.CLAUDE)
        hooks_ = self.air_hooks(self.settings())
        self.assertEqual([(e, m) for e, m, _ in hooks_], EXPECTED_HOOKS)
        for _, _, command in hooks_:
            self.assertTrue(command.endswith(" claude"), command)
            self.assertIn('"$CLAUDE_PROJECT_DIR"', command)
        for entries in self.settings()["hooks"].values():
            for entry in entries:
                for h in entry["hooks"]:
                    self.assertIn("timeout", h)
        self.assertNotIn("description", self.settings())  # settings.json 不加 Codex 才有的欄位

    def test_idempotent(self):
        self.assertEqual(install.ensure_hooks(self.p.root, hosts.CLAUDE), "updated")
        self.assertEqual(install.ensure_hooks(self.p.root, hosts.CLAUDE), "unchanged")
        self.assertEqual(len(self.air_hooks(self.settings())), len(EXPECTED_HOOKS))

    def test_user_hook_mentioning_air_is_not_mistaken_for_ours(self):
        # 子字串比對會把使用者的 echo 當成 AIR 的 hook（F09）
        user = {"matcher": "Read", "hooks": [{"type": "command", "command": "echo AIR/hooks/pre_bash.sh"}]}
        self.p.write(".claude/settings.json", json.dumps({"other": True, "hooks": {"PreToolUse": [user]}}))
        install.ensure_hooks(self.p.root, hosts.CLAUDE)
        pre = self.settings()["hooks"]["PreToolUse"]
        self.assertIn("Bash", [e.get("matcher") for e in pre])
        self.assertIn(user, pre)
        install.remove_hooks(self.p.root, hosts.CLAUDE)
        after = self.settings()
        self.assertEqual(after["hooks"]["PreToolUse"], [user])
        self.assertTrue(after["other"])

    def test_old_format_entries_are_upgraded_not_duplicated(self):
        old = [{"matcher": "Bash", "hooks": [{"type": "command", "command": '"$CLAUDE_PROJECT_DIR"/AIR/hooks/pre_bash.sh'}]},
               {"matcher": "Edit|Write|MultiEdit", "hooks": [{"type": "command", "command": 'sh "$CLAUDE_PROJECT_DIR"/AIR/hooks/pre_edit.sh', "timeout": 30}]}]
        self.p.write(".claude/settings.json", json.dumps({"hooks": {"PreToolUse": old}}))
        install.ensure_hooks(self.p.root, hosts.CLAUDE)
        pre = [h["command"] for e in self.settings()["hooks"]["PreToolUse"] for h in e["hooks"]]
        self.assertEqual(sum("pre_bash" in c for c in pre), 1)
        self.assertEqual(sum("pre_edit" in c for c in pre), 1)
        self.assertTrue(all(c.startswith("sh ") and c.endswith(" claude") for c in pre))

    def test_remove_cleans_up_empty_structures(self):
        install.ensure_hooks(self.p.root, hosts.CLAUDE)
        install.remove_hooks(self.p.root, hosts.CLAUDE)
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
                self.assertNotIn("內部錯誤", r.stderr)
                self.assertIn(r.returncode, (0, 2))

    def test_codex_commands_find_the_root_via_git_from_a_subdirectory(self):
        data = json.loads((self.p.root / ".codex" / "hooks.json").read_text(encoding="utf-8"))
        commands = [h["command"] for entries in data["hooks"].values() for e in entries for h in e["hooks"]]
        self.assertEqual(len(commands), len(hosts.CODEX.scripts))
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        stop = next(c for c in commands if "stop.sh" in c)
        import subprocess
        r = subprocess.run(stop, shell=True, cwd=self.p.root / "demo", input="{}", capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("AIR001", r.stderr)

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
        self.assertEqual((self.p.root / ".air" / "hosts").read_text(encoding="utf-8").split(), ["claude", "codex"])
        self.assertTrue((self.p.root / ".claude" / "skills" / "air-migrate" / ".air-managed").exists())
        self.assertTrue((self.p.root / ".agents" / "skills" / "air-migrate" / ".air-managed").exists())
        self.assertTrue((self.p.root / ".codex" / "hooks.json").exists())
        self.assertIn("信任", r.stdout)  # Codex 的 hook 要使用者信任
        self.assertTrue(os.access(self.p.root / ".git" / "hooks" / "pre-commit", os.X_OK))
        ignore = (self.p.root / ".air" / ".gitignore").read_text(encoding="utf-8").split()
        for name in ("python", "hosts", "verify.json", "sessions"):
            self.assertIn(name, ignore)
        r2 = self.p.air("install")
        self.assertEqual(r2.returncode, 0)
        self.assertIn("unchanged", r2.stdout)

    def test_host_option_limits_what_is_installed(self):
        r = self.p.air("install", "--host", "claude")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.p.root / ".codex").exists())
        self.assertFalse((self.p.root / ".agents").exists())
        self.assertEqual((self.p.root / ".air" / "hosts").read_text(encoding="utf-8").split(), ["claude"])

    def test_invalid_settings_json_does_not_abort_the_rest(self):
        # 中途失敗不能留下「看起來裝好一半」的狀態（F10）：其他接點照做，失敗的列出來
        self.p.write(".claude/settings.json", "{invalid json")
        r = self.p.air("install")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("失敗", r.stdout)
        self.assertIn("settings.json", r.stdout)
        self.assertNotIn("安裝完成", r.stdout)
        self.assertTrue((self.p.root / ".git" / "hooks" / "pre-commit").exists())
        self.assertTrue((self.p.root / ".codex" / "hooks.json").exists())
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
        self.assertFalse((self.p.root / "AGENTS.md").exists())
        self.assertFalse((self.p.root / ".claude" / "settings.json").exists())
        self.assertFalse((self.p.root / ".codex").exists())
        self.assertFalse((self.p.root / ".agents").exists())
        self.assertFalse((self.p.root / ".air" / "hosts").exists())
        self.assertFalse((self.p.root / ".git" / "hooks" / "pre-commit").exists())
        self.assertTrue((self.p.root / ".air" / "project.toml").exists())
        self.assertEqual(self.p.air("check").returncode, 0)  # 移除後不會因為接點不在而報 AIR009


class TestLegacyRuleFiles(ProjectTestCase):
    git = True

    def test_install_removes_old_managed_rule_files_but_keeps_users(self):
        self.p.layer("steps")
        self.p.layer("flows")
        self.p.write("demo/steps/CLAUDE.md", f"<!-- {install.MANAGED} 這個檔案由 AIR 產生 -->\n# 舊規則\n")
        self.p.write("demo/flows/CLAUDE.md", "# user's own\n")
        r = self.p.air("install")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("移除 demo/steps/CLAUDE.md", r.stdout)
        self.assertFalse((self.p.root / "demo" / "steps" / "CLAUDE.md").exists())
        self.assertEqual((self.p.root / "demo" / "flows" / "CLAUDE.md").read_text(encoding="utf-8"), "# user's own\n")

    def test_layers_command_no_longer_writes_rule_files(self):
        self.assertEqual(self.p.air("layers").returncode, 0)
        self.assertTrue((self.p.root / "demo" / "steps" / "__init__.py").exists())
        self.assertFalse((self.p.root / "demo" / "steps" / "CLAUDE.md").exists())


if __name__ == "__main__":
    unittest.main()
