from __future__ import annotations

import json
import re
import shlex
import textwrap
from pathlib import PurePosixPath
from typing import Any

import bashlex
import bashlex.ast
import bashlex.errors
import yaml
from markdown_it import MarkdownIt

FENCE_LANGUAGES = {
    "py": "python",
    "python": "python",
    "python3": "python",
    "bash": "bash",
    "sh": "bash",
    "shell": "bash",
    "sql": "sql",
    "javascript": "javascript",
    "js": "javascript",
    "ts": "javascript",
    "typescript": "javascript",
    "tsx": "javascript",
    "jsx": "javascript",
    "css": "css",
    "html": "html",
    "yaml": "yaml",
    "yml": "yaml",
    "toml": "toml",
    "makefile": "make",
    "dockerfile": "docker",
    "json": "json",
    "ini": "ini",
    "c": "c",
    "powershell": "powershell",
    "console": "console",
    "groovy": "groovy",
    "sql+jinja": "sql+jinja",
}
DISPLAY_FENCES = {"", "text", "plaintext", "none", "output", "mermaid", "diff", "csv", "md", "markdown"}


ASTRO_FENCE_OPEN = re.compile(r"---[ \t]*\r?\n")
ASTRO_FENCE_CLOSE = re.compile(r"---[ \t]*(?:\r?\n|\Z)")
ASTRO_EMBEDDED_BLOCK = re.compile(r"(<(?:script|style)\b[^>]*>)(.*?)(</(?:script|style)\s*>)", re.I | re.S)
EXPRESSION_PREFIX = "[\n"
EXPRESSION_SUFFIX = "\n];"


BASHLEX_FAILURES = (
    bashlex.errors.ParsingError,
    NotImplementedError,
    AssertionError,
    AttributeError,
    IndexError,
    TypeError,
)


def blank_text(text: str) -> str:
    return re.sub(r"[^\n]", " ", text)


def js_string_end(text: str, pos: int, multiline: bool) -> int:
    quote = text[pos]
    pos += 1
    while pos < len(text):
        if text[pos] == "\\":
            pos += 2
        elif text[pos] == quote or (text[pos] == "\n" and not multiline):
            return pos + 1
        else:
            pos += 1
    raise ValueError("unterminated string in Astro source")


def js_template_end(text: str, pos: int, sink: list[tuple[int, str]] | None = None) -> int:
    pos += 1
    while pos < len(text):
        if text[pos] == "\\":
            pos += 2
        elif text[pos] == "`":
            return pos + 1
        elif text.startswith("${", pos):
            pos = expression_end(text, pos + 2, sink) + 1
        else:
            pos += 1
    raise ValueError("unterminated template literal in Astro source")


def jsx_starts(text: str, pos: int) -> bool:
    following = text[pos + 1 : pos + 2]
    previous = text[max(0, pos - 64) : pos].rstrip()[-1:]
    return (following.isalpha() or following == ">") and not (previous.isalnum() or previous in {"_", "$", ")", "]"})


def tag_end(
    text: str, pos: int, found: list[tuple[int, int]] | None = None, sink: list[tuple[int, str]] | None = None
) -> int:
    pos += 1
    while pos < len(text):
        char = text[pos]
        if char in "'\"":
            pos = js_string_end(text, pos, True)
        elif char == "{":
            end = expression_end(text, pos + 1, sink)
            if found is not None:
                found.append((pos + 1, end))
            pos = end + 1
        elif char == ">":
            return pos + 1
        else:
            pos += 1
    raise ValueError("unterminated tag in Astro source")


def jsx_end(text: str, pos: int, sink: list[tuple[int, str]] | None = None) -> int:
    pos = tag_end(text, pos, None, sink)
    if text[pos - 2 : pos] == "/>":
        return pos
    depth = 1
    while pos < len(text):
        if text[pos] == "{":
            pos = expression_end(text, pos + 1, sink) + 1
        elif text.startswith("<!--", pos):
            end = text.find("-->", pos + 4)
            if end < 0:
                raise ValueError("unterminated HTML comment in Astro source")
            if sink is not None:
                sink.append((pos, text[pos : end + 3]))
            pos = end + 3
        elif text.startswith("</", pos):
            pos = text.index(">", pos) + 1
            depth -= 1
            if depth == 0:
                return pos
        elif text[pos] == "<" and jsx_starts(text, pos):
            pos = tag_end(text, pos, None, sink)
            depth += text[pos - 2 : pos] != "/>"
        else:
            pos += 1
    raise ValueError("unterminated JSX element in Astro source")


REGEX_PRECEDERS = set("(,=:[!&|?{};+-*%<>~^")
REGEX_KEYWORDS = {"return", "typeof", "case", "in", "of", "void", "delete", "throw", "new", "yield", "await"}


def regex_allowed(text: str, pos: int, floor: int) -> bool:
    before = text[floor:pos].rstrip()
    if not before:
        return True
    if before[-2:] in {"++", "--"}:
        return False
    if before[-1] in REGEX_PRECEDERS:
        return True
    word = re.search(r"[A-Za-z_$][\w$]*\Z", before)
    return bool(word) and word.group() in REGEX_KEYWORDS


def regex_end(text: str, pos: int) -> int:
    pos += 1
    in_class = False
    while pos < len(text) and text[pos] != "\n":
        char = text[pos]
        if char == "\\":
            pos += 2
            continue
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            pos += 1
            while pos < len(text) and (text[pos].isalnum() or text[pos] in "_$"):
                pos += 1
            return pos
        pos += 1
    raise ValueError("unterminated regular expression in Astro source")


def skip_js_comment(text: str, pos: int) -> int:
    if text.startswith("//", pos):
        end = text.find("\n", pos)
        return len(text) if end < 0 else end
    end = text.find("*/", pos + 2)
    if end < 0:
        raise ValueError("unterminated comment in Astro source")
    return end + 2


def expression_end(text: str, pos: int, sink: list[tuple[int, str]] | None = None) -> int:
    depth = 0
    floor = pos
    while pos < len(text):
        char = text[pos]
        if char in "'\"":
            pos = js_string_end(text, pos, False)
        elif char == "`":
            pos = js_template_end(text, pos, sink)
        elif text.startswith(("//", "/*"), pos):
            pos = skip_js_comment(text, pos)
        elif char == "/" and regex_allowed(text, pos, floor):
            pos = regex_end(text, pos)
        elif char == "<" and jsx_starts(text, pos):
            pos = jsx_end(text, pos, sink)
        elif char == "}" and depth == 0:
            return pos
        else:
            depth += (char == "{") - (char == "}")
            pos += 1
    raise ValueError("unterminated expression in Astro source")


def next_frontmatter_state(text: str, pos: int, state: str, nesting: list[int], floor: int) -> tuple[int, str]:
    char = text[pos]
    if state == "block":
        return (pos + 2, "code") if text.startswith("*/", pos) else (pos + 1, state)
    if state == "line":
        return pos + 1, "code" if char == "\n" else state
    if state in {"'", '"'}:
        if char == "\\":
            return pos + 2, state
        return pos + 1, "code" if char in {state, "\n"} else state
    if state == "`":
        if char == "\\":
            return pos + 2, state
        if text.startswith("${", pos):
            nesting.append(0)
            return pos + 2, "code"
        return pos + 1, "code" if char == "`" else state
    if text.startswith("//", pos):
        return pos + 2, "line"
    if text.startswith("/*", pos):
        return pos + 2, "block"
    if char in "'\"`":
        return pos + 1, char
    if char == "/" and regex_allowed(text, pos, floor):
        return regex_end(text, pos), state
    if nesting and char == "}" and nesting[-1] == 0:
        nesting.pop()
        return pos + 1, "`"
    if nesting and char in "{}":
        nesting[-1] += 1 if char == "{" else -1
    return pos + 1, state


