from __future__ import annotations

import pytest

from benchbox._vendor import sqlglot
from benchbox._vendor.sqlglot import exp
from benchbox.core.dryrun import DryRunExecutor
from benchbox.core.schemas import BenchmarkConfig
from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark
from benchbox.core.tpchavoc.dialect_compat import (
    CLICKHOUSE_COALESCED_COUNT_SUBQUERY_VARIANT_IDS,
    CLICKHOUSE_FILTER_VARIANT_IDS,
    CLICKHOUSE_OUTER_ALIAS_VARIANTS,
    DATAFUSION_EMPTY_GROUP_VARIANT_IDS,
    POSTGRES_ALIAS_VARIANT_IDS,
    POSTGRES_DUAL_VARIANT_IDS,
    POSTGRES_QUALIFIED_COLUMNS,
)
from benchbox.platforms.clickhouse_local import ClickHouseLocalAdapter
from benchbox.platforms.datafusion import DataFusionAdapter
from benchbox.sql_compat.rules.execution_filter.clickhouse_tpchavoc import CLICKHOUSE_TPCHAVOC_SKIPS
from benchbox.sql_compat.rules.execution_filter.datafusion_tpchavoc import DATAFUSION_TPCHAVOC_SKIPS
from benchbox.sql_compat.rules.execution_filter.postgres_tpchavoc import POSTGRES_TPCHAVOC_SKIPS

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def test_postgres_alias_variants_inline_having_and_where_references():
    benchmark = TPCHavocBenchmark(scale_factor=0.1)

    for query_id in POSTGRES_ALIAS_VARIANT_IDS:
        query = benchmark.get_query(query_id, dialect="postgres")
        tree = sqlglot.parse_one(query, read="postgres")

        assert query_id not in POSTGRES_TPCHAVOC_SKIPS
        for select in tree.find_all(exp.Select):
            aliases = {projection.alias for projection in select.expressions if isinstance(projection, exp.Alias)}
            for clause_name in ("having", "where"):
                clause = select.args.get(clause_name)
                if clause is None:
                    continue
                assert (
                    not {
                        column.name
                        for column in clause.find_all(exp.Column)
                        if not column.table and column.find_ancestor(exp.Select) is select
                    }
                    & aliases
                )


def test_postgres_alias_rules_cover_each_family_platform():
    import benchbox.sql_compat.rules.query_adapter.postgres_tpchavoc_rewrites
    from benchbox.sql_compat.context import Phase
    from benchbox.sql_compat.registry import REGISTRY

    for platform in ("pg-duckdb", "pg-mooncake", "timescaledb"):
        alias_rules = {
            key[3]
            for key, _ in REGISTRY.all_rules()
            if key[:3] == (Phase.QUERY_ADAPTER, platform, "tpchavoc") and key[3] in POSTGRES_ALIAS_VARIANT_IDS
        }
        qualified_rules = {
            key[3]
            for key, _ in REGISTRY.all_rules()
            if key[:3] == (Phase.QUERY_ADAPTER, platform, "tpchavoc") and key[3] in POSTGRES_QUALIFIED_COLUMNS
        }
        dual_rules = {
            key[3]
            for key, _ in REGISTRY.all_rules()
            if key[:3] == (Phase.QUERY_ADAPTER, platform, "tpchavoc") and key[3] in POSTGRES_DUAL_VARIANT_IDS
        }
        assert alias_rules == POSTGRES_ALIAS_VARIANT_IDS
        assert qualified_rules == set(POSTGRES_QUALIFIED_COLUMNS)
        assert dual_rules == POSTGRES_DUAL_VARIANT_IDS


def test_postgres_ambiguous_columns_are_qualified():
    benchmark = TPCHavocBenchmark(scale_factor=0.1)

    for query_id, expected in POSTGRES_QUALIFIED_COLUMNS.items():
        query = benchmark.get_query(query_id, dialect="postgres")
        tree = sqlglot.parse_one(query, read="postgres")

        assert query_id not in POSTGRES_TPCHAVOC_SKIPS
        assert not {column.name for column in tree.find_all(exp.Column) if not column.table and column.name in expected}
        for column_name, table in expected.items():
            assert any(column.name == column_name and column.table == table for column in tree.find_all(exp.Column))


