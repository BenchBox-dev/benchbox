import sqlite3

import duckdb
import pytest

from benchbox.core.tpcds.benchmark.runner import TPCDSBenchmark

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SHIPPED_PREDICATE = (
    "SELECT d1.d_date_sk AS a, d3.d_date_sk AS b FROM date_dim AS d1 JOIN date_dim AS d3 "
    "ON d3.d_date > d1.d_date + 5 ORDER BY 1, 2"
)
DATES = [
    ("2001-01-01", 1),
    ("2001-01-05", 2),
    ("2001-01-06", 3),
    ("2001-01-07", 4),
    ("2001-01-12", 5),
    ("2001-02-03", 6),
]


def _translate(sql: str) -> str:
    benchmark = object.__new__(TPCDSBenchmark)
    return benchmark.translate_query_text(sql, "netezza", "sqlite")


def _duckdb_rows(sql: str) -> list[tuple]:
    connection = duckdb.connect()
    connection.execute("CREATE TABLE date_dim (d_date_sk INTEGER, d_date DATE)")
    connection.executemany("INSERT INTO date_dim VALUES (?, ?)", [(sk, value) for value, sk in DATES])
    return connection.execute(sql).fetchall()


def _sqlite_rows(sql: str) -> list[tuple]:
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE date_dim (d_date_sk INTEGER, d_date TEXT)")
    connection.executemany("INSERT INTO date_dim VALUES (?, ?)", [(sk, value) for value, sk in DATES])
    return connection.execute(sql).fetchall()


def test_q72_ship_date_predicate_adds_whole_days_in_sqlite():
    translated = _translate(SHIPPED_PREDICATE)
    assert "'5 DAY'" in translated.upper() or "'+5 DAY'" in translated.upper()
    expected = _duckdb_rows(SHIPPED_PREDICATE)
    assert expected
    assert _sqlite_rows(translated) == expected


def test_unmarked_translation_differs_from_duckdb():
    from benchbox.utils.dialect_utils import translate_sql_query

    unmarked = translate_sql_query(SHIPPED_PREDICATE, "sqlite", source_dialect="netezza", identify=True)
    assert _sqlite_rows(unmarked) != _duckdb_rows(SHIPPED_PREDICATE)
