from __future__ import annotations

import ast
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]
CORE_ROOT = REPO_ROOT / "benchbox" / "core"

PLATFORM_NAMES = frozenset(
    {
        "athena",
        "bigquery",
        "clickhouse",
        "cockroachdb",
        "databricks",
        "datafusion",
        "doris",
        "duckdb",
        "firebolt",
        "mariadb",
        "monetdb",
        "mssql",
        "mysql",
        "oracle",
        "postgres",
        "postgresql",
        "presto",
        "questdb",
        "redshift",
        "singlestore",
        "snowflake",
        "spark",
        "sqlite",
        "sqlserver",
        "starrocks",
        "synapse",
        "teradata",
        "timescaledb",
        "trino",
        "tsql",
        "vertica",
    }
)
SQL_NAME_TOKENS = frozenset(
    {"sql", "query", "queries", "statement", "statements", "stmt", "stmts", "ddl", "dml", "script", "scripts", "q"}
)
NON_SQL_NAME_TOKENS = frozenset(
    {
        "id",
        "ids",
        "num",
        "number",
        "name",
        "names",
        "type",
        "types",
        "platform",
        "dialect",
        "path",
        "file",
        "key",
        "index",
        "idx",
        "len",
        "length",
        "label",
    }
)


def _is_sql_name(name: str) -> bool:
    tokens = {token.lower() for token in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", name)}
    return bool(tokens & SQL_NAME_TOKENS) and not tokens & NON_SQL_NAME_TOKENS


DIALECT_IDENTIFIER = re.compile(r"platform|dialect|engine|backend|target", re.IGNORECASE)
SQL_KEYWORD = re.compile(r"\b(select|create|insert|alter|cast|limit|interval|date_trunc)\b|array_", re.IGNORECASE)

KIND_REPLACE = "replace"
KIND_REGEX = "re.sub"
KIND_DICT = "platform-keyed-dict"
KIND_TABLE = "platform-keyed-rewrite-table"

COMPILED_PATTERN = re.compile(r"pattern|regex|(^|_)re$", re.IGNORECASE)
REWRITE_TABLE_NAME = re.compile(r"replac|substitut|rewrite|clause|operator|fragment|dummy", re.IGNORECASE)

FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class Finding:
    path: str
    scope: str
    kind: str
    count: int = 1

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.path, self.scope, self.kind)

    def render(self) -> str:
        return f"{'::'.join(self.key)} x{self.count}"


class Accepted(NamedTuple):
    count: int
    reason: str


Key = tuple[str, str, str]

REVIEWED_ALLOWLIST: dict[Key, Accepted] = {
    ("benchbox/core/tpcds/c_tools.py", "DSQGenBinary._clean_sql", "re.sub"): Accepted(
        27, "strips dsqgen tool markers from generated source SQL before any platform translation; no platform key"
    ),
    ("benchbox/core/tpcds/maintenance_test.py", "TPCDSMaintenanceTest._expected_removals", "re.sub"): Accepted(
        1, "inlines staging relation names into a count guard identically on every platform"
    ),
    ("benchbox/core/tpcds/qualification/runner.py", "prepare", "re.sub"): Accepted(
        1, "re.sub strips non-digits from a query label, not SQL text"
    ),
    ("benchbox/core/tpcds/qualification/runner.py", "worker", "re.sub"): Accepted(
        1, "re.sub strips non-digits from a query label, not SQL text"
    ),
    ("benchbox/core/tpcds_obt/benchmark.py", "TPCDSOBTBenchmark.get_create_tables_sql", "re.sub"): Accepted(
        1, "idempotence edit to the standard source DDL applied before translate_sql_query; not platform-keyed"
    ),
}

UNRESOLVED_VIOLATIONS: dict[Key, Accepted] = {}


def _name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _name(node.value)
    return None


def _is_sql_variable(node: ast.AST) -> bool:
    name = _name(node)
    return name is not None and _is_sql_name(name)


@dataclass(frozen=True)
class ReAliases:
    modules: frozenset[str]
    functions: frozenset[str]


def _re_aliases(tree: ast.Module) -> ReAliases:
    modules = {"re"}
    functions: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.asname or alias.name for alias in node.names if alias.name == "re")
        elif isinstance(node, ast.ImportFrom) and node.module == "re" and node.level == 0:
            functions.update(alias.asname or alias.name for alias in node.names if alias.name in {"sub", "subn"})
    return ReAliases(frozenset(modules), frozenset(functions))


