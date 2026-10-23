from __future__ import annotations

import re

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

SEEDS = (1, 2, 3)


def _bench():
    from benchbox.core.tpcds.benchmark.runner import TPCDSBenchmark

    return TPCDSBenchmark()


def _extract_v2_suffix(query: str) -> str:
    _, suffix = query.split(", v2 AS ", 1)
    return suffix


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
    with pytest.raises(ValueError, match="expected 3 correlated EXISTS rewrites"):
        _bench()._apply_target_dialect_overrides(35, "SELECT * FROM store_sales", "clickhouse")


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
    with pytest.raises(ValueError, match="expected 3 correlated EXISTS rewrites"):
        _bench()._apply_target_dialect_overrides(10, "SELECT * FROM store_sales", "clickhouse")


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


def test_clickhouse_q47_q57_alias_qualified_rank_neighbor_projections_across_seeds():
    bench = _bench()

    for seed in SEEDS:
        for query_id in (47, 57):
            base = bench.get_query(query_id, seed=seed)
            clickhouse = bench.get_query(query_id, seed=seed, dialect="clickhouse")

            assert "AVG(SUM(" in base
            assert "AVG(SUM(" in clickhouse
            v2 = _extract_v2_suffix(clickhouse)
            assert "v1.d_year AS d_year" in v2, f"ClickHouse Q{query_id} seed {seed} must alias v1.d_year"
            assert "v1.sum_sales AS sum_sales" in v2
            assert "enable_analyzer = 0" not in clickhouse


def test_clickhouse_q66_qualifies_alias_shadowed_columns_across_seeds():
    bench = _bench()

    for seed in SEEDS:
        base = bench.get_query(66, seed=seed)
        clickhouse = bench.get_query(66, seed=seed, dialect="clickhouse")
        base_carriers, base_time_windows = _extract_q66_filters(base)

        assert "SUM(jan_sales)" in base
        assert "SUM(jan_sales)" not in clickhouse
        assert "SUM(x.jan_sales)" in clickhouse
        assert "SUM(x.jan_sales / w_warehouse_sq_ft)" in clickhouse
        assert _extract_order_limit(base) == _extract_order_limit(clickhouse)
        for carrier_filter in base_carriers:
            assert carrier_filter in clickhouse
        for time_window in base_time_windows:
            assert time_window in clickhouse
        assert "enable_analyzer = 0" not in clickhouse


def test_non_clickhouse_q47_q57_q66_are_not_aliased():
    bench = _bench()

    assert "v1.d_year AS d_year" not in bench.get_query(47, seed=1, dialect="duckdb")
    assert "SUM(x.jan_sales)" not in bench.get_query(66, seed=1, dialect="duckdb")