def astro_frontmatter_span(source: str) -> tuple[int, int, int] | None:
    opening = ASTRO_FENCE_OPEN.match(source)
    if not opening:
        return None
    pos, state, nesting, line_start = opening.end(), "code", [], True
    while pos < len(source):
        if line_start and state == "code" and not nesting:
            closing = ASTRO_FENCE_CLOSE.match(source, pos)
            if closing:
                return opening.end(), pos, closing.end()
        line_start = source[pos] == "\n"
        pos, state = next_frontmatter_state(source, pos, state, nesting, opening.end())
    raise ValueError("unterminated Astro frontmatter")


def blank_astro_frontmatter(source: str) -> str:
    span = astro_frontmatter_span(source)
    return source if span is None else blank_text(source[: span[2]]) + source[span[2] :]


def astro_template(source: str) -> str:
    return ASTRO_EMBEDDED_BLOCK.sub(
        lambda m: m.group(1) + blank_text(m.group(2)) + m.group(3), blank_astro_frontmatter(source)
    )


def astro_template_parts(source: str) -> tuple[list[tuple[int, str]], list[tuple[int, int]]]:
    template = astro_template(source)
    comments: list[tuple[int, str]] = []
    nested: list[tuple[int, str]] = []
    expressions: list[tuple[int, int]] = []
    pos = 0
    while pos < len(template):
        if template.startswith("<!--", pos):
            end = template.find("-->", pos + 4)
            if end < 0:
                raise ValueError("unterminated HTML comment in Astro source")
            comments.append((template[:pos].count("\n") + 1, template[pos : end + 3]))
            pos = end + 3
        elif template[pos] == "<" and (
            template[pos + 1 : pos + 2].isalpha() or template[pos + 1 : pos + 2] in {"/", ">"}
        ):
            pos = tag_end(template, pos, expressions, nested)
        elif template[pos] == "{":
            end = expression_end(template, pos + 1, nested)
            expressions.append((pos + 1, end))
            pos = end + 1
        else:
            pos += 1
    comments.extend((template[:start].count("\n") + 1, text) for start, text in nested)
    return sorted(comments, key=lambda item: item[0]), expressions


def astro_template_comments(source: str) -> list[tuple[int, str]]:
    return astro_template_parts(source)[0]


def astro_expression_sources(path: str, source: str) -> list[tuple[int, str, str, str, str]]:
    template = astro_template(source)
    return [
        (
            template[:start].count("\n"),
            path + ".tsx",
            EXPRESSION_PREFIX + template[start:end] + EXPRESSION_SUFFIX,
            "javascript",
            f"expression:{index}",
        )
        for index, (start, end) in enumerate(astro_template_parts(source)[1])
    ]


def example_blocks(source: str) -> list[tuple[int, str, str, str]]:
    blocks: list[tuple[int, str, str, str]] = []
    display_directives = {
        "tags",
        "toctree",
        "mermaid",
        "image",
        "figure",
        "include",
        "literalinclude",
        "postlist",
        "eval-rst",
    }
    containers = {
        "note",
        "tip",
        "warning",
        "important",
        "attention",
        "caution",
        "danger",
        "error",
        "hint",
        "admonition",
        "deprecated",
        "versionadded",
        "versionchanged",
        "seealso",
        "dropdown",
        "tab-set",
        "tab-item",
        "grid",
        "grid-item",
        "grid-item-card",
        "list-table",
    }
    for token in MarkdownIt("commonmark").parse(source):
        if token.type != "fence" or token.map is None:
            continue
        info = token.info.strip()
        directive = re.fullmatch(r"\{([^}]+)\}(?:\s+(.*))?", info)
        start = token.map[0] + 2
        if directive:
            name, argument = directive.groups()
            if name in display_directives:
                continue
            if name in containers:
                for child_start, tag, text, identity in example_blocks(token.content):
                    blocks.append((start + child_start - 1, tag, text, f"block:{len(blocks)}:{identity}"))
                continue
            if name not in {"code", "code-block", "sourcecode", "code-cell"}:
                raise ValueError(f"unregistered documentation directive: {name}")
            tag = (argument or "").split()[0] if argument else ""
            lines = token.content.splitlines(keepends=True)
            while lines and (not lines[0].strip() or lines[0].lstrip().startswith(":")):
                lines.pop(0)
                start += 1
            text = textwrap.dedent("".join(lines))
        else:
            tag = info.split()[0] if info else ""
            text = token.content
        if tag not in DISPLAY_FENCES:
            blocks.append((start, tag, text, f"block:{len(blocks)}"))
    lines = source.splitlines(keepends=True)
    pos = 0
    while pos < len(lines):
        directive = re.match(r"(\s*)\.\.\s+(?:code-block|code|sourcecode)::\s*(\S+)\s*$", lines[pos])
        if not directive:
            pos += 1
            continue
        indent, tag = directive.groups()
        pos += 1
        while pos < len(lines) and (not lines[pos].strip() or lines[pos].lstrip().startswith(":")):
            pos += 1
        start = pos
        while pos < len(lines) and (not lines[pos].strip() or len(lines[pos]) - len(lines[pos].lstrip()) > len(indent)):
            pos += 1
        blocks.append((start + 1, tag, textwrap.dedent("".join(lines[start:pos])), f"block:{len(blocks)}"))
    return blocks


def _wrapper_operand(words: list[str | None], index: int, wrapper: str) -> str:
    if index + 1 >= len(words):
        raise ValueError(f"{wrapper} option requires an operand: {words[index]}")
    operand = words[index + 1]
    if operand is None:
        raise ValueError(f"dynamic {wrapper} option operand requires an adapter")
    return operand


def _wrapper_tail(
    words: list[str | None], *, value_options: set[str], flag_options: set[str], wrapper: str
) -> list[str | None]:
    index = 0
    while index < len(words):
        word = words[index]
        if word is None:
            raise ValueError(f"dynamic {wrapper} option or executable requires an adapter")
        if word == "--":
            return words[index + 1 :]
        if word in {"-S", "--split-string"} and word in value_options:
            operand = _wrapper_operand(words, index, wrapper)
            if any(token in operand for token in ("#", "\\c", "$")):
                raise ValueError(f"unsupported {wrapper} split-string syntax")
            try:
                split_words = shlex.split(operand)
            except ValueError as exc:
                raise ValueError(f"malformed {wrapper} split-string operand") from exc
            if not split_words:
                raise ValueError(f"empty {wrapper} split-string operand")
            return _wrapper_tail(
                [*split_words, *words[index + 2 :]],
                value_options=value_options,
                flag_options=flag_options,
                wrapper=wrapper,
            )
        if word.startswith("--split-string=") and "--split-string" in value_options:
            if any(token in word.split("=", 1)[1] for token in ("#", "\\c", "$")):
                raise ValueError(f"unsupported {wrapper} split-string syntax")
            try:
                split_words = shlex.split(word.split("=", 1)[1])
            except ValueError as exc:
                raise ValueError(f"malformed {wrapper} split-string operand") from exc
            if not split_words:
                raise ValueError(f"empty {wrapper} split-string operand")
            return _wrapper_tail(
                [*split_words, *words[index + 1 :]],
                value_options=value_options,
                flag_options=flag_options,
                wrapper=wrapper,
            )
        if "=" in word and word.startswith("--"):
            option, _ = word.split("=", 1)
            if option in value_options:
                index += 1
                continue
        if word in value_options:
            _wrapper_operand(words, index, wrapper)
            index += 2
            continue
        if word in flag_options:
            index += 1
            continue
        if word.startswith("-"):
            raise ValueError(f"unregistered {wrapper} option: {word}")
        return words[index:]
    return []


