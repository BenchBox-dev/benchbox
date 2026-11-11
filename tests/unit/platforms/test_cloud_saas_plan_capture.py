from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from benchbox.core.query_plans.parsers.azure_synapse import AzureSynapseQueryPlanParser
from benchbox.core.query_plans.parsers.bigquery import BigQueryQueryPlanParser
from benchbox.core.query_plans.parsers.fabric_warehouse import FabricWarehouseQueryPlanParser
from benchbox.core.query_plans.parsers.firebolt import FireboltQueryPlanParser
from benchbox.core.query_plans.parsers.registry import get_parser_for_platform
from benchbox.core.query_plans.parsers.snowflake import SnowflakeQueryPlanParser
from benchbox.core.query_plans.parsers.spark import SparkQueryPlanParser

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "query_plans"


def _load(name: str) -> str:
    return (_FIXTURES / name).read_text()


@pytest.mark.parametrize(
    ("platform", "parser_cls"),
    [
        ("snowflake", SnowflakeQueryPlanParser),
        ("bigquery", BigQueryQueryPlanParser),
        ("azure_synapse", AzureSynapseQueryPlanParser),
        ("firebolt", FireboltQueryPlanParser),
        ("fabric_warehouse", FabricWarehouseQueryPlanParser),
        ("lakesail", SparkQueryPlanParser),
    ],
)
def test_registry_resolves_platform(platform, parser_cls):
    assert isinstance(get_parser_for_platform(platform), parser_cls)


def _build(module_name: str, class_name: str, monkeypatch, **config):
    module = importlib.import_module(module_name)
    if hasattr(module, "check_platform_dependencies"):
        monkeypatch.setattr(module, "check_platform_dependencies", lambda *a, **k: (True, []))
    adapter = getattr(module, class_name)(capture_plans=True, **config)
    return adapter


def _make_snowflake(monkeypatch):
    adapter = _build(
        "benchbox.platforms.snowflake",
        "SnowflakeAdapter",
        monkeypatch,
        account="a",
        username="u",
        password="p",
        warehouse="w",
        database="d",
    )

    monkeypatch.setattr(adapter, "_get_query_statistics", lambda *a, **k: {})
    return adapter


def _make_azure_synapse(monkeypatch):
    return _build(
        "benchbox.platforms.azure_synapse",
        "AzureSynapseAdapter",
        monkeypatch,
        server="s",
        username="u",
        password="p",
        database="d",
    )


def _make_firebolt(monkeypatch):
    return _build("benchbox.platforms.firebolt", "FireboltAdapter", monkeypatch)


def _make_fabric(monkeypatch):
    return _build(
        "benchbox.platforms.fabric_warehouse",
        "FabricWarehouseAdapter",
        monkeypatch,
        server="s.datawarehouse.fabric.microsoft.com",
        database="d",
        username="u",
        password="p",
    )


def _cursor_conn():

    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.fetchall.return_value = [(1,)]
    cursor.fetchone.return_value = (1,)
    return conn


_CURSOR_CASES = [
    (_make_snowflake, "snowflake_explain_sample.json", SnowflakeQueryPlanParser, {"validate_row_count": False}),
    (
        _make_azure_synapse,
        "azure_synapse_explain_sample.xml",
        AzureSynapseQueryPlanParser,
        {"validate_row_count": False},
    ),
    (_make_firebolt, "firebolt_explain_sample.txt", FireboltQueryPlanParser, {"validate_row_count": False}),
    (_make_fabric, "fabric_warehouse_showplan_sample.txt", FabricWarehouseQueryPlanParser, {}),
]


