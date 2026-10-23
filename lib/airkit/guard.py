"""判斷一行 shell 指令是否在略過 commit 前的檢查。PreToolUse（Bash）hook 使用。

用 shlex 解析，避免把 commit 訊息裡的文字誤判成選項。
`bash -c "..."`、`sh -lc '...'` 裡的指令會拆開再檢查（最多三層）；`ssh host '...'` 的遠端指令也一樣（遠端的 clone 不經過 AIR，
只能擋本機發出去的寫法；要可信還是以 CI 為準）。解析不了的指令若含可疑字面值就保守擋下。
這是盡力而為的防護：變數展開、腳本檔（`sh run.sh`）裡的指令無法判斷；權威的關卡是 commit 前檢查。
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
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
REMOTE = {"ssh"}
SSH_WITH_ARG = set("bcDEeFIiJLlmOopQRSWw")  # ssh 會吃下一個參數的選項（-p 22、-i key…）；-p22 這種黏在一起的也認得
MAX_DEPTH = 3
# git config 的唯讀用法（查 core.hooksPath 沒問題）與會改值的用法
CONFIG_READ = {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "-l", "--list", "get", "list"}
CONFIG_WRITE = {"--unset", "--unset-all", "--add", "--replace-all", "--edit", "-e", "set", "unset", "--remove-section", "--rename-section"}
# shlex 解析失敗（引號不成對）時，含這些字面值就擋下
SUSPICIOUS = ("--no-verify", "core.hookspath", "skip=", ".git/hooks", "pre-commit uninstall")


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


def config_writes_hooks_path(args: list[str]) -> bool:
    """`git config` 是否在設定 core.hooksPath（純查詢放行）。"""
    low = [a.lower() for a in args]
    if "core.hookspath" not in low or any(a in CONFIG_READ for a in low):
        return False
    if any(a in CONFIG_WRITE for a in low):
        return True
    return low.index("core.hookspath") != len(low) - 1  # 後面還有值就是在設定


def shell_script(rest: list[str]) -> str | None:
    """`bash -c '...'`、`sh -lc '...'` 裡的腳本；不是這種寫法回傳 None。"""
    if rest[0].rsplit("/", 1)[-1] not in SHELLS:
        return None
    for i, arg in enumerate(rest[1:-1], start=1):
        if arg.startswith("-") and not arg.startswith("--") and "c" in arg[1:]:
            return rest[i + 1]
    return None


def remote_script(rest: list[str]) -> str | None:
    """`ssh [選項] 主機 指令…` 裡的遠端指令（接成一條）；不是 ssh、或沒有指令（互動登入）回傳 None。"""
    if rest[0].rsplit("/", 1)[-1] not in REMOTE:
        return None
    i = 1
    while i < len(rest) and rest[i].startswith("-") and rest[i] != "-":
        opt = rest[i]
        if opt == "--":
            i += 1
            break
        i += 2 if (len(opt) == 2 and opt[1] in SSH_WITH_ARG) else 1
    if i + 1 >= len(rest):
        return None
    return " ".join(rest[i + 1:])


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
    if sub == "config":
        return "不能修改 core.hooksPath。" if config_writes_hooks_path(args) else None
    if sub != "commit":
        return None
    if any(e.startswith("SKIP=") for e in env):
        return "不能用 SKIP= 略過 pre-commit。"
    if commit_skips_hooks(args):
        return "不能用 --no-verify / -n 略過 pre-commit。"
    return None


def check(command: str, depth: int = 0) -> str | None:
    try:
        commands = split_commands(command)
    except ValueError:  # 引號不成對等無法解析的情況：含可疑字面值就擋，否則交給 shell 自己報錯
        low = command.lower()
        hit = next((s for s in SUSPICIOUS if s in low), None)
        return f"這行指令無法解析，而且含有 `{hit}`，先擋下。" if hit else None
    for tokens in commands:
        _env, rest = strip_env(tokens)
        script = (shell_script(rest) or remote_script(rest)) if rest and depth < MAX_DEPTH else None
        reason = check(script, depth + 1) if script is not None else check_one(tokens)
        if reason:
            return reason
    return None
