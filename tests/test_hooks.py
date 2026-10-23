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
        self.assertIn("…另有 3 組（共 13 個違規），完整清單在 .air/last-check.json", r.stderr)
        self.assertEqual(r.stderr.count("AIR002"), 9)
        full = json.loads((self.p.root / ".air" / "last-check.json").read_text(encoding="utf-8"))
        self.assertEqual((full["schema"], len(full["hard"])), ("air.check.v1", 13))

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
