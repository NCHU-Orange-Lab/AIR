"""按需注入（context.py）：第一次讀寫某層附上那層的規則，同一個 session 只附一次；session 開始／壓縮後重置並附上狀態。"""

import json
import os
import time
import unittest

from tests.helpers import ProjectTestCase
from airkit import context


def read_payload(path, session="s1") -> dict:
    return {"session_id": session, "tool_name": "Read", "tool_input": {"file_path": str(path)}}


def edit_payload(path, session="s1") -> dict:
    return {"session_id": session, "tool_name": "Edit", "tool_input": {"file_path": str(path)}}


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

    def test_first_read_injects_layer_rules_once_per_session(self):
        first = self.context_of(self.p.hook("pre-read", read_payload(self.steps)))
        self.assertIn("STEP-1", first)
        self.assertIn("AIR/rules/steps.md", first)
        self.assertIn("只附一次", first)
        self.assertEqual(self.context_of(self.p.hook("pre-read", read_payload(self.steps))), "")
        self.assertEqual(self.context_of(self.p.hook("pre-edit", edit_payload(self.steps))), "")  # 讀過就不再附

    def test_first_edit_injects_when_not_read_before(self):
        new = self.p.root / "demo" / "steps" / "new.py"
        self.assertIn("STEP-2", self.context_of(self.p.hook("pre-edit", edit_payload(new))))

    def test_other_layer_and_other_session_get_their_own(self):
        self.p.hook("pre-read", read_payload(self.steps))
        flows = self.context_of(self.p.hook("pre-read", read_payload(self.p.root / "demo" / "flows" / "f.py")))
        self.assertIn("FLOW-1", flows)
        self.assertNotIn("# steps/ 規則", flows)
        again = self.context_of(self.p.hook("pre-read", read_payload(self.steps, session="s2")))
        self.assertIn("STEP-1", again)

    def test_tests_dir_gets_test_rules(self):
        out = self.context_of(self.p.hook("pre-read", read_payload(self.p.root / "tests" / "test_a.py")))
        self.assertIn("TEST-1", out)

    def test_file_in_adapter_subpackage_gets_adapter_rules(self):
        # adapters/<工具>/ 底下的檔案（ADP-1 的子套件）也是 adapters 層
        out = self.context_of(self.p.hook("pre-read", read_payload(self.p.root / "demo" / "adapters" / "torch" / "trainer.py")))
        self.assertIn("ADP-1", out)
        self.assertIn("AIR/rules/adapters.md", out)

    def test_files_outside_layers_get_nothing(self):
        for rel in ("demo/legacy.py", "README.md", "docs/AIR-遷移報告.md", "/etc/hosts"):
            with self.subTest(rel=rel):
                path = rel if rel.startswith("/") else self.p.root / rel
                self.assertEqual(self.context_of(self.p.hook("pre-read", read_payload(path))), "")

    def test_session_start_resets_so_rules_come_back_after_compaction(self):
        self.p.hook("pre-read", read_payload(self.steps))
        r = self.p.hook("session-start", {"session_id": "s1", "source": "compact"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("STEP-1", self.context_of(self.p.hook("pre-read", read_payload(self.steps))))

    def test_missing_session_id_still_works_with_shared_record(self):
        payload = {"tool_name": "Read", "tool_input": {"file_path": str(self.steps)}}
        self.assertIn("STEP-1", self.context_of(self.p.hook("pre-read", payload)))
        self.assertEqual(self.context_of(self.p.hook("pre-read", payload)), "")
        self.assertTrue((self.p.root / ".air" / "sessions" / "unknown.json").exists())

    def test_session_record_is_in_air_sessions_and_ignored_by_git(self):
        self.p.hook("pre-read", read_payload(self.steps, session="ab/c:d"))
        files = sorted(p.name for p in (self.p.root / ".air" / "sessions").iterdir())
        self.assertEqual(files, ["ab_c_d.json"])
        from airkit import install
        self.assertIn("sessions", install.AIR_IGNORE)

    def test_old_records_are_pruned_on_session_start(self):
        self.p.hook("pre-read", read_payload(self.steps, session="old"))
        old = self.p.root / ".air" / "sessions" / "old.json"
        stale = time.time() - (context.KEEP_DAYS + 1) * 86400
        os.utime(old, (stale, stale))
        self.p.hook("pre-read", read_payload(self.steps, session="recent"))
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


class TestSessionStart(ProjectTestCase):
    def test_status_and_phase_guidance_are_injected(self):
        r = self.p.hook("session-start", {"session_id": "s1", "source": "startup"})
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "SessionStart")
        text = out["additionalContext"]
        self.assertIn("遷移階段：installed", text)
        self.assertIn("分析階段", text)
        self.assertIn("不修改 .py", text)
        self.assertIn("AIR/rules/", text)

    def test_current_stage_of_start_md_is_injected_instead_of_the_whole_file(self):
        from airkit import project
        text = json.loads(self.p.hook("session-start", {"session_id": "s1"}).stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("【AIR/START.md 階段 2（已附上這一節，不用再讀整份 START.md）】", text)
        self.assertIn("survey", text)
        self.assertNotIn("## 階段 3", text)
        self.assertNotIn("## 階段 1", text)
        project.set_phase(self.p.cfg(), "migrating")
        text = json.loads(self.p.hook("session-start", {"session_id": "s1"}).stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("階段 5", text)
        self.assertIn("補行為保護測試", text)
        project.set_phase(self.p.cfg(), "done")
        text = json.loads(self.p.hook("session-start", {"session_id": "s1"}).stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("【AIR/START.md", text)
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
        self.assertNotIn("不修改 .py（會被擋下）", text)  # 不再催分析階段的事
        self.assertNotIn("【AIR/START.md", text)       # 也不附那一節
        project.resume(self.p.cfg())
        text = json.loads(self.p.hook("session-start", {"session_id": "s1"}).stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("分析階段", text)
        self.assertIn("【AIR/START.md 階段 2", text)

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
