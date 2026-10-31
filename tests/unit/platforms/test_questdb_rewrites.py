from __future__ import annotations

from unittest.mock import Mock

import pytest

from benchbox.platforms.questdb import QuestDBAdapter
from benchbox.platforms.questdb_rewrites import rewrite_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_date_trunc_unit_is_lowercased():
    sql = "SELECT DATE_TRUNC('MINUTE', ts) AS m, COUNT(*) FROM t GROUP BY DATE_TRUNC('MINUTE', ts)"
    assert (
        rewrite_query(sql) == "SELECT DATE_TRUNC('minute', ts) AS m, COUNT(*) FROM t GROUP BY DATE_TRUNC('minute', ts)"
    )


@pytest.mark.parametrize("unit", ["DAY", "MONTH", "SECOND", "HOUR"])
def test_date_trunc_every_unit(unit):
    assert f"DATE_TRUNC('{unit.lower()}', ts)" in rewrite_query(f"SELECT DATE_TRUNC('{unit}', ts) FROM t")


def test_query_without_rewrites_is_returned_verbatim():
    sql = "SELECT  a,b   FROM t ;"
    assert rewrite_query(sql) == sql


def test_unparseable_query_is_returned_verbatim():
    sql = "SELEC ((("
    assert rewrite_query(sql) == sql


def test_execute_query_runs_rewritten_sql(monkeypatch):
    import benchbox.platforms.questdb as questdb_module

    captured = {}

    def fake_execute(connection, query, query_id, **kwargs):
        captured["query"] = query
        return {"query_id": query_id, "status": "SUCCESS"}

    monkeypatch.setattr(questdb_module, "execute_sql_query", fake_execute)
    adapter = QuestDBAdapter()
    monkeypatch.setattr(adapter, "_merge_plan_capture_into_result", lambda *args, **kwargs: None)
    adapter.execute_query(Mock(), "SELECT DATE_TRUNC('DAY', ts) FROM t", "q1")
    assert captured["query"] == "SELECT DATE_TRUNC('day', ts) FROM t"


def test_date_trunc_with_zone_is_left_unchanged():
    sql = "SELECT DATE_TRUNC('DAY', ts, 'UTC') FROM t"
    assert rewrite_query(sql) == sql


def test_nested_date_trunc_is_lowercased():
    assert rewrite_query("SELECT DATE_TRUNC('DAY', DATE_TRUNC('HOUR', ts)) FROM t") == (
        "SELECT DATE_TRUNC('day', DATE_TRUNC('hour', ts)) FROM t"
    )


def test_plan_capture_and_explain_see_the_rewritten_query(monkeypatch):
    import benchbox.platforms.questdb as questdb_module

    seen = {}
    monkeypatch.setattr(
        questdb_module,
        "execute_sql_query",
        lambda connection, query, query_id, **kwargs: {"query_id": query_id, "status": "SUCCESS"},
    )
    adapter = QuestDBAdapter()
    monkeypatch.setattr(
        adapter, "_merge_plan_capture_into_result", lambda result, connection, query, query_id: seen.update(query=query)
    )
    adapter.execute_query(Mock(), "SELECT DATE_TRUNC('DAY', ts) FROM t", "q1")
    assert seen["query"] == "SELECT DATE_TRUNC('day', ts) FROM t"

    cursor = Mock()
    cursor.fetchall.return_value = [("plan",)]
    connection = Mock()
    connection.cursor.return_value = cursor
    adapter.get_query_plan(connection, "SELECT DATE_TRUNC('DAY', ts) FROM t")
    cursor.execute.assert_called_once_with("EXPLAIN SELECT DATE_TRUNC('day', ts) FROM t")


def test_already_lowercase_date_trunc_is_returned_verbatim():
    sql = "select  date_trunc('day',ts)  from t"
    assert rewrite_query(sql) == sql


def test_regeneration_by_another_rewrite_keeps_lowercase_units(monkeypatch):
    from benchbox.platforms import questdb_rewrites

    class Marker:
        ORDER = 5

        @staticmethod
        def transform(expression):
            return expression.copy().where("1 = 1")

    discovered = questdb_rewrites._modules()
    monkeypatch.setattr(questdb_rewrites, "_modules", lambda: (Marker, *discovered))
    assert rewrite_query("SELECT DATE_TRUNC('day', ts) FROM t") == "SELECT DATE_TRUNC('day', ts) FROM t WHERE 1 = 1"


