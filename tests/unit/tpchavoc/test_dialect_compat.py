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
    GROUP_BY_EMPTY_VARIANT_IDS,
    POSTGRES_ALIAS_VARIANT_IDS,
    POSTGRES_DECORRELATED_VARIANT_IDS,
    POSTGRES_DUAL_VARIANT_IDS,
    POSTGRES_QUALIFIED_COLUMNS,
    SINGLESTORE_AGGREGATE_SUBQUERY_VARIANT_IDS,
    SINGLESTORE_ALIAS_QUALIFIED_COLUMNS,
    SINGLESTORE_CORRELATED_GROUP_VARIANTS,
    SINGLESTORE_FILTER_VARIANT_IDS,
    SINGLESTORE_GROUPING_SET_VARIANT_IDS,
    SINGLESTORE_ORDER_SUBQUERY_VARIANT_IDS,
    SINGLESTORE_SOURCELESS_WHERE_VARIANT_IDS,
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
        decorrelated_rules = {
            key[3]
            for key, _ in REGISTRY.all_rules()
            if key[:3] == (Phase.QUERY_ADAPTER, platform, "tpchavoc") and key[3] in POSTGRES_DECORRELATED_VARIANT_IDS
        }
        assert decorrelated_rules == POSTGRES_DECORRELATED_VARIANT_IDS
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


@pytest.mark.parametrize("query_id", sorted(POSTGRES_DECORRELATED_VARIANT_IDS))
def test_postgres_excess_inventory_variants_join_a_grouped_lineitem_subquery(query_id):
    query = TPCHavocBenchmark(scale_factor=0.1).get_query(query_id, dialect="postgres")
    tree = sqlglot.parse_one(query, read="postgres")

    assert query_id not in POSTGRES_TPCHAVOC_SKIPS
    lineitem_aggregates = [
        select
        for select in tree.find_all(exp.Select)
        if any(projection.find(exp.Sum) for projection in select.expressions)
    ]
    assert len(lineitem_aggregates) == 1
    assert {column.table for column in lineitem_aggregates[0].args["where"].find_all(exp.Column)} == {"lineitem"}
    assert "GROUP BY lineitem.l_partkey, lineitem.l_suppkey" in query


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


def test_doris_variants_run_without_filter_clause_or_empty_group_by():
    from benchbox.core.tpchavoc.dialect_compat import DORIS_FILTER_VARIANT_IDS, GROUP_BY_EMPTY_VARIANT_IDS

    benchmark = TPCHavocBenchmark(scale_factor=0.1)
    queries = benchmark.get_queries(dialect="doris")

    for query_id in DORIS_FILTER_VARIANT_IDS:
        tree = sqlglot.parse_one(queries[query_id], read="doris")
        assert not list(tree.find_all(exp.Filter)), query_id
    for query_id in GROUP_BY_EMPTY_VARIANT_IDS:
        assert "GROUP BY ()" not in queries[query_id].upper()
        assert "HAVING" in queries[query_id].upper()


def test_doris_filter_variants_are_the_variants_that_use_filter():
    from benchbox.core.tpchavoc.dialect_compat import DORIS_FILTER_VARIANT_IDS

    benchmark = TPCHavocBenchmark(scale_factor=0.1)
    uses_filter = {
        query_id
        for query_id, query in benchmark.get_queries(dialect="duckdb").items()
        if list(sqlglot.parse_one(query, read="duckdb").find_all(exp.Filter))
    }

    assert uses_filter >= DORIS_FILTER_VARIANT_IDS


def test_doris_rewrites_are_registered_per_variant():
    import benchbox.sql_compat.rules.query_adapter.doris_tpchavoc_rewrites
    from benchbox.core.tpchavoc.dialect_compat import (
        DORIS_CORRELATED_GROUP_VARIANTS,
        DORIS_FILTER_VARIANT_IDS,
        DORIS_NAMED_WINDOW_VARIANT_IDS,
        DORIS_ORDER_BY_PROJECTION_VARIANT_IDS,
        GROUP_BY_EMPTY_VARIANT_IDS,
    )
    from benchbox.sql_compat.context import Phase
    from benchbox.sql_compat.registry import REGISTRY

    registered = {key[3] for key, _ in REGISTRY.all_rules() if key[:3] == (Phase.QUERY_ADAPTER, "doris", "tpchavoc")}

    assert registered == set(DORIS_FILTER_VARIANT_IDS) | set(GROUP_BY_EMPTY_VARIANT_IDS) | set(
        DORIS_NAMED_WINDOW_VARIANT_IDS
    ) | set(DORIS_ORDER_BY_PROJECTION_VARIANT_IDS) | set(DORIS_CORRELATED_GROUP_VARIANTS)


