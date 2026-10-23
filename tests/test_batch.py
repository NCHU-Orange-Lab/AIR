"""批次的代理指標（batch.py）：batch close 把這批的成本寫進 history.jsonl，batch list 列表；前置是驗收新鮮且通過。
報告 §4.1.4 第 10 點：AIR 看不到 token，但看得到放大器；寫進 history 才能比較改版前後。"""

import json
import subprocess
import time
import unittest

from tests.helpers import TESTS_PASS, ProjectTestCase
from airkit import batch, usage

REPORT = "docs/AIR-遷移報告.md"


class BatchCase(ProjectTestCase):
    phase = "migrating"
    git = True
    test_command = TESTS_PASS

    def setUp(self):
        super().setUp()
        self.p.layer("steps", ok="x = 1\n")
        self.p.write("demo/old.py", "y = 2\n")
        self.p.commit("base")

    def close(self, *args):
        return self.p.air("batch", "close", *args)


class TestPreconditions(BatchCase):
    def test_needs_a_fresh_accepted_verify_and_the_migrating_phase(self):
        r = self.close()
        self.assertEqual(r.returncode, 1)
        self.assertIn("還沒有通過驗收", r.stderr)
        self.p.write("demo/steps/bad.py", 'print("x")\n')
        self.assertEqual(self.p.air("verify").returncode, 1)
        r = self.close()
        self.assertEqual(r.returncode, 1)
        self.assertIn("未通過", r.stderr)
        (self.p.root / "demo" / "steps" / "bad.py").unlink()
        self.assertEqual(self.p.air("verify").returncode, 0)
        self.p.write("demo/steps/more.py", "z = 3\n")  # 驗收後又改了程式：紀錄過期
        r = self.close()
        self.assertEqual(r.returncode, 1)
        self.assertIn("已過期", r.stderr)
        self.assertEqual(self.p.history(), [])  # 都沒有寫
        self.assertEqual(self.p.air("verify").returncode, 0)
        self.assertEqual(self.close().returncode, 0)
        self.assertEqual(len(batch.batches(self.p.root)), 1)


class TestWrongPhase(ProjectTestCase):
    phase = "layered"
    test_command = TESTS_PASS

    def test_only_while_migrating(self):
        r = self.p.air("batch", "close")
        self.assertEqual(r.returncode, 1)
        self.assertIn("phase = migrating", r.stderr)
        self.assertIn("layered", r.stderr)
        self.assertIn("還沒有結束過任何一批", self.p.air("batch", "list").stdout)


