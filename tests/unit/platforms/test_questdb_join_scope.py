from __future__ import annotations

from unittest.mock import Mock

import pytest

from benchbox.platforms.questdb import QuestDBAdapter
from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SCHEMA = {
    "customer": frozenset({"c_custkey", "c_nationkey"}),
    "orders": frozenset({"o_orderkey", "o_custkey"}),
    "lineitem": frozenset({"l_orderkey", "l_suppkey"}),
    "supplier": frozenset({"s_suppkey", "s_nationkey"}),
    "nation": frozenset({"n_nationkey", "n_name"}),
    "part": frozenset({"p_partkey", "p_size"}),
    "partsupp": frozenset({"ps_partkey", "ps_suppkey"}),
}


def columns_of(table: str):
    return SCHEMA.get(table)


def test_unqualified_predicates_follow_the_table_that_owns_them():
    scrambled = (
        "SELECT p_partkey FROM part JOIN supplier ON p_partkey = ps_partkey "
        "JOIN partsupp ON s_suppkey = ps_suppkey JOIN nation ON s_nationkey = n_nationkey WHERE p_size = 15"
    )
    assert rewrite_query(scrambled, columns_of) == (
        "SELECT p_partkey FROM part JOIN partsupp ON p_partkey = ps_partkey "
        "JOIN supplier ON s_suppkey = ps_suppkey JOIN nation ON s_nationkey = n_nationkey WHERE p_size = 15"
    )


def test_cross_join_with_a_linking_predicate_in_where_gets_the_predicate_as_on():
    scrambled = (
        "SELECT 1 FROM customer CROSS JOIN orders CROSS JOIN lineitem "
        "WHERE c_custkey = o_custkey AND l_orderkey = o_orderkey AND c_nationkey = 3"
    )
    assert rewrite_query(scrambled, columns_of) == (
        "SELECT 1 FROM customer JOIN orders ON c_custkey = o_custkey JOIN lineitem ON l_orderkey = o_orderkey "
        "WHERE c_nationkey = 3"
    )


def test_correctly_placed_joins_are_untouched():
    sql = "SELECT 1 FROM customer JOIN orders ON c_custkey = o_custkey JOIN lineitem ON l_orderkey = o_orderkey"
    assert rewrite_query(sql, columns_of) == sql


def test_correlated_predicate_moves_to_where_of_the_subquery():
    scrambled = (
        "SELECT 1 FROM part WHERE p_size = (SELECT COUNT(*) FROM partsupp "
        "JOIN supplier ON p_partkey = ps_partkey JOIN nation ON s_suppkey = ps_suppkey "
        "WHERE s_nationkey = n_nationkey)"
    )
    assert rewrite_query(scrambled, columns_of) == (
        "SELECT 1 FROM part WHERE p_size = (SELECT COUNT(*) FROM partsupp "
        "JOIN supplier ON s_suppkey = ps_suppkey JOIN nation ON s_nationkey = n_nationkey "
        "WHERE p_partkey = ps_partkey)"
    )


def test_join_order_follows_the_predicates_when_the_next_table_is_unconnected():
    scrambled = "SELECT 1 FROM customer JOIN lineitem ON c_custkey = o_custkey JOIN orders ON l_orderkey = o_orderkey"
    assert rewrite_query(scrambled, columns_of) == (
        "SELECT 1 FROM customer JOIN orders ON c_custkey = o_custkey JOIN lineitem ON l_orderkey = o_orderkey"
    )


def test_outer_joins_are_not_reordered():
    sql = (
        "SELECT 1 FROM customer LEFT JOIN lineitem ON c_custkey = o_custkey LEFT JOIN orders ON l_orderkey = o_orderkey"
    )
    assert rewrite_query(sql, columns_of) == sql


def test_unknown_table_leaves_the_query_alone():
    sql = "SELECT 1 FROM customer JOIN mystery ON c_custkey = m_custkey JOIN orders ON o_custkey = c_custkey"
    assert rewrite_query(sql, columns_of) == sql


def test_without_a_schema_the_query_is_returned_verbatim():
    sql = "SELECT 1 FROM part JOIN supplier ON p_partkey = ps_partkey JOIN partsupp ON s_suppkey = ps_suppkey"
    assert rewrite_query(sql) == sql


def test_adapter_resolves_columns_from_the_live_table_and_caches_them(monkeypatch):
    adapter = QuestDBAdapter()
    lookup = Mock(return_value=["P_PARTKEY", "p_size"])
    monkeypatch.setattr(adapter, "_get_table_columns", lookup)
    assert adapter._table_columns("part") == frozenset({"p_partkey", "p_size"})
    assert adapter._table_columns("part") == frozenset({"p_partkey", "p_size"})
    lookup.assert_called_once_with("part")


def test_adapter_does_not_cache_a_failed_lookup(monkeypatch):
    adapter = QuestDBAdapter()
    lookup = Mock(return_value=[])
    monkeypatch.setattr(adapter, "_get_table_columns", lookup)
    assert adapter._table_columns("part") is None
    assert adapter._table_columns("part") is None
    assert lookup.call_count == 2


def test_cross_join_linked_only_to_a_later_table_is_connected_by_its_predicate():
    scrambled = (
        "SELECT 1 FROM customer CROSS JOIN supplier CROSS JOIN orders "
        "WHERE o_custkey = c_custkey AND s_nationkey = o_orderkey AND o_orderkey = 1"
    )
    assert rewrite_query(scrambled, columns_of) == (
        "SELECT 1 FROM customer JOIN orders ON o_custkey = c_custkey JOIN supplier ON s_nationkey = o_orderkey "
        "WHERE o_orderkey = 1"
    )
