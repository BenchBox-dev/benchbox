from __future__ import annotations

import ast
import copy
import hashlib
import io
import json
import re
import shlex
import tokenize
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath

import yaml
from comment_execution import PythonBindings, python_html_sources
from comment_payloads import (
    astro_template_comments,
    bounded_html_template,
    mdx_jsx_comments,
    nested_sources,
    shell_payloads,
    sql_template_sources,
)
from pygments.lexers import get_lexer_by_name
from pygments.token import Comment, Error


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str
    text: str
    symbol: str = ""
    payload: str = ""

    @property
    def identity(self) -> tuple[str, str, str, str, str]:
        return self.path, self.kind, self.symbol, self.text, self.payload


LANGUAGES = {
    ".py": "python",
    ".pyi": "python",
    ".sql": "sql",
    ".sh": "bash",
    ".zsh": "bash",
    ".bash": "bash",
    ".mk": "make",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".css": "css",
    ".astro": "astro",
    ".html": "html",
    ".htm": "html",
    ".jinja": "html+jinja",
    ".j2": "html+jinja",
    ".mdx": "mdx",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "javascript",
    ".tsx": "javascript",
    ".mts": "javascript",
    ".cts": "javascript",
    ".ipynb": "notebook",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".rs": "rust",
    ".go": "go",
    ".ps1": "powershell",
    ".psm1": "powershell",
    ".ksh": "bash",
    ".fish": "unsupported",
    ".bat": "bat",
    ".java": "java",
    ".properties": "properties",
    ".json": "json",
    ".jsonl": "jsonl",
    ".xml": "xml",
    ".tf": "terraform",
    ".r": "r",
    ".tpl": "unsupported",
    ".jsonc": "json",
    ".json5": "json5",
    ".lua": "unsupported",
    ".scala": "unsupported",
    ".svelte": "unsupported",
    ".vue": "unsupported",
}
OWNED_ROOTS = (
    "benchbox/",
    "tests/",
    "scripts/",
    "tools/",
    "_project/scripts/",
    "docker/",
    "make/",
    ".github/",
    "docs/",
    "examples/",
    "_sources/compilation/",
)
DATA_SUFFIXES = {
    ".lock",
    ".md",
    ".rst",
    ".txt",
    ".csv",
    ".tsv",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".webp",
    ".pdf",
    ".parquet",
    ".arrow",
    ".db",
    ".duckdb",
    ".gz",
    ".zip",
    ".gitkeep",
    ".woff",
    ".woff2",
    ".ttf",
    ".map",
    ".snap",
    ".pem",
}


def language(path: str) -> str | None:
    name = PurePosixPath(path).name
    if (
        name in {".gitignore", ".gitattributes", ".dockerignore", "CODEOWNERS", "MANIFEST.in"}
        or path == ".github/soundness-paths.txt"
        or path == ".github/inert-paths.txt"
    ):
        return "line-config"
    if name.endswith(".py.backup"):
        return "python"
    if name == "CMakeLists.txt" or name.endswith(".cmake"):
        return "cmake"
    if path == "quality/comment-policy-requirements.txt" or name.startswith("requirements") and name.endswith(".txt"):
        return "line-config"
    if name == ".importlinter":
        return "ini"
    if name.startswith(".env"):
        return "bash"
    if name == "skill-sync.conf" or name == "CNAME":
        return "line-config"
    if name.startswith(".") and name in DATA_SUFFIXES:
        return None
    if path == "tools/skill-sync":
        return "bash"
    if name.lower() in {"makefile", "gnumakefile"} or name.startswith("Makefile."):
        return "make"
    if name.startswith("Dockerfile"):
        return "docker"
    if path.endswith((".md", ".rst")) and not path.startswith("_project/"):
        return "examples"
    if not PurePosixPath(path).suffix and path.startswith(OWNED_ROOTS):
        return "unsupported"
    suffix = PurePosixPath(path).suffix.lower()
    if path.startswith("docs/_templates/") and suffix in {".html", ".htm"}:
        return "html+jinja"
    if suffix not in LANGUAGES and suffix not in DATA_SUFFIXES and path.startswith(OWNED_ROOTS):
        return "unsupported"
    return LANGUAGES.get(suffix)


def source_language(path: str, source: str) -> str | None:
    if source.startswith("#!"):
        words = shlex.split(source.splitlines()[0][2:])
        if not words:
            return "unsupported"
        interpreter = words[0].split("/")[-1]
        if interpreter == "env":
            commands = [word for word in words[1:] if not word.startswith("-") and "=" not in word]
            interpreter = commands[0] if commands else ""
        return (
            "python"
            if interpreter.startswith("python")
            else {"sh": "bash", "bash": "bash", "zsh": "bash", "ksh": "bash", "node": "javascript"}.get(
                interpreter, "unsupported"
            )
        )
    return language(path)


def sql_quoted_end(source: str, pos: int) -> int:
    char = source[pos]
    closing = "]" if char == "[" else char
    pos += 1
    while pos < len(source):
        if source[pos] == "\\" and char != "[":
            pos += 2
        elif source[pos] == closing:
            pos += 1
            if pos < len(source) and source[pos] == closing:
                pos += 1
            else:
                return pos
        else:
            pos += 1
    raise ValueError("unterminated SQL quoted value")


