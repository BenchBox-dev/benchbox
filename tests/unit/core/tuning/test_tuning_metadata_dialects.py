from __future__ import annotations

import sys
import types
from unittest.mock import patch

import pytest

from benchbox._vendor import sqlglot
from benchbox._vendor.sqlglot import exp
from benchbox.core.tuning.interface import UnifiedTuningConfiguration
from benchbox.core.tuning.metadata import TuningMetadataManager

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _NoResultSet(Exception):
    pass


class _Cursor:
    def __init__(self, statements: list[tuple[str, object]]):
        self.statements = statements
        self._last = ""

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        self._last = sql.lstrip().upper()

    def fetchall(self):
        if not self._last.startswith(("SELECT", "WITH")):
            raise _NoResultSet("No results. Previous SQL was not a query.")
        return []

    def fetchone(self):
        if not self._last.startswith(("SELECT", "WITH")):
            raise _NoResultSet("No results. Previous SQL was not a query.")
        return (0,)


class _Connection:
    def __init__(self):
        self.statements: list[tuple[str, object]] = []

    def cursor(self):
        return _Cursor(self.statements)

    def commit(self):
        pass


class _Adapter:
    def __init__(self, platform_name: str, *, with_execute_query: bool = False):
        self.platform_name = platform_name
        self.platform_config: dict = {}
        if with_execute_query:
            self.execute_query = self._execute_query

    def _execute_query(self, connection, sql, query_id, *args, **kwargs):
        cursor = connection.cursor()
        cursor.execute(sql)
        return {"status": "SUCCESS", "result": cursor.fetchall()}


def _statements(connection: _Connection) -> list[str]:
    return [sql for sql, _ in connection.statements]


def _run_marker_flow(platform_name: str, connection: _Connection, *, with_execute_query: bool = False) -> None:
    manager = TuningMetadataManager(
        _Adapter(platform_name, with_execute_query=with_execute_query), connection=connection
    )
    assert manager.save_unified_tunings(UnifiedTuningConfiguration()) is True
    assert manager.write_tuned_run_marker() is True
    manager.has_tuned_run_marker()
    assert manager.clear_tunings() is True


DIALECTS = [
    ("duckdb", "duckdb"),
    ("sqlite", "sqlite"),
    ("postgresql", "postgres"),
    ("snowflake", "snowflake"),
    ("redshift", "redshift"),
    ("clickhouse", "clickhouse"),
    ("synapse", "tsql"),
    ("fabric_dw", "tsql"),
]


@pytest.mark.parametrize(("platform", "dialect"), DIALECTS)
def test_every_statement_the_store_issues_parses_in_its_dialect(platform, dialect):
    connection = _Connection()
    _run_marker_flow(platform, connection, with_execute_query=dialect == "tsql")

    issued = [sql for sql in _statements(connection) if not sql.lstrip().upper().startswith("INSERT")]
    assert issued
    for sql in issued:
        parsed = sqlglot.parse_one(sql, dialect=dialect, error_level=sqlglot.ErrorLevel.RAISE)
        assert not isinstance(parsed, exp.Command), f"{platform}: {sql}"


@pytest.mark.parametrize("platform", ["synapse", "fabric_dw"])
def test_tsql_store_uses_tsql_forms_and_avoids_the_driver_result_fetch(platform):
    connection = _Connection()
    _run_marker_flow(platform, connection, with_execute_query=True)

    statements = _statements(connection)
    create = next(sql for sql in statements if "CREATE TABLE" in sql.upper())
    assert create.lstrip().upper().startswith("IF OBJECT_ID(")
    assert "CREATE TABLE IF NOT EXISTS" not in create.upper()
    parsed_create = sqlglot.parse_one(create, dialect="tsql")
    column_types = {node.this for node in parsed_create.find_all(exp.DataType)}
    assert exp.DataType.Type.DATETIME2 in column_types
    assert exp.DataType.Type.ROWVERSION not in column_types

    assert not any("LIMIT" in sql.upper() for sql in statements)
    assert not any("WHERE TRUE" in sql.upper() for sql in statements)
    marker_probe = next(sql for sql in statements if "run_kind" in sql and sql.lstrip().upper().startswith("SELECT"))
    assert marker_probe.upper().startswith("SELECT TOP 1 1 FROM")
    delete = next(sql for sql in statements if sql.lstrip().upper().startswith("DELETE"))
    assert "WHERE" not in delete.upper()


def test_synapse_keeps_its_table_index_and_fabric_warehouse_gets_none():
    assert TuningMetadataManager(_Adapter("synapse"))._get_create_index_sql() is not None
    assert TuningMetadataManager(_Adapter("fabric_dw"))._get_create_index_sql() is None


def test_tsql_missing_table_error_is_recognised_as_a_missing_table():
    manager = TuningMetadataManager(_Adapter("synapse"))
    error = RuntimeError("[42S02] Invalid object name 'benchbox_tuning_metadata'. (208)")
    assert manager._is_missing_metadata_table_error(error) is True


def test_marker_insert_uses_the_pyformat_mark_for_a_pyformat_driver(monkeypatch):
    monkeypatch.setitem(sys.modules, "fake_marker_driver", types.SimpleNamespace(paramstyle="pyformat"))
    connection_type = type("_PyformatConn", (_Connection,), {"__module__": "fake_marker_driver.connection"})
    connection = connection_type()

    manager = TuningMetadataManager(_Adapter("snowflake"), connection=connection)
    assert manager.write_tuned_run_marker() is True

    insert, params = next(item for item in connection.statements if item[0].lstrip().upper().startswith("INSERT"))
    assert "?" not in insert
    assert insert.count("%s") == 7
    assert "run_kind" in params


def test_bigquery_store_is_created_without_replacing_an_existing_table():
    from benchbox.platforms.bigquery import BigQueryAdapter

    with (
        patch("benchbox.platforms.bigquery.check_platform_dependencies", return_value=(True, [])),
        patch("benchbox.platforms.bigquery.bigquery"),
    ):
        adapter = BigQueryAdapter(project_id="proj", dataset_id="ds")

    for _ in range(2):
        create_sql = TuningMetadataManager(_Adapter("bigquery"))._get_create_table_sql()
        sent = adapter._translate_create_table(create_sql)
        assert "CREATE TABLE IF NOT EXISTS" in sent
        assert "OR REPLACE" not in sent.upper()
        parsed = sqlglot.parse_one(sent, dialect="bigquery", error_level=sqlglot.ErrorLevel.RAISE)
        assert not isinstance(parsed, exp.Command)


def test_athena_reports_an_unsupported_store_for_every_spelling():
    for spelling in ("athena", "Athena"):
        reason = TuningMetadataManager(_Adapter(spelling)).unsupported_reason()
        assert reason is not None
        assert "Athena" in reason
    assert TuningMetadataManager(_Adapter("synapse")).unsupported_reason() is None
