from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_derived_table_column_list_becomes_projection_aliases():
    sql = (
        "SELECT c_count, COUNT(*) AS custdist FROM (SELECT c_custkey, COUNT(o_orderkey) FROM customer "
        "GROUP BY c_custkey) AS c_orders(c_custkey, c_count) GROUP BY c_count"
    )
    assert rewrite_query(sql) == (
        "SELECT c_count, COUNT(*) AS custdist FROM (SELECT c_custkey AS c_custkey, COUNT(o_orderkey) AS c_count "
        "FROM customer GROUP BY c_custkey) AS c_orders GROUP BY c_count"
    )


def test_derived_table_column_list_replaces_existing_aliases():
    sql = "SELECT a FROM (SELECT x AS p, y AS q FROM t) AS d(a, b)"
    assert rewrite_query(sql) == "SELECT a FROM (SELECT x AS a, y AS b FROM t) AS d"


def test_derived_table_with_star_projection_is_left_alone():
    sql = "SELECT a FROM (SELECT * FROM t) AS d(a, b)"
    assert rewrite_query(sql) == sql


def test_derived_table_with_mismatched_column_count_is_left_alone():
    sql = "SELECT a FROM (SELECT x, y FROM t) AS d(a)"
    assert rewrite_query(sql) == sql
