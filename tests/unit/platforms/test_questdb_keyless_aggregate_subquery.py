from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_scalar_subquery_next_to_keyless_aggregates_is_cross_joined_to_a_staged_aggregate():
    sql = "SELECT 'n' AS name, COUNT(*) AS m, (SELECT COUNT(*) FROM t WHERE a = 1) AS c FROM u WHERE u.x > 1"
    assert rewrite_query(sql) == (
        "SELECT 'n' AS name, _ag._ag1 AS m, _ka1._ka1_v AS c "
        "FROM (SELECT COUNT(*) AS _ag1 FROM u WHERE u.x > 1) AS _ag "
        "CROSS JOIN (SELECT COUNT(*) AS _ka1_v FROM t WHERE a = 1) AS _ka1"
    )


def test_staged_aggregate_keeps_its_single_row_when_the_source_is_empty():
    sql = "SELECT COUNT(*) AS m, (SELECT COUNT(*) FROM t) AS c FROM u WHERE 1 = 0"
    rewritten = rewrite_query(sql)
    assert "FROM (SELECT COUNT(*) AS _ag1 FROM u WHERE 1 = 0) AS _ag" in rewritten
    assert "MAX(" not in rewritten


def test_aggregates_inside_expressions_and_joins_move_into_the_staged_select():
    sql = (
        "SELECT COUNT(DISTINCT k) AS m, SUM(a) / COUNT(*) AS r, (SELECT COUNT(*) FROM t) + (SELECT COUNT(*) FROM v) AS c "
        "FROM u JOIN w ON u.k = w.k WHERE u.x > 1"
    )
    assert rewrite_query(sql) == (
        "SELECT _ag._ag1 AS m, _ag._ag2 / _ag._ag3 AS r, _ka1._ka1_v + _ka2._ka2_v AS c "
        "FROM (SELECT COUNT(DISTINCT k) AS _ag1, SUM(a) AS _ag2, COUNT(*) AS _ag3 FROM u JOIN w ON u.k = w.k "
        "WHERE u.x > 1) AS _ag CROSS JOIN (SELECT COUNT(*) AS _ka1_v FROM t) AS _ka1 "
        "CROSS JOIN (SELECT COUNT(*) AS _ka2_v FROM v) AS _ka2"
    )


def test_subquery_in_each_union_branch_is_rewritten_in_its_own_select():
    sql = (
        "SELECT 'a' AS n, COUNT(*) AS m, (SELECT COUNT(*) FROM t) AS c FROM u "
        "UNION ALL SELECT 'b' AS n, COUNT(*) AS m, (SELECT COUNT(*) FROM v) AS c FROM w"
    )
    rewritten = rewrite_query(sql)
    assert rewritten.count("CROSS JOIN") == 2
    assert rewritten.count("_ag.") == 2
    assert "_ka1" in rewritten and "_ka2" in rewritten


def test_correlated_subquery_is_left_alone():
    sql = "SELECT COUNT(*) AS m, (SELECT MAX(b) FROM v WHERE v.k = u.k) AS c FROM u"
    assert rewrite_query(sql) == sql


def test_subquery_that_may_return_many_rows_is_left_alone():
    sql = "SELECT COUNT(*) AS m, (SELECT a FROM t WHERE id = 5) AS c FROM u"
    assert rewrite_query(sql) == sql


def test_subquery_without_a_sibling_aggregate_is_left_to_other_rewrites():
    sql = "SELECT a, (SELECT COUNT(*) FROM t) AS c FROM u"
    assert "_ka" not in rewrite_query(sql)


def test_grouped_select_is_left_alone():
    sql = "SELECT k, COUNT(*) AS m, (SELECT COUNT(*) FROM t) AS c FROM u GROUP BY k"
    assert "_ag" not in rewrite_query(sql)


def test_unaliased_items_keep_their_original_output_names():
    sql = "SELECT AVG(x), (SELECT COUNT(*) FROM t) FROM u"
    assert rewrite_query(sql) == (
        'SELECT _ag._ag1 AS "AVG(x)", _ka1._ka1_v AS "(SELECT COUNT(*) FROM t)" '
        "FROM (SELECT AVG(x) AS _ag1 FROM u) AS _ag CROSS JOIN (SELECT COUNT(*) AS _ka1_v FROM t) AS _ka1"
    )
