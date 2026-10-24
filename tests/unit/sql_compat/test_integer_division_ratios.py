from __future__ import annotations

import sqlite3

import pytest

from benchbox.utils.dialect_utils import translate_sql_query

duckdb = pytest.importorskip("duckdb")

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROWS = [
    ("a", "before", 7, 3, 1, 5),
    ("a", "after", 3, 4, 2, 2),
    ("b", "before", 9, 3, 4, 4),
    ("b", "after", 1, 6, 5, 3),
    ("c", "before", 2, 8, 6, 1),
    ("c", "after", 5, 1, 7, 9),
]

RATIO_QUERIES = {
    "q21_sum_ratio": """
        SELECT item, inv_after / inv_before AS ratio FROM (
            SELECT item,
                   SUM(CASE WHEN phase = 'before' THEN qty ELSE 0 END) AS inv_before,
                   SUM(CASE WHEN phase = 'after' THEN qty ELSE 0 END) AS inv_after
            FROM facts GROUP BY item) x
        WHERE inv_before > 0 ORDER BY item
    """,
    "q78_rounded_ratio": """
        SELECT item, ROUND(ss_qty / (COALESCE(ws_qty, 0) + COALESCE(cs_qty, 0)), 2) AS ratio FROM (
            SELECT item, SUM(qty) AS ss_qty, SUM(web) AS ws_qty, SUM(cat) AS cs_qty FROM facts GROUP BY item) x
        ORDER BY item
    """,
    "q83_nested_ratio": """
        SELECT item, sr_qty / (sr_qty + cr_qty + wr_qty) / 3.0 * 100 AS pct FROM (
            SELECT item, SUM(qty) AS sr_qty, SUM(web) AS cr_qty, SUM(cat) AS wr_qty FROM facts GROUP BY item) x
        ORDER BY item
    """,
}


def _reference(sql: str) -> list[tuple]:
    connection = duckdb.connect()
    connection.execute(
        "CREATE TABLE facts (item VARCHAR, phase VARCHAR, qty BIGINT, web BIGINT, cat BIGINT, other BIGINT)"
    )
    connection.executemany("INSERT INTO facts VALUES (?, ?, ?, ?, ?, ?)", ROWS)
    return connection.execute(sql).fetchall()


def _sqlite(sql: str) -> list[tuple]:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE facts (item TEXT, phase TEXT, qty INTEGER, web INTEGER, cat INTEGER, other INTEGER)"
    )
    connection.executemany("INSERT INTO facts VALUES (?, ?, ?, ?, ?, ?)", ROWS)
    return connection.execute(sql).fetchall()


def _datafusion(sql: str) -> list[tuple]:
    datafusion = pytest.importorskip("datafusion")
    pa = pytest.importorskip("pyarrow")
    context = datafusion.SessionContext()
    columns = list(zip(*ROWS, strict=True))
    context.register_record_batches(
        "facts",
        [
            [
                pa.RecordBatch.from_arrays(
                    [
                        pa.array(column, type=pa.string() if index < 2 else pa.int64())
                        for index, column in enumerate(columns)
                    ],
                    names=["item", "phase", "qty", "web", "cat", "other"],
                )
            ]
        ],
    )
    return [
        tuple(column[index].as_py() for column in batch.columns)
        for batch in context.sql(sql).collect()
        for index in range(batch.num_rows)
    ]


def _close(actual: list[tuple], expected: list[tuple]) -> None:
    assert len(actual) == len(expected)
    for actual_row, expected_row in zip(actual, expected, strict=True):
        assert actual_row[0] == expected_row[0]
        assert float(actual_row[1]) == pytest.approx(float(expected_row[1]), rel=1e-9)


@pytest.mark.parametrize("name", RATIO_QUERIES)
def test_sqlite_integer_ratios_match_duckdb(name: str):
    source = RATIO_QUERIES[name]
    translated = translate_sql_query(source, "sqlite", "duckdb")
    assert "REAL" in translated.upper()
    _close(_sqlite(translated), _reference(source))


@pytest.mark.parametrize("name", RATIO_QUERIES)
def test_datafusion_integer_ratios_match_duckdb(name: str):
    source = RATIO_QUERIES[name]
    translated = translate_sql_query(source, "datafusion", "duckdb")
    assert "DOUBLE" in translated.upper()
    _close(_datafusion(translated), _reference(source))


def test_sqlite_untranslated_integer_division_truncates():
    source = RATIO_QUERIES["q21_sum_ratio"]
    truncated = _sqlite(source)
    assert truncated != [(item, float(ratio)) for item, ratio in _reference(source)]


@pytest.mark.parametrize("name", RATIO_QUERIES)
def test_postgres_integer_ratios_cast_the_dividend(name: str):
    translated = translate_sql_query(RATIO_QUERIES[name], "postgres", "duckdb")
    assert "DOUBLE PRECISION" in translated.upper()
