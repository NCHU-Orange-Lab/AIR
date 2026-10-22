"""受保護路徑與唯讀階段（pre-edit hook）：編輯工具改到 AIR/、hook 設定、AIR 產生的檔案會被擋；
.air/project.toml 改成請使用者確認；分析、計畫階段不能改 .py。"""

import json
import unittest
from pathlib import Path

from tests.helpers import ProjectTestCase
from airkit import install, protect
from airkit.protect import ALLOW, ASK, DENY


def claude_edit(path) -> str:
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_input": {"file_path": str(path)}})


class TestClassify(ProjectTestCase):
    phase = "migrating"

    def decide(self, rel: str) -> str:
        return protect.classify(self.p.root / rel, self.p.cfg()).action

    def test_air_package_denied(self):
        for rel in ("AIR/config.toml", "AIR/lib/airkit/checks.py", "AIR/rules/steps.md", "AIR/new_file.py", "AIR/MANIFEST.sha256"):
            with self.subTest(rel=rel):
                self.assertEqual(self.decide(rel), DENY)

    def test_reason_points_to_the_legitimate_path(self):
        d = protect.classify(self.p.root / "AIR" / "config.toml", self.p.cfg())
        self.assertIn("air-maintain-rules", d.reason)

    def test_hook_settings_and_git_denied(self):
        self.assertEqual(self.decide(".claude/settings.json"), DENY)
        self.assertEqual(self.decide(".git/hooks/pre-commit"), DENY)

    def test_project_toml_asks(self):
        d = protect.classify(self.p.root / ".air" / "project.toml", self.p.cfg())
        self.assertEqual(d.action, ASK)
        self.assertIn("同意", d.reason)

    def test_other_air_records_denied(self):
        self.assertEqual(self.decide(".air/history.jsonl"), DENY)
        self.assertEqual(self.decide(".air/verify.json"), DENY)

    def test_managed_rule_file_denied_but_user_files_allowed(self):
        self.p.layer("steps")
        self.p.layer("flows")
        self.p.write("demo/flows/CLAUDE.md", "# user's own\n")
        install.refresh_rules(self.p.cfg())
        self.assertEqual(self.decide("demo/steps/CLAUDE.md"), DENY)  # AIR 產生
        self.assertEqual(self.decide("demo/flows/CLAUDE.md"), ALLOW)  # 使用者自己的
        self.p.write("CLAUDE.md", "# mine\n")
        self.assertEqual(self.decide("CLAUDE.md"), ALLOW)

    def test_managed_skill_denied_but_user_skill_allowed(self):
        self.p.write(".claude/skills/air-x/SKILL.md", "x")
        self.p.write(".claude/skills/air-x/.air-managed", "")
        self.p.write(".claude/skills/mine/SKILL.md", "y")
        self.assertEqual(self.decide(".claude/skills/air-x/SKILL.md"), DENY)
        self.assertEqual(self.decide(".claude/skills/mine/SKILL.md"), ALLOW)

    def test_code_and_docs_allowed_while_migrating(self):
        for rel in ("demo/steps/clean.py", "tests/test_clean.py", "README.md", "pyproject.toml", "docs/AIR-遷移報告.md"):
            with self.subTest(rel=rel):
                self.assertEqual(self.decide(rel), ALLOW)

    def test_outside_project_allowed(self):
        self.assertEqual(protect.classify(Path("/tmp/elsewhere.py"), self.p.cfg()).action, ALLOW)


class TestReadOnlyDuringAnalysis(ProjectTestCase):
    phase = "installed"

    def test_python_denied_with_phase_message(self):
        for rel in ("demo/steps/a.py", "demo/legacy.py", "tests/test_a.py"):
            with self.subTest(rel=rel):
                d = protect.classify(self.p.root / rel, self.p.cfg())
                self.assertEqual(d.action, DENY)
                self.assertIn("分析階段", d.reason)
                self.assertIn("AIR-遷移報告.md", d.reason)

    def test_report_and_docs_allowed(self):
        for rel in ("docs/AIR-遷移報告.md", "README.md", "docs/notes.md"):
            with self.subTest(rel=rel):
                self.assertEqual(protect.classify(self.p.root / rel, self.p.cfg()).action, ALLOW)


class TestReadOnlyDuringPlanning(ProjectTestCase):
    phase = "analyzed"

    def test_python_denied_with_planning_message(self):
        d = protect.classify(self.p.root / "demo" / "steps" / "a.py", self.p.cfg())
        self.assertEqual(d.action, DENY)
        self.assertIn("計畫階段", d.reason)


class TestWritablePhases(ProjectTestCase):
    def test_python_allowed_from_planned_onwards(self):
        from airkit import project
        for phase in ("planned", "layered", "migrating", "done"):
            project.set_phase(self.p.cfg(), phase)
            with self.subTest(phase=phase):
                self.assertEqual(protect.classify(self.p.root / "demo" / "steps" / "a.py", self.p.cfg()).action, ALLOW)


class TestPreEditHook(ProjectTestCase):
    phase = "migrating"

    def test_deny_exits_2_before_the_write(self):
        r = self.p.air("hook", "pre-edit", stdin=claude_edit(self.p.root / "AIR" / "config.toml"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("沒有被改到", r.stderr)
        self.assertIn("AIR/config.toml", r.stderr)
        self.assertIn("air-maintain-rules", r.stderr)

    def test_ask_outputs_permission_decision(self):
        r = self.p.air("hook", "pre-edit", stdin=claude_edit(self.p.root / ".air" / "project.toml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual((out["hookEventName"], out["permissionDecision"]), ("PreToolUse", "ask"))
        self.assertIn("同意", out["permissionDecisionReason"])

    def test_allow_is_silent(self):
        r = self.p.air("hook", "pre-edit", stdin=claude_edit(self.p.root / "demo" / "steps" / "a.py"))
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def test_codex_patch_payload(self):
        patch = "*** Begin Patch\n*** Update File: AIR/config.toml\n*** End Patch"
        payload = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "cwd": str(self.p.root), "tool_input": {"command": patch}})
        r = self.p.air("hook", "pre-edit", stdin=payload)
        self.assertEqual(r.returncode, 2)

    def test_not_installed_does_nothing(self):
        (self.p.root / ".air" / "project.toml").unlink()
        r = self.p.air("hook", "pre-edit", stdin=claude_edit(self.p.root / "AIR" / "config.toml"))
        self.assertEqual((r.returncode, r.stderr), (0, ""))


class TestPreEditHookReadOnly(ProjectTestCase):
    def test_python_edit_denied_during_analysis(self):
        r = self.p.air("hook", "pre-edit", stdin=claude_edit(self.p.root / "demo" / "steps" / "a.py"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("分析階段", r.stderr)
        r = self.p.air("hook", "pre-edit", stdin=claude_edit(self.p.root / "docs" / "AIR-遷移報告.md"))
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
