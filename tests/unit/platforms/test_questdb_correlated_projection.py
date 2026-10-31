from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_correlated_aggregate_in_a_grouped_select_becomes_a_left_joined_group_by():
    sql = (
        "SELECT l1.k AS k, SUM(l1.q) AS s, "
        "(SELECT AVG(l2.q) AS c FROM l AS l2 WHERE l2.d <= 5 AND l2.k = l1.k) AS avg_q "
        "FROM l AS l1 GROUP BY l1.k"
    )
    assert rewrite_query(sql) == (
        "SELECT l1.k AS k, SUM(l1.q) AS s, MAX(_cs1._cv) AS avg_q FROM l AS l1 "
        "LEFT JOIN (SELECT AVG(l2.q) AS _cv, l2.k AS _ck0 FROM l AS l2 WHERE l2.d <= 5 GROUP BY l2.k) AS _cs1 "
        "ON _cs1._ck0 = l1.k GROUP BY l1.k"
    )


def test_count_defaults_to_zero_when_no_group_matches():
    sql = "SELECT s.a, (SELECT COUNT(*) FROM t WHERE t.a = s.a) AS n FROM (SELECT DISTINCT a FROM z) AS s"
    assert rewrite_query(sql) == (
        "SELECT s.a, COALESCE(_cs1._cv, 0) AS n FROM (SELECT DISTINCT a FROM z) AS s "
        "LEFT JOIN (SELECT COUNT(*) AS _cv, t.a AS _ck0 FROM t GROUP BY t.a) AS _cs1 ON _cs1._ck0 = s.a"
    )


def test_identical_subqueries_share_one_join_including_order_by():
    sql = (
        "SELECT l1.k AS k, (SELECT SUM(l2.q) FROM l AS l2 WHERE l2.k = l1.k) AS r FROM l AS l1 "
        "GROUP BY l1.k ORDER BY (SELECT SUM(l2.q) FROM l AS l2 WHERE l2.k = l1.k) DESC"
    )
    rewritten = rewrite_query(sql)
    assert rewritten.count("LEFT JOIN") == 1
    assert rewritten.endswith("ORDER BY MAX(_cs1._cv) DESC")


def test_correlation_inside_a_derived_source_is_staged_into_the_derived_select():
    sql = (
        "SELECT a.y AS y, (SELECT SUM(s.v) FROM (SELECT t.v AS v FROM t WHERE t.y = a.y) AS s) AS total "
        "FROM (SELECT DISTINCT y FROM z) AS a"
    )
    assert rewrite_query(sql) == (
        "SELECT a.y AS y, _cs1._cv AS total FROM (SELECT DISTINCT y FROM z) AS a "
        "LEFT JOIN (SELECT SUM(s.v) AS _cv, s._ck0 FROM (SELECT t.v AS v, t.y AS _ck0 FROM t) AS s GROUP BY s._ck0) "
        "AS _cs1 ON _cs1._ck0 = a.y"
    )


def test_non_equality_correlation_is_left_alone():
    sql = "SELECT l1.k, (SELECT SUM(l2.q) FROM l AS l2 WHERE l2.k < l1.k) AS r FROM l AS l1"
    assert rewrite_query(sql) == sql


def test_correlation_on_a_column_that_is_not_grouped_is_left_alone():
    sql = "SELECT l1.k, SUM(l1.q), (SELECT SUM(l2.q) FROM l AS l2 WHERE l2.j = l1.j) AS r FROM l AS l1 GROUP BY l1.k"
    assert rewrite_query(sql) == sql


def test_count_nested_in_an_expression_is_left_alone():
    sql = "SELECT l1.k, (SELECT COUNT(*) + 1 FROM l AS l2 WHERE l2.k = l1.k) AS r FROM l AS l1"
    assert rewrite_query(sql) == sql


def test_uncorrelated_subquery_is_left_alone():
    sql = "SELECT l1.k, (SELECT SUM(l2.q) FROM l AS l2 WHERE l2.j = 1) AS r FROM l AS l1"
    assert rewrite_query(sql) == sql


def test_correlation_inside_an_aggregating_derived_source_is_left_alone():
    sql = (
        "SELECT a.y AS y, (SELECT SUM(s.v) FROM (SELECT COUNT(*) AS v FROM t WHERE t.y = a.y) AS s) AS total "
        "FROM (SELECT DISTINCT y FROM z) AS a"
    )
    assert rewrite_query(sql) == sql
