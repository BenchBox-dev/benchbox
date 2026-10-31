from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_with_modules

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def rewrite_query(sql: str) -> str:
    return rewrite_with_modules(sql, "distinct_window_expression")


def test_distinct_over_an_expression_of_windows_is_staged_in_a_derived_table():
    sql = "SELECT DISTINCT 100.0 * SUM(a) OVER () / SUM(b) OVER () AS p FROM t WHERE x = 1"
    assert rewrite_query(sql) == (
        "SELECT DISTINCT _qdw.p FROM (SELECT 100.0 * SUM(a) OVER () / SUM(b) OVER () AS p FROM t WHERE x = 1) AS _qdw"
    )


def test_order_by_is_remapped_onto_the_staged_output_columns():
    sql = "SELECT DISTINCT o.y AS y, SUM(v) OVER (PARTITION BY o.y) / 2 AS s FROM o ORDER BY CASE WHEN o.y IS NULL THEN 1 ELSE 0 END, o.y LIMIT 5"
    assert rewrite_query(sql) == (
        "SELECT DISTINCT _qdw.y, _qdw.s FROM (SELECT o.y AS y, SUM(v) OVER (PARTITION BY o.y) / 2 AS s FROM o) AS _qdw "
        "ORDER BY CASE WHEN _qdw.y IS NULL THEN 1 ELSE 0 END, _qdw.y LIMIT 5"
    )


def test_distinct_over_a_bare_window_is_left_alone():
    sql = "SELECT DISTINCT SUM(a) OVER () AS p FROM t"
    assert rewrite_query(sql) == sql


def test_order_by_on_a_column_outside_the_select_list_is_left_alone():
    sql = "SELECT DISTINCT a, SUM(a) OVER () + 1 AS p FROM t ORDER BY b"
    assert rewrite_query(sql) == sql


def test_select_without_distinct_is_left_alone():
    sql = "SELECT SUM(a) OVER () + 1 AS p FROM t"
    assert rewrite_query(sql) == sql


@pytest.mark.parametrize(
    "select_list",
    [
        "SUM(a) OVER () + 1 AS p, SUM(a) OVER () + 1 AS q",
        "SUM(a) OVER () + 1 AS p, b AS p",
        "a, t.a, SUM(a) OVER () + 1 AS p",
    ],
)
def test_duplicate_expressions_or_output_names_are_left_alone(select_list):
    sql = f"SELECT DISTINCT {select_list} FROM t"
    assert rewrite_query(sql) == sql
