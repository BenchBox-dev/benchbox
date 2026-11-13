from __future__ import annotations

import warnings
from typing import Any

import pytest

from benchbox.core.results.fetch_mode import FETCH_MODE_ARROW, FETCH_MODE_ROWS
from benchbox.utils.duckdb_extensions import duckdb_relation_arrow_supported, fetch_duckdb_arrow_table

pytestmark = [pytest.mark.unit, pytest.mark.fast]

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")

requires_duckdb_2 = pytest.mark.skipif(
    not duckdb_relation_arrow_supported(duckdb.__version__), reason="relation Arrow output is the DuckDB 2.x API"
)


def _adapter(mode: str) -> Any:
    from benchbox.platforms.duckdb import DuckDBAdapter

    return DuckDBAdapter(database_path=":memory:", fetch_mode=mode)


@pytest.mark.parametrize(
    ("version", "expected"),
    [("1.4.0", False), ("1.5.5", False), ("2.0.0", True), ("v2.1.0", True), (None, duckdb_relation_arrow_supported())],
)
def test_relation_arrow_is_gated_on_the_running_major_version(version: str | None, expected: bool) -> None:
    assert duckdb_relation_arrow_supported(version) is expected


def test_older_runtimes_fetch_arrow_through_the_dbapi_result() -> None:
    class Result:
        def fetch_arrow_table(self) -> str:
            return "table"

    class Connection:
        def execute(self, sql: str) -> Result:
            return Result()

        def sql(self, sql: str) -> None:
            raise AssertionError("relation API is not used before DuckDB 2")

    assert fetch_duckdb_arrow_table(Connection(), "SELECT 1", "1.5.5") == "table"


@requires_duckdb_2
def test_arrow_fetch_does_not_raise_the_dbapi_copy_warning() -> None:
    connection = duckdb.connect(":memory:")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert fetch_duckdb_arrow_table(connection, "SELECT range AS i FROM range(3)").num_rows == 3


@requires_duckdb_2
def test_arrow_fetch_finishes_an_unfinished_result_on_the_connection() -> None:
    connection = duckdb.connect(":memory:")
    connection.execute("SELECT range AS i FROM range(5000)").fetchone()
    assert fetch_duckdb_arrow_table(connection, "SELECT 1 AS a").num_rows == 1


@requires_duckdb_2
def test_adapter_arrow_mode_reads_through_a_relation_without_warnings() -> None:
    connection = duckdb.connect(":memory:")
    connection.execute("SELECT range AS i FROM range(5000)").fetchone()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = _adapter(FETCH_MODE_ARROW).execute_query(
            connection, "SELECT range AS i FROM range(7)", "q", validate_row_count=False
        )
    assert result["status"] == "SUCCESS"
    assert result["rows_returned"] == 7
    assert result["fetch_mode"] == FETCH_MODE_ARROW


@requires_duckdb_2
@pytest.mark.parametrize(
    ("query", "rows"),
    [
        ("WITH t AS (SELECT 1 AS a) SELECT a FROM t", 1),
        ("  ( (SELECT 1 AS a))", 1),
        ("-- start query 1\n-- in stream 0\nSELECT 1 AS a", 1),
        ("/* header */ SELECT 1 AS a", 1),
        ("FROM range(3)", 3),
        ("VALUES (1), (2)", 2),
        ("SHOW TABLES", 1),
        ("PRAGMA table_info('probe')", 1),
        ("SUMMARIZE probe", 1),
        ("PIVOT (SELECT 1 AS a, 2 AS b) ON a USING sum(b)", 1),
        ("INSERT INTO probe VALUES (7), (8) RETURNING i", 2),
        ("CREATE TABLE created AS SELECT 1 AS i", 1),
    ],
)
def test_adapter_arrow_mode_reads_every_statement_through_a_relation(query: str, rows: int) -> None:
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE TABLE probe (i INTEGER)")
    connection.execute("INSERT INTO probe VALUES (1)")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = _adapter(FETCH_MODE_ARROW).execute_query(connection, query, "q", validate_row_count=False)
    assert result["status"] == "SUCCESS"
    assert result["fetch_mode"] == FETCH_MODE_ARROW
    assert result["rows_returned"] == rows


@requires_duckdb_2
def test_adapter_arrow_mode_runs_a_data_changing_statement_once() -> None:
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE TABLE probe (i INTEGER)")
    result = _adapter(FETCH_MODE_ARROW).execute_query(
        connection, "INSERT INTO probe VALUES (7), (8) RETURNING i", "q", validate_row_count=False
    )
    assert result["rows_returned"] == 2
    assert connection.execute("SELECT COUNT(*) FROM probe").fetchall() == [(2,)]


def test_adapter_rows_mode_is_unchanged() -> None:
    result = _adapter(FETCH_MODE_ROWS).execute_query(
        duckdb.connect(":memory:"), "SELECT range AS i FROM range(4)", "q", validate_row_count=False
    )
    assert result["rows_returned"] == 4
    assert result["fetch_mode"] == FETCH_MODE_ROWS
