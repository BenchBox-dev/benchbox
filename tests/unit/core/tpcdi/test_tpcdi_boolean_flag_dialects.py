from __future__ import annotations

import re

import pytest

from benchbox._vendor import sqlglot

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _bench():
    from benchbox.core.tpcdi.benchmark import TPCDIBenchmark

    return TPCDIBenchmark(scale_factor=0.01)


def test_bigquery_compares_flag_columns_to_boolean_literals():
    translated = _bench().translate_query_text(
        "SELECT * FROM DimCustomer WHERE IsCurrent = 1 AND TT_IS_SELL = 0 AND BatchID = 1", "bigquery"
    )
    assert "`IsCurrent` = TRUE" in translated
    assert "`TT_IS_SELL` = FALSE" in translated
    assert "`BatchID` = 1" in translated


def test_questdb_compares_flag_columns_to_boolean_literals():
    translated = _bench().translate_query_text(
        "SELECT * FROM DimCustomer WHERE IsCurrent = 1 AND TT_IS_SELL = 0 AND BatchID = 1", "questdb"
    )
    assert "IsCurrent = TRUE" in translated
    assert "TT_IS_SELL = FALSE" in translated
    assert "BatchID = 1" in translated


def test_spark_tests_flag_columns_with_is_boolean():
    translated = _bench().translate_query_text(
        "SELECT * FROM DimCustomer WHERE IsCurrent = 1 AND TT_IS_SELL = 0 AND BatchID = 1", "spark"
    )
    assert "`IsCurrent` IS TRUE" in translated
    assert "`TT_IS_SELL` IS FALSE" in translated
    assert "`BatchID` = 1" in translated


def test_databricks_tests_flag_columns_with_is_boolean():
    translated = _bench().translate_query_text(
        "SELECT * FROM DimCustomer WHERE IsCurrent = 1 AND TT_IS_SELL = 0 AND BatchID = 1", "databricks"
    )
    assert "`IsCurrent` IS TRUE" in translated
    assert "`TT_IS_SELL` IS FALSE" in translated
    assert "`BatchID` = 1" in translated


@pytest.mark.parametrize("dialect", ["bigquery", "databricks"])
def test_analytical_queries_leave_no_sqlite_idioms_and_stay_parseable(dialect: str):
    queries = _bench().get_queries(dialect=dialect)
    for query_id in ("AQ7", "AQ8", "AQ10"):
        query = queries[query_id]
        assert "JULIANDAY" not in query.upper()
        assert "DATE('now'" not in query
        sqlglot.parse_one(query, read=dialect)


def test_bigquery_relative_date_uses_interval_arithmetic():
    query = _bench().get_queries(dialect="bigquery")["AQ7"]
    assert re.search(r"INTERVAL '?90'? DAY", query)


def test_eq3_counts_source_rows_with_a_single_count():
    query = _bench().get_queries()["EQ3"]
    assert "(SELECT COUNT(*) FROM DimCustomer WHERE BatchID = 1) as source_records" in query
    assert not re.search(r"SELECT\s+COUNT\s*\(\s*\*\s*\)\s+FROM\s+\(\s*(?:--[^\n]*\s*)?SELECT\s+COUNT", query)