def _doris_tree(query_id):
    return sqlglot.parse_one(TPCHavocBenchmark(scale_factor=0.01).get_query(query_id, dialect="doris"), read="doris")


def test_doris_named_window_variant_inlines_the_window_definition():
    tree = _doris_tree("1_v8")

    assert tree.args.get("windows") is None
    assert not [window for window in tree.find_all(exp.Window) if window.args.get("alias") is not None]
    assert "WINDOW" not in tree.sql(dialect="doris").upper()


@pytest.mark.parametrize("query_id", ["3_v1", "3_v9", "5_v1", "10_v1", "16_v1"])
def test_doris_order_by_references_the_projected_alias(query_id):
    tree = _doris_tree(query_id)
    order = tree.args["order"]

    assert order.find(exp.Subquery) is None
    assert order.find(exp.Window) is None


def test_doris_correlated_scalar_group_by_is_a_single_group_guarded_by_case():
    tree = _doris_tree("2_v2")
    scalar = next(
        subquery.this
        for subquery in tree.find_all(exp.Subquery)
        if isinstance(subquery.parent, exp.Binary) and isinstance(subquery.this, exp.Select)
    )

    assert scalar.args.get("group") is None
    assert scalar.args.get("having") is None
    assert scalar.find(exp.Case) is not None


def _singlestore_query(query_id):
    return TPCHavocBenchmark(scale_factor=0.01).get_query(query_id, dialect="singlestore")


def _singlestore_tree(query_id):
    return sqlglot.parse_one(_singlestore_query(query_id), read="singlestore")


@pytest.mark.parametrize("query_id", sorted(SINGLESTORE_FILTER_VARIANT_IDS))
def test_singlestore_filter_variants_use_conditional_aggregates(query_id):
    query = _singlestore_query(query_id)

    assert "FILTER(" not in query.upper().replace(" ", "")
    assert _singlestore_tree(query_id)


@pytest.mark.parametrize("query_id", sorted(GROUP_BY_EMPTY_VARIANT_IDS))
def test_singlestore_empty_group_by_is_dropped_and_having_kept(query_id):
    query = _singlestore_query(query_id)

    assert "GROUP BY ()" not in query
    assert " HAVING " in query


def test_singlestore_single_grouping_set_becomes_plain_group_by():
    (query_id,) = SINGLESTORE_GROUPING_SET_VARIANT_IDS
    query = _singlestore_query(query_id)

    assert "GROUPING SETS" not in query
    assert "GROUP BY `l_returnflag`, `l_linestatus` HAVING" in query


@pytest.mark.parametrize("query_id", sorted(SINGLESTORE_SOURCELESS_WHERE_VARIANT_IDS))
def test_singlestore_filtered_selects_without_source_read_from_dual(query_id):
    tree = _singlestore_tree(query_id)

    for select in tree.find_all(exp.Select):
        if select.args.get("where") is not None:
            assert select.args.get("from_") is not None
    assert "FROM DUAL WHERE" in _singlestore_query(query_id)


@pytest.mark.parametrize(("query_id", "columns"), sorted(SINGLESTORE_ALIAS_QUALIFIED_COLUMNS.items()))
def test_singlestore_aggregated_columns_are_qualified_against_same_named_alias(query_id, columns):
    query = _singlestore_query(query_id)

    for column, table in columns.items():
        assert f"SUM({table}.`{column}`)" in query
        assert f"ORDER BY {table}." not in query


@pytest.mark.parametrize("query_id", sorted(SINGLESTORE_ORDER_SUBQUERY_VARIANT_IDS))
def test_singlestore_order_by_on_subquery_alias_runs_in_enclosing_query(query_id):
    tree = _singlestore_tree(query_id)

    assert tree.args.get("order") is not None
    assert isinstance(tree.args["from_"].this, exp.Subquery)
    inner = tree.args["from_"].this.this
    assert inner.args.get("order") is None
    assert inner.find(exp.Subquery) is not None


