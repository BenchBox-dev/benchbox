from __future__ import annotations

import pytest

from benchbox.core.tpcdi.etl.sql_backend import bulk_insert_dataframe
from benchbox.platforms.base.data_loading import insert_arrow_table
from benchbox.utils.duckdb_extensions import finish_unfinished_result

pytestmark = [pytest.mark.unit, pytest.mark.fast]

duckdb = pytest.importorskip("duckdb")
pd = pytest.importorskip("pandas")
pa = pytest.importorskip("pyarrow")


def _connection_with_unfinished_result() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE TABLE t (i INTEGER)")
    connection.execute("SELECT range AS i FROM range(5000)").fetchone()
    return connection


def test_bulk_dataframe_insert_registers_after_an_unfinished_result() -> None:
    connection = _connection_with_unfinished_result()

    assert bulk_insert_dataframe(connection, "t", ["i"], pd.DataFrame({"i": [1, 2, 3]})) is True

    assert connection.execute("SELECT COUNT(*) FROM t").fetchall() == [(3,)]


def test_arrow_insert_registers_after_an_unfinished_result() -> None:
    connection = _connection_with_unfinished_result()

    assert insert_arrow_table(connection, "t", pa.table({"i": [1, 2, 3, 4]})) == 4

    assert connection.execute("SELECT COUNT(*) FROM t").fetchall() == [(4,)]


def test_finishing_a_connection_with_no_open_result_is_a_no_op() -> None:
    connection = duckdb.connect(":memory:")
    finish_unfinished_result(connection)
    finish_unfinished_result(object())
    assert connection.execute("SELECT 1").fetchall() == [(1,)]
