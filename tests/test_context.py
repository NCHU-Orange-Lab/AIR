"""按需注入（context.py）：沒有路徑規則的宿主（Codex）第一次編輯某層時附上那層的規則，同一個 session 只附一次；
Claude Code 不經 hook，由 .claude/rules/air-<層>.md 自己載入（install.py）。session 開始／壓縮後重置並附上狀態。"""

import json
import os
import time
import unittest

from tests.helpers import ProjectTestCase
from airkit import context


def edit_payload(path, session="s1") -> dict:
    return {"session_id": session, "tool_name": "apply_patch", "tool_input": {"file_path": str(path)}}


class TestInjection(ProjectTestCase):
    phase = "migrating"

    def setUp(self):
        super().setUp()
        self.p.layer("steps", a="x = 1\n")
        self.p.layer("flows")
        self.steps = self.p.root / "demo" / "steps" / "a.py"

    def context_of(self, r) -> str:
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"] if r.stdout else ""

    def edit(self, path, session="s1") -> str:
        """Codex 的 pre-edit：沒有路徑規則的宿主，規則由 hook 附上。"""
        return self.context_of(self.p.hook("pre-edit", edit_payload(path, session), host="codex"))

    def test_first_edit_injects_layer_rules_once_per_session(self):
        first = self.edit(self.steps)
        self.assertIn("STEP-1", first)
        self.assertIn("AIR/rules/steps.md", first)
        self.assertIn("只附一次", first)
        self.assertEqual(self.edit(self.steps), "")
        self.assertEqual(self.edit(self.p.root / "demo" / "steps" / "new.py"), "")  # 同一層的另一個檔案也不再附

    def test_claude_gets_nothing_from_the_hook_because_rules_come_from_dot_claude_rules(self):
        # Claude Code 由宿主自己載入 .claude/rules/air-<層>.md：hook 再附一次就是重複，而且原本每次 Read 都多啟動一個 Python
        r = self.p.hook("pre-edit", {"session_id": "s1", "tool_name": "Edit", "tool_input": {"file_path": str(self.steps)}})
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        self.assertFalse((self.p.root / ".air" / "sessions").exists())  # 連注入紀錄都不用寫
        self.assertEqual(self.p.air("hook", "pre-read").returncode, 2)  # 讀檔前的 hook 已經退休

    def test_other_layer_and_other_session_get_their_own(self):
        self.edit(self.steps)
        flows = self.edit(self.p.root / "demo" / "flows" / "f.py")
        self.assertIn("FLOW-1", flows)
        self.assertNotIn("# steps/ 規則", flows)
        self.assertIn("STEP-1", self.edit(self.steps, session="s2"))

    def test_tests_dir_gets_test_rules(self):
        self.assertIn("TEST-1", self.edit(self.p.root / "tests" / "test_a.py"))

    def test_file_in_adapter_subpackage_gets_adapter_rules(self):
        # adapters/<工具>/ 底下的檔案（ADP-1 的子套件）也是 adapters 層
        out = self.edit(self.p.root / "demo" / "adapters" / "torch" / "trainer.py")
        self.assertIn("ADP-1", out)
        self.assertIn("AIR/rules/adapters.md", out)

    def test_files_outside_layers_get_nothing(self):
        for rel in ("demo/legacy.py", "README.md", "docs/AIR-遷移報告.md", "/etc/hosts"):
            with self.subTest(rel=rel):
                path = rel if rel.startswith("/") else self.p.root / rel
                self.assertEqual(self.edit(path), "")

    def test_session_start_resets_so_rules_come_back_after_compaction(self):
        self.edit(self.steps)
        r = self.p.hook("session-start", {"session_id": "s1", "source": "compact"}, host="codex")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("STEP-1", self.edit(self.steps))

    def test_missing_session_id_still_works_with_shared_record(self):
        payload = {"tool_name": "apply_patch", "tool_input": {"file_path": str(self.steps)}}
        self.assertIn("STEP-1", self.context_of(self.p.hook("pre-edit", payload, host="codex")))
        self.assertEqual(self.context_of(self.p.hook("pre-edit", payload, host="codex")), "")
        self.assertTrue((self.p.root / ".air" / "sessions" / "unknown.json").exists())

    def test_session_record_is_in_air_sessions_and_ignored_by_git(self):
        self.edit(self.steps, session="ab/c:d")
        files = sorted(p.name for p in (self.p.root / ".air" / "sessions").iterdir())
        self.assertEqual(files, ["ab_c_d.json"])
        from airkit import install
        self.assertIn("sessions", install.AIR_IGNORE)

    def test_old_records_are_pruned_on_session_start(self):
        self.edit(self.steps, session="old")
        old = self.p.root / ".air" / "sessions" / "old.json"
        stale = time.time() - (context.KEEP_DAYS + 1) * 86400
        os.utime(old, (stale, stale))
        self.edit(self.steps, session="recent")
        self.p.hook("session-start", {"session_id": "s9"})
        self.assertFalse(old.exists())
        self.assertTrue((self.p.root / ".air" / "sessions" / "recent.json").exists())

    def test_direct_api(self):
        cfg = self.p.cfg()
        self.assertIn("STEP-1", context.rules_to_inject(cfg, "x", [self.steps]))
        self.assertEqual(context.rules_to_inject(cfg, "x", [self.steps]), "")
        context.reset(cfg.root, "x")
        self.assertIn("STEP-1", context.rules_to_inject(cfg, "x", [self.steps]))
        self.assertEqual(context.rule_name(self.p.root / "demo" / "legacy.py", cfg), None)


