from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _bench():
    from benchbox.core.tpcdi.benchmark import TPCDIBenchmark

    return TPCDIBenchmark()


def test_eq7_bigquery_uses_derived_table_variant():
    q = _bench().get_queries(dialect="bigquery")["EQ7"]
    assert "quality_metrics" in q, f"EQ7 must alias the derived table as quality_metrics:\n{q}"
    assert "overall_quality_score" in q, f"EQ7 must project overall_quality_score:\n{q}"
    assert "quality_calculation" not in q, f"EQ7 must not cross-reference sibling aliases:\n{q}"


def test_get_query_bigquery_eq7_preserves_parameter_substitution():
    q = _bench().get_query(
        "EQ7",
        params={
            "excellent_quality_threshold": 99.0,
            "good_quality_threshold": 88.0,
            "acceptable_quality_threshold": 77.0,
        },
        dialect="bigquery",
    )

    assert ") >= 99.0 THEN" in q
    assert ") >= 88.0 THEN" in q
    assert ") >= 77.0 THEN" in q


def test_non_overridden_queries_present_in_bigquery_dialect():
    bench = _bench()
    base = bench.get_queries()
    bigquery = bench.get_queries(dialect="bigquery")

    for qid in base:
        assert qid in bigquery, f"{qid} missing from BigQuery queries"
        assert bigquery[qid].strip(), f"{qid} is empty in BigQuery queries"


