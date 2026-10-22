"""hook 入口（air.py hook <event>）：用三種宿主的 payload 形狀測試。

Claude Code：tool_input.file_path；Codex：tool_input.command 是 apply_patch 的 patch 文字；
Grok：Stop 的延續旗標是 stopHookActive（沒有 snake_case 別名）。
"""

import json
import unittest

from tests.helpers import ProjectTestCase

BAD = 'print("x")\n'
SOFT = "def f():\n" + "\n".join(f"    x{i} = {i}" for i in range(45)) + "\n    return x0\n"


def claude_edit(path) -> str:
    return json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Edit", "tool_input": {"file_path": str(path)}})


def codex_patch(cwd, *headers: str) -> str:
    body = "\n".join(f"*** {h}" for h in headers)
    return json.dumps({"hook_event_name": "PostToolUse", "tool_name": "apply_patch", "cwd": str(cwd),
                       "tool_input": {"command": f"*** Begin Patch\n{body}\n*** End Patch"}})


class TestPostEdit(ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.p.layer("steps")

    def test_violation_is_reported_as_already_written(self):
        f = self.p.write("demo/steps/bad.py", BAD)
        r = self.p.air("hook", "post-edit", stdin=claude_edit(f))
        self.assertEqual(r.returncode, 2)
        self.assertIn("AIR001", r.stderr)
        self.assertIn("已經寫入檔案", r.stderr)

    def test_clean_file(self):
        f = self.p.write("demo/steps/ok.py", "x = 1\n")
        r = self.p.air("hook", "post-edit", stdin=claude_edit(f))
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def test_soft_only_goes_to_additional_context(self):
        f = self.p.write("demo/steps/long.py", SOFT)
        r = self.p.air("hook", "post-edit", stdin=claude_edit(f))
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertIn("AIR101", out["hookSpecificOutput"]["additionalContext"])

    def test_non_python_file_ignored(self):
        f = self.p.write("README.md", "print('x')")
        r = self.p.air("hook", "post-edit", stdin=claude_edit(f))
        self.assertEqual(r.returncode, 0)

    def test_file_outside_layers_ignored(self):
        f = self.p.write("demo/legacy.py", BAD)
        r = self.p.air("hook", "post-edit", stdin=claude_edit(f))
        self.assertEqual(r.returncode, 0)

    def test_codex_update_file(self):
        self.p.write("demo/steps/bad.py", BAD)
        r = self.p.air("hook", "post-edit", stdin=codex_patch(self.p.root, "Update File: demo/steps/bad.py"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("AIR001", r.stderr)

    def test_codex_add_file_and_delete_file(self):
        self.p.write("demo/steps/new.py", BAD)
        r = self.p.air("hook", "post-edit", stdin=codex_patch(self.p.root, "Add File: demo/steps/new.py", "Delete File: demo/steps/gone.py"))
        self.assertEqual(r.returncode, 2)

    def test_codex_relative_path_uses_payload_cwd(self):
        self.p.write("demo/steps/bad.py", BAD)
        # 用別的 cwd 執行，但 payload 的 cwd 指向專案
        r = self.p.air("hook", "post-edit", stdin=codex_patch(self.p.root, "Update File: demo/steps/bad.py"), cwd=self.p.root / "demo")
        self.assertEqual(r.returncode, 2)

    def test_undecodable_file_reports_air000(self):
        f = self.p.write("demo/steps/bad.py", b"name = 'caf\xe9'\n")
        r = self.p.air("hook", "post-edit", stdin=claude_edit(f))
        self.assertEqual(r.returncode, 2)
        self.assertIn("AIR000", r.stderr)
        self.assertNotIn("Traceback", r.stderr)


class TestHookRobustness(ProjectTestCase):
    def test_not_installed_does_nothing(self):
        (self.p.root / ".air" / "project.toml").unlink()
        r = self.p.air("hook", "stop", stdin="{}")
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_malformed_json_fails_open_with_message(self):
        r = self.p.air("hook", "post-edit", stdin="{not json")
        self.assertEqual(r.returncode, 0)
        self.assertIn("AIR hook", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_unexpected_payload_shape_fails_open_with_message(self):
        r = self.p.air("hook", "post-edit", stdin=json.dumps({"tool_input": "not a dict"}))
        self.assertEqual(r.returncode, 0)
        self.assertIn("AIR hook", r.stderr)
        self.assertNotIn("Traceback", r.stderr)


class TestPreBash(ProjectTestCase):
    def test_blocks_no_verify(self):
        r = self.p.air("hook", "pre-bash", stdin=json.dumps({"tool_input": {"command": "git commit --no-verify -m x"}}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("no-verify", r.stderr)

    def test_allows_normal_command(self):
        r = self.p.air("hook", "pre-bash", stdin=json.dumps({"tool_input": {"command": "git status"}}))
        self.assertEqual(r.returncode, 0)


class TestStop(ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.p.layer("steps", bad=BAD)

    def test_violation_asks_to_continue(self):
        r = self.p.air("hook", "stop", stdin=json.dumps({"stop_hook_active": False}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("AIR001", r.stderr)

    def test_second_stop_lets_go_claude(self):
        r = self.p.air("hook", "stop", stdin=json.dumps({"stop_hook_active": True}))
        self.assertEqual(r.returncode, 0)

    def test_second_stop_lets_go_grok_camel_case(self):
        r = self.p.air("hook", "stop", stdin=json.dumps({"stopHookActive": True}))
        self.assertEqual(r.returncode, 0)

    def test_clean_project_passes(self):
        (self.p.root / "demo" / "steps" / "bad.py").unlink()
        r = self.p.air("hook", "stop", stdin="{}")
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
