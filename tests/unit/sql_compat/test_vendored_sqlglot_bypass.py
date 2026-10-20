from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = REPO_ROOT / "benchbox"

VENDORED_SQLGLOT = "benchbox._vendor.sqlglot"
ALLOWED_PATHS = ("benchbox/_vendor/", "benchbox/sql_compat/", "benchbox/utils/dialect_utils.py")
WATCHED_ATTRIBUTE_CALLS = frozenset({"transpile", "parse_one", "sql"})
WATCHED_MODULE_ATTRIBUTE_CALLS = frozenset({"parse"})
WATCHED_IMPORTED_NAMES = frozenset({"transpile", "parse_one", "parse"})

MIGRATE = "migrate_via_translate_sql_query"
PARSE_ONLY = "parse_only"


@dataclass(frozen=True)
class AllowlistEntry:
    calls: int
    disposition: str
    reason: str


MIGRATION_PENDING = "Moves behind translate_sql_query in the B2 migration items"
DATAFRAME_PARSE_ONLY = "Parses SQL into an AST to build DataFrame operations; emits no dialect SQL"

BYPASS_ALLOWLIST: dict[str, AllowlistEntry] = {
    "benchbox/core/equivalence/builders/tpcds.py": AllowlistEntry(
        1, PARSE_ONLY, "Splits a script into statements for result equivalence; emits no dialect SQL"
    ),
    "benchbox/core/equivalence/cross_surface.py": AllowlistEntry(
        7, PARSE_ONLY, "Normalizes SQL and DataFrame surfaces to one AST form to compare them"
    ),
    "benchbox/core/flightdata/queries.py": AllowlistEntry(2, MIGRATE, MIGRATION_PENDING),
    "benchbox/core/joinorder/dataframe_queries.py": AllowlistEntry(2, PARSE_ONLY, DATAFRAME_PARSE_ONLY),
    "benchbox/core/joinorder_synthetic/dataframe_queries.py": AllowlistEntry(3, PARSE_ONLY, DATAFRAME_PARSE_ONLY),
    "benchbox/core/read_primitives/variant_contracts.py": AllowlistEntry(1, MIGRATE, MIGRATION_PENDING),
    "benchbox/core/tpcdi/etl/scd_processor.py": AllowlistEntry(1, MIGRATE, MIGRATION_PENDING),
    "benchbox/core/tpcdi/loader.py": AllowlistEntry(1, MIGRATE, MIGRATION_PENDING),
    "benchbox/core/tpcdi/queries.py": AllowlistEntry(1, MIGRATE, MIGRATION_PENDING),
    "benchbox/core/tpcdi/schema.py": AllowlistEntry(2, MIGRATE, MIGRATION_PENDING),
    "benchbox/core/tpcdi/validation.py": AllowlistEntry(1, MIGRATE, MIGRATION_PENDING),
    "benchbox/core/tpcds/c_tools.py": AllowlistEntry(2, MIGRATE, MIGRATION_PENDING),
    "benchbox/core/tpcds_obt/query_conversion.py": AllowlistEntry(2, MIGRATE, MIGRATION_PENDING),
    "benchbox/core/tpchavoc/cloud_compat.py": AllowlistEntry(8, MIGRATE, MIGRATION_PENDING),
    "benchbox/core/tpchavoc/dialect_compat.py": AllowlistEntry(16, MIGRATE, MIGRATION_PENDING),
    "benchbox/core/tpchavoc/spark_query_transformer.py": AllowlistEntry(6, MIGRATE, MIGRATION_PENDING),
    "benchbox/platforms/bigquery.py": AllowlistEntry(3, MIGRATE, MIGRATION_PENDING),
    "benchbox/platforms/databricks/adapter.py": AllowlistEntry(4, MIGRATE, MIGRATION_PENDING),
    "benchbox/platforms/questdb_rewriter.py": AllowlistEntry(2, MIGRATE, MIGRATION_PENDING),
    "benchbox/platforms/snowflake.py": AllowlistEntry(2, MIGRATE, MIGRATION_PENDING),
}


def _is_vendored_module(module: str) -> bool:
    return module == VENDORED_SQLGLOT or module.startswith(VENDORED_SQLGLOT + ".")


