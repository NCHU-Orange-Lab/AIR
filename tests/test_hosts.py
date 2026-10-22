"""宿主接點（hosts.py）：Codex 的 .codex/hooks.json 與 .agents/skills/、payload 正規化、宿主聽不懂的回應要換成它聽得懂的。"""

import json
import unittest
from pathlib import Path

from tests.helpers import ProjectTestCase
from airkit import hosts, install

CODEX_HOOKS = [("SessionStart", None), ("PreToolUse", "Bash"), ("PreToolUse", "Edit|Write|MultiEdit"), ("PostToolUse", "Edit|Write|MultiEdit"), ("Stop", None)]


def codex_patch(cwd, *headers: str, session: str = "c1") -> dict:
    body = "\n".join(f"*** {h}" for h in headers)
    return {"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "session_id": session, "cwd": str(cwd),
            "tool_input": {"command": f"*** Begin Patch\n{body}\n*** End Patch"}}


class TestCodexHooksFile(ProjectTestCase):
    def data(self) -> dict:
        return json.loads((self.p.root / ".codex" / "hooks.json").read_text(encoding="utf-8"))

    def entries(self, data: dict) -> list[tuple[str, str | None, str]]:
        return [(event, entry.get("matcher"), h["command"]) for event, entries in data.get("hooks", {}).items()
                for entry in entries for h in entry["hooks"] if install.is_air_hook(h)]

    def test_only_description_and_hooks_at_top_level(self):
        # Codex 的 hooks.json 最外層只允許這兩個欄位，多了會解析失敗
        install.ensure_hooks(self.p.root, hosts.CODEX)
        data = self.data()
        self.assertEqual(set(data), {"description", "hooks"})
        self.assertIn("AIR", data["description"])

    def test_codex_hooks_use_git_toplevel_not_claude_env_and_pass_host_name(self):
        install.ensure_hooks(self.p.root, hosts.CODEX)
        entries = self.entries(self.data())
        self.assertEqual([(e, m) for e, m, _ in entries], CODEX_HOOKS)
        for _, _, command in entries:
            with self.subTest(command=command):
                self.assertNotIn("CLAUDE_PROJECT_DIR", command)
                self.assertIn("git rev-parse --show-toplevel", command)
                self.assertTrue(command.startswith("sh "))
                self.assertTrue(command.endswith(" codex"))

    def test_no_read_hook_for_codex(self):
        install.ensure_hooks(self.p.root, hosts.CODEX)
        self.assertFalse(any("pre_read" in c for _, _, c in self.entries(self.data())))
        self.assertIn("pre_read.sh", hosts.CLAUDE.scripts)

    def test_every_hook_has_timeout(self):
        install.ensure_hooks(self.p.root, hosts.CODEX)
        for entries in self.data()["hooks"].values():
            for entry in entries:
                for h in entry["hooks"]:
                    self.assertIn("timeout", h)

    def test_idempotent_and_merges_with_user_entries(self):
        user = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}
        self.p.write(".codex/hooks.json", json.dumps({"description": "theirs", "hooks": {"PreToolUse": [user]}}))
        self.assertEqual(install.ensure_hooks(self.p.root, hosts.CODEX), "updated")
        self.assertEqual(install.ensure_hooks(self.p.root, hosts.CODEX), "unchanged")
        data = self.data()
        self.assertEqual(data["description"], "theirs")
        self.assertIn(user, data["hooks"]["PreToolUse"])
        self.assertEqual(len(self.entries(data)), len(CODEX_HOOKS))

    def test_remove_keeps_user_entries_and_deletes_file_when_ours_only(self):
        user = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}
        self.p.write(".codex/hooks.json", json.dumps({"description": "theirs", "hooks": {"PreToolUse": [user]}}))
        install.ensure_hooks(self.p.root, hosts.CODEX)
        self.assertTrue(install.remove_hooks(self.p.root, hosts.CODEX))
        self.assertEqual(self.data(), {"description": "theirs", "hooks": {"PreToolUse": [user]}})
        (self.p.root / ".codex" / "hooks.json").unlink()
        install.ensure_hooks(self.p.root, hosts.CODEX)
        install.remove_hooks(self.p.root, hosts.CODEX)
        self.assertFalse((self.p.root / ".codex").exists())  # 整個檔案是 AIR 建立的，連目錄一起清掉

    def test_claude_form_in_codex_file_is_present_but_stale(self):
        # 使用者把 Claude Code 的設定複製到 Codex：hook 齊全（不是 AIR009），但 doctor 要指出格式不對
        self.p.write(".codex/hooks.json", json.dumps({"hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": hosts.hook_command(hosts.CLAUDE, "stop.sh")}]}]}}))
        data = install.read_hooks(self.p.root / ".codex" / "hooks.json")
        self.assertNotIn("Stop/stop.sh", install.missing_hooks(data, hosts.CODEX))
        self.assertEqual(install.stale_hooks(data, hosts.CODEX), ["stop.sh"])