def _is_regex_substitution(node: ast.Call, aliases: ReAliases) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in aliases.functions
    return (
        isinstance(func, ast.Attribute)
        and func.attr in {"sub", "subn"}
        and isinstance(func.value, ast.Name)
        and func.value.id in aliases.modules
    )


def _is_compiled_substitution(node: ast.Call) -> bool:
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr not in {"sub", "subn"}:
        return False
    receiver = _name(func.value)
    return receiver is not None and COMPILED_PATTERN.search(receiver) is not None


def _string_keyword_is_sql(node: ast.Call) -> bool:
    return any(keyword.arg == "string" and _is_sql_variable(keyword.value) for keyword in node.keywords)


def _text_rewrite_kind(node: ast.Call, aliases: ReAliases) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "replace" and _is_sql_variable(func.value):
        return KIND_REPLACE
    if _is_regex_substitution(node, aliases):
        if (len(node.args) >= 3 and _is_sql_variable(node.args[2])) or _string_keyword_is_sql(node):
            return KIND_REGEX
    elif _is_compiled_substitution(node):
        if any(_is_sql_variable(arg) for arg in node.args) or _string_keyword_is_sql(node):
            return KIND_REGEX
    return None


def _is_any_rewrite(node: ast.Call, aliases: ReAliases) -> bool:
    func = node.func
    return (isinstance(func, ast.Attribute) and func.attr == "replace") or (
        _is_regex_substitution(node, aliases) or _is_compiled_substitution(node)
    )


def _is_platform_keyed_rewrite_table(node: ast.AST) -> bool:
    if isinstance(node, ast.Assign):
        targets, value = node.targets, node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        targets, value = [node.target], node.value
    else:
        return False
    if not isinstance(value, ast.Dict) or not value.keys:
        return False
    if not any((name := _name(target)) is not None and REWRITE_TABLE_NAME.search(name) for target in targets):
        return False
    return all(
        isinstance(key, ast.Constant)
        and isinstance(key.value, str)
        and key.value.lower() in PLATFORM_NAMES
        and isinstance(item, ast.Constant)
        and isinstance(item.value, str)
        for key, item in zip(value.keys, value.values, strict=True)
    )


def _is_platform_keyed_sql_dict(node: ast.Dict) -> bool:
    keyed = [
        key
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str) and key.value.lower() in PLATFORM_NAMES
    ]
    if len(keyed) < 2:
        return False
    return any(
        isinstance(value, ast.Constant) and isinstance(value.value, str) and SQL_KEYWORD.search(value.value)
        for value in node.values
    )


def _is_dialect_aware(nodes: list[ast.AST]) -> bool:
    for node in nodes:
        if isinstance(node, ast.arg) and DIALECT_IDENTIFIER.search(node.arg):
            return True
        if isinstance(node, (ast.Name, ast.Attribute)):
            name = _name(node)
            if name is not None and DIALECT_IDENTIFIER.search(name):
                return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.lower() in PLATFORM_NAMES:
            return True
    return False


def _own_nodes(scope: ast.AST) -> list[ast.AST]:
    nodes: list[ast.AST] = []
    pending = list(ast.iter_child_nodes(scope))
    while pending:
        node = pending.pop()
        nodes.append(node)
        if isinstance(node, FUNCTION_NODES + (ast.ClassDef,)):
            continue
        pending.extend(ast.iter_child_nodes(node))
    return nodes


def _scopes(tree: ast.Module) -> list[tuple[str, list[ast.AST]]]:
    scopes: list[tuple[str, list[ast.AST]]] = []

    def visit(owner: ast.AST, prefix: str) -> None:
        local: list[ast.AST] = []
        for node in _own_nodes(owner) if isinstance(owner, FUNCTION_NODES) else _body_nodes(owner):
            local.append(node)
        label = prefix[:-1] if prefix else "<module>"
        if isinstance(owner, ast.ClassDef):
            label = f"{label}.<body>"
        scopes.append((label, local))
        for node in _direct_definitions(owner):
            visit(node, f"{prefix}{node.name}.")

    visit(tree, "")
    return scopes


def _body_nodes(owner: ast.AST) -> list[ast.AST]:
    nodes: list[ast.AST] = []
    pending = [
        child for child in ast.iter_child_nodes(owner) if not isinstance(child, FUNCTION_NODES + (ast.ClassDef,))
    ]
    while pending:
        node = pending.pop()
        nodes.append(node)
        if isinstance(node, FUNCTION_NODES + (ast.ClassDef,)):
            continue
        pending.extend(ast.iter_child_nodes(node))
    return nodes


