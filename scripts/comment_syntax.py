from __future__ import annotations

import ast
import hashlib
import io
import json
import re
import shlex
import tokenize
from dataclasses import dataclass
from pathlib import PurePosixPath

import yaml
from comment_execution import PythonBindings, python_html_sources
from comment_payloads import (
    astro_template_comments,
    bounded_html_template,
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
    "results-explorer/",
    "website/",
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
    if path.endswith((".md", ".mdx", ".rst")) and not path.startswith(("_project/", "_blog/")):
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


def python_findings(path: str, source: str, js_results: dict[str, list[dict]] | None = None) -> list[Finding]:
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
                    path, line, "payload-error", "unresolved HTML output source", symbol, python_consumer_digest(tree)
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
                for finding in scan(path + ".html", text, "html", js_results)
            )
    return result


def python_consumer_digest(tree: ast.AST) -> str:
    def canonical(value: object) -> object:
        if isinstance(value, ast.AST):
            return type(value).__name__, tuple(
                (name, canonical(child))
                for name, child in ast.iter_fields(value)
                if not (name == "type_params" and child == [])
            )
        if isinstance(value, list):
            return tuple(canonical(child) for child in value)
        return value

    return "sha256:" + hashlib.sha256(repr(canonical(tree)).encode()).hexdigest()


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
                    python_consumer_digest(tree),
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


def javascript_key(path: str, source: str) -> str:
    suffix = ".tsx" if path.endswith((".tsx", ".jsx")) else ".ts"
    return hashlib.sha256(source.encode()).hexdigest() + suffix


def javascript_requests(path: str, source: str, lang: str) -> dict[str, str]:
    if source.startswith("#!"):
        lang = source_language(path, source) or lang
    if lang == "javascript":
        return {javascript_key(path, source): source}
    result = {}
    try:
        if lang == "python":
            for _, text, _ in python_html_sources(source):
                if text is not None:
                    result.update(javascript_requests(path + ".html", text, "html"))
        for _, child_path, text, child_lang, _ in nested_sources(path, source, lang):
            result.update(javascript_requests(child_path, text, child_lang))
    except (SyntaxError, ValueError, KeyError, TypeError, yaml.YAMLError):
        return {}
    return result


REVIEWED_JAVASCRIPT_FLOWS: dict[tuple[str, str], str] = {
    (
        "results-explorer/src/db.ts",
        "unresolved executable sql payload: scan.sql",
    ): "SNAPSHOT_READY_SCANS entries are object literals whose sql values are scanned as SQL",
    (
        "results-explorer/src/lib/duckdbQueries.ts",
        "unresolved executable sql payload: query.sql",
    ): "SnapshotRowsQuery objects come from query builders whose sql property values are scanned as SQL",
    (
        "results-explorer/src/pages/Query.tsx",
        "unresolved executable sql payload: pageQueries.rows.sql",
    ): "page query objects come from query builders whose sql property values are scanned as SQL",
    (
        "results-explorer/src/pages/Query.tsx",
        "unresolved executable sql payload: pageQueries.count.sql",
    ): "page query objects come from query builders whose sql property values are scanned as SQL",
    (
        "results-explorer/src/pages/Query.tsx",
        "unresolved executable sql payload: query.sql",
    ): "starter and builder query objects have sql property values that are scanned as SQL",
    (
        "results-explorer/src/pages/Query.tsx",
        "unresolved executable sql payload: selectQuery.sql",
    ): "select query objects come from query builders whose sql property values are scanned as SQL",
    (
        "results-explorer/src/pages/Query.tsx",
        "unresolved executable sql payload: sqlText",
    ): "sqlText is SQL the user types into the query editor; it is runtime input, not source",
    (
        "results-explorer/scripts/generate-browser-fixtures.mjs",
        "unresolved process arguments require an executable-payload adapter: spawnSync(args[0], args.slice(1))",
    ): "the pipeline command comes from the explorer build contract data file and runs the snapshot pipeline CLI",
}


def javascript_findings(path: str, source: str, js_results: dict[str, list[dict]] | None) -> list[Finding]:
    key = javascript_key(path, source)
    if js_results is None or key not in js_results:
        raise ValueError("TypeScript parser result missing")
    result = []
    for row in js_results[key]:
        if row["kind"] == "payload":
            result.extend(
                Finding(
                    path,
                    row["line"] + f.line - 1,
                    f.kind,
                    f.text,
                    f"{row.get('symbol', '')}:payload:{f.symbol}",
                    f.payload or row["text"],
                )
                for f in scan(path + "." + row["language"], row["text"], row["language"], js_results)
            )
        elif (path, row["text"]) not in REVIEWED_JAVASCRIPT_FLOWS:
            result.append(
                Finding(path, row["line"], row["kind"], row["text"], row.get("symbol", ""), row.get("payload", ""))
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


def scan(path: str, source: str, lang: str, js_results: dict[str, list[dict]] | None = None) -> list[Finding]:
    try:
        if source.startswith("#!"):
            lang = source_language(path, source) or lang
        if lang == "python":
            return python_findings(path, source, js_results)
        if lang in {"sql", "sql+jinja"}:
            return sql_findings(path, source, lang)
        if lang == "javascript":
            return javascript_findings(path, source, js_results)
        nested = [
            Finding(path, start + f.line - 1, f.kind, f.text, f"{symbol}:{f.symbol}", f.payload or text)
            for start, child_path, text, child_lang, symbol in nested_sources(path, source, lang)
            for f in scan(child_path, text, child_lang, js_results)
        ]
        nested.extend(template_coverage(path, source, lang))
        if lang in {"notebook", "examples", "astro"}:
            return nested + astro_findings(path, source, lang)
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