def test_postgres_dual_variant_drops_the_one_row_source():
    query = TPCHavocBenchmark(scale_factor=0.1).get_query("17_v4", dialect="postgres")

    assert "17_v4" not in POSTGRES_TPCHAVOC_SKIPS
    assert "VALUES" not in query.upper()
    assert "dual" not in query
    assert "SELECT 0 AS revenue WHERE FALSE" in query
    assert sqlglot.parse_one(query, read="postgres")


def test_postgres_alias_rewrite_preserves_order_by_aliases():
    query = TPCHavocBenchmark(scale_factor=0.1).get_query("3_v7", dialect="postgres")

    assert "HAVING SUM(" in query
    assert "ORDER BY revenue DESC" in query


def test_clickhouse_filtered_aggregates_use_native_combinators():
    benchmark = TPCHavocBenchmark(scale_factor=0.1)

    for query_id in CLICKHOUSE_FILTER_VARIANT_IDS:
        query = benchmark.get_query(query_id, dialect="clickhouse")

        assert " FILTER(" not in query
        assert query_id not in CLICKHOUSE_TPCHAVOC_SKIPS
        assert sqlglot.parse_one(query, read="clickhouse")

    q1 = benchmark.get_query("1_v6", dialect="clickhouse")
    q12 = benchmark.get_query("12_v7", dialect="clickhouse")
    assert "sumOrNullIf(" in q1
    assert "avgOrNullIf(" in q1
    assert "countIf(" in q1
    assert q12.count("countIf(") == 2
    assert "sumOrNullIf(" in benchmark.get_query("9_v7", dialect="clickhouse")


@pytest.mark.parametrize("aggregate", ["COUNT(l_orderkey)", "MIN(l_orderkey)", "MAX(l_orderkey)"])
def test_clickhouse_filter_rewrite_handles_every_aggregate_shape(aggregate):
    from benchbox.utils.dialect_utils import apply_rewrite

    rewritten = apply_rewrite(
        "filtered_aggregates_to_conditional",
        f"SELECT {aggregate} FILTER (WHERE l_orderkey > 0) FROM lineitem",
        "clickhouse",
    )

    assert "FILTER" not in rewritten.upper()
    assert "l_orderkey > 0" in rewritten


def test_clickhouse_filter_rules_cover_each_deployment_mode():
    import benchbox.sql_compat.rules.query_adapter.clickhouse_tpchavoc_rewrites
    from benchbox.sql_compat.context import Phase
    from benchbox.sql_compat.registry import REGISTRY

    for platform in ("clickhouse-local", "clickhouse-server", "clickhouse-cloud"):
        registered = {
            key[3]
            for key, _ in REGISTRY.all_rules()
            if key[:3] == (Phase.QUERY_ADAPTER, platform, "tpchavoc") and key[3] in CLICKHOUSE_FILTER_VARIANT_IDS
        }
        assert registered == CLICKHOUSE_FILTER_VARIANT_IDS


@pytest.mark.parametrize(("query_id", "outer_alias"), [("2_v8", "partsupp_outer"), ("10_v8", "lineitem_outer")])
def test_clickhouse_exists_variants_alias_the_outer_table(query_id, outer_alias):
    table = CLICKHOUSE_OUTER_ALIAS_VARIANTS[query_id][0]
    query = TPCHavocBenchmark(scale_factor=0.1).get_query(query_id, dialect="clickhouse")

    assert f"{table} AS {outer_alias}" in query
    assert f"{table}." not in query
    assert f"{outer_alias}." in query
    assert sqlglot.parse_one(query, read="clickhouse")


def test_alias_outer_table_references_leaves_inner_aliased_table_untouched():
    from benchbox.utils.dialect_utils import apply_rewrite

    rewritten = apply_rewrite(
        "alias_outer_table_references",
        "SELECT a FROM t WHERE NOT EXISTS (SELECT 1 FROM t t2 WHERE t2.k = t.k AND t2.v < t.v)",
        "clickhouse",
        table="t",
        alias="t_outer",
    )

    assert rewritten == (
        "SELECT a FROM t AS t_outer WHERE NOT EXISTS(SELECT 1 FROM t AS t2 WHERE t2.k = t_outer.k AND t2.v < t_outer.v)"
    )


