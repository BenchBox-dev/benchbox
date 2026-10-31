from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _levels(sql: str) -> list[str]:
    return rewrite_query(sql).split(" UNION ALL ")


def test_rollup_expands_to_one_select_per_level():
    rewritten = rewrite_query("SELECT a, b, SUM(x) AS s FROM t GROUP BY ROLLUP (a, b)")
    assert rewritten == (
        "SELECT __g0 AS a, __g1 AS b, __a0 AS s FROM ("
        "SELECT a AS __g0, b AS __g1, SUM(x) AS __a0 FROM t GROUP BY a, b "
        "UNION ALL SELECT a AS __g0, NULL AS __g1, SUM(x) AS __a0 FROM t GROUP BY a "
        "UNION ALL SELECT NULL AS __g0, NULL AS __g1, SUM(x) AS __a0 FROM t"
        ") AS __staged"
    )


def test_cube_expands_to_every_subset():
    assert len(_levels("SELECT a, b, SUM(x) FROM t GROUP BY CUBE (a, b)")) == 4


def test_grouping_sets_expand_to_listed_sets():
    levels = _levels("SELECT a, b, SUM(x) FROM t GROUP BY GROUPING SETS ((a, b), (a), ())")
    assert len(levels) == 3
    assert "GROUP BY a, b" in levels[0]
    assert levels[2].count("GROUP BY") == 0


def test_plain_group_columns_stay_in_every_level():
    levels = _levels("SELECT c, a, SUM(x) FROM t GROUP BY c, ROLLUP (a)")
    assert len(levels) == 2
    assert all("GROUP BY c" in level for level in levels)


def test_grouping_function_becomes_level_flag():
    rewritten = rewrite_query("SELECT a, b, SUM(x), GROUPING(a) + GROUPING(b) AS lvl FROM t GROUP BY ROLLUP (a, b)")
    assert "__f0 + __f1 AS lvl" in rewritten
    assert "GROUPING(" not in rewritten
    assert "0 AS __f0, 1 AS __f1" in rewritten


def test_window_over_rolled_up_aggregate_runs_after_union():
    rewritten = rewrite_query(
        "SELECT a, SUM(x), RANK() OVER (PARTITION BY GROUPING(a) ORDER BY SUM(x) DESC) AS r "
        "FROM t GROUP BY ROLLUP (a) ORDER BY r LIMIT 5"
    )
    assert rewritten.startswith(
        'SELECT __g0 AS a, __a0 AS "SUM(x)", RANK() OVER (PARTITION BY __f0 ORDER BY __a0 DESC) AS r FROM ('
    )
    assert rewritten.endswith("ORDER BY r LIMIT 5")


def test_having_becomes_outer_filter():
    assert rewrite_query("SELECT a, SUM(x) FROM t GROUP BY ROLLUP (a) HAVING SUM(x) > 1").endswith(
        ") AS __staged WHERE __a0 > 1"
    )


def test_with_clause_is_hoisted_above_the_union():
    rewritten = rewrite_query("WITH c AS (SELECT 1 AS x) SELECT a, SUM(x) FROM t GROUP BY ROLLUP (a)")
    assert rewritten.startswith("WITH c AS (SELECT 1 AS x) SELECT __g0 AS a")
    assert rewritten.count("WITH") == 1


def test_rollup_inside_subquery_is_expanded_in_place():
    rewritten = rewrite_query("SELECT * FROM (SELECT a, SUM(x) AS s FROM t GROUP BY ROLLUP (a)) AS q")
    assert rewritten.startswith("SELECT * FROM (SELECT __g0 AS a, __a0 AS s FROM (")
    assert "ROLLUP" not in rewritten


def test_plain_group_by_is_untouched():
    sql = "SELECT a, SUM(x) FROM t GROUP BY a"
    assert rewrite_query(sql) == sql


def test_window_ordering_expression_is_computed_below_the_window():
    rewritten = rewrite_query(
        "SELECT a, RANK() OVER (ORDER BY SUM(x) / COUNT(*)) AS r FROM t GROUP BY ROLLUP (a) ORDER BY r"
    )
    assert "RANK() OVER (ORDER BY __o0) AS r" in rewritten
    assert "__a0 / __a1 AS __o0" in rewritten


def test_window_ordering_expression_shared_with_order_by_is_computed_once():
    rewritten = rewrite_query(
        "SELECT a, RANK() OVER (ORDER BY SUM(x) / COUNT(*)) AS r FROM t GROUP BY ROLLUP (a) "
        "ORDER BY RANK() OVER (ORDER BY SUM(x) / COUNT(*))"
    )
    assert rewritten.count("AS __o0") == 1
    assert "__o1" not in rewritten
    assert rewritten.endswith("ORDER BY RANK() OVER (ORDER BY __o0)")


