from __future__ import annotations

import logging
import threading
import time
from typing import Any

import pytest

from benchbox.core.errors import PlanCaptureError
from benchbox.core.plan_capture_phase import run_plan_capture_phase
from benchbox.platforms.base.adapter import PlatformAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class DummyAdapter(PlatformAdapter):
    def __init__(
        self,
        explain_output: Any = "PLAN",
        parser: Any = None,
        explain_error: Exception | None = None,
        explain_delay_seconds: float = 0,
        **config,
    ):
        self._explain_output = explain_output
        self._parser = parser
        self._explain_error = explain_error
        self._explain_delay_seconds = explain_delay_seconds
        super().__init__(**config)

    @staticmethod
    def add_cli_arguments(parser) -> None:
        return None

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        return cls(**config)

    def create_connection(self, **connection_config) -> Any:
        return None

    def create_schema(self, benchmark, connection: Any) -> float:
        return 0.0

    def get_target_dialect(self) -> str:
        return "generic"

    def apply_platform_optimizations(self, platform_config, connection: Any) -> None:
        return None

    def apply_constraint_configuration(self, primary_key_config, foreign_key_config, connection: Any) -> None:
        return None

    def load_data(self, benchmark, connection: Any, data_dir):
        return {}, 0.0, None

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        return None

    def execute_query(
        self,
        connection: Any,
        query: str,
        query_id: str,
        benchmark_type: str | None = None,
        scale_factor: float | None = None,
        validate_row_count: bool = True,
        stream_id: int | None = None,
    ) -> dict[str, Any]:
        return {}

    def get_query_plan(self, connection: Any, query: str) -> Any:
        if self._explain_delay_seconds > 0:
            time.sleep(self._explain_delay_seconds)
        if self._explain_error:
            raise self._explain_error
        return self._explain_output

    def get_query_plan_parser(self):
        return self._parser


class _QuickParser:
    platform_name = "test"

    def parse_explain_output(self, query_id: str, explain_output: str):
        from benchbox.core.results.query_plan_models import (
            LogicalOperator,
            LogicalOperatorType,
            QueryPlanDAG,
        )

        return QueryPlanDAG(
            query_id=query_id,
            platform="test",
            logical_root=LogicalOperator(
                operator_type=LogicalOperatorType.SCAN,
                operator_id="scan_1",
            ),
        )


def test_parser_unavailable_records_warning(caplog: pytest.LogCaptureFixture) -> None:
    adapter = DummyAdapter(capture_plans=True)

    caplog.set_level(logging.WARNING)
    plan, capture_time_ms = adapter.capture_query_plan(None, "SELECT 1", "q1")

    assert plan is None
    assert capture_time_ms >= 0
    assert adapter.query_plans_captured == 0
    assert adapter.plan_capture_failures == 1
    assert adapter.plan_capture_errors[0]["reason"] == "parser_unavailable"
    assert any("Query plan capture disabled" in record.message for record in caplog.records)


def test_strict_plan_capture_raises_error() -> None:
    adapter = DummyAdapter(
        capture_plans=True,
        strict_plan_capture=True,
        explain_error=RuntimeError("boom"),
    )

    with pytest.raises(PlanCaptureError) as excinfo:
        adapter.capture_query_plan(None, "SELECT 1", "q2")

    assert "boom" in str(excinfo.value)
    assert adapter.plan_capture_failures == 1
    assert adapter.plan_capture_errors[0]["reason"] == "explain_failed"


def test_plan_capture_failure_is_graceful_when_not_strict() -> None:
    adapter = DummyAdapter(capture_plans=True, explain_output="")

    plan, capture_time_ms = adapter.capture_query_plan(None, "SELECT 1", "q3")

    assert plan is None
    assert capture_time_ms >= 0
    assert adapter.plan_capture_failures == 1
    assert adapter.plan_capture_errors[0]["reason"] == "explain_failed"


def test_plan_capture_timeout_records_failure(caplog: pytest.LogCaptureFixture) -> None:
    adapter = DummyAdapter(
        capture_plans=True,
        explain_delay_seconds=2.0,
        plan_capture_timeout_seconds=1,
    )

    caplog.set_level(logging.WARNING)
    plan, capture_time_ms = adapter.capture_query_plan(None, "SELECT 1", "q4")

    assert plan is None
    assert capture_time_ms >= 1000
    assert adapter.query_plans_captured == 0
    assert adapter.plan_capture_failures == 1
    assert adapter.plan_capture_errors[0]["reason"] == "timeout"
    assert "timed out" in adapter.plan_capture_errors[0]["message"]
    assert any("timed out" in record.message for record in caplog.records)