class TestClose(BatchCase):
    def test_records_window_metrics_git_stats_legacy_and_warns_about_review(self):
        self.assertEqual(self.p.air("verify").returncode, 0)             # metrics：verify 1
        self.p.write("demo/steps/calc.py", "def total(xs: list[int]) -> int:\n    return sum(xs)\n")
        self.p.air("review")                                              # metrics：review-scope 1
        usage.record(self.p.root, "post-edit-blocked", files=1, violations=2)
        usage.record(self.p.root, "stop-blocked", violations=3, groups=1, scope="session")
        self.assertEqual(self.p.air("verify").returncode, 0)             # 改了程式，重新驗收
        r = self.close()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("這一批已記進 .air/history.jsonl：第 1 批", r.stdout)
        self.assertIn("注意：沒有語意審查紀錄", r.stdout)               # 審查只提醒，不擋
        self.assertIn("停下來向使用者報告", r.stdout)
        e = self.p.history()[-1]
        self.assertEqual((e["how"], e["n"], e["note"], e["from"], e["to"], e["review"]), ("batch", 1, None, "migrating", "migrating", "none"))
        self.assertEqual(e["since"], {"time": None, "head": None})       # 沒有上一批、history 裡也沒有進入 migrating 的轉移：算全部
        self.assertIsNone(e["git"])                                       # 沒有起點就沒有 git 統計
        self.assertEqual(e["air"], (self.p.root / "AIR" / "VERSION").read_text(encoding="utf-8").strip())
        m = e["metrics"]
        self.assertEqual((m["verify_runs"], m["verify_accepted"], m["review_scope_runs"], m["post_edit_blocked"], m["stop_blocked"], m["review_results"]), (2, 2, 1, 1, 1, 0))
        self.assertGreater(m["review_scope_chars"], 100)
        self.assertEqual((e["legacy_remaining"], e["legacy_shims"], e["legacy_markers"]), (1, 0, 1))  # demo/old.py 還有自己的程式；空的 demo/__init__.py 是套件標記
        self.assertEqual((e["review_blocking"], e["ack"]), (None, None))   # 沒有審查紀錄
        self.assertIn("git 統計不可用", r.stdout)
        # 第二批：時間窗只算第一批之後的事；git 統計從第一批的 HEAD 算
        first_head = e["head"]
        time.sleep(1.1)                                                   # metrics 與 history 的時間都到秒，確保在同一秒之後
        self.p.write("demo/steps/calc.py", "def total(xs: list[int]) -> int:\n    return sum(xs) + 0\n")
        self.p.write("demo/steps/extra.py", "a = 1\n")
        self.p.write("tests/test_x.py", "def test_x():\n    assert 1\n")
        self.p.write("scripts/not_counted.py", "print(1)\n")
        self.p.commit("batch 2")
        self.assertEqual(self.p.air("verify").returncode, 0)
        r = self.close("--note", "第 2 批：calc")
        self.assertEqual(r.returncode, 0, r.stderr)
        e2 = self.p.history()[-1]
        self.assertEqual((e2["n"], e2["note"], e2["since"]["head"]), (2, "第 2 批：calc", first_head))
        self.assertEqual(e2["since"]["time"], e["time"])
        self.assertEqual(e2["metrics"]["verify_runs"], 1)                # 只有第一批之後的那一次
        self.assertEqual(e2["metrics"]["post_edit_blocked"], 0)
        g = e2["git"]
        self.assertEqual((g["files"], g["layer_files_added"]), (3, 2))   # calc、extra、tests/test_x；scripts/ 不算；第一批的 HEAD 還沒有 calc，所以 calc 與 extra 都算新搬進層
        self.assertEqual(g["layer_insertions"], 3)                       # calc 2 行＋extra 1 行（numstat）
        self.assertGreaterEqual(g["insertions"], g["layer_insertions"])
        self.assertIn("第 2 批「第 2 批：calc」", r.stdout)
        self.assertIn("搬進層 2 檔 3 行、共改 3 檔", r.stdout)
        status = self.p.air("status").stdout
        self.assertIn("最近一批：第 2 批「第 2 批：calc」", status)
        self.assertEqual(json.loads(self.p.air("status", "--json").stdout)["last_batch"]["n"], 2)
        listing = self.p.air("batch", "list").stdout
        self.assertIn("批次 | 時間 | AIR", listing)
        self.assertEqual(listing.count("\n1 | "), 1)
        self.assertIn("2 | ", listing)
        self.assertIn("第 2 批：calc", listing)
        self.assertIn("這台機器", listing)

    def test_stale_review_is_a_warning_not_a_block(self):
        self.p.write("demo/steps/calc.py", "def total(xs: list[int]) -> int:\n    return sum(xs)\n")
        result = {"findings": [], "overall": "pass", "explanation": "ok"}
        self.assertEqual(self.p.air("review", "--result", "-", stdin=json.dumps(result)).returncode, 0)
        self.p.write("demo/steps/calc.py", "def total(xs: list[int]) -> int:\n    return sum(xs) + 1\n")  # 審過的檔案又改了
        self.assertEqual(self.p.air("verify").returncode, 0)
        r = self.close()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("注意：語意審查紀錄已過期", r.stdout)
        self.assertEqual(self.p.history()[-1]["review"], "stale")


class TestWindowFromTransition(ProjectTestCase):
    """沒有上一批時，起點是進入 migrating 的那次轉移。"""
    phase = "layered"
    git = True
    test_command = TESTS_PASS

    def test_window_starts_at_the_migrating_transition(self):
        self.p.air("layers")
        self.p.write("demo/steps/ok.py", "x = 1\n")
        self.p.write(REPORT, "# 報告\n")
        base = self.p.commit("layers")
        usage.record(self.p.root, "survey", modules=3, chars=100)        # 轉移之前的事：不算
        time.sleep(1.1)
        r = self.p.air("phase", "migrating")
        self.assertEqual(r.returncode, 0, r.stderr)
        transition = self.p.history()[-1]
        self.assertEqual((transition["to"], transition["head"], transition["how"]), ("migrating", base, "normal"))
        time.sleep(1.1)
        self.assertEqual(self.p.air("pause", "--reason", "x").returncode, 0)  # 轉移之後暫停又繼續：起點仍是轉移
        self.assertEqual(self.p.air("resume").returncode, 0)
        time.sleep(1.1)
        self.p.write("demo/steps/moved.py", "y = 2\n")
        self.p.commit("batch 1")
        self.assertEqual(self.p.air("verify").returncode, 0)
        self.assertEqual(self.p.air("batch", "close").returncode, 0)
        e = self.p.history()[-1]
        self.assertEqual(e["since"], {"time": transition["time"], "head": base})
        self.assertEqual((e["metrics"]["survey_runs"], e["metrics"]["verify_runs"]), (0, 1))
        self.assertEqual((e["git"]["layer_files_added"], e["git"]["files"]), (1, 1))


