"""語意審查（review.py）：用 git 界定這次的變更、列出要靠審查的規則；交回的結果要通過驗證才記錄；紀錄綁定變更的內容。"""

import json
import subprocess
import unittest

from tests.helpers import ProjectTestCase
from airkit import review, rules

FLOW_BEFORE = "def run(orders, fx):\n    rates = fx.rates_for()\n    return rates\n"
FLOW_AFTER = "def run(orders, fx):\n    rates = fx.rates_for()\n    valid = [o for o in orders if o.amount > 0]\n    return rates, valid\n"
STEP_NEW = "def total(xs: list[int]) -> int:\n    return sum(xs)\n"


def finding(**overrides) -> dict:
    base = {"rule": "FLOW-3", "title": "把篩選移到 steps", "body": "run() 在 flow 裡篩選訂單。", "confidence": 0.8,
            "priority": 1, "path": "demo/flows/report.py", "lines": {"start": 3, "end": 3}}
    return {**base, **overrides}


def result(findings=(), overall=None, **extra) -> dict:
    findings = list(findings) if isinstance(findings, (list, tuple)) else findings  # 故意給錯的型別時照原樣送出
    if overall is None:
        items = findings if isinstance(findings, list) else []
        overall = "fail" if any(isinstance(f, dict) and f.get("priority") in (0, 1) for f in items) else "pass"
    return {"findings": findings, "overall": overall, "explanation": "測試", **extra}


