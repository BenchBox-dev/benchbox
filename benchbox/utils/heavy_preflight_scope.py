from __future__ import annotations

import os
import re
import shlex
from collections.abc import Sequence

PREFLIGHT_GOAL = "pr-preflight"
MAKE_NAMES = frozenset({"make", "gmake"})
SHELL_NAMES = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
MAKE_VALUE_FLAGS = frozenset("CfIoWEl")
MAKE_LONG_VALUE_FLAGS = frozenset(
    {"--directory", "--file", "--makefile", "--include-dir", "--old-file", "--new-file", "--what-if", "--eval"}
)
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SEPARATOR_CHARS = frozenset(";&|()")
KEYWORDS = frozenset({"if", "then", "else", "elif", "do", "while", "until", "!", "{", "time", "exec", "command"})
MAX_DEPTH = 4


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
        elif arg.startswith("--"):
            if arg in MAKE_LONG_VALUE_FLAGS:
                index += 1
        else:
            cluster = arg[1:]
            for position, flag in enumerate(cluster):
                rest = cluster[position + 1 :]
                if flag in MAKE_VALUE_FLAGS:
                    index += 0 if rest else 1
                    break
                if flag == "j":
                    if not rest and index < len(args) and args[index].isdigit():
                        index += 1
                    break
    return goals


def _shell_payload(args: Sequence[str]) -> str | None:
    index = 0
    while index < len(args):
        arg = args[index]
        index += 1
        if arg in ("-o", "+o"):
            index += 1
        elif arg.startswith("--"):
            continue
        elif arg[:1] in "-+" and len(arg) > 1:
            if arg[0] == "-" and "c" in arg[1:]:
                return args[index] if index < len(args) else None
        else:
            return None
    return None


def _simple_commands(text: str) -> list[list[str]]:
    commands: list[list[str]] = []
    for line in text.splitlines():
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        current: list[str] = []
        for token in lexer:
            if set(token) <= SEPARATOR_CHARS:
                if current:
                    commands.append(current)
                current = []
            else:
                current.append(token)
        if current:
            commands.append(current)
    return commands


def _is_preflight(words: list[str], depth: int) -> bool:
    index = 0
    while index < len(words) and (words[index] in KEYWORDS or ASSIGNMENT.match(words[index])):
        index += 1
    if index >= len(words):
        return False
    name = os.path.basename(words[index])
    args = words[index + 1 :]
    if name in MAKE_NAMES:
        return PREFLIGHT_GOAL in _make_goals(args)
    if name in SHELL_NAMES and depth < MAX_DEPTH:
        payload = _shell_payload(args)
        return payload is not None and _text_is_preflight(payload, depth + 1)
    return False


def _text_is_preflight(text: str, depth: int) -> bool:
    try:
        commands = _simple_commands(text)
    except ValueError:
        return re.search(r"\bg?make\b[^;&|]*\bpr-preflight\b", text) is not None
    return any(_is_preflight(words, depth) for words in commands)


def wraps_pr_preflight(command: Sequence[str]) -> bool:
    return _is_preflight(list(command), 0)


def rejection_message(command: Sequence[str]) -> str | None:
    if not wraps_pr_preflight(command):
        return None
    return (
        "refusing to hold the host lock around 'make pr-preflight': its heavy steps already take the lock "
        "themselves, so wrapping the whole pipeline blocks every other heavy run for its full length. "
        "Run `make pr-preflight` directly, never wrap the whole pipeline"
    )
