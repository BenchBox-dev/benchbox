from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_filtered_sum_guards_its_argument_with_case():
    sql = "SELECT SUM(a) FILTER(WHERE a > 1) AS s FROM t"
    assert rewrite_query(sql) == "SELECT SUM(CASE WHEN a > 1 THEN a END) AS s FROM t"


def test_filtered_count_star_counts_a_guarded_constant():
    sql = "SELECT COUNT(*) FILTER(WHERE b) AS c FROM t"
    assert rewrite_query(sql) == "SELECT COUNT(CASE WHEN b THEN 1 END) AS c FROM t"


def test_filtered_distinct_count_guards_the_distinct_argument():
    sql = "SELECT COUNT(DISTINCT x) FILTER(WHERE b) AS d FROM t"
    assert rewrite_query(sql) == "SELECT COUNT(DISTINCT CASE WHEN b THEN x END) AS d FROM t"


def test_filtered_average_keeps_the_expression():
    sql = "SELECT AVG(a * 2) FILTER(WHERE a > 1) FROM t"
    assert rewrite_query(sql) == "SELECT AVG(CASE WHEN a > 1 THEN a * 2 END) FROM t"


def test_aggregate_without_filter_is_untouched():
    sql = "SELECT SUM(a) FROM t"
    assert rewrite_query(sql) == sql