def inline_source_index(words: list[str | None], language: str | None) -> int | None:
    inline_flags = {
        "python": {"-c"},
        "bash": {"-c", "-lc", "--command"},
        "javascript": {"-e", "--eval", "-p", "--print"},
        "sql": {"-c", "--command"},
    }.get(language or "", {"-c", "-e", "--eval", "--command", "-lc"})
    python_flags = {
        "-b",
        "-bb",
        "-B",
        "-d",
        "-E",
        "-i",
        "-I",
        "-O",
        "-OO",
        "-P",
        "-q",
        "-R",
        "-s",
        "-S",
        "-u",
        "-v",
        "-x",
    }
    index = 1
    while index < len(words):
        word = words[index]
        if word is None:
            raise ValueError("dynamic interpreter option requires an adapter")
        if word in inline_flags:
            if index + 1 < len(words):
                return index + 1
            raise ValueError("inline interpreter argument requires explicit support")
        if word.startswith("--") and word.split("=", 1)[0] in inline_flags:
            raise ValueError("inline interpreter argument requires explicit support")
        if language == "bash" and re.fullmatch(r"-[A-Za-z]+", word) and "c" in word[1:]:
            if index + 1 < len(words):
                return index + 1
            raise ValueError("inline interpreter argument requires explicit support")
        short_inline = {flag for flag in inline_flags if len(flag) == 2}
        if not word.startswith("--") and any(word.startswith(flag) for flag in short_inline):
            raise ValueError("inline interpreter argument requires explicit support")
        if language == "python":
            if word in {"-m", "--", "-"} or (word and not word.startswith("-")):
                return None
            if word not in python_flags and not word.startswith(("-W", "-X")):
                raise ValueError("interpreter option operand requires explicit support")
        if language == "bash" and word in {"-o", "+o"}:
            if index + 1 >= len(words) or words[index + 1] is None:
                raise ValueError("interpreter option operand requires explicit support")
            index += 2
            continue
        if language == "bash" and (word == "--" or (word and not word.startswith("-"))):
            return None
        if language == "python" and word in {"-W", "-X"}:
            if index + 1 >= len(words) or words[index + 1] is None:
                raise ValueError("interpreter option operand requires explicit support")
            index += 2
        else:
            index += 1
    return None


SQL_CLIENT_VALUE_OPTIONS = {
    "-separator",
    "-nullvalue",
    "-newline",
    "-init",
    "-vfs",
    "-maxsize",
    "-mmap",
    "-pagecache",
    "-lookaside",
    "-heap",
}
SQL_CLIENT_FLAGS = {
    "-batch",
    "-bail",
    "-header",
    "-noheader",
    "-csv",
    "-json",
    "-line",
    "-list",
    "-markdown",
    "-table",
    "-box",
    "-html",
    "-ascii",
    "-column",
    "-quote",
    "-readonly",
    "-no-stdin",
    "-unsigned",
    "-echo",
    "-interactive",
    "-safe",
    "-version",
    "-help",
}


def sql_client_source_index(name: str, words: list[str | None]) -> int | None:
    if name == "psql":
        index = 1
        while index < len(words):
            word = words[index]
            if word in {"-c", "--command"}:
                if index + 1 < len(words):
                    return index + 1
                raise ValueError("inline SQL client argument requires explicit support")
            if word is not None and (word.startswith("--command=") or (word.startswith("-c") and word != "-c")):
                raise ValueError("inline SQL client argument requires explicit support")
            index += 2 if word in PSQL_VALUE_OPTIONS else 1
        return None
    positional = 0
    index = 1
    while index < len(words):
        word = words[index]
        if word is None:
            raise ValueError("dynamic SQL client argument requires an adapter")
        if word in {"-c", "-s", "-cmd", "--command"}:
            if index + 1 < len(words):
                return index + 1
            raise ValueError("inline SQL client argument requires explicit support")
        if word in SQL_CLIENT_VALUE_OPTIONS:
            index += 2
            continue
        if word.startswith("-"):
            if word not in SQL_CLIENT_FLAGS:
                raise ValueError("SQL client option requires explicit support")
            index += 1
            continue
        positional += 1
        if positional == 2:
            return index
        index += 1
    return None


SQL_CLIENTS = {"psql", "duckdb", "sqlite3"}
PSQL_VALUE_OPTIONS = {
    "-h",
    "--host",
    "-p",
    "--port",
    "-U",
    "--username",
    "-d",
    "--dbname",
    "-f",
    "--file",
    "-v",
    "--set",
    "--variable",
    "-P",
    "--pset",
    "-o",
    "--output",
    "-L",
    "--log-file",
    "-T",
    "-F",
    "-R",
}


def command_words(words: list[str | None]) -> list[str | None]:
    if not words or words[0] is None:
        raise ValueError("dynamic shell command receiving a heredoc")
    name = words[0].rsplit("/", 1)[-1]
    if name == "env":
        tail = _wrapper_tail(
            words[1:],
            value_options={
                "-u",
                "--unset",
                "-C",
                "--chdir",
                "-S",
                "--split-string",
                "--block-signal",
                "--default-signal",
                "--ignore-signal",
                "--argv0",
            },
            flag_options={"-i", "--ignore-environment", "-0", "--null"},
            wrapper="env",
        )
        while tail and tail[0] is not None and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tail[0]):
            tail = tail[1:]
        return command_words(tail)
    if name == "uv" and words[1:2] == [None]:
        raise ValueError("dynamic uv command requires an adapter")
    if name == "uv" and words[1:2] == ["run"]:
        return command_words(
            _wrapper_tail(
                words[2:],
                value_options={
                    "--allow-insecure-host",
                    "--config-setting",
                    "--config-settings-package",
                    "--cache-dir",
                    "--config-file",
                    "--directory",
                    "--env-file",
                    "--exclude-newer",
                    "--exclude-newer-package",
                    "--extra",
                    "--extra-index-url",
                    "--find-links",
                    "--fork-strategy",
                    "--group",
                    "--index",
                    "--index-strategy",
                    "--index-url",
                    "--keyring-provider",
                    "--link-mode",
                    "--no-editable-package",
                    "--no-extra",
                    "--no-group",
                    "--no-sources-package",
                    "--only-group",
                    "--package",
                    "--python-platform",
                    "--project",
                    "--python",
                    "--prerelease",
                    "--refresh-package",
                    "--reinstall-package",
                    "--resolution",
                    "--upgrade-group",
                    "--upgrade-package",
                    "--with",
                    "--with-editable",
                    "--with-requirements",
                    "--color",
                    "-C",
                    "-f",
                    "-i",
                    "-p",
                    "-P",
                    "-w",
                },
                flag_options={
                    "--active",
                    "--all-extras",
                    "--all-groups",
                    "--all-packages",
                    "--compile-bytecode",
                    "--exact",
                    "--frozen",
                    "--inexact",
                    "--isolated",
                    "--locked",
                    "--managed-python",
                    "--native-tls",
                    "--no-active",
                    "--no-binary",
                    "--no-build",
                    "--no-build-isolation",
                    "--no-cache",
                    "--no-config",
                    "--no-default-groups",
                    "--no-dev",
                    "--no-editable",
                    "--no-managed-python",
                    "--no-project",
                    "--no-python-downloads",
                    "--no-reinstall",
                    "--no-sources",
                    "--no-sync",
                    "--offline",
                    "--only-dev",
                    "--quiet",
                    "--refresh",
                    "--reinstall",
                    "--verbose",
                    "--system-certs",
                    "--no-env-file",
                    "--no-index",
                    "--no-progress",
                    "-U",
                    "-n",
                    "-q",
                    "-v",
                },
                wrapper="uv run",
            )
        )
    return words


def stdin_language(words: list[str]) -> str | None:
    words = command_words(words)
    name = words[0].rsplit("/", 1)[-1]
    if name.startswith("python") or name in {"node", "bash", "sh", "zsh", "ksh"}:
        args = words[1:]
        if "-c" in args or "-e" in args or "--eval" in args or any(not arg.startswith("-") for arg in args):
            return None
        return "python" if name.startswith("python") else "javascript" if name == "node" else "bash"
    if name in {"psql", "duckdb", "mysql", "sqlite3"}:
        return "sql"
    if name in {"cat", "printf", "tee", "curl", "wget", "sed", "awk"}:
        return None
    raise ValueError(f"unregistered heredoc consumer: {name}")


