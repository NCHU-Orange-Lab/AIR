"""batch finish（commit 之前一個指令做完一批的收尾）、review --bundle 認得搬移、指令輸出的上限。

試跑（2026-10-24）：AIR 組比對照組多出的呼叫一半是流程步驟（verify、review --result、commit、batch close 各一次），
--bundle 把搬進層的 330 行整份印出，Codex 又不會自己截斷輸出。"""

import json
import subprocess
import unittest

from tests.helpers import TESTS_FAIL, TESTS_PASS, ProjectTestCase
from airkit import history, project, review, usage, verify

PASS = json.dumps({"findings": [], "overall": "pass", "explanation": "對照過列出的規則，沒有問題。"})


def p1(path: str) -> str:
    return json.dumps({"findings": [{"rule": "STEP-4", "title": "補型別", "body": "回傳值沒有型別標註。", "confidence": 0.9,
                                     "priority": 1, "path": path, "lines": {"start": 1, "end": 1}}],
                       "overall": "fail", "explanation": "一個 STEP-4。"})


class FinishCase(ProjectTestCase):
    phase = "migrating"
    git = True
    test_command = TESTS_PASS

    def setUp(self):
        super().setUp()
        self.p.layer("steps", ok="x = 1\n")
        self.p.write("demo/old.py", "def total(xs: list[int]) -> int:\n    return sum(xs)\n")
        head = self.p.commit("base")
        project.record(self.p.root, {"time": verify.now(), "from": "layered", "to": "migrating", "head": head, "how": "normal", "reason": None})
        self.p.commit("進入 migrating")

    def finish(self, *args, stdin=""):
        return self.p.air("batch", "finish", *args, stdin=stdin)

    def verify_runs(self):
        return sum(1 for e in usage.load(self.p.root) if e["kind"] == "verify")


