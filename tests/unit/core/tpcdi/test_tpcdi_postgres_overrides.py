from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _bench():
    from benchbox.core.tpcdi.benchmark import TPCDIBenchmark

    return TPCDIBenchmark(scale_factor=0.01)


def test_postgres_replaces_julianday_date_arithmetic() -> None:
    queries = _bench().get_queries(dialect="postgres")

    for query_id in ("AQ7", "AQ8", "AQ10"):
        query = queries[query_id]
        assert "JULIANDAY" not in query.upper(), f"{query_id} retained JULIANDAY:\n{query}"
        assert "EXTRACT(epoch FROM" in query and "/ 86400" in query, (
            f"{query_id} should use PostgreSQL timestamp subtraction:\n{query}"
        )


def test_postgres_renders_boolean_numeric_comparisons() -> None:
    queries = _bench().get_queries(dialect="postgres")
    rendered = "\n".join(queries.values())

    assert "IsCurrent = 1" not in rendered
    assert "IsCurrent = 0" not in rendered
    assert "TT_IS_SELL = 1" not in rendered
    assert "TT_IS_SELL = 0" not in rendered
    assert "IsCurrent IS TRUE" in rendered
    assert "TT_IS_SELL IS TRUE" in rendered
    assert "TT_IS_SELL IS FALSE" in rendered
    assert "HolidayFlag = 1" not in rendered
    assert "HolidayFlag = 0" not in rendered


def test_postgres_removes_same_select_alias_references() -> None:
    queries = _bench().get_queries(dialect="postgres")

    assert "HAVING customer_count > 10" not in queries["A5"]
    assert "HAVING COUNT(DISTINCT c.SK_CustomerID) > 10" in queries["A5"]
    assert "CASE risk_profile" not in queries["AQ10"]
    assert "ORDER BY 15, total_trade_value DESC" in queries["AQ10"]
    assert "FROM (VALUES (0)) AS dummy" not in queries["EQ7"]
    assert "overall_quality_score" in queries["EQ7"]


def test_datafusion_renders_boolean_dates_and_eq7_aliases() -> None:
    queries = _bench().get_queries(dialect="datafusion")
    rendered = "\n".join(queries.values())

    assert "IsCurrent = 1" not in rendered
    assert "IsCurrent = 0" not in rendered
    assert "TT_IS_SELL = 1" not in rendered
    assert "TT_IS_SELL = 0" not in rendered
    assert "JULIANDAY" not in rendered.upper()
    assert "date_diff(" not in rendered.lower()
    assert "AGE(" not in rendered.upper()
    assert "FROM (VALUES (0)) AS dummy" not in queries["EQ7"]
    assert "FROM (\n    SELECT" in queries["EQ7"]
    assert "IsCurrent IS TRUE" in queries["EQ7"]


@pytest.mark.parametrize("query_id", ["AQ9", "EQ7", "VQ6"])
def test_get_query_datafusion_matches_bulk_variant(query_id: str) -> None:
    bench = _bench()

    assert bench.get_query(query_id, dialect="datafusion") == bench.get_queries(dialect="datafusion")[query_id]
