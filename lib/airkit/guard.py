"""判斷一行 shell 指令是否在略過 commit 前的檢查。PreToolUse（Bash）hook 使用。

用 shlex 解析，避免把 commit 訊息裡的文字誤判成選項。
這是盡力而為的防護：包在 `bash -c "..."`、變數展開、腳本檔裡的指令無法判斷。

"""

import re
import shlex

# git commit 中會吃掉下一個參數的短選項（-m "訊息" 等）
SHORT_WITH_ARG = set("mFCctS")
LONG_WITH_ARG = {
    "--message", "--file", "--author", "--date", "--template", "--cleanup",
    "--reuse-message", "--reedit-message", "--fixup", "--squash", "--trailer", "--gpg-sign",
}
GIT_GLOBAL_WITH_ARG = {"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
SEPARATORS = {";", "&&", "||", "|", "&"}


def split_commands(command: str) -> list[list[str]]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    commands, current = [], []
    for token in lexer:
        if token in SEPARATORS:
            commands.append(current)
            current = []
        else:
            current.append(token)
    commands.append(current)
    return [c for c in commands if c]


def commit_skips_hooks(args: list[str]) -> bool:
    """args 是 `git commit` 之後的參數。"""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            return False
        if a == "--no-verify":
            return True
        if a.startswith("--"):
            if a in LONG_WITH_ARG:
                i += 1
        elif a.startswith("-") and len(a) > 1:
            for j, ch in enumerate(a[1:]):
                if ch == "n":
                    return True
                if ch in SHORT_WITH_ARG:
                    if j == len(a) - 2:
                        i += 1  # 參數是下一個 token
                    break
        i += 1
    return False


def strip_env(tokens: list[str]) -> tuple[list[str], list[str]]:
    """拆出指令前的環境變數設定（`SKIP=x git ...`、`env SKIP=x git ...`）。"""
    env, rest = [], list(tokens)
    while rest and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", rest[0]):
        env.append(rest.pop(0))
    if rest and rest[0] == "env":
        rest.pop(0)
        while rest and "=" in rest[0]:
            env.append(rest.pop(0))
    return env, rest


def check_hook_removal(prog: str, args: list[str]) -> str | None:
    if prog == "rm" and any(".git/hooks" in a for a in args):
        return "不能刪除 git hook。"
    if prog == "pre-commit" and "uninstall" in args:
        return "不能解除安裝 pre-commit。"
    return None


def check_one(tokens: list[str]) -> str | None:
    env, tokens = strip_env(tokens)
    if not tokens:
        return None
    prog, args = tokens[0].rsplit("/", 1)[-1], tokens[1:]
    if prog == "git":
        return check_git(env, args)
    return check_hook_removal(prog, args)


def check_git(env: list[str], rest: list[str]) -> str | None:
    rest = list(rest)
    while rest and rest[0].startswith("-"):
        opt = rest.pop(0)
        if opt == "-c" and rest and rest[0].lower().startswith("core.hookspath"):
            return "不能用 core.hooksPath 繞過 hook。"
        if opt in GIT_GLOBAL_WITH_ARG and rest:
            rest.pop(0)
    if not rest:
        return None
    sub, args = rest[0], rest[1:]
    if sub == "config" and any(a.lower() == "core.hookspath" for a in args):
        return "不能修改 core.hooksPath。"
    if sub != "commit":
        return None
    if any(e.startswith("SKIP=") for e in env):
        return "不能用 SKIP= 略過 pre-commit。"
    if commit_skips_hooks(args):
        return "不能用 --no-verify / -n 略過 pre-commit。"
    return None


def check(command: str) -> str | None:
    try:
        commands = split_commands(command)
    except ValueError:  # 引號不成對等無法解析的情況，交給 git 自己報錯
        return None
    for tokens in commands:
        reason = check_one(list(tokens))
        if reason:
            return reason
    return None
