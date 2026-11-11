from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _bench():
    from benchbox.core.tpcdi.benchmark import TPCDIBenchmark

    return TPCDIBenchmark()


def test_aq7_snowflake_replaces_julianday_with_datediff():
    q = _bench().get_queries(dialect="snowflake")["AQ7"]
    assert "JULIANDAY" not in q.upper(), f"AQ7 must not use JULIANDAY for Snowflake:\n{q}"
    assert "DATEDIFF(" in q, f"AQ7 must use DATEDIFF() for Snowflake:\n{q}"
    assert "CURRENT_DATE" in q, f"AQ7 must use CURRENT_DATE instead of DATE('now'):\n{q}"


def test_aq8_snowflake_replaces_julianday():
    q = _bench().get_queries(dialect="snowflake")["AQ8"]
    assert "JULIANDAY" not in q.upper(), f"AQ8 must not use JULIANDAY for Snowflake:\n{q}"
    assert "DATEDIFF(" in q, f"AQ8 must use DATEDIFF() for Snowflake:\n{q}"


def test_aq10_snowflake_replaces_julianday():
    q = _bench().get_queries(dialect="snowflake")["AQ10"]
    assert "JULIANDAY" not in q.upper(), f"AQ10 must not use JULIANDAY for Snowflake:\n{q}"
    assert "DATEDIFF(" in q, f"AQ10 must use DATEDIFF() for Snowflake:\n{q}"


def test_eq7_snowflake_uses_derived_quality_relation():
    q = _bench().get_queries(dialect="snowflake")["EQ7"]
    assert "overall_quality_score" in q


def test_get_query_snowflake_matches_bulk_variant():
    bench = _bench()

    assert bench.get_query("EQ7", dialect="snowflake") == bench.get_queries(dialect="snowflake")["EQ7"]


