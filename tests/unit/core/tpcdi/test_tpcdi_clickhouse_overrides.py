from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _bench():
    from benchbox.core.tpcdi.benchmark import TPCDIBenchmark

    return TPCDIBenchmark()


def test_aq6_clickhouse_uses_translated_base_query_semantics():
    q = _bench().get_queries(dialect="clickhouse")["AQ6"]
    assert "stddevSamp(mh.ClosePrice)" in q, f"AQ6 must keep sample standard deviation:\n{q}"
    assert "stddevPop" not in q, f"AQ6 must not use population standard deviation:\n{q}"
    assert "grand_totals" not in q, f"AQ6 must not use a hand-written grand-total join:\n{q}"
    assert "OVER ()" in q, f"AQ6 market share must keep the window-aggregate denominator:\n{q}"


def test_aq6_base_dialect_retains_window_aggregate():
    q = _bench().get_queries()["AQ6"]
    assert "SUM(SUM(" in q, f"Base AQ6 must use nested window aggregate:\n{q}"


def test_aq7_clickhouse_replaces_julianday_with_datediff():
    q = _bench().get_queries(dialect="clickhouse")["AQ7"]
    assert "JULIANDAY" not in q.upper(), f"AQ7 must not use JULIANDAY for ClickHouse:\n{q}"
    assert "dateDiff(" in q, f"AQ7 must use dateDiff() for ClickHouse:\n{q}"
    assert "today()" in q, f"AQ7 must use today() instead of DATE('now') for ClickHouse:\n{q}"


def test_aq7_base_dialect_retains_julianday():
    q = _bench().get_queries()["AQ7"]
    assert "JULIANDAY" in q.upper(), f"Base AQ7 must use JULIANDAY:\n{q}"


def test_aq8_clickhouse_replaces_julianday():
    q = _bench().get_queries(dialect="clickhouse")["AQ8"]
    assert "JULIANDAY" not in q.upper(), f"AQ8 must not use JULIANDAY for ClickHouse:\n{q}"
    assert "dateDiff(" in q, f"AQ8 must use dateDiff() for ClickHouse:\n{q}"


def test_aq10_clickhouse_replaces_julianday():
    q = _bench().get_queries(dialect="clickhouse")["AQ10"]
    assert "JULIANDAY" not in q.upper(), f"AQ10 must not use JULIANDAY for ClickHouse:\n{q}"
    assert "dateDiff(" in q, f"AQ10 must use dateDiff() for ClickHouse:\n{q}"


@pytest.mark.parametrize("query_id", ["AQ7", "AQ8", "AQ10", "EQ7"])
def test_get_query_clickhouse_matches_bulk_variant(query_id):
    bench = _bench()

    assert bench.get_query(query_id, dialect="clickhouse") == bench.get_queries(dialect="clickhouse")[query_id]


def test_get_query_clickhouse_preserves_parameter_substitution():
    q = _bench().get_query(
        "AQ10",
        params={
            "start_year": 2020,
            "large_trade_threshold": 1234.5,
            "large_trade_count_threshold": 7,
            "same_day_trade_threshold": 2,
            "limit_rows": 11,
        },
        dialect="clickhouse",
    )

    assert "2020" in q
    assert "1234.5" in q
    assert "> 7 THEN" in q
    assert "> 2 THEN" in q
    assert "LIMIT 11" in q


def test_get_query_clickhouse_eq7_preserves_parameter_substitution():
    q = _bench().get_query(
        "EQ7",
        params={
            "excellent_quality_threshold": 99.0,
            "good_quality_threshold": 88.0,
            "acceptable_quality_threshold": 77.0,
        },
        dialect="clickhouse",
    )

    assert ") >= 99.0 THEN" in q
    assert ") >= 88.0 THEN" in q
    assert ") >= 77.0 THEN" in q


def test_eq7_clickhouse_uses_derived_quality_relation():
    import re

    q = _bench().get_queries(dialect="clickhouse")["EQ7"]
    assert "quality_metrics" in q
    assert not re.search(r"FROM\s+VALUES", q, re.IGNORECASE)
    assert "overall_quality_score" in q


def test_eq7_base_dialect_retains_values():
    q = _bench().get_queries()["EQ7"]
    assert "VALUES" in q.upper(), f"Base EQ7 must use VALUES dummy row:\n{q}"


def test_eq3_clickhouse_flattens_nested_count():
    import re

    q = _bench().get_queries(dialect="clickhouse")["EQ3"]

    assert not re.search(r"SELECT\s+COUNT\s*\(\s*\*\s*\)\s+FROM\s+\(\s*(?:/\*|--)", q, re.IGNORECASE), (
        f"EQ3 must not have double-nested COUNT scalar subquery for ClickHouse:\n{q[:500]}"
    )

    assert re.search(r"COUNT\(\*\)\s+FROM\s+DimCustomer\s+WHERE\s+BatchID", q, re.IGNORECASE), (
        f"EQ3 must have flat COUNT(*) FROM DimCustomer for ClickHouse:\n{q[:500]}"
    )


def test_non_overridden_queries_present_in_clickhouse_dialect():
    bench = _bench()
    base = bench.get_queries()
    clickhouse = bench.get_queries(dialect="clickhouse")

    for qid in base:
        assert qid in clickhouse, f"{qid} missing from ClickHouse queries"
        assert clickhouse[qid].strip(), f"{qid} is empty in ClickHouse queries"
