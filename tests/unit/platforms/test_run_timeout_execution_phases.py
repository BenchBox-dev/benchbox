from __future__ import annotations

import concurrent.futures
import inspect
import logging
import threading
from collections import defaultdict
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest

from benchbox.core.throughput import runner as throughput_runner
from benchbox.core.throughput.result import ThroughputResult
from benchbox.core.throughput.runner import StreamRunner
from benchbox.core.tpcds import maintenance_test as tpcds_maintenance, power_test as tpcds_power
from benchbox.core.tpcds.maintenance_functions import MAINTENANCE_FUNCTIONS
from benchbox.core.tpcds.platform_power import execute_tpcds_power_test
from benchbox.core.tpcds.throughput_test import TPCDSThroughputTest, TPCDSThroughputTestConfig
from benchbox.core.tpch import maintenance_test as tpch_maintenance, power_test as tpch_power
from benchbox.core.tpch.platform_power import execute_tpch_power_test
from benchbox.core.tpch.throughput_test import TPCHThroughputTestConfig
from benchbox.platforms.base import execution
from benchbox.platforms.base.connection_wrappers import StreamConnectionCapability
from benchbox.platforms.base.execution import TestDriversMixin
from benchbox.platforms.dataframe import tpch_full_spec
from benchbox.platforms.dataframe.benchmark_mixin import BenchmarkExecutionMixin
from benchbox.utils import run_deadline
from benchbox.utils.run_deadline import RunDeadline, active_run_deadline, limit_to_remaining
from tests.unit.platforms.test_base_adapter import MockPlatformAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

QUERIES = {"1": "SELECT 1", "2": "SELECT 2", "3": "SELECT 3"}


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(run_deadline, "mono_time", lambda: fake.now)
    monkeypatch.setattr(run_deadline, "elapsed_seconds", lambda started: fake.now - started)
    return fake


def _deadline(limit: int = 10) -> RunDeadline:
    deadline = RunDeadline(limit)
    deadline.enter_phase("execute")
    return deadline


def _benchmark() -> Mock:
    benchmark = Mock()
    benchmark.get_queries.return_value = dict(QUERIES)
    benchmark.get_platform_skip_queries.return_value = []
    return benchmark


class SlowConnection:
    def __init__(self, clock: FakeClock, events: list[str] | None = None, fail_on_call: int | None = None) -> None:
        self.clock = clock
        self.events = events if events is not None else []
        self.fail_on_call = fail_on_call
        self.calls = 0

    def execute(self, sql: str) -> Any:
        self.calls += 1
        self.events.append(f"start:{sql}")
        self.clock.now += 6
        self.events.append(f"end:{sql}")
        if self.calls == self.fail_on_call:
            raise RuntimeError("cancelled by the platform")
        return SimpleNamespace(rowcount=1, fetchall=lambda: [(1,)])

    def close(self) -> None:
        return None

    def commit(self) -> None:
        return None


def _tpch_power(monkeypatch, connection: Any, iterations: int = 1, warm_ups: int = 0) -> list[dict[str, Any]]:
    monkeypatch.setattr(tpch_power.TPCHPowerTest, "_generate_query", lambda self, query_id, seed: f"q{query_id}")
    monkeypatch.setattr(tpch_power.TPCHPowerTest, "_preflight_validate_generation", lambda self, permutation: None)
    return execute_tpch_power_test(
        SimpleNamespace(get_target_dialect=lambda: "standard"),
        Mock(),
        Mock(),
        {
            "query_subset": ["1", "2", "3"],
            "iterations": iterations,
            "warm_up_iterations": warm_ups,
            "scale_factor": 1.0,
            "seed": 1,
        },
        make_connection_adapter=lambda _connection, _benchmark_id, _scale_factor: connection,
        console=Mock(),
    )


def _tpcds_power(monkeypatch, connection: Any, iterations: int = 1, warm_ups: int = 0) -> list[dict[str, Any]]:
    monkeypatch.setattr(tpcds_power.TPCDSPowerTest, "_determine_available_query_ids", lambda self: [1, 2, 3])
    monkeypatch.setattr(
        tpcds_power.TPCDSPowerTest,
        "_build_queries_to_execute",
        lambda self, available: [(1, None), (2, None), (3, None)],
    )
    monkeypatch.setattr(tpcds_power.TPCDSPowerTest, "_preflight_validate_generation", lambda self, available: None)
    monkeypatch.setattr(
        tpcds_power.TPCDSPowerTest, "_generate_query", lambda self, query_id, variant, seed: f"q{query_id}"
    )
    return execute_tpcds_power_test(
        SimpleNamespace(get_target_dialect=lambda: "standard"),
        Mock(),
        Mock(),
        {"iterations": iterations, "warm_up_iterations": warm_ups, "scale_factor": 1.0},
        make_connection_adapter=lambda _connection, _benchmark_id, _scale_factor: connection,
        console=Mock(),
    )


def _statuses(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(str(row["query_id"]), row["status"]) for row in rows]


