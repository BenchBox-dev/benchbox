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


@pytest.fixture(scope="module")
def presto_queries():
    benchmark = TPCHavocBenchmark(scale_factor=0.1)
    return {query_id: benchmark.get_query(query_id, dialect="presto") for query_id in PRESTO_REWRITTEN_VARIANT_IDS}


def test_presto_alias_variants_inline_having_and_where_references(presto_queries):
    for query_id in PRESTO_ALIAS_VARIANT_IDS:
        tree = sqlglot.parse_one(presto_queries[query_id], read="presto")
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
                assert not referenced & aliases, query_id


def test_presto_window_aggregates_have_no_filter_clause(presto_queries):
    for query_id in PRESTO_WINDOW_FILTER_VARIANT_IDS:
        tree = sqlglot.parse_one(presto_queries[query_id], read="presto")
        windows = list(tree.find_all(exp.Window))
        assert windows, query_id
        assert not any(isinstance(window.this, exp.Filter) for window in windows), query_id


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) FILTER (WHERE x > 0) OVER (PARTITION BY k) FROM t",
        "SELECT SUM(DISTINCT y) FILTER (WHERE x > 0) OVER (PARTITION BY k) FROM t",
    ],
)
def test_presto_window_filter_rejects_star_and_distinct(sql):
    with pytest.raises(NotImplementedError, match=r"COUNT\(\*\) or DISTINCT"):
        _rewrite_window_filters(sql, "presto")


def test_presto_array_aggregation_uses_presto_functions(presto_queries):
    for query_id in PRESTO_ARRAY_AGGREGATION_VARIANT_IDS:
        query = presto_queries[query_id]
        assert "LIST" not in query.upper().replace("ARRAY_AGG", ""), query_id
        assert "CROSS JOIN UNNEST(quantities, prices, discounts, taxes)" in query, query_id
        assert "REDUCE(" not in query, query_id
        tree = sqlglot.parse_one(query, read="presto")
        assert [agg.sql(dialect="presto") for agg in tree.find_all(exp.ArrayAgg)] == [
            f'ARRAY_AGG("{column}" ORDER BY l_orderkey, l_linenumber)'
            for column in ("l_quantity", "l_extendedprice", "l_discount", "l_tax")
        ]


def test_presto_rules_cover_each_rewritten_variant():
    import benchbox.sql_compat.rules.query_adapter.presto_tpchavoc_rewrites
    from benchbox.sql_compat.context import Phase
    from benchbox.sql_compat.registry import REGISTRY

    registered = {key[3] for key, _ in REGISTRY.all_rules() if key[:3] == (Phase.QUERY_ADAPTER, "presto", "tpchavoc")}
    assert registered == PRESTO_REWRITTEN_VARIANT_IDS
