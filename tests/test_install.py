"""安裝與移除：接點正確、可重複執行、只動 AIR 自己的東西、失敗不留下誤導的狀態。"""

import json
import os
import stat
import subprocess
import sys
import unittest
from pathlib import Path

from tests.helpers import ProjectTestCase
from airkit import hosts, install

HOOK_SCRIPTS = ["session_start.sh", "pre_bash.sh", "pre_edit.sh", "post_edit.sh", "stop.sh"]
EXPECTED_HOOKS = [("SessionStart", None), ("PreToolUse", "Bash"), ("PreToolUse", "Edit|Write|MultiEdit"),
                  ("PostToolUse", "Edit|Write|MultiEdit"), ("Stop", None)]
RULE_FILES = [f"air-{n}.md" for n in ("adapters", "contracts", "flows", "main", "steps", "storage", "tests")]


class TestInstructions(ProjectTestCase):
    def test_new_claude_md_imports_agents_md_which_holds_the_full_rules(self):
        install.ensure_instructions(self.p.root)
        text = (self.p.root / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("@AGENTS.md", text)
        self.assertNotIn("@AIR/AGENTS.md", text)  # 常駐規則全文在 AGENTS.md，不匯入兩份
        agents = (self.p.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("# AIR 規範（常駐）", agents)
        self.assertIn(install.agents_body(), agents)

    def test_existing_agents_md_keeps_its_content_and_gets_the_block(self):
        self.p.write("AGENTS.md", "# mine\n")
        install.ensure_instructions(self.p.root)
        self.assertIn("@AGENTS.md", (self.p.root / "CLAUDE.md").read_text(encoding="utf-8"))
        agents = (self.p.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertTrue(agents.startswith("# mine\n"))
        self.assertIn("AIR:BEGIN", agents)

    def test_users_own_claude_md_imports_air_agents_directly(self):
        self.p.write("CLAUDE.md", "# mine\n")
        install.ensure_instructions(self.p.root)
        text = (self.p.root / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# mine\n"))
        self.assertIn("@AIR/AGENTS.md", text)
        self.assertNotIn("@AGENTS.md\n", text)

    def test_dot_claude_claude_md_uses_relative_path_from_its_own_directory(self):
        # @ 匯入是相對於「包含它的檔案」解析（F07）
        self.p.write(".claude/CLAUDE.md", "# mine\n")
        install.ensure_instructions(self.p.root)
        text = (self.p.root / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("@../AIR/AGENTS.md", text)
        self.assertNotIn("@AIR/AGENTS.md", text)
        self.assertFalse((self.p.root / "CLAUDE.md").exists())

    def test_agents_block_is_refreshed_when_kit_text_changes(self):
        install.ensure_instructions(self.p.root)
        agents = self.p.root / "AGENTS.md"
        agents.write_text(agents.read_text(encoding="utf-8").replace("## 架構", "## 舊架構"), encoding="utf-8")
        out = install.ensure_instructions(self.p.root)
        self.assertIn("AGENTS.md：updated", out)
        self.assertIn("## 架構", agents.read_text(encoding="utf-8"))

    def test_agents_override_warns(self):
        self.p.write("AGENTS.override.md", "# o\n")
        self.assertTrue(any("AGENTS.override.md" in line for line in install.ensure_instructions(self.p.root)))

    def test_idempotent_and_removable(self):
        self.p.write("CLAUDE.md", "# mine\n")
        self.p.write("AGENTS.md", "# theirs\n")
        install.ensure_instructions(self.p.root)
        before = [(p.name, p.read_text(encoding="utf-8")) for p in (self.p.root / "CLAUDE.md", self.p.root / "AGENTS.md")]
        self.assertTrue(all(s.endswith("unchanged") for s in install.ensure_instructions(self.p.root)))
        self.assertEqual(before, [(p.name, p.read_text(encoding="utf-8")) for p in (self.p.root / "CLAUDE.md", self.p.root / "AGENTS.md")])
        install.uninstall(self.p.root, purge=False)
        self.assertEqual((self.p.root / "CLAUDE.md").read_text(encoding="utf-8"), "# mine\n")
        self.assertEqual((self.p.root / "AGENTS.md").read_text(encoding="utf-8"), "# theirs\n")

    def test_air_created_files_are_removed_entirely(self):
        install.ensure_instructions(self.p.root)
        install.uninstall(self.p.root, purge=False)
        self.assertFalse((self.p.root / "CLAUDE.md").exists())
        self.assertFalse((self.p.root / "AGENTS.md").exists())


class TestSettings(ProjectTestCase):
    def settings(self) -> dict:
        return json.loads((self.p.root / ".claude" / "settings.json").read_text(encoding="utf-8"))

    def air_hooks(self, data: dict) -> list[tuple[str, str | None, str]]:
        return [(event, entry.get("matcher"), h["command"]) for event, entries in data.get("hooks", {}).items()
                for entry in entries for h in entry["hooks"] if "AIR/hooks/" in h["command"]]

    def test_five_hooks_with_matchers_timeouts_and_host_argument(self):
        install.ensure_hooks(self.p.root, hosts.CLAUDE)
        hooks_ = self.air_hooks(self.settings())
        self.assertEqual([(e, m) for e, m, _ in hooks_], EXPECTED_HOOKS)
        for _, _, command in hooks_:
            self.assertTrue(command.endswith(" claude"), command)
            self.assertIn('"$CLAUDE_PROJECT_DIR"', command)
        for entries in self.settings()["hooks"].values():
            for entry in entries:
                for h in entry["hooks"]:
                    self.assertIn("timeout", h)
        self.assertNotIn("description", self.settings())  # settings.json 不加 Codex 才有的欄位

    def test_idempotent(self):
        self.assertEqual(install.ensure_hooks(self.p.root, hosts.CLAUDE), "updated")
        self.assertEqual(install.ensure_hooks(self.p.root, hosts.CLAUDE), "unchanged")
        self.assertEqual(len(self.air_hooks(self.settings())), len(EXPECTED_HOOKS))

    def test_user_hook_mentioning_air_is_not_mistaken_for_ours(self):
        # 子字串比對會把使用者的 echo 當成 AIR 的 hook（F09）
        user = {"matcher": "Read", "hooks": [{"type": "command", "command": "echo AIR/hooks/pre_bash.sh"}]}
        self.p.write(".claude/settings.json", json.dumps({"other": True, "hooks": {"PreToolUse": [user]}}))
        install.ensure_hooks(self.p.root, hosts.CLAUDE)
        pre = self.settings()["hooks"]["PreToolUse"]
        self.assertIn("Bash", [e.get("matcher") for e in pre])
        self.assertIn(user, pre)
        install.remove_hooks(self.p.root, hosts.CLAUDE)
        after = self.settings()
        self.assertEqual(after["hooks"]["PreToolUse"], [user])
        self.assertTrue(after["other"])

    def test_old_format_entries_are_upgraded_not_duplicated(self):
        old = [{"matcher": "Bash", "hooks": [{"type": "command", "command": '"$CLAUDE_PROJECT_DIR"/AIR/hooks/pre_bash.sh'}]},
               {"matcher": "Edit|Write|MultiEdit", "hooks": [{"type": "command", "command": 'sh "$CLAUDE_PROJECT_DIR"/AIR/hooks/pre_edit.sh', "timeout": 30}]}]
        self.p.write(".claude/settings.json", json.dumps({"hooks": {"PreToolUse": old}}))
        install.ensure_hooks(self.p.root, hosts.CLAUDE)
        pre = [h["command"] for e in self.settings()["hooks"]["PreToolUse"] for h in e["hooks"]]
        self.assertEqual(sum("pre_bash" in c for c in pre), 1)
        self.assertEqual(sum("pre_edit" in c for c in pre), 1)
        self.assertTrue(all(c.startswith("sh ") and c.endswith(" claude") for c in pre))

    def test_remove_cleans_up_empty_structures(self):
        install.ensure_hooks(self.p.root, hosts.CLAUDE)
        install.remove_hooks(self.p.root, hosts.CLAUDE)
        self.assertFalse((self.p.root / ".claude" / "settings.json").exists())

    def test_retired_read_hook_from_an_older_install_is_replaced(self):
        # 2.6.1 以前裝的 settings.json 有 pre_read.sh：重新安裝要把它拿掉，不能當成使用者的 hook 留著
        old = {"matcher": "Read", "hooks": [{"type": "command", "command": hosts.hook_command(hosts.CLAUDE, "pre_read.sh"), "timeout": 30}]}
        self.p.write(".claude/settings.json", json.dumps({"hooks": {"PreToolUse": [old]}}))
        data = install.read_hooks(self.p.root / ".claude" / "settings.json")
        self.assertTrue(install.is_air_hook(old["hooks"][0]))
        self.assertIn("pre_read.sh", install.stale_hooks(data, hosts.CLAUDE))
        self.assertEqual(install.ensure_hooks(self.p.root, hosts.CLAUDE), "updated")
        commands = [c for _, _, c in self.air_hooks(self.settings())]
        self.assertFalse(any("pre_read" in c for c in commands))
        self.assertEqual(len(commands), len(EXPECTED_HOOKS))


class TestPathRules(ProjectTestCase):
    """Claude Code 的各層規則：.claude/rules/air-<層>.md，AIR/rules/<層>.md 的複本加 paths 前言，由宿主在讀寫該層時自己載入。"""

    def rules_dir(self):
        return self.p.root / ".claude" / "rules"

    def test_written_with_paths_frontmatter_marker_and_rule_text(self):
        out = install.ensure_path_rules(self.p.root, hosts.CLAUDE, self.p.cfg())
        self.assertEqual(sorted(p.name for p in self.rules_dir().iterdir()), RULE_FILES)
        self.assertIn(".claude/rules/：各層規則 created 7", out[0])
        steps = (self.rules_dir() / "air-steps.md").read_text(encoding="utf-8")
        self.assertTrue(steps.startswith('---\npaths:\n  - "demo/steps/**/*.py"\n---\n'), steps[:80])  # 含子套件
        self.assertIn(install.MANAGED, steps.splitlines()[4])
        self.assertIn("# steps/ 規則", steps)
        self.assertIn("**STEP-1【必須】**", steps)
        self.assertEqual(steps.count("---\n"), 2)
        tests = (self.rules_dir() / "air-tests.md").read_text(encoding="utf-8")
        self.assertIn('  - "tests/**/*.py"', tests)
        self.assertIn("TEST-1", tests)
        self.assertIn("unchanged 7", install.ensure_path_rules(self.p.root, hosts.CLAUDE, self.p.cfg())[0])
        self.assertEqual(install.ensure_path_rules(self.p.root, hosts.CODEX, self.p.cfg()), [])  # Codex 沒有路徑規則

    def test_globs_follow_the_package_dir(self):
        self.p.write(".air/project.toml", (self.p.root / ".air" / "project.toml").read_text(encoding="utf-8").replace('package-dir = "demo"', 'package-dir = "src/demo"'))
        self.assertEqual(install.rule_globs(self.p.cfg(), "adapters"), ["src/demo/adapters/**/*.py"])
        self.assertEqual(install.rule_globs(self.p.cfg(), "tests"), ["tests/**/*.py"])

    def test_stale_copy_is_refreshed_but_foreign_file_is_kept(self):
        install.ensure_path_rules(self.p.root, hosts.CLAUDE, self.p.cfg())
        steps = self.rules_dir() / "air-steps.md"
        steps.write_text(steps.read_text(encoding="utf-8").replace("STEP-1", "STEP-9"), encoding="utf-8")  # 舊版的複本（仍有標記）
        self.p.write(".claude/rules/air-flows.md", "# 使用者自己的檔案，剛好同名\n")
        self.assertEqual(install.path_rules_status(self.p.root, hosts.CLAUDE, self.p.cfg()), ([], ["air-flows.md"], ["air-steps.md"]))
        out = install.ensure_path_rules(self.p.root, hosts.CLAUDE, self.p.cfg())
        self.assertIn("updated 1", out[0])
        self.assertIn("unchanged 5", out[0])
        self.assertTrue(any("略過 .claude/rules/air-flows.md" in line and "不是 AIR 建立的" in line for line in out))
        self.assertIn("STEP-1", steps.read_text(encoding="utf-8"))
        self.assertEqual((self.rules_dir() / "air-flows.md").read_text(encoding="utf-8"), "# 使用者自己的檔案，剛好同名\n")
        (self.rules_dir() / "air-main.md").unlink()
        self.assertEqual(install.path_rules_status(self.p.root, hosts.CLAUDE, self.p.cfg())[0], ["air-main.md"])

    def test_remove_only_touches_managed_files_and_prunes_the_directory(self):
        install.ensure_path_rules(self.p.root, hosts.CLAUDE, self.p.cfg())
        self.p.write(".claude/rules/mine.md", "# mine\n")
        removed = install.remove_path_rules(self.p.root, hosts.CLAUDE)
        self.assertEqual(len(removed), len(RULE_FILES))
        self.assertEqual([p.name for p in self.rules_dir().iterdir()], ["mine.md"])
        (self.rules_dir() / "mine.md").unlink()
        install.ensure_path_rules(self.p.root, hosts.CLAUDE, self.p.cfg())
        install.remove_path_rules(self.p.root, hosts.CLAUDE)
        self.assertFalse((self.p.root / ".claude").exists())
        self.assertEqual(install.remove_path_rules(self.p.root, hosts.CODEX), [])


class TestHookScripts(ProjectTestCase):
    git = True

    def setUp(self):
        super().setUp()
        self.p.layer("steps", bad='print("x")\n')
        r = self.p.air("install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for s in HOOK_SCRIPTS:  # 模擬 fresh checkout：腳本沒有執行位元（F08）
            (self.p.root / "AIR" / "hooks" / s).chmod(0o644)

    def test_generated_commands_run_without_exec_bit(self):
        data = json.loads((self.p.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        commands = [h["command"] for entries in data["hooks"].values() for e in entries for h in e["hooks"]]
        self.assertEqual(len(commands), len(EXPECTED_HOOKS))
        for cmd in commands:
            with self.subTest(cmd=cmd):
                r = self.p.sh(cmd, stdin="{}", env={"CLAUDE_PROJECT_DIR": str(self.p.root)})
                self.assertNotIn("Permission denied", r.stderr)
                self.assertNotIn("not found", r.stderr)
                self.assertNotIn("內部錯誤", r.stderr)
                self.assertIn(r.returncode, (0, 2))

    def test_codex_commands_find_the_root_via_git_from_a_subdirectory(self):
        data = json.loads((self.p.root / ".codex" / "hooks.json").read_text(encoding="utf-8"))
        commands = [h["command"] for entries in data["hooks"].values() for e in entries for h in e["hooks"]]
        self.assertEqual(len(commands), len(hosts.CODEX.scripts))
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        stop = next(c for c in commands if "stop.sh" in c)
        import subprocess
        # 用 sh -c，不用 shell=True：Windows 的 shell=True 是 cmd.exe，不會展開 $(git rev-parse …)；宿主本來就是用 Git Bash 跑
        r = subprocess.run(["sh", "-c", stop], cwd=self.p.root / "demo", input="{}", capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("AIR001", r.stderr)

    def test_scripts_find_project_root_without_env_and_from_subdirectory(self):
        # 不依賴 $CLAUDE_PROJECT_DIR，也不依賴 cwd（A3）
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        import subprocess
        r = subprocess.run(["sh", str(self.p.root / "AIR" / "hooks" / "stop.sh")], cwd=self.p.root / "demo",
                           input="{}", capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("AIR001", r.stderr)

    def run_hook_with_path(self, bin_dir: Path, script: str = "stop.sh") -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
        return subprocess.run(["sh", str(self.p.root / "AIR" / "hooks" / script)], cwd=self.p.root / "demo", input="{}",
                              capture_output=True, text=True, encoding="utf-8", env=env)

    def fake_bin(self, python_ok: bool) -> Path:
        """放在 PATH 最前面的假指令：python3.x、python3 都像 Microsoft Store 的假指令一樣有指令卻跑不起來；python 依參數決定能不能用。"""
        bin_dir = self.p.root / "fakebin"
        bin_dir.mkdir()
        for name in ("python3.14", "python3.13", "python3.12", "python3.11", "python3", "py") + (("python",) if not python_ok else ()):
            self.p.write(f"fakebin/{name}", "#!/bin/sh\nexit 9009\n")
            (bin_dir / name).chmod(0o755)
        if python_ok:
            self.p.write("fakebin/python", f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
            (bin_dir / "python").chmod(0o755)
        return bin_dir

    @unittest.skipIf(sys.platform == "win32", "用 PATH 上的假指令模擬 Windows 的情況，本身需要 POSIX 的執行位元")
    def test_hook_falls_back_to_python_when_python3_is_a_stub_and_air_python_is_missing(self):
        # 新的 git worktree 沒有 .air/python；Windows 的 python3 是 Store 假指令：hook 要自己找到 python，不能整個靜默失效
        (self.p.root / ".air" / "python").unlink()
        r = self.run_hook_with_path(self.fake_bin(python_ok=True))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("AIR001", r.stderr)
        self.assertNotIn("cannot open", r.stderr)  # 沒有 .air/python 不是錯誤，不該吐 shell 的重導向訊息給模型看
        self.assertFalse((self.p.root / ".air" / "hook-errors.log").exists())

    @unittest.skipIf(sys.platform == "win32", "用 PATH 上的假指令模擬 Windows 的情況，本身需要 POSIX 的執行位元")
    def test_hook_without_any_python_logs_and_fails_open_but_commit_check_blocks(self):
        (self.p.root / ".air" / "python").unlink()
        bin_dir = self.fake_bin(python_ok=False)
        r = self.run_hook_with_path(bin_dir)
        self.assertEqual(r.returncode, 0, r.stderr)  # 放行，但不能靜默
        self.assertIn("找不到 Python", r.stderr)
        self.assertNotIn("cannot open", r.stderr)
        log = (self.p.root / ".air" / "hook-errors.log").read_text(encoding="utf-8")
        self.assertIn("stop.sh\t找不到 Python", log)
        self.assertIn("hook 錯誤（24 小時內）：1 次", self.p.air("status").stdout)
        r = self.run_hook_with_path(bin_dir, "commit_check.sh")  # commit 前檢查是關卡：找不到 Python 要擋下，不能放行
        self.assertEqual(r.returncode, 1)
        self.assertIn("commit 已擋下", r.stderr)

    def test_commit_check_blocks_commit(self):
        import subprocess
        subprocess.run(["git", "add", "-A"], cwd=self.p.root, check=True)
        # 指定 UTF-8：hook 的訊息含中文，Windows 預設的 cp1252 解不開，讀取執行緒會當掉、stderr 變成 None
        r = subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=self.p.root, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("AIR001", r.stdout + r.stderr)


class TestFullInstall(ProjectTestCase):
    git = True

    def test_install_reports_and_is_idempotent(self):
        r = self.p.air("install")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("安裝完成", r.stdout)
        self.assertTrue((self.p.root / ".air" / "python").exists())
        self.assertEqual((self.p.root / ".air" / "hosts").read_text(encoding="utf-8").split(), ["claude", "codex"])
        self.assertTrue((self.p.root / ".claude" / "skills" / "air-migrate" / ".air-managed").exists())
        self.assertTrue((self.p.root / ".agents" / "skills" / "air-migrate" / ".air-managed").exists())
        for base in (".claude/skills", ".agents/skills"):  # air-review 連同 Codex 的介面設定與 schema 一起裝
            self.assertTrue((self.p.root / base / "air-review" / "SKILL.md").exists(), base)
            self.assertTrue((self.p.root / base / "air-review" / "agents" / "openai.yaml").exists(), base)
            self.assertTrue((self.p.root / base / "air-review" / "schema.json").exists(), base)
        self.assertTrue((self.p.root / ".codex" / "hooks.json").exists())
        self.assertIn("信任", r.stdout)  # Codex 的 hook 要使用者信任
        self.assertEqual(sorted(p.name for p in (self.p.root / ".claude" / "rules").iterdir()), RULE_FILES)
        self.assertIn(".claude/rules/：各層規則 created 7", r.stdout)
        self.assertTrue(os.access(self.p.root / ".git" / "hooks" / "pre-commit", os.X_OK))
        ignore = (self.p.root / ".air" / ".gitignore").read_text(encoding="utf-8").split()
        for name in ("python", "hosts", "verify.json", "review.json", "sessions", "hook-errors.log", "last-check.json", "metrics.jsonl"):
            self.assertIn(name, ignore)
        r2 = self.p.air("install")
        self.assertEqual(r2.returncode, 0)
        self.assertIn("unchanged", r2.stdout)

    def test_dirty_worktree_is_fine_and_the_commit_list_only_has_air_paths(self):
        # 報告 §4.8：730 個未追蹤的實驗檔，「安裝前要乾淨」等於裝不了。改成列出安裝動到的路徑，commit 時只加這些
        self.p.write("scripts/exp1.py", "print(1)\n")
        self.p.write("notes.txt", "wip\n")
        r = self.p.air("install")
        self.assertEqual(r.returncode, 0, r.stderr)
        line = next(l for l in r.stdout.splitlines() if "commit 時只加這些" in l)
        listed = line.split("：", 1)[1].split()
        for expected in ("AIR", ".air/project.toml", ".air/.gitignore", "CLAUDE.md", "AGENTS.md", ".claude/settings.json",
                         ".claude/skills", ".claude/rules", ".codex/hooks.json", ".agents/skills"):
            self.assertIn(expected, listed, line)
        self.assertNotIn("scripts/exp1.py", listed)
        self.assertNotIn("notes.txt", listed)
        self.assertEqual(install.commit_paths(self.p.root, [hosts.CLAUDE]), [p for p in listed if not p.startswith((".codex", ".agents"))])

    def test_host_option_limits_what_is_installed(self):
        r = self.p.air("install", "--host", "claude")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.p.root / ".codex").exists())
        self.assertFalse((self.p.root / ".agents").exists())
        self.assertEqual((self.p.root / ".air" / "hosts").read_text(encoding="utf-8").split(), ["claude"])
        self.assertTrue((self.p.root / ".claude" / "rules" / "air-steps.md").exists())
        self.p.air("uninstall")
        self.assertEqual(self.p.air("install", "--host", "codex").returncode, 0)
        self.assertFalse((self.p.root / ".claude").exists())  # Codex 沒有路徑規則

    def test_invalid_settings_json_does_not_abort_the_rest(self):
        # 中途失敗不能留下「看起來裝好一半」的狀態（F10）：其他接點照做，失敗的列出來
        self.p.write(".claude/settings.json", "{invalid json")
        r = self.p.air("install")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("失敗", r.stdout)
        self.assertIn("settings.json", r.stdout)
        self.assertNotIn("安裝完成", r.stdout)
        self.assertTrue((self.p.root / ".git" / "hooks" / "pre-commit").exists())
        self.assertTrue((self.p.root / ".codex" / "hooks.json").exists())
        self.assertTrue((self.p.root / "CLAUDE.md").exists())
        self.assertNotIn("Traceback", r.stderr)

    @unittest.skipIf(sys.platform == "win32" or not hasattr(os, "geteuid") or os.geteuid() == 0, "root 與 Windows 不受檔案權限限制")
    def test_permission_error_is_reported_not_crashed(self):
        hooks_dir = self.p.root / ".git" / "hooks"
        hooks_dir.mkdir(exist_ok=True)
        hooks_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(hooks_dir.chmod, 0o755)
        r = self.p.air("install")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("失敗", r.stdout)
        self.assertIn("權限", r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        self.assertTrue((self.p.root / ".claude" / "settings.json").exists())

    def test_uninstall_restores(self):
        self.p.write("CLAUDE.md", "# mine\n")
        self.p.air("install")
        r = self.p.air("uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.p.root / "CLAUDE.md").read_text(encoding="utf-8"), "# mine\n")
        self.assertFalse((self.p.root / "AGENTS.md").exists())
        self.assertFalse((self.p.root / ".claude" / "settings.json").exists())
        self.assertFalse((self.p.root / ".claude" / "rules").exists())
        self.assertFalse((self.p.root / ".codex").exists())
        self.assertFalse((self.p.root / ".agents").exists())
        self.assertFalse((self.p.root / ".air" / "hosts").exists())
        self.assertFalse((self.p.root / ".git" / "hooks" / "pre-commit").exists())
        self.assertTrue((self.p.root / ".air" / "project.toml").exists())
        self.assertEqual(self.p.air("check").returncode, 0)  # 移除後不會因為接點不在而報 AIR009


class TestLegacyRuleFiles(ProjectTestCase):
    git = True

    def test_install_removes_old_managed_rule_files_but_keeps_users(self):
        self.p.layer("steps")
        self.p.layer("flows")
        self.p.write("demo/steps/CLAUDE.md", f"<!-- {install.MANAGED} 這個檔案由 AIR 產生 -->\n# 舊規則\n")
        self.p.write("demo/flows/CLAUDE.md", "# user's own\n")
        r = self.p.air("install")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("移除 demo/steps/CLAUDE.md", r.stdout)
        self.assertFalse((self.p.root / "demo" / "steps" / "CLAUDE.md").exists())
        self.assertEqual((self.p.root / "demo" / "flows" / "CLAUDE.md").read_text(encoding="utf-8"), "# user's own\n")

    def test_layers_command_no_longer_writes_rule_files(self):
        self.assertEqual(self.p.air("layers").returncode, 0)
        self.assertTrue((self.p.root / "demo" / "steps" / "__init__.py").exists())
        self.assertFalse((self.p.root / "demo" / "steps" / "CLAUDE.md").exists())


if __name__ == "__main__":
    unittest.main()