def test_capture_timeout_returns_promptly() -> None:
    release_explain = threading.Event()
    adapter = DummyAdapter(capture_plans=True, plan_capture_timeout_seconds=1)

    def hanging_explain(connection: Any, query: str) -> str:
        release_explain.wait(timeout=30)
        return "PLAN"

    adapter.get_query_plan = hanging_explain

    start = time.monotonic()
    try:
        plan, capture_time_ms = adapter.capture_query_plan(None, "SELECT 1", "q_hang")
        elapsed = time.monotonic() - start
    finally:
        release_explain.set()

    assert plan is None
    assert elapsed < 5, f"capture_query_plan blocked for {elapsed:.1f}s after timeout - blocking-shutdown regression"
    assert capture_time_ms >= 1000
    assert adapter.plan_capture_failures == 1
    assert adapter.plan_capture_errors[0]["reason"] == "timeout"


def test_capture_timeout_strict_mode_raises_promptly() -> None:
    release_explain = threading.Event()
    adapter = DummyAdapter(
        capture_plans=True,
        plan_capture_timeout_seconds=1,
        strict_plan_capture=True,
    )

    def hanging_explain(connection: Any, query: str) -> str:
        release_explain.wait(timeout=30)
        return "PLAN"

    adapter.get_query_plan = hanging_explain

    start = time.monotonic()
    try:
        with pytest.raises(PlanCaptureError):
            adapter.capture_query_plan(None, "SELECT 1", "q_hang_strict")
        elapsed = time.monotonic() - start
    finally:
        release_explain.set()

    assert elapsed < 5, f"strict-mode timeout blocked for {elapsed:.1f}s - blocking-shutdown regression"
    assert adapter.plan_capture_errors[0]["reason"] == "timeout"


def test_plan_capture_completes_within_timeout() -> None:

    adapter = DummyAdapter(
        capture_plans=True,
        explain_output="EXPLAIN PLAN",
        explain_delay_seconds=0.1,
        plan_capture_timeout_seconds=5,
        parser=_QuickParser(),
    )

    plan, capture_time_ms = adapter.capture_query_plan(None, "SELECT 1", "q5")

    assert plan is not None
    assert capture_time_ms >= 100
    assert adapter.query_plans_captured == 1
    assert adapter.plan_capture_failures == 0


def test_plan_capture_returns_zero_time_when_disabled() -> None:
    adapter = DummyAdapter(capture_plans=False)

    plan, capture_time_ms = adapter.capture_query_plan(None, "SELECT 1", "q6")

    assert plan is None
    assert capture_time_ms == 0.0


def test_capture_phase_replaces_owned_connection_after_timeout() -> None:
    release_explain = threading.Event()
    created_connections: list[object] = []
    capture_connections: list[object] = []

    class FencedAdapter(DummyAdapter):
        def create_connection(self, **connection_config) -> Any:
            connection = object()
            created_connections.append(connection)
            return connection

        def get_query_plan(self, connection: Any, query: str) -> Any:
            capture_connections.append(connection)
            if query == "SELECT slow":
                release_explain.wait(timeout=30)
                return "SLOW PLAN"
            if connection is created_connections[0]:
                raise AssertionError("timed-out connection was reused for a later capture")
            return "FAST PLAN"

    adapter = FencedAdapter(
        capture_plans=True,
        plan_capture_timeout_seconds=1,
        parser=_QuickParser(),
    )

    try:
        result = run_plan_capture_phase(
            adapter,
            [("slow", "SELECT slow"), ("fast", "SELECT fast")],
        )
    finally:
        release_explain.set()

    assert result.failed == 1
    assert result.captured == 1
    assert set(result.plans) == {"fast"}
    assert len(created_connections) == 2
    assert capture_connections[:2] == [created_connections[0], created_connections[1]]


