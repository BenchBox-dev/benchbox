from __future__ import annotations

import re

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _bench():
    from benchbox.core.tpcdi.benchmark import TPCDIBenchmark

    return TPCDIBenchmark()


def test_aq7_doris_replaces_julianday_with_datediff():
    q = _bench().get_queries(dialect="doris")["AQ7"]
    assert "JULIANDAY" not in q.upper(), f"AQ7 must not use JULIANDAY for Doris:\n{q}"
    assert "DATEDIFF(" in q, f"AQ7 must use DATEDIFF() for Doris:\n{q}"
    assert "DATE_SUB(" in q, f"AQ7 must use DATE_SUB() for Doris:\n{q}"
    assert "CURRENT_DATE()" in q, f"AQ7 must use CURRENT_DATE() for Doris:\n{q}"


def test_aq8_doris_replaces_julianday():
    q = _bench().get_queries(dialect="doris")["AQ8"]
    assert "JULIANDAY" not in q.upper(), f"AQ8 must not use JULIANDAY for Doris:\n{q}"
    assert "DATEDIFF(" in q, f"AQ8 must use DATEDIFF() for Doris:\n{q}"


def test_aq10_doris_replaces_julianday():
    q = _bench().get_queries(dialect="doris")["AQ10"]
    assert "JULIANDAY" not in q.upper(), f"AQ10 must not use JULIANDAY for Doris:\n{q}"
    assert "DATEDIFF(" in q, f"AQ10 must use DATEDIFF() for Doris:\n{q}"


def test_eq3_doris_flattens_nested_count():
    q = _bench().get_queries(dialect="doris")["EQ3"]
    assert not re.search(
        r"SELECT\s+COUNT\s*\(\s*\*\s*\)\s+FROM\s+\(\s*SELECT\s+COUNT",
        q,
        re.IGNORECASE,
    ), f"EQ3 must not keep the double-nested COUNT scalar subquery for Doris:\n{q[:500]}"
    assert re.search(
        r"SELECT COUNT\(\*\) FROM `?DimCustomer`? WHERE `?BatchID`?\s*=\s*1",
        q,
        re.IGNORECASE,
    ), f"EQ3 must use a flat COUNT(*) FROM DimCustomer subquery for Doris:\n{q[:500]}"


def test_eq7_doris_uses_derived_table_variant():
    q = _bench().get_queries(dialect="doris")["EQ7"]
    assert "quality_metrics" in q, f"EQ7 must alias the derived table as quality_metrics:\n{q}"
    assert "overall_quality_score" in q, f"EQ7 must project overall_quality_score:\n{q}"


def test_non_overridden_queries_present_in_doris_dialect():
    bench = _bench()
    base = bench.get_queries()
    doris = bench.get_queries(dialect="doris")

    for qid in base:
        assert qid in doris, f"{qid} missing from Doris queries"
        assert doris[qid].strip(), f"{qid} is empty in Doris queries"