@pytest.mark.parametrize(
    ("part", "function"),
    [
        ("YEAR", "year"),
        ("MONTH", "month"),
        ("DAY", "day"),
        ("HOUR", "hour"),
        ("MINUTE", "minute"),
        ("SECOND", "second"),
        ("WEEK", "week_of_year"),
        ("ISODOW", "day_of_week"),
    ],
)
def test_extract_part_becomes_function(part, function):
    assert rewrite_query(f"SELECT EXTRACT({part} FROM ts) FROM t") == f"SELECT {function.upper()}(ts) FROM t"


def test_extract_inside_case_is_rewritten():
    sql = "SELECT a FROM t ORDER BY CASE WHEN EXTRACT(HOUR FROM ts) IS NULL THEN 1 ELSE 0 END, EXTRACT(HOUR FROM ts)"
    assert rewrite_query(sql) == "SELECT a FROM t ORDER BY CASE WHEN HOUR(ts) IS NULL THEN 1 ELSE 0 END, HOUR(ts)"


def test_extract_dow_is_zero_based_from_sunday():
    assert rewrite_query("SELECT EXTRACT(DOW FROM ts) FROM t") == "SELECT (DAY_OF_WEEK_SUNDAY_FIRST(ts) - 1) FROM t"


def test_extract_without_a_function_equivalent_is_left_alone():
    sql = "SELECT EXTRACT(QUARTER FROM ts) FROM t"
    assert rewrite_query(sql) == sql


def test_group_by_alias_of_an_extract_is_expanded_to_the_expression():
    sql = (
        "SELECT EXTRACT(YEAR FROM ts) AS year, COUNT(*) AS n FROM t GROUP BY year "
        "ORDER BY CASE WHEN EXTRACT(YEAR FROM ts) IS NULL THEN 1 ELSE 0 END, EXTRACT(YEAR FROM ts)"
    )
    assert rewrite_query(sql) == (
        "SELECT YEAR(ts) AS year, COUNT(*) AS n FROM t GROUP BY YEAR(ts) "
        "ORDER BY CASE WHEN YEAR(ts) IS NULL THEN 1 ELSE 0 END, YEAR(ts)"
    )


def test_group_by_alias_of_a_plain_column_is_kept():
    sql = "SELECT EXTRACT(YEAR FROM ts) AS y, a AS k, COUNT(*) FROM t GROUP BY k"
    assert rewrite_query(sql) == "SELECT YEAR(ts) AS y, a AS k, COUNT(*) FROM t GROUP BY k"


@pytest.mark.parametrize("type_sql", ["DECIMAL(15,2)", "DECIMAL(10,4)", "DECIMAL", "NUMERIC(12,2)"])
def test_decimal_cast_becomes_double(type_sql):
    assert rewrite_query(f"SELECT CAST(a * 2 AS {type_sql}) FROM t") == "SELECT CAST(a * 2 AS DOUBLE) FROM t"


def test_non_decimal_casts_are_left_unchanged():
    sql = "SELECT CAST(a AS INT), CAST(b AS VARCHAR) FROM t"
    assert rewrite_query(sql) == sql


def test_create_table_as_select_is_parenthesised():
    assert rewrite_query("CREATE TABLE a AS SELECT * FROM b WHERE x > 1") == (
        "CREATE TABLE a AS (SELECT * FROM b WHERE x > 1)"
    )


def test_parenthesised_create_table_as_select_is_left_unchanged():
    sql = "CREATE TABLE a AS (SELECT * FROM b)"
    assert rewrite_query(sql) == sql


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("SELECT ANY_VALUE(c_name) FROM c GROUP BY m", "SELECT FIRST(c_name) FROM c GROUP BY m"),
        ("SELECT APPROX_DISTINCT(a) FROM t", "SELECT APPROX_COUNT_DISTINCT(a) FROM t"),
        ("SELECT APPROX_QUANTILE(q, 0.5) FROM t", "SELECT APPROX_PERCENTILE(q, 0.5) FROM t"),
    ],
)
def test_function_names_are_mapped_to_questdb_equivalents(source, expected):
    assert rewrite_query(source) == expected
