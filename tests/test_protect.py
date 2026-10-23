"""受保護路徑與唯讀階段（pre-edit hook）：編輯工具改到 AIR/、任一宿主的 hook 設定、AIR 複製的 skill 會被擋；
.air/project.toml 改成請使用者確認；分析、計畫階段不能改 .py。"""

import json
import unittest
from pathlib import Path

from tests.helpers import ProjectTestCase
from airkit import protect
from airkit.protect import ALLOW, ASK, DENY


def claude_edit(path) -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": "Edit", "session_id": "s1", "tool_input": {"file_path": str(path)}}


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

    def test_every_hosts_hook_file_and_git_denied(self):
        for rel in (".claude/settings.json", ".codex/hooks.json", ".git/hooks/pre-commit"):
            with self.subTest(rel=rel):
                self.assertEqual(self.decide(rel), DENY)
        self.assertIn("Codex", protect.classify(self.p.root / ".codex" / "hooks.json", self.p.cfg()).reason)

    def test_project_toml_asks(self):
        d = protect.classify(self.p.root / ".air" / "project.toml", self.p.cfg())
        self.assertEqual(d.action, ASK)
        self.assertIn("同意", d.reason)

    def test_other_air_records_denied(self):
        self.assertEqual(self.decide(".air/history.jsonl"), DENY)
        self.assertEqual(self.decide(".air/verify.json"), DENY)
        self.assertEqual(self.decide(".air/review.json"), DENY)  # 審查紀錄只能由 review --result 寫
        self.assertEqual(self.decide(".air/sessions/s1.json"), DENY)

    def test_managed_skill_denied_in_every_host_but_user_skill_allowed(self):
        for base in (".claude/skills", ".agents/skills"):
            self.p.write(f"{base}/air-x/SKILL.md", "x")
            self.p.write(f"{base}/air-x/.air-managed", "")
            self.p.write(f"{base}/mine/SKILL.md", "y")
            with self.subTest(base=base):
                self.assertEqual(self.decide(f"{base}/air-x/SKILL.md"), DENY)
                self.assertEqual(self.decide(f"{base}/mine/SKILL.md"), ALLOW)

    def test_managed_path_rule_denied_but_users_rule_allowed(self):
        # .claude/rules/air-<層>.md 由安裝程式產生（AIR/rules 的複本）：跟 skill 一樣不能在專案裡改；使用者自己的規則檔不管
        self.p.write(".claude/rules/air-steps.md", "---\npaths:\n  - \"demo/steps/**/*.py\"\n---\n<!-- AIR:MANAGED -->\n# steps\n")
        self.p.write(".claude/rules/mine.md", "# mine\n")
        self.p.write(".claude/rules/air-flows.md", "# 使用者自己取了 air- 開頭的名字，沒有標記\n")
        d = protect.classify(self.p.root / ".claude" / "rules" / "air-steps.md", self.p.cfg())
        self.assertEqual(d.action, DENY)
        self.assertIn("air-maintain-rules", d.reason)
        self.assertEqual(self.decide(".claude/rules/mine.md"), ALLOW)
        self.assertEqual(self.decide(".claude/rules/air-flows.md"), ALLOW)

    def test_users_rule_files_and_docs_allowed(self):
        self.p.layer("flows")
        self.p.write("demo/flows/CLAUDE.md", "# user's own\n")
        self.p.write("CLAUDE.md", "# mine\n")
        for rel in ("demo/flows/CLAUDE.md", "CLAUDE.md", "demo/steps/clean.py", "tests/test_clean.py", "README.md", "pyproject.toml", "docs/AIR-遷移報告.md"):
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


