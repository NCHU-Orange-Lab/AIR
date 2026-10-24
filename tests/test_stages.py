"""遷移階段的做法拆成 AIR/start/ 的六份檔案（stages.py）：START.md 只放共通原則與目錄，status、install、phase 指出該讀哪一份。

試跑時 AI 一開始就把整份 START.md 讀進對話、之後每次呼叫都背著它；SessionStart 只附當下那一節，但安裝前開始的 session 不會附。
"""

import re
import unittest

from tests.helpers import KIT, ProjectTestCase
from airkit import project, stages


class TestStageFiles(unittest.TestCase):
    def test_every_stage_has_its_own_file(self):
        for n, name in stages.FILES.items():
            path = KIT / "start" / name
            self.assertTrue(path.is_file(), name)
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith(f"# 階段 {n}："), name)
            self.assertIn("共通原則在 `AIR/START.md`", text)
            self.assertEqual(stages.text(n), text.strip())
        self.assertEqual(sorted(p.name for p in (KIT / "start").iterdir()), sorted(stages.FILES.values()))  # 沒有多出沒登記的檔案

    def test_each_stage_points_to_the_next_one(self):
        # 只讀當下那一份，所以每一份的結尾要說下一份在哪（最後一份除外）
        for n in range(1, 6):
            self.assertIn(f"`{stages.shown(n + 1)}`", stages.text(n), n)
        self.assertNotIn("AIR/start/", stages.text(6).split("\n", 3)[-1])

    def test_start_md_is_only_the_index(self):
        start = (KIT / "START.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(start), stages.START_MAX_CHARS, f"START.md 有 {len(start)} 字")
        self.assertEqual(re.findall(r"^## ", start, re.M), [])  # 各階段的做法不再放在這裡
        for n in stages.FILES:
            self.assertIn(f"`{stages.shown(n)}`", start)
        self.assertIn("python3 AIR/air.py status", start)
        self.assertIn("只有使用者自己的訊息算同意", start)  # 共通原則留在這裡

    def test_docs_quote_the_current_size(self):
        # 設計說明與 context.py 引用六份合計的字數：改階段檔時容易忘了同步，所以算出來核對（取到百位）
        total = sum(len(stages.text(n)) + 1 for n in stages.FILES)  # 檔案結尾的換行也算
        sentence = f"六份合計約 {round(total, -2):,} 字"
        self.assertIn(sentence, (KIT / "docs" / "設計說明.md").read_text(encoding="utf-8"))
        self.assertIn(sentence, (KIT / "lib" / "airkit" / "context.py").read_text(encoding="utf-8"))

    def test_phase_maps_to_a_stage_until_done(self):
        self.assertEqual({p: stages.for_phase(p) for p in project.PHASES},
                         {"installed": 2, "analyzed": 3, "planned": 4, "layered": 5, "migrating": 5, "done": None})
        self.assertIsNone(stages.path(9))
        self.assertEqual(stages.text(9), "")


class TestPointers(ProjectTestCase):
    git = True

    def test_status_says_which_file_to_read(self):
        out = self.p.air("status").stdout
        self.assertIn("現在讀：AIR/start/2-analyze.md（只讀這一份", out)
        self.assertEqual(project.status_data(self.p.cfg())["guide"], "AIR/start/2-analyze.md")
        project.set_phase(self.p.cfg(), "migrating")
        self.assertIn("現在讀：AIR/start/5-migrate.md", self.p.air("status").stdout)
        project.set_phase(self.p.cfg(), "done")
        self.assertNotIn("現在讀：", self.p.air("status").stdout)
        self.assertIsNone(project.status_data(self.p.cfg())["guide"])

    def test_paused_has_no_stage_to_read(self):
        project.pause(self.p.cfg(), "先趕比賽")
        self.assertNotIn("現在讀：", self.p.air("status").stdout)
        self.assertIsNone(project.status_data(self.p.cfg())["guide"])

    def test_phase_transition_names_the_next_file(self):
        self.p.write("docs/AIR-遷移報告.md", "# 報告\n")
        self.p.write(".air/project.toml", self.p.root.joinpath(".air/project.toml").read_text(encoding="utf-8").replace('command = ""', 'command = "true"'))
        r = self.p.air("phase", "analyzed")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("【AIR/start/3-plan.md（階段 3 的做法，已附上全文，不用再讀）】", r.stdout)
        self.assertIn(stages.text(3), r.stdout)                      # 全文：session 中途換階段，SessionStart 不會再附

    def test_same_stage_is_not_printed_twice(self):
        # layered → migrating 都是階段 5：建立分層時已經印過 5-migrate.md
        project.set_phase(self.p.cfg(), "layered")
        r = self.p.air("phase", "migrating")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("做法同上一個階段：AIR/start/5-migrate.md（已經讀過就不用再讀）", r.stdout)
        self.assertNotIn(stages.text(5), r.stdout)

    def test_done_prints_no_stage(self):
        project.set_phase(self.p.cfg(), "migrating")
        r = self.p.air("phase", "done", "--force", "--reason", "測試")
        self.assertIn("遷移階段：done", r.stdout)
        self.assertNotIn("做法", r.stdout)

    def test_install_names_the_analysis_file(self):
        r = self.p.air("install", "--package", "demo", "--package-dir", "demo", "--host", "claude")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("讀 AIR/start/2-analyze.md（只讀這一份", r.stdout)

    def test_not_installed_points_to_stage_one(self):
        (self.p.root / ".air" / "project.toml").unlink()
        r = self.p.air("status")
        self.assertEqual(r.returncode, 2)
        self.assertIn("AIR/start/1-install.md", r.stderr)


if __name__ == "__main__":
    unittest.main()
