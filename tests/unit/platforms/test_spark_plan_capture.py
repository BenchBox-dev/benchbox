from pathlib import Path

import pytest

from benchbox.core.query_plans.parsers.spark import SparkQueryPlanParser

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "query_plans"
_PLAN_TEXT = (_FIXTURES / "spark_explain_sample.txt").read_text()


class _DF:
    def __init__(self, rows):
        self._rows = rows

    def collect(self):
        return self._rows


class _FakeSpark:
    def __init__(self):
        self.queries = []

    def sql(self, query):
        self.queries.append(query)
        if query.strip().upper().startswith("EXPLAIN"):
            return _DF([(_PLAN_TEXT,)])
        return _DF([(1,)])


class _FakeCursor:
    def __init__(self):
        self.last = ""

    def execute(self, sql, *a, **k):
        self.last = sql

    def fetchone(self):
        return ("use_cached_result", "false") if self.last.strip().lower() == "set use_cached_result" else None

    def fetchall(self):
        return [(_PLAN_TEXT,)] if self.last.strip().upper().startswith("EXPLAIN") else [(1,)]

    def close(self):
        pass


class _FakeDbxConn:
    def __init__(self):
        self.cursors = []

    def cursor(self):
        c = _FakeCursor()
        self.cursors.append(c)
        return c


@pytest.fixture()
def spark_adapter():
    from benchbox.platforms.spark import SparkAdapter

    adapter = SparkAdapter(capture_plans=True)
    adapter.disable_cache = False
    return adapter


@pytest.fixture()
def databricks_adapter(monkeypatch):
    monkeypatch.setattr(
        "benchbox.platforms.databricks.adapter.check_platform_dependencies",
        lambda *a, **k: (True, []),
    )
    from benchbox.platforms.databricks.adapter import DatabricksAdapter

    return DatabricksAdapter(capture_plans=True, server_hostname="h", http_path="/sql/1.0/x", access_token="t")


class TestSparkPlanCapture:
    def test_parser_is_spark(self, spark_adapter):
        assert isinstance(spark_adapter.get_query_plan_parser(), SparkQueryPlanParser)

    def test_execute_query_captures_plan(self, spark_adapter):
        result = spark_adapter._execute_query_spark(
            connection=_FakeSpark(), query="SELECT 1", query_id="sq1", validate_row_count=False
        )
        assert result["status"] == "SUCCESS"
        assert result["query_plan"] is not None
        assert result["plan_fingerprint"] is not None
        assert result["query_plan"].plan_fingerprint == result["plan_fingerprint"]

    def test_no_capture_when_disabled(self, spark_adapter):
        spark_adapter.capture_plans = False
        spark = _FakeSpark()
        result = spark_adapter._execute_query_spark(
            connection=spark, query="SELECT 1", query_id="sq2", validate_row_count=False
        )
        assert "query_plan" not in result or result.get("query_plan") is None
        assert not any(q.strip().upper().startswith("EXPLAIN") for q in spark.queries)

    def test_graceful_when_explain_fails(self, spark_adapter, monkeypatch):

        monkeypatch.setattr(spark_adapter, "get_query_plan", lambda *a, **k: None)
        result = spark_adapter._execute_query_spark(
            connection=_FakeSpark(), query="SELECT 1", query_id="sq3", validate_row_count=False
        )
        assert result["status"] == "SUCCESS"
        assert "query_plan" not in result or result.get("query_plan") is None


class TestDatabricksPlanCapture:
    def test_parser_is_spark(self, databricks_adapter):
        assert isinstance(databricks_adapter.get_query_plan_parser(), SparkQueryPlanParser)

    def test_get_query_plan_uses_explain_extended(self, databricks_adapter):
        conn = _FakeDbxConn()
        plan = databricks_adapter.get_query_plan(conn, "SELECT 1")
        assert plan is not None
        assert any("EXPLAIN EXTENDED" in c.last.upper() for c in conn.cursors)

    def test_execute_query_captures_plan(self, databricks_adapter):
        result = databricks_adapter.execute_query(
            connection=_FakeDbxConn(), query="SELECT 1", query_id="dq1", validate_row_count=False
        )
        assert result["status"] == "SUCCESS"
        assert result["query_plan"] is not None
        assert result["plan_fingerprint"] is not None


