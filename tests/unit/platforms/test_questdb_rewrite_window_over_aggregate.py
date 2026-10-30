from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_window_over_aggregate_runs_above_the_aggregation():
    assert rewrite_query("SELECT a, SUM(x) / SUM(SUM(x)) OVER () AS share FROM t GROUP BY a ORDER BY share") == (
        "SELECT __g0 AS a, __a0 / SUM(__a0) OVER () AS share "
        "FROM (SELECT a AS __g0, SUM(x) AS __a0 FROM t GROUP BY a) AS __staged ORDER BY share"
    )


def test_cumulative_window_over_grouped_sum_partitions_by_group_column():
    rewritten = rewrite_query(
        "SELECT item, d, SUM(SUM(p)) OVER (PARTITION BY item ORDER BY d) AS c FROM w GROUP BY item, d"
    )
    assert rewritten == (
        "SELECT __g0 AS item, __g1 AS d, SUM(__a0) OVER (PARTITION BY __g0 ORDER BY __g1) AS c "
        "FROM (SELECT item AS __g0, d AS __g1, SUM(p) AS __a0 FROM w GROUP BY item, d) AS __staged"
    )


def test_window_over_group_column_only_is_staged():
    assert rewrite_query("SELECT a, RANK() OVER (ORDER BY a) AS r FROM t GROUP BY a").startswith(
        "SELECT __g0 AS a, RANK() OVER (ORDER BY __g0) AS r FROM (SELECT a AS __g0 FROM t GROUP BY a)"
    )


def test_ordinal_group_by_resolves_to_projection():
    rewritten = rewrite_query("SELECT a AS k, SUM(x), RANK() OVER (ORDER BY SUM(x)) AS r FROM t GROUP BY 1")
    assert "GROUP BY a)" in rewritten
    assert "__g0 AS k" in rewritten


def test_having_becomes_filter_below_the_window():
    assert rewrite_query("SELECT a, RANK() OVER (ORDER BY SUM(x)) AS r FROM t GROUP BY a HAVING SUM(x) > 1").endswith(
        "AS __staged_having WHERE __a0 > 1) AS __staged"
    )


def test_cte_with_window_over_aggregate_is_staged_in_place():
    rewritten = rewrite_query(
        "WITH v AS (SELECT a, SUM(SUM(x)) OVER (ORDER BY a) AS c FROM t GROUP BY a) SELECT * FROM v"
    )
    assert rewritten.startswith(
        "WITH v AS (SELECT __g0 AS a, SUM(__a0) OVER (ORDER BY __g0) AS c FROM (SELECT a AS __g0"
    )


def test_window_without_group_by_is_untouched():
    sql = "SELECT a, RANK() OVER (ORDER BY a) AS r FROM t"
    assert rewrite_query(sql) == sql


def test_group_by_without_window_is_untouched():
    sql = "SELECT a, SUM(x) FROM t GROUP BY a"
    assert rewrite_query(sql) == sql


def test_rollup_with_window_is_staged_once():
    rewritten = rewrite_query("SELECT a, SUM(x) AS s, RANK() OVER (ORDER BY SUM(x)) AS r FROM t GROUP BY ROLLUP (a)")
    assert rewritten.count("RANK()") == 1
    assert "UNION ALL" in rewritten
    assert rewritten.startswith("SELECT __g0 AS a, __a0 AS s, RANK() OVER (ORDER BY __a0) AS r FROM (")


def test_subquery_in_having_is_left_alone():
    rewritten = rewrite_query(
        "SELECT a, RANK() OVER (ORDER BY SUM(x)) AS r FROM t GROUP BY a HAVING SUM(x) > (SELECT AVG(y) FROM u)"
    )
    assert rewritten.endswith("WHERE __a0 > (SELECT AVG(y) FROM u)) AS __staged")
    assert "__a-1" not in rewritten


def test_window_pass_leaves_grouping_sets_the_first_pass_declined():
    sql = "SELECT *, RANK() OVER (ORDER BY a) AS r FROM t GROUP BY ROLLUP (a)"
    assert rewrite_query(sql) == sql


def test_group_by_projection_alias_is_resolved_before_staging():
    rewritten = rewrite_query("SELECT a + 1 AS k, SUM(x) AS s, RANK() OVER (ORDER BY SUM(x)) AS r FROM t GROUP BY k")
    assert "GROUP BY a + 1" in rewritten
    assert "SELECT k AS" not in rewritten