class TestPowerTestsStopAtTheDeadline:
    def test_tpch_power_stops_before_the_next_query_and_marks_the_one_in_flight(self, monkeypatch, clock) -> None:
        connection = SlowConnection(clock, fail_on_call=2)
        deadline = _deadline()

        with active_run_deadline(deadline):
            rows = _tpch_power(monkeypatch, connection)

        assert _statuses(rows) == [("1", "SUCCESS"), ("2", "TIMEOUT")]
        assert connection.calls == 2
        assert (deadline.timeout.phase, deadline.timeout.query_id) == ("execute", "2")

    def test_tpch_power_does_not_start_the_next_iteration_after_the_deadline(self, monkeypatch, clock) -> None:
        connection = SlowConnection(clock)
        deadline = _deadline(10)

        with active_run_deadline(deadline):
            rows = _tpch_power(monkeypatch, connection, iterations=3, warm_ups=1)

        assert _statuses(rows) == [("1", "SUCCESS"), ("2", "SUCCESS")]
        assert connection.calls == 2
        assert {row["run_type"] for row in rows} == {"warmup"}
        assert deadline.timeout.query_id == "3"

    def test_tpch_power_adds_no_failure_row_when_the_deadline_stops_it_before_any_query(
        self, monkeypatch, clock
    ) -> None:
        connection = SlowConnection(clock)
        deadline = _deadline(10)
        clock.now = 11

        with active_run_deadline(deadline):
            rows = _tpch_power(monkeypatch, connection)

        assert rows == []
        assert connection.calls == 0

    def test_tpcds_power_stops_before_the_next_query_and_marks_the_one_in_flight(self, monkeypatch, clock) -> None:
        connection = SlowConnection(clock, fail_on_call=2)
        deadline = _deadline()

        with active_run_deadline(deadline):
            rows = _tpcds_power(monkeypatch, connection)

        assert _statuses(rows) == [("1", "SUCCESS"), ("2", "TIMEOUT")]
        assert connection.calls == 2
        assert (deadline.timeout.phase, deadline.timeout.query_id) == ("execute", "2")

    def test_tpcds_power_does_not_start_the_next_iteration_after_the_deadline(self, monkeypatch, clock) -> None:
        connection = SlowConnection(clock)
        deadline = _deadline(10)

        with active_run_deadline(deadline):
            rows = _tpcds_power(monkeypatch, connection, iterations=3)

        assert _statuses(rows) == [("1", "SUCCESS"), ("2", "SUCCESS")]
        assert connection.calls == 2

    def test_a_failed_query_is_a_plain_failure_while_the_deadline_has_not_passed(self, monkeypatch, clock) -> None:
        connection = SlowConnection(clock, fail_on_call=1)

        with active_run_deadline(_deadline(600)):
            rows = _tpch_power(monkeypatch, connection)

        assert [row["status"] for row in rows] == ["FAILED", "SUCCESS", "SUCCESS"]

    def test_generic_power_marks_the_query_in_flight_and_leaves_later_ones_out(self, clock) -> None:
        adapter = MockPlatformAdapter()
        calls: list[str] = []

        def slow_query(_benchmark: Any, _connection: Any, query_id: Any, _sql: str, **_kwargs: Any) -> dict[str, Any]:
            calls.append(str(query_id))
            clock.now += 6
            status = "FAILED" if len(calls) == 2 else "SUCCESS"
            return {"query_id": str(query_id), "status": status, "execution_time_seconds": 6.0, "rows_returned": 1}

        adapter._execute_single_query = slow_query

        with active_run_deadline(_deadline()):
            rows = adapter._execute_generic_power_test(
                _benchmark(),
                Mock(),
                {"benchmark_name": "tpch", "iterations": 3, "warm_up_iterations": 1, "scale_factor": 1.0},
            )

        assert calls == ["1", "2"]
        assert _statuses(rows) == [("1", "SUCCESS"), ("2", "TIMEOUT")]

    def test_generic_power_marks_a_raised_query_as_timeout(self, clock) -> None:
        adapter = MockPlatformAdapter()

        def failing_query(
            _benchmark: Any, _connection: Any, query_id: Any, _sql: str, **_kwargs: Any
        ) -> dict[str, Any]:
            clock.now += 11
            raise RuntimeError("cancelled")

        adapter._execute_single_query = failing_query

        with active_run_deadline(_deadline()):
            rows = adapter._execute_all_queries(_benchmark(), Mock(), {"benchmark_name": "tpch"})

        assert _statuses(rows) == [("1", "TIMEOUT")]


class TestNoDeadlineCheckInsideATimedSection:
    def _record(self, monkeypatch, module: Any, name: str, events: list[str]) -> None:
        real = getattr(module, name)

        def recording(*args: Any, **kwargs: Any) -> Any:
            events.append("check")
            return real(*args, **kwargs)

        monkeypatch.setattr(module, name, recording)

    def test_tpch_power_checks_only_between_queries(self, monkeypatch, clock) -> None:
        events: list[str] = []
        self._record(monkeypatch, tpch_power, "stop_requested_before_query", events)
        self._record(monkeypatch, tpch_power, "failed_query_status", events)

        with active_run_deadline(_deadline(600)):
            _tpch_power(monkeypatch, SlowConnection(clock, events))

        assert events == ["check", "start:q1", "end:q1", "check", "start:q2", "end:q2", "check", "start:q3", "end:q3"]

    def test_tpcds_power_checks_only_between_queries(self, monkeypatch, clock) -> None:
        events: list[str] = []
        self._record(monkeypatch, tpcds_power, "stop_requested_before_query", events)
        self._record(monkeypatch, tpcds_power, "failed_query_status", events)

        with active_run_deadline(_deadline(600)):
            _tpcds_power(monkeypatch, SlowConnection(clock, events))

        assert events == ["check", "start:q1", "end:q1", "check", "start:q2", "end:q2", "check", "start:q3", "end:q3"]

    def test_a_failure_is_classified_after_the_query_ended(self, monkeypatch, clock) -> None:
        events: list[str] = []
        self._record(monkeypatch, tpch_power, "stop_requested_before_query", events)
        self._record(monkeypatch, tpch_power, "failed_query_status", events)

        with active_run_deadline(_deadline(600)):
            _tpch_power(monkeypatch, SlowConnection(clock, events, fail_on_call=1))

        assert events[:4] == ["check", "start:q1", "end:q1", "check"]
        assert events[4] == "check"


def _tpch_maintenance_ops(monkeypatch, clock: FakeClock, fail_rf: str | None = None) -> list[str]:
    calls: list[str] = []

    def timed(name: str) -> Any:
        def apply(*_args: Any, **_kwargs: Any) -> Any:
            calls.append(name)
            clock.now += 6
            if fail_rf == name:
                raise RuntimeError("cancelled")
            return SimpleNamespace(rows=1, orders=1) if name == "RF2" else 1

        return apply

    monkeypatch.setattr(tpch_maintenance, "record_refresh_applied", lambda connection: None)
    monkeypatch.setattr(tpch_maintenance, "apply_rf1", timed("RF1"))
    monkeypatch.setattr(tpch_maintenance, "check_rf1_applied", lambda *args, **kwargs: None)
    monkeypatch.setattr(tpch_maintenance, "capture_rf2_baseline", lambda *args, **kwargs: None)
    monkeypatch.setattr(tpch_maintenance, "rf2_precount", lambda *args, **kwargs: None)
    monkeypatch.setattr(tpch_maintenance, "apply_rf2", timed("RF2"))
    monkeypatch.setattr(tpch_maintenance, "verify_rf2_deletes", lambda *args, **kwargs: None)
    monkeypatch.setattr(tpch_maintenance, "check_rf2_applied", lambda *args, **kwargs: None)
    return calls


def _run_tpch_maintenance(pairs: int = 3) -> tpch_maintenance.TPCHMaintenanceTestResult:
    test = tpch_maintenance.TPCHMaintenanceTest(connection_factory=lambda: Mock(), first_set=1)
    config = tpch_maintenance.TPCHMaintenanceTestConfig(maintenance_pairs=pairs)
    result = tpch_maintenance.TPCHMaintenanceTestResult(
        config=config,
        start_time="",
        end_time="",
        total_time=0.0,
        rf1_operations=0,
        rf2_operations=0,
        total_operations=0,
        successful_operations=0,
        failed_operations=0,
    )
    update_sets = {index: SimpleNamespace(set_id=index) for index in range(1, pairs + 1)}
    return test._run_maintenance_pairs(result, update_sets, pairs, 0.0, 0.0)


