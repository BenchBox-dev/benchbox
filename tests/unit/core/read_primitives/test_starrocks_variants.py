from __future__ import annotations

import pytest

from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark
from benchbox.core.read_primitives.catalog import load_primitives_catalog
from benchbox.core.read_primitives.variant_contracts import collect_variant_contract_issues

pytestmark = [pytest.mark.unit, pytest.mark.fast]

STARROCKS_VARIANT_IDS = {
    "approx_quantile_groupby",
    "approx_quantiles_array",
    "array_unnest",
    "asof_join_basic",
    "groupby_all_simple",
    "groupby_all_complex",
    "orderby_all_simple",
    "orderby_all_desc",
    "json_aggregates",
    "json_extract_simple",
    "json_extract_nested",
    "list_reduce",
    "map_construction",
    "map_access",
    "map_keys_values",
    "optimizer_scalar_subquery_flattening",
    "statistical_correlation",
    "statistical_percentiles",
    "struct_access",
    "timeseries_trend_analysis",
    "unpivot_basic",
    "window_moving_frame",
}
STARROCKS_SKIPPED_IDS = {"fulltext_simple_search", "fulltext_boolean_search", "fulltext_phrase_search"}
UNSUPPORTED_ON_STARROCKS = (
    "GROUP BY ALL",
    "ORDER BY ALL",
    "APPROX_QUANTILE(",
    "MAP_FROM_ENTRIES",
    "JSON_ARRAYAGG",
    "REGR_SLOPE",
    "REGR_R2",
    "PERCENTILE_CONT(0.",
    "JSON_VALID",
    "UNPIVOT (",
    "EXPLODE",
    "COLLECT_LIST",
)


def test_starrocks_variants_cover_the_queries_starrocks_cannot_run_as_written():
    catalog = load_primitives_catalog()
    assert {qid for qid, entry in catalog.queries.items() if "starrocks" in (entry.variants or {})} == (
        STARROCKS_VARIANT_IDS
    )


def test_starrocks_skips_the_fulltext_queries_it_cannot_express():
    catalog = load_primitives_catalog()
    assert {qid for qid, entry in catalog.queries.items() if "starrocks" in (entry.skip_on or [])} == (
        STARROCKS_SKIPPED_IDS
    )
    for query_id in STARROCKS_SKIPPED_IDS:
        assert "GIN index" in catalog.queries[query_id].description


def test_starrocks_variants_avoid_syntax_starrocks_rejects():
    catalog = load_primitives_catalog()
    for query_id in STARROCKS_VARIANT_IDS:
        sql = catalog.queries[query_id].variants["starrocks"].upper()
        for token in UNSUPPORTED_ON_STARROCKS:
            assert token not in sql, f"{query_id} uses {token}"


def test_starrocks_variants_keep_the_base_projection_contract():
    catalog = load_primitives_catalog()
    issues = [issue for issue in collect_variant_contract_issues(catalog) if issue.dialect == "starrocks"]
    assert issues == []


def test_qualify_queries_are_rewritten_to_a_filtered_subquery():
    queries = ReadPrimitivesBenchmark(scale_factor=0.01).get_queries(dialect="starrocks")
    for query_id in ("qualify_row_number", "qualify_ntile", "window_rank"):
        assert "QUALIFY" not in queries[query_id]
    assert not STARROCKS_SKIPPED_IDS & set(queries)