HERESTRING_DATA_CONSUMERS = {"read", "mapfile", "readarray", "jq", "grep", "sort", "tr", "wc", "head", "tail", "cut"}


def herestring_data_only(header: str) -> None:
    text = re.sub(r"^(\s*)(?:if|elif|while|until)(?=\s)", lambda match: match.group(1) + "  ", header)
    text = re.sub(r"^(\s*)!(?=\s)", lambda match: match.group(1) + " ", text)
    text = re.sub(r"\s*;?\s*(?:then|do)\s*$", "\n", text)
    try:
        trees = bashlex.parse(text, strictmode=False)
    except BASHLEX_FAILURES as exc:
        raise ValueError("shell here-string requires an executable-payload adapter") from exc
    consumers: list[str] = []

    def visit(node: bashlex.ast.node) -> None:
        if node.kind == "command":
            words = [part.word for part in node.parts if part.kind == "word"]
            if any(part.kind == "redirect" and part.type == "<<<" for part in node.parts):
                consumers.append(words[0].rsplit("/", 1)[-1] if words else "")
        for child in [*getattr(node, "parts", []), *getattr(node, "list", [])]:
            visit(child)
        if getattr(node, "command", None) is not None:
            visit(node.command)

    for tree in trees:
        visit(tree)
    if not consumers or any(consumer not in HERESTRING_DATA_CONSUMERS for consumer in consumers):
        raise ValueError("shell here-string requires an executable-payload adapter")


def unclosed_shell_suffix(text: str) -> str:
    stack: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        top = stack[-1] if stack else None
        if top == "'":
            if char == "'":
                stack.pop()
        elif char == "\\":
            index += 1
        elif text.startswith("$(", index):
            stack.append(")")
            index += 1
        elif char == ")" and top == ")":
            stack.pop()
        elif char == '"':
            if top == '"':
                stack.pop()
            else:
                stack.append('"')
        elif char == "'" and top != '"':
            stack.append("'")
        index += 1
    if "'" in stack:
        raise ValueError("unterminated single quote in heredoc header")
    return "".join(reversed(stack))


def heredoc_redirects(header: str) -> list[tuple[str, str, list[str], bool]]:
    redirects = []
    header = re.sub(r"^(\s*)if(?=\s)", lambda match: match.group(1) + "  ", header)
    raw_markers = None
    try:
        trees = bashlex.parse(header, strictmode=False)
    except BASHLEX_FAILURES as exc:
        tokens = re.findall(r"(?<!<)<<-?\s*(['\"]?[A-Za-z_][A-Za-z0-9_]*['\"]?)", header)
        markers = [token.strip("'\"") for token in tokens]
        unquoted = re.sub(
            r"(?<!<)(<<-?\s*)['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]", r"\1\2", header.rstrip("\n").replace('"$(', "$(")
        )
        closed = "\n".join([unquoted, *markers, unclosed_shell_suffix(unquoted)]) + "\n"
        try:
            trees = bashlex.parse(closed, strictmode=False)
        except (*BASHLEX_FAILURES, ValueError):
            raise ValueError("shell heredoc header requires an adapter") from exc
        raw_markers = tokens

    def visit(node: bashlex.ast.node, pipeline: list | None = None) -> None:
        if node.kind == "pipeline":
            pipeline = [child for child in node.parts if child.kind == "command"]
        if node.kind == "command":
            words = [part.word for part in node.parts if part.kind == "word"]
            redirs = [part for part in node.parts if part.kind == "redirect" and part.type in {"<<", "<<-"}]
            for redirect in redirs:
                consumer = words
                if words[:1] == ["cat"] and pipeline and pipeline[0] is node:
                    if len(pipeline) != 2:
                        raise ValueError("multi-stage heredoc pipeline requires an adapter")
                    consumer = [part.word for part in pipeline[1].parts if part.kind == "word"]
                redirects.append((redirect, consumer, redirect is redirs[-1]))
        for child in getattr(node, "parts", []):
            visit(child, pipeline)
        for child in getattr(node, "list", []):
            visit(child, pipeline)
        if node.kind in {"commandsubstitution", "processsubstitution"}:
            visit(node.command)

    for tree in trees:
        visit(tree)
    redirects.sort(key=lambda item: item[0].pos[0])
    if raw_markers is None:
        raw_markers = [header[redirect.output.pos[0] : redirect.output.pos[1]] for redirect, _, _ in redirects]
    elif len(raw_markers) != len(redirects):
        raise ValueError("shell heredoc header requires an adapter")
    return [
        (raw, redirect.type, consumer, effective)
        for raw, (redirect, consumer, effective) in zip(raw_markers, redirects, strict=True)
    ]


def shell_payloads(path: str, source: str, include_data: bool = False) -> list[tuple[int, str, str, str, str]]:
    result = []
    lines = source.splitlines(keepends=True)
    index = 0
    while index < len(lines):
        header = lines[index]
        index += 1
        while header.rstrip().endswith("\\") and index < len(lines):
            header += lines[index]
            index += 1
        if not re.search(r"(?<!<)<<(?!<)", header):
            if "<<<" in header and not re.match(r"\s*done\s+<<<\s+", header):
                herestring_data_only(header)
            continue
        redirects = heredoc_redirects(header)
        for raw_marker, redirect_type, consumer, effective in redirects:
            parsed = shlex.split(raw_marker)
            if len(parsed) != 1:
                raise ValueError("dynamic heredoc delimiter requires an adapter")
            marker = parsed[0]
            start = index
            while (
                index < len(lines)
                and (lines[index].lstrip("\t") if redirect_type == "<<-" else lines[index]).rstrip("\r\n") != marker
            ):
                index += 1
            if index == len(lines):
                raise ValueError("unterminated shell heredoc")
            text = "".join(lines[start:index])
            if redirect_type == "<<-":
                text = "".join(line.lstrip("\t") for line in lines[start:index])
            unescaped = re.sub(r"\\.", "", text)
            nested_lang = stdin_language(consumer) if effective else None
            if not any(char in raw_marker for char in "'\"\\") and ("$(" in unescaped or "`" in unescaped):
                simple = re.sub(r"\$\([^()#`\n]*\)", "", unescaped)
                if nested_lang is not None or "$(" in simple or "`" in simple:
                    raise ValueError("executable substitution in a shell heredoc requires an adapter")
            if nested_lang or include_data:
                nested_lang = nested_lang or "data"
                result.append((start + 1, path + "." + nested_lang, text, nested_lang, f"heredoc:{marker}"))
            index += 1
    return result


def unwrap_static_command(words: list) -> list:
    command = words[0].word.rsplit("/", 1)[-1]
    if command in SHELL_WRAPPERS or (command == "uv" and [word.word for word in words[1:3]] == ["tool", "run"]):
        skip = 3 if command == "uv" else 2 if command == "timeout" else 1
        if command == "time":
            while skip < len(words) and not words[skip].parts and words[skip].word in TIME_FLAGS:
                skip += 1
            if skip > 1:
                return unwrap_static_command(words[skip:])
        if len(words) <= skip or any(word.parts or word.word.startswith("-") for word in words[1 : skip + 1]):
            raise ValueError("shell command wrapper options require an adapter")
        return unwrap_static_command(words[skip:])
    if not any(word.parts for word in words):
        return words
    if command == "uv" and [word.word for word in words[1:2]] == ["run"]:
        separator = next((i for i, word in enumerate(words) if word.word == "--" and not word.parts), None)
        if separator is not None and separator + 1 < len(words):
            return words[separator + 1 :]
        if len(words) > 2 and not words[2].parts and re.fullmatch(r"python[0-9.]*|node|bash|sh|zsh", words[2].word):
            return words[2:]
    if command == "env":
        index = 1
        while index < len(words) and (
            re.match(r"[A-Za-z_][A-Za-z0-9_]*=", words[index].word)
            or (not words[index].parts and words[index].word in {"-i", "--ignore-environment"})
        ):
            index += 1
        if index < len(words) and not words[index].parts and not words[index].word.startswith("-"):
            return words[index:]
    return words


