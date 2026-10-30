import pytest

from benchbox.core.read_primitives.catalog import load_primitives_catalog
from benchbox.core.read_primitives.variant_contracts import collect_variant_contract_issues

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

DORIS_VARIANT_IDS = {
    "approx_quantile_groupby",
    "approx_top_k_lineitem",
    "json_aggregates",
    "statistical_percentiles",
    "approx_quantiles_array",
    "statistical_correlation",
    "optimizer_scalar_subquery_flattening",
    "groupby_all_simple",
    "groupby_all_complex",
    "orderby_all_simple",
    "orderby_all_desc",
    "array_agg_simple",
    "array_agg_distinct",
    "array_unnest",
    "array_slice",
    "struct_access",
    "array_of_struct",
    "map_construction",
    "map_access",
    "map_keys_values",
    "list_transform",
    "list_filter",
    "list_reduce",
    "unpivot_basic",
}

UNSUPPORTED_ON_DORIS = (
    "GROUP BY ALL",
    "ORDER BY ALL",
    "APPROX_QUANTILE(",
    "MAP_FROM_ENTRIES",
    "JSON_ARRAYAGG",
    "REGR_R2",
)


def test_doris_variants_cover_the_queries_doris_cannot_run_as_written():
    catalog = load_primitives_catalog()

    assert {qid for qid, entry in catalog.queries.items() if "doris" in (entry.variants or {})} == DORIS_VARIANT_IDS


def test_doris_variants_avoid_syntax_doris_rejects():
    catalog = load_primitives_catalog()

    for query_id in DORIS_VARIANT_IDS:
        sql = catalog.queries[query_id].variants["doris"].upper()
        for fragment in UNSUPPORTED_ON_DORIS:
            assert fragment not in sql, f"{query_id} uses {fragment}"


def test_doris_variants_keep_the_declared_result_columns():
    issues = [issue for issue in collect_variant_contract_issues(load_primitives_catalog()) if issue.dialect == "doris"]

    assert issues == []


def test_doris_is_not_skipped_for_queries_it_has_variants_for():
    catalog = load_primitives_catalog()

    for query_id in DORIS_VARIANT_IDS:
        assert "doris" not in (catalog.queries[query_id].skip_on or [])


def test_doris_array_of_struct_orders_the_collected_structs_explicitly():
    sql = load_primitives_catalog().queries["array_of_struct"].variants["doris"].upper()

    assert "ARRAY_SORTBY" in sql
    assert "ORDER BY O_ORDERKEY, L_LINENUMBER" not in sql