class ReviewCase(ProjectTestCase):
    """baseline commit：flows/report.py（舊內容）、空的 steps、tests。"""
    phase = "migrating"
    git = True

    def setUp(self):
        super().setUp()
        self.p.layer("flows", report=FLOW_BEFORE)
        self.p.layer("steps")
        (self.p.root / "tests").mkdir(exist_ok=True)
        self.p.write("tests/__init__.py", "")
        self.base = self.p.commit("baseline")

    def git(self, *args) -> str:
        return subprocess.run(["git", *args], cwd=self.p.root, capture_output=True, text=True, check=True).stdout.strip()

    def scope(self, *args) -> dict:
        r = self.p.air("review", "--json", *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def submit(self, data, *args):
        return self.p.air("review", "--result", "-", *args, stdin=json.dumps(data, ensure_ascii=False))

    def record(self) -> dict | None:
        path = self.p.root / ".air" / "review.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


class TestScope(ReviewCase):
    def test_untracked_layer_file_gets_unchecked_rules(self):
        self.p.write("demo/steps/calc.py", STEP_NEW)
        sc = self.scope()
        (f,) = sc["files"]
        self.assertEqual((f["path"], f["layer"], f["status"], f["changed_lines"]), ("demo/steps/calc.py", "steps", "untracked", None))
        self.assertEqual([r["id"] for r in f["rules"]], ["STEP-2", "STEP-3", "STEP-4", "STEP-5", "STEP-6"])  # STEP-1 由 check 負責
        step2 = f["rules"][0]
        self.assertEqual((step2["checked"], step2["source"], step2["level"]), ("partial", "AIR/rules/steps.md", "必須"))
        self.assertIn("型別標註", step2["gap"])
        self.assertEqual(sc["schema"], review.SCOPE_SCHEMA)

    def test_modified_file_lists_changed_lines(self):
        self.p.write("demo/flows/report.py", FLOW_AFTER)
        (f,) = self.scope()["files"]
        self.assertEqual((f["status"], f["changed_lines"]), ("modified", [[3, 4]]))
        self.assertEqual([r["id"] for r in f["rules"]], ["FLOW-2", "FLOW-3", "FLOW-4"])

    def test_tests_dir_gets_test_rules(self):
        self.p.write("tests/test_calc.py", "def test_x():\n    assert 1\n")
        (f,) = self.scope()["files"]
        self.assertEqual((f["layer"], [r["id"] for r in f["rules"]]), ("tests", ["TEST-1", "TEST-2", "TEST-3", "TEST-4", "TEST-5"]))

    def test_out_of_scope_files_are_listed_with_reason(self):
        self.p.write("demo/legacy.py", "x = 1\n")
        self.p.write("demo/flows/notes.md", "x\n")
        self.p.write("README.md", "x\n")
        sc = self.scope()
        self.assertEqual(sc["files"], [])
        self.assertEqual({f["path"]: f["why"] for f in sc["out_of_scope"]},
                         {"demo/legacy.py": "不在任何層（舊程式碼或其他檔案）", "demo/flows/notes.md": "不是 Python 檔", "README.md": "不是 Python 檔"})

    def test_deleted_file_is_not_listed(self):
        (self.p.root / "demo" / "flows" / "report.py").unlink()
        sc = self.scope()
        self.assertEqual((sc["files"], sc["out_of_scope"]), ([], []))

    def test_clean_tree_has_nothing_to_review(self):
        r = self.p.air("review")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("沒有要審查的變更", r.stdout)
        self.assertNotIn("交回", r.stdout)

    def test_text_output_has_sources_and_diff_command(self):
        self.p.write("demo/flows/report.py", FLOW_AFTER)
        self.p.write("demo/steps/calc.py", STEP_NEW)
        out = self.p.air("review").stdout
        self.assertIn("demo/flows/report.py（flows，修改；改到的行：3–4）", out)
        self.assertIn("FLOW-3【必須】", out)
        self.assertIn("（AIR/rules/flows.md:7）", out)
        self.assertIn("只審程式抓不到的部分：", out)  # STEP-2 的缺口
        self.assertIn(f"git diff --no-renames {self.base[:12]} -- demo/flows/report.py", out)
        self.assertIn("新檔案（整個檔案都在範圍，直接讀）：demo/steps/calc.py", out)
        self.assertIn("review --result -", out)

    def test_base_covers_commits_since_merge_base_and_uncommitted(self):
        self.git("branch", "base")
        self.git("checkout", "-q", "-b", "work")
        self.p.write("demo/flows/report.py", FLOW_AFTER)
        self.p.commit("committed on work")
        self.git("checkout", "-q", "base")
        self.p.write("demo/steps/__init__.py", "# moved on base\n")
        self.p.commit("base moved on")  # base 自己也前進了：比對的基準要是 merge-base，不是 base 的最新 commit
        self.git("checkout", "-q", "work")
        self.p.write("demo/steps/calc.py", STEP_NEW)  # 未 commit 的也算
        sc = self.scope("--base", "base")
        self.assertEqual((sc["base"], sc["ref"]), ("base", self.base))
        self.assertEqual([(f["path"], f["status"], f["changed_lines"]) for f in sc["files"]],
                         [("demo/flows/report.py", "modified", [[3, 4]]), ("demo/steps/calc.py", "untracked", None)])
        self.assertEqual(self.scope()["files"][0]["path"], "demo/steps/calc.py")  # 沒有 --base 只看 HEAD 之後的

    def test_unknown_base_is_an_error(self):
        r = self.p.air("review", "--base", "nope")
        self.assertEqual(r.returncode, 2)
        self.assertIn("找不到 nope", r.stderr)

    def test_fingerprint_follows_content(self):
        self.p.write("demo/steps/calc.py", STEP_NEW)
        a = self.scope()["fingerprint"]
        self.assertEqual(self.scope()["fingerprint"], a)
        self.p.write("demo/steps/calc.py", STEP_NEW + "\n")
        self.assertNotEqual(self.scope()["fingerprint"], a)


class TestScopeWithoutHistory(ProjectTestCase):
    phase = "migrating"
    git = True

    def test_no_commits_yet_treats_everything_as_new(self):
        self.p.layer("steps", calc=STEP_NEW)
        r = self.p.air("review", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        sc = json.loads(r.stdout)
        self.assertIsNone(sc["ref"])
        self.assertEqual([(f["path"], f["status"]) for f in sc["files"]],
                         [("demo/steps/__init__.py", "untracked"), ("demo/steps/calc.py", "untracked")])
        self.assertIn("還沒有 commit", self.p.air("review").stdout)
        self.assertEqual(self.p.air("review", "--base", "main").returncode, 2)


class TestNotGit(ProjectTestCase):
    phase = "migrating"

    def test_review_needs_git(self):
        self.p.layer("steps", calc=STEP_NEW)
        r = self.p.air("review")
        self.assertEqual(r.returncode, 2)
        self.assertIn("不是 git repo", r.stderr)

    def test_status_with_record_but_no_git(self):
        self.p.write(".air/review.json", json.dumps({"schema": review.SCHEMA, "time": "2026-01-01T00:00:00", "base": None, "ref": None,
                                                     "fingerprint": "x", "files": [], "findings": [], "blocking": 0,
                                                     "overall": "pass", "explanation": ""}))
        self.assertIn("語意審查：無法判斷", self.p.air("status").stdout)


class TestResult(ReviewCase):
    def setUp(self):
        super().setUp()
        self.p.write("demo/flows/report.py", FLOW_AFTER)  # 改到第 3–4 行
        self.p.write("demo/steps/calc.py", STEP_NEW)      # 新檔案

    def assert_rejected(self, data, *fragments):
        r = self.submit(data)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("審查結果無效，沒有記錄", r.stderr)
        for fragment in fragments:
            self.assertIn(fragment, r.stderr)
        self.assertIsNone(self.record())

    def test_pass_with_no_findings_is_recorded(self):
        r = self.submit(result())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("通過（0 個發現", r.stdout)
        rec = self.record()
        self.assertEqual((rec["schema"], rec["blocking"], rec["overall"], rec["files"]),
                         (review.SCHEMA, 0, "pass", ["demo/flows/report.py", "demo/steps/calc.py"]))
        self.assertEqual(rec["ref"], self.base)
        self.assertIn("語意審查：通過（0 個發現，沒有 priority 0／1）", self.p.air("status").stdout)
        self.assertIn("與目前的變更一致", self.p.air("status").stdout)

    def test_blocking_finding_exits_1_and_resolves_rule_source(self):
        r = self.submit(result([finding(), finding(rule="STEP-6", path="demo/steps/calc.py", lines={"start": 1, "end": 1}, priority=2, confidence=1)]))
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("1 個 priority 0／1 的發現待處理", r.stdout)
        self.assertIn("[P1] FLOW-3【必須】demo/flows/report.py:3　把篩選移到 steps（信心 0.8；AIR/rules/flows.md:7）", r.stdout)
        self.assertIn("[P2] STEP-6【建議】demo/steps/calc.py:1", r.stdout)
        rec = self.record()
        self.assertEqual(rec["blocking"], 1)
        flow3 = rules.by_id()["FLOW-3"]
        self.assertEqual(rec["findings"][0]["rule_source"], {"path": flow3.source, "line": flow3.line})
        self.assertEqual(rec["findings"][0]["rule_text"], flow3.text)
        self.assertEqual(json.loads(self.p.air("status", "--json").stdout)["last_review"],
                         {"state": "fresh", "blocking": 1, "findings": 2, "time": rec["time"]})

    def test_result_from_file_and_json_output(self):
        path = self.p.write("out.json", json.dumps(result()))
        r = self.p.air("review", "--result", str(path), "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["schema"], review.SCHEMA)

    def test_newer_result_replaces_older(self):
        self.submit(result([finding()]))
        self.submit(result())
        self.assertEqual(self.record()["blocking"], 0)

    # ── 驗證：這些都不能記錄 ──

    def test_unknown_rule(self):
        self.assert_rejected(result([finding(rule="FLOW-9")]), "沒有規則 'FLOW-9'")

    def test_rule_of_another_layer(self):
        self.assert_rejected(result([finding(rule="STEP-4")]), "STEP-4 是 steps 的規則，demo/flows/report.py 屬於 flows")

    def test_rule_checked_by_program(self):
        self.assert_rejected(result([finding(rule="FLOW-1")]), "FLOW-1 由 python3 AIR/air.py check 負責")

    def test_partially_checked_rule_is_allowed(self):
        self.assertEqual(self.submit(result([finding(rule="FLOW-2")])).returncode, 1)

    def test_path_outside_scope(self):
        self.p.write("demo/legacy.py", "x = 1\n")
        self.assert_rejected(result([finding(path="demo/legacy.py")]), "'demo/legacy.py' 不在這次變更的範圍")
        self.assert_rejected(result([finding(path="demo/flows/report.py ")]), "不在這次變更的範圍")  # 寫法要跟 review 列的一樣

    def test_lines_must_be_near_the_change(self):
        self.assert_rejected(result([finding(lines={"start": 8, "end": 9})]), "第 8–9 行不在這次改到的行（3–4）附近")
        self.assertEqual(self.submit(result([finding(lines={"start": 7, "end": 7})])).returncode, 1)  # ±3 行以內
        self.assertEqual(self.submit(result([finding(lines={"start": 1, "end": 1})])).returncode, 1)

    def test_any_line_of_a_new_file_is_in_scope(self):
        self.assertEqual(self.submit(result([finding(rule="STEP-4", path="demo/steps/calc.py", lines={"start": 2, "end": 2})])).returncode, 1)

    def test_bad_lines(self):
        for lines in ({"start": 4, "end": 3}, {"start": 0, "end": 1}, {"start": "3", "end": 3}, {"start": 3}, [3, 4], {"start": True, "end": 3}):
            self.assert_rejected(result([finding(lines=lines)]), "lines 要是")

    def test_low_confidence_is_not_reported(self):
        self.assert_rejected(result([finding(confidence=0.4)]), "confidence 0.4 不到 0.5")
        for bad in (1.5, -0.1, "0.8", True, None):
            self.assert_rejected(result([finding(confidence=bad)]), "confidence 要是 0–1 的數字")

    def test_bad_priority(self):
        for bad in (4, -1, "1", 1.0, True):
            self.assert_rejected(result([finding(priority=bad)]), "priority 要是 0–3 的整數")

    def test_overall_must_match_findings(self):
        self.assert_rejected(result([finding(priority=1)], overall="pass"), "overall 應該是 fail：有 priority 0 或 1 的發現")
        self.assert_rejected(result([finding(priority=2)], overall="fail"), "overall 應該是 pass：沒有 priority 0 或 1 的發現")
        self.assert_rejected(result([], overall="ok"), "overall 只能是 pass 或 fail")

    def test_fixed_shape(self):
        self.assert_rejected(result(note="x"), "不認得的欄位：note")
        self.assert_rejected({"findings": []}, "缺少欄位：explanation、overall")
        self.assert_rejected(result([finding(extra=1)]), "findings[1]：不認得的欄位 extra")
        self.assert_rejected(result([{k: v for k, v in finding().items() if k != "body"}]), "findings[1]：缺少欄位 body")
        self.assert_rejected(result([finding(title=" ")]), "title 要是非空字串")
        self.assert_rejected(result(findings="x"), "findings 要是陣列")
        self.assert_rejected(result(["x"]), "findings[1]：要是物件")
        self.assert_rejected([], "結果要是 JSON 物件")

    def test_not_json(self):
        r = self.p.air("review", "--result", "-", stdin="{not json")
        self.assertEqual(r.returncode, 2)
        self.assertIn("不是 JSON", r.stderr)
        self.assertIsNone(self.record())

    def test_errors_are_all_listed(self):
        r = self.submit(result([finding(rule="ZZ-1", path="nope.py", confidence=2, priority=9)]))
        self.assertEqual(r.returncode, 2)
        for fragment in ("沒有規則", "不在這次變更的範圍", "confidence 要是", "priority 要是"):
            self.assertIn(fragment, r.stderr)

    # ── 紀錄綁定變更的內容 ──

    def test_record_goes_stale_when_reviewed_files_change_and_clean_after_commit(self):
        self.submit(result())
        state = lambda: json.loads(self.p.air("status", "--json").stdout)["last_review"]["state"]  # noqa: E731
        self.assertEqual(state(), "fresh")
        self.p.write("demo/steps/calc.py", STEP_NEW + "# more\n")
        self.assertEqual(state(), "stale")
        self.assertIn("已過期：之後變更的內容又變了，要重新審查", self.p.air("status").stdout)
        self.p.commit("batch")
        self.assertEqual(state(), "clean")
        self.assertIn("目前沒有要審查的變更", self.p.air("status").stdout)

    def test_record_with_base_is_checked_against_the_same_base(self):
        self.git("branch", "base")
        r = self.submit(result(), "--base", "base")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.record()["base"], "base")
        self.assertEqual(json.loads(self.p.air("status", "--json").stdout)["last_review"]["state"], "fresh")
        self.p.commit("batch")  # commit 之後，相對 base 的變更還在：紀錄仍然一致
        self.assertEqual(json.loads(self.p.air("status", "--json").stdout)["last_review"]["state"], "fresh")

    def test_corrupt_record_is_treated_as_none(self):
        self.p.write(".air/review.json", "{not json")
        self.assertIn("語意審查：無（", self.p.air("status").stdout)


class TestSchemaMatchesValidator(unittest.TestCase):
    """skill 資料夾裡的 schema.json（給非互動模式、CI 用）要跟程式的驗證一致。"""

    def test_keys_and_ranges(self):
        from tests.helpers import KIT
        schema = json.loads((KIT / "skills" / "air-review" / "schema.json").read_text(encoding="utf-8"))
        self.assertEqual(set(schema["required"]), set(review.TOP_KEYS))
        self.assertEqual(set(schema["properties"]), set(review.TOP_KEYS))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["overall"]["enum"], list(review.OVERALL))
        item = schema["properties"]["findings"]["items"]
        self.assertEqual(set(item["required"]), set(review.FINDING_KEYS))
        self.assertEqual(set(item["properties"]), set(review.FINDING_KEYS))
        self.assertFalse(item["additionalProperties"])
        self.assertEqual((item["properties"]["confidence"]["minimum"], item["properties"]["confidence"]["maximum"]), (0, 1))
        self.assertEqual((item["properties"]["priority"]["minimum"], item["properties"]["priority"]["maximum"]), (0, 3))
        self.assertEqual(set(item["properties"]["lines"]["required"]), {"start", "end"})


if __name__ == "__main__":
    unittest.main()