SHELL_INLINE_INTERPRETER = re.compile(
    r"\beval\b|\b(?:python[0-9.]*|node|bash|sh|zsh)\b[^\n]*\s(?:-[A-Za-z]*[ceEp]|--eval|--command|--print)"
    r"|(?<![\w.-])(?:ksh|dash|fish|perl|ruby|php|lua|pwsh|powershell|deno|bun|tclsh|osascript|Rscript|awk|gawk|mawk"
    r"|psql|duckdb|sqlite3)(?=[\s;|&)]|$)"
    r"|(?<![\w.-])find\b[^\n]*\s-(?:exec|execdir|ok|okdir)\b"
)
SHELL_WRAPPERS = {"sudo", "nice", "nohup", "timeout", "time", "command", "exec", "stdbuf", "ionice", "uvx"}
TIME_FLAGS = {"-l", "-p"}


UNMODELED_MARKERS = {
    "perl": re.compile(r"#|^=[A-Za-z]", re.M),
    "ruby": re.compile(r"#|^=begin", re.M),
    "lua": re.compile(r"--"),
    "deno": re.compile(r"//|/\*"),
    "bun": re.compile(r"//|/\*"),
    "php": re.compile(r"#|//|/\*|<!--"),
    "osascript": re.compile(r"--|#|\(\*|//|/\*"),
    "pwsh": re.compile(r"#|<#"),
    "powershell": re.compile(r"#|<#"),
}
AWK_VALUE_OPTIONS = {"-v", "-F", "-f"}


def unmodeled_program_words(words: list, command: str) -> list:
    if command in {"awk", "gawk", "mawk"}:
        index = 1
        while index < len(words):
            word = words[index]
            if word.parts:
                if index > 1 and not words[index - 1].parts and words[index - 1].word in AWK_VALUE_OPTIONS:
                    index += 1
                    continue
                raise ValueError("inline source for an unmodeled interpreter requires an adapter")
            if word.word == "-f":
                return []
            if word.word in AWK_VALUE_OPTIONS:
                index += 2
                continue
            if word.word.startswith("-") and word.word != "-":
                index += 1
                continue
            return [word]
        return []
    programs = []
    positional = False
    for index, word in enumerate(words[1:], start=1):
        if word.parts:
            if not positional:
                raise ValueError("inline source for an unmodeled interpreter requires an adapter")
            continue
        text = word.word
        if command == "deno" and text == "eval":
            operands = [later for later in words[index + 1 :] if later.parts or not later.word.startswith("-")]
            if not operands:
                raise ValueError("inline source for an unmodeled interpreter requires an adapter")
            programs.append(operands[0])
        elif INLINE_OPTION.fullmatch(text):
            if index + 1 >= len(words):
                raise ValueError("inline source for an unmodeled interpreter requires an adapter")
            programs.append(words[index + 1])
        elif text.startswith("-") and not PLAIN_OPTION.fullmatch(text) and re.match(r"-[A-Za-z]*[ceErBR]", text):
            programs.append(word)
        elif not text.startswith("-"):
            positional = True
    return programs


def check_unmodeled_inline(words: list, command: str) -> None:
    markers = UNMODELED_MARKERS.get(command, re.compile(r"#"))
    for program in unmodeled_program_words(words, command):
        if program.parts or markers.search(program.word):
            raise ValueError("inline source for an unmodeled interpreter requires an adapter")


SHELL_UNMODELED = {
    "perl",
    "ruby",
    "php",
    "lua",
    "Rscript",
    "osascript",
    "pwsh",
    "powershell",
    "deno",
    "bun",
    "tclsh",
    "awk",
    "gawk",
    "mawk",
    "fish",
    "ksh",
    "dash",
}
FIND_EXEC_ACTIONS = {"-exec", "-execdir", "-ok", "-okdir"}


def runner_command_start(words: list[str | None], command: str) -> int:
    if command != "find":
        return 1
    for index, word in enumerate(words[1:], start=1):
        if word in FIND_EXEC_ACTIONS:
            if index + 1 >= len(words) or words[index + 1] is None:
                raise ValueError("dynamic find command requires an adapter")
            return index + 1
    return len(words)


SHELL_COMMAND_RUNNERS = {
    "find",
    "docker",
    "podman",
    "kubectl",
    "ssh",
    "xargs",
    "chroot",
    "nsenter",
    "flock",
    "watch",
    "su",
    "runuser",
    "doas",
}
SHELL_NESTED_TARGETS = {"node", "sh", "bash", "zsh", "eval"} | SQL_CLIENTS | SHELL_UNMODELED
COMMENT_MARKERS = re.compile(r"#|//|/\*|--|<#")
PLAIN_OPTION = re.compile(r"--?[A-Za-z][A-Za-z0-9_-]*|--")
INLINE_OPTION = re.compile(r"-[A-Za-z]*[ceErBR]|--(?:eval|command)")


def logical_line_end(
    source: str, index: int, depth: int, start: int, chunks: list[tuple[int, str]]
) -> tuple[int, str | None]:
    line = source[source.rfind("\n", 0, index) + 1 : index]
    marker = re.search(r"(?<!<)<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", line)
    if depth == 0:
        chunks.append((start, source[start:index]))
        start = index + 1
    return start, marker.group(1) if marker else None


def shell_logical_chunks(source: str) -> list[tuple[int, str]]:
    chunks = []
    start = index = depth = 0
    quote = None
    heredoc = None
    length = len(source)
    while index < length:
        char = source[index]
        if heredoc is not None:
            end = source.find("\n", index)
            end = length if end < 0 else end
            if source[index:end].strip("\t") == heredoc:
                heredoc = None
                start = end + 1
            index = end + 1
            continue
        if quote == "'":
            quote = None if char == "'" else quote
        elif char == "\\":
            index += 1
        elif quote == '"':
            if char == '"':
                quote = None
            elif source.startswith("$(", index):
                depth += 1
                index += 1
            elif char == ")" and depth:
                depth -= 1
        elif char in "'\"":
            quote = char
        elif source.startswith("$(", index):
            depth += 1
            index += 1
        elif char == ")" and depth:
            depth -= 1
        elif char == "#" and (index == 0 or source[index - 1] in " \t\n;"):
            end = source.find("\n", index)
            index = (length if end < 0 else end) - 1
        elif char == "\n":
            start, heredoc = logical_line_end(source, index, depth, start, chunks)
        index += 1
    if quote is not None or depth or heredoc is not None:
        raise ValueError("shell command source requires an executable-payload adapter")
    if start < length:
        chunks.append((start, source[start:]))
    return chunks


def neutral_list_operators(text: str) -> str:
    characters = list(text)
    quote = None
    index = 0
    while index < len(characters):
        char = characters[index]
        if quote == "'":
            quote = None if char == "'" else quote
        elif char == "\\":
            index += 1
        elif quote == '"':
            quote = None if char == '"' else quote
        elif char in "'\"":
            quote = char
        elif text.startswith(("||", "&&"), index):
            characters[index : index + 2] = [";", " "]
            index += 1
        index += 1
    return "".join(characters)


SHELL_MODELED_INLINE = re.compile(
    r"\beval\b|\b(?:python[0-9.]*|node|bash|sh|zsh)\b[^\n]*\s(?:-[A-Za-z]*[ceEp]|--eval|--command|--print)"
)
SHELL_UNMODELED_NAME = re.compile(
    r"(?<![\w.-])(ksh|dash|fish|perl|ruby|php|lua|pwsh|powershell|deno|bun|tclsh|osascript|Rscript|awk|gawk|mawk"
    r"|psql|duckdb|sqlite3)(?=[\s;|&)]|$)"
    r"|(?<![\w.-])find\b[^\n]*\s-(?:exec|execdir|ok|okdir)\b"
)