class TestPausedLiftsTheFreeze(ProjectTestCase):
    phase = "installed"

    def test_python_allowed_while_paused_but_protected_paths_still_denied(self):
        from airkit import project
        py = self.p.root / "demo" / "steps" / "a.py"
        d = protect.classify(py, self.p.cfg())
        self.assertEqual(d.action, DENY)
        self.assertIn("pause --reason", d.reason)  # 擋下的訊息要指出正當的出口
        project.pause(self.p.cfg(), "先趕比賽")
        self.assertEqual(protect.classify(py, self.p.cfg()).action, ALLOW)
        for rel in ("AIR/config.toml", ".claude/settings.json", ".air/history.jsonl", ".air/paused.json"):
            with self.subTest(rel=rel):
                self.assertEqual(protect.classify(self.p.root / rel, self.p.cfg()).action, DENY)
        self.assertEqual(protect.classify(self.p.root / ".air" / "project.toml", self.p.cfg()).action, ASK)
        r = self.p.hook("pre-edit", claude_edit(py))
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)  # Claude Code：放行，規則由 .claude/rules 載入
        r = self.p.hook("pre-edit", claude_edit(py), host="codex")
        self.assertIn("STEP-1", json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"])  # Codex：解除凍結後照常附規則
        project.resume(self.p.cfg())
        self.assertEqual(protect.classify(py, self.p.cfg()).action, DENY)


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
        r = self.p.hook("pre-edit", claude_edit(self.p.root / "AIR" / "config.toml"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("沒有被改到", r.stderr)
        self.assertIn("AIR/config.toml", r.stderr)
        self.assertIn("air-maintain-rules", r.stderr)
        self.assertEqual(r.stdout, "")  # 擋下時不附規則

    def test_ask_outputs_permission_decision(self):
        r = self.p.hook("pre-edit", claude_edit(self.p.root / ".air" / "project.toml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual((out["hookEventName"], out["permissionDecision"]), ("PreToolUse", "ask"))
        self.assertIn("同意", out["permissionDecisionReason"])

    def test_allow_outside_layers_is_silent(self):
        r = self.p.hook("pre-edit", claude_edit(self.p.root / "demo" / "legacy.py"))
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def test_allow_in_a_layer_is_silent_for_claude_and_attaches_rules_once_for_codex(self):
        a, b = self.p.root / "demo" / "steps" / "a.py", self.p.root / "demo" / "steps" / "b.py"
        r = self.p.hook("pre-edit", claude_edit(a))
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))  # Claude Code 的規則由 .claude/rules 載入，不重複附
        r = self.p.hook("pre-edit", claude_edit(a), host="codex")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("STEP-1", json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"])
        r = self.p.hook("pre-edit", claude_edit(b), host="codex")
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_reminder_and_rules_can_be_combined(self):
        # Codex 同一個 patch 改了 project.toml 與某層的檔案：既要提醒要同意，也要附規則
        patch = "*** Begin Patch\n*** Update File: .air/project.toml\n*** Update File: demo/steps/a.py\n*** End Patch"
        payload = {"tool_name": "apply_patch", "session_id": "s1", "cwd": str(self.p.root), "tool_input": {"command": patch}}
        out = json.loads(self.p.hook("pre-edit", payload, host="codex").stdout)["hookSpecificOutput"]
        self.assertIn("需要使用者同意", out["additionalContext"])
        self.assertIn("STEP-1", out["additionalContext"])

    def test_codex_patch_payload(self):
        patch = "*** Begin Patch\n*** Update File: AIR/config.toml\n*** End Patch"
        payload = {"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "cwd": str(self.p.root), "tool_input": {"command": patch}}
        r = self.p.hook("pre-edit", payload, host="codex")
        self.assertEqual(r.returncode, 2)

    def test_not_installed_does_nothing(self):
        (self.p.root / ".air" / "project.toml").unlink()
        r = self.p.hook("pre-edit", claude_edit(self.p.root / "AIR" / "config.toml"))
        self.assertEqual((r.returncode, r.stderr), (0, ""))


class TestPreEditHookReadOnly(ProjectTestCase):
    def test_python_edit_denied_during_analysis(self):
        r = self.p.hook("pre-edit", claude_edit(self.p.root / "demo" / "steps" / "a.py"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("分析階段", r.stderr)
        r = self.p.hook("pre-edit", claude_edit(self.p.root / "docs" / "AIR-遷移報告.md"))
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
