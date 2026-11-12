from __future__ import annotations

import pytest

from benchbox.utils.duckdb_extensions import (
    DATASKETCHES_COMMUNITY_STATEMENTS,
    DATASKETCHES_QUERY_FARM_STATEMENTS,
    datasketches_install_statements,
    duckdb_major_version,
    install_datasketches,
    rewrite_datasketches_install_sql,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

CATALOG_SQL = "INSTALL datasketches FROM community;\nLOAD datasketches;\nDROP TABLE IF EXISTS t;\n"


@pytest.mark.parametrize(
    ("version", "major"),
    [("1.5.5", 1), ("2.0.0", 2), ("v2.1.0", 2), ("3.0.0.dev1", 3), (None, None), ("garbage", None)],
)
def test_major_version_parsing(version, major):
    if version is None:
        import duckdb

        assert duckdb_major_version() == int(duckdb.__version__.split(".")[0])
    else:
        assert duckdb_major_version(version) == major


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("1.0.0", DATASKETCHES_COMMUNITY_STATEMENTS),
        ("1.5.5", DATASKETCHES_COMMUNITY_STATEMENTS),
        ("2.0.0", DATASKETCHES_QUERY_FARM_STATEMENTS),
        ("3.0.0", DATASKETCHES_QUERY_FARM_STATEMENTS),
    ],
)
def test_statement_selection(version, expected):
    assert datasketches_install_statements(version) == expected


def test_query_farm_sequence_enables_repository_idempotently():
    statements = DATASKETCHES_QUERY_FARM_STATEMENTS
    assert statements[0] == "SET allow_extension_repositories = 'allowed'"
    assert "CREATE EXTENSION REPOSITORY IF NOT EXISTS query_farm" in statements[1]
    assert statements[2] == "INSTALL datasketches FROM query_farm"
    assert statements[3] == "LOAD datasketches FROM query_farm"


def test_install_executes_selected_statements_in_order():
    executed: list[str] = []

    class Recorder:
        def execute(self, statement):
            executed.append(statement)

    install_datasketches(Recorder(), "1.5.5")
    assert executed == list(DATASKETCHES_COMMUNITY_STATEMENTS)
    executed.clear()
    install_datasketches(Recorder(), "2.0.0")
    assert executed == list(DATASKETCHES_QUERY_FARM_STATEMENTS)


def test_sql_rewrite_only_changes_install_prelude_on_2x():
    assert rewrite_datasketches_install_sql(CATALOG_SQL, "1.5.5") == CATALOG_SQL
    rewritten = rewrite_datasketches_install_sql(CATALOG_SQL, "2.0.0")
    assert "FROM community" not in rewritten
    assert "INSTALL datasketches FROM query_farm;" in rewritten
    assert rewritten.endswith("DROP TABLE IF EXISTS t;\n")
    assert rewrite_datasketches_install_sql("SELECT 1", "2.0.0") == "SELECT 1"
