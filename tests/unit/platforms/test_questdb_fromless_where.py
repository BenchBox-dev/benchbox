from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_exists_over_a_fromless_filter_becomes_its_condition():
    sql = "SELECT a FROM t WHERE EXISTS(SELECT 1 AS \"1\" WHERE t.m IN ('A', 'B'))"
    assert rewrite_query(sql) == "SELECT a FROM t WHERE COALESCE(t.m IN ('A', 'B'), FALSE)"


def test_not_exists_over_a_fromless_filter_keeps_null_conditions_false_before_negation():
    sql = "SELECT a FROM t WHERE NOT EXISTS(SELECT 1 WHERE t.c LIKE '%x%')"
    assert rewrite_query(sql) == "SELECT a FROM t WHERE NOT COALESCE(t.c LIKE '%x%', FALSE)"


def test_fromless_filter_branch_of_a_union_reads_from_a_one_row_sequence():
    sql = "SELECT 'a' AS c, 1 AS n UNION ALL SELECT 'b', 2 WHERE FALSE"
    assert rewrite_query(sql) == ("SELECT 'a' AS c, 1 AS n UNION ALL SELECT 'b', 2 FROM LONG_SEQUENCE(1) WHERE FALSE")


def test_select_with_a_source_is_left_alone():
    sql = "SELECT a FROM t WHERE EXISTS(SELECT 1 FROM u WHERE u.k = 1)"
    assert rewrite_query(sql) == sql


def test_fromless_select_without_a_filter_is_left_alone():
    sql = "SELECT (SELECT 1) AS x FROM t WHERE a = 1"
    assert "LONG_SEQUENCE" not in rewrite_query(sql)
