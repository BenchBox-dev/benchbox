from __future__ import annotations

import os
import re
from collections.abc import Sequence

PREFLIGHT_GOAL = "pr-preflight"
MAX_DEPTH = 8
MAKE_NAMES = frozenset({"make", "gmake"})
MAKE_VARIABLES = frozenset({"$MAKE", "${MAKE}", "$(MAKE)"})
SHELL_NAMES = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
TRANSPARENT_NAMES = frozenset({"command", "exec", "time", "nohup", "env", "nice", "timeout", "caffeinate"})
SHELL_KEYWORDS = frozenset({"if", "then", "else", "elif", "do", "while", "until", "!", "{", "}", "fi", "done", "esac"})
MAKE_SHORT_VALUE_FLAGS = frozenset("CfIoWEl")
MAKE_LONG_VALUE_FLAGS = frozenset(
    {
        "--directory",
        "--file",
        "--makefile",
        "--include-dir",
        "--old-file",
        "--assume-old",
        "--what-if",
        "--new-file",
        "--assume-new",
        "--eval",
        "--load-average",
        "--max-load",
    }
)
UV_RUN_VALUE_FLAGS = frozenset(
    {
        "--with",
        "--with-requirements",
        "--with-editable",
        "--python",
        "-p",
        "--project",
        "--directory",
        "--env-file",
        "--extra",
        "--group",
        "--package",
        "--only-group",
        "--no-group",
        "--index",
        "--default-index",
        "--config-file",
        "-w",
        "-m",
    }
)
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
FALLBACK_INVOCATION = re.compile(r"(?:^|[\s;&|(`{])(?:\S*/)?g?make(?:\s+\S+)*?\s+pr-preflight(?=[\s;&|)`}]|$)")


class _Unparseable(Exception):
    pass


def _scan_simple_commands(text: str) -> list[list[str]]:
    commands: list[list[str]] = []
    words: list[str] = []
    current: list[str] = []
    in_word = False
    length = len(text)
    index = 0

    def end_word() -> None:
        nonlocal in_word
        if in_word:
            words.append("".join(current))
            current.clear()
            in_word = False

    def end_command() -> None:
        end_word()
        if words:
            commands.append(list(words))
            words.clear()

    while index < length:
        char = text[index]
        if char == "\\":
            if index + 1 < length and text[index + 1] != "\n":
                current.append(text[index + 1])
                in_word = True
            index += 2
            continue
        if char == "'":
            end = text.find("'", index + 1)
            if end < 0:
                raise _Unparseable
            current.append(text[index + 1 : end])
            in_word = True
            index = end + 1
            continue
        if char == '"':
            index = _scan_double_quoted(text, index + 1, current, commands)
            in_word = True
            continue
        if text.startswith("$(", index) or char == "`":
            index = _scan_substitution(text, index, commands)
            current.append("$SUBST")
            in_word = True
            continue
        if char == "#" and not in_word:
            newline = text.find("\n", index)
            index = length if newline < 0 else newline
            continue
        if char in ";&|\n()":
            end_command()
            index += 1
            continue
        if char in " \t\r":
            end_word()
            index += 1
            continue
        current.append(char)
        in_word = True
        index += 1
    end_command()
    return commands


def _scan_double_quoted(text: str, index: int, current: list[str], commands: list[list[str]]) -> int:
    length = len(text)
    while index < length:
        char = text[index]
        if char == '"':
            return index + 1
        if char == "\\" and index + 1 < length:
            current.append(text[index + 1] if text[index + 1] in '"\\$`' else char + text[index + 1])
            index += 2
            continue
        if text.startswith("$(", index) or char == "`":
            index = _scan_substitution(text, index, commands)
            current.append("$SUBST")
            continue
        current.append(char)
        index += 1
    raise _Unparseable


def _scan_substitution(text: str, index: int, commands: list[list[str]]) -> int:
    opener = "$(" if text.startswith("$(", index) else "`"
    start = index + len(opener)
    if opener == "`":
        end = start
        while end < len(text) and text[end] != "`":
            end += 2 if text[end] == "\\" else 1
        if end >= len(text):
            raise _Unparseable
        commands.extend(_scan_simple_commands(text[start:end]))
        return end + 1
    depth = 1
    end = start
    quote = ""
    while end < len(text):
        char = text[end]
        if quote:
            if char == "\\" and quote == '"':
                end += 1
            elif char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char == "\\":
            end += 1
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                commands.extend(_scan_simple_commands(text[start:end]))
                return end + 1
        end += 1
    raise _Unparseable


def _basename(word: str) -> str:
    return os.path.basename(word)


