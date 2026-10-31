from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_with_modules

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def rewrite_query(sql: str) -> str:
    return rewrite_with_modules(sql, "having_to_subquery")


def test_having_becomes_filter_over_derived_table():
    assert rewrite_query("SELECT a, COUNT(*) AS n FROM t GROUP BY a HAVING COUNT(*) > 1") == (
        "SELECT qdb_grp_1 AS a, qdb_agg_2 AS n FROM "
        "(SELECT a AS qdb_grp_1, COUNT(*) AS qdb_agg_2 FROM t GROUP BY a) AS qdb_having WHERE qdb_agg_2 > 1"
    )


def test_having_keeps_order_by_and_limit_and_rewrites_order_aggregates():
    assert rewrite_query(
        "SELECT a, SUM(b) AS s FROM t GROUP BY a HAVING SUM(b) > 5 ORDER BY SUM(b) DESC, a LIMIT 10"
    ) == (
        "SELECT qdb_grp_1 AS a, qdb_agg_2 AS s FROM "
        "(SELECT a AS qdb_grp_1, SUM(b) AS qdb_agg_2 FROM t GROUP BY a) AS qdb_having "
        "WHERE qdb_agg_2 > 5 ORDER BY qdb_agg_2 DESC, qdb_grp_1 LIMIT 10"
    )


def test_having_order_by_select_alias_and_ordinal_are_preserved():
    rewritten = rewrite_query("SELECT a, COUNT(*) AS n FROM t GROUP BY a HAVING COUNT(*) > 1 ORDER BY n DESC, 1")
    assert rewritten.endswith("WHERE qdb_agg_2 > 1 ORDER BY n DESC, 1")


def test_having_on_select_alias_and_group_by_ordinal():
    rewritten = rewrite_query("SELECT a AS k, COUNT(*) AS n FROM t GROUP BY 1 HAVING n > 1")
    assert rewritten == (
        "SELECT qdb_grp_1 AS k, qdb_agg_2 AS n FROM "
        "(SELECT a AS qdb_grp_1, COUNT(*) AS qdb_agg_2 FROM t GROUP BY a) AS qdb_having WHERE qdb_agg_2 > 1"
    )


def test_having_in_nested_query_cte_and_union_member():
    rewritten = rewrite_query(
        "WITH c AS (SELECT a, SUM(b) AS s FROM t GROUP BY a HAVING SUM(b) > 1) "
        "SELECT * FROM (SELECT x, COUNT(*) AS n FROM u GROUP BY x HAVING COUNT(*) > 2) AS q "
        "UNION ALL SELECT a, s FROM c"
    )
    assert "HAVING" not in rewritten
    assert "WHERE qdb_agg_2 > 1" in rewritten
    assert "WHERE qdb_agg_2 > 2" in rewritten
    assert rewritten.startswith("WITH c AS (SELECT qdb_grp_1 AS a")


def test_having_with_scalar_subquery_condition():
    rewritten = rewrite_query(
        "SELECT k, SUM(v) AS total FROM t GROUP BY k "
        "HAVING SUM(v) > (SELECT SUM(v) * 0.0001 FROM t) ORDER BY SUM(v) DESC"
    )
    assert rewritten == (
        "SELECT qdb_grp_1 AS k, qdb_agg_2 AS total FROM "
        "(SELECT k AS qdb_grp_1, SUM(v) AS qdb_agg_2 FROM t GROUP BY k) AS qdb_having "
        "CROSS JOIN (SELECT SUM(v) * 0.0001 AS qdb_sub_1 FROM t) AS qdb_scalar_1 "
        "WHERE qdb_agg_2 > qdb_scalar_1.qdb_sub_1 ORDER BY qdb_agg_2 DESC"
    )


def test_having_scalar_subquery_inside_arithmetic_and_in_list_subquery_untouched():
    rewritten = rewrite_query(
        "SELECT k FROM t GROUP BY k HAVING SUM(v) > 0.5 * (SELECT AVG(x) FROM u) AND k IN (SELECT y FROM w)"
    )
    assert rewritten == (
        "SELECT qdb_grp_1 AS k FROM (SELECT k AS qdb_grp_1, SUM(v) AS qdb_agg_2 FROM t GROUP BY k) AS qdb_having "
        "CROSS JOIN (SELECT AVG(x) AS qdb_sub_1 FROM u) AS qdb_scalar_1 "
        "WHERE qdb_agg_2 > 0.5 * qdb_scalar_1.qdb_sub_1 AND qdb_grp_1 IN (SELECT y FROM w)"
    )


def test_having_with_distinct_and_window_function_stays_in_outer_query():
    rewritten = rewrite_query(
        "SELECT DISTINCT a, RANK() OVER (ORDER BY SUM(b)) AS r FROM t GROUP BY a HAVING SUM(b) > 1"
    )
    assert rewritten == (
        "SELECT DISTINCT qdb_grp_1 AS a, RANK() OVER (ORDER BY qdb_agg_2) AS r FROM "
        "(SELECT a AS qdb_grp_1, SUM(b) AS qdb_agg_2 FROM t GROUP BY a) AS qdb_having WHERE qdb_agg_2 > 1"
    )


