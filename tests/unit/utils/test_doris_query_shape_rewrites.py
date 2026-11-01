from __future__ import annotations

import pytest

from benchbox.platforms.doris import _empty_fields_to_null
from benchbox.utils.dialect_utils import translate_sql_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _doris(sql: str) -> str:
    return translate_sql_query(sql, "doris", source_dialect="duckdb")


def test_full_outer_join_stays_a_full_outer_join():
    sql = _doris("SELECT SUM(a.x) FROM a FULL OUTER JOIN b ON a.k = b.k")
    assert "FULL OUTER JOIN" in sql
    assert "UNION" not in sql


def test_date_column_plus_integer_becomes_date_add():
    sql = _doris("SELECT 1 FROM t WHERE d1.d_date + 5 > d2.d_date - 3")
    assert "DATE_ADD(`d1`.`d_date`, INTERVAL 5 DAY)" in sql
    assert "DATE_SUB(`d2`.`d_date`, INTERVAL 3 DAY)" in sql


def test_rollup_over_empty_input_still_emits_the_grand_total_row():
    sql = _doris(
        "WITH c AS (SELECT 1 AS a, 2 AS x) SELECT a, AVG(x) AS m FROM c GROUP BY ROLLUP (a) ORDER BY a LIMIT 5"
    )
    assert sql.startswith("WITH `c` AS")
    assert "HAVING COUNT(*) = 0" in sql
    assert "UNION ALL" in sql


def test_rollup_with_grouping_function_is_left_alone():
    sql = _doris("SELECT a, GROUPING(a) AS g, SUM(x) FROM t GROUP BY ROLLUP (a)")
    assert "UNION" not in sql


def test_empty_fields_become_nulls_without_touching_the_trailing_delimiter():
    assert _empty_fields_to_null("1||x|", "|") == "1|\\N|x|"
    assert _empty_fields_to_null("1||x", "|") == "1|\\N|x"


def test_sourceless_exists_is_inlined_as_its_predicate():
    from benchbox._vendor import sqlglot
    from benchbox.utils.query_shape_rewrites import inline_sourceless_exists

    tree = sqlglot.parse_one("SELECT 1 FROM a WHERE EXISTS(SELECT 1 WHERE a.x = 1 OR a.y = 2)", read="starrocks")
    rewritten = inline_sourceless_exists(tree).sql(dialect="starrocks")
    assert "EXISTS" not in rewritten
    assert "(a.x = 1 OR a.y = 2)" in rewritten

    kept = sqlglot.parse_one("SELECT 1 FROM a WHERE EXISTS(SELECT 1 FROM b WHERE b.k = a.k)", read="starrocks")
    assert "EXISTS" in inline_sourceless_exists(kept).sql(dialect="starrocks")
