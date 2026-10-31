from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query as rewrite_pipeline, rewrite_with_modules

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def rewrite_query(sql: str) -> str:
    return rewrite_with_modules(sql, "scalar_subquery_projection")


def test_scalar_subquery_next_to_keyless_aggregates_is_left_alone():
    sql = "SELECT 'n' AS name, (SELECT COUNT(*) FROM t WHERE a = 1) AS c, COUNT(*) AS m FROM u"
    assert rewrite_query(sql) == sql


def test_scalar_subquery_in_a_grouped_select_becomes_a_cross_joined_derived_table():
    sql = "SELECT k, COUNT(*) AS m, (SELECT COUNT(*) FROM t) AS c FROM u GROUP BY k"
    assert rewrite_query(sql) == (
        "SELECT k, COUNT(*) AS m, _qs1._qs1_v AS c FROM u CROSS JOIN (SELECT COUNT(*) AS _qs1_v FROM t) AS _qs1 "
        "GROUP BY k"
    )


def test_projection_without_from_takes_the_derived_table_as_its_source():
    sql = "SELECT x.c FROM (SELECT (SELECT COUNT(*) FROM t) AS c, (SELECT MAX(a) FROM t) AS d) AS x"
    assert rewrite_query(sql) == (
        "SELECT x.c FROM (SELECT _qs1._qs1_v AS c, _qs2._qs2_v AS d FROM (SELECT COUNT(*) AS _qs1_v FROM t) AS _qs1 "
        "CROSS JOIN (SELECT MAX(a) AS _qs2_v FROM t) AS _qs2) AS x"
    )


def test_subquery_inside_an_aggregate_argument_stays_a_plain_column():
    sql = "SELECT SUM(a * (SELECT MAX(b) FROM t)) FROM u"
    assert (
        rewrite_query(sql) == "SELECT SUM(a * _qs1._qs1_v) FROM u CROSS JOIN (SELECT MAX(b) AS _qs1_v FROM t) AS _qs1"
    )


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
        "SELECT a, SUM(b) OVER (PARTITION BY a) AS s, _qs1._qs1_v AS m FROM u CROSS JOIN (SELECT MAX(c) AS _qs1_v FROM t) AS _qs1"
    )


def test_constant_subquery_without_from_is_moved():
    sql = "SELECT (SELECT 1) AS x FROM u"
    assert rewrite_query(sql) == "SELECT _qs1._qs1_v AS x FROM u CROSS JOIN (SELECT 1 AS _qs1_v) AS _qs1"


def test_outer_column_named_v_does_not_collide_with_the_moved_value():
    sql = "SELECT v, (SELECT COUNT(*) FROM t) AS c FROM u"
    assert rewrite_query(sql) == (
        "SELECT v, _qs1._qs1_v AS c FROM u CROSS JOIN (SELECT COUNT(*) AS _qs1_v FROM t) AS _qs1"
    )


def test_subquery_whose_projection_holds_a_nested_aggregate_subquery_is_not_a_single_row_aggregate():
    sql = "SELECT (SELECT (SELECT MAX(b) FROM v WHERE v.k = t.k) FROM t) AS x FROM u"
    assert rewrite_query(sql) == sql


def test_outer_reference_through_the_base_name_of_an_aliased_inner_table_is_correlated():
    sql = "SELECT (SELECT MAX(t.b) FROM t AS t2 WHERE t2.k = t.k) AS m FROM t"
    assert rewrite_query(sql) == sql


def test_full_pipeline_moves_the_subquery_of_a_grouped_select():
    sql = "SELECT k, COUNT(*) AS m, (SELECT COUNT(*) FROM t) AS c FROM u GROUP BY k"
    assert rewrite_pipeline(sql) == (
        "SELECT k, COUNT(*) AS m, _qs1._qs1_v AS c FROM u CROSS JOIN (SELECT COUNT(*) AS _qs1_v FROM t) AS _qs1 "
        "GROUP BY k"
    )
