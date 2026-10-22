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


def _execute(sql: str, dialect: str) -> tuple[list[str], list[tuple]]:
    if dialect == "sqlite":
        connection = sqlite3.connect(":memory:")
    else:
        connection = pytest.importorskip("duckdb").connect(":memory:")
    try:
        cursor = connection.execute(sql.rstrip().rstrip(";"))
        return [d[0] for d in cursor.description], cursor.fetchall()
    finally:
        connection.close()


def _rows(query_id: str, dialect: str) -> list[dict]:
    sql = TPCDIETLQueries().get_query(query_id)
    names, rows = _execute(translate_sql_query(sql, dialect), dialect)
    return [dict(zip(names, row)) for row in rows]


@pytest.mark.parametrize("dialect", ["duckdb", "sqlite"])
def test_eq8_resolution_rate_is_exact(dialect):
    rows = {row["error_category"]: row for row in _rows("EQ8", dialect)}
    assert float(rows["Data Quality Errors"]["error_resolution_rate_pct"]) == pytest.approx(90.0)
    assert float(rows["Transformation Errors"]["error_resolution_rate_pct"]) == pytest.approx(1300 / 15)
    assert float(rows["Referential Integrity Errors"]["error_resolution_rate_pct"]) == pytest.approx(200 / 3)
    assert float(rows["Business Rule Violations"]["error_resolution_rate_pct"]) == pytest.approx(80.0)
    assert float(rows["Loading Errors"]["error_resolution_rate_pct"]) == pytest.approx(100.0)


@pytest.mark.parametrize("dialect", ["duckdb", "sqlite"])
def test_eq6_memory_headroom_terms_are_exact(dialect):
    sql = TPCDIETLQueries().get_query("EQ6")
    terms = re.findall(r"\(\s*1000\s*-\s*(\d+)\s*\)\s*/\s*(\d+(?:\.\d+)?)", sql)
    assert sorted(int(n) for n, _ in terms) == [64, 128, 256, 512]
    for memory, divisor in terms:
        translated = translate_sql_query(f"SELECT (1000 - {memory}) / {divisor} AS headroom", dialect)
        value = _execute(translated, dialect)[1][0][0]
        assert float(value) == pytest.approx((1000 - int(memory)) / 10)


def test_etl_queries_have_no_integer_literal_division():
    queries = TPCDIETLQueries()
    for query_id in ("EQ6", "EQ8"):
        sql = queries.get_query(query_id)
        assert INTEGER_LITERAL_DIVISION.search(sql) is None, query_id
        assert INTEGER_LITERAL_SUBTRACTION_DIVISION.search(sql) is None, query_id