class TestCursorAdapterWiring:
    @pytest.mark.parametrize(("make", "fixture", "parser_cls", "call_kwargs"), _CURSOR_CASES)
    def test_parser_is_expected(self, make, fixture, parser_cls, call_kwargs, monkeypatch):
        adapter = make(monkeypatch)
        assert isinstance(adapter.get_query_plan_parser(), parser_cls)

    @pytest.mark.parametrize(("make", "fixture", "parser_cls", "call_kwargs"), _CURSOR_CASES)
    def test_execute_query_captures_plan(self, make, fixture, parser_cls, call_kwargs, monkeypatch):
        adapter = make(monkeypatch)
        monkeypatch.setattr(adapter, "get_query_plan", lambda *a, **k: _load(fixture))
        result = adapter.execute_query(_cursor_conn(), "SELECT 1", "q1", **call_kwargs)
        assert result["status"] == "SUCCESS"
        assert result["query_plan"] is not None
        assert result["plan_fingerprint"] == result["query_plan"].plan_fingerprint

    @pytest.mark.parametrize(("make", "fixture", "parser_cls", "call_kwargs"), _CURSOR_CASES)
    def test_no_capture_when_disabled(self, make, fixture, parser_cls, call_kwargs, monkeypatch):
        adapter = make(monkeypatch)
        adapter.capture_plans = False
        boom = MagicMock(side_effect=AssertionError("EXPLAIN must not run when capture is disabled"))
        monkeypatch.setattr(adapter, "get_query_plan", boom)
        result = adapter.execute_query(_cursor_conn(), "SELECT 1", "q2", **call_kwargs)
        assert result["status"] == "SUCCESS"
        assert "query_plan" not in result

    @pytest.mark.parametrize(("make", "fixture", "parser_cls", "call_kwargs"), _CURSOR_CASES)
    def test_graceful_when_plan_unavailable(self, make, fixture, parser_cls, call_kwargs, monkeypatch):
        adapter = make(monkeypatch)
        monkeypatch.setattr(adapter, "get_query_plan", lambda *a, **k: None)
        result = adapter.execute_query(_cursor_conn(), "SELECT 1", "q3", **call_kwargs)
        assert result["status"] == "SUCCESS"
        assert "query_plan" not in result

    @pytest.mark.parametrize(("make", "fixture", "parser_cls", "call_kwargs"), _CURSOR_CASES)
    def test_strict_capture_failure_propagates(self, make, fixture, parser_cls, call_kwargs, monkeypatch):

        from benchbox.core.errors import PlanCaptureError

        adapter = make(monkeypatch)
        adapter.strict_plan_capture = True

        def boom(*a, **k):
            raise RuntimeError("EXPLAIN blew up")

        monkeypatch.setattr(adapter, "get_query_plan", boom)
        with pytest.raises(PlanCaptureError):
            adapter.execute_query(_cursor_conn(), "SELECT 1", "q_strict", **call_kwargs)


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
            return _DF([(_load("spark_explain_sample.txt"),)])
        return _DF([(1,)])


class TestLakeSailWiring:
    @pytest.fixture()
    def adapter(self, monkeypatch):
        adapter = _build("benchbox.platforms.lakesail", "LakeSailAdapter", monkeypatch)
        adapter.disable_cache = False
        return adapter

    def test_parser_is_spark(self, adapter):
        assert isinstance(adapter.get_query_plan_parser(), SparkQueryPlanParser)

    def test_execute_query_captures_plan(self, adapter, monkeypatch):
        monkeypatch.setattr(adapter, "get_query_plan", lambda *a, **k: _load("spark_explain_sample.txt"))
        result = adapter.execute_query(_FakeSpark(), "SELECT 1", "q1", validate_row_count=False)
        assert result["status"] == "SUCCESS"
        assert result["query_plan"] is not None
        assert result["plan_fingerprint"] == result["query_plan"].plan_fingerprint

    def test_no_capture_when_disabled(self, adapter):
        adapter.capture_plans = False
        spark = _FakeSpark()
        result = adapter.execute_query(spark, "SELECT 1", "q2", validate_row_count=False)
        assert result["status"] == "SUCCESS"
        assert "query_plan" not in result or result.get("query_plan") is None
        assert not any(q.strip().upper().startswith("EXPLAIN") for q in spark.queries)

    def test_graceful_when_plan_unavailable(self, adapter, monkeypatch):
        monkeypatch.setattr(adapter, "get_query_plan", lambda *a, **k: None)
        result = adapter.execute_query(_FakeSpark(), "SELECT 1", "q3", validate_row_count=False)
        assert result["status"] == "SUCCESS"
        assert "query_plan" not in result or result.get("query_plan") is None


def _make_bigquery(monkeypatch):
    return _build("benchbox.platforms.bigquery", "BigQueryAdapter", monkeypatch, project_id="proj")


class _FakeQueryJob:
    def __init__(self, query_plan):
        self.query_plan = query_plan


