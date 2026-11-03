from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_group_by_all_groups_on_non_aggregate_positions():
    sql = "SELECT a, b, SUM(c) AS total, AVG(d) FROM t GROUP BY ALL"
    assert rewrite_query(sql) == "SELECT a, b, SUM(c) AS total, AVG(d) FROM t GROUP BY 1, 2"


def test_group_by_all_keeps_computed_projection_as_a_group_key():
    sql = "SELECT a + 1 AS k, COUNT(*) AS n FROM t GROUP BY ALL ORDER BY k"
    assert rewrite_query(sql) == "SELECT a + 1 AS k, COUNT(*) AS n FROM t GROUP BY 1 ORDER BY k"


def test_order_by_all_orders_by_every_projection():
    sql = "SELECT a, b, COUNT(*) AS n FROM t GROUP BY a, b ORDER BY ALL"
    assert rewrite_query(sql) == "SELECT a, b, COUNT(*) AS n FROM t GROUP BY a, b ORDER BY a, b, n"


def test_order_by_all_desc_applies_direction_to_every_position():
    sql = "SELECT a, AVG(b) AS m FROM t GROUP BY a, b ORDER BY ALL DESC LIMIT 100"
    assert rewrite_query(sql) == "SELECT a, AVG(b) AS m FROM t GROUP BY a, b ORDER BY a DESC, m DESC LIMIT 100"


def test_order_by_all_with_translated_null_ordering_is_expanded_per_projection():
    sql = "SELECT a, COUNT(*) AS n FROM t GROUP BY a ORDER BY CASE WHEN ALL IS NULL THEN 1 ELSE 0 END, ALL"
    assert rewrite_query(sql) == (
        "SELECT a, COUNT(*) AS n FROM t GROUP BY a "
        "ORDER BY CASE WHEN a IS NULL THEN 1 ELSE 0 END, a, CASE WHEN COUNT(*) IS NULL THEN 1 ELSE 0 END, n"
    )


def test_by_all_inside_a_subquery_is_expanded():
    sql = "SELECT * FROM (SELECT a, COUNT(*) AS n FROM t GROUP BY ALL) AS s"
    assert rewrite_query(sql) == "SELECT * FROM (SELECT a, COUNT(*) AS n FROM t GROUP BY 1) AS s"


def test_union_all_and_explicit_keys_are_untouched():
    sql = "SELECT a FROM t UNION ALL SELECT a FROM u ORDER BY a"
    assert rewrite_query(sql) == sql
