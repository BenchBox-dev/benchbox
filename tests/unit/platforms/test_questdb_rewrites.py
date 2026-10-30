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