def chunk_can_hold_comment(chunk: str) -> bool:
    if SHELL_MODELED_INLINE.search(chunk):
        return True
    for match in SHELL_UNMODELED_NAME.finditer(chunk):
        name = match.group(1)
        markers = re.compile(r"--|/\*") if name in SQL_CLIENTS else UNMODELED_MARKERS.get(name, re.compile(r"#"))
        if markers.search(chunk, match.end()):
            return True
    return False


def shell_command_payloads(path: str, source: str) -> list[tuple[int, str, str, str, str]]:
    if not SHELL_INLINE_INTERPRETER.search(source):
        return []
    try:
        units = [(0, source, bashlex.parse(source))]
    except BASHLEX_FAILURES as exc:
        units = []
        for offset, chunk in shell_logical_chunks(source):
            if not SHELL_INLINE_INTERPRETER.search(chunk):
                continue
            try:
                units.append((offset, chunk, bashlex.parse(chunk)))
            except BASHLEX_FAILURES:
                try:
                    units.append((offset, chunk, bashlex.parse(neutral_list_operators(chunk))))
                except BASHLEX_FAILURES:
                    if not chunk_can_hold_comment(chunk):
                        continue
                    raise ValueError("shell command source requires an executable-payload adapter") from exc
    result = []
    for offset, text, trees in units:
        result.extend(shell_command_unit(path, source, offset, text, trees))
    return result


def shell_command_unit(path: str, source: str, offset: int, unit: str, trees: list) -> list:
    result = []

    def visit(node: bashlex.ast.node, symbol: str = "") -> None:
        if node.kind == "function":
            symbol = f"{symbol}.{node.name.word}".strip(".")
        if node.kind == "command":
            words = [part for part in node.parts if part.kind == "word"]
            if words:
                command = words[0].word.rsplit("/", 1)[-1]
                words = unwrap_static_command(words)
                command = words[0].word.rsplit("/", 1)[-1]
                if command in SHELL_COMMAND_RUNNERS:
                    first = runner_command_start([None if word.parts else word.word for word in words], command)
                    for index, word in enumerate(words[first:], start=first):
                        nested = word.word.rsplit("/", 1)[-1]
                        if not word.parts and (
                            re.fullmatch(r"python[0-9.]*", nested) or nested in SHELL_NESTED_TARGETS
                        ):
                            words = words[index:]
                            command = nested
                            break
                if command in SHELL_UNMODELED:
                    check_unmodeled_inline(words, command)
                inert = command == "uv" and len(words) > 1 and words[1].word not in {"run", "tool", "--"}
                if command in {"env", "uv"} and not inert:
                    if any(word.parts for word in words):
                        raise ValueError("dynamic inline wrapper arguments require an adapter")
                    normalized = command_words([word.word for word in words])
                    if normalized != [word.word for word in words[-len(normalized) :]]:
                        raise ValueError("inline split-string wrapper requires an adapter")
                    words = words[-len(normalized) :]
                    command = words[0].word.rsplit("/", 1)[-1]
                language = (
                    None
                    if inert
                    else "python"
                    if re.fullmatch(r"python[0-9.]*", command)
                    else "sql"
                    if command in SQL_CLIENTS
                    else {"node": "javascript", "sh": "bash", "bash": "bash", "zsh": "bash", "eval": "bash"}.get(
                        command
                    )
                )
                static_words = [None if word.parts else word.word for word in words]
                source_index = (
                    sql_client_source_index(command, static_words)
                    if command in SQL_CLIENTS
                    else inline_source_index(static_words, language)
                    if language and command != "eval"
                    else None
                )
                payload_words = (
                    words[1:]
                    if command == "eval"
                    else words[source_index : source_index + 1]
                    if source_index is not None
                    else []
                )
                if language and payload_words:
                    if any(word.parts for word in payload_words):
                        raise ValueError("unresolved executable shell argument: " + unit[node.pos[0] : node.pos[1]])
                    text = " ".join(word.word for word in payload_words)
                    line = source[: offset + payload_words[0].pos[0]].count("\n") + 1
                    result.append((line, path + "." + language, text, language, f"{symbol}:command:{command}"))
        for child in getattr(node, "parts", []):
            visit(child, symbol)
        for child in getattr(node, "list", []):
            visit(child, symbol)
        if node.kind in {"commandsubstitution", "processsubstitution"}:
            visit(node.command, symbol)

    for tree in trees:
        visit(tree)
    return result


def notebook_pip_install(line: str) -> bool:
    words = line.split()
    arguments = words[2:]
    archives = (
        ".whl",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".tgz",
        ".tbz",
        ".tbz2",
        ".txz",
        ".tlz",
        ".lz",
        ".lzma",
        ".egg",
    )
    return (
        words[:2] in [["%pip", "install"], ["!pip", "install"], ["!pip3", "install"]]
        and any(word not in {"--quiet", "-q"} for word in arguments)
        and all(
            word in {"--quiet", "-q"}
            or (
                not ("[" in word and "." in word.split("[", 1)[0])
                and not word.split("[", 1)[0].lower().endswith(archives)
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_.-]+(?:,[A-Za-z0-9_.-]+)*\])?", word)
            )
            for word in arguments
        )
    )


def notebook_python_sources(path: str, source: str, symbol: str, ipython: bool) -> list[tuple[int, str, str, str, str]]:
    import io
    import tokenize

    if not ipython:
        return [(1, path + ".python", source, "python", symbol)]
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError) as exc:
        raise ValueError("unresolved notebook Python tokenization") from exc
    protected = {
        line
        for token in tokens
        if token.type == tokenize.STRING and token.start[0] != token.end[0]
        for line in range(token.start[0], token.end[0] + 1)
    }
    lines = source.splitlines(keepends=True)
    shells = []
    magic_lines = set()
    for index, line in enumerate(lines):
        if index + 1 in protected or not line.lstrip().startswith(("!", "%")):
            continue
        if line[0].isspace():
            raise ValueError("unresolved indented notebook magic")
        if line.startswith("!") and not line.startswith("!!"):
            command = line[1:].rstrip("\r\n")
            if not command.strip() or any(char in command for char in "${}\\"):
                raise ValueError("unresolved notebook shell interpolation or continuation")
            shells.append((index + 1, path + ".sh", command, "bash", symbol + ":shell"))
            if not notebook_pip_install(line):
                shells.append((index + 1, path + ".unsupported", command, "unsupported", symbol + ":shell-semantics"))
        elif line.rstrip("\r\n") != "%matplotlib inline" and not notebook_pip_install(line):
            raise ValueError("unresolved notebook magic")
        magic_lines.add(index + 1)
        lines[index] = "\n" if line.endswith("\n") else ""
    python = "".join(lines)
    depth = 0
    for token in tokenize.generate_tokens(io.StringIO(python).readline):
        if token.start[0] in magic_lines and depth:
            raise ValueError("unresolved notebook magic in Python continuation")
        if token.type == tokenize.OP:
            depth += int(token.string in "([{") - int(token.string in ")]}")
    if any(start > 1 and source.splitlines()[start - 2].rstrip().endswith(chr(92)) for start in magic_lines):
        raise ValueError("unresolved notebook magic in Python continuation")
    return [(1, path + ".python", python, "python", symbol), *shells]


