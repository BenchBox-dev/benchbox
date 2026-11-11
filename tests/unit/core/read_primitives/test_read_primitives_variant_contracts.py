from __future__ import annotations

import pytest

from benchbox.core.read_primitives.catalog import (
    PrimitiveCatalog,
    PrimitiveQuery,
    ResultColumnContract,
    ResultContract,
    load_primitives_catalog,
)
from benchbox.core.read_primitives.variant_contracts import (
    VariantContractIssue,
    collect_variant_contract_issues,
    extract_projection_names,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_extract_projection_names_resolves_simple_select_star_wrapper():
    sql = """
    SELECT *
    FROM (
      SELECT
        l_orderkey,
        l_partkey,
        DENSE_RANK() OVER (PARTITION BY l_orderkey ORDER BY l_extendedprice DESC) AS price_rank
      FROM lineitem
    ) t
    ORDER BY l_orderkey, price_rank
    """

    assert extract_projection_names(sql, dialect="clickhouse") == ("l_orderkey", "l_partkey", "price_rank")


def test_extract_projection_names_keeps_unrelated_select_star_as_unknown():
    sql = """
    SELECT *
    FROM orders
    WHERE EXISTS (
      SELECT 1
      FROM lineitem
      WHERE l_orderkey = o_orderkey
    )
    """

    assert extract_projection_names(sql, dialect="duckdb") is None


def test_linter_detects_variant_column_mismatch():
    catalog = PrimitiveCatalog(
        version=1,
        queries={
            "fulltext_boolean_search": PrimitiveQuery(
                id="fulltext_boolean_search",
                category="fulltext",
                sql="SELECT c_custkey, c_name, relevance_score FROM customer",
                variants={"duckdb": "SELECT c_custkey, c_name FROM customer"},
                result_contract=ResultContract(
                    columns=(
                        ResultColumnContract("c_custkey"),
                        ResultColumnContract("c_name"),
                        ResultColumnContract("relevance_score"),
                    ),
                    capability="fulltext_boolean_rank",
                ),
            )
        },
    )

    assert collect_variant_contract_issues(catalog, require_contracts=True) == [
        VariantContractIssue(
            query_id="fulltext_boolean_search",
            dialect="duckdb",
            kind="column_mismatch",
            detail=(
                "result_contract columns ('c_custkey', 'c_name', 'relevance_score') "
                "!= variant columns ('c_custkey', 'c_name')"
            ),
        )
    ]


def test_linter_compares_variants_to_declared_contract_columns():
    catalog = PrimitiveCatalog(
        version=1,
        queries={
            "array_agg_simple": PrimitiveQuery(
                id="array_agg_simple",
                category="array",
                sql="SELECT ps_suppkey, ARRAY_AGG(ps_partkey) AS raw_parts FROM partsupp",
                variants={"clickhouse": "SELECT ps_suppkey, groupArray(ps_partkey) AS supplied_parts FROM partsupp"},
                result_contract=ResultContract(
                    columns=(
                        ResultColumnContract("ps_suppkey"),
                        ResultColumnContract("supplied_parts", type_class="array"),
                    ),
                    capability="ordered_array_aggregation",
                ),
            )
        },
    )

    assert collect_variant_contract_issues(catalog, require_contracts=True) == [
        VariantContractIssue(
            query_id="array_agg_simple",
            dialect=None,
            kind="contract_column_mismatch",
            detail="base columns ('ps_suppkey', 'raw_parts') != result_contract columns ('ps_suppkey', 'supplied_parts')",
        )
    ]


def test_linter_requires_contract_for_any_variant_query():
    catalog = PrimitiveCatalog(
        version=1,
        queries={
            "orderby_all_simple": PrimitiveQuery(
                id="orderby_all_simple",
                category="orderby",
                sql="SELECT r_name, n_name, COUNT(*) AS supplier_count FROM supplier GROUP BY r_name, n_name",
                variants={
                    "datafusion": (
                        "SELECT r_name, n_name, COUNT(*) AS supplier_count "
                        "FROM supplier GROUP BY r_name, n_name ORDER BY r_name, n_name, supplier_count"
                    )
                },
            )
        },
    )

    assert collect_variant_contract_issues(catalog, require_contracts=True) == [
        VariantContractIssue(
            query_id="orderby_all_simple",
            dialect=None,
            kind="missing_result_contract",
            detail="Variant-bearing query must declare result_contract",
        )
    ]


def test_linter_detects_contract_column_mismatch_and_missing_capability():
    catalog = PrimitiveCatalog(
        version=1,
        queries={
            "array_agg_simple": PrimitiveQuery(
                id="array_agg_simple",
                category="array",
                sql="SELECT ps_suppkey, ARRAY_AGG(ps_partkey) AS supplied_parts FROM partsupp",
                variants={"clickhouse": "SELECT ps_suppkey, groupArray(ps_partkey) AS supplied_parts FROM partsupp"},
                result_contract=ResultContract(
                    columns=(
                        ResultColumnContract("ps_suppkey"),
                        ResultColumnContract("parts", type_class="array"),
                    ),
                ),
            )
        },
    )

    issues = collect_variant_contract_issues(catalog, require_contracts=True)

    assert issues == [
        VariantContractIssue(
            query_id="array_agg_simple",
            dialect=None,
            kind="contract_column_mismatch",
            detail="base columns ('ps_suppkey', 'supplied_parts') != result_contract columns ('ps_suppkey', 'parts')",
        ),
        VariantContractIssue(
            query_id="array_agg_simple",
            dialect=None,
            kind="missing_capability",
            detail="result_contract must declare measured capability",
        ),
        VariantContractIssue(
            query_id="array_agg_simple",
            dialect="clickhouse",
            kind="column_mismatch",
            detail="result_contract columns ('ps_suppkey', 'parts') != variant columns ('ps_suppkey', 'supplied_parts')",
        ),
    ]


def test_actual_catalog_static_linter_reports_no_variant_contract_issues():
    issues = collect_variant_contract_issues(load_primitives_catalog(), require_contracts=True)
    allowlisted = {
        VariantContractIssue(
            query_id="approx_quantile_groupby",
            dialect="redshift",
            kind="variant_parse_error",
            detail="Could not parse variant projection columns",
        ),
    }
    assert set(issues) - allowlisted == set()


def test_summarize_variant_comparability_is_json_serializable():
    import json

    from benchbox.core.read_primitives.variant_contracts import summarize_variant_comparability

    summary = summarize_variant_comparability()
    assert summary["total_queries"] > 0
    assert summary["issue_count"] == 0
    assert summary["comparable"] is True
    assert json.loads(json.dumps(summary)) == summary


