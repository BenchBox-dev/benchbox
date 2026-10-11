from __future__ import annotations

import re

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

SEEDED_QUERY_IDS = (35, 47, 57, 66)
SEEDS = (1, 2, 3)


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


def _extract_q66_filters(query: str) -> tuple[list[str], list[str]]:
    carriers = re.findall(r"sm_carrier IN \([^)]+\)", query)
    time_windows = re.findall(r"t_time BETWEEN \d+ AND \d+ \+ 28800", query)
    return carriers, time_windows


def test_clickhouse_q35_rewrites_correlated_exists_to_semijoins_across_seeds():
    bench = _bench()

    for seed in SEEDS:
        base = bench.get_query(35, seed=seed)
        clickhouse = bench.get_query(35, seed=seed, dialect="clickhouse")

        assert base.count("EXISTS(SELECT * FROM") == 3
        assert "EXISTS(SELECT * FROM" not in clickhouse
        assert clickhouse.count("c.c_customer_sk IN (SELECT") == 3
        assert "d_year = " in clickhouse
        assert "d_qoy < 4" in clickhouse


def test_clickhouse_q35_rewrite_fails_closed_on_unexpected_shape():
    with pytest.raises(ValueError, match="expected 3 semi-join predicates"):
        from benchbox.core.tpcds.benchmark.clickhouse_overrides import rewrite_q35_for_clickhouse

        rewrite_q35_for_clickhouse("SELECT * FROM store_sales")


def test_clickhouse_q10_rewrites_correlated_exists_to_semijoins_across_seeds():
    bench = _bench()

    for seed in SEEDS:
        base = bench.get_query(10, seed=seed)
        clickhouse = bench.get_query(10, seed=seed, dialect="clickhouse")

        assert "exists" in base.lower()
        assert "EXISTS" not in clickhouse
        assert clickhouse.count("c.c_customer_sk IN (SELECT") == 3
        assert "d_moy BETWEEN" in clickhouse


def test_clickhouse_q10_rewrite_fails_closed_on_unexpected_shape():
    with pytest.raises(ValueError, match="expected 3 semi-join predicates"):
        from benchbox.core.tpcds.benchmark.clickhouse_overrides import rewrite_q10_for_clickhouse

        rewrite_q10_for_clickhouse("SELECT * FROM store_sales")


def test_non_clickhouse_q10_keeps_original_exists_shape():
    bench = _bench()

    query = bench.get_query(10, seed=SEEDS[0])

    assert "exists" in query.lower()
    assert "c.c_customer_sk IN (SELECT" not in query


def test_non_clickhouse_q35_keeps_original_exists_shape():
    bench = _bench()

    query = bench.get_query(35, seed=SEEDS[0])

    assert "EXISTS(SELECT * FROM" in query
    assert "c.c_customer_sk IN (SELECT" not in query


def test_clickhouse_q35a_keeps_supported_variant_shape():
    bench = _bench()

    query = bench.get_query(35, variant="a", seed=SEEDS[0], dialect="clickhouse")

    assert "store_sales" in query
    assert "web_sales" in query
    assert "catalog_sales" in query
    assert "c.c_customer_sk IN (SELECT" not in query


def test_clickhouse_q47_q57_seeded_queries_preserve_dsqgen_tail_across_seeds():
    bench = _bench()

    for seed in SEEDS:
        for query_id in (47, 57):
            base = bench.get_query(query_id, seed=seed)
            clickhouse = bench.get_query(query_id, seed=seed, dialect="clickhouse")

            assert "AVG(SUM(" in base, f"Base Q{query_id} seed {seed} must retain nested aggregate shape"
            assert "AVG(SUM(" not in clickhouse, f"ClickHouse Q{query_id} seed {seed} must remove nested aggregate"
            assert _normalize_v2_suffix_for_comparison(base) == _normalize_v2_suffix_for_comparison(clickhouse), (
                f"ClickHouse Q{query_id} seed {seed} must preserve dsqgen-selected v2 tail"
            )
            assert "enable_analyzer = 0" not in clickhouse, f"ClickHouse Q{query_id} seed {seed} must not opt out"


