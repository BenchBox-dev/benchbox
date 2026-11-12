from __future__ import annotations

import warnings

import pytest

from benchbox.core.results.fetch_mode import FETCH_MODE_ARROW, FETCH_MODE_ROWS
from benchbox.platforms.duckdb import DuckDBAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _adapter(fetch_mode: str) -> DuckDBAdapter:
    return DuckDBAdapter(database_path=":memory:", fetch_mode=fetch_mode)


def test_arrow_mode_reads_selects_through_a_relation_without_an_arrow_warning() -> None:
    import duckdb

    adapter = _adapter(FETCH_MODE_ARROW)
    connection = duckdb.connect(":memory:")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = adapter._open_result(connection, "SELECT range AS i FROM range(3)")
        assert result.to_arrow_table().num_rows == 3


def test_arrow_mode_executes_statements_that_are_not_queries() -> None:
    import duckdb

    adapter = _adapter(FETCH_MODE_ARROW)
    connection = duckdb.connect(":memory:")

    adapter._open_result(connection, "CREATE TABLE t (i INTEGER)")

    assert connection.execute("SELECT count(*) FROM t").fetchone() == (0,)


def test_rows_mode_keeps_dbapi_execution() -> None:
    import duckdb

    adapter = _adapter(FETCH_MODE_ROWS)
    connection = duckdb.connect(":memory:")

    assert adapter._open_result(connection, "SELECT 1").fetchall() == [(1,)]