def test_having_without_group_by():
    assert rewrite_query("SELECT SUM(b) AS s FROM t HAVING SUM(b) > 1") == (
        "SELECT qdb_agg_1 AS s FROM (SELECT SUM(b) AS qdb_agg_1 FROM t) AS qdb_having WHERE qdb_agg_1 > 1"
    )


def test_having_ignores_aggregates_inside_subquery_condition():
    rewritten = rewrite_query("SELECT a FROM t GROUP BY a HAVING COUNT(*) > (SELECT COUNT(*) FROM u)")
    assert "(SELECT COUNT(*) AS qdb_sub_1 FROM u) AS qdb_scalar_1" in rewritten
    assert rewritten.endswith("WHERE qdb_agg_2 > qdb_scalar_1.qdb_sub_1")


def test_having_keeps_same_named_columns_from_different_tables_distinct():
    rewritten = rewrite_query(
        "SELECT a.x, b.x, COUNT(*) AS n FROM a JOIN b ON a.k = b.k GROUP BY a.x, b.x HAVING COUNT(*) > 1 ORDER BY b.x"
    )
    assert rewritten == (
        "SELECT qdb_grp_1 AS x, qdb_grp_2 AS x, qdb_agg_3 AS n FROM "
        "(SELECT a.x AS qdb_grp_1, b.x AS qdb_grp_2, COUNT(*) AS qdb_agg_3 FROM a JOIN b ON a.k = b.k "
        "GROUP BY a.x, b.x) AS qdb_having WHERE qdb_agg_3 > 1 ORDER BY qdb_grp_2"
    )


def test_having_matches_unqualified_reference_to_unique_qualified_group_column():
    rewritten = rewrite_query("SELECT t.k, COUNT(*) AS n FROM t GROUP BY t.k HAVING COUNT(*) > 1 ORDER BY k")
    assert rewritten.endswith("WHERE qdb_agg_2 > 1 ORDER BY qdb_grp_1")


def test_having_unaliased_unknown_function_keeps_its_name():
    rewritten = rewrite_query("SELECT a, my_fn(a) FROM t GROUP BY a HAVING COUNT(*) > 1")
    assert rewritten.startswith("SELECT qdb_grp_1 AS a, MY_FN(qdb_grp_1) AS my_fn FROM")


def test_having_subquery_that_can_return_no_rows_is_not_hoisted():
    rewritten = rewrite_query("SELECT k FROM t GROUP BY k HAVING SUM(v) > 5 OR SUM(v) > (SELECT x FROM u WHERE y = 1)")
    assert "CROSS JOIN" not in rewritten
    assert rewritten.endswith("WHERE qdb_agg_2 > 5 OR qdb_agg_2 > (SELECT x FROM u WHERE y = 1)")


def test_having_ungrouped_aggregate_subquery_is_hoisted_under_or():
    rewritten = rewrite_query("SELECT k FROM t GROUP BY k HAVING SUM(v) > 5 OR SUM(v) > (SELECT MAX(x) FROM u)")
    assert "CROSS JOIN (SELECT MAX(x) AS qdb_sub_1 FROM u) AS qdb_scalar_1" in rewritten


def test_having_subquery_whose_only_aggregate_is_nested_is_not_hoisted():
    rewritten = rewrite_query("SELECT k FROM t GROUP BY k HAVING SUM(v) > (SELECT (SELECT MAX(z) FROM q) FROM u)")
    assert "CROSS JOIN" not in rewritten


def test_having_subquery_with_window_aggregate_only_is_not_hoisted():
    rewritten = rewrite_query("SELECT k FROM t GROUP BY k HAVING SUM(v) > (SELECT SUM(x) OVER () FROM u)")
    assert "CROSS JOIN" not in rewritten


def test_having_with_empty_group_by_drops_the_group_clause():
    rewritten = rewrite_query("SELECT 100.0 * SUM(a) / SUM(b) AS r FROM t GROUP BY () HAVING SUM(b) > 0")
    assert rewritten == (
        "SELECT 100.0 * qdb_agg_1 / qdb_agg_2 AS r FROM "
        "(SELECT SUM(a) AS qdb_agg_1, SUM(b) AS qdb_agg_2 FROM t) AS qdb_having WHERE qdb_agg_2 > 0"
    )


@pytest.mark.parametrize(
    "group_by",
    ["GROUP BY a WITH ROLLUP", "GROUP BY ALL", "GROUP BY a GROUPING SETS ((a), ())", "GROUP BY ROLLUP (a)"],
)
def test_having_with_group_by_modifiers_is_left_alone(group_by):
    sql = f"SELECT a, COUNT(*) AS n FROM t {group_by} HAVING COUNT(*) > 1"
    assert "qdb_having" not in rewrite_query(sql)
