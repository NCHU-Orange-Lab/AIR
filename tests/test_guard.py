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


class TestUnparseable(unittest.TestCase):
    def test_suspicious_literal_in_unparseable_command_is_blocked(self):
        # 引號不成對時 shlex 解析失敗；含可疑字面值就保守擋下（fail-closed）
        self.assertIsNotNone(guard.check('git commit --no-verify -m "oops'))
        self.assertIsNotNone(guard.check("SKIP=air-check git commit -m 'oops"))

    def test_harmless_unparseable_command_passes(self):
        self.assertIsNone(guard.check('echo "oops'))


if __name__ == "__main__":
    unittest.main()
