"""常駐內容的預算（budget.py、doctor --context）：依宿主盤點一開場載入的檔案，有上限的算佔比，80% 注意、超過或 AIR 區塊落在外面是問題。
報告 §4.4：記憶索引 52 行看起來健康，卻有 26,073 bytes，最後 4 行沒被載入、沒有人收到警告。"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ProjectTestCase
from airkit import budget, doctor, install


class BudgetCase(ProjectTestCase):
    git = True

    def setUp(self):
        super().setUp()
        self.home = Path(tempfile.mkdtemp(prefix="air-home-")).resolve()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, ignore_errors=True))
        self.codex = self.home / ".codex"
        self.codex.mkdir()
        self.assertEqual(self.p.air("install").returncode, 0)
        self.env = {"HOME": str(self.home), "USERPROFILE": str(self.home), "CODEX_HOME": str(self.codex)}

    def memory(self, text: str) -> Path:
        p = budget.memory_file(self.p.root, self.home)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def items(self, with_air=False):
        return budget.inventory(self.p.root, self.p.cfg() if with_air else None, self.home, self.codex, with_air=with_air)

    def by_kind(self, kind: str) -> budget.Item:
        return next(it for it in self.items() if it.kind == kind)


class TestSlug(unittest.TestCase):
    def test_memory_slug_replaces_everything_but_alnum_and_dash(self):
        # 由現有目錄反推：/home/x/AIR_project → -home-x-AIR-project；.claude → -claude
        self.assertEqual(budget.memory_slug(Path("/home/x/AIR_project/.claude/worktrees/a-b")), "-home-x-AIR-project--claude-worktrees-a-b")


class TestInventory(BudgetCase):
    def test_launch_files_are_listed_and_path_scoped_rules_are_not(self):
        self.p.write(".claude/rules/mine.md", "# 我自己的規則，每次都載入\n")
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "CLAUDE.md").write_text("個人偏好 @notes.md\n", encoding="utf-8")
        (self.home / ".claude" / "notes.md").write_text("筆記\n", encoding="utf-8")
        (self.home / ".claude" / "rules").mkdir()
        (self.home / ".claude" / "rules" / "u.md").write_text("使用者規則\n", encoding="utf-8")
        (self.home / ".claude" / "rules" / "scoped.md").write_text("---\npaths:\n  - \"src/**\"\n---\n不是開場\n", encoding="utf-8")
        self.memory("索引\n")
        items = self.items()
        kinds = [(it.kind, it.path) for it in items if it.host == "Claude Code"]
        self.assertEqual(kinds, [
            ("專案 CLAUDE.md", "CLAUDE.md"),
            ("@ 匯入", "AGENTS.md"),                       # AIR 建立的 CLAUDE.md 匯入 AGENTS.md
            ("規則（沒有 paths，每次載入）", ".claude/rules/mine.md"),  # air-*.md 都有 paths，不算
            ("使用者 CLAUDE.md", "~/.claude/CLAUDE.md"),
            ("使用者 @ 匯入", "~/.claude/notes.md"),
            ("使用者規則（沒有 paths）", "~/.claude/rules/u.md"),
            ("自動記憶索引", f"~/.claude/projects/{budget.memory_slug(self.p.root)}/memory/MEMORY.md"),
        ])
        self.assertTrue(all(it.level == budget.OK for it in items), [it.note for it in items])
        codex = [it for it in items if it.host == "Codex"]
        self.assertEqual([it.kind for it in codex], ["AGENTS.md 鏈（$CODEX_HOME 的在前）", "AGENTS.md 鏈合計"])
        self.assertEqual(codex[-1].limit_bytes, budget.CODEX_DOC_MAX_BYTES)
        self.assertIn("AIR 區塊的結尾在第", codex[-1].note)
        self.assertIsNone(budget.status_line(self.p.root, self.home))           # 都沒到 80%：status 不多那一行
        self.assertNotIn("常駐內容", self.p.air("status", env=self.env).stdout)
        self.assertFalse(any(i.area == "常駐內容" for i in doctor.diagnose(self.p.root)))

    def test_missing_memory_is_a_note_not_a_problem(self):
        it = self.by_kind("自動記憶索引")
        self.assertEqual((it.level, it.bytes), (budget.OK, 0))
        self.assertIn("找不到", it.note)

    def test_imports_follow_the_importing_file_skip_missing_and_stop_at_four_hops(self):
        root = self.p.root
        self.p.write("docs/a.md", "見 @b.md 與 @missing.md，信箱 me@x.com\n```\n@notcode.md\n```\n")
        self.p.write("docs/b.md", "@sub/c.md\n")
        self.p.write("docs/sub/c.md", "@../d.md\n")
        self.p.write("docs/d.md", "@e.md\n")
        self.p.write("docs/e.md", "@f.md\n")
        self.p.write("docs/f.md", "最深\n")
        self.p.write("docs/notcode.md", "x\n")
        got = [p.relative_to(root).as_posix() for p in budget.imports_of(root / "docs" / "a.md", self.home)]
        self.assertEqual(got, ["docs/b.md", "docs/sub/c.md", "docs/d.md", "docs/e.md"])  # f 是第 5 層；missing、email、程式碼區塊都不算


class TestMemoryLimit(BudgetCase):
    def test_report_scenario_last_lines_are_not_loaded(self):
        # 報告 F1：52 行、26,073 bytes；第 48 行橫跨 24,580–25,067 bytes，第 49–52 行新 session 看不到
        lines = ["- " + "x" * 518 for _ in range(47)] + ["- " + "y" * 578] + ["- 暫停重構（token 太多）" + "z" * 200 for _ in range(4)]
        text = "\n".join(lines) + "\n"
        mem = self.memory(text)
        self.assertGreater(len(text.encode("utf-8")), budget.MEMORY_LIMIT_BYTES)
        it = self.by_kind("自動記憶索引")
        self.assertEqual((it.level, it.lines), (budget.FAIL, 52))
        self.assertIn("第 48 行起載不到", it.note)  # 橫跨上限的那一行算載不到（保守）
        self.assertEqual(budget.first_line_beyond(mem, budget.MEMORY_LIMIT_BYTES, budget.MEMORY_LIMIT_LINES), 48)
        self.assertEqual(it.long_lines, 52)
        line = budget.status_line(self.p.root, self.home)
        self.assertIn("自動記憶索引", line)
        self.assertIn("第 48 行起載不到", line)
        self.assertIn("doctor --context", line)
        self.assertIn("常駐內容：Claude Code", self.p.air("status", env=self.env).stdout)
        r = self.p.air("doctor", env=self.env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("[問題] 常駐內容：", r.stdout)
        self.assertIn("MEMORY.md", r.stdout)

    def test_eighty_percent_is_a_note_and_the_line_limit_counts_too(self):
        self.memory("- " + "x" * 20_400 + "\n")  # 20,403 bytes ≈ 82%
        it = self.by_kind("自動記憶索引")
        self.assertEqual(it.level, budget.WARN)
        self.assertGreaterEqual(it.ratio, 0.8)
        r = self.p.air("doctor", env=self.env)
        self.assertEqual(r.returncode, 0)
        self.assertIn("[注意] 常駐內容：", r.stdout)
        self.assertIn("常駐內容：", self.p.air("status", env=self.env).stdout)
        self.memory("".join(f"- 第 {i} 行\n" for i in range(1, 202)))  # 201 行、不到 3KB：行數的上限
        it = self.by_kind("自動記憶索引")
        self.assertEqual(it.level, budget.FAIL)
        self.assertIn("第 201 行起載不到", it.note)
        self.memory("- " + "x" * 10_000 + "\n")  # 40%：沒事
        self.assertEqual(self.by_kind("自動記憶索引").level, budget.OK)
        self.assertIsNone(budget.status_line(self.p.root, self.home))


class TestCodexChain(BudgetCase):
    def test_air_block_beyond_the_codex_limit_is_a_problem_and_config_can_raise_it(self):
        agents = self.p.root / "AGENTS.md"
        agents.write_text("# 專案自己的說明\n" + ("規則 x。\n" * 3300), encoding="utf-8")  # 約 33KB 在前面
        install.ensure_instructions(self.p.root)                                  # AIR 區塊附在尾端
        total = self.by_kind("AGENTS.md 鏈合計")
        self.assertEqual(total.level, budget.FAIL)
        self.assertIn("超過 32,768：Codex 讀不到它", total.note)
        self.assertIn("Codex：（合計）", budget.status_line(self.p.root, self.home))
        r = self.p.air("doctor", env=self.env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("[問題] 常駐內容：Codex", r.stdout)
        self.p.write(".codex/config.toml", "project_doc_max_bytes = 65536\n")
        total = self.by_kind("AGENTS.md 鏈合計")
        self.assertEqual((total.level, total.limit_bytes), (budget.OK, 65536))

    def test_codex_home_agents_comes_first_in_the_chain(self):
        (self.codex / "AGENTS.md").write_text("全域指示\n" * 10, encoding="utf-8")
        (self.codex / "config.toml").write_text("project_doc_max_bytes = 4096\n", encoding="utf-8")
        items = [it for it in self.items() if it.host == "Codex"]
        self.assertEqual([it.path for it in items[:2]], ["~/.codex/AGENTS.md", "AGENTS.md"])
        self.assertEqual(items[-1].limit_bytes, 4096)
        self.assertEqual(items[-1].bytes, items[0].bytes + items[1].bytes + 2)  # 中間用空行接
        self.assertIn("AIR 區塊的結尾在第", items[-1].note)


class TestClaudeMdGuide(BudgetCase):
    def test_claude_md_over_200_lines_is_only_a_note(self):
        claude = self.p.root / "CLAUDE.md"
        claude.write_text(claude.read_text(encoding="utf-8") + "\n".join(f"- 規則 {i}" for i in range(210)) + "\n", encoding="utf-8")
        it = self.by_kind("專案 CLAUDE.md")
        self.assertEqual(it.level, budget.WARN)
        self.assertIn("文件建議的 200 行", it.note)
        self.assertIsNone(it.ratio)  # 沒有硬上限，不算佔比
        self.assertEqual(self.p.air("doctor", env=self.env).returncode, 0)


class TestDoctorContext(BudgetCase):
    def test_full_table_and_json_include_air_injection(self):
        self.memory("索引\n")
        r = self.p.air("doctor", "--context", env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        for text in ("[Claude Code]", "[Codex]", "[AIR]", "SessionStart 注入（Claude Code）", "SessionStart 注入（Codex）", "自動記憶索引", "Claude Code 開場合計"):
            self.assertIn(text, r.stdout)
        data = json.loads(self.p.air("doctor", "--context", "--json", env=self.env).stdout)
        self.assertEqual(data["schema"], "air.context.v1")
        air = [it for it in data["items"] if it["host"] == "AIR"]
        self.assertEqual(len(air), 2)
        self.assertGreater(air[0]["bytes"], 500)
        self.memory("- " + "x" * 26_000 + "\n")
        self.assertEqual(self.p.air("doctor", "--context", env=self.env).returncode, 1)  # 有問題：exit 1


if __name__ == "__main__":
    unittest.main()
