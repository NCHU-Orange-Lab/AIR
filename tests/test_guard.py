"""Bash 指令攔截（guard.py）：擋下略過 commit 前檢查的寫法，放行一般指令。"""

import unittest

from tests import helpers  # noqa: F401  （設定 sys.path）
from airkit import guard

BLOCKED = [
    "git commit --no-verify -m x",
    "git commit -n -m x",
    "git commit -am x -n",
    "git commit -anm x",
    'git commit -m "fix" --no-verify',
    "SKIP=air-check git commit -m x",
    "env SKIP=air-check git commit -m x",
    "git -c core.hooksPath=/dev/null commit -m x",
    "git -c core.hookspath=/tmp commit -m x",
    "git config core.hooksPath /tmp/none",
    "git config --local core.hooksPath /tmp/none",
    "git config --unset core.hooksPath",
    "rm -rf .git/hooks",
    "rm .git/hooks/pre-commit",
    "pre-commit uninstall",
    "cd sub && git commit --no-verify -m x",
    "git status; git commit -n -m x",
    "/usr/bin/git commit --no-verify -m x",
]

ALLOWED = [
    "git commit -m 'fix -n flag'",
    'git commit -m "--no-verify in message"',
    "git commit -m x",
    "git commit --no-edit",
    "git -C sub commit -m x",
    "git status",
    "git config --get core.hooksPath",
    "git config --get-all core.hooksPath",
    "git config --list",
    "git config -l",
    "git config user.name",
    "echo hi",
    "rm -rf build",
    "pre-commit run --all-files",
    "python3 AIR/air.py check",
]


class TestGuard(unittest.TestCase):
    def test_blocked(self):
        for cmd in BLOCKED:
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(guard.check(cmd))

    def test_allowed(self):
        for cmd in ALLOWED:
            with self.subTest(cmd=cmd):
                self.assertIsNone(guard.check(cmd))


class TestNestedShell(unittest.TestCase):
    def test_bash_c_is_inspected(self):
        self.assertIsNotNone(guard.check('bash -c "git commit --no-verify -m x"'))

    def test_sh_lc_with_env(self):
        self.assertIsNotNone(guard.check("sh -lc 'SKIP=air-check git commit -m y'"))

    def test_zsh_c(self):
        self.assertIsNotNone(guard.check("zsh -c 'git commit -n -m x'"))

    def test_nested_twice(self):
        self.assertIsNotNone(guard.check('bash -c "sh -c \'git commit -n -m x\'"'))

    def test_harmless_nested(self):
        self.assertIsNone(guard.check('bash -c "git status && ls"'))


class TestRemote(unittest.TestCase):
    """報告 §4.9：`ssh host 'git commit --no-verify'` 不經過 guard。比照 bash -c 拆開遠端指令。"""

    def test_remote_commit_that_skips_hooks_is_blocked(self):
        for cmd in ("ssh lab02 'git commit --no-verify -m x'",
                    'ssh -p 22 -i ~/.ssh/key user@lab02 "cd ~/repo && git commit -n -m x"',
                    "ssh -p22 lab02 git commit --no-verify -m x",
                    "ssh -o StrictHostKeyChecking=no lab02 'SKIP=air-check git commit -m x'",
                    "ssh lab02 'bash -c \"git commit --no-verify -m x\"'",
                    "ssh lab02 'git status; git config core.hooksPath /tmp/none'"):
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(guard.check(cmd))

    def test_harmless_remote_commands_pass(self):
        for cmd in ("ssh lab02 git status", "ssh lab02 'git commit -m x'", "ssh -t lab02", "ssh lab02", "ssh -p 22 lab02 ./run_lab02.sh",
                    "ssh lab02 'git config --get core.hooksPath'"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(guard.check(cmd))

    def test_remote_script_parsing(self):
        self.assertEqual(guard.remote_script(["ssh", "-p", "22", "-v", "lab02", "git", "status"]), "git status")
        self.assertEqual(guard.remote_script(["ssh", "--", "lab02", "ls"]), "ls")
        self.assertIsNone(guard.remote_script(["ssh", "-p", "22", "lab02"]))
        self.assertIsNone(guard.remote_script(["scp", "a", "b"]))


class TestUnparseable(unittest.TestCase):
    def test_suspicious_literal_in_unparseable_command_is_blocked(self):
        # 引號不成對時 shlex 解析失敗；含可疑字面值就保守擋下（fail-closed）
        self.assertIsNotNone(guard.check('git commit --no-verify -m "oops'))
        self.assertIsNotNone(guard.check("SKIP=air-check git commit -m 'oops"))

    def test_harmless_unparseable_command_passes(self):
        self.assertIsNone(guard.check('echo "oops'))


if __name__ == "__main__":
    unittest.main()
