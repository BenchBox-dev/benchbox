from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SCHEMA = {
    "lineitem": frozenset({"l_partkey", "l_quantity"}),
    "part": frozenset({"p_partkey", "p_brand"}),
}


def columns_of(table: str):
    return SCHEMA.get(table)


def test_shared_join_key_is_factored_out_of_or_branches():
    sql = (
        "SELECT 1 FROM lineitem, part WHERE (p_partkey = l_partkey AND p_brand = 'A' AND l_quantity < 5) "
        "OR (p_partkey = l_partkey AND p_brand = 'B' AND l_quantity > 9)"
    )
    assert rewrite_query(sql, columns_of) == (
        "SELECT 1 FROM lineitem JOIN part ON p_partkey = l_partkey "
        "WHERE ((p_brand = 'A' AND l_quantity < 5) OR (p_brand = 'B' AND l_quantity > 9))"
    )


def test_factoring_without_schema_leaves_the_join_key_in_where():
    sql = "SELECT 1 FROM lineitem CROSS JOIN part WHERE (a.x = b.x AND a.y = 1) OR (a.x = b.x AND a.y = 2)"
    assert rewrite_query(sql) == "SELECT 1 FROM lineitem CROSS JOIN part WHERE a.x = b.x AND (a.y = 1 OR a.y = 2)"


def test_branch_made_only_of_the_shared_key_collapses_the_disjunction():
    sql = "SELECT 1 FROM t WHERE (a.x = b.x) OR (a.x = b.x AND a.y = 2)"
    assert rewrite_query(sql) == "SELECT 1 FROM t WHERE a.x = b.x"


def test_or_without_a_common_join_key_is_untouched():
    sql = "SELECT 1 FROM t WHERE (a.x = b.x AND a.y = 1) OR (a.z = b.z AND a.y = 2)"
    assert rewrite_query(sql) == sql


def test_shared_non_join_conjunct_is_not_factored():
    sql = "SELECT 1 FROM t WHERE (a.y = 1 AND a.z = 2) OR (a.y = 1 AND a.z = 3)"
    assert rewrite_query(sql) == sql