def _direct_definitions(owner: ast.AST) -> list[ast.AST]:
    found: list[ast.AST] = []
    pending = list(ast.iter_child_nodes(owner))
    while pending:
        node = pending.pop()
        if isinstance(node, FUNCTION_NODES + (ast.ClassDef,)):
            found.append(node)
            continue
        pending.extend(ast.iter_child_nodes(node))
    return found


def scan_source(source: str, path: str) -> set[Finding]:
    counts: Counter[tuple[str, str]] = Counter()
    tree = ast.parse(source)
    aliases = _re_aliases(tree)
    for scope_name, nodes in _scopes(tree):
        calls = [node for node in nodes if isinstance(node, ast.Call)]
        if _is_dialect_aware(nodes):
            for call in calls:
                if kind := _text_rewrite_kind(call, aliases):
                    counts[(scope_name, kind)] += 1
        if any(_is_any_rewrite(call, aliases) for call in calls):
            keyed_dicts = sum(isinstance(node, ast.Dict) and _is_platform_keyed_sql_dict(node) for node in nodes)
            if keyed_dicts:
                counts[(scope_name, KIND_DICT)] += keyed_dicts
        tables = sum(_is_platform_keyed_rewrite_table(node) for node in nodes)
        if tables:
            counts[(scope_name, KIND_TABLE)] += tables
    return {Finding(path, scope, kind, count) for (scope, kind), count in counts.items()}


def scan_core() -> set[Finding]:
    findings: set[Finding] = set()
    for file in sorted(CORE_ROOT.rglob("*.py")):
        relative = file.relative_to(REPO_ROOT).as_posix()
        findings |= scan_source(file.read_text(encoding="utf-8"), relative)
    return findings


PER_PLATFORM_REPLACE_FIXTURE = """
def adapt(query, platform):
    if platform == "clickhouse":
        query = query.replace("LAG(x, 1)", "lag(x, 1)")
    return query
"""

PER_PLATFORM_REGEX_FIXTURE = """
import re

def adapt(sql, dialect):
    return re.sub(r"\\bLIMIT\\b", "TOP", sql)
"""

PLATFORM_DICT_FIXTURE = """
def build(sql):
    types = {"snowflake": "CAST(x AS TIMESTAMP_NTZ)", "mysql": "CAST(x AS DATETIME)"}
    return sql.replace("{ts}", types["mysql"])
"""

PLATFORM_TABLE_FIXTURE = """
import re

_DUMMY_TABLE_CLAUSES = {"oracle": "from dual", "sqlserver": ""}
_DUMMY_PATTERN = re.compile("{dummy}")

def load(sql, platform):
    return _DUMMY_PATTERN.subn(lambda _: _DUMMY_TABLE_CLAUSES.get(platform, ""), sql)
"""

QUERY_TEXT_FIXTURE = """
def adapt(query_text, platform):
    if platform == "clickhouse":
        query_text = query_text.replace("LAG(x, 1)", "lag(x, 1)")
    return query_text
"""

SQL_TEXT_FIXTURE = """
class Adapter:
    def adapt(self, dialect):
        return self.sql_text.replace("TIMESTAMP", "DATETIME2")
"""

KEYWORD_MODULE_FIXTURE = """
import re

def adapt(sql, dialect):
    return re.sub(pattern=r"\\bLIMIT\\b", repl="TOP", string=sql)
"""

KEYWORD_COMPILED_FIXTURE = """
import re

_LIMIT_PATTERN = re.compile("LIMIT")

def adapt(sql, dialect):
    return _LIMIT_PATTERN.sub(repl="TOP", string=sql)
"""

MODULE_ALIAS_FIXTURE = """
import re as rx

def adapt(sql, dialect):
    return rx.sub(r"\\bLIMIT\\b", "TOP", sql)
"""

FROM_IMPORT_FIXTURE = """
from re import sub as substitute

def adapt(sql, dialect):
    return substitute(r"\\bLIMIT\\b", "TOP", sql)
"""

SUBSCRIPT_FIXTURE = """
def adapt(queries, platform):
    return queries[platform].replace("LAG(x, 1)", "lag(x, 1)")
"""

CLEAN_PARAMETER_FIXTURE = """
def render(sql, params):
    for key, value in params.items():
        sql = sql.replace("{" + key + "}", str(value))
    return sql
"""

