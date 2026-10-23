"""hook 入口（air.py hook <event>）：用三種宿主的 payload 形狀測試。

Claude Code：tool_input.file_path；Codex：tool_input.command 是 apply_patch 的 patch 文字；
Grok：Stop 的延續旗標是 stopHookActive（沒有 snake_case 別名）。
"""

import json
import os
import subprocess
import sys
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
        self.assertIn("post-edit 擋下 1 次", self.p.air("status").stdout)

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
        # fail-open 不能靜默：寫進 .air/hook-errors.log，status 看得到
        log = (self.p.root / ".air" / "hook-errors.log").read_text(encoding="utf-8")
        self.assertIn("\thook post-edit\tJSONDecodeError", log)
        self.assertIn("hook 錯誤（24 小時內）：1 次（最近一次 hook post-edit：JSONDecodeError", self.p.air("status").stdout)
        self.assertEqual(json.loads(self.p.air("status", "--json").stdout)["hook_errors"], 1)

    def test_unexpected_payload_shape_fails_open_with_message(self):
        r = self.p.air("hook", "post-edit", stdin=json.dumps({"tool_input": "not a dict"}))
        self.assertEqual(r.returncode, 0)
        self.assertIn("AIR hook", r.stderr)
        self.assertNotIn("Traceback", r.stderr)


class TestHookLog(ProjectTestCase):
    def test_recent_counts_only_last_24_hours_and_skips_bad_lines(self):
        from airkit import hooklog
        hooklog.log(self.p.root, "hook stop", "ValueError：x\ny")
        path = self.p.root / ".air" / "hook-errors.log"
        path.write_text(path.read_text(encoding="utf-8") + "2000-01-01T00:00:00Z\thook stop\t舊的\n不是紀錄\n", encoding="utf-8")
        self.assertEqual([(w, r) for _, w, r in hooklog.recent(self.p.root)], [("hook stop", "ValueError：x y")])
        self.assertIn("1 次（最近一次 hook stop：ValueError：x y", hooklog.describe(self.p.root))
        self.assertEqual(hooklog.describe(self.p.root / "nowhere"), "無")


