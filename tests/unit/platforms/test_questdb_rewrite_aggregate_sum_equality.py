from __future__ import annotations

import pytest

from benchbox.core.tpchavoc.variant_sets import Q15_VARIANTS
from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_sum_subquery_equal_to_max_of_sums_uses_tolerance():
    sql = (
        "SELECT s FROM t WHERE (SELECT SUM(a) FROM u WHERE u.k = t.k) = "
        "(SELECT MAX(r.v) FROM (SELECT k, SUM(a) AS v FROM u GROUP BY k) AS r)"
    )
    rewritten = rewrite_query(sql)
    assert "ABS((SELECT SUM(a)" in rewritten
    assert "<= 0.0001" in rewritten
    assert ") = (" not in rewritten


def test_equality_without_summed_subqueries_is_left_alone():
    sql = "SELECT s FROM t WHERE (SELECT MIN(a) FROM u) = (SELECT MAX(b) FROM (SELECT b FROM w) AS x)"
    assert "ABS" not in rewrite_query(sql)


def test_tpchavoc_q15_scalar_subquery_variant_has_no_float_equality():
    rewritten = rewrite_query(Q15_VARIANTS[1]._sql)
    assert "ABS(" in rewritten
    assert "<= 0.0001" in rewritten


REVENUE_NAMES = {"total_revenue", "_col_0", "max_revenue", "max_total_revenue", "_qx_agg_1"}


def _float_revenue_equalities(sql: str) -> list[str]:
    from benchbox._vendor import sqlglot
    from benchbox.platforms.questdb_rewrites import exp

    tree = sqlglot.parse_one(sql, read="postgres")
    return [
        node.sql()
        for node in tree.find_all(exp.EQ)
        if not isinstance(node.expression, exp.Literal)
        and any(column.name.lower() in REVENUE_NAMES for column in node.find_all(exp.Column))
    ]


@pytest.mark.parametrize("variant_id", [1, 2, 3, 4, 5, 6, 7, 8, 10])
def test_tpchavoc_q15_variants_have_no_float_revenue_equality(variant_id):
    rewritten = rewrite_query(Q15_VARIANTS[variant_id]._sql)
    assert _float_revenue_equalities(rewritten) == []
    assert "ABS(" in rewritten or ">=" in rewritten


def test_cte_alias_joined_to_max_cte_uses_tolerance():
    sql = (
        "WITH rev AS (SELECT k, SUM(a) AS total FROM u GROUP BY k), "
        "mx AS (SELECT MAX(total) AS top FROM rev) "
        "SELECT r.k FROM rev AS r JOIN mx AS m ON r.total = m.top"
    )
    rewritten = rewrite_query(sql)
    assert "ABS(r.total - m.top) <= 0.0001" in rewritten
    assert "r.total = m.top" not in rewritten


def test_column_equal_to_windowed_max_of_sums_uses_tolerance():
    sql = (
        "WITH rev AS (SELECT k, SUM(a) AS total FROM u GROUP BY k) "
        "SELECT r.k FROM (SELECT total, k, MAX(total) OVER () AS peak FROM rev) AS r WHERE r.total = r.peak"
    )
    rewritten = rewrite_query(sql)
    assert "ABS(r.total - r.peak) <= 0.0001" in rewritten


def test_sum_in_having_compared_to_max_of_sums_gets_tolerance_floor():
    sql = (
        "SELECT t.k, SUM(t.a) AS total FROM t CROSS JOIN "
        "(SELECT MAX(x.total) AS top FROM (SELECT SUM(a) AS total FROM u GROUP BY k) AS x) AS m "
        "GROUP BY t.k HAVING SUM(t.a) >= MAX(m.top)"
    )
    rewritten = rewrite_query(sql)
    assert "(qdb_agg_3 - 0.0001)" in rewritten


def test_max_equality_without_sums_is_left_alone():
    sql = "SELECT a FROM t JOIN (SELECT MAX(b) AS top FROM w) AS m ON t.b = m.top"
    assert "ABS" not in rewrite_query(sql)
