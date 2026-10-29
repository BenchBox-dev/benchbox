from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from benchbox.core.errors import PlanCaptureError
from benchbox.core.results.query_plan_models import QueryPlanDAG
from benchbox.platforms.databricks import dataframe_adapter as mod
from tests.unit.platforms.dataframe.spark_plan_fixture import FakeSparkFrame

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.cloud_import,
]


def _parent_init(self, **config):
    self.server_hostname = config.get("server_hostname")
    self.catalog = "main"
    self.schema = "bench"
    self.logger = logging.getLogger("test.databricks.plan")
    self.log_verbose = lambda *_a, **_k: None
    self.log_very_verbose = lambda *_a, **_k: None
    self.capture_plans = config.get("capture_plans", True)
    self.normalize_plan_literals = config.get("normalize_plan_literals", False)
    self.strict_plan_capture = config.get("strict_plan_capture", False)
    self.plan_query_filter = config.get("plan_query_filter", set())
    self.plan_capture_timeout_seconds = 30
    self.plan_capture_failures = 0
    self.plan_capture_errors = []
    self.query_plans_captured = 0
    self.platform_config = {}


def _adapter(monkeypatch: pytest.MonkeyPatch, frame, **config) -> mod.DatabricksDataFrameAdapter:
    with (
        patch.object(mod.DatabricksAdapter, "__init__", _parent_init),
        patch.object(mod, "DATABRICKS_CONNECT_AVAILABLE", True),
    ):
        adapter = mod.DatabricksDataFrameAdapter(
            server_hostname="host", http_path="/p", access_token="t", execution_mode="dataframe", **config
        )

    class _Catalog:
        def setCurrentCatalog(self, _c):
            return None

        def setCurrentDatabase(self, _d):
            return None

    class _Spark:
        catalog = _Catalog()

    monkeypatch.setattr(adapter, "_get_or_create_spark_session", lambda: _Spark())
    adapter._frame = frame
    return adapter


def _execute(adapter, builder=None):
    return adapter.execute_query(
        connection=None,
        query=builder or (lambda _spark, _tables: adapter._frame),
        query_id="Q1",
        validate_row_count=False,
    )


def test_callable_query_captures_dag_plan_with_fingerprint(monkeypatch):
    adapter = _adapter(monkeypatch, FakeSparkFrame())

    result = _execute(adapter)

    assert result["status"] == "SUCCESS"
    assert isinstance(result["query_plan"], QueryPlanDAG)
    assert result["plan_fingerprint"] == result["query_plan"].plan_fingerprint
    assert "plan_fingerprint_normalized" not in result
    assert result["plan_capture_time_ms"] > 0
    assert adapter.query_plans_captured == 1
    assert adapter.plan_capture_failures == 0


def test_callable_query_adds_normalized_fingerprint_when_requested(monkeypatch):
    adapter = _adapter(monkeypatch, FakeSparkFrame(), normalize_plan_literals=True)

    result = _execute(adapter)

    assert result["plan_fingerprint_normalized"] == result["query_plan"].normalized_fingerprint


def test_capture_time_is_not_part_of_execution_time(monkeypatch):
    import time

    frame = FakeSparkFrame()
    original = frame.explain

    def slow_explain(extended=False):
        time.sleep(0.2)
        original(extended=extended)

    frame.explain = slow_explain
    adapter = _adapter(monkeypatch, frame)

    result = _execute(adapter)

    assert result["plan_capture_time_ms"] >= 200
    assert result["execution_time_seconds"] < 0.1


def test_capture_disabled_leaves_row_without_plan_fields(monkeypatch):
    adapter = _adapter(monkeypatch, FakeSparkFrame(), capture_plans=False)

    result = _execute(adapter)

    assert "query_plan" not in result
    assert "plan_capture_time_ms" not in result


def test_plan_query_filter_skips_unselected_queries(monkeypatch):
    adapter = _adapter(monkeypatch, FakeSparkFrame(), plan_query_filter={"Q9"})

    result = _execute(adapter)

    assert "query_plan" not in result
    assert adapter.plan_capture_failures == 0


def test_explain_failure_is_recorded_without_failing_the_query(monkeypatch):
    frame = FakeSparkFrame()
    frame.explain = lambda extended=False: (_ for _ in ()).throw(RuntimeError("explain exploded"))
    adapter = _adapter(monkeypatch, frame)

    result = _execute(adapter)

    assert result["status"] == "SUCCESS"
    assert "query_plan" not in result
    assert adapter.plan_capture_failures == 1
    assert adapter.plan_capture_errors[0]["reason"] == "explain_failed"
    assert "explain exploded" in adapter.plan_capture_errors[0]["message"]


def test_strict_capture_propagates_failure(monkeypatch):
    frame = FakeSparkFrame("")
    adapter = _adapter(monkeypatch, frame, strict_plan_capture=True)

    with pytest.raises(PlanCaptureError):
        _execute(adapter)


def test_failed_query_does_not_attempt_capture(monkeypatch):
    adapter = _adapter(monkeypatch, FakeSparkFrame())

    result = _execute(adapter, builder=lambda *_a: (_ for _ in ()).throw(RuntimeError("boom")))

    assert result["status"] == "FAILED"
    assert adapter.plan_capture_failures == 0
