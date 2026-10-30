from __future__ import annotations

import pytest

from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_where_inequality_between_columns_excludes_nulls():
    assert rewrite_query("SELECT a FROM t WHERE b <> c") == (
        "SELECT a FROM t WHERE (b <> c AND NOT b IS NULL AND NOT c IS NULL)"
    )


def test_inequality_against_literal_guards_only_the_column():
    assert rewrite_query("SELECT a FROM t WHERE b <> 'x'") == "SELECT a FROM t WHERE (b <> 'x' AND NOT b IS NULL)"


def test_bang_equals_is_rewritten():
    assert "NOT b IS NULL" in rewrite_query("SELECT a FROM t WHERE b != c")


def test_join_condition_inequality_is_rewritten():
    assert "NOT t1.b IS NULL" in rewrite_query("SELECT 1 FROM t1 JOIN t2 ON t1.a = t2.a AND t1.b <> t2.b")


def test_having_and_case_conditions_are_rewritten():
    assert "NOT b IS NULL" in rewrite_query("SELECT CASE WHEN b <> c THEN 1 ELSE 0 END FROM t")


def test_inequality_under_not_is_untouched():
    sql = "SELECT a FROM t WHERE NOT (b <> c)"
    assert rewrite_query(sql) == sql


def test_projected_inequality_is_untouched():
    sql = "SELECT b <> c AS flag FROM t"
    assert rewrite_query(sql) == sql


def test_inequality_between_literals_is_untouched():
    sql = "SELECT a FROM t WHERE 1 <> 2"
    assert rewrite_query(sql) == sql


def test_nested_inequality_inside_an_operand_is_also_guarded():
    rewritten = rewrite_query("SELECT a FROM t WHERE b <> (SELECT MAX(c) FROM u WHERE d <> e)")
    assert "NOT d IS NULL AND NOT e IS NULL" in rewritten
    assert rewritten.count("NOT b IS NULL") == 1


def test_scalar_subquery_operand_is_not_duplicated_into_a_guard():
    rewritten = rewrite_query("SELECT a FROM t WHERE b <> (SELECT MAX(c) FROM u)")
    assert rewritten.count("SELECT MAX(c)") == 1
    assert "NOT b IS NULL" in rewritten


@pytest.mark.parametrize("quantifier", ["ANY", "ALL"])
def test_quantified_subquery_operand_gets_no_guard(quantifier):
    rewritten = rewrite_query(f"SELECT a FROM t WHERE b <> {quantifier} (SELECT c FROM u)")
    assert "IS NULL" not in rewritten.replace("NOT b IS NULL", "")


def test_having_inequality_is_guarded():
    assert "NOT SUM(x) IS NULL" in rewrite_query("SELECT a FROM t GROUP BY a HAVING SUM(x) <> MAX(y)")


def test_qualify_inequality_is_guarded():
    assert "NOT r IS NULL" in rewrite_query("SELECT a, ROW_NUMBER() OVER (ORDER BY a) AS r FROM t QUALIFY r <> b")
