from __future__ import annotations

import re

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.slow,
    pytest.mark.resource_heavy,
]

TARGET_QUERY_IDS = ("47", "57", "66")


def _bench():
    from benchbox.core.tpcds.benchmark.runner import TPCDSBenchmark

    return TPCDSBenchmark()


def _extract_v2_suffix(query: str) -> str:
    _, suffix = query.split(", v2 AS ", 1)
    return suffix


def _normalize_v2_suffix_for_comparison(query: str) -> str:
    suffix = _extract_v2_suffix(query)
    return re.sub(r"\bv1\.([a-zA-Z_][a-zA-Z0-9_]*)\s+AS\s+\1\b", r"v1.\1", suffix)


def _extract_order_limit(query: str) -> tuple[str, str]:
    match = re.search(r"ORDER BY (?P<order_by>.+) LIMIT (?P<limit>\d+)$", query)
    assert match is not None, f"Expected ORDER BY ... LIMIT ... tail in query:\n{query}"
    return match.group("order_by"), match.group("limit")


def test_clickhouse_target_queries_are_present_and_non_empty():
    clickhouse = _bench().get_queries(dialect="clickhouse")

    for query_id in TARGET_QUERY_IDS:
        assert query_id in clickhouse, f"Q{query_id} missing from ClickHouse query set"
        assert clickhouse[query_id].strip(), f"Q{query_id} is empty in ClickHouse query set"


def test_clickhouse_q47_q57_bulk_queries_alias_qualified_projections_and_keep_dsqgen_shape():
    bench = _bench()
    base = bench.get_queries()
    clickhouse = bench.get_queries(dialect="clickhouse")

    for query_id in ("47", "57"):
        assert "AVG(SUM(" in base[query_id], f"Base Q{query_id} must retain dsqgen nested aggregate shape"
        assert "AVG(SUM(" in clickhouse[query_id], f"ClickHouse Q{query_id} must retain nested aggregate"
        assert _normalize_v2_suffix_for_comparison(base[query_id]) == _normalize_v2_suffix_for_comparison(
            clickhouse[query_id]
        ), f"ClickHouse Q{query_id} must preserve dsqgen-selected projection/order tail"
        assert "v1.d_year AS d_year" in _extract_v2_suffix(clickhouse[query_id])
        assert "enable_analyzer = 0" not in clickhouse[query_id], f"ClickHouse Q{query_id} must not opt out"


def test_clickhouse_q66_bulk_query_qualifies_alias_shadowed_columns():
    bench = _bench()
    base = bench.get_queries()["66"]
    clickhouse = bench.get_queries(dialect="clickhouse")["66"]

    assert "SUM(jan_sales)" in base, "Base Q66 must retain the original aggregate-alias shape"
    assert "SUM(jan_sales)" not in clickhouse, "ClickHouse Q66 must not aggregate over jan_sales alias"
    assert "SUM(x.jan_sales)" in clickhouse
    assert _extract_order_limit(base) == _extract_order_limit(clickhouse), "ClickHouse Q66 must preserve ORDER/LIMIT"
    assert "enable_analyzer = 0" not in clickhouse, "ClickHouse Q66 must not opt out"


def test_clickhouse_overrides_compose_cleanly_with_execution_rewrites():
    from benchbox.platforms.clickhouse.query_rewrites import rewrite_query_for_clickhouse

    bench = _bench()

    for query_id in TARGET_QUERY_IDS:
        ch_query = bench.get_queries(dialect="clickhouse")[query_id]
        final = rewrite_query_for_clickhouse(ch_query)

        assert "SETTINGS joined_subquery_requires_alias = 0" in final, (
            f"Q{query_id}: execution rewrites must append session settings"
        )
        assert "enable_analyzer" not in final, f"Q{query_id}: pipeline must not re-introduce analyzer opt-out"
        assert ("AVG(SUM(" in final) == ("AVG(SUM(" in ch_query), (
            f"Q{query_id}: execution rewrites keep nested aggregates"
        )