def test_clickhouse_q66_seeded_queries_preserve_filters_without_old_analyzer_fallback():
    bench = _bench()

    for seed in SEEDS:
        base = bench.get_query(66, seed=seed)
        clickhouse = bench.get_query(66, seed=seed, dialect="clickhouse")
        base_carriers, base_time_windows = _extract_q66_filters(base)

        assert "SUM(jan_sales)" in base, f"Base Q66 seed {seed} must retain original aggregate-alias shape"
        assert "SUM(jan_sales)" not in clickhouse, f"ClickHouse Q66 seed {seed} must remove jan_sales alias aggregation"
        assert "SUM(jan_net)" not in clickhouse, f"ClickHouse Q66 seed {seed} must remove jan_net alias aggregation"
        assert "WITH channel_sales AS (" in clickhouse, f"ClickHouse Q66 seed {seed} must use channel_sales"
        assert _extract_order_limit(base) == _extract_order_limit(clickhouse), (
            f"ClickHouse Q66 seed {seed} must preserve ORDER/LIMIT"
        )
        for carrier_filter in base_carriers:
            assert carrier_filter in clickhouse, (
                f"ClickHouse Q66 seed {seed} must preserve carrier filter {carrier_filter}"
            )
        for time_window in base_time_windows:
            assert time_window in clickhouse, f"ClickHouse Q66 seed {seed} must preserve time window {time_window}"
        assert "enable_analyzer = 0" not in clickhouse, f"ClickHouse Q66 seed {seed} must not opt out"


def test_clickhouse_q47_rewrite_rejects_unexpected_cte_shape():
    bench = _bench()
    malformed = "SELECT AVG(SUM(x)) OVER () FROM t"
    with pytest.raises(ValueError, match="expected leading v1 CTE"):
        bench._rewrite_clickhouse_monthly_avg_query(47, malformed)


def test_clickhouse_q47_rewrite_rejects_unexpected_aggregate_shape():
    bench = _bench()
    malformed = "WITH v1 AS (SELECT AVG(SUM(x)) FROM t)"
    with pytest.raises(ValueError, match="monthly aggregate shape"):
        bench._rewrite_clickhouse_monthly_avg_query(47, malformed)


def test_clickhouse_q66_rewrite_rejects_missing_derived_table():
    bench = _bench()
    malformed = "SELECT SUM(jan_sales) AS jan_sales FROM t"
    with pytest.raises(ValueError, match="missing derived table"):
        bench._rewrite_clickhouse_q66(malformed)


def test_clickhouse_q66_rewrite_rejects_missing_group_by():
    bench = _bench()
    malformed = (
        "SELECT w_warehouse_name, SUM(jan_sales) AS jan_sales "
        "FROM (SELECT 1 AS jan_sales UNION ALL SELECT 2 AS jan_sales) AS x"
    )
    with pytest.raises(ValueError, match="missing outer"):
        bench._rewrite_clickhouse_q66(malformed)


def test_base_tpcds_queries_retain_original_dsqgen_shapes():
    bench = _bench()

    for seed in SEEDS:
        q47 = bench.get_query(47, seed=seed)
        q57 = bench.get_query(57, seed=seed)
        q66 = bench.get_query(66, seed=seed)

        assert "AVG(SUM(" in q47, f"Base Q47 seed {seed} must retain nested aggregate shape"
        assert "AVG(SUM(" in q57, f"Base Q57 seed {seed} must retain nested aggregate shape"
        assert "SUM(jan_sales)" in q66, f"Base Q66 seed {seed} must retain original aggregate-alias shape"
        assert "SUM(jan_net)" in q66, f"Base Q66 seed {seed} must retain original aggregate-alias shape"
