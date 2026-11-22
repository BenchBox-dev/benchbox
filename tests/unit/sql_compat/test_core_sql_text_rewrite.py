from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

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

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.path, self.scope, self.kind)

    def render(self) -> str:
        return "::".join(self.key)


REVIEWED_ALLOWLIST: dict[tuple[str, str, str], str] = {
    ("benchbox/core/tpcds/c_tools.py", "DSQGenBinary._clean_sql", "re.sub"): (
        "strips dsqgen tool markers from generated source SQL before any platform translation; no platform key"
    ),
    ("benchbox/core/tpcds/maintenance_test.py", "TPCDSMaintenanceTest._expected_removals", "re.sub"): (
        "inlines staging relation names into a count guard identically on every platform"
    ),
    ("benchbox/core/tpcds/qualification/runner.py", "prepare", "re.sub"): (
        "re.sub strips non-digits from a query label, not SQL text"
    ),
    ("benchbox/core/tpcds/qualification/runner.py", "worker", "re.sub"): (
        "re.sub strips non-digits from a query label, not SQL text"
    ),
    ("benchbox/core/tpcds_obt/benchmark.py", "TPCDSOBTBenchmark.get_create_tables_sql", "re.sub"): (
        "idempotence edit to the standard source DDL applied before translate_sql_query; not platform-keyed"
    ),
}

UNRESOLVED_VIOLATIONS: dict[tuple[str, str, str], str] = {
    ("benchbox/core/nyctaxi/benchmark.py", "NYCTaxiBenchmark.translate_query_text", "re.sub"): (
        "per-dialect day-of-week and epoch-difference rewrites by regex on query text"
    ),
    ("benchbox/core/read_primitives/queries.py", "ReadPrimitivesQueryManager.get_query", "replace"): (
        "ClickHouse-only function-name case rewrite on query text"
    ),
    ("benchbox/core/tpcds/maintenance_functions.py", "MaintenanceFunction.statements_for", "replace"): (
        "MySQL-family CAST target rewrite on statement text"
    ),
    ("benchbox/core/tpchavoc/dialect_compat.py", "rewrite_dialect_variant", "replace"): (
        "Doris and StarRocks GROUP BY () removal by text replacement"
    ),
    (
        "benchbox/core/transaction_primitives/benchmark.py",
        "TransactionPrimitivesBenchmark._rewrite_transactional_sql_for_platform",
        "re.sub",
    ): "SQLite series and typed-date rewrites by regex on transactional SQL",
    (
        "benchbox/core/transactional/benchmark_base.py",
        "TransactionalBenchmarkBase._rewrite_transactional_sql_for_platform",
        "re.sub",
    ): "per-platform series, date-literal, temp-table and BEGIN rewrites by regex on transactional SQL",
    ("benchbox/core/transactional/benchmark_base.py", "_starrocks_begin_statement", "re.sub"): (
        "StarRocks BEGIN rewrite by regex"
    ),
    ("benchbox/core/transactional/questdb_sql.py", "rewrite_questdb_operation_sql", "re.sub"): (
        "QuestDB CURRENT_TIMESTAMP rewrite by regex"
    ),
    ("benchbox/core/tuning/metadata.py", "TuningMetadataManager._first_row_query", "re.sub"): (
        "T-SQL TOP 1 injection by regex on select text"
    ),
    ("benchbox/core/tuning/metadata.py", "TuningMetadataManager._get_create_table_sql", "replace"): (
        "per-platform column type substitution on metadata DDL text"
    ),
    ("benchbox/core/vector_search/queries.py", "<module>", "replace"): (
        "SingleStore vector-function variants built by replacing function text in query SQL"
    ),
}


def _name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_sql_variable(node: ast.AST) -> bool:
    name = _name(node)
    return name is not None and _is_sql_name(name)


def _is_regex_substitution(func: ast.Attribute) -> bool:
    return func.attr in {"sub", "subn"} and _name(func.value) == "re"


def _is_compiled_substitution(func: ast.Attribute) -> bool:
    receiver = _name(func.value)
    return func.attr in {"sub", "subn"} and receiver is not None and COMPILED_PATTERN.search(receiver) is not None


def _text_rewrite_kind(node: ast.Call) -> str | None:
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr == "replace" and _is_sql_variable(func.value):
        return KIND_REPLACE
    if _is_regex_substitution(func) and len(node.args) >= 3 and _is_sql_variable(node.args[2]):
        return KIND_REGEX
    if _is_compiled_substitution(func) and any(_is_sql_variable(arg) for arg in node.args):
        return KIND_REGEX
    return None


def _is_any_rewrite(node: ast.Call) -> bool:
    func = node.func
    return isinstance(func, ast.Attribute) and (
        func.attr == "replace" or _is_regex_substitution(func) or _is_compiled_substitution(func)
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
    findings: set[Finding] = set()
    for scope_name, nodes in _scopes(ast.parse(source)):
        calls = [node for node in nodes if isinstance(node, ast.Call)]
        rewrite_kinds = {kind for call in calls if (kind := _text_rewrite_kind(call))}
        if rewrite_kinds and _is_dialect_aware(nodes):
            findings.update(Finding(path, scope_name, kind) for kind in rewrite_kinds)
        if any(_is_any_rewrite(call) for call in calls) and any(
            isinstance(node, ast.Dict) and _is_platform_keyed_sql_dict(node) for node in nodes
        ):
            findings.add(Finding(path, scope_name, KIND_DICT))
        for node in nodes:
            if _is_platform_keyed_rewrite_table(node):
                findings.add(Finding(path, scope_name, KIND_TABLE))
    return findings


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
    ],
)
def test_fixture_with_per_platform_text_rewrite_is_flagged(source: str, kind: str) -> None:
    assert kind in {finding.kind for finding in scan_source(source, "fixture.py")}


@pytest.mark.parametrize("source", [CLEAN_PARAMETER_FIXTURE, CLEAN_TRANSLATE_FIXTURE])
def test_clean_fixture_passes(source: str) -> None:
    assert scan_source(source, "fixture.py") == set()


def test_core_has_no_unreviewed_sql_text_rewrite() -> None:
    accepted = set(REVIEWED_ALLOWLIST) | set(UNRESOLVED_VIOLATIONS)
    unexpected = sorted(finding.render() for finding in scan_core() if finding.key not in accepted)
    assert not unexpected, (
        "Query text must reach a platform through translate_sql_query, with translator gaps as named fork "
        "rewrites through apply_rewrite. Text-level SQL rewriting found in benchbox/core:\n  " + "\n  ".join(unexpected)
    )


def test_core_allowlists_have_no_stale_entries() -> None:
    present = {finding.key for finding in scan_core()}
    stale = sorted("::".join(key) for key in (set(REVIEWED_ALLOWLIST) | set(UNRESOLVED_VIOLATIONS)) - present)
    assert not stale, "Remove entries that no longer match a finding:\n  " + "\n  ".join(stale)


def test_allowlists_are_disjoint_and_reasoned() -> None:
    assert not set(REVIEWED_ALLOWLIST) & set(UNRESOLVED_VIOLATIONS)
    assert all(reason.strip() for reason in REVIEWED_ALLOWLIST.values())
    assert all(reason.strip() for reason in UNRESOLVED_VIOLATIONS.values())
