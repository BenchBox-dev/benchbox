from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]

DERIVED = "LEFT JOIN (SELECT AVG(q) AS _col_0, k AS _u_2 FROM l AS l2 GROUP BY k) AS _u_0 ON _u_0._u_2 = l1.k"


def test_decorrelated_column_in_a_grouped_select_is_wrapped_in_max():
    sql = f"SELECT l1.k AS k, SUM(l1.q) AS s, _u_0._col_0 AS avg_qty FROM l AS l1 {DERIVED} GROUP BY l1.k"
    assert rewrite_query(sql) == (
        f"SELECT l1.k AS k, SUM(l1.q) AS s, MAX(_u_0._col_0) AS avg_qty FROM l AS l1 {DERIVED} GROUP BY l1.k"
    )


def test_decorrelated_column_inside_an_expression_and_order_by_is_wrapped():
    sql = f"SELECT l1.k AS k, COALESCE(_u_0._col_0, 0) AS c FROM l AS l1 {DERIVED} GROUP BY l1.k ORDER BY _u_0._col_0"
    assert rewrite_query(sql) == (
        f"SELECT l1.k AS k, COALESCE(MAX(_u_0._col_0), 0) AS c FROM l AS l1 {DERIVED} "
        "GROUP BY l1.k ORDER BY MAX(_u_0._col_0)"
    )


def test_decorrelated_column_already_inside_an_aggregate_is_untouched():
    sql = f"SELECT l1.k AS k, SUM(_u_0._col_0) AS s FROM l AS l1 {DERIVED} GROUP BY l1.k"
    assert rewrite_query(sql) == sql


def test_decorrelated_column_that_is_a_group_key_is_untouched():
    sql = f"SELECT _u_0._col_0 AS v, COUNT(*) AS n FROM l AS l1 {DERIVED} GROUP BY _u_0._col_0"
    assert rewrite_query(sql) == sql


def test_select_without_group_by_is_untouched():
    sql = f"SELECT l1.k, _u_0._col_0 FROM l AS l1 {DERIVED}"
    assert rewrite_query(sql) == sql


def test_ordinary_derived_table_column_is_untouched():
    sql = "SELECT l1.k, d.c FROM l AS l1 LEFT JOIN (SELECT k, COUNT(*) AS c FROM l GROUP BY k) AS d ON d.k = l1.k GROUP BY l1.k, d.c"
    assert rewrite_query(sql) == sql


def test_decorrelated_column_is_left_alone_when_the_join_key_is_not_grouped():
    sql = f"SELECT l1.j AS j, _u_0._col_0 AS avg_qty FROM l AS l1 {DERIVED} GROUP BY l1.j"
    assert rewrite_query(sql) == sql


def test_decorrelated_column_is_left_alone_when_the_join_is_not_a_plain_key_equality():
    sql = (
        "SELECT l1.k AS k, _u_0._col_0 AS avg_qty FROM l AS l1 "
        "LEFT JOIN (SELECT AVG(q) AS _col_0, k AS _u_2 FROM l AS l2 GROUP BY k) AS _u_0 ON _u_0._u_2 < l1.k "
        "GROUP BY l1.k"
    )
    assert rewrite_query(sql) == sql


def test_decorrelated_column_in_having_is_wrapped():
    sql = f"SELECT l1.k AS k, COUNT(*) AS n FROM l AS l1 {DERIVED} GROUP BY l1.k HAVING COUNT(*) > _u_0._col_0"
    assert rewrite_query(sql) == (
        f"SELECT l1.k AS k, COUNT(*) AS n FROM l AS l1 {DERIVED} GROUP BY l1.k HAVING COUNT(*) > MAX(_u_0._col_0)"
    )