def sql_comments(source: str, dialect: str | None = None) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    pos = 0
    while pos < len(source):
        start = pos
        char = source[pos]
        dollar = re.match(r"\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$", source[pos:]) if char == "$" else None
        if dollar:
            marker = dollar.group()
            end = source.find(marker, pos + len(marker))
            if end < 0:
                raise ValueError("unterminated SQL dollar string")
            pos = end + len(marker)
        elif char == "[" and dialect != "tsql" and not re.search(r"\bARRAY\s*$", source[:pos], re.I):
            end = source.find("]", pos + 1)
            if dialect is None and end >= 0 and re.search(r"--|/\*|#", source[pos + 1 : end]):
                raise ValueError("SQL bracket syntax containing comment delimiters requires a declared dialect")
            if dialect in {"duckdb", "bigquery"} or re.search(r"[\w)\]]\s*$", source[:pos]):
                pos += 1
            elif end >= 0:
                pos = end + 1
            else:
                raise ValueError("unterminated SQL bracket")
        elif char == "[" and dialect != "tsql":
            pos += 1
        elif char in "'\"`[":
            pos = sql_quoted_end(source, pos)
        elif source.startswith("--", pos) or (
            char == "#"
            and not source.startswith(("#>", "#-", "##"), pos)
            and not re.search(r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s*$", source[:pos], re.I)
        ):
            end = source.find("\n", pos)
            pos = len(source) if end < 0 else end
            result.append((start, source[start:pos]))
        elif source.startswith("/*", pos):
            pos += 2
            depth = 1
            while pos < len(source) and depth:
                if source.startswith("/*", pos):
                    depth += 1
                    pos += 2
                elif source.startswith("*/", pos):
                    depth -= 1
                    pos += 2
                else:
                    pos += 1
            if depth:
                raise ValueError("unterminated SQL comment")
            result.append((start, source[start:pos]))
        else:
            pos += 1
    return result


def _is_sql_callable(func: ast.AST) -> bool:
    return (
        isinstance(func, ast.Attribute)
        and func.attr in {"execute", "executemany", "sql", "query", "prepare", "read_sql", "read_sql_query"}
    ) or (isinstance(func, ast.Name) and func.id in {"text", "read_sql", "read_sql_query"})


def _is_sql_keyword(name: str | None) -> bool:
    return name is not None and (name in {"sql", "query", "statement"} or name.endswith(("_sql", "_query")))


def _python_sql_context(node: ast.AST, parent: ast.AST | None, grandparent: ast.AST | None) -> bool:
    if isinstance(parent, ast.Call):
        callable_is_sql = _is_sql_callable(parent.func)
        if node in parent.args:
            return callable_is_sql
        return callable_is_sql and any(
            keyword.value is node and _is_sql_keyword(keyword.arg) for keyword in parent.keywords
        )
    if isinstance(parent, ast.keyword) and parent.value is node and isinstance(grandparent, ast.Call):
        return _is_sql_callable(grandparent.func) and _is_sql_keyword(parent.arg)
    if isinstance(parent, (ast.Assign, ast.AnnAssign)):
        targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
        names = [child.id.lower() for target in targets for child in ast.walk(target) if isinstance(child, ast.Name)]
        return any(name in {"sql", "query", "statement"} or name.endswith(("_sql", "_query")) for name in names)
    if isinstance(parent, ast.Dict):
        return any(
            value is node
            and isinstance(key, ast.Constant)
            and isinstance(key.value, str)
            and (key.value in {"sql", "query"} or key.value.endswith("_sql"))
            for key, value in zip(parent.keys, parent.values)
        )
    return False


def python_findings(path: str, source: str) -> list[Finding]:
    tree = ast.parse(source)
    result: list[Finding] = []
    scopes: list[tuple[int, int, str]] = []

    def visit(node: ast.AST, symbol: str, parent: ast.AST | None = None, grandparent: ast.AST | None = None) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbol = f"{symbol}.{node.name}".strip(".")
            scopes.append((node.lineno, node.end_lineno or node.lineno, symbol))
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            result.append(Finding(path, node.lineno, "docstring", node.value.value, symbol))
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.JoinedStr):
            result.append(Finding(path, node.lineno, "inert-string", ast.unparse(node.value), symbol))
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and any(
            (isinstance(child, ast.Attribute) and child.attr == "__doc__" and isinstance(child.ctx, ast.Store))
            or (isinstance(child, ast.Name) and child.id == "__doc__" and isinstance(child.ctx, ast.Store))
            or (
                isinstance(child, ast.Subscript)
                and isinstance(child.ctx, ast.Store)
                and isinstance(child.slice, ast.Constant)
                and child.slice.value == "__doc__"
            )
            for child in ast.walk(node)
        ):
            result.append(Finding(path, node.lineno, "runtime-docstring", ast.unparse(node), symbol))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "__doc__"
        ):
            result.append(Finding(path, node.lineno, "runtime-docstring", ast.unparse(node), symbol))
        sql_text = ""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            sql_text = node.value
        elif isinstance(node, ast.JoinedStr):
            sql_text = "".join(
                str(value.value) if isinstance(value, ast.Constant) else "__expression__" for value in node.values
            )
        if sql_text and _python_sql_context(node, parent, grandparent) and re.search(r"--|/\*|#", sql_text):
            try:
                result.extend(
                    Finding(path, node.lineno + sql_text[:offset].count("\n"), "comment", text, symbol, sql_text)
                    for offset, text in sql_comments(sql_text)
                )
            except ValueError as exc:
                result.append(Finding(path, node.lineno, "payload-error", str(exc), symbol, sql_text))
        for child in ast.iter_child_nodes(node):
            visit(child, symbol, node, parent)

    visit(tree, "")
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            symbol = next((name for start, end, name in reversed(scopes) if start <= token.start[0] <= end), "")
            result.append(Finding(path, token.start[0], "comment", token.string, symbol))
    result.extend(python_executable_findings(path, tree, scopes))
    for line, text, symbol in python_html_sources(source, tree):
        if text is None:
            result.append(
                Finding(
                    path,
                    line,
                    "payload-error",
                    "unresolved HTML output source",
                    symbol,
                    python_scoped_digest(tree, line),
                )
            )
        else:
            result.extend(
                Finding(
                    path,
                    line + finding.line - 1,
                    finding.kind,
                    finding.text,
                    f"{symbol}:{finding.symbol}",
                    finding.payload or text,
                )
                for finding in scan(path + ".html", text, "html")
            )
    return result


def _canonical_node(value: object) -> object:
    if isinstance(value, ast.AST):
        return type(value).__name__, tuple(
            (name, _canonical_node(child))
            for name, child in ast.iter_fields(value)
            if not (name == "type_params" and child == [])
        )
    if isinstance(value, list):
        return tuple(_canonical_node(child) for child in value)
    return value


def _digest_canonical(part: object) -> str:
    return "sha256:" + hashlib.sha256(repr(part).encode()).hexdigest()


def python_consumer_digest(tree: ast.AST) -> str:
    return _digest_canonical(_canonical_node(tree))


_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_DEFINITIONS = (*_FUNCTIONS, ast.ClassDef)
_IMPLICIT_ENTRY_POINTS = frozenset(
    {
        "pytestmark",
        "pytest_plugins",
        "setup_module",
        "teardown_module",
        "setup_function",
        "teardown_function",
        "setUpModule",
        "tearDownModule",
        "__getattr__",
    }
)
_NAMESPACE_NAMES = frozenset(
    {
        "globals",
        "locals",
        "vars",
        "eval",
        "exec",
        "__import__",
        "import_module",
        "builtins",
        "__builtins__",
        "modules",
    }
)
_NAMESPACE_ATTRIBUTES = frozenset(
    {
        "__dict__",
        "__globals__",
        "f_globals",
        "_getframe",
        "currentframe",
        "getmodule",
        "import_module",
        "modules",
        "builtins",
    }
)


