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