def test_subquery_inside_having_is_left_alone():
    rewritten = rewrite_query("SELECT a, SUM(x) AS s FROM t GROUP BY ROLLUP (a) HAVING SUM(x) > (SELECT AVG(y) FROM u)")
    assert "(SELECT AVG(y) FROM u)" in rewritten
    assert "__a-1" not in rewritten
    assert rewritten.endswith("WHERE __a0 > (SELECT AVG(y) FROM u)")


def test_group_by_ordinals_inside_rollup_resolve_to_projections():
    rewritten = rewrite_query("SELECT a, b, SUM(x) AS s FROM t GROUP BY ROLLUP (1, 2)")
    assert "GROUP BY a, b" in rewritten
    assert "1 AS __g0" not in rewritten
    assert "NULL AS __g1" in rewritten


def test_mixed_ordinal_and_rollup_resolves_ordinals():
    rewritten = rewrite_query("SELECT c, a, SUM(x) AS s FROM t GROUP BY 1, ROLLUP (a)")
    assert rewritten.count("GROUP BY c, a") == 1
    assert "GROUP BY c UNION ALL" in rewritten or "GROUP BY c)" in rewritten


def test_grouping_with_three_arguments_builds_a_binary_weighted_mask():
    rewritten = rewrite_query("SELECT a, b, c, SUM(x), GROUPING(a, b, c) AS g FROM t GROUP BY ROLLUP (a, b, c)")
    assert "(__f0 * 4 + __f1 * 2 + __f2) AS g" in rewritten


def test_duplicate_grouping_sets_are_kept():
    assert len(_levels("SELECT a, SUM(x) FROM t GROUP BY GROUPING SETS ((a), (a))")) == 2
    assert len(_levels("SELECT a, SUM(x) FROM t GROUP BY ROLLUP (a), ROLLUP (a)")) == 4


def test_decimal_literal_in_group_by_is_not_an_ordinal():
    rewritten = rewrite_query("SELECT a, SUM(x) FROM t GROUP BY ROLLUP (a), 1.5")
    assert "GROUP BY a, 1.5" in rewritten


def test_parenthesised_literal_in_expression_is_not_an_ordinal():
    rewritten = rewrite_query("SELECT a + (1) AS k, SUM(x) FROM t GROUP BY ROLLUP (a + (1))")
    assert "a + (1)" in rewritten


def test_window_inside_scalar_subquery_keeps_its_ordering():
    rewritten = rewrite_query(
        "SELECT a, (SELECT RANK() OVER (ORDER BY y + 1) FROM u LIMIT 1) AS r, SUM(x) FROM t GROUP BY ROLLUP (a)"
    )
    assert "ORDER BY y + 1" in rewritten
    assert "__o0" not in rewritten


def test_group_by_projection_alias_resolves_to_its_expression():
    rewritten = rewrite_query("SELECT a + 1 AS k, SUM(x) AS s FROM t GROUP BY ROLLUP (k)")
    assert "GROUP BY a + 1" in rewritten
    assert "SELECT k AS" not in rewritten
    assert rewritten.startswith("SELECT __g0 AS k, __a0 AS s FROM")


def test_group_by_alias_of_the_same_column_is_unchanged():
    rewritten = rewrite_query("SELECT a AS a, SUM(x) FROM t GROUP BY ROLLUP (a)")
    assert "GROUP BY a" in rewritten


def test_rollup_nested_in_grouping_sets_is_expanded():
    levels = _levels("SELECT a, b, SUM(x) FROM t GROUP BY GROUPING SETS (ROLLUP (a), b)")
    assert len(levels) == 3
    assert "ROLLUP" not in rewrite_query("SELECT a, b, SUM(x) FROM t GROUP BY GROUPING SETS (ROLLUP (a), b)")
    assert levels[0].count("GROUP BY a") == 1
    assert "GROUP BY b" in levels[2]


def test_cube_nested_in_grouping_sets_is_expanded():
    assert len(_levels("SELECT a, b, SUM(x) FROM t GROUP BY GROUPING SETS (CUBE (a, b), ())")) == 5


def test_unaliased_projections_keep_their_expression_text_as_the_column_label():
    rewritten = rewrite_query("SELECT a, COUNT(*), a || 'x' FROM t GROUP BY ROLLUP (a)")
    assert rewritten.startswith("SELECT __g0 AS a, __a0 AS \"COUNT(*)\", __g0 || 'x' AS \"a || 'x'\" FROM (")


def test_having_filters_before_window_functions_run():
    rewritten = rewrite_query(
        "SELECT a, RANK() OVER (ORDER BY SUM(x)) AS r FROM t GROUP BY ROLLUP (a) HAVING SUM(x) > 1"
    )
    assert rewritten.startswith("SELECT __g0 AS a, RANK() OVER (ORDER BY __a0) AS r FROM (SELECT __g0 AS __g0")
    assert rewritten.endswith("AS __staged_having WHERE __a0 > 1) AS __staged")