@dataclass
class _Facts:
    bound: set[str]
    mentions: set[str]
    loads: set[str]
    escapes: set[str]
    params: set[str]
    declared: set[str]
    heads: set[tuple[str, str]]
    stores: list[tuple[str, tuple[str, ...]]]
    calls: list[tuple[str, tuple[str, ...]]]
    strings: set[str]
    name_calls: set[str]
    value_roots: set[str]
    bases: set[str]
    value_heads: set[tuple[str, str]]


def _chain(node: ast.AST) -> tuple[ast.AST, tuple[str, ...]]:
    attributes: list[str] = []
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        if isinstance(node, ast.Attribute):
            attributes.append(node.attr)
        node = node.value
    return node, tuple(reversed(attributes))


def _registered_names(definition: ast.AST) -> set[str]:
    names: set[str] = set()
    for decorator in getattr(definition, "decorator_list", []):
        if isinstance(decorator, ast.Call):
            names.update(
                keyword.value.value
                for keyword in decorator.keywords
                if keyword.arg == "name"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            )
    return names


def _bound_names(statement: ast.stmt) -> set[str]:
    names: set[str] = set()
    stack: list[ast.AST] = [statement]
    while stack:
        node = stack.pop()
        if isinstance(node, _DEFINITIONS):
            names.add(node.name)
            names.update(_registered_names(node))
            names.update(name for child in ast.walk(node) if isinstance(child, ast.Global) for name in child.names)
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
        stack.extend(ast.iter_child_nodes(node))
    return names


def _annotation_nodes(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, (ast.arg, ast.AnnAssign)) and node.annotation is not None:
        return [node.annotation]
    if isinstance(node, _FUNCTIONS) and node.returns is not None:
        return [node.returns]
    return []


_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _scope_locals(scope: ast.AST) -> set[str]:
    names: set[str] = set()
    declared: set[str] = set()
    stack: list[ast.AST] = []
    if isinstance(scope, (*_FUNCTIONS, ast.Lambda)):
        arguments = scope.args
        names.update(
            argument.arg
            for argument in (
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
                *([arguments.vararg] if arguments.vararg else []),
                *([arguments.kwarg] if arguments.kwarg else []),
            )
        )
        stack.extend(scope.body if isinstance(scope.body, list) else [scope.body])
    elif isinstance(scope, ast.ClassDef):
        stack.extend(scope.body)
    else:
        stack.extend(generator.target for generator in scope.generators)
    while stack:
        node = stack.pop()
        if isinstance(node, (*_DEFINITIONS, ast.Lambda, *_COMPREHENSIONS)):
            if isinstance(node, _DEFINITIONS):
                names.add(node.name)
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            declared.update(node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
        stack.extend(ast.iter_child_nodes(node))
    return names - declared


def _module_name_ids(statement: ast.stmt) -> set[int]:
    resolved: set[int] = set()
    stack: list[tuple[ast.AST, tuple[tuple[set[str], bool], ...]]] = [(statement, ())]
    while stack:
        node, scopes = stack.pop()
        if isinstance(node, ast.Name):
            hidden = any(node.id in names for names, is_class in scopes if not is_class)
            if scopes and scopes[-1][1] and not isinstance(node.ctx, ast.Load):
                hidden = hidden or node.id in scopes[-1][0]
            if not hidden:
                resolved.add(id(node))
            continue
        if isinstance(node, _COMPREHENSIONS):
            inner = (*scopes, (_scope_locals(node), False))
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.comprehension):
                    outer_iterable = child is node.generators[0]
                    stack.append((child.iter, scopes if outer_iterable else inner))
                    stack.extend((part, inner) for part in (child.target, *child.ifs))
                else:
                    stack.append((child, inner))
            continue
        if isinstance(node, (*_DEFINITIONS, ast.Lambda)):
            inner = (*scopes, (_scope_locals(node), isinstance(node, ast.ClassDef)))
            body = node.body if isinstance(node.body, list) else [node.body]
            inner_ids = {id(child) for child in body}
            stack.extend((child, inner if id(child) in inner_ids else scopes) for child in ast.iter_child_nodes(node))
            continue
        stack.extend((child, scopes) for child in ast.iter_child_nodes(node))
    return resolved


def _type_use_ids(node: ast.AST) -> list[int]:
    if isinstance(node, ast.ExceptHandler) and node.type is not None:
        return [id(child) for child in ast.walk(node.type)]
    if isinstance(node, ast.ClassDef):
        return [id(base) for base in node.bases]
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"isinstance", "issubclass"}
        and len(node.args) > 1
    ):
        return [id(child) for child in ast.walk(node.args[1])]
    return []


def _inert_ids(nodes: list[ast.AST]) -> set[int]:
    inert = {id(node.value) for node in nodes if isinstance(node, ast.Attribute)}
    inert.update(id(node.func) for node in nodes if isinstance(node, ast.Call))
    for node in nodes:
        inert.update(_type_use_ids(node))
        for annotation in _annotation_nodes(node):
            inert.update(id(child) for child in ast.walk(annotation))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            root, _ = _chain(node.func)
            if isinstance(root, ast.Name) and root.id == "monkeypatch":
                arguments = [*node.args, *(keyword.value for keyword in node.keywords)]
                inert.update(id(child) for argument in arguments for child in ast.walk(argument))
    return inert


def _record_bases(facts: _Facts, node: ast.ClassDef, module_ids: set[int]) -> None:
    for base in (*node.bases, *(keyword.value for keyword in node.keywords)):
        facts.bases.update(
            child.id for child in ast.walk(base) if isinstance(child, ast.Name) and id(child) in module_ids
        )


def _record_call(facts: _Facts, node: ast.Call, module_ids: set[int]) -> None:
    func = node.func
    if isinstance(func, ast.Name):
        if id(func) in module_ids:
            facts.name_calls.add(func.id)
    elif isinstance(func, ast.Attribute):
        root, attributes = _chain(func)
        if not (isinstance(root, ast.Name) and root.id == "pytest" and attributes[:1] == ("mark",)):
            facts.calls.append((root.id if isinstance(root, ast.Name) and id(root) in module_ids else "", attributes))


