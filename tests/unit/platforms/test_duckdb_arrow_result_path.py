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
    "query",
    [
        "WITH t AS (SELECT 1 AS a) SELECT a FROM t",
        "  (SELECT 1 AS a)",
        "-- start query 1\n-- in stream 0\nSELECT 1 AS a",
        "/* header */ SELECT 1 AS a",
    ],
)
def test_adapter_arrow_mode_treats_with_and_parenthesised_selects_as_row_returning(query: str) -> None:
    result = _adapter(FETCH_MODE_ARROW).execute_query(duckdb.connect(":memory:"), query, "q", validate_row_count=False)
    assert result["fetch_mode"] == FETCH_MODE_ARROW
    assert result["rows_returned"] == 1


@requires_duckdb_2
def test_adapter_arrow_mode_falls_back_to_rows_for_statements_that_are_not_selects() -> None:
    result = _adapter(FETCH_MODE_ARROW).execute_query(
        duckdb.connect(":memory:"), "SHOW TABLES", "q", validate_row_count=False
    )
    assert result["status"] == "SUCCESS"
    assert result["fetch_mode"] == FETCH_MODE_ROWS


def test_adapter_rows_mode_is_unchanged() -> None:
    result = _adapter(FETCH_MODE_ROWS).execute_query(
        duckdb.connect(":memory:"), "SELECT range AS i FROM range(4)", "q", validate_row_count=False
    )
    assert result["rows_returned"] == 4
    assert result["fetch_mode"] == FETCH_MODE_ROWS