def notebook_sources(path: str, source: str) -> list[tuple[int, str, str, str, str]]:
    notebook = json.loads(source)
    info = notebook.get("metadata", {}).get("language_info", {})
    if not isinstance(info, dict):
        raise ValueError("unresolved notebook language metadata")
    declared = info.get("name", "python")
    mode = info.get("codemirror_mode")
    ipython = info.get("pygments_lexer") == "ipython3" or (isinstance(mode, dict) and mode.get("name") == "ipython")
    result = []
    for index, cell in enumerate(notebook["cells"]):
        if cell["cell_type"] != "code":
            continue
        text = "".join(cell["source"])
        symbol = f"cell:{cell.get('id', index)}"
        if declared == "python":
            try:
                result.extend(notebook_python_sources(path, text, symbol, ipython))
            except ValueError:
                result.append((1, path + ".unsupported", text, "unsupported", symbol))
        else:
            result.append((1, path + "." + declared, text, FENCE_LANGUAGES.get(declared, "unsupported"), symbol))
    return result


def structured_sources(path: str, source: str, lang: str) -> list[tuple[int, str, str, str, str]]:
    trees = list(yaml.compose_all(source)) if lang == "yaml" else [yaml.compose(source)]
    result = []

    active: set[int] = set()

    def visit(node: yaml.Node | None, symbol: str = "", github_script: bool = False, sql_mapping: bool = False) -> None:
        if node is not None and id(node) in active:
            raise ValueError("recursive YAML alias requires an adapter")
        active.add(id(node))
        if isinstance(node, yaml.MappingNode):
            github_script = github_script or any(
                isinstance(k, yaml.ScalarNode)
                and k.value == "uses"
                and isinstance(v, yaml.ScalarNode)
                and v.value.startswith("actions/github-script@")
                for k, v in node.value
            )
            for key, value in node.value:
                if (
                    isinstance(key, yaml.ScalarNode)
                    and isinstance(value, yaml.ScalarNode)
                    and (
                        key.value in {"run", "sql", "query"}
                        or key.value.endswith("_sql")
                        or (github_script and key.value == "script")
                        or sql_mapping
                    )
                ):
                    nested_lang = (
                        "bash"
                        if key.value == "run"
                        else "javascript"
                        if github_script and key.value == "script"
                        else "sql"
                    )
                    text = value.value
                    if path.startswith(".github/") and nested_lang in {"bash", "javascript"}:
                        text = github_expression_placeholders(text)
                    result.append(
                        (
                            value.start_mark.line + 1,
                            path + "." + nested_lang,
                            text,
                            nested_lang,
                            f"{symbol}.{key.value}",
                        )
                    )
                if isinstance(key, yaml.ScalarNode):
                    result.extend(command_key_sources(path, symbol, key, value, github_script))
                visit(
                    value,
                    f"{symbol}.{key.value}" if isinstance(key, yaml.ScalarNode) else symbol,
                    github_script,
                    isinstance(key, yaml.ScalarNode) and key.value == "platform_overrides",
                )
        elif isinstance(node, yaml.SequenceNode):
            for index, item in enumerate(node.value):
                identity = str(index)
                if isinstance(item, yaml.MappingNode):
                    identities = {
                        key.value: value.value
                        for key, value in item.value
                        if isinstance(key, yaml.ScalarNode) and isinstance(value, yaml.ScalarNode)
                    }
                    identity = identities.get("id", identities.get("name", identity))
                visit(item, f"{symbol}[{identity}]")
        active.remove(id(node))

    for index, tree in enumerate(trees):
        visit(tree, f"document:{index}" if len(trees) > 1 else "")
    return result


def nested_sources(path: str, source: str, lang: str) -> list[tuple[int, str, str, str, str]]:
    if lang == "groovy":
        return groovy_payloads(path, source)
    if lang == "html+jinja":
        parsed_template(source)
    if lang == "bash":
        return shell_payloads(path, source) + shell_command_payloads(path, source)
    if lang == "examples":
        return [
            (start, path + "." + tag, text, FENCE_LANGUAGES.get(tag, "unsupported"), symbol)
            for start, tag, text, symbol in example_blocks(source)
        ]
    if lang == "notebook":
        return notebook_sources(path, source)
    if lang in {"yaml", "json"}:
        return structured_sources(path, source, lang)
    if lang == "astro":
        span = astro_frontmatter_span(source)
        frontmatter = (
            [(1 + source[: span[0]].count("\n"), path + ".ts", source[span[0] : span[1]], "javascript", "frontmatter")]
            if span
            else []
        )
        return (
            frontmatter
            + nested_sources(path, blank_astro_frontmatter(source), "html")
            + astro_expression_sources(path, source)
        )
    if lang in {"html", "html+jinja"}:
        spans = template_data_spans(source) if lang == "html+jinja" else [(0, len(source))]
        return [
            (
                source[: match.start(2)].count("\n") + 1,
                path + (".js" if match.group(1).lower() == "script" else ".css"),
                match.group(2),
                (
                    "json"
                    if re.search(r"\btype\s*=\s*['\"]application/(?:ld\+)?json['\"]", match.group(), re.I)
                    else "javascript"
                )
                if match.group(1).lower() == "script"
                else "css",
                f"{match.group(1)}:{index}",
            )
            for index, match in enumerate(re.finditer(r"<(script|style)\b[^>]*>(.*?)</\1\s*>", source, re.I | re.S))
            if any(start <= match.start() and match.end() <= end for start, end in spans)
        ]
    return []


def command_key_sources(
    path: str, symbol: str, key: yaml.ScalarNode, value: yaml.Node, github_script: bool
) -> list[tuple[int, str, str, str, str]]:
    name = key.value
    line = value.start_mark.line + 1
    if name in {"command", "entrypoint", "entry"}:
        if isinstance(value, yaml.ScalarNode):
            return [(line, path + ".bash", value.value, "bash", f"{symbol}.{name}")]
        if isinstance(value, yaml.SequenceNode):
            if not all(isinstance(item, yaml.ScalarNode) for item in value.value):
                raise ValueError(f"non-scalar {name} argument requires an adapter")
            text = shlex.join(item.value for item in value.value)
            return [(line, path + ".bash", text, "bash", f"{symbol}.{name}")]
    if name == "script" and not github_script:
        if isinstance(value, yaml.ScalarNode):
            text = value.value
        elif isinstance(value, yaml.SequenceNode) and all(isinstance(item, yaml.ScalarNode) for item in value.value):
            text = "\n".join(item.value for item in value.value)
        else:
            raise ValueError("script value outside actions/github-script requires an adapter")
        try:
            tokens = shlex.split(text, comments=False)
        except ValueError as exc:
            raise ValueError("script value outside actions/github-script requires an adapter") from exc
        if any(token.startswith(("//", "/*")) or re.search(r"[;(,=!?{}]\s*(?://|/\*)", token) for token in tokens):
            raise ValueError("script value outside actions/github-script requires an adapter")
        return [(line, path + ".bash", text, "bash", f"{symbol}.{name}")]
    if (
        PurePosixPath(path).name == "package.json"
        and symbol == ""
        and name == "scripts"
        and isinstance(value, yaml.MappingNode)
    ):
        return [
            (item.start_mark.line + 1, path + ".bash", item.value, "bash", f".scripts.{entry.value}")
            for entry, item in value.value
            if isinstance(entry, yaml.ScalarNode) and isinstance(item, yaml.ScalarNode)
        ]
    return []


def github_expression_placeholders(text: str) -> str:
    return re.sub(
        r"\$\{\{.*?\}\}",
        lambda match: "GITHUB_EXPRESSION" + "\n" * match.group().count("\n"),
        text,
        flags=re.S,
    )


def parsed_template(source: str) -> Any:
    from jinja2 import Environment, TemplateSyntaxError

    try:
        return Environment().parse(source)
    except TemplateSyntaxError as exc:
        raise ValueError(f"invalid template syntax at line {exc.lineno}: {exc.message}") from exc


