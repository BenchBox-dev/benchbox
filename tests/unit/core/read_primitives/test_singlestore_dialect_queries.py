import re

import pytest

from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark
from benchbox.core.read_primitives.catalog import load_primitives_catalog
from benchbox.core.read_primitives.variant_contracts import collect_variant_contract_issues, extract_projection_names

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SINGLESTORE_VARIANTS = (
    "approx_quantiles_array",
    "approx_top_k_lineitem",
    "array_agg_distinct",
    "array_contains",
    "array_distinct",
    "array_length",
    "array_min_max",
    "array_of_struct",
    "array_slice",
    "array_sort",
    "array_unnest",
    "asof_join_basic",
    "json_aggregates",
    "json_extract_nested",
    "list_filter",
    "list_reduce",
    "list_transform",
    "map_access",
    "map_construction",
    "map_keys_values",
    "max_by_complex",
    "max_by_simple",
    "max_by_with_ties",
    "min_by_complex",
    "min_by_simple",
    "min_by_with_ties",
    "olap_rollup_analysis",
    "optimizer_scalar_subquery_flattening",
    "orderby_all_desc",
    "orderby_all_simple",
    "qualify_cume_dist",
    "statistical_correlation",
    "struct_access",
    "struct_construction",
    "timeseries_trend_analysis",
    "unpivot_basic",
    "window_moving_frame",
    "window_multiple_orderings",
)
UNSUPPORTED_ON_SINGLESTORE = (
    "fulltext_simple_search",
    "fulltext_boolean_search",
    "fulltext_phrase_search",
)
SYNTAX_SINGLESTORE_REJECTS = {
    "ARG_MAX": r"\b(ARG_MAX|ARG_MIN|MAX_BY|MIN_BY)\s*\(",
    "CUME_DIST": r"\bCUME_DIST\s*\(",
    "ORDER BY ALL": r"\bORDER\s+BY\s+ALL\b",
    "QUALIFY": r"\bQUALIFY\b",
    "UNPIVOT": r"\bUNPIVOT\b",
    "ASOF JOIN": r"\bASOF\s+JOIN\b",
    "MATCH AGAINST modes": r"\bIN\s+(NATURAL\s+LANGUAGE|BOOLEAN)\s+MODE\b",
    "lambda arrow": r"->",
    "ROLLUP": r"\bROLLUP\b",
    "CORR/REGR": r"\b(CORR|COVAR_POP|COVAR_SAMP|REGR_[A-Z0-9]+)\s*\(",
    "DATE literal": r"\bDATE\s+'",
    "INTERVAL RANGE frame": r"RANGE\s+BETWEEN\s+INTERVAL",
    "array/struct/map constructors": r"\b(STRUCT|ARRAY_AGG|MAP_FROM_ENTRIES|UNNEST|APPROX_QUANTILE|APPROX_TOP_K)\s*\(",
}


@pytest.fixture(scope="module")
def catalog():
    return load_primitives_catalog()


@pytest.fixture(scope="module")
def singlestore_queries() -> dict[str, str]:
    return ReadPrimitivesBenchmark(scale_factor=0.01).get_queries(dialect="singlestore")


@pytest.mark.parametrize("query_id", UNSUPPORTED_ON_SINGLESTORE)
def test_full_text_search_without_a_fulltext_index_is_excluded(catalog, singlestore_queries, query_id):
    assert query_id not in singlestore_queries
    assert "singlestore" in catalog.queries[query_id].skip_on
    assert "FULLTEXT index" in catalog.queries[query_id].description


def test_exactly_the_queries_singlestore_cannot_run_are_skipped(catalog):
    skipped = sorted(query_id for query_id, entry in catalog.queries.items() if "singlestore" in (entry.skip_on or ()))

    assert skipped == sorted(UNSUPPORTED_ON_SINGLESTORE)


def test_singlestore_variant_set_is_exactly_the_covered_queries(catalog):
    covered = sorted(query_id for query_id, entry in catalog.queries.items() if "singlestore" in (entry.variants or {}))

    assert covered == sorted(SINGLESTORE_VARIANTS)


@pytest.mark.parametrize("query_id", SINGLESTORE_VARIANTS)
def test_singlestore_variant_is_served_for_the_singlestore_dialect(catalog, singlestore_queries, query_id):
    assert singlestore_queries[query_id] == catalog.queries[query_id].variants["singlestore"]
    assert singlestore_queries[query_id] != catalog.queries[query_id].sql


@pytest.mark.parametrize("query_id", SINGLESTORE_VARIANTS)
def test_singlestore_variant_projects_the_contract_columns(catalog, query_id):
    entry = catalog.queries[query_id]
    expected = tuple(column.name for column in entry.result_contract.columns)

    assert extract_projection_names(entry.variants["singlestore"], dialect="singlestore") == expected


@pytest.mark.parametrize("query_id", SINGLESTORE_VARIANTS)
@pytest.mark.parametrize("construct", sorted(SYNTAX_SINGLESTORE_REJECTS))
def test_singlestore_variant_avoids_syntax_singlestore_rejects(catalog, query_id, construct):
    sql = catalog.queries[query_id].variants["singlestore"]

    assert re.search(SYNTAX_SINGLESTORE_REJECTS[construct], sql, flags=re.IGNORECASE) is None


def test_singlestore_variants_satisfy_the_variant_contract_check(catalog):
    issues = [issue for issue in collect_variant_contract_issues(catalog) if issue.dialect == "singlestore"]

    assert issues == []


@pytest.mark.parametrize("query_id", ("json_aggregates", "map_construction", "map_access"))
def test_singlestore_map_variants_cannot_truncate(query_id):
    from benchbox.core.read_primitives.catalog import load_primitives_catalog

    sql = load_primitives_catalog().queries[query_id].variants["singlestore"].upper()

    assert "GROUP_CONCAT" not in sql