class TestFinish(FinishCase):
    def test_runs_verify_records_the_batch_and_prints_the_commit(self):
        self.p.write("demo/steps/calc.py", "def double(x: int) -> int:\n    return 2 * x\n")
        r = self.finish("--note", "第 1 批：double")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.verify_runs(), 1)                 # 沒有驗收紀錄：自己跑
        self.assertIn("AIR 驗收（batch）", r.stdout)
        self.assertIn("這一批已記進 .air/history.jsonl：第 1 批", r.stdout)
        self.assertIn('git add demo .air/history.jsonl && git commit -m "AIR 遷移第 1 批：double"', r.stdout)  # 沒有 tests/ 與報告就不列
        e = self.p.history()[-1]
        self.assertTrue(e["before_commit"])
        self.assertEqual(e["git"]["layer_files_added"], 1)      # 還沒追蹤的新檔也算進這一批（比的是工作樹）
        self.assertEqual(e["git"]["layer_insertions"], 2)

    def test_reuses_a_fresh_receipt(self):
        self.assertEqual(self.p.air("verify").returncode, 0)
        r = self.finish()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.verify_runs(), 1)                 # 沒有重跑
        self.assertIn("驗收：沿用 通過", r.stdout)

    def test_failed_verify_writes_nothing(self):
        self.p.write("demo/steps/bad.py", 'print("x")\n')
        r = self.finish()
        self.assertEqual(r.returncode, 1)
        self.assertIn("驗收沒有通過", r.stderr)
        self.assertEqual(len(self.p.history()), 1)

    def test_review_is_validated_and_recorded_in_the_same_call(self):
        self.p.write("demo/steps/calc.py", "def double(x: int) -> int:\n    return 2 * x\n")
        r = self.finish("--review", "-", stdin=PASS)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("語意審查：通過", r.stdout)
        self.assertEqual(self.p.history()[-1]["review"], "fresh")
        self.assertIsNotNone(review.load_record(self.p.root))

    def test_invalid_review_does_not_close_but_keeps_the_receipt(self):
        self.p.write("demo/steps/calc.py", "def double(x: int) -> int:\n    return 2 * x\n")
        r = self.finish("--review", "-", stdin=json.dumps({"findings": [], "overall": "fail", "explanation": "x"}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("審查結果無效", r.stderr)
        self.assertIn("overall 應該是 pass", r.stderr)
        self.assertEqual(len(self.p.history()), 1)              # 只有進入 migrating 那一筆
        self.assertEqual(self.finish("--review", "-", stdin=PASS).returncode, 0)
        self.assertEqual(self.verify_runs(), 1)                 # 第二次沿用第一次的驗收

    def test_open_p1_needs_ack(self):
        self.p.write("demo/steps/calc.py", "def double(x):\n    return 2 * x\n")
        r = self.finish("--review", "-", stdin=p1("demo/steps/calc.py"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("priority 0／1 的發現沒有處理", r.stderr)
        self.assertEqual(len(self.p.history()), 1)
        r = self.finish("--ack", "使用者同意下一批再補")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.p.history()[-1]["ack"]["reason"], "使用者同意下一批再補")

    def test_close_does_not_take_a_review(self):
        r = self.p.air("batch", "close", "--review", "-", stdin=PASS)
        self.assertEqual(r.returncode, 2)
        self.assertIn("--review 只用在 batch finish", r.stderr)


class TestOldPathHints(FinishCase):
    """第二輪試跑：Codex 8 次裡只有 1 次用 batch finish，其餘走 review --result、commit、batch close，還去讀 batch.py 找用法
    （第三輪查出主要是試跑提示詞要它用 batch close；這個提示是保險）。"""

    def test_review_result_and_close_point_to_finish(self):
        self.p.write("demo/steps/calc.py", "def double(x: int) -> int:\n    return 2 * x\n")
        r = self.p.air("review", "--result", "-", stdin=PASS)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("下一批改用 batch finish", r.stdout)
        self.assertNotIn("下一批改用", self.p.air("review", "--result", "-", "--json", stdin=PASS).stdout)  # 給程式讀的不加
        self.assertEqual(self.p.air("verify").returncode, 0)
        r = self.p.air("batch", "close", "--note", "第 1 批")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("下一批改用 batch finish", r.stdout)

    def test_finish_itself_has_no_hint(self):
        self.p.write("demo/steps/calc.py", "def double(x: int) -> int:\n    return 2 * x\n")
        r = self.finish("--note", "第 1 批", "--review", "-", stdin=PASS)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("下一批改用", r.stdout)


class TestReviewHintOutsideMigration(ProjectTestCase):
    phase = "layered"
    git = True

    def test_no_finish_hint_before_migrating(self):
        self.p.layer("steps", calc="def double(x: int) -> int:\n    return 2 * x\n")
        r = self.p.air("review", "--result", "-", stdin=PASS)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("batch finish", r.stdout)


class TestWindowAfterFinish(FinishCase):
    def test_next_batch_starts_at_the_commit_that_contains_the_entry(self):
        self.p.write("demo/steps/calc.py", "def double(x: int) -> int:\n    return 2 * x\n")
        self.assertEqual(self.finish().returncode, 0)
        entry = self.p.history()[-1]
        self.assertEqual(history.window_start(self.p.root), (entry["time"], entry["head"]))  # 還沒 commit：先用它記的 head
        first = self.p.commit("AIR 遷移第 1 批")
        self.assertEqual(history.committed_in(self.p.root, entry), first)
        self.assertEqual(history.window_start(self.p.root)[1], first)
        self.p.write("demo/steps/more.py", "def triple(x: int) -> int:\n    return 3 * x\n")
        self.assertEqual(self.finish().returncode, 0)
        second = self.p.history()[-1]
        self.assertEqual(second["since"]["head"], first)
        self.assertEqual(second["git"]["layer_files_added"], 1)  # 只有這一批的 more.py，不含第 1 批的 calc.py

    def test_entry_is_found_by_batch_number_not_by_time(self):
        # 階段轉移與批次可能落在同一秒：用時間找會找到轉移那一行的 commit
        when = "2026-10-24T20:00:00+08:00"
        project.record(self.p.root, {"time": when, "from": "migrating", "to": "migrating", "head": None, "how": "pause", "reason": "x"})
        self.p.commit("暫停")
        entry = {"time": when, "from": "migrating", "to": "migrating", "head": None, "how": "batch", "n": 1, "before_commit": True}
        project.record(self.p.root, entry)
        added = self.p.commit("第 1 批")
        self.assertEqual(history.committed_in(self.p.root, entry), added)
        project.record(self.p.root, entry)  # 同一行又出現一次（例如合併時重複）：仍然是最早加入它的那個 commit
        self.p.commit("重複")
        self.assertEqual(history.committed_in(self.p.root, entry), added)

    def test_close_entries_keep_their_head(self):
        self.assertEqual(self.p.air("verify").returncode, 0)
        self.assertEqual(self.p.air("batch", "close").returncode, 0)
        e = self.p.history()[-1]
        self.assertNotIn("before_commit", e)
        self.assertEqual(history.window_start(self.p.root)[1], e["head"])


class TestFinishPhase(ProjectTestCase):
    phase = "layered"
    test_command = TESTS_FAIL

    def test_only_while_migrating(self):
        r = self.p.air("batch", "finish")
        self.assertEqual(r.returncode, 1)
        self.assertIn("phase = migrating", r.stderr)


class TestBundleMoves(ProjectTestCase):
    phase = "migrating"
    git = True
    MODEL = ("import logging\n\nlogging.basicConfig(level=logging.INFO)\n\n\nclass Net:\n    def forward(self, x):\n        return x\n\n"
             "    def loss(self, y):\n        return y\n\n\ndef build() -> Net:\n    return Net()\n")

    def setUp(self):
        super().setUp()
        self.p.layer("adapters")
        self.p.write("demo/model.py", self.MODEL)
        self.p.commit("base")

    def bundle(self, *args):
        r = self.p.air("review", "--bundle", *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_git_mv_shows_origin_and_outline_not_the_whole_file(self):
        subprocess.run(["git", "mv", "demo/model.py", "demo/adapters/model.py"], cwd=self.p.root, check=True)
        out = self.bundle()
        self.assertIn("=== demo/adapters/model.py（新增；搬自 demo/model.py，相似度 100%）", out)
        self.assertIn("（內容與搬移前相同）", out)
        self.assertIn("── 大綱", out)
        self.assertIn("3│logging.basicConfig(level=logging.INFO)", out)  # import 時就執行的敘述照樣列出
        self.assertIn("6–11│class Net:", out)
        self.assertIn("10–11│    def loss(self, y):", out)
        self.assertNotIn("return y", out)                                # 不重印全文
        full = self.bundle("--full")
        self.assertIn("+        return y", full)                       # 照舊：git 的差異（新增的檔案整個在裡面）
        self.assertNotIn("搬自", full)

    def test_plain_mv_without_git_add_is_recognised(self):
        (self.p.root / "demo" / "model.py").rename(self.p.root / "demo" / "adapters" / "model.py")
        self.assertIn("（新檔案；搬自 demo/model.py，相似度 100%）", self.bundle())

    def test_move_that_leaves_a_shim_behind_is_recognised(self):
        # START.md 要求在舊路徑留轉接檔：git 看到的是「複製＋舊檔修改」，不是改名
        new = self.p.root / "demo" / "adapters" / "model.py"
        new.write_text(self.MODEL, encoding="utf-8")
        self.p.write("demo/model.py", "from demo.adapters.model import Net, build  # noqa: F401\n")
        self.assertIn("（新檔案；搬自 demo/model.py，相似度 100%）", self.bundle())     # 還沒 git add
        subprocess.run(["git", "add", "-A"], cwd=self.p.root, check=True)
        self.assertIn("（新增；搬自 demo/model.py，相似度 100%）", self.bundle())       # git add 之後是複製

    def test_moved_and_edited_shows_the_diff_against_the_old_path(self):
        subprocess.run(["git", "mv", "demo/model.py", "demo/adapters/model.py"], cwd=self.p.root, check=True)
        self.p.write("demo/adapters/model.py", self.MODEL.replace("level=logging.INFO", "level=logging.WARNING"))
        out = self.bundle()
        self.assertRegex(out, r"搬自 demo/model\.py，相似度 \d\d%")
        self.assertIn("-logging.basicConfig(level=logging.INFO)", out)
        self.assertIn("+logging.basicConfig(level=logging.WARNING)", out)

    def test_unparsable_moved_file_falls_back_to_full_text(self):
        self.p.write("demo/model.py", "def broken(:\n")
        self.p.commit("broken")
        subprocess.run(["git", "mv", "demo/model.py", "demo/adapters/model.py"], cwd=self.p.root, check=True)
        out = self.bundle()
        self.assertIn("   1│def broken(:", out)
        self.assertNotIn("── 大綱", out)

    def test_outline_lists_imports_statements_and_signatures(self):
        lines = review.outline(self.MODEL)
        self.assertEqual([line.split("│", 1)[1] for line in lines],
                         ["import logging", "logging.basicConfig(level=logging.INFO)", "class Net:",
                          "    def forward(self, x):", "    def loss(self, y):", "def build() -> Net:"])
        self.assertEqual(review.outline("def broken(:\n"), [])


class TestOutputCap(ProjectTestCase):
    extra_toml = "\n[output]\nmax-chars = 300\n"

    def setUp(self):
        super().setUp()
        for i in range(12):
            self.p.write(f"demo/mod{i:02d}.py", "import os\n\ndef f():\n    return os.getcwd()\n")

    def test_long_output_goes_to_a_file(self):
        r = self.p.air("survey")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("完整內容在 .air/out/survey.txt", r.stdout)
        self.assertLess(len(r.stdout), 600)
        full = (self.p.root / ".air" / "out" / "survey.txt").read_text(encoding="utf-8")
        self.assertIn("demo.mod11", full)
        self.assertNotIn("demo.mod11", r.stdout)
        self.assertEqual((self.p.root / ".air" / "out" / ".gitignore").read_text(encoding="utf-8"), "*\n")

    def test_json_is_never_cut(self):
        r = self.p.air("check", "--json")
        self.assertNotIn("完整內容在", r.stdout)
        json.loads(r.stdout)


class TestOutputCapSettings(ProjectTestCase):
    def test_zero_means_no_limit_and_bad_values_are_rejected(self):
        for i in range(40):
            self.p.write(f"demo/mod{i:02d}.py", "x = 1\n")
        toml = self.p.root / ".air" / "project.toml"
        base = toml.read_text(encoding="utf-8")
        toml.write_text(base + "\n[output]\nmax-chars = 0\n", encoding="utf-8")
        self.assertNotIn("完整內容在", self.p.air("survey").stdout)
        toml.write_text(base + "\n[output]\nmax-chars = -1\n", encoding="utf-8")
        r = self.p.air("status")
        self.assertEqual(r.returncode, 2)
        self.assertIn("output.max-chars 要是 0 以上的整數", r.stderr)
        toml.write_text(base, encoding="utf-8")
        self.assertEqual(self.p.cfg().output_max_chars, 4000)  # 預設


if __name__ == "__main__":
    unittest.main()