def _strip_prefix(words: list[str]) -> list[str]:
    index = 0
    while index < len(words) and (words[index] in SHELL_KEYWORDS or ASSIGNMENT.match(words[index])):
        index += 1
    return words[index:]


def _make_goals(args: Sequence[str]) -> list[str]:
    goals: list[str] = []
    index = 0
    options_ended = False
    while index < len(args):
        arg = args[index]
        index += 1
        if options_ended or not arg.startswith("-") or arg == "-":
            if not ASSIGNMENT.match(arg):
                goals.append(arg)
            continue
        if arg == "--":
            options_ended = True
            continue
        if arg.startswith("--"):
            name = arg.split("=", 1)[0]
            if name in MAKE_LONG_VALUE_FLAGS and "=" not in arg:
                index += 1
            elif name in ("--jobs", "--output-sync") and "=" not in arg:
                if index < len(args) and args[index].isdigit():
                    index += 1
            continue
        cluster = arg[1:]
        for position, flag in enumerate(cluster):
            rest = cluster[position + 1 :]
            if flag in MAKE_SHORT_VALUE_FLAGS:
                if not rest:
                    index += 1
                break
            if flag == "j":
                if not rest and index < len(args) and args[index].isdigit():
                    index += 1
                break
            if flag == "O":
                break
    return goals


def _shell_payload(args: Sequence[str]) -> tuple[str, list[str]] | None:
    index = 0
    while index < len(args):
        arg = args[index]
        index += 1
        if arg in ("-o", "+o", "-O", "+O"):
            index += 1
            continue
        if arg == "--":
            return None
        if arg.startswith("--"):
            continue
        if not (arg.startswith("-") or arg.startswith("+")):
            return None
        if arg[0] == "-" and "c" in arg[1:] and arg[1:].isalpha():
            if index >= len(args):
                return None
            return args[index], list(args[index + 1 :])
    return None


def _uv_run_command(args: Sequence[str]) -> list[str] | None:
    if not args or args[0] != "run":
        return None
    rest = list(args[1:])
    if "--" in rest:
        return rest[rest.index("--") + 1 :]
    index = 0
    while index < len(rest):
        arg = rest[index]
        if not arg.startswith("-"):
            return rest[index:]
        index += 2 if arg in UV_RUN_VALUE_FLAGS else 1
    return []


def _unwrap_transparent(name: str, args: list[str]) -> list[str]:
    if name == "env":
        index = 0
        while index < len(args) and (args[index].startswith("-") or ASSIGNMENT.match(args[index])):
            index += 2 if args[index] in ("-u", "-C", "-S") else 1
        return args[index:]
    if name == "nice":
        if args[:1] == ["-n"]:
            return args[2:]
        return args[1:] if args[:1] and args[0].startswith("-") else args
    if name == "timeout":
        index = 0
        while index < len(args) and args[index].startswith("-"):
            index += 2 if args[index] in ("-s", "-k", "--signal", "--kill-after") else 1
        return args[index + 1 :]
    if name == "caffeinate":
        index = 0
        while index < len(args) and args[index].startswith("-"):
            index += 2 if args[index] in ("-t", "-w") else 1
        return args[index:]
    return args


def _command_is_preflight(words: list[str], depth: int) -> bool:
    words = _strip_prefix(words)
    if not words or depth > MAX_DEPTH:
        return False
    name = _basename(words[0])
    args = words[1:]
    if name in MAKE_NAMES or words[0] in MAKE_VARIABLES:
        return PREFLIGHT_GOAL in _make_goals(args)
    if name in TRANSPARENT_NAMES:
        return _command_is_preflight(_unwrap_transparent(name, args), depth + 1)
    if name == "uv":
        inner = _uv_run_command(args)
        return inner is not None and _command_is_preflight(inner, depth + 1)
    if name in SHELL_NAMES:
        payload = _shell_payload(args)
        if payload is None:
            return False
        text, positional = payload
        if "$" in text and PREFLIGHT_GOAL in positional:
            return True
        return _text_is_preflight(text, depth + 1)
    return False


def _text_is_preflight(text: str, depth: int) -> bool:
    try:
        commands = _scan_simple_commands(text)
    except _Unparseable:
        return FALLBACK_INVOCATION.search(text) is not None
    return any(_command_is_preflight(words, depth) for words in commands)


def wraps_pr_preflight(command: Sequence[str]) -> bool:
    return _command_is_preflight(list(command), 0)


def rejection_message(command: Sequence[str]) -> str | None:
    if not wraps_pr_preflight(command):
        return None
    return (
        "refusing to hold the host lock around 'make pr-preflight': its heavy steps already take the lock "
        "themselves, so wrapping the whole pipeline blocks every other heavy run for its full length. "
        "Run `make pr-preflight` directly, never wrap the whole pipeline"
    )