def _vendored_bindings(tree: ast.AST) -> tuple[set[str], set[str]]:
    module_names: set[str] = set()
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_vendored_module(alias.name):
                    module_names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "benchbox._vendor":
                module_names.update(alias.asname or alias.name for alias in node.names if alias.name == "sqlglot")
            elif _is_vendored_module(module):
                imported_names.update(
                    alias.asname or alias.name for alias in node.names if alias.name in WATCHED_IMPORTED_NAMES
                )
                module_names.update(
                    alias.asname or alias.name for alias in node.names if alias.name in ("exp", "expressions")
                )
    return module_names, imported_names


def count_bypass_calls(source: str) -> int:
    tree = ast.parse(source)
    module_names, imported_names = _vendored_bindings(tree)
    uses_vendored_sqlglot = bool(module_names or imported_names)
    if not uses_vendored_sqlglot:
        return 0

    calls = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Attribute):
            if function.attr in WATCHED_ATTRIBUTE_CALLS or (
                function.attr in WATCHED_MODULE_ATTRIBUTE_CALLS
                and isinstance(function.value, ast.Name)
                and function.value.id in module_names
            ):
                calls += 1
        elif isinstance(function, ast.Name) and function.id in imported_names:
            calls += 1
    return calls


def scan_package(package_root: Path = PACKAGE_ROOT, repo_root: Path = REPO_ROOT) -> dict[str, int]:
    offenders: dict[str, int] = {}
    for path in sorted(package_root.rglob("*.py")):
        relative = path.relative_to(repo_root).as_posix()
        if relative.startswith(ALLOWED_PATHS):
            continue
        calls = count_bypass_calls(path.read_text(encoding="utf-8"))
        if calls:
            offenders[relative] = calls
    return offenders


def test_no_new_vendored_sqlglot_bypass_callers():
    offenders = scan_package()
    allowed = {path: entry.calls for path, entry in BYPASS_ALLOWLIST.items()}

    new_or_grown = {path: calls for path, calls in offenders.items() if calls > allowed.get(path, 0)}
    assert not new_or_grown, (
        "Direct transpile/parse_one/.sql( calls on benchbox._vendor.sqlglot outside "
        f"benchbox/utils/dialect_utils.py and benchbox/sql_compat/: {new_or_grown}. "
        "Route through translate_sql_query instead of extending the allowlist."
    )


def test_bypass_allowlist_has_no_stale_entries():
    offenders = scan_package()
    stale = {
        path: (entry.calls, offenders.get(path, 0))
        for path, entry in BYPASS_ALLOWLIST.items()
        if offenders.get(path, 0) < entry.calls
    }
    assert not stale, f"Allowlist entries exceed actual calls; shrink them (path: (listed, actual)): {stale}"


def test_allowlist_entries_carry_valid_dispositions_and_reasons():
    for path, entry in BYPASS_ALLOWLIST.items():
        assert (REPO_ROOT / path).is_file(), path
        assert entry.disposition in {MIGRATE, PARSE_ONLY}, path
        assert entry.reason.strip(), path
        assert entry.calls > 0, path


def test_scanner_detects_each_bypass_form():
    assert count_bypass_calls("from benchbox._vendor import sqlglot\nsqlglot.transpile('SELECT 1')\n") == 1
    assert count_bypass_calls("from benchbox._vendor import sqlglot\nsqlglot.parse_one('SELECT 1').sql()\n") == 2
    assert count_bypass_calls("from benchbox._vendor.sqlglot import transpile\ntranspile('SELECT 1')\n") == 1
    assert count_bypass_calls("from benchbox._vendor.sqlglot import parse_one as p\np('SELECT 1')\n") == 1
    assert count_bypass_calls("import benchbox._vendor.sqlglot as sg\nsg.parse('SELECT 1')\n") == 1
    assert count_bypass_calls("from benchbox._vendor.sqlglot import exp\nexp.column('a').sql()\n") == 1


def test_scanner_ignores_modules_without_vendored_sqlglot():
    assert count_bypass_calls("import sqlglot\nsqlglot.transpile('SELECT 1')\n") == 0
    assert count_bypass_calls("connection.sql('SELECT 1')\n") == 0


def test_new_offender_file_fails_scan(tmp_path: Path):
    package_root = tmp_path / "benchbox"
    package_root.mkdir()
    (package_root / "new_offender.py").write_text(
        "from benchbox._vendor import sqlglot\n\n\ndef translate(query):\n    return sqlglot.transpile(query)[0]\n",
        encoding="utf-8",
    )
    assert scan_package(package_root, tmp_path) == {"benchbox/new_offender.py": 1}