class TestMaintenanceTestsStopAtTheDeadline:
    def test_tpch_maintenance_stops_between_operations(self, monkeypatch, clock) -> None:
        calls = _tpch_maintenance_ops(monkeypatch, clock)
        deadline = _deadline(10)

        with active_run_deadline(deadline):
            result = _run_tpch_maintenance()

        assert calls == ["RF1", "RF2"]
        assert [operation.operation_type for operation in result.operations] == ["RF1", "RF2"]
        assert result.stopped_at_time_limit is True
        assert result.success is False
        assert (deadline.timeout.phase, deadline.timeout.query_id) == ("execute", "RF1")

    def test_tpch_maintenance_marks_the_operation_in_flight(self, monkeypatch, clock) -> None:
        calls = _tpch_maintenance_ops(monkeypatch, clock, fail_rf="RF2")

        with active_run_deadline(_deadline(10)):
            result = _run_tpch_maintenance()

        assert calls == ["RF1", "RF2"]
        assert [operation.timed_out for operation in result.operations] == [False, True]

    def test_tpch_maintenance_failure_before_the_deadline_is_not_a_timeout(self, monkeypatch, clock) -> None:
        _tpch_maintenance_ops(monkeypatch, clock, fail_rf="RF1")

        with active_run_deadline(_deadline(600)):
            result = _run_tpch_maintenance()

        assert [operation.timed_out for operation in result.operations] == [False]
        assert result.stopped_at_time_limit is False

    def test_tpch_maintenance_sleeps_no_longer_than_the_time_remaining(self, monkeypatch, clock) -> None:
        slept: list[float] = []
        monkeypatch.setattr(tpch_maintenance, "sleep", slept.append)
        _tpch_maintenance_ops(monkeypatch, clock)
        test = tpch_maintenance.TPCHMaintenanceTest(connection_factory=lambda: Mock(), first_set=1)
        result = tpch_maintenance.TPCHMaintenanceTestResult(
            config=tpch_maintenance.TPCHMaintenanceTestConfig(),
            start_time="",
            end_time="",
            total_time=0.0,
            rf1_operations=0,
            rf2_operations=0,
            total_operations=0,
            successful_operations=0,
            failed_operations=0,
        )

        with active_run_deadline(_deadline(100)):
            test._run_maintenance_pairs(result, {1: SimpleNamespace(set_id=1)}, 1, 500.0, 0.0)

        assert slept == [pytest.approx(94.0)]

    def _tpcds(self, monkeypatch, clock: FakeClock, fail_on_call: int | None = None) -> tuple[Any, list[str]]:
        calls: list[str] = []
        test = tpcds_maintenance.TPCDSMaintenanceTest(benchmark=Mock(), connection_factory=lambda: Mock())

        def operation(function: Any, refresh_set: Any) -> Any:
            calls.append(function.name)
            clock.now += 6
            return tpcds_maintenance.TPCDSMaintenanceOperation(
                operation_type=function.operation_type,
                table_name="t",
                start_time=0.0,
                end_time=1.0,
                duration=6.0,
                rows_affected=1,
                success=True,
                function=function.name,
            )

        monkeypatch.setattr(test, "_execute_function", operation)
        return test, calls

    def _tpcds_result(self) -> dict[str, Any]:
        return {
            "refresh_sets": [],
            "operations": [],
            "total_operations": 0,
            "insert_operations": 0,
            "delete_operations": 0,
            "successful_operations": 0,
            "failed_operations": 0,
            "errors": [],
            "stopped_at_time_limit": False,
            "total_time": 0.0,
            "overall_throughput": 0.0,
        }

    def test_tpcds_maintenance_stops_between_functions(self, monkeypatch, clock) -> None:
        test, calls = self._tpcds(monkeypatch, clock)
        result = self._tpcds_result()
        refresh_sets = {1: SimpleNamespace(set_id=1), 2: SimpleNamespace(set_id=2)}
        deadline = _deadline(10)

        with active_run_deadline(deadline):
            test._run_refresh_runs(result, refresh_sets)

        assert calls == [MAINTENANCE_FUNCTIONS[0].name, MAINTENANCE_FUNCTIONS[1].name]
        assert result["stopped_at_time_limit"] is True
        assert deadline.timeout.query_id == MAINTENANCE_FUNCTIONS[2].name

    def test_tpcds_maintenance_marks_the_function_in_flight(self, clock) -> None:
        def slow_connection() -> Any:
            clock.now += 11
            raise RuntimeError("cancelled")

        test = tpcds_maintenance.TPCDSMaintenanceTest(benchmark=Mock(), connection_factory=slow_connection)
        refresh_set = SimpleNamespace(set_id=1, row_counts=defaultdict(int), path_for=lambda name: name)

        with active_run_deadline(_deadline(10)):
            operation = test._execute_function(MAINTENANCE_FUNCTIONS[0], refresh_set)

        assert operation.success is False
        assert operation.timed_out is True

    def test_tpcds_maintenance_failure_before_the_deadline_is_not_a_timeout(self, clock) -> None:
        def failing_connection() -> Any:
            raise RuntimeError("boom")

        test = tpcds_maintenance.TPCDSMaintenanceTest(benchmark=Mock(), connection_factory=failing_connection)
        refresh_set = SimpleNamespace(set_id=1, row_counts=defaultdict(int), path_for=lambda name: name)

        with active_run_deadline(_deadline(600)):
            operation = test._execute_function(MAINTENANCE_FUNCTIONS[0], refresh_set)

        assert operation.timed_out is False

    def test_tpcds_maintenance_checks_only_between_functions(self, monkeypatch, clock) -> None:
        events: list[str] = []
        test, _calls = self._tpcds(monkeypatch, clock)
        real_check = tpcds_maintenance.stop_requested_before_query

        def recording_check(query_id: Any) -> bool:
            events.append(f"check:{query_id}")
            return real_check(query_id)

        monkeypatch.setattr(tpcds_maintenance, "stop_requested_before_query", recording_check)
        original = test._execute_function

        def recording_function(function: Any, refresh_set: Any) -> Any:
            events.append(f"start:{function.name}")
            outcome = original(function, refresh_set)
            events.append(f"end:{function.name}")
            return outcome

        monkeypatch.setattr(test, "_execute_function", recording_function)

        with active_run_deadline(_deadline(1000)):
            test._run_refresh_runs(self._tpcds_result(), {1: SimpleNamespace(set_id=1)})

        expected = [
            f"{step}:{function.name}" for function in MAINTENANCE_FUNCTIONS for step in ("check", "start", "end")
        ]
        assert events == expected

    def test_a_stopped_tpcds_maintenance_run_is_not_a_success_and_adds_no_error_row(self, monkeypatch, clock) -> None:
        test = tpcds_maintenance.TPCDSMaintenanceTest(benchmark=Mock(), connection_factory=lambda: Mock())
        result = self._tpcds_result()
        result["total_operations"] = 2
        result["stopped_at_time_limit"] = True
        config = tpcds_maintenance.TPCDSMaintenanceTestConfig(validate_integrity=False)

        test._finalize(config, result)

        assert result["success"] is False

        adapter = MockPlatformAdapter()
        monkeypatch.setattr(adapter, "_plan_capture_checkpoint", lambda connection: None, raising=False)
        monkeypatch.setattr(
            tpcds_maintenance.TPCDSMaintenanceTest,
            "run",
            lambda self, config=None: {**result, "operations": [], "errors": []},
        )
        monkeypatch.setattr(tpcds_maintenance, "maintenance_unsupported_reason", lambda adapter: None)
        adapter.supports_refresh_functions = True
        rows = adapter._execute_tpcds_maintenance_test(Mock(), Mock(), {"scale_factor": 1.0})
        assert rows == []

    def test_wrappers_report_a_timed_out_operation_as_timeout(self, monkeypatch) -> None:
        adapter = MockPlatformAdapter()
        monkeypatch.setattr(adapter, "_plan_capture_checkpoint", lambda connection: None, raising=False)
        operation = tpcds_maintenance.TPCDSMaintenanceOperation(
            operation_type="INSERT",
            table_name="t",
            start_time=0.0,
            end_time=1.0,
            duration=1.0,
            rows_affected=0,
            success=False,
            error="cancelled",
            function="LF_CR",
            timed_out=True,
        )
        monkeypatch.setattr(tpcds_maintenance, "maintenance_unsupported_reason", lambda adapter: None)
        monkeypatch.setattr(
            tpcds_maintenance.TPCDSMaintenanceTest,
            "run",
            lambda self, config=None: {
                "success": False,
                "operations": [operation],
                "errors": ["LF_CR failed: cancelled"],
                "insert_operations": 1,
                "delete_operations": 0,
                "total_operations": 1,
                "successful_operations": 0,
                "total_time": 1.0,
                "overall_throughput": 0.0,
            },
        )

        rows = adapter._execute_tpcds_maintenance_test(Mock(), Mock(), {"scale_factor": 1.0})

        assert [(row["query_id"], row["status"]) for row in rows] == [("LF_CR", "TIMEOUT")]