class TestSessionRecord(ProjectTestCase):
    """session 紀錄的三件事：rules（注入過的規則）、touched（編輯工具動過的檔案）、snapshot（開始時各層檔案的雜湊）。"""
    phase = "migrating"

    def setUp(self):
        super().setUp()
        self.p.layer("steps", a="x = 1\n")
        self.cfg = self.p.cfg()

    def test_touched_is_relative_posix_and_ignores_files_outside_the_project(self):
        context.remember_touched(self.cfg.root, "s", [self.p.root / "demo" / "steps" / "a.py", "/etc/hosts", self.p.root / "README.md"])
        context.remember_touched(self.cfg.root, "s", [self.p.root / "demo" / "steps" / "a.py"])  # 重複不重記
        self.assertEqual(context.touched(self.cfg.root, "s"), ["README.md", "demo/steps/a.py"])
        self.assertEqual(context.touched(self.cfg.root, "other"), [])

    def test_snapshot_is_taken_once_and_reset_keeps_it_but_drops_rules(self):
        self.assertTrue(context.take_snapshot(self.cfg, "s"))
        self.assertFalse(context.take_snapshot(self.cfg, "s"))  # 已經有就不重拍（壓縮、恢復）
        context.remember(self.cfg.root, "s", {"steps"})
        context.remember_touched(self.cfg.root, "s", [self.p.root / "demo" / "steps" / "a.py"])
        self.p.write("demo/steps/b.py", "y = 2\n")
        context.reset(self.cfg.root, "s")
        self.assertEqual(context.injected(self.cfg.root, "s"), set())
        self.assertEqual(context.touched(self.cfg.root, "s"), ["demo/steps/a.py"])
        edited, changed, has_snapshot = context.stop_targets(self.cfg, "s")
        self.assertEqual(([p.name for p in edited], [p.name for p in changed], has_snapshot), (["a.py"], ["b.py"], True))
        self.assertEqual(context.stop_targets(self.cfg, "never"), ([], [], False))
        context.reset(self.cfg.root, "empty")  # 沒有紀錄的 session：reset 不留檔
        self.assertFalse((self.p.root / ".air" / "sessions" / "empty.json").exists())

    def test_post_edit_records_touched_for_both_hosts(self):
        a = self.p.root / "demo" / "steps" / "a.py"
        self.p.hook("post-edit", {"session_id": "s", "tool_name": "Edit", "tool_input": {"file_path": str(a)}})
        patch = "*** Begin Patch\n*** Update File: demo/steps/a.py\n*** Add File: demo/flows/f.py\n*** End Patch"
        self.p.hook("post-edit", {"session_id": "c", "tool_name": "apply_patch", "cwd": str(self.p.root), "tool_input": {"command": patch}}, host="codex")
        self.assertEqual(context.touched(self.cfg.root, "s"), ["demo/steps/a.py"])
        self.assertEqual(context.touched(self.cfg.root, "c"), ["demo/flows/f.py", "demo/steps/a.py"])