CLEAN_TRANSLATE_FIXTURE = """
from benchbox.utils.dialect_utils import translate_sql_query

def adapt(query, platform):
    return translate_sql_query(query, platform)
"""


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        (PER_PLATFORM_REPLACE_FIXTURE, KIND_REPLACE),
        (PER_PLATFORM_REGEX_FIXTURE, KIND_REGEX),
        (PLATFORM_DICT_FIXTURE, KIND_DICT),
        (PLATFORM_TABLE_FIXTURE, KIND_TABLE),
        (PLATFORM_TABLE_FIXTURE, KIND_REGEX),
        (QUERY_TEXT_FIXTURE, KIND_REPLACE),
        (SQL_TEXT_FIXTURE, KIND_REPLACE),
        (KEYWORD_MODULE_FIXTURE, KIND_REGEX),
        (KEYWORD_COMPILED_FIXTURE, KIND_REGEX),
        (MODULE_ALIAS_FIXTURE, KIND_REGEX),
        (FROM_IMPORT_FIXTURE, KIND_REGEX),
        (SUBSCRIPT_FIXTURE, KIND_REPLACE),
    ],
)
def test_fixture_with_per_platform_text_rewrite_is_flagged(source: str, kind: str) -> None:
    assert kind in {finding.kind for finding in scan_source(source, "fixture.py")}


@pytest.mark.parametrize("source", [CLEAN_PARAMETER_FIXTURE, CLEAN_TRANSLATE_FIXTURE])
def test_clean_fixture_passes(source: str) -> None:
    assert scan_source(source, "fixture.py") == set()


ACCEPTED: dict[Key, Accepted] = {**REVIEWED_ALLOWLIST, **UNRESOLVED_VIOLATIONS}


def _excess(findings: set[Finding], accepted: dict[Key, Accepted]) -> list[str]:
    return sorted(
        f"{finding.render()} (accepted {accepted[finding.key].count if finding.key in accepted else 0})"
        for finding in findings
        if finding.count > (accepted[finding.key].count if finding.key in accepted else 0)
    )


def _stale(findings: set[Finding], accepted: dict[Key, Accepted]) -> list[str]:
    found = {finding.key: finding.count for finding in findings}
    return sorted(
        f"{'::'.join(key)} accepted {entry.count}, found {found.get(key, 0)}"
        for key, entry in accepted.items()
        if found.get(key, 0) < entry.count
    )


TWO_REWRITES_FIXTURE = """
def adapt(query_text, platform):
    if platform == "clickhouse":
        query_text = query_text.replace("LAG(x, 1)", "lag(x, 1)")
    if platform == "mysql":
        query_text = query_text.replace("AS INTEGER", "AS SIGNED")
    return query_text
"""

TWO_REWRITES_KEY: Key = ("fixture.py", "adapt", KIND_REPLACE)


def test_fixture_scope_counts_every_occurrence() -> None:
    (finding,) = scan_source(TWO_REWRITES_FIXTURE, "fixture.py")
    assert finding.key == TWO_REWRITES_KEY
    assert finding.count == 2


def test_extra_occurrence_in_accepted_scope_fails() -> None:
    findings = scan_source(TWO_REWRITES_FIXTURE, "fixture.py")
    assert _excess(findings, {TWO_REWRITES_KEY: Accepted(1, "reviewed")})
    assert not _excess(findings, {TWO_REWRITES_KEY: Accepted(2, "reviewed")})


def test_lower_occurrence_count_is_stale() -> None:
    findings = scan_source(TWO_REWRITES_FIXTURE, "fixture.py")
    assert _stale(findings, {TWO_REWRITES_KEY: Accepted(3, "reviewed")})
    assert not _stale(findings, {TWO_REWRITES_KEY: Accepted(2, "reviewed")})


def test_core_has_no_unreviewed_sql_text_rewrite() -> None:
    unexpected = _excess(scan_core(), ACCEPTED)
    assert not unexpected, (
        "Query text must reach a platform through translate_sql_query, with translator gaps as named fork "
        "rewrites through apply_rewrite. New or additional text-level SQL rewriting found in benchbox/core:\n  "
        + "\n  ".join(unexpected)
    )


def test_core_allowlists_have_no_stale_entries() -> None:
    stale = _stale(scan_core(), ACCEPTED)
    assert not stale, "Lower or remove entries that no longer match the recorded count:\n  " + "\n  ".join(stale)


def test_allowlists_are_disjoint_and_reasoned() -> None:
    assert not set(REVIEWED_ALLOWLIST) & set(UNRESOLVED_VIOLATIONS)
    assert all(entry.reason.strip() and entry.count > 0 for entry in ACCEPTED.values())