class TestPauseAndWindow(BatchCase):
    def test_pause_and_resume_records_are_not_the_window_start(self):
        # pause／resume 的 history 紀錄 to 也是 migrating：暫停過的專案第一批的起點曾經變成 resume 那一筆（v3.0.1 驗收時發現）
        usage.record(self.p.root, "survey", modules=1, chars=10)
        time.sleep(1.1)
        self.assertEqual(self.p.air("pause", "--reason", "x").returncode, 0)
        self.assertEqual(self.p.air("resume").returncode, 0)
        self.assertEqual([e["how"] for e in self.p.history()], ["pause", "resume"])
        self.assertEqual(batch.window_start(self.p.root), (None, None))  # 沒有真正的轉移：算全部
        self.assertEqual(self.p.air("verify").returncode, 0)
        self.assertEqual(self.close().returncode, 0)
        e = self.p.history()[-1]
        self.assertEqual(e["since"], {"time": None, "head": None})
        self.assertEqual(e["metrics"]["survey_runs"], 1)  # 暫停之前的也算進第一批

    def test_close_is_refused_while_paused(self):
        self.assertEqual(self.p.air("verify").returncode, 0)
        self.assertEqual(self.p.air("pause", "--reason", "先趕比賽").returncode, 0)
        r = self.close()
        self.assertEqual(r.returncode, 1)
        self.assertIn("遷移暫停中", r.stderr)
        self.assertIn("先趕比賽", r.stderr)
        self.assertEqual([e["how"] for e in self.p.history()], ["pause"])  # 沒有寫批次
        self.assertEqual(self.p.air("resume").returncode, 0)
        self.assertEqual(self.close().returncode, 0)


def _finding(**overrides) -> dict:
    base = {"rule": "STEP-6", "title": "回傳型別不明", "body": "total 沒有型別。", "confidence": 0.8, "priority": 1,
            "path": "demo/steps/calc.py", "lines": {"start": 1, "end": 2}}
    return {**base, **overrides}


class TestUnresolvedP1(BatchCase):
    CALC = "def total(xs):\n    return sum(xs)\n"

    def review_with(self, *findings) -> None:
        overall = "fail" if any(f["priority"] in (0, 1) for f in findings) else "pass"
        data = {"findings": list(findings), "overall": overall, "explanation": "測試"}
        r = self.p.air("review", "--result", "-", stdin=json.dumps(data))
        self.assertIn(r.returncode, (0, 1), r.stderr)

    def setUp(self):
        super().setUp()
        self.p.write("demo/steps/calc.py", self.CALC)
        self.review_with(_finding())
        self.assertEqual(self.p.air("verify").returncode, 0)

    def test_close_is_refused_while_the_review_record_has_a_p1(self):
        r = self.close()
        self.assertEqual(r.returncode, 1)
        self.assertIn("1 個 priority 0／1 的發現沒有處理", r.stderr)
        self.assertIn("demo/steps/calc.py:1", r.stderr)
        self.assertIn("--ack", r.stderr)
        self.assertEqual(self.p.history(), [])
        self.assertEqual(self.close("--ack", "   ").returncode, 1)         # 理由是空白：一樣不收
        self.assertEqual(self.p.history(), [])

    def test_ack_with_a_reason_records_the_unresolved_findings(self):
        r = self.close("--ack", "使用者同意：型別留到下一批")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("確認不處理 1 個 P1（使用者同意：型別留到下一批）", r.stdout)
        e = self.p.history()[-1]
        self.assertEqual(e["review_blocking"], 1)
        self.assertEqual(e["ack"], {"reason": "使用者同意：型別留到下一批", "findings": ["P1 STEP-6 demo/steps/calc.py:1"]})
        listing = self.p.air("batch", "list").stdout
        self.assertIn("未解 P1", listing)
        self.assertIn("1（已確認）", listing)

    def test_fixing_and_re_reviewing_clears_it_and_ack_is_then_unused(self):
        self.p.write("demo/steps/calc.py", "def total(xs: list[int]) -> int:\n    return sum(xs)\n")
        self.review_with()                                                  # 改過的檔案重審，沒有發現
        self.assertEqual(self.p.air("verify").returncode, 0)
        r = self.close("--ack", "不需要")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--ack 沒有用到", r.stdout)
        e = self.p.history()[-1]
        self.assertEqual((e["review_blocking"], e["ack"]), (0, None))
        self.assertIn("| 0 |", self.p.air("batch", "list").stdout.replace("  ", " "))

    def test_p2_findings_do_not_block(self):
        self.review_with(_finding(priority=2, confidence=1))
        r = self.close()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.p.history()[-1]["review_blocking"], 0)

    def test_a_p1_in_the_record_still_counts_after_the_file_changed(self):
        self.p.write("demo/steps/calc.py", self.CALC + "# 改了但還沒重審\n")
        self.assertEqual(self.p.air("verify").returncode, 0)
        r = self.close()
        self.assertEqual(r.returncode, 1)
        self.assertIn("priority 0／1 的發現沒有處理", r.stderr)


class TestTimeParsing(unittest.TestCase):
    def test_metrics_utc_and_history_local_compare_correctly(self):
        self.assertEqual(batch._when("2026-10-23T15:31:05+08:00"), batch._when("2026-10-23T07:31:05Z"))
        self.assertGreater(batch._when("2026-10-23T07:31:06Z"), batch._when("2026-10-23T15:31:05+08:00"))
        self.assertIsNone(batch._when("not a time"))
        self.assertIsNone(batch._when(None))


if __name__ == "__main__":
    unittest.main()
