from pathlib import Path
from unittest.mock import MagicMock

import pytest

from benchbox.core.query_plans.parsers.presto_trino import PrestoTrinoQueryPlanParser

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "query_plans"


class _FakeCursor:
    def __init__(self, explain_json: str):
        self._explain_json = explain_json
        self._last_sql = ""

    def execute(self, sql, *args, **kwargs):
        self._last_sql = sql

    def fetchall(self):
        if self._last_sql.strip().upper().startswith("EXPLAIN"):
            return [(self._explain_json,)]
        return [(1,)]

    def fetchone(self):
        return (1,)

    def close(self):
        pass


class _FakeConn:
    def __init__(self, explain_json: str):
        self._explain_json = explain_json

    def cursor(self):
        return _FakeCursor(self._explain_json)


def _load(name: str) -> str:
    return (_FIXTURES / name).read_text()


@pytest.fixture()
def presto_adapter(monkeypatch):
    monkeypatch.setattr("benchbox.platforms.presto.prestodb", MagicMock(), raising=False)
    from benchbox.platforms.presto import PrestoAdapter

    return PrestoAdapter(capture_plans=True)


@pytest.fixture()
def starburst_adapter(monkeypatch):
    monkeypatch.setattr("benchbox.platforms.trino.trino", MagicMock(), raising=False)
    from benchbox.platforms.starburst import StarburstAdapter

    return StarburstAdapter(
        capture_plans=True,
        host="cluster.trino.galaxy.starburst.io",
        username="user@example.com/accountadmin",
        password="pw",
    )


@pytest.fixture()
def athena_adapter(monkeypatch):
    monkeypatch.setattr("benchbox.platforms.athena.boto3", MagicMock(), raising=False)
    monkeypatch.setattr("benchbox.platforms.athena.athena_connect", MagicMock(), raising=False)
    monkeypatch.setattr("benchbox.platforms.athena.AthenaAdapter._validate_configuration", lambda self: None)
    from benchbox.platforms.athena import AthenaAdapter

    return AthenaAdapter(capture_plans=True, s3_bucket="test-bucket", database="benchbox_db")


class TestParserWiring:
    def test_presto_parser_is_presto_trino(self, presto_adapter):
        assert isinstance(presto_adapter.get_query_plan_parser(), PrestoTrinoQueryPlanParser)

    def test_starburst_parser_is_presto_trino(self, starburst_adapter):
        assert isinstance(starburst_adapter.get_query_plan_parser(), PrestoTrinoQueryPlanParser)

    def test_athena_parser_is_presto_trino(self, athena_adapter):
        assert isinstance(athena_adapter.get_query_plan_parser(), PrestoTrinoQueryPlanParser)


class TestGetQueryPlanUsesFormatJson:
    def test_presto_get_query_plan_requests_format_json(self, presto_adapter):
        conn = _FakeConn(_load("presto_explain_sample.json"))
        cursor = conn.cursor()
        conn.cursor = lambda: cursor
        plan = presto_adapter.get_query_plan(conn, "SELECT 1")
        assert "FORMAT JSON" in cursor._last_sql.upper()
        assert plan.strip().startswith("{")


class TestExecuteQueryCapture:
    def test_presto_execute_query_captures_plan(self, presto_adapter):
        conn = _FakeConn(_load("presto_explain_sample.json"))
        result = presto_adapter.execute_query(
            connection=conn, query="SELECT 1", query_id="pq1", validate_row_count=False
        )
        assert result["status"] == "SUCCESS"
        assert result["query_plan"] is not None
        assert result["plan_fingerprint"] is not None
        assert result["query_plan"].plan_fingerprint == result["plan_fingerprint"]

    def test_presto_execute_query_no_capture_when_disabled(self, presto_adapter):
        presto_adapter.capture_plans = False
        conn = _FakeConn(_load("presto_explain_sample.json"))
        result = presto_adapter.execute_query(
            connection=conn, query="SELECT 1", query_id="pq2", validate_row_count=False
        )
        assert "query_plan" not in result or result.get("query_plan") is None

    def test_athena_execute_query_captures_plan(self, athena_adapter):
        conn = _FakeConn(_load("athena_explain_sample.json"))
        result = athena_adapter.execute_query(
            connection=conn, query="SELECT 1", query_id="aq1", validate_row_count=False
        )
        assert result["status"] == "SUCCESS"
        assert result["query_plan"] is not None
        assert result["plan_fingerprint"] is not None