@pytest.mark.parametrize("query_id", sorted(SINGLESTORE_AGGREGATE_SUBQUERY_VARIANT_IDS))
def test_singlestore_aggregates_do_not_contain_subqueries(query_id):
    tree = _singlestore_tree(query_id)

    for aggregate in tree.find_all(exp.AggFunc):
        assert aggregate.find(exp.Subquery, exp.Exists) is None
        assert not any(node.args.get("query") is not None for node in aggregate.find_all(exp.In))


def test_singlestore_correlated_scalar_subquery_drops_group_by():
    for query_id, columns in SINGLESTORE_CORRELATED_GROUP_VARIANTS.items():
        tree = _singlestore_tree(query_id)
        scalar = next(node for node in tree.find_all(exp.Subquery) if isinstance(node.parent, exp.EQ))

        assert scalar.this.args.get("group") is None
        assert scalar.this.args.get("having") is not None
        assert columns


def test_singlestore_variant_rules_are_registered():
    import benchbox.sql_compat.rules.query_adapter.singlestore_tpchavoc_rewrites
    from benchbox.sql_compat.context import Phase
    from benchbox.sql_compat.registry import REGISTRY

    expected = (
        SINGLESTORE_FILTER_VARIANT_IDS
        | GROUP_BY_EMPTY_VARIANT_IDS
        | SINGLESTORE_GROUPING_SET_VARIANT_IDS
        | SINGLESTORE_SOURCELESS_WHERE_VARIANT_IDS
        | set(SINGLESTORE_ALIAS_QUALIFIED_COLUMNS)
        | SINGLESTORE_ORDER_SUBQUERY_VARIANT_IDS
        | SINGLESTORE_AGGREGATE_SUBQUERY_VARIANT_IDS
        | set(SINGLESTORE_CORRELATED_GROUP_VARIANTS)
    )
    registered = {
        key[3] for key, _ in REGISTRY.all_rules() if key[:3] == (Phase.QUERY_ADAPTER, "singlestore", "tpchavoc")
    }

    assert registered == expected


def test_hoist_aggregate_subqueries_rejects_qualified_multi_source_references():
    from benchbox.utils.dialect_utils import apply_rewrite

    with pytest.raises(ValueError, match="qualified column references"):
        apply_rewrite(
            "hoist_aggregate_subqueries",
            "SELECT SUM(CASE WHEN EXISTS(SELECT 1 FROM u WHERE u.k = a.k) THEN a.v ELSE 0 END) FROM a, b",
            "singlestore",
        )


def test_wrap_order_by_subquery_aliases_keeps_limit_outside():
    from benchbox.utils.dialect_utils import apply_rewrite

    rewritten = apply_rewrite(
        "wrap_order_by_subquery_aliases",
        "SELECT k, (SELECT SUM(v) FROM u WHERE u.k = t.k) AS total FROM t ORDER BY total DESC LIMIT 5",
        "singlestore",
    )

    assert rewritten.endswith("AS ordered_rows ORDER BY total DESC LIMIT 5")


def test_singlestore_rewrites_resolve_without_conflict_for_every_registered_variant():
    import benchbox.sql_compat.rules.query_adapter.singlestore_tpchavoc_rewrites
    from benchbox.sql_compat.context import CompatibilityContext, Phase
    from benchbox.sql_compat.registry import REGISTRY

    query_ids = {
        key[3] for key, _ in REGISTRY.all_rules() if key[:3] == (Phase.QUERY_ADAPTER, "singlestore", "tpchavoc")
    }

    assert {"4_v7", "16_v7", "17_v7"} <= query_ids
    for query_id in query_ids:
        decision = REGISTRY.resolve(
            CompatibilityContext(
                platform="singlestore",
                platform_version=None,
                benchmark="tpchavoc",
                query_id=query_id,
                phase=Phase.QUERY_ADAPTER,
                mode="sql",
                dialect="singlestore",
            )
        )
        assert decision is not None, query_id
