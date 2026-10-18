from __future__ import annotations

import pytest
import sqlglot
from sqlglot import exp

from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark
from benchbox.core.tpchavoc.dialect_compat import (
    PRESTO_ALIAS_VARIANT_IDS,
    PRESTO_ARRAY_AGGREGATION_VARIANT_IDS,
    PRESTO_WINDOW_FILTER_VARIANT_IDS,
    _rewrite_window_filters,
)

pytestmark = [pytest.mark.unit, pytest.mark.medium]

PRESTO_REWRITTEN_VARIANT_IDS = (
    PRESTO_ALIAS_VARIANT_IDS | PRESTO_WINDOW_FILTER_VARIANT_IDS | PRESTO_ARRAY_AGGREGATION_VARIANT_IDS
)


@pytest.fixture(scope="module", params=("presto", "trino", "athena"))
def presto_trino_queries(request):
    dialect = request.param
    benchmark = TPCHavocBenchmark(scale_factor=0.1)
    return dialect, {
        query_id: benchmark.get_query(query_id, dialect=dialect) for query_id in PRESTO_REWRITTEN_VARIANT_IDS
    }


def test_presto_alias_variants_inline_having_and_where_references(presto_trino_queries):
    dialect, queries = presto_trino_queries
    for query_id in PRESTO_ALIAS_VARIANT_IDS:
        tree = sqlglot.parse_one(queries[query_id], read=dialect)
        for select in tree.find_all(exp.Select):
            aliases = {projection.alias for projection in select.expressions if isinstance(projection, exp.Alias)}
            for clause_name in ("having", "where"):
                clause = select.args.get(clause_name)
                if clause is None:
                    continue
                referenced = {
                    column.name
                    for column in clause.find_all(exp.Column)
                    if not column.table and column.find_ancestor(exp.Select) is select
                }
                assert not referenced & aliases, (dialect, query_id)


def test_presto_window_aggregates_have_no_filter_clause(presto_trino_queries):
    dialect, queries = presto_trino_queries
    for query_id in PRESTO_WINDOW_FILTER_VARIANT_IDS:
        tree = sqlglot.parse_one(queries[query_id], read=dialect)
        windows = list(tree.find_all(exp.Window))
        assert windows, (dialect, query_id)
        assert not any(isinstance(window.this, exp.Filter) for window in windows), (dialect, query_id)


@pytest.mark.parametrize("dialect", ("presto", "trino", "athena"))
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) FILTER (WHERE x > 0) OVER (PARTITION BY k) FROM t",
        "SELECT SUM(DISTINCT y) FILTER (WHERE x > 0) OVER (PARTITION BY k) FROM t",
    ],
)
def test_presto_window_filter_rejects_star_and_distinct(dialect, sql):
    with pytest.raises(NotImplementedError, match=r"COUNT\(\*\) or DISTINCT"):
        _rewrite_window_filters(sql, dialect)


def test_presto_array_aggregation_uses_presto_functions(presto_trino_queries):
    dialect, queries = presto_trino_queries
    for query_id in PRESTO_ARRAY_AGGREGATION_VARIANT_IDS:
        query = queries[query_id]
        tree = sqlglot.parse_one(query, read=dialect)
        with_ = tree.find(exp.With)
        assert with_ is not None and with_.expressions[0].alias == "aggregated"
        arrays = list(with_.find_all(exp.ArrayAgg))
        assert len(arrays) == 4
        assert {aggregate.find(exp.Column).name for aggregate in arrays} == {
            "l_quantity",
            "l_extendedprice",
            "l_discount",
            "l_tax",
        }
        for aggregate in arrays:
            order = aggregate.find(exp.Order)
            assert order is not None
            assert [ordered.this.name for ordered in order.expressions] == ["l_orderkey", "l_linenumber"]
        unnest = tree.find(exp.Unnest)
        assert unnest is not None
        assert [column.name for column in unnest.expressions] == ["quantities", "prices", "discounts", "taxes"]
        assert unnest.alias_column_names == ["quantity", "price", "discount", "tax"]


@pytest.mark.parametrize("platform", ("presto", "trino"))
def test_presto_rules_cover_each_rewritten_variant(platform):
    import benchbox.sql_compat.rules.query_adapter.presto_tpchavoc_rewrites
    from benchbox.sql_compat.context import Phase
    from benchbox.sql_compat.registry import REGISTRY

    registered = {key[3] for key, _ in REGISTRY.all_rules() if key[:3] == (Phase.QUERY_ADAPTER, platform, "tpchavoc")}
    assert registered == PRESTO_REWRITTEN_VARIANT_IDS