def _facts(statement: ast.stmt) -> _Facts:
    nodes = list(ast.walk(statement))
    module_ids = _module_name_ids(statement)
    inert = _inert_ids(nodes)
    facts = _Facts(
        _bound_names(statement), set(), set(), set(), set(), set(), set(), [], [], set(), set(), set(), set(), set()
    )

    def root_name(root: ast.AST) -> str:
        return root.id if isinstance(root, ast.Name) and id(root) in module_ids else ""

    for node in nodes:
        if isinstance(node, ast.Name):
            if id(node) in module_ids:
                facts.mentions.add(node.id)
                if isinstance(node.ctx, ast.Load):
                    facts.loads.add(node.id)
                    if id(node) not in inert:
                        facts.escapes.add(node.id)
        elif isinstance(node, ast.arg):
            facts.mentions.add(node.arg)
            facts.params.add(node.arg)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            facts.mentions.update(node.names)
            facts.loads.update(node.names)
            if isinstance(node, ast.Global):
                facts.declared.update(node.names)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.isidentifier():
            facts.mentions.add(node.value)
            facts.strings.add(node.value)
        elif isinstance(node, (ast.Attribute, ast.Subscript)):
            root, attributes = _chain(node)
            if isinstance(node, ast.Attribute) and root_name(root):
                facts.heads.add((root_name(root), attributes[0]))
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                facts.stores.append((root_name(root), attributes))
            elif id(node) not in inert and root_name(root):
                facts.value_roots.add(root_name(root))
                if attributes:
                    facts.value_heads.add((root_name(root), attributes[0]))
        elif isinstance(node, ast.Call):
            _record_call(facts, node, module_ids)
        elif isinstance(node, ast.ClassDef):
            _record_bases(facts, node, module_ids)
    return facts


def _is_fixture_reference(node: ast.AST) -> bool:
    return (isinstance(node, ast.Name) and node.id == "fixture") or (
        isinstance(node, ast.Attribute) and node.attr == "fixture"
    )


def _has_irregular_fixture(tree: ast.AST) -> bool:
    if any(
        isinstance(node, ast.ImportFrom) and any(alias.name == "fixture" and alias.asname for alias in node.names)
        for node in ast.walk(tree)
    ):
        return True
    regular: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, _DEFINITIONS):
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if not _is_fixture_reference(target):
                    continue
                regular.add(id(target))
                if isinstance(decorator, ast.Call) and any(
                    keyword.arg is None
                    or (
                        keyword.arg == "name"
                        and not (isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str))
                    )
                    for keyword in decorator.keywords
                ):
                    return True
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "getfixturevalue"
            and not (node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str))
        ):
            return True
    return any(_is_fixture_reference(node) and id(node) not in regular for node in ast.walk(tree))


def _fixture_names(body: list[ast.stmt]) -> tuple[set[str], set[str]]:
    wide: set[str] = set()
    every: set[str] = set()
    for statement in body:
        if not isinstance(statement, _FUNCTIONS):
            continue
        for decorator in statement.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if not _is_fixture_reference(target):
                continue
            names = {statement.name, *_registered_names(statement)}
            every |= names
            if isinstance(decorator, ast.Call) and any(
                keyword.arg == "scope"
                and not (isinstance(keyword.value, ast.Constant) and keyword.value.value == "function")
                for keyword in decorator.keywords
            ):
                wide |= names
    return wide, every


def _is_dynamic(tree: ast.AST) -> bool:
    return any(
        (isinstance(node, ast.Name) and node.id in _NAMESPACE_NAMES)
        or (isinstance(node, ast.Attribute) and node.attr in _NAMESPACE_ATTRIBUTES)
        or (isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in _NAMESPACE_ATTRIBUTES)
        for node in ast.walk(tree)
    )


def _has_star_import(tree: ast.AST) -> bool:
    return any(isinstance(node, ast.alias) and node.name == "*" for node in ast.walk(tree))


def _is_implicit_entry_point(statement: ast.stmt, names: set[str]) -> bool:
    if names & _IMPLICIT_ENTRY_POINTS:
        return True
    if not isinstance(statement, _FUNCTIONS):
        return False
    return statement.name.startswith("pytest_") or any(
        isinstance(decorator, ast.Call)
        and any(
            keyword.arg == "autouse" and not (isinstance(keyword.value, ast.Constant) and keyword.value.value is False)
            for keyword in decorator.keywords
        )
        for decorator in statement.decorator_list
    )


def _top_level_unit(tree: ast.Module, line: int) -> int | None:
    for index, statement in enumerate(tree.body):
        if not isinstance(statement, _DEFINITIONS):
            continue
        start = min([statement.lineno, *(decorator.lineno for decorator in statement.decorator_list)])
        if start <= line <= (statement.end_lineno or statement.lineno):
            return index
    return None


_PATH_CONSTRUCTORS = frozenset({"Path", "PurePath", "PurePosixPath", "PureWindowsPath"})
_BUILTIN_CONSTRUCTORS = frozenset({"str", "int", "float", "bool", "bytes", "tuple", "frozenset"})
_BUILTIN_PURE = frozenset(
    {
        *_BUILTIN_CONSTRUCTORS,
        "sorted",
        "list",
        "dict",
        "set",
        "range",
        "len",
        "min",
        "max",
        "sum",
        "any",
        "all",
        "enumerate",
        "zip",
        "reversed",
        "isinstance",
        "property",
        "staticmethod",
        "classmethod",
    }
)
_IMMUTABLE_METHODS = frozenset(
    {
        "joinpath",
        "with_name",
        "with_suffix",
        "with_stem",
        "relative_to",
        "as_posix",
        "format",
        "strip",
        "lower",
        "upper",
    }
)
_FILE_ANCHORED_METHODS = frozenset({"resolve", "absolute"})
_IMMUTABLE_ATTRIBUTES = frozenset({"parent", "parents", "name", "stem", "suffix", "suffixes", "parts", "anchor"})
_IMMUTABLE_MODULE_VALUES = frozenset(
    {("sys", "executable"), ("sys", "platform"), ("os", "name"), ("os", "sep"), ("os", "linesep")}
)
_IMMUTABLE_MODULE_FUNCTIONS = frozenset(
    {
        ("os", "path", "join"),
        ("os", "path", "dirname"),
        ("os", "path", "basename"),
        ("os", "path", "normpath"),
        ("logging", "getLogger"),
    }
)
_PURE_IMPORTS = frozenset(
    {
        ("dataclasses", "dataclass"),
        ("dataclasses", "field"),
        ("functools", "partial"),
        ("functools", "wraps"),
        ("functools", "lru_cache"),
        ("functools", "cache"),
        ("functools", "cached_property"),
        ("functools", "total_ordering"),
        ("collections", "namedtuple"),
        ("typing", "TypeVar"),
        ("typing", "NewType"),
        ("typing", "overload"),
        ("typing_extensions", "TypeVar"),
        ("abc", "abstractmethod"),
        ("contextlib", "contextmanager"),
        ("enum", "unique"),
        ("unittest", "patch"),
        ("mock", "patch"),
    }
)
_PURE_MODULE_CALLS = frozenset(
    {
        ("dataclasses", "dataclass"),
        ("dataclasses", "field"),
        ("functools", "partial"),
        ("functools", "wraps"),
        ("functools", "lru_cache"),
        ("functools", "cache"),
        ("functools", "cached_property"),
        ("functools", "total_ordering"),
        ("collections", "namedtuple"),
        ("typing", "TypeVar"),
        ("typing", "NewType"),
        ("typing", "overload"),
        ("abc", "abstractmethod"),
        ("contextlib", "contextmanager"),
        ("enum", "unique"),
    }
)