class _DataFrameHost(BenchmarkExecutionMixin):
    platform_name = "polars-df"

    def __init__(self, clock: FakeClock, events: list[str], fail_on_call: int | None = None) -> None:
        self.clock = clock
        self.events = events
        self.fail_on_call = fail_on_call
        self.calls = 0

    def execute_query(self, ctx: Any, query: Any) -> dict[str, Any]:
        self.calls += 1
        self.events.append(f"start:{query.query_id}")
        self.clock.now += 6
        self.events.append(f"end:{query.query_id}")
        if self.calls == self.fail_on_call:
            raise RuntimeError("cancelled")
        return {"query_id": query.query_id, "status": "SUCCESS", "execution_time_seconds": 6.0, "rows_returned": 1}

    def _get_queries_for_benchmark(
        self, benchmark_config: Any, benchmark_instance: Any, stream_id: int = 0
    ) -> list[Any]:
        return [SimpleNamespace(query_id=query_id) for query_id in ("Q1", "Q2", "Q3")]


def _dataframe_run(host: _DataFrameHost, iterations: int = 1, warm_ups: int = 0) -> list[dict[str, Any]]:
    benchmark_config = SimpleNamespace(
        name="clickbench",
        options={"power_iterations": iterations, "power_warmup_iterations": warm_ups},
        capture_plans=False,
        scale_factor=1.0,
        queries=None,
        concurrency=None,
    )
    return host._run_query_iterations(Mock(), benchmark_config, None, None)


class TestDataFramePowerStopsAtTheDeadline:
    def test_the_measurement_loop_stops_before_the_next_query_and_marks_the_one_in_flight(self, clock) -> None:
        host = _DataFrameHost(clock, [], fail_on_call=2)
        deadline = _deadline()

        with active_run_deadline(deadline):
            rows = _dataframe_run(host, iterations=3, warm_ups=1)

        assert _statuses(rows) == [("Q1", "SUCCESS"), ("Q2", "TIMEOUT")]
        assert host.calls == 2
        assert (deadline.timeout.phase, deadline.timeout.query_id) == ("execute", "Q2")

    def test_the_measurement_loop_does_not_start_another_iteration(self, clock) -> None:
        host = _DataFrameHost(clock, [])

        with active_run_deadline(_deadline(10)):
            rows = _dataframe_run(host, iterations=3)

        assert [row["query_id"] for row in rows] == ["Q1", "Q2"]
        assert {row["iteration"] for row in rows} == {1}

    def test_the_partial_rows_are_shared_with_the_deadline(self, clock) -> None:
        host = _DataFrameHost(clock, [])
        deadline = _deadline(10)

        with active_run_deadline(deadline):
            rows = _dataframe_run(host)

        assert deadline.partial_query_results is rows

    def test_the_loop_checks_only_between_queries(self, monkeypatch, clock) -> None:
        events: list[str] = []
        host = _DataFrameHost(clock, events)
        from benchbox.platforms.dataframe import benchmark_mixin

        real_check = benchmark_mixin.stop_requested_before_query

        def recording_check(query_id: Any) -> bool:
            events.append(f"check:{query_id}")
            return real_check(query_id)

        monkeypatch.setattr(benchmark_mixin, "stop_requested_before_query", recording_check)

        with active_run_deadline(_deadline(1000)):
            _dataframe_run(host)

        assert events == [f"{step}:{query}" for query in ("Q1", "Q2", "Q3") for step in ("check", "start", "end")]

    def test_the_dataframe_loop_is_unchanged_without_a_deadline(self, clock) -> None:
        host = _DataFrameHost(clock, [])

        rows = _dataframe_run(host)

        assert [row["query_id"] for row in rows] == ["Q1", "Q2", "Q3"]