def bounded_html_template(source: str) -> None:
    from jinja2 import nodes

    tree = parsed_template(source)
    for match in re.finditer(r"<(script|style)\b[^>]*>(.*?)</\1\s*>", source, re.I | re.S):
        if re.search(r"\{\{|\{%|\{#", match.group(2)):
            raise ValueError("template syntax inside script or style requires an adapter")
    for constant in tree.find_all(nodes.Const):
        if isinstance(constant.value, str) and "<" in constant.value:
            raise ValueError("template string constant may emit markup")


def static_html_template(source: str) -> None:
    from jinja2 import nodes

    tree = parsed_template(source)
    for statement in tree.body:
        if not isinstance(statement, nodes.Output) or any(
            not isinstance(expression, nodes.TemplateData) for expression in statement.nodes
        ):
            raise ValueError("unresolved emitted HTML template source")


def _template_call(node: Any) -> str:
    from jinja2 import nodes

    if not isinstance(node.node, nodes.Name) or node.dyn_args is not None or node.dyn_kwargs is not None:
        raise ValueError("unresolved SQL template call")
    name = node.node.name
    if name in {"source", "ref"}:
        if node.kwargs or len(node.args) not in ({2} if name == "source" else {1, 2}):
            raise ValueError("unresolved SQL template identifier")
        if any(
            not isinstance(arg, nodes.Const)
            or not isinstance(arg.value, str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", arg.value) is None
            for arg in node.args
        ):
            raise ValueError("unresolved SQL template identifier")
        return "__dbt_identifier__"
    if name == "is_incremental" and not node.args and not node.kwargs:
        return "__dbt_flag__"
    if name == "config":
        if node.args:
            raise ValueError("unresolved SQL template configuration")
        values = {
            "materialized": {"table", "view", "incremental", "ephemeral"},
            "on_schema_change": {"ignore", "fail", "append_new_columns", "sync_all_columns"},
        }
        for keyword in node.kwargs:
            if not isinstance(keyword.value, nodes.Const) or not isinstance(keyword.value.value, str):
                raise ValueError("unresolved SQL template configuration")
            value = keyword.value.value
            valid = (
                re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", value)
                if keyword.key == "unique_key"
                else value in values.get(keyword.key, set())
            )
            if not valid:
                raise ValueError("unresolved SQL template configuration")
        return ""
    raise ValueError("unresolved SQL template call")


def _template_literal(node: Any, bindings: dict[str, list[Any]], seen: frozenset[str] = frozenset()) -> Any:
    from jinja2 import nodes

    if isinstance(node, nodes.Const) and isinstance(node.value, (str, int, float, bool, type(None))):
        return node.value
    if isinstance(node, nodes.Name):
        values = bindings.get(node.name, [])
        if not values and node.name == "this":
            return "__dbt_identifier__"
        if len(values) != 1 or node.name in seen:
            raise ValueError(f"unresolved SQL template output: {node.name}")
        return _template_literal(values[0], bindings, seen | {node.name})
    if isinstance(node, nodes.Concat):
        return "".join(str(_template_literal(operand, bindings, seen)) for operand in node.nodes)
    if isinstance(node, nodes.Add):
        return _template_literal(node.left, bindings, seen) + _template_literal(node.right, bindings, seen)
    if isinstance(node, nodes.Call):
        return _template_call(node)
    raise ValueError("unresolved SQL template output")


def _template_append(text: str, line: int, original: bool, parts: list[str], line_map: list[int]) -> None:
    parts.append(text)
    for char in text:
        line_map.append(line)
        if original and char == "\n":
            line += 1


def _template_render(
    body: list[Any], bindings: dict[str, list[Any]], variants: list[Any], branch: bool = False
) -> list[Any]:
    from jinja2 import nodes

    for statement in body:
        if isinstance(statement, nodes.Output):
            for expression in statement.nodes:
                original = isinstance(expression, nodes.TemplateData)
                text = expression.data if original else str(_template_literal(expression, bindings))
                for parts, line_map in variants:
                    _template_append(text, expression.lineno, original, parts, line_map)
        elif isinstance(statement, nodes.If):
            bodies = [statement.body, *(item.body for item in statement.elif_), statement.else_]
            expanded = []
            for parts, line_map in variants:
                for alternative in bodies:
                    expanded.extend(
                        _template_render(alternative, dict(bindings), [(parts.copy(), line_map.copy())], True)
                    )
                    if len(expanded) > 64:
                        raise ValueError("SQL template branch limit exceeded")
            variants = expanded
        elif isinstance(statement, nodes.Assign):
            if branch or not isinstance(statement.target, nodes.Name) or statement.target.name in bindings:
                raise ValueError("unresolved SQL template assignment")
            _template_literal(statement.node, bindings)
            bindings[statement.target.name] = [statement.node]
        else:
            raise ValueError("unresolved SQL template statement")
    return variants


def sql_template_sources(source: str) -> list[tuple[str, list[int]]]:
    from jinja2 import nodes

    tree = parsed_template(source)
    for call in tree.find_all(nodes.Call):
        _template_call(call)
    if next(tree.find_all(nodes.Filter), None) is not None:
        raise ValueError("unresolved SQL template filter")
    return [("".join(parts), line_map) for parts, line_map in _template_render(tree.body, {}, [([], [])])]


def groovy_payloads(path: str, source: str) -> list[tuple[int, str, str, str, str]]:
    from pygments.lexers import get_lexer_by_name
    from pygments.token import Comment, Name, Operator, String, Text

    tokens = [
        (offset, token, text)
        for offset, token, text in get_lexer_by_name("groovy").get_tokens_unprocessed(source)
        if token not in Text.Whitespace and token not in Comment
    ]
    result = []
    for index, (_, token, text) in enumerate(tokens):
        if (
            token in String
            and index
            and tokens[index - 1][2] == "."
            and text.strip("\"'") in {"sh", "execute", "bat", "powershell", "pwsh"}
        ):
            raise ValueError("unresolved quoted Groovy process callee")
        if token in Name and text in {"execute", "ProcessBuilder", "bat", "powershell", "pwsh"}:
            raise ValueError(f"unresolved Groovy process carrier: {text}")
        if token not in Name or text != "sh":
            continue
        cursor = index + 1
        parenthesized = cursor < len(tokens) and tokens[cursor][2] == "("
        if parenthesized:
            cursor += 1
        if cursor < len(tokens) and tokens[cursor][2] == "script:":
            cursor += 1
        if cursor >= len(tokens) or tokens[cursor][1] not in String:
            raise ValueError("unresolved Groovy sh source")
        offset, _, value = tokens[cursor]
        delimiter = next((quote for quote in ("'''", '"""', "'", '"') if value.startswith(quote)), None)
        if delimiter is None or not value.endswith(delimiter):
            raise ValueError("unresolved Groovy sh literal")
        body = value[len(delimiter) : -len(delimiter)]
        if "\\" in body or delimiter.startswith('"') and "$" in body:
            raise ValueError("unresolved Groovy sh escapes or interpolation")
        if (
            cursor + 1 < len(tokens)
            and tokens[cursor + 1][1] in Operator
            and tokens[cursor + 1][2] not in {")", "}", ";", "]", ","}
        ):
            raise ValueError("unresolved Groovy sh expression")
        if cursor + 1 < len(tokens) and tokens[cursor + 1][2] == ",":
            raise ValueError("unresolved Groovy sh option arguments")
        if parenthesized and (cursor + 1 >= len(tokens) or tokens[cursor + 1][2] != ")"):
            raise ValueError("unresolved Groovy sh call boundary")
        line = source[: offset + len(delimiter)].count("\n") + 1
        result.append((line, path + ".sh", body, "bash", f"groovy:sh:{index}"))
    return result


def template_data_spans(source: str) -> list[tuple[int, int]]:
    from jinja2 import Environment

    cursor = 0
    result = []
    for _, token, value in Environment().lex(source):
        start = source.find(value, cursor)
        if start < 0:
            raise ValueError("unresolved HTML template source mapping")
        cursor = start + len(value)
        if token == "data":
            result.append((start, cursor))
    return result