@dataclass
class _Env:
    constants: set[str]
    shadowed: set[str]
    imports: set[str]
    links: dict[str, tuple[str, str]]
    real: dict[str, str]


def _origin(name: str, env: _Env) -> str | None:
    if name in env.shadowed:
        return None
    if name in env.links:
        return env.links[name][0]
    if name in env.real:
        return env.real[name]
    return None if name in env.imports else ""


def _is_immutable(node: ast.AST | None, env: _Env) -> bool:
    def immutable(child: ast.AST | None) -> bool:
        return _is_immutable(child, env)

    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return node.id in env.constants or (node.id in {"__file__", "__name__"} and node.id not in env.shadowed)
    if isinstance(node, ast.JoinedStr):
        return all(immutable(value) for value in node.values)
    if isinstance(node, ast.FormattedValue):
        return immutable(node.value) and node.format_spec is None
    if isinstance(node, ast.Tuple):
        return all(immutable(element) for element in node.elts)
    if isinstance(node, ast.BinOp):
        return immutable(node.left) and immutable(node.right)
    if isinstance(node, ast.Slice):
        return all(part is None or immutable(part) for part in (node.lower, node.upper, node.step))
    if isinstance(node, ast.Subscript):
        return immutable(node.value) and immutable(node.slice)
    if isinstance(node, ast.Attribute):
        return node.attr in _IMMUTABLE_ATTRIBUTES and immutable(node.value) or _is_module_value(node, env)
    if isinstance(node, ast.Call):
        return _is_immutable_call(node, immutable, env)
    return False


def _is_module_value(node: ast.Attribute, env: _Env) -> bool:
    root, attributes = _chain(node)
    if not isinstance(root, ast.Name) or root.id in env.shadowed:
        return False
    return node.attr == "__file__" or (env.real.get(root.id), *attributes) in _IMMUTABLE_MODULE_VALUES


def _is_pathlib(name: str, env: _Env) -> bool:
    return name in _PATH_CONSTRUCTORS and env.links.get(name) == ("pathlib", name) and name not in env.shadowed


def _is_immutable_call(node: ast.Call, immutable: Callable[[ast.AST | None], bool], env: _Env) -> bool:
    if not all(immutable(argument) for argument in [*node.args, *(keyword.value for keyword in node.keywords)]):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        if func.id in _BUILTIN_CONSTRUCTORS:
            return _origin(func.id, env) == ""
        return _is_pathlib(func.id, env)
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr in _IMMUTABLE_METHODS and (
        immutable(func.value) or (isinstance(func.value, ast.Name) and _is_pathlib(func.value.id, env))
    ):
        return True
    if func.attr in _FILE_ANCHORED_METHODS and immutable(func.value):
        return any(isinstance(child, ast.Name) and child.id == "__file__" for child in ast.walk(func.value))
    root, attributes = _chain(func)
    return (
        isinstance(root, ast.Name)
        and root.id not in env.shadowed
        and (env.real.get(root.id), *attributes) in _IMMUTABLE_MODULE_FUNCTIONS
    )


def _assigns_immutable(statement: ast.stmt, name: str, env: _Env) -> bool:
    if isinstance(statement, ast.Assign):
        targets, value = statement.targets, statement.value
    elif isinstance(statement, ast.AnnAssign):
        targets, value = [statement.target], statement.value
    else:
        return False
    return (
        len(targets) == 1 and isinstance(targets[0], ast.Name) and targets[0].id == name and _is_immutable(value, env)
    )


def _is_pure_call(func: ast.AST, env: _Env) -> bool:
    root, attributes = _chain(func)
    if not isinstance(root, ast.Name):
        return False
    origin = _origin(root.id, env)
    if origin is None:
        return False
    if not attributes:
        if origin == "":
            return root.id in _BUILTIN_PURE
        return origin == "pytest" or env.links.get(root.id) in _PURE_IMPORTS
    if origin == "pytest":
        return True
    if origin in {"unittest", "mock"} and ("patch" in attributes or root.id == "patch"):
        return True
    chain = (env.real.get(root.id), *attributes)
    return chain in _IMMUTABLE_MODULE_FUNCTIONS or chain in _PURE_MODULE_CALLS


def _import_time_effect(node: ast.AST, env: _Env) -> bool:
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, (ast.Assign, ast.AnnAssign)) and current.value is not None:
            targets = current.targets if isinstance(current, ast.Assign) else [current.target]
            if all(isinstance(target, ast.Name) for target in targets) and _is_immutable(current.value, env):
                continue
        if isinstance(current, ast.Call):
            if not _is_pure_call(current.func, env):
                return True
        elif isinstance(current, ast.Delete) or (
            isinstance(current, (ast.Attribute, ast.Subscript)) and isinstance(current.ctx, (ast.Store, ast.Del))
        ):
            return True
        for name, value in ast.iter_fields(current):
            if name == "body" and isinstance(current, (*_FUNCTIONS, ast.Lambda)):
                continue
            stack.extend(
                child for child in (value if isinstance(value, list) else [value]) if isinstance(child, ast.AST)
            )
    return False