def test_capture_phase_skips_remaining_on_caller_connection_timeout() -> None:
    release_explain = threading.Event()
    original_connection = object()
    created_connections: list[object] = []
    capture_connections: list[object] = []

    class FencedAdapter(DummyAdapter):
        def create_connection(self, **connection_config) -> Any:
            created_connections.append(object())
            raise AssertionError("caller-supplied connection must not be reopened after a timeout")

        def get_query_plan(self, connection: Any, query: str) -> Any:
            capture_connections.append(connection)
            if query == "SELECT slow":
                release_explain.wait(timeout=30)
                return "SLOW PLAN"
            return "FAST PLAN"

    adapter = FencedAdapter(
        capture_plans=True,
        plan_capture_timeout_seconds=1,
        parser=_QuickParser(),
    )

    try:
        result = run_plan_capture_phase(
            adapter,
            [("slow", "SELECT slow"), ("fast", "SELECT fast")],
            connection=original_connection,
        )
    finally:
        release_explain.set()

    assert result.captured == 0
    assert result.failed == 2
    assert result.plans == {}
    assert created_connections == []
    assert capture_connections == [original_connection]


def test_plan_query_filter_only_captures_specified_queries() -> None:

    class SimpleParser:
        platform_name = "test"

        def parse_explain_output(self, query_id: str, explain_output: str):
            from benchbox.core.results.query_plan_models import (
                LogicalOperator,
                LogicalOperatorType,
                QueryPlanDAG,
            )

            return QueryPlanDAG(
                query_id=query_id,
                platform="test",
                logical_root=LogicalOperator(
                    operator_type=LogicalOperatorType.SCAN,
                    operator_id="scan_1",
                ),
            )

    adapter = DummyAdapter(
        capture_plans=True,
        explain_output="EXPLAIN PLAN",
        parser=SimpleParser(),
        plan_queries="q01,q02",
    )

    plan1, time1 = adapter.capture_query_plan(None, "SELECT 1", "q01")
    assert plan1 is not None

    plan3, time3 = adapter.capture_query_plan(None, "SELECT 1", "q03")
    assert plan3 is None
    assert time3 == 0.0


def test_plan_query_filter_only_captures_selected_queries() -> None:

    class SimpleParser:
        platform_name = "test"

        def parse_explain_output(self, query_id: str, explain_output: str):
            from benchbox.core.results.query_plan_models import (
                LogicalOperator,
                LogicalOperatorType,
                QueryPlanDAG,
            )

            return QueryPlanDAG(
                query_id=query_id,
                platform="test",
                logical_root=LogicalOperator(
                    operator_type=LogicalOperatorType.SCAN,
                    operator_id="scan_1",
                ),
            )

    adapter = DummyAdapter(
        capture_plans=True,
        explain_output="EXPLAIN PLAN",
        parser=SimpleParser(),
        plan_queries="q01",
    )

    plan1, _ = adapter.capture_query_plan(None, "SELECT 1", "q01")
    assert plan1 is not None
    plan2, _ = adapter.capture_query_plan(None, "SELECT 1", "q01")
    assert plan2 is not None

    plan3, time3 = adapter.capture_query_plan(None, "SELECT 1", "q02")
    assert plan3 is None
    assert time3 == 0.0


class _FakeCursor:
    def __init__(self, error: Exception) -> None:
        self._error = error
        self.closed = False

    def execute(self, _sql: str) -> None:
        raise self._error

    def fetchall(self):
        return []

    def close(self) -> None:
        self.closed = True


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _FakeCursor:
        return self._cursor


def test_get_query_plan_from_cursor_returns_none_and_logs_on_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from benchbox.platforms.base.sql_execution import get_query_plan_from_cursor

    cursor = _FakeCursor(RuntimeError("EXPLAIN blew up"))
    connection = _FakeConnection(cursor)

    with caplog.at_level(logging.WARNING, logger="benchbox.platforms.base.sql_execution"):
        result = get_query_plan_from_cursor(connection, "SELECT 1")

    assert result is None
    assert cursor.closed is True
    assert any("EXPLAIN blew up" in rec.getMessage() for rec in caplog.records)
    assert result is None or "Could not get query plan" not in result


def test_get_query_plan_from_cursor_returns_joined_rows_on_success() -> None:
    from benchbox.platforms.base.sql_execution import get_query_plan_from_cursor

    class _OkCursor:
        def __init__(self) -> None:
            self.closed = False

        def execute(self, _sql: str) -> None:
            pass

        def fetchall(self):
            return [("row one",), ("row two",)]

        def close(self) -> None:
            self.closed = True

    cursor = _OkCursor()

    class _OkConnection:
        def cursor(self) -> _OkCursor:
            return cursor

    result = get_query_plan_from_cursor(_OkConnection(), "SELECT 1")

    assert result == "row one\nrow two"
    assert cursor.closed is True
