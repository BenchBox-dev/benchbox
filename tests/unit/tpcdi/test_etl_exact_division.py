import re
import sqlite3

import pytest

from benchbox.core.tpcdi.query_etl import TPCDIETLQueries
from benchbox.utils.dialect_utils import translate_sql_query

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

INTEGER_LITERAL_DIVISION = re.compile(r"(?<![\w.])\d+\s*/\s*\d+(?![\w.])")
INTEGER_LITERAL_SUBTRACTION_DIVISION = re.compile(r"\(\s*\d+\s*-\s*\d+\s*\)\s*/\s*\d+(?![\w.])")


def _run_duckdb(sql: str) -> list[tuple]:
    duckdb = pytest.importorskip("duckdb")
    connection = duckdb.connect(":memory:")
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def _run_sqlite(sql: str) -> list[tuple]:
    connection = sqlite3.connect(":memory:")
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


RUNNERS = {"duckdb": _run_duckdb, "sqlite": _run_sqlite}


def _rows(query_id: str, dialect: str) -> list[dict]:
    sql = TPCDIETLQueries().get_query(query_id)
    translated = translate_sql_query(sql, dialect, source_dialect="duckdb")
    cursor_sql = translated.rstrip().rstrip(";")
    if dialect == "sqlite":
        connection = sqlite3.connect(":memory:")
        try:
            cursor = connection.execute(cursor_sql)
            names = [d[0] for d in cursor.description]
            return [dict(zip(names, row)) for row in cursor.fetchall()]
        finally:
            connection.close()
    duckdb = pytest.importorskip("duckdb")
    connection = duckdb.connect(":memory:")
    try:
        cursor = connection.execute(cursor_sql)
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]
    finally:
        connection.close()


@pytest.mark.parametrize("dialect", ["duckdb", "sqlite"])
def test_eq8_resolution_rate_is_exact(dialect):
    rows = {row["error_category"]: row for row in _rows("EQ8", dialect)}
    assert float(rows["Data Quality Errors"]["error_resolution_rate_pct"]) == pytest.approx(90.0)
    assert float(rows["Transformation Errors"]["error_resolution_rate_pct"]) == pytest.approx(1300 / 15)
    assert float(rows["Referential Integrity Errors"]["error_resolution_rate_pct"]) == pytest.approx(200 / 3)
    assert float(rows["Business Rule Violations"]["error_resolution_rate_pct"]) == pytest.approx(80.0)
    assert float(rows["Loading Errors"]["error_resolution_rate_pct"]) == pytest.approx(100.0)


@pytest.mark.parametrize("dialect", ["duckdb", "sqlite"])
def test_eq6_performance_rating_uses_exact_memory_term(dialect):
    rows = {row["etl_phase"]: row for row in _rows("EQ6", dialect)}
    expected = {
        "Data Extraction": (10000 / 2.5) / 100 * 50 + (100 - 45) * 0.3 + (1000 - 256) / 10 * 0.2,
        "Data Transformation": (10000 / 8.0) / 100 * 50 + (100 - 75) * 0.3 + (1000 - 512) / 10 * 0.2,
        "Data Loading": (10000 / 5.0) / 100 * 50 + (100 - 30) * 0.3 + (1000 - 128) / 10 * 0.2,
        "Data Validation": (10000 / 3.0) / 100 * 50 + (100 - 20) * 0.3 + (1000 - 64) / 10 * 0.2,
    }
    for phase, raw in expected.items():
        assert float(rows[phase]["performance_rating"]) == pytest.approx(min(100.0, raw))


@pytest.mark.parametrize("dialect", ["duckdb", "sqlite"])
def test_eq6_memory_headroom_term_is_decimal(dialect):
    sql = translate_sql_query("SELECT (1000 - 64) / 10.0 AS headroom", dialect, source_dialect="duckdb")
    assert float(RUNNERS[dialect](sql)[0][0]) == pytest.approx(93.6)


@pytest.mark.parametrize("dialect", ["duckdb", "sqlite"])
def test_eq8_literal_ratio_is_decimal(dialect):
    sql = translate_sql_query("SELECT 45.0 / 50 * 100 AS rate", dialect, source_dialect="duckdb")
    assert float(RUNNERS[dialect](sql)[0][0]) == pytest.approx(90.0)


def test_etl_queries_have_no_integer_literal_division():
    queries = TPCDIETLQueries()
    for query_id in ("EQ6", "EQ8"):
        sql = queries.get_query(query_id)
        assert INTEGER_LITERAL_DIVISION.search(sql) is None, query_id
        assert INTEGER_LITERAL_SUBTRACTION_DIVISION.search(sql) is None, query_id