class TestEncoding(ProjectTestCase):
    """輸出入一律 UTF-8，不看系統語系。模擬 zh-TW Windows（cp950）：沒有這個保證，含中文的 payload 會解析失敗而放行，
    規則注入與修法說明也會變成亂碼。"""
    phase = "migrating"
    CP950 = {"PYTHONIOENCODING": "cp950", "PYTHONUTF8": "0"}

    def setUp(self):
        super().setUp()
        self.p.layer("steps")

    def raw(self, event: str, payload: dict, *args: str) -> subprocess.CompletedProcess:
        """直接執行 air.py，拿原始位元組（不先解碼），環境模擬 cp950。"""
        return subprocess.run([sys.executable, str(self.p.root / "AIR" / "air.py"), "hook", event, *args],
                              input=json.dumps(payload, ensure_ascii=False).encode("utf-8"), capture_output=True,
                              cwd=self.p.root, env={**os.environ, **self.CP950})

    def test_chinese_commit_message_is_still_blocked_under_cp950(self):
        for cmd in ('git commit --no-verify -m "修正分層"', 'git commit -n -m "加入語意審查"', 'SKIP=air-check git commit -m "調整"'):
            with self.subTest(cmd=cmd):
                r = self.raw("pre-bash", {"tool_input": {"command": cmd}})
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("略過", r.stderr.decode("utf-8"))  # 理由也要看得懂

    def test_hook_output_is_valid_utf8_json_under_cp950(self):
        r = self.raw("session-start", {"session_id": "s1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.decode("utf-8"))  # 不是 UTF-8、不是 JSON 都會丟例外
        self.assertIn("遷移階段：migrating", out["hookSpecificOutput"]["additionalContext"])
        payload = {"session_id": "s1", "tool_name": "apply_patch", "tool_input": {"file_path": str(self.p.root / "demo" / "steps" / "a.py")}}
        r = self.raw("pre-edit", payload, "--host", "codex")  # 規則注入（Codex）：中文規則要原樣送到
        self.assertIn("STEP-1", json.loads(r.stdout.decode("utf-8"))["hookSpecificOutput"]["additionalContext"])

    def test_violation_message_is_readable_under_cp950(self):
        f = self.p.write("demo/steps/bad.py", BAD)
        r = self.raw("post-edit", {"tool_name": "Edit", "tool_input": {"file_path": str(f)}})
        self.assertEqual(r.returncode, 2)
        self.assertIn("已經寫入檔案", r.stderr.decode("utf-8"))


class TestPreBash(ProjectTestCase):
    def test_blocks_no_verify(self):
        r = self.p.air("hook", "pre-bash", stdin=json.dumps({"tool_input": {"command": "git commit --no-verify -m x"}}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("no-verify", r.stderr)

    def test_allows_normal_command(self):
        r = self.p.air("hook", "pre-bash", stdin=json.dumps({"tool_input": {"command": "git status"}}))
        self.assertEqual(r.returncode, 0)


class TestStop(ProjectTestCase):
    """hooks.stop-scope = all：整個專案的檢查（分組、上限、二次放行）。"""
    stop_scope = "all"

    def setUp(self):
        super().setUp()
        self.p.layer("steps", bad=BAD)

    def test_violation_asks_to_continue(self):
        r = self.p.air("hook", "stop", stdin=json.dumps({"stop_hook_active": False}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("AIR001", r.stderr)

    def test_stop_groups_same_violation(self):
        self.p.write("demo/steps/b.py", BAD)
        self.p.write("demo/steps/c.py", BAD)
        r = self.p.air("hook", "stop", stdin="{}")
        self.assertEqual(r.returncode, 2)
        self.assertIn("AIR001 ×3：", r.stderr)
        self.assertEqual(r.stderr.count("不能 print"), 1)
        self.assertIn("Stop 擋下 1 次", self.p.air("status").stdout)

    def test_stop_prints_at_most_ten_groups_and_writes_the_full_list(self):
        mods = ["os", "sys", "requests", "random", "time", "logging", "pathlib", "shutil", "subprocess", "httpx", "io", "pprint"]
        for m in mods:
            self.p.write(f"demo/steps/u_{m}.py", f"import {m}\n")  # 12 種不同的修法提示，加上 setUp 的 AIR001 共 13 組
        r = self.p.air("hook", "stop", stdin="{}")
        self.assertEqual(r.returncode, 2)
        self.assertIn("…另有 3 組（共 13 個違規），完整清單在 .air/branches/no-git/last-check.json", r.stderr)
        self.assertEqual(r.stderr.count("AIR002"), 9)
        full = json.loads((self.p.root / ".air" / "branches" / "no-git" / "last-check.json").read_text(encoding="utf-8"))
        self.assertEqual((full["schema"], len(full["hard"])), ("air.check.v1", 13))
        self.assertIn("範圍：所有層（hooks.stop-scope = all）", r.stderr)

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


class TestStopSession(ProjectTestCase):
    """預設 hooks.stop-scope = session：只管這個 session 動過的檔案。報告 §4.3 c：session B 什麼都沒改，卻因為另一個 session 的
    半成品被擋下，還被要求去修別人正在改的檔案。"""
    phase = "migrating"

    def setUp(self):
        super().setUp()
        self.p.layer("steps", ok="x = 1\n")

    def stop(self, session="b"):
        return self.p.air("hook", "stop", stdin=json.dumps({"session_id": session}))

    def start(self, session="b", source="startup"):
        r = self.p.air("hook", "session-start", stdin=json.dumps({"session_id": session, "source": source}))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_other_sessions_wip_does_not_block_a_session_that_touched_nothing(self):
        self.p.write("demo/steps/wip_other_session.py", BAD)  # session A 的半成品，在 B 開始前就在
        self.start()
        r = self.stop()
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(self.p.air("hook", "stop", stdin=json.dumps({"session_id": "a"})).returncode, 0)  # 沒開始過的 session 也不會被別人的檔案擋
        self.assertIn("AIR001", self.p.air("check").stdout)  # 違規還在，由 check 與 commit 前檢查把關

    def test_files_edited_by_this_session_are_checked(self):
        self.p.write("demo/steps/wip_other_session.py", BAD)
        self.start()
        mine = self.p.write("demo/steps/mine.py", BAD)
        self.p.hook("post-edit", {"session_id": "b", "tool_name": "Edit", "tool_input": {"file_path": str(mine)}})
        r = self.stop()
        self.assertEqual(r.returncode, 2)
        self.assertIn("demo/steps/mine.py", r.stderr)
        self.assertNotIn("wip_other_session", r.stderr)  # 別人的半成品不在範圍
        self.assertIn("範圍：這個 session 動過的 1 個檔案＋開始後內容變了的 1 個層內檔案", r.stderr)
        self.assertIn("python3 AIR/air.py check 可以先看", r.stderr)
        self.assertIn("Stop 擋下 1 次", self.p.air("status").stdout)

    def test_files_changed_by_bash_after_session_start_are_caught_by_the_snapshot(self):
        self.start()
        self.p.write("demo/steps/ok.py", BAD)  # 不經編輯工具（Bash）改的
        r = self.stop()
        self.assertEqual(r.returncode, 2)
        self.assertIn("demo/steps/ok.py", r.stderr)
        self.assertIn("開始後內容變了的 1 個層內檔案", r.stderr)
        self.p.write("demo/steps/ok.py", "x = 1\n")  # 改回快照時的內容就不算
        self.assertEqual(self.stop().returncode, 0)

    def test_snapshot_survives_compaction_and_touched_survives_reset(self):
        self.start()
        mine = self.p.write("demo/steps/mine.py", BAD)
        self.p.hook("post-edit", {"session_id": "b", "tool_name": "Edit", "tool_input": {"file_path": str(mine)}})
        self.p.write("demo/steps/ok.py", BAD)
        self.start(source="compact")  # 壓縮後的 SessionStart：不重拍快照、不清 touched
        r = self.stop()
        self.assertIn("動過的 1 個檔案＋開始後內容變了的 2 個層內檔案", r.stderr)

    def test_no_snapshot_means_only_tool_edits_count(self):
        self.p.write("demo/steps/ok.py", BAD)
        r = self.stop(session="never-started")
        self.assertEqual(r.returncode, 0)  # 沒有快照、沒動過任何檔案：放行
        mine = self.p.write("demo/steps/mine.py", BAD)
        self.p.hook("post-edit", {"session_id": "never-started", "tool_name": "Edit", "tool_input": {"file_path": str(mine)}})
        r = self.stop(session="never-started")
        self.assertEqual(r.returncode, 2)
        self.assertIn("沒有 session 開始時的快照", r.stderr)
        self.assertNotIn("demo/steps/ok.py", r.stderr)

    def test_integrity_is_checked_regardless_of_session(self):
        self.start()
        (self.p.root / "AIR" / "rules" / "steps.md").write_text("tampered\n", encoding="utf-8")  # 改 config.toml 會讓設定讀不了，hook 直接放行
        r = self.stop()
        self.assertEqual(r.returncode, 2)
        self.assertIn("AIR009", r.stderr)


if __name__ == "__main__":
    unittest.main()