def test_alias_outer_table_references_ignores_queries_without_the_outer_table():
    from benchbox.utils.dialect_utils import apply_rewrite

    rewritten = apply_rewrite(
        "alias_outer_table_references", "SELECT a FROM u", "clickhouse", table="t", alias="t_outer"
    )

    assert rewritten == "SELECT a FROM u"


def test_clickhouse_correlated_count_subquery_is_coalesced_to_zero():
    query = TPCHavocBenchmark(scale_factor=0.1).get_query("13_v1", dialect="clickhouse")

    assert "COALESCE((SELECT COUNT(o2.o_orderkey)" in query
    assert sqlglot.parse_one(query, read="clickhouse")


def test_clickhouse_variant_rules_cover_each_deployment_mode():
    import benchbox.sql_compat.rules.query_adapter.clickhouse_tpchavoc_rewrites
    from benchbox.sql_compat.context import Phase
    from benchbox.sql_compat.registry import REGISTRY

    expected = set(CLICKHOUSE_OUTER_ALIAS_VARIANTS) | CLICKHOUSE_COALESCED_COUNT_SUBQUERY_VARIANT_IDS
    for platform in ("clickhouse-local", "clickhouse-server", "clickhouse-cloud"):
        registered = {
            key[3]
            for key, _ in REGISTRY.all_rules()
            if key[:3] == (Phase.QUERY_ADAPTER, platform, "tpchavoc") and key[3] in expected
        }
        assert registered == expected


def test_datafusion_empty_group_variants_drop_grouping_and_unskip():
    benchmark = TPCHavocBenchmark(scale_factor=0.1)

    for query_id in DATAFUSION_EMPTY_GROUP_VARIANT_IDS:
        query = benchmark.get_query(query_id, dialect="datafusion")

        assert "GROUP BY ()" not in query
        assert " HAVING " in query
        assert query_id not in DATAFUSION_TPCHAVOC_SKIPS
        assert sqlglot.parse_one(query, read="postgres")


def test_datafusion_empty_group_rules_cover_both_variants():
    import benchbox.sql_compat.rules.query_adapter.datafusion_tpchavoc_rewrites
    from benchbox.sql_compat.context import Phase
    from benchbox.sql_compat.registry import REGISTRY

    registered = {
        key[3]
        for key, _ in REGISTRY.all_rules()
        if key[:3] == (Phase.QUERY_ADAPTER, "datafusion", "tpchavoc") and key[3] in DATAFUSION_EMPTY_GROUP_VARIANT_IDS
    }
    assert registered == DATAFUSION_EMPTY_GROUP_VARIANT_IDS


@pytest.mark.parametrize(
    ("adapter_cls", "query_ids", "required_fragments", "forbidden_fragment"),
    [
        (
            DataFusionAdapter,
            ["6_v2", "14_v2"],
            [" HAVING "],
            "GROUP BY ()",
        ),
        (
            ClickHouseLocalAdapter,
            ["1_v6", "12_v7"],
            ["sumOrNullIf(", "countIf("],
            " FILTER(",
        ),
    ],
)
def test_dry_run_uses_production_dialect_rewrites_and_query_subset(
    chdb_probe_satisfied,
    adapter_cls,
    query_ids: list[str],
    required_fragments: list[str],
    forbidden_fragment: str,
):
    adapter = adapter_cls()
    benchmark = TPCHavocBenchmark(scale_factor=0.01)
    config = BenchmarkConfig(
        name="tpchavoc",
        display_name="TPC-Havoc",
        scale_factor=0.01,
        queries=query_ids,
        test_execution_type="power",
    )

    queries = DryRunExecutor()._extract_queries(
        benchmark,
        config,
        platform_adapter=adapter,
        execution_mode="sql",
    )

    assert list(queries) == query_ids
    rendered = "\n".join(queries.values())
    assert forbidden_fragment not in rendered
    assert all(fragment in rendered for fragment in required_fragments)