class TestSessionStart(ProjectTestCase):
    def test_status_and_phase_guidance_are_injected(self):
        r = self.p.hook("session-start", {"session_id": "s1", "source": "startup"})
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "SessionStart")
        text = out["additionalContext"]
        self.assertIn("遷移階段：installed", text)
        self.assertIn("分析階段", text)
        self.assertIn("不修改套件與測試目錄裡的 .py", text)
        self.assertIn("AIR/rules/", text)

    def test_rule_delivery_line_follows_the_host(self):
        from airkit import hosts
        claude = json.loads(self.p.hook("session-start", {"session_id": "s1"}).stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Claude Code 會自動載入 .claude/rules/air-<層>.md", claude)
        self.assertNotIn("hook 會附上", claude)
        codex = json.loads(self.p.hook("session-start", {"session_id": "s1"}, host="codex").stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("hook 會附上那一層的規則", codex)
        self.assertNotIn(".claude/rules", codex)
        self.assertIn("hook 會附上", context.session_context(self.p.cfg()))  # 沒給宿主：保守的說法
        self.assertIn(".claude/rules", context.session_context(self.p.cfg(), hosts.CLAUDE))

    def test_current_stage_of_start_md_is_injected_instead_of_the_whole_file(self):
        from airkit import project
        text = json.loads(self.p.hook("session-start", {"session_id": "s1"}).stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("【AIR/start/2-analyze.md（階段 2 的做法，已附上全文，不用再讀）】", text)
        self.assertIn("survey", text)
        self.assertNotIn("## 階段 3", text)
        self.assertNotIn("## 階段 1", text)
        project.set_phase(self.p.cfg(), "migrating")
        text = json.loads(self.p.hook("session-start", {"session_id": "s1"}).stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("階段 5", text)
        self.assertIn("補行為保護測試", text)
        project.set_phase(self.p.cfg(), "done")
        text = json.loads(self.p.hook("session-start", {"session_id": "s1"}).stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("【AIR/start/", text)
        self.assertNotIn("現在讀：", text)
        self.assertEqual(context.stage_text(9), "")

    def test_paused_replaces_the_phase_guidance_and_skips_start_md(self):
        from airkit import project
        project.pause(self.p.cfg(), "先趕比賽")
        text = json.loads(self.p.hook("session-start", {"session_id": "s1"}).stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("現在該做的事：遷移暫停中", text)
        self.assertIn("先趕比賽", text)
        self.assertIn("python3 AIR/air.py resume", text)
        self.assertIn("不要自己執行", text)
        self.assertIn("新程式仍寫進對應的層", text)
        self.assertNotIn("不修改套件與測試目錄裡的 .py（會被擋下", text)  # 不再催分析階段的事
        self.assertNotIn("【AIR/start/", text)       # 也不附那一份
        self.assertNotIn("現在讀：", text)
        project.resume(self.p.cfg())
        text = json.loads(self.p.hook("session-start", {"session_id": "s1"}).stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("分析階段", text)
        self.assertIn("【AIR/start/2-analyze.md", text)

    def test_guidance_follows_phase(self):
        from airkit import project
        project.set_phase(self.p.cfg(), "migrating")
        text = json.loads(self.p.hook("session-start", {"session_id": "s1"}).stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("逐批遷移", text)
        self.assertIn("verify", text)

    def test_not_installed_is_silent(self):
        (self.p.root / ".air" / "project.toml").unlink()
        r = self.p.hook("session-start", {"session_id": "s1"})
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def test_every_phase_has_guidance(self):
        from airkit import project
        self.assertEqual(set(context.PHASE_GUIDE), set(project.PHASES))


if __name__ == "__main__":
    unittest.main()
