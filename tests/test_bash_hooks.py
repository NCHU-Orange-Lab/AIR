"""Bash 改檔也送得到規則與檢查（pre-bash 比對上一個 Bash 改了什麼），以及每個 hook 觸發幾次（status、doctor）。

試跑（2026-10-24）：AI 幾乎都用 Bash 改檔（heredoc、sed -i、git mv），編輯工具的 hook 一次都沒觸發：
Codex 的規則注入沒發生、改完也沒有檢查，16 次 AIR 試跑的 post-edit 與 Stop 擋下都是 0，分不出是沒有違規還是 hook 沒跑。"""

import json
import unittest

from tests.helpers import ProjectTestCase
from airkit import hooklog, project


def bash(command: str, session: str = "s1") -> dict:
    return {"session_id": session, "tool_name": "Bash", "tool_input": {"command": command}}


class BashCase(ProjectTestCase):
    phase = "migrating"
    git = True

    def setUp(self):
        super().setUp()
        self.p.layer("steps", ok="x = 1\n")
        self.p.layer("adapters")
        self.p.commit("base")

    def start(self, host=None):
        self.assertEqual(self.p.hook("session-start", {"session_id": "s1", "source": "startup"}, host=host).returncode, 0)

    def pre_bash(self, command="ls", host=None) -> str:
        r = self.p.hook("pre-bash", bash(command), host=host)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"] if r.stdout.strip() else ""


class TestBashEdits(BashCase):
    def test_violation_written_by_the_previous_command_is_reported_once(self):
        self.start()
        self.p.write("demo/steps/bad.py", 'print("x")\n')          # 上一個 Bash 指令寫的
        text = self.pre_bash()
        self.assertIn("上一個指令改的檔案違反規則（修改已經寫入，必須修正）", text)
        self.assertIn("AIR001", text)
        self.assertIn("demo/steps/bad.py", text)
        self.assertNotIn("違反規則", self.pre_bash())               # 沒有再改：不重複報

    def test_files_dirty_before_the_session_started_are_not_this_sessions(self):
        self.p.write("demo/steps/bad.py", 'print("x")\n')          # 別的 session 留下的半成品
        self.start()
        self.assertEqual(self.pre_bash(), "")

    def test_edits_already_checked_by_post_edit_are_not_reported_again(self):
        self.start()
        path = self.p.write("demo/steps/bad.py", 'print("x")\n')
        r = self.p.hook("post-edit", {"session_id": "s1", "tool_name": "Write", "tool_input": {"file_path": str(path)}})
        self.assertEqual(r.returncode, 2)                           # post-edit 已經擋過
        self.assertNotIn("違反規則", self.pre_bash())

    def test_layer_changed_by_bash_gets_its_rules_once(self):
        self.start()
        self.p.write("demo/adapters/tool.py", "import requests\n")
        text = self.pre_bash()
        self.assertIn("【AIR：你剛用指令改了 adapters 的檔案", text)
        self.assertIn("ADP-1", text)
        self.assertNotIn("違反規則", text)                           # adapters 可以 import 第三方
        self.p.write("demo/adapters/other.py", "import os\n")
        self.assertEqual(self.pre_bash(), "")                       # 同一層只附一次

    def test_without_a_baseline_nothing_is_reported(self):
        # session 開始時還沒裝好（沒有基準）：第一次只記基準
        self.p.write("demo/steps/bad.py", 'print("x")\n')
        self.assertEqual(self.pre_bash(), "")
        self.p.write("demo/steps/bad2.py", 'print("y")\n')
        self.assertIn("demo/steps/bad2.py", self.pre_bash())


class TestCommandPaths(BashCase):
    def test_codex_gets_rules_for_layers_named_in_the_command(self):
        self.start(host="codex")
        text = self.pre_bash("cat > demo/steps/calc.py <<'EOF'\ndef f() -> int:\n    return 1\nEOF", host="codex")
        self.assertIn("【AIR：這個指令會動到 steps 的檔案", text)
        self.assertIn("STEP-1", text)
        self.assertEqual(self.pre_bash("cat demo/steps/calc.py", host="codex"), "")  # 只附一次

    def test_claude_reads_rules_from_dot_claude_rules(self):
        self.start()
        self.assertEqual(self.pre_bash("cat demo/steps/ok.py"), "")

    def test_similar_names_do_not_match(self):
        self.start(host="codex")
        self.assertEqual(self.pre_bash("ls demo/steps_old demo/stepsfoo", host="codex"), "")


class TestNotGit(ProjectTestCase):
    phase = "migrating"

    def test_no_git_no_tracking(self):
        self.p.layer("steps")
        self.assertEqual(self.p.hook("session-start", {"session_id": "s1"}).returncode, 0)
        self.p.write("demo/steps/bad.py", 'print("x")\n')
        r = self.p.hook("pre-bash", bash("ls"))
        self.assertEqual((r.returncode, r.stdout), (0, ""))


class TestFired(ProjectTestCase):
    def test_counts_show_up_in_status_and_doctor(self):
        self.assertIn("hook 觸發：還沒有紀錄", project.status(self.p.cfg()))
        self.p.hook("session-start", {"session_id": "s1"})
        self.p.hook("pre-bash", bash("ls"))
        self.p.hook("pre-bash", bash("pwd"))
        self.p.hook("stop", {"session_id": "s1"})
        when, counts = hooklog.fired(self.p.root)
        self.assertEqual(counts, {"session-start": 1, "pre-edit": 0, "pre-bash": 2, "post-edit": 0, "stop": 1})
        self.assertIn("session-start 1、pre-edit 0、pre-bash 2、post-edit 0、stop 1", project.status(self.p.cfg()))
        self.assertIn("hook 觸發：最近一個 session", self.p.air("doctor").stdout)

    def test_latest_session_wins(self):
        self.p.hook("pre-bash", bash("ls", session="old"))
        state = self.p.root / ".air" / "sessions" / "old.json"
        data = json.loads(state.read_text(encoding="utf-8"))
        data["last"] = "2000-01-01T00:00:00+00:00"
        state.write_text(json.dumps(data), encoding="utf-8")
        self.p.hook("stop", {"session_id": "new"})
        self.assertEqual(hooklog.fired(self.p.root)[1]["stop"], 1)
        self.assertEqual(hooklog.fired(self.p.root)[1]["pre-bash"], 0)

    def test_not_installed_records_nothing(self):
        (self.p.root / ".air" / "project.toml").unlink()
        self.p.hook("pre-bash", bash("ls"))
        self.assertIsNone(hooklog.fired(self.p.root))


if __name__ == "__main__":
    unittest.main()
