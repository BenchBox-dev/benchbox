from __future__ import annotations

import sqlite3

import duckdb
import pytest

from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark
from benchbox.core.tpchavoc.dialect_compat import LIST_AGGREGATE_DIALECTS, LIST_AGGREGATE_VARIANT_IDS

pytestmark = [pytest.mark.unit, pytest.mark.medium]

LINEITEM_DDL = (
    "CREATE TABLE lineitem (l_orderkey INTEGER, l_linenumber INTEGER, l_returnflag TEXT, l_linestatus TEXT, "
    "l_quantity REAL, l_extendedprice REAL, l_discount REAL, l_tax REAL, l_shipdate DATE)"
)
LINEITEM_ROWS = [
    (1, 1, "A", "F", 17.0, 21168.23, 0.04, 0.02, "1996-03-13"),
    (1, 2, "A", "F", 36.0, 45983.16, 0.09, 0.06, "1996-04-12"),
    (2, 1, "A", "F", 8.0, 13309.6, 0.10, 0.02, "1997-01-28"),
    (3, 1, "N", "O", 45.0, 54058.05, 0.06, 0.0, "1997-11-30"),
    (3, 2, "N", "O", 49.0, 46796.47, 0.10, 0.0, "1998-01-02"),
    (4, 1, "N", "O", 30.0, 41544.1, 0.03, 0.08, "1998-08-19"),
    (5, 1, "R", "F", 3.0, 4400.0, 0.05, 0.04, "1995-06-01"),
    (6, 1, "R", "F", 9.0, 9000.0, 0.07, 0.01, "1998-12-01"),
]


def test_sqlite_is_a_list_aggregate_dialect():
    assert "sqlite" in LIST_AGGREGATE_DIALECTS
    assert {"1_v7"} == LIST_AGGREGATE_VARIANT_IDS


def test_sqlite_list_aggregate_variant_matches_duckdb():
    benchmark = TPCHavocBenchmark(scale_factor=0.01)
    sqlite_sql = benchmark.get_query("1_v7", dialect="sqlite")
    duckdb_sql = benchmark.get_query("1_v7", dialect="duckdb")
    for duckdb_function in ("LIST(", "LIST_SUM", "LIST_AVG", "LIST_ZIP", "LIST_TRANSFORM"):
        assert duckdb_function not in sqlite_sql.upper()

    marks = ", ".join("?" * 9)
    with sqlite3.connect(":memory:") as lite, duckdb.connect(":memory:") as duck:
        lite.execute(LINEITEM_DDL)
        lite.executemany(f"INSERT INTO lineitem VALUES ({marks})", LINEITEM_ROWS)
        duck.execute(LINEITEM_DDL.replace("REAL", "DOUBLE"))
        duck.executemany(f"INSERT INTO lineitem VALUES ({marks})", LINEITEM_ROWS)
        actual = lite.execute(sqlite_sql).fetchall()
        expected = duck.execute(duckdb_sql).fetchall()

    assert [row[:2] for row in actual] == [row[:2] for row in expected] == [("A", "F"), ("N", "O"), ("R", "F")]
    assert len(actual) == len(expected)
    for got_row, want_row in zip(actual, expected, strict=True):
        for got, want in zip(got_row[2:], want_row[2:], strict=True):
            assert float(got) == pytest.approx(float(want), rel=1e-9)