class _StreamDriver(TestDriversMixin):
    stream_connection_capability = StreamConnectionCapability.SHARED_CURSOR
    platform_name = "stub"
    very_verbose = False

    def __init__(self, gate: threading.Event) -> None:
        self.gate = gate
        self.calls: list[Any] = []
        self._lock = threading.Lock()
        self._last_throughput_test_result = None

    def get_target_dialect(self) -> str | None:
        return None

    def new_stream_connection(self, connection: Any, *, benchmark_type: str | None = None) -> Any:
        return connection

    def execute_query(
        self,
        connection: Any,
        query: str,
        query_id: Any,
        benchmark_type: str | None = None,
        scale_factor: float | None = None,
        validate_row_count: bool = True,
        stream_id: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self.calls.append(query_id)
        self.gate.wait(timeout=30)
        return {"query_id": query_id, "status": "SUCCESS", "rows_returned": 1, "execution_time_seconds": 0.0}


def _tpch_benchmark() -> Mock:
    benchmark = Mock()
    benchmark.get_query.return_value = "SELECT 1"
    return benchmark


def _stream_queries(count: int = 3) -> dict[int, list[tuple[Any, str]]]:
    return {
        stream_id: [(SimpleNamespace(query_id=position + 1, variant=None), "SELECT 1") for position in range(count)]
        for stream_id in range(6)
    }


def _run_tpch_stream_driver(driver: _StreamDriver, **run_config: Any) -> tuple[Any, list[dict[str, Any]]]:
    rows = driver._execute_tpch_throughput_test(
        _tpch_benchmark(), Mock(), {"scale_factor": 1.0, "num_streams": 2, **run_config}
    )
    return driver._last_throughput_test_result, rows


def _run_tpcds_stream_driver(monkeypatch, driver: _StreamDriver, **run_config: Any) -> tuple[Any, list[dict[str, Any]]]:
    monkeypatch.setattr(TPCDSThroughputTest, "_pregenerate_stream_queries", lambda self, config: _stream_queries())
    rows = driver._execute_tpcds_throughput_test(Mock(), Mock(), {"scale_factor": 1.0, "num_streams": 2, **run_config})
    return driver._last_throughput_test_result, rows


class TestDataFrameThroughputStopsAtTheDeadline:
    def test_a_dataframe_throughput_run_stops_at_the_deadline_with_the_in_flight_query_marked(self) -> None:
        gate = threading.Event()
        calls: list[Any] = []
        lock = threading.Lock()

        def execute_query(snapshot: Any, query: Any) -> dict[str, Any]:
            with lock:
                calls.append(query)
            gate.wait(timeout=30)
            return {"query_id": "Q1", "status": "SUCCESS", "rows_returned": 1, "execution_time_seconds": 0.0}

        adapter = SimpleNamespace(execute_query=execute_query, platform_name="polars-df")
        ctx = SimpleNamespace(snapshot=lambda: object())
        benchmark_config = SimpleNamespace(
            name="tpch", scale_factor=1.0, options={}, queries=["1", "2", "3"], concurrency=2
        )
        deadline = RunDeadline(1)
        deadline.enter_phase("execute")

        try:
            with active_run_deadline(deadline):
                phase, rows = tpch_full_spec.run_throughput(adapter, ctx, benchmark_config, None, False)
        finally:
            gate.set()

        assert deadline.timeout is not None
        assert sorted((row["stream_id"], row["status"]) for row in rows) == [(1, "TIMEOUT"), (2, "TIMEOUT")]
        assert len(calls) == 2


class TestEffectiveStreamLimit:
    def test_the_limit_is_the_smaller_of_the_setting_and_the_time_remaining(self, clock) -> None:
        deadline = RunDeadline(100)
        clock.now = 30

        assert deadline.limit_to_remaining(500) == pytest.approx(70)
        assert deadline.limit_to_remaining(20) == pytest.approx(20)
        assert deadline.limit_to_remaining(None) == pytest.approx(70)

    def test_without_a_deadline_the_setting_is_kept(self) -> None:
        assert limit_to_remaining(None) is None
        assert limit_to_remaining(90) == 90

    def test_the_runner_hands_the_smaller_limit_to_the_wait(self, clock, monkeypatch) -> None:
        seen: list[float | None] = []
        real = concurrent.futures.as_completed

        def recording(futures: Any, timeout: float | None = None) -> Any:
            seen.append(timeout)
            return real(futures, timeout=timeout)

        monkeypatch.setattr(throughput_runner, "wait_for_completed", recording)
        deadline = RunDeadline(100)
        clock.now = 30

        def run(stream_timeout: int) -> None:
            config = SimpleNamespace(
                num_streams=1,
                max_workers=None,
                base_seed=1,
                stream_timeout=stream_timeout,
                scale_factor=1.0,
                verbose=False,
                cancel_on_timeout=False,
            )
            result = ThroughputResult(
                start_time="",
                end_time="",
                total_time=0.0,
                throughput_at_size=None,
                streams_executed=0,
                streams_successful=0,
            )
            StreamRunner.execute(
                lambda stream_id, seed, cfg: SimpleNamespace(
                    stream_id=stream_id,
                    queries_executed=1,
                    queries_successful=1,
                    queries_failed=0,
                    query_results=[],
                    success=True,
                    error=None,
                ),
                config,
                result,
                logging.getLogger("test"),
            )

        with active_run_deadline(deadline):
            run(500)
            run(20)
            run(0)

        assert seen == [pytest.approx(70), pytest.approx(20), pytest.approx(70)]


STREAM_SECONDS = 8000


class _StreamWait:
    def __init__(self) -> None:
        self.seen: list[float | None] = []
        self.gate: threading.Event | None = None


class TestBenchBoxImposesNoStreamLimit:
    @pytest.fixture
    def stream_wait(self, monkeypatch) -> _StreamWait:
        wait = _StreamWait()
        real = concurrent.futures.as_completed

        def simulated(futures: Any, timeout: float | None = None) -> Any:
            wait.seen.append(timeout)
            if timeout is not None and timeout < STREAM_SECONDS:
                raise concurrent.futures.TimeoutError()
            wait.gate.set()
            return real(futures, timeout=60)

        monkeypatch.setattr(throughput_runner, "wait_for_completed", simulated)
        return wait

    def test_a_tpch_stream_that_outlives_3600_seconds_is_not_stopped_when_nothing_is_set(
        self, stream_wait: _StreamWait
    ) -> None:
        gate = threading.Event()
        stream_wait.gate = gate
        driver = _StreamDriver(gate)

        result, rows = _run_tpch_stream_driver(driver)

        assert stream_wait.seen == [None]
        assert result.streams_successful == 2
        assert result.outstanding_stream_ids == []
        assert {row["status"] for row in rows} == {"SUCCESS"}

    def test_a_tpcds_stream_that_outlives_7200_seconds_is_not_stopped_when_nothing_is_set(
        self, stream_wait: _StreamWait, monkeypatch
    ) -> None:
        gate = threading.Event()
        stream_wait.gate = gate
        driver = _StreamDriver(gate)

        result, rows = _run_tpcds_stream_driver(monkeypatch, driver)

        assert stream_wait.seen == [None]
        assert result.streams_successful == 2
        assert result.outstanding_stream_ids == []
        assert {row["status"] for row in rows} == {"SUCCESS"}

    @pytest.mark.parametrize("which", ["tpch", "tpcds"])
    def test_a_user_set_stream_limit_still_stops_the_stream(self, stream_wait: _StreamWait, monkeypatch, which) -> None:
        gate = threading.Event()
        stream_wait.gate = gate
        driver = _StreamDriver(gate)

        try:
            if which == "tpch":
                result, _rows = _run_tpch_stream_driver(driver, stream_timeout_seconds=90)
            else:
                result, _rows = _run_tpcds_stream_driver(monkeypatch, driver, stream_timeout_seconds=90)
        finally:
            gate.set()

        assert stream_wait.seen == [90]
        assert sorted(result.outstanding_stream_ids) == [1, 2]

    def test_the_defaults_of_both_throughput_configs_are_the_zero_sentinel(self) -> None:
        assert TPCHThroughputTestConfig().stream_timeout == 0
        assert TPCDSThroughputTestConfig().stream_timeout == 0

    def test_the_legacy_configs_carry_no_default_limits(self) -> None:
        from benchbox.core.tpcds.benchmark.config import ThroughputTestConfig
        from benchbox.core.tpch.benchmark import TPCHThroughputTestConfig as LegacyTPCHConfig

        for config in (ThroughputTestConfig(), LegacyTPCHConfig()):
            assert config.query_timeout is None
            assert config.stream_timeout is None


class TestAfterTheFactChecksAreGone:
    def test_the_tpcds_power_query_helper_has_no_timeout_parameter(self) -> None:
        from benchbox.core.tpcds.benchmark.runner import TPCDSBenchmark

        assert "timeout" not in inspect.signature(TPCDSBenchmark._execute_power_test_query).parameters
        assert "timeout" not in inspect.signature(TPCDSBenchmark.run_power_test).parameters

    def test_a_slow_tpcds_power_query_is_not_failed_afterwards(self, monkeypatch) -> None:
        from benchbox.core.tpcds.benchmark import runner as tpcds_runner

        benchmark = tpcds_runner.TPCDSBenchmark.__new__(tpcds_runner.TPCDSBenchmark)
        benchmark.get_query = lambda query_id, seed, dialect: "SELECT 1"
        connection = SimpleNamespace(execute=lambda sql: None, fetchall=lambda: [(1,)])
        monkeypatch.setattr(tpcds_runner, "elapsed_seconds", lambda start, end=None: 100000.0)

        result = benchmark._execute_power_test_query(connection, 1, 1, "standard", False, logging.getLogger("t"))

        assert result["status"] == "success"
        assert result["execution_time_seconds"] == 100000.0

    def test_the_tpcds_throughput_api_has_no_default_limits_and_applies_none(self) -> None:
        from benchbox.core.tpcds.benchmark.runner import TPCDSBenchmark

        parameters = inspect.signature(TPCDSBenchmark.run_throughput_test).parameters
        assert parameters["query_timeout"].default is None
        assert parameters["stream_timeout"].default is None

        benchmark = TPCDSBenchmark.__new__(TPCDSBenchmark)
        benchmark.scale_factor = 1.0
        benchmark.verbose = False
        adapter = MagicMock()
        adapter._run_routed_throughput.return_value = SimpleNamespace(
            total_time=1.0,
            streams_executed=2,
            streams_successful=2,
            stream_results=[],
            throughput_at_size=1.0,
            success=True,
            errors=[],
            outstanding_stream_ids=[],
        )

        benchmark.run_throughput_test(adapter=adapter, connection=Mock(), _warn_deprecated=False)

        passed = adapter._run_routed_throughput.call_args.args[2]
        assert passed["stream_timeout_seconds"] is None


class TestGizmoSQLReceivesTheRemainingTime:
    def _cursor(self, query_timeout: int) -> Any:
        from benchbox.platforms.gizmosql import GizmoSQLCursor

        return GizmoSQLCursor(Mock(), SimpleNamespace(query_timeout=query_timeout, guard=lambda sql: None))

    @pytest.fixture
    def timers(self, monkeypatch) -> list[float]:
        from benchbox.platforms import gizmosql

        started: list[float] = []

        class RecordingTimer:
            def __init__(self, interval: float, function: Any) -> None:
                started.append(interval)
                self.daemon = False

            def start(self) -> None:
                return None

            def cancel(self) -> None:
                return None

        monkeypatch.setattr(gizmosql, "make_timer", RecordingTimer)
        return started

    def test_the_cancel_timer_gets_the_time_remaining_when_no_query_timeout_is_set(self, timers, clock) -> None:
        cursor = self._cursor(0)
        deadline = RunDeadline(100)
        clock.now = 40

        with active_run_deadline(deadline):
            cursor._arm()

        assert timers == [pytest.approx(60)]

    def test_the_cancel_timer_gets_the_smaller_of_the_query_timeout_and_the_time_remaining(self, timers, clock) -> None:
        deadline = RunDeadline(100)
        clock.now = 40

        with active_run_deadline(deadline):
            self._cursor(500)._arm()
            self._cursor(10)._arm()

        assert timers == [pytest.approx(60), pytest.approx(10)]

    def test_nothing_is_armed_without_a_deadline_or_a_query_timeout(self, timers) -> None:
        self._cursor(0)._arm()

        assert timers == []

    def test_a_query_cancelled_by_the_run_limit_says_so(self, timers, clock) -> None:
        cursor = self._cursor(0)

        with active_run_deadline(RunDeadline(100)):
            cursor._arm()

        assert "run time limit" in str(cursor._timeout_error())

    def test_a_query_cancelled_by_its_own_timeout_keeps_its_message(self, timers, clock) -> None:
        cursor = self._cursor(30)

        with active_run_deadline(RunDeadline(100)):
            cursor._arm()

        assert "30s query_timeout" in str(cursor._timeout_error())


class TestCombinedTestStopsBetweenPhases:
    def _adapter(self, calls: list[str], clock: FakeClock | None) -> MockPlatformAdapter:
        adapter = MockPlatformAdapter()

        def phase(name: str) -> Any:
            def run(benchmark: Any, connection: Any, run_config: dict[str, Any]) -> list[dict[str, Any]]:
                calls.append(name)
                if clock is not None:
                    clock.now += 11
                return []

            return run

        adapter._execute_tpch_power_test = phase("power")
        adapter._execute_tpch_throughput_test = phase("throughput")
        adapter._execute_tpch_maintenance_test = phase("maintenance")
        return adapter

    _CONFIG = {"benchmark_name": "tpch", "options": {"requested_phases": ["power", "throughput", "maintenance"]}}

    def test_a_phase_is_not_started_after_the_deadline(self, clock) -> None:
        calls: list[str] = []

        with active_run_deadline(_deadline(10)):
            self._adapter(calls, clock)._execute_combined_test(_benchmark(), Mock(), dict(self._CONFIG))

        assert calls == ["power"]

    def test_every_phase_runs_without_a_deadline(self) -> None:
        calls: list[str] = []

        self._adapter(calls, None)._execute_combined_test(_benchmark(), Mock(), dict(self._CONFIG))

        assert sorted(calls) == ["maintenance", "power", "throughput"]


def _tpch_power_test(monkeypatch, connection: Any) -> tpch_power.TPCHPowerTest:
    monkeypatch.setattr(tpch_power.TPCHPowerTest, "_generate_query", lambda self, query_id, seed: f"q{query_id}")
    monkeypatch.setattr(tpch_power.TPCHPowerTest, "_preflight_validate_generation", lambda self, permutation: None)
    return tpch_power.TPCHPowerTest(
        benchmark=Mock(),
        connection=connection,
        scale_factor=1.0,
        seed=1,
        validation=False,
        query_subset=["1", "2", "3"],
    )


def _tpcds_power_test(monkeypatch, connection: Any) -> tpcds_power.TPCDSPowerTest:
    monkeypatch.setattr(tpcds_power.TPCDSPowerTest, "_determine_available_query_ids", lambda self: [1, 2, 3])
    monkeypatch.setattr(
        tpcds_power.TPCDSPowerTest,
        "_build_queries_to_execute",
        lambda self, available: [(1, None), (2, None), (3, None)],
    )
    monkeypatch.setattr(tpcds_power.TPCDSPowerTest, "_preflight_validate_generation", lambda self, available: None)
    monkeypatch.setattr(
        tpcds_power.TPCDSPowerTest, "_generate_query", lambda self, query_id, variant, seed: f"q{query_id}"
    )
    return tpcds_power.TPCDSPowerTest(
        benchmark=Mock(),
        connection_factory=lambda: connection,
        scale_factor=1.0,
        seed=1,
        validation=False,
    )


class _FetchingConnection(SlowConnection):
    def fetchall(self) -> list[tuple[int]]:
        return [(1,)]


class TestAPowerTestStoppedAtTheLimitReportsNoPowerAtSize:
    @pytest.mark.parametrize("build", [_tpch_power_test, _tpcds_power_test])
    def test_a_stream_cut_off_by_the_deadline_is_not_a_success_and_has_no_power_at_size(
        self, monkeypatch, clock, build
    ) -> None:
        with active_run_deadline(_deadline(10)):
            result = build(monkeypatch, SlowConnection(clock)).run()

        assert result.queries_executed == 2
        assert result.power_at_size == 0.0
        assert result.stopped_at_time_limit is True
        assert result.success is False
        assert any("run time limit" in error for error in result.errors)

    @pytest.mark.parametrize("build", [_tpch_power_test, _tpcds_power_test])
    def test_a_stream_that_finishes_inside_the_limit_keeps_its_power_at_size(self, monkeypatch, clock, build) -> None:
        with active_run_deadline(_deadline(600)):
            result = build(monkeypatch, SlowConnection(clock)).run()

        assert result.queries_executed == 3
        assert result.power_at_size > 0.0
        assert result.stopped_at_time_limit is False
        assert result.success is True

    def test_the_legacy_tpcds_power_run_cut_off_by_the_deadline_is_not_a_success(self, clock) -> None:
        from benchbox.core.tpcds.benchmark import runner as tpcds_runner

        benchmark = tpcds_runner.TPCDSBenchmark.__new__(tpcds_runner.TPCDSBenchmark)
        benchmark.scale_factor = 1.0
        benchmark.verbose = False
        benchmark.get_query = lambda query_id, seed, dialect: "SELECT 1"

        with active_run_deadline(_deadline(10)):
            result = benchmark.run_power_test(_FetchingConnection(clock), warm_up=False)

        assert len(result["query_results"]) == 2
        assert result["power_at_size"] == 0.0
        assert result["success"] is False
        assert any("run time limit" in error for error in result["errors"])

    def test_the_legacy_tpcds_power_run_that_finishes_keeps_its_power_at_size(self, clock) -> None:
        from benchbox.core.tpcds.benchmark import runner as tpcds_runner

        benchmark = tpcds_runner.TPCDSBenchmark.__new__(tpcds_runner.TPCDSBenchmark)
        benchmark.scale_factor = 1.0
        benchmark.verbose = False
        benchmark.get_query = lambda query_id, seed, dialect: "SELECT 1"

        with active_run_deadline(_deadline(600)):
            result = benchmark.run_power_test(_FetchingConnection(clock), warm_up=False)

        assert len(result["query_results"]) == 99
        assert result["power_at_size"] > 0.0
        assert result["success"] is True


class TestAStopAtTheLimitIsRecordedByTheGateThatTakesIt:
    @pytest.mark.parametrize("iterations,warm_ups", [(2, 0), (1, 2)])
    def test_tpch_power_records_the_timeout_when_it_skips_the_next_iteration(
        self, monkeypatch, clock, iterations, warm_ups
    ) -> None:
        deadline = _deadline(17)

        with active_run_deadline(deadline):
            rows = _tpch_power(monkeypatch, SlowConnection(clock), iterations=iterations, warm_ups=warm_ups)

        assert [row["status"] for row in rows] == ["SUCCESS"] * 3
        assert deadline.timeout is not None

    @pytest.mark.parametrize("iterations,warm_ups", [(2, 0), (1, 2)])
    def test_tpcds_power_records_the_timeout_when_it_skips_the_next_iteration(
        self, monkeypatch, clock, iterations, warm_ups
    ) -> None:
        deadline = _deadline(17)

        with active_run_deadline(deadline):
            rows = _tpcds_power(monkeypatch, SlowConnection(clock), iterations=iterations, warm_ups=warm_ups)

        assert [row["status"] for row in rows] == ["SUCCESS"] * 3
        assert deadline.timeout is not None

    @pytest.mark.parametrize("iterations,warm_ups", [(2, 0), (1, 2)])
    def test_generic_power_records_the_timeout_when_it_skips_the_next_iteration(
        self, clock, iterations, warm_ups
    ) -> None:
        adapter = MockPlatformAdapter()
        calls: list[str] = []

        def slow_query(_benchmark: Any, _connection: Any, query_id: Any, _sql: str, **_kwargs: Any) -> dict[str, Any]:
            calls.append(str(query_id))
            clock.now += 11
            return {"query_id": str(query_id), "status": "SUCCESS", "execution_time_seconds": 11.0, "rows_returned": 1}

        adapter._execute_single_query = slow_query
        benchmark = _benchmark()
        benchmark.get_queries.return_value = {"1": "SELECT 1"}
        deadline = _deadline(10)

        with active_run_deadline(deadline):
            adapter._execute_generic_power_test(
                benchmark,
                Mock(),
                {
                    "benchmark_name": "tpch",
                    "iterations": iterations,
                    "warm_up_iterations": warm_ups,
                    "scale_factor": 1.0,
                },
            )

        assert calls == ["1"]
        assert deadline.timeout is not None

    def test_a_combined_test_records_the_timeout_when_it_skips_a_phase(self, clock) -> None:
        calls: list[str] = []
        deadline = _deadline(10)

        with active_run_deadline(deadline):
            TestCombinedTestStopsBetweenPhases()._adapter(calls, clock)._execute_combined_test(
                _benchmark(), Mock(), dict(TestCombinedTestStopsBetweenPhases._CONFIG)
            )

        assert calls == ["power"]
        assert deadline.timeout is not None

    def test_dataframe_power_records_the_timeout_when_it_skips_the_next_iteration(self, clock) -> None:
        host = _DataFrameHost(clock, [])
        deadline = _deadline(17)

        with active_run_deadline(deadline):
            rows = _dataframe_run(host, iterations=2)

        assert [row["query_id"] for row in rows] == ["Q1", "Q2", "Q3"]
        assert deadline.timeout is not None

    def test_dataframe_run_records_the_timeout_when_it_skips_the_throughput_phase(self, monkeypatch, clock) -> None:
        host = _DataFrameHost(clock, [])
        host.supports_dataframe_throughput = True
        ran: list[str] = []

        def slow_power(**_kwargs: Any) -> list[dict[str, Any]]:
            clock.now += 11
            return []

        host._execute_queries_phase = slow_power
        monkeypatch.setattr(tpch_full_spec, "resolve_stream_count", lambda config: 2)
        monkeypatch.setattr(tpch_full_spec, "resolve_refresh_plan", lambda *args, **kwargs: None)
        monkeypatch.setattr(tpch_full_spec, "run_throughput", lambda *args, **kwargs: ran.append("throughput"))
        deadline = _deadline(10)

        with active_run_deadline(deadline):
            host._execute_query_phases(
                Mock(),
                Mock(),
                SimpleNamespace(load=True, execute=True, power=True, throughput=True),
                SimpleNamespace(name="tpch", scale_factor=1.0),
                None,
                None,
            )

        assert ran == []
        assert deadline.timeout is not None


class TestGizmoSQLDoesNotCancelWorkAfterTheRunLimit:
    def _cursor(self, query_timeout: int) -> Any:
        from benchbox.platforms.gizmosql import GizmoSQLCursor

        return GizmoSQLCursor(Mock(), SimpleNamespace(query_timeout=query_timeout, guard=lambda sql: None))

    @pytest.fixture
    def timers(self, monkeypatch) -> list[float]:
        from benchbox.platforms import gizmosql

        started: list[float] = []

        class RecordingTimer:
            def __init__(self, interval: float, function: Any) -> None:
                started.append(interval)
                self.daemon = False

            def start(self) -> None:
                return None

            def cancel(self) -> None:
                return None

        monkeypatch.setattr(gizmosql, "make_timer", RecordingTimer)
        return started

    def test_no_timer_is_armed_for_a_statement_issued_after_the_deadline_passed(self, timers, clock) -> None:
        cursor = self._cursor(0)
        deadline = RunDeadline(100)
        clock.now = 150

        with active_run_deadline(deadline):
            cursor._arm()

        assert timers == []
        assert cursor._run_limited is False

    def test_the_query_timeout_still_bounds_a_statement_issued_after_the_deadline_passed(self, timers, clock) -> None:
        cursor = self._cursor(30)
        deadline = RunDeadline(100)
        clock.now = 150

        with active_run_deadline(deadline):
            cursor._arm()

        assert timers == [30]
        assert "30s query_timeout" in str(cursor._timeout_error())

    def test_a_statement_issued_just_before_the_deadline_is_still_bounded_by_the_time_left(self, timers, clock) -> None:
        cursor = self._cursor(0)
        deadline = RunDeadline(100)
        clock.now = 99

        with active_run_deadline(deadline):
            cursor._arm()

        assert timers == [pytest.approx(1)]
        assert cursor._run_limited is True


class TestADataFramePowerRunStoppedAtTheLimitReportsNoPowerAtSize:
    def _build(self, clock: FakeClock, limit: int) -> Any:
        from benchbox.core.results.builder import BenchmarkInfoInput, PlatformInfoInput, ResultBuilder
        from benchbox.core.results.query_normalizer import normalize_query_result

        class Host(_DataFrameHost):
            def _get_queries_for_benchmark(
                self, benchmark_config: Any, benchmark_instance: Any, stream_id: int = 0
            ) -> list[Any]:
                return [SimpleNamespace(query_id=str(query_id)) for query_id in (1, 2, 3)]

        builder = ResultBuilder(
            benchmark=BenchmarkInfoInput(name="TPC-H", scale_factor=1.0, test_type="power", benchmark_id="tpch"),
            platform=PlatformInfoInput(name="polars-df", execution_mode="dataframe"),
        )
        benchmark_config = SimpleNamespace(
            name="tpch",
            options={"power_iterations": 1, "power_warmup_iterations": 0},
            capture_plans=False,
            scale_factor=1.0,
            queries=None,
            concurrency=None,
        )

        with active_run_deadline(_deadline(limit)):
            rows = Host(clock, [])._execute_query_phases(
                Mock(),
                builder,
                SimpleNamespace(load=True, execute=True, power=True, throughput=False),
                benchmark_config,
                None,
                None,
            )

        for row in rows:
            builder.add_query_result(normalize_query_result({**row, "test_type": "power"}))
        return rows, builder.build()

    def test_a_deadline_that_passes_between_queries_leaves_no_power_at_size(self, clock) -> None:
        rows, results = self._build(clock, 10)

        assert [row["query_id"] for row in rows] == ["1", "2"]
        assert {row["status"] for row in rows} == {"SUCCESS"}
        assert results.power_at_size is None
        assert results.qph_at_size is None

    def test_a_run_that_finishes_inside_the_limit_keeps_its_power_at_size(self, clock) -> None:
        rows, results = self._build(clock, 600)

        assert len(rows) == 3
        assert results.power_at_size is not None and results.power_at_size > 0