def _is_pure_decorator(decorator: ast.expr, env: _Env) -> bool:
    return _is_pure_call(decorator.func if isinstance(decorator, ast.Call) else decorator, env)


def _unknown_decorators(statement: ast.stmt, env: _Env) -> bool:
    return any(
        not _is_pure_decorator(decorator, env)
        for node in ast.walk(statement)
        if isinstance(node, _DEFINITIONS)
        for decorator in node.decorator_list
    )


def _calls_own_code(definition: ast.ClassDef) -> bool:
    own = {statement.name for statement in definition.body if isinstance(statement, _DEFINITIONS)}
    for statement in definition.body:
        if isinstance(statement, ast.ClassDef):
            if _calls_own_code(statement):
                return True
        elif not isinstance(statement, _FUNCTIONS) and any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in own
            for node in ast.walk(statement)
        ):
            return True
    return False


def _runs_effects(statement: ast.stmt, fact: _Facts, env: _Env) -> bool:
    if isinstance(statement, (ast.Import, ast.ImportFrom)):
        return False
    if isinstance(statement, (ast.Assign, ast.AnnAssign)) and fact.bound and fact.bound <= env.constants:
        return False
    if isinstance(statement, _DEFINITIONS) and _unknown_decorators(statement, env):
        return True
    return _import_time_effect(statement, env)


def _is_dataclass_reference(node: ast.expr, env: _Env, name: str, module: str) -> bool:
    root, attributes = _chain(node.func if isinstance(node, ast.Call) else node)
    if not isinstance(root, ast.Name) or root.id in env.shadowed:
        return False
    if not attributes:
        return env.links.get(root.id) == (module, name)
    return env.real.get(root.id) == module and attributes == (name,)


def _class_mutable(definition: ast.ClassDef, env: _Env) -> bool:
    is_dataclass = any(
        _is_dataclass_reference(decorator, env, "dataclass", "dataclasses") for decorator in definition.decorator_list
    )
    for statement in definition.body:
        if isinstance(statement, ast.ClassDef):
            if _class_mutable(statement, env):
                return True
        elif (
            isinstance(statement, (ast.Assign, ast.AnnAssign))
            and statement.value is not None
            and not _is_immutable(statement.value, env)
            and not (is_dataclass and _is_dataclass_reference(statement.value, env, "field", "dataclasses"))
        ):
            return True
    return False


_PROCESS_CALLS = frozenset({("os", "chdir"), ("os", "putenv"), ("os", "unsetenv"), ("os", "umask")})
_PROCESS_OBJECTS = frozenset({("os", "environ"), ("sys", "path"), ("sys", "argv"), ("sys", "modules")})
_MUTATING_METHODS = frozenset(
    {
        "update",
        "setdefault",
        "pop",
        "popitem",
        "clear",
        "insert",
        "append",
        "extend",
        "remove",
        "sort",
        "reverse",
        "add",
        "discard",
    }
)


def _changes_process_state(root: str, attributes: tuple[str, ...], real: dict[str, str]) -> bool:
    module = real.get(root, root)
    if len(attributes) == 1:
        return (module, attributes[0]) in _PROCESS_CALLS
    return len(attributes) >= 2 and (module, attributes[0]) in _PROCESS_OBJECTS and attributes[-1] in _MUTATING_METHODS


def _mutates_state(fact: _Facts, data: set[str], code: set[str], imports: set[str], real: dict[str, str]) -> bool:
    if fact.declared or fact.escapes & data or fact.value_roots & data or fact.name_calls & data:
        return True
    mutable = data | code
    if any(root in mutable or root in imports for root, _ in fact.stores):
        return True
    for root, attributes in fact.calls:
        if root in mutable:
            return True
        if root in imports and (
            _changes_process_state(root, attributes, real)
            or (len(attributes) >= 2 and attributes[-1] in _MUTATING_METHODS)
        ):
            return True
    return False


@dataclass
class _Kinds:
    imports: set[str]
    code: set[str]
    data: set[str]
    named: set[str]
    stateful: set[str]
    always: set[int]
    full: set[int]
    links: dict[str, tuple[str, str]]
    real: dict[str, str]


def _import_links(body: list[ast.stmt]) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    links: dict[str, tuple[str, str]] = {}
    real: dict[str, str] = {}
    for statement in body:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                real[alias.asname or alias.name.split(".")[0]] = alias.name.split(".")[0]
        elif isinstance(statement, ast.ImportFrom) and statement.module and not statement.level:
            for alias in statement.names:
                links[alias.asname or alias.name] = (statement.module.split(".")[0], alias.name)
    return links, real


def _classify(body: list[ast.stmt], facts: list[_Facts]) -> _Kinds:
    binders: dict[str, list[int]] = {}
    for index, fact in enumerate(facts):
        for name in fact.bound:
            binders.setdefault(name, []).append(index)
    declared = set().union(*(fact.declared for fact in facts))
    imports = {
        name
        for name, indexes in binders.items()
        if all(isinstance(body[index], (ast.Import, ast.ImportFrom)) for index in indexes)
    }
    code = {
        name
        for name, indexes in binders.items()
        if name not in declared and all(isinstance(body[index], _DEFINITIONS) for index in indexes)
    }
    links, real = _import_links(body)
    env = _Env(set(), set(binders) - imports, imports, links, real)
    changed = True
    while changed:
        changed = False
        for name, indexes in binders.items():
            if name in imports or name in code or name in env.constants or name in declared:
                continue
            if all(_assigns_immutable(body[index], name, env) for index in indexes):
                env.constants.add(name)
                changed = True
    named = set(binders) - imports
    data = named - code - env.constants
    stateful = {
        name
        for name in code
        if any(
            _mutates_state(facts[index], data, code, imports, real)
            or (isinstance(body[index], ast.ClassDef) and _class_mutable(body[index], env))
            for index in binders[name]
        )
    }
    changed = True
    while changed:
        changed = False
        for name in code - stateful:
            if any(facts[index].loads & stateful for index in binders[name]):
                stateful.add(name)
                changed = True
    always = {index for index, statement in enumerate(body) if _runs_effects(statement, facts[index], env)}
    full = {
        index
        for index in always
        if isinstance(body[index], _DEFINITIONS)
        and (
            _unknown_decorators(body[index], env)
            or (isinstance(body[index], ast.ClassDef) and _calls_own_code(body[index]))
        )
    }
    return _Kinds(imports, code, data, named, stateful, always, full, links, real)