class TestBigQueryCapture:
    def test_parser_is_bigquery(self, monkeypatch):
        adapter = _make_bigquery(monkeypatch)
        assert isinstance(adapter.get_query_plan_parser(), BigQueryQueryPlanParser)

    def test_get_query_plan_returns_dry_run_cost_estimate(self, monkeypatch):
        import benchbox.platforms.bigquery as bq_module

        mock_bq = MagicMock()
        mock_bq.QueryJobConfig.return_value = MagicMock()
        monkeypatch.setattr(bq_module, "bigquery", mock_bq)
        adapter = _make_bigquery(monkeypatch)
        job = MagicMock(total_bytes_processed=1024)
        connection = MagicMock()
        connection.query.return_value = job

        result = adapter.get_query_plan(connection, "SELECT 1")

        assert result is not None
        assert result["bytes_processed"] == 1024
        assert "estimated_cost" in result
        connection.query.assert_called_once()

    def test_capture_query_plan_does_not_raise_attribute_error(self, monkeypatch):
        adapter = _make_bigquery(monkeypatch)
        plan, capture_ms = adapter.capture_query_plan(MagicMock(), "SELECT 1", "q0")
        assert plan is None
        assert capture_ms >= 0

    def test_execute_query_attaches_normalized_fingerprint(self, monkeypatch):
        import benchbox.platforms.bigquery as bq_module

        monkeypatch.setattr(bq_module, "bigquery", MagicMock())
        adapter = _make_bigquery(monkeypatch)
        adapter.normalize_plan_literals = True
        stages = json.loads(_load("bigquery_query_plan_sample.json"))

        job = MagicMock()
        job.result.return_value = [(1,)]
        job.total_bytes_processed = 123
        job.total_bytes_billed = 123
        job.slot_millis = 1
        job.created = None
        job.started = None
        job.ended = None
        job.job_id = "job-1"
        job.query_plan = stages

        conn = MagicMock()
        conn.query.return_value = job

        result = adapter.execute_query(conn, "SELECT 1", "q1", validate_row_count=False)
        assert result["status"] == "SUCCESS"
        assert result["query_plan"] is not None
        assert result["plan_fingerprint"] == result["query_plan"].plan_fingerprint
        assert result["plan_fingerprint_normalized"] == result["query_plan"].normalized_fingerprint

    def test_execute_query_omits_normalized_when_disabled(self, monkeypatch):
        import benchbox.platforms.bigquery as bq_module

        monkeypatch.setattr(bq_module, "bigquery", MagicMock())
        adapter = _make_bigquery(monkeypatch)
        adapter.normalize_plan_literals = False
        stages = json.loads(_load("bigquery_query_plan_sample.json"))

        job = MagicMock()
        job.result.return_value = [(1,)]
        job.total_bytes_processed = 123
        job.total_bytes_billed = 123
        job.slot_millis = 1
        job.created = None
        job.started = None
        job.ended = None
        job.job_id = "job-1"
        job.query_plan = stages

        conn = MagicMock()
        conn.query.return_value = job

        result = adapter.execute_query(conn, "SELECT 1", "q1", validate_row_count=False)
        assert result["status"] == "SUCCESS"
        assert "plan_fingerprint_normalized" not in result

    def test_capture_bq_plan_builds_dag(self, monkeypatch):
        adapter = _make_bigquery(monkeypatch)
        stages = json.loads(_load("bigquery_query_plan_sample.json"))
        plan, capture_ms = adapter._capture_bq_plan(_FakeQueryJob(stages), "q1")
        assert plan is not None
        assert plan.platform == "bigquery"
        assert plan.plan_fingerprint is not None
        assert capture_ms >= 0

    def test_capture_bq_plan_disabled_is_noop(self, monkeypatch):
        adapter = _make_bigquery(monkeypatch)
        adapter.capture_plans = False
        stages = json.loads(_load("bigquery_query_plan_sample.json"))
        plan, capture_ms = adapter._capture_bq_plan(_FakeQueryJob(stages), "q2")
        assert plan is None
        assert capture_ms == 0.0

    def test_capture_bq_plan_graceful_without_stages(self, monkeypatch):
        adapter = _make_bigquery(monkeypatch)
        plan, _ = adapter._capture_bq_plan(_FakeQueryJob([]), "q3")
        assert plan is None

        assert adapter.plan_capture_failures >= 1


def _snowflake_conn_with_cell(cell):

    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.fetchone.return_value = (cell,) if cell is not None else None
    return conn


class TestSnowflakeGetQueryPlanPassthrough:
    def test_str_cell_passes_through(self, monkeypatch):
        adapter = _make_snowflake(monkeypatch)
        raw = _load("snowflake_explain_sample.json")
        assert adapter.get_query_plan(_snowflake_conn_with_cell(raw), "SELECT 1") == raw

    def test_null_row_returns_none(self, monkeypatch):
        adapter = _make_snowflake(monkeypatch)
        assert adapter.get_query_plan(_snowflake_conn_with_cell(None), "SELECT 1") is None


