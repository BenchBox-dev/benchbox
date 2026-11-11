from pathlib import Path

import pytest

from benchbox.core.query_plans.parsers.clickhouse import ClickHouseQueryPlanParser

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.usefixtures("chdb_probe_satisfied"),
]

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "query_plans"

_PLAN_TEXT = (_FIXTURES / "clickhouse_explain_plan_sample.txt").read_text(encoding="utf-8")


class _FakeConn:
    def __init__(self, explain_rows=None):
        self._explain_rows = _PLAN_TEXT.splitlines() if explain_rows is None else explain_rows
        self.executed = []

    def execute(self, sql, params=None, query_id=None):
        self.executed.append(sql)
        if sql.strip().upper().startswith("EXPLAIN"):
            return [(line,) for line in self._explain_rows]
        return [(1,)]


def _make(mode, **kwargs):
    if mode == "local":
        from benchbox.platforms.clickhouse_local import ClickHouseLocalAdapter

        return ClickHouseLocalAdapter(**kwargs)
    if mode == "server":
        from benchbox.platforms.clickhouse_server import ClickHouseServerAdapter

        return ClickHouseServerAdapter(**kwargs)
    from benchbox.platforms.clickhouse_cloud import ClickHouseCloudAdapter

    return ClickHouseCloudAdapter(host="h", password="p", **kwargs)


ALL_MODES = ["local", "server", "cloud"]


class TestClickHouseParserWiring:
    @pytest.mark.parametrize("mode", ALL_MODES)
    def test_parser_is_clickhouse(self, mode):
        adapter = _make(mode, capture_plans=True)
        assert isinstance(adapter.get_query_plan_parser(), ClickHouseQueryPlanParser)

    @pytest.mark.parametrize("mode", ALL_MODES)
    def test_get_query_plan_uses_explain_plan(self, mode):
        adapter = _make(mode, capture_plans=True)
        conn = _FakeConn()
        plan = adapter.get_query_plan(conn, "SELECT 1")
        assert plan is not None
        assert any("EXPLAIN PLAN" in sql.upper() for sql in conn.executed)
        assert "ReadFromMergeTree" in plan


class TestClickHouseExecuteQueryCapture:
    @pytest.mark.parametrize("mode", ALL_MODES)
    def test_capture_adds_plan_fields(self, mode):
        adapter = _make(mode, capture_plans=True)
        result = adapter.execute_query(
            connection=_FakeConn(), query="SELECT 1", query_id="cq1", validate_row_count=False
        )
        assert result["status"] == "SUCCESS"
        assert result["query_plan"] is not None
        assert result["plan_fingerprint"] is not None
        assert result["query_plan"].plan_fingerprint == result["plan_fingerprint"]

    def test_no_capture_when_disabled_and_no_explain_issued(self):
        adapter = _make("local", capture_plans=False)
        conn = _FakeConn()
        result = adapter.execute_query(connection=conn, query="SELECT 1", query_id="cq2", validate_row_count=False)
        assert "query_plan" not in result or result.get("query_plan") is None

        assert not any("EXPLAIN" in sql.upper() for sql in conn.executed)

    def test_graceful_when_explain_empty(self):

        adapter = _make("local", capture_plans=True)
        result = adapter.execute_query(
            connection=_FakeConn(explain_rows=[]), query="SELECT 1", query_id="cq3", validate_row_count=False
        )
        assert result["status"] == "SUCCESS"
        assert "query_plan" not in result or result.get("query_plan") is None