@dataclass
class _Reach:
    data: set[str]
    code: set[str]
    imports: set[str]
    named: set[str]
    stateful: set[str]
    shared: set[str]
    heads: set[tuple[str, str]]
    every_import: set[str]
    real: dict[str, str]
    links: dict[str, tuple[str, str]]


def _reaches(root: str, attributes: tuple[str, ...], depth: int, reach: _Reach) -> bool:
    if set(attributes) & reach.named or root in reach.code or root in reach.data:
        return True
    if root in reach.imports and depth == 1:
        return True
    return root in reach.imports and len(attributes) >= depth and (root, attributes[0]) in reach.heads


def _reads_covered_head(
    head: tuple[str, str], heads: set[tuple[str, str]], imports: set[str], real: dict[str, str]
) -> bool:
    root, attribute = head
    return root in imports and head in heads and (real.get(root), attribute) not in _IMMUTABLE_MODULE_VALUES


def _joins(facts: _Facts, touched: set[str], reach: _Reach) -> bool:
    if (
        facts.bound & touched
        or facts.loads & reach.stateful
        or facts.escapes & (reach.data | reach.code | reach.imports)
        or facts.value_roots & (reach.data | reach.code)
        or facts.name_calls & reach.data
        or facts.bases & (reach.data | reach.code)
        or facts.strings & reach.data
        or facts.params & reach.shared
    ):
        return True
    if (
        any(root in reach.every_import for root, _ in facts.stores)
        or any(_changes_process_state(root, attributes, reach.real) for root, attributes in facts.calls)
        or any(reach.links.get(name) in _PROCESS_CALLS for name in facts.name_calls)
    ):
        return True
    return (
        any(_reaches(root, attributes, 1, reach) for root, attributes in facts.stores)
        or any(_reaches(root, attributes, 2, reach) for root, attributes in facts.calls)
        or any(_reads_covered_head(head, reach.heads, reach.imports, reach.real) for head in facts.value_heads)
    )


def _enclosing_defs(statement: ast.stmt, line: int) -> list[ast.AST]:
    return [
        node
        for node in ast.walk(statement)
        if isinstance(node, _DEFINITIONS) and node.lineno <= line <= (node.end_lineno or node.lineno)
    ]


def _takes_caller_input(definitions: list[ast.AST]) -> bool:
    for node in definitions:
        if isinstance(node, ast.ClassDef):
            return True
        arguments = node.args
        names = [
            argument.arg
            for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
            if argument.arg not in {"self", "cls"}
        ]
        if names or arguments.vararg or arguments.kwarg:
            return True
        if arguments.args and arguments.args[0].arg in {"self", "cls"}:
            return True
    return False


def _references(statement: ast.stmt, names: set[str]) -> bool:
    return any(
        (isinstance(node, ast.Name) and node.id in names)
        or (isinstance(node, ast.Attribute) and node.attr in names)
        or (isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in names)
        for node in ast.walk(statement)
    )


def _dynamic_dispatch(statement: ast.stmt) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"getattr", "setattr", "delattr"}
        and not (len(node.args) > 1 and isinstance(node.args[1], ast.Constant))
        for node in ast.walk(statement)
    )


def _callers(body: list[ast.stmt], unit: int, line: int) -> set[int] | None:
    definitions = _enclosing_defs(body[unit], line)
    if not _takes_caller_input(definitions):
        return set()
    followed: set[str] = set()
    pending = {node.name for node in definitions}
    found: set[int] = set()
    while pending:
        followed |= pending
        names, pending = pending, set()
        for index, statement in enumerate(body):
            if index == unit or index in found or not _references(statement, names):
                continue
            if _dynamic_dispatch(statement):
                return None
            found.add(index)
            pending |= {node.name for node in ast.walk(statement) if isinstance(node, _DEFINITIONS)} - followed
    return found


def _header_copy(statement: ast.stmt) -> ast.stmt:
    clone = copy.deepcopy(statement)
    for node in ast.walk(clone):
        if isinstance(node, _FUNCTIONS):
            node.body = [ast.Pass()]
    return clone


def _scoped_parts(tree: ast.AST, line: int) -> tuple[tuple[str, object], ...] | None:
    if not isinstance(tree, ast.Module) or _has_star_import(tree) or _is_dynamic(tree) or _has_irregular_fixture(tree):
        return None
    unit = _top_level_unit(tree, line)
    if unit is None:
        return None
    body = tree.body
    facts = [_facts(statement) for statement in body]
    kinds = _classify(body, facts)
    if "monkeypatch" in kinds.named:
        return None
    wide, every = _fixture_names(body)
    shared = wide | (every & kinds.stateful)
    entry_points = {
        index
        for index, statement in enumerate(body)
        if index != unit and _is_implicit_entry_point(statement, facts[index].bound)
    }
    headers = {
        index: _header_copy(body[index])
        for index in kinds.always
        if index != unit
        and index not in entry_points
        and index not in kinds.full
        and isinstance(body[index], _DEFINITIONS)
    }
    header_facts = {index: _facts(statement) for index, statement in headers.items()}
    included = {unit, *entry_points, *(kinds.always - set(headers))}
    callers = _callers(body, unit, line)
    if callers is None:
        return None
    included.update(callers)
    touched: set[str] = set()
    heads: set[tuple[str, str]] = set()
    escaped: set[str] = set()
    for index in range(len(body)):
        if index in included:
            touched |= facts[index].mentions | facts[index].bound
            heads |= facts[index].heads
            escaped |= facts[index].escapes
        elif index in header_facts:
            touched |= header_facts[index].mentions
            heads |= header_facts[index].heads
            escaped |= header_facts[index].escapes
    changed = True
    while changed:
        changed = False
        real_heads = {(kinds.real.get(root, root), attribute) for root, attribute in heads}
        valued = (escaped & kinds.imports) | {
            local for local, pair in kinds.links.items() if pair in real_heads and local in kinds.imports
        }
        dynamic = {
            name
            for index in included
            for name in facts[index].bound & kinds.code
            if any(_reads_covered_head(head, heads, kinds.imports, kinds.real) for head in facts[index].value_heads)
        }
        reach = _Reach(
            (touched & kinds.data) | valued,
            touched & kinds.code,
            touched & kinds.imports,
            (touched & kinds.named) | valued,
            (touched & kinds.stateful) | dynamic,
            touched & shared,
            heads,
            kinds.imports,
            kinds.real,
            kinds.links,
        )
        for index, fact in enumerate(facts):
            if index in included or not _joins(fact, touched, reach):
                continue
            included.add(index)
            headers.pop(index, None)
            touched |= fact.mentions | fact.bound
            heads |= fact.heads
            escaped |= fact.escapes
            changed = True
    return tuple(
        (
            "consumer" if index == unit else "covered" if index in included else "header",
            _canonical_node(body[index] if index in included else headers[index]),
        )
        for index in sorted(included | set(headers))
    )


