from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_scalar_subquery_in_an_aggregate_projection_becomes_a_cross_joined_derived_table():
    sql = "SELECT 'n' AS name, (SELECT COUNT(*) FROM t WHERE a = 1) AS c, COUNT(*) AS m FROM u"
    assert rewrite_query(sql) == (
        "SELECT 'n' AS name, MAX(_qs1.v) AS c, COUNT(*) AS m FROM u "
        "CROSS JOIN (SELECT COUNT(*) AS v FROM t WHERE a = 1) AS _qs1"
    )


def test_projection_without_from_takes_the_derived_table_as_its_source():
    sql = "SELECT x.c FROM (SELECT (SELECT COUNT(*) FROM t) AS c, (SELECT MAX(a) FROM t) AS d) AS x"
    assert rewrite_query(sql) == (
        "SELECT x.c FROM (SELECT _qs1.v AS c, _qs2.v AS d FROM (SELECT COUNT(*) AS v FROM t) AS _qs1 "
        "CROSS JOIN (SELECT MAX(a) AS v FROM t) AS _qs2) AS x"
    )


def test_subquery_inside_an_aggregate_argument_stays_a_plain_column():
    sql = "SELECT SUM(a * (SELECT MAX(b) FROM t)) FROM u"
    assert rewrite_query(sql) == "SELECT SUM(a * _qs1.v) FROM u CROSS JOIN (SELECT MAX(b) AS v FROM t) AS _qs1"


def test_correlated_subquery_is_left_alone():
    sql = "SELECT a, (SELECT MAX(b) FROM v WHERE v.k = u.k) AS m FROM u"
    assert rewrite_query(sql) == sql


def test_subquery_in_where_is_left_alone():
    sql = "SELECT a FROM u WHERE a > (SELECT AVG(b) FROM t)"
    assert rewrite_query(sql) == sql


def test_in_and_exists_subqueries_are_left_alone():
    sql = "SELECT a IN (SELECT b FROM t) AS x, EXISTS(SELECT 1 FROM t) AS y FROM u"
    assert rewrite_query(sql) == sql


def test_subquery_that_may_return_no_row_or_many_is_left_alone():
    sql = "SELECT (SELECT a FROM t WHERE id = 5) AS x FROM u"
    assert rewrite_query(sql) == sql


def test_grouped_or_having_subquery_is_left_alone():
    grouped = "SELECT (SELECT COUNT(*) FROM t GROUP BY k LIMIT 1) AS x FROM u"
    filtered = "SELECT (SELECT COUNT(*) FROM t HAVING COUNT(*) > 5) AS x FROM u"
    assert rewrite_query(grouped) == grouped
    assert rewrite_query(filtered) == filtered


def test_window_function_does_not_make_the_select_an_aggregate_query():
    sql = "SELECT a, SUM(b) OVER (PARTITION BY a) AS s, (SELECT MAX(c) FROM t) AS m FROM u"
    assert rewrite_query(sql) == (
        "SELECT a, SUM(b) OVER (PARTITION BY a) AS s, _qs1.v AS m FROM u CROSS JOIN (SELECT MAX(c) AS v FROM t) AS _qs1"
    )


def test_constant_subquery_without_from_is_moved():
    sql = "SELECT (SELECT 1) AS x FROM u"
    assert rewrite_query(sql) == "SELECT _qs1.v AS x FROM u CROSS JOIN (SELECT 1 AS v) AS _qs1"
