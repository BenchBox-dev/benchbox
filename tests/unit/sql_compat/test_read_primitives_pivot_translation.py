import sqlite3

import duckdb
import pytest

from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark

pytestmark = [pytest.mark.unit, pytest.mark.fast]

QUERY_IDS = ("pivot_basic", "unpivot_basic", "orderby_all_desc")

SETUP = (
    "CREATE TABLE lineitem (l_returnflag VARCHAR, l_shipmode VARCHAR, l_quantity DOUBLE, l_shipdate DATE)",
    "INSERT INTO lineitem VALUES "
    "('A', 'AIR', 10, '1995-01-05'), ('A', 'AIR', 5, '1995-02-05'), ('A', 'RAIL', 7, '1995-03-05'), "
    "('N', 'SHIP', 1, '1995-01-09'), ('N', 'MAIL', 2, '1995-01-10'), ('R', 'TRUCK', 9, '1995-03-30'), "
    "('R', 'AIR', 4, '1996-01-01')",
    "CREATE TABLE part (p_partkey INTEGER, p_brand VARCHAR, p_type VARCHAR, p_size INTEGER, p_retailprice DOUBLE)",
    "INSERT INTO part VALUES (1, 'B1', 'T1', 5, 10.5), (2, 'B2', 'T1', 7, 20.25), (3, 'B1', 'T2', 9, 30.0), "
    "(4, 'B3', 'T3', 11, 40.0), (200, 'B4', 'T4', 1, 1.0)",
)


def _duckdb_rows(sql):
    with duckdb.connect() as connection:
        for statement in SETUP:
            connection.execute(statement)
        return connection.execute(sql).fetchall()


def _sqlite_rows(sql):
    connection = sqlite3.connect(":memory:")
    try:
        for statement in SETUP:
            connection.execute(statement)
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def _datafusion_rows(sql):
    datafusion = pytest.importorskip("datafusion")
    context = datafusion.SessionContext()
    for statement in SETUP:
        context.sql(statement).collect()
    rows = []
    for batch in context.sql(sql).collect():
        columns = [batch.column(index).to_pylist() for index in range(batch.num_columns)]
        rows.extend(zip(*columns))
    return rows


def _normalise(rows):
    return sorted(tuple(float(cell) if isinstance(cell, (int, float)) else cell for cell in row) for row in rows)


@pytest.fixture(scope="module")
def read_primitives():
    return ReadPrimitivesBenchmark(scale_factor=0.01)


@pytest.mark.parametrize("query_id", QUERY_IDS)
def test_sqlite_rows_equal_duckdb_rows(read_primitives, query_id):
    source = read_primitives.get_queries()[query_id]
    translated = read_primitives.get_queries(dialect="sqlite")[query_id]
    assert _normalise(_sqlite_rows(translated)) == _normalise(_duckdb_rows(source))


def test_sqlite_orderby_all_desc_keeps_direction(read_primitives):
    translated = read_primitives.get_queries(dialect="sqlite")["orderby_all_desc"]
    assert "ORDER BY 1 DESC" in translated
    assert _sqlite_rows(translated) == _duckdb_rows(read_primitives.get_queries()["orderby_all_desc"])


def test_datafusion_pivot_rows_equal_duckdb_rows(read_primitives):
    source = read_primitives.get_queries()["pivot_basic"]
    translated = read_primitives.get_queries(dialect="datafusion")["pivot_basic"]
    assert _normalise(_datafusion_rows(translated)) == _normalise(_duckdb_rows(source))