def python_scoped_digest(tree: ast.AST, line: int) -> str:
    parts = _scoped_parts(tree, line)
    return python_consumer_digest(tree) if parts is None else _digest_canonical(("scoped", parts))


def python_executable_findings(path: str, tree: ast.AST, scopes: list[tuple[int, int, str]]) -> list[Finding]:
    result = []
    bindings = PythonBindings(tree)
    for node in ast.walk(tree):
        payload = bindings.payload(node) if isinstance(node, ast.Call) else None
        if payload is None:
            continue
        expression, lang, text = payload
        if (text is None or lang == "unsupported") and bindings.reviewed_argv(path, node):
            continue
        symbol = next((name for start, end, name in reversed(scopes) if start <= node.lineno <= end), "")
        if text is None or lang == "unsupported":
            result.append(
                Finding(
                    path,
                    node.lineno,
                    "payload-error",
                    "unresolved executable source: " + ast.unparse(expression),
                    symbol,
                    python_scoped_digest(tree, node.lineno),
                )
            )
        elif lang == "javascript":
            result.append(
                Finding(
                    path,
                    node.lineno,
                    "coverage-error",
                    "Python-to-JavaScript process payload requires an adapter",
                    symbol,
                    text,
                )
            )
        else:
            result.extend(
                Finding(
                    path,
                    node.lineno + finding.line - 1,
                    finding.kind,
                    finding.text,
                    f"{symbol}:payload:{finding.symbol}",
                    text,
                )
                for finding in scan(path + "." + lang, text, lang)
            )
    return result


def sql_findings(path: str, source: str, lang: str) -> list[Finding]:
    if lang == "sql":
        return [
            Finding(path, source[:offset].count("\n") + 1, "comment", text) for offset, text in sql_comments(source)
        ]
    result = [
        Finding(path, line_map[offset], "comment", text, "template-sql", rendered)
        for rendered, line_map in sql_template_sources(source)
        for offset, text in sql_comments(rendered)
    ]
    result.extend(
        Finding(path, source[:offset].count("\n") + 1, "comment", text.rstrip("\r\n"), "template-comment", source)
        for offset, token, text in get_lexer_by_name("jinja").get_tokens_unprocessed(source)
        if token in Comment and token not in Comment.Preproc
    )
    return result


def template_coverage(path: str, source: str, lang: str) -> list[Finding]:
    if lang != "html+jinja":
        return []
    try:
        if "{#" in source:
            raise ValueError("unresolved HTML template comment expansion")
        bounded_html_template(source)
    except ValueError as exc:
        return [Finding(path, 1, "coverage-error", str(exc))]
    return []


def mask_embedded_sources(path: str, source: str, lang: str) -> str:
    if lang == "bash":
        lines = source.splitlines(keepends=True)
        for start, _, text, _, _ in shell_payloads(path, source, include_data=True):
            for index in range(start - 1, start - 1 + len(text.splitlines())):
                lines[index] = re.sub(r"[^\n]", " ", lines[index])
        return "".join(lines) + "\n"
    if lang in {"html", "html+jinja"}:
        return re.sub(
            r"(<(?:script|style)\b[^>]*>)(.*?)(</(?:script|style)\s*>)",
            lambda m: m.group(1) + re.sub(r"[^\n]", " ", m.group(2)) + m.group(3),
            source,
            flags=re.I | re.S,
        )
    if lang == "css":
        return re.sub(r"@apply\b[^;{}/]*;", lambda m: re.sub(r"[^\n]", " ", m.group()), source)
    return source


def validate_json_lines(source: str) -> None:
    for index, line in enumerate(source.splitlines(), 1):
        if line.strip():
            try:
                json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON line {index}") from exc


def astro_findings(path: str, source: str, lang: str) -> list[Finding]:
    if lang != "astro":
        return []
    return [Finding(path, line, "comment", text) for line, text in astro_template_comments(source)]


def mdx_findings(path: str, source: str, lang: str) -> list[Finding]:
    if lang != "mdx":
        return []
    return [Finding(path, line, "comment", text) for line, text in mdx_jsx_comments(source)]


def scan(path: str, source: str, lang: str) -> list[Finding]:
    try:
        if source.startswith("#!"):
            lang = source_language(path, source) or lang
        if lang == "python":
            return python_findings(path, source)
        if lang in {"sql", "sql+jinja"}:
            return sql_findings(path, source, lang)
        nested = [
            Finding(path, start + f.line - 1, f.kind, f.text, f"{symbol}:{f.symbol}", f.payload or text)
            for start, child_path, text, child_lang, symbol in nested_sources(path, source, lang)
            for f in scan(child_path, text, child_lang)
        ]
        nested.extend(template_coverage(path, source, lang))
        if lang in {"notebook", "examples", "astro", "mdx"}:
            return nested + astro_findings(path, source, lang) + mdx_findings(path, source, lang)
        source = mask_embedded_sources(path, source, lang)
        if lang == "unsupported":
            raise ValueError("source language has no registered adapter")
        if lang == "jsonl":
            validate_json_lines(source)
            return nested
        if lang == "line-config":
            return [
                Finding(path, index, "comment", text)
                for index, text in enumerate(source.splitlines(), 1)
                if text.startswith("#")
            ]
        result = nested
        for offset, token, text in get_lexer_by_name(lang).get_tokens_unprocessed(source):
            if token in Error:
                raise ValueError(f"unrecognized {lang} syntax at line {source[:offset].count(chr(10)) + 1}")
            if token in Comment:
                if token in Comment.Preproc or token in Comment.PreprocFile:
                    continue
                if lang == "bash" and offset and source[offset - 1] not in " \t\r\n;|&()":
                    continue
                result.append(Finding(path, source[:offset].count("\n") + 1, "comment", text.rstrip("\r\n")))
        return result
    except (SyntaxError, tokenize.TokenError, IndentationError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        return [Finding(path, 1, "coverage-error", str(exc))]