class TestSkillsPerHost(ProjectTestCase):
    def test_codex_skills_go_to_agents_dir_with_mark(self):
        installed, skipped = install.ensure_skills(self.p.root, hosts.CODEX)
        self.assertEqual(installed, install.skill_names())
        self.assertEqual(skipped, [])
        self.assertTrue((self.p.root / ".agents" / "skills" / "air-migrate" / ".air-managed").exists())
        self.assertFalse((self.p.root / ".claude").exists())

    def test_remove_skills_prunes_empty_agents_dir(self):
        install.ensure_skills(self.p.root, hosts.CODEX)
        self.assertEqual(len(install.remove_skills(self.p.root, hosts.CODEX)), len(install.skill_names()))
        self.assertFalse((self.p.root / ".agents").exists())

    def test_skill_differs_detects_stale_copy(self):
        install.ensure_skills(self.p.root, hosts.CODEX)
        dest = self.p.root / ".agents" / "skills" / "air-migrate"
        self.assertFalse(install.skill_differs(self.p.root / "AIR" / "skills" / "air-migrate", dest))
        (dest / "SKILL.md").write_text("舊版\n", encoding="utf-8")
        self.assertTrue(install.skill_differs(self.p.root / "AIR" / "skills" / "air-migrate", dest))


class TestParse(unittest.TestCase):
    def test_claude_edit(self):
        e = hosts.parse({"session_id": "s", "tool_name": "Edit", "tool_input": {"file_path": "/x/a.py"}})
        self.assertEqual((e.host.name, e.session_id, e.tool, e.files), ("claude", "s", "Edit", (Path("/x/a.py"),)))
        self.assertFalse(e.stop_active)

    def test_codex_patch_relative_paths_use_payload_cwd(self):
        patch = "*** Begin Patch\n*** Add File: a.py\n*** Update File: b/c.py\n*** Delete File: d.py\n*** Move to: e.py\n*** End Patch"
        e = hosts.parse({"tool_name": "apply_patch", "cwd": "/root", "tool_input": {"command": patch}}, "codex")
        self.assertEqual(e.host, hosts.CODEX)
        self.assertEqual(e.files, (Path("/root/a.py"), Path("/root/b/c.py"), Path("/root/d.py"), Path("/root/e.py")))

    def test_grok_camel_case_fields(self):
        e = hosts.parse({"sessionId": "g", "toolName": "search_replace", "stopHookActive": True, "tool_input": {"file_path": "/x.py"}})
        self.assertEqual((e.session_id, e.tool, e.stop_active), ("g", "search_replace", True))

    def test_missing_fields_are_empty_not_guessed(self):
        e = hosts.parse({})
        self.assertEqual((e.session_id, e.tool, e.files, e.command, e.stop_active, e.source), ("", "", (), "", False, ""))

    def test_bad_shapes_raise(self):
        with self.assertRaises(ValueError):
            hosts.parse({"tool_input": "nope"})
        with self.assertRaises(ValueError):
            hosts.parse(["not", "a", "dict"])  # type: ignore[arg-type]

    def test_air_commands_cover_every_host_script_and_legacy_forms(self):
        known = hosts.air_commands()
        for host in hosts.HOSTS.values():
            for script in host.scripts:
                self.assertEqual(known[hosts.hook_command(host, script)], (host, script))
        self.assertEqual(known['sh "$CLAUDE_PROJECT_DIR"/AIR/hooks/stop.sh'], (None, "stop.sh"))
        self.assertEqual(known['"$CLAUDE_PROJECT_DIR"/AIR/hooks/stop.sh'], (None, "stop.sh"))


class TestHostSpecificResponses(ProjectTestCase):
    phase = "migrating"

    def test_codex_cannot_ask_so_project_toml_edit_becomes_a_reminder(self):
        r = self.p.hook("pre-edit", codex_patch(self.p.root, "Update File: .air/project.toml"), host="codex")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertNotIn("permissionDecision", out)
        self.assertIn("需要使用者同意", out["additionalContext"])
        self.assertIn("Codex", out["additionalContext"])

    def test_claude_asks(self):
        r = self.p.hook("pre-edit", {"tool_input": {"file_path": str(self.p.root / ".air" / "project.toml")}}, host="claude")
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], "ask")

    def test_codex_deny_is_still_exit_2(self):
        r = self.p.hook("pre-edit", codex_patch(self.p.root, "Update File: AIR/config.toml"), host="codex")
        self.assertEqual(r.returncode, 2)
        self.assertIn("沒有被改到", r.stderr)

    def test_same_violation_same_finding_on_both_hosts(self):
        self.p.layer("steps", bad='print("x")\n')
        claude = self.p.hook("post-edit", {"tool_input": {"file_path": str(self.p.root / "demo" / "steps" / "bad.py")}})
        codex = self.p.hook("post-edit", codex_patch(self.p.root, "Update File: demo/steps/bad.py"), host="codex")
        self.assertEqual((claude.returncode, codex.returncode), (2, 2))
        self.assertEqual([l for l in claude.stderr.splitlines() if "AIR001" in l], [l for l in codex.stderr.splitlines() if "AIR001" in l])

    def test_unknown_host_is_rejected_by_argparse(self):
        r = self.p.hook("stop", {}, host="cursor")
        self.assertEqual(r.returncode, 2)


class TestHookScriptsPassHost(ProjectTestCase):
    git = True

    def test_scripts_pass_host_argument_and_default_to_claude(self):
        self.assertEqual(self.p.air("install").returncode, 0)
        self.p.write("demo/steps/__init__.py", "")
        import subprocess
        payload = codex_patch(self.p.root, "Update File: .air/project.toml")
        script = str(self.p.root / "AIR" / "hooks" / "pre_edit.sh")
        r = subprocess.run(["sh", script, "codex"], cwd=self.p.root / "demo", input=json.dumps(payload), capture_output=True, text=True)
        self.assertIn("additionalContext", r.stdout)  # codex：提醒
        r = subprocess.run(["sh", script], cwd=self.p.root / "demo", input=json.dumps(payload), capture_output=True, text=True)
        self.assertIn('"ask"', r.stdout)  # 沒給宿主：claude


if __name__ == "__main__":
    unittest.main()
