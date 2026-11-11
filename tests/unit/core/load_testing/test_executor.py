# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import threading
import time
import types
from unittest.mock import patch

import pytest

from benchbox.experimental.load_testing.executor import (
    ConcurrentLoadConfig,
    ConcurrentLoadExecutor,
    ConcurrentLoadResult,
    QueryExecution,
    StreamResult,
)
from benchbox.experimental.load_testing.patterns import SteadyPattern, StepPattern

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class TestQueryExecution:
    def test_basic_creation(self):
        exec_record = QueryExecution(
            query_id="q1",
            stream_id=0,
            start_time=1000.0,
            end_time=1001.0,
            success=True,
        )
        assert exec_record.query_id == "q1"
        assert exec_record.stream_id == 0
        assert exec_record.success is True

    def test_with_error(self):
        exec_record = QueryExecution(
            query_id="q1",
            stream_id=0,
            start_time=1000.0,
            end_time=1001.0,
            success=False,
            error="Connection timeout",
        )
        assert exec_record.success is False
        assert exec_record.error == "Connection timeout"

    def test_queue_wait_time(self):
        exec_record = QueryExecution(
            query_id="q1",
            stream_id=0,
            start_time=1000.0,
            end_time=1001.0,
            success=True,
            queue_wait_time=0.5,
        )
        assert exec_record.queue_wait_time == 0.5


class TestStreamResult:
    def test_basic_creation(self):
        result = StreamResult(
            stream_id=1,
            queries_executed=10,
            queries_succeeded=9,
            queries_failed=1,
            total_time_seconds=5.0,
        )
        assert result.stream_id == 1
        assert result.queries_executed == 10

    def test_success_rate(self):
        result = StreamResult(
            stream_id=0,
            queries_executed=10,
            queries_succeeded=8,
            queries_failed=2,
            total_time_seconds=5.0,
        )
        assert result.success_rate == 80.0

    def test_success_rate_zero_queries(self):
        result = StreamResult(
            stream_id=0,
            queries_executed=0,
            queries_succeeded=0,
            queries_failed=0,
            total_time_seconds=1.0,
        )
        assert result.success_rate == 0.0

    def test_throughput(self):
        result = StreamResult(
            stream_id=0,
            queries_executed=100,
            queries_succeeded=100,
            queries_failed=0,
            total_time_seconds=10.0,
        )
        assert result.throughput == 10.0

    def test_throughput_zero_time(self):
        result = StreamResult(
            stream_id=0,
            queries_executed=10,
            queries_succeeded=10,
            queries_failed=0,
            total_time_seconds=0.0,
        )
        assert result.throughput == 0.0


class TestConcurrentLoadResult:
    def test_basic_creation(self):
        result = ConcurrentLoadResult(
            start_time=1000.0,
            end_time=1010.0,
            total_duration_seconds=10.0,
            streams=[],
            total_streams_executed=5,
            total_streams_succeeded=5,
            total_queries_executed=50,
            total_queries_succeeded=48,
            total_queries_failed=2,
            overall_throughput=5.0,
        )
        assert result.total_duration_seconds == 10.0
        assert result.overall_throughput == 5.0

    def test_success_rate(self):
        result = ConcurrentLoadResult(
            start_time=0,
            end_time=10,
            total_duration_seconds=10,
            streams=[],
            total_streams_executed=1,
            total_streams_succeeded=1,
            total_queries_executed=100,
            total_queries_succeeded=95,
            total_queries_failed=5,
            overall_throughput=10,
        )
        assert result.success_rate == 95.0

    def test_percentile_latency(self):
        executions = [
            QueryExecution(
                query_id=f"q{i}",
                stream_id=0,
                start_time=float(i),
                end_time=float(i) + 0.1 * (i + 1),
                success=True,
            )
            for i in range(10)
        ]
        stream = StreamResult(
            stream_id=0,
            queries_executed=10,
            queries_succeeded=10,
            queries_failed=0,
            total_time_seconds=5.0,
            query_executions=executions,
        )
        result = ConcurrentLoadResult(
            start_time=0,
            end_time=5,
            total_duration_seconds=5,
            streams=[stream],
            total_streams_executed=1,
            total_streams_succeeded=1,
            total_queries_executed=10,
            total_queries_succeeded=10,
            total_queries_failed=0,
            overall_throughput=2.0,
        )
        p50 = result.get_percentile_latency(50)
        p99 = result.get_percentile_latency(99)
        assert p50 > 0
        assert p99 >= p50


class TestConcurrentLoadConfig:
    def test_default_pattern(self, mock_connection_factory, mock_query_factory, mock_execute_query):
        config = ConcurrentLoadConfig(
            query_factory=mock_query_factory,
            connection_factory=mock_connection_factory,
            execute_query=mock_execute_query,
        )
        assert config.pattern.max_concurrency == 1
        assert config.queries_per_stream == 10


class TestConcurrentLoadExecutor:
    def test_basic_execution(self, mock_connection_factory, mock_query_factory, mock_execute_query):
        config = ConcurrentLoadConfig(
            query_factory=mock_query_factory,
            connection_factory=mock_connection_factory,
            execute_query=mock_execute_query,
            pattern=SteadyPattern(concurrency=2, duration_seconds=1),
            queries_per_stream=3,
            collect_resource_metrics=False,
        )
        executor = ConcurrentLoadExecutor(config)
        result = executor.run()

        assert result.total_queries_executed > 0
        assert result.total_duration_seconds >= 1.0
        assert result.pattern_name == "SteadyPattern"

    def test_tracks_stream_results(self, mock_connection_factory, mock_query_factory, mock_execute_query):
        config = ConcurrentLoadConfig(
            query_factory=mock_query_factory,
            connection_factory=mock_connection_factory,
            execute_query=mock_execute_query,
            pattern=SteadyPattern(concurrency=3, duration_seconds=0.5),
            queries_per_stream=2,
            collect_resource_metrics=False,
        )
        executor = ConcurrentLoadExecutor(config)
        result = executor.run()

        assert len(result.streams) > 0
        for stream in result.streams:
            assert stream.queries_executed <= 2

    def test_handles_query_failure(self, mock_connection_factory, mock_query_factory):

        def failing_execute(connection, sql):
            if "1" in sql:
                return (False, None, "Query failed")
            return (True, 1, None)

        config = ConcurrentLoadConfig(
            query_factory=mock_query_factory,
            connection_factory=mock_connection_factory,
            execute_query=failing_execute,
            pattern=SteadyPattern(concurrency=1, duration_seconds=0.5),
            queries_per_stream=3,
            collect_resource_metrics=False,
        )
        executor = ConcurrentLoadExecutor(config)
        result = executor.run()

        assert result.total_queries_failed > 0
        assert result.success_rate < 100

    def test_handles_connection_failure(self, mock_query_factory, mock_execute_query):

        def failing_factory():
            raise RuntimeError("Connection refused")

        config = ConcurrentLoadConfig(
            query_factory=mock_query_factory,
            connection_factory=failing_factory,
            execute_query=mock_execute_query,
            pattern=SteadyPattern(concurrency=1, duration_seconds=0.5),
            queries_per_stream=1,
            collect_resource_metrics=False,
        )
        executor = ConcurrentLoadExecutor(config)
        result = executor.run()

        assert result.total_streams_executed >= 0

    def test_tracks_max_concurrency(self, mock_connection_factory, mock_query_factory, mock_execute_query):
        config = ConcurrentLoadConfig(
            query_factory=mock_query_factory,
            connection_factory=mock_connection_factory,
            execute_query=mock_execute_query,
            pattern=StepPattern([(1, 0.3), (3, 0.3)]),
            queries_per_stream=5,
            collect_resource_metrics=False,
        )
        executor = ConcurrentLoadExecutor(config)
        result = executor.run()

        assert result.max_concurrency_reached >= 1

    def test_calculates_throughput(self, mock_connection_factory, mock_query_factory, mock_execute_query):
        config = ConcurrentLoadConfig(
            query_factory=mock_query_factory,
            connection_factory=mock_connection_factory,
            execute_query=mock_execute_query,
            pattern=SteadyPattern(concurrency=2, duration_seconds=1),
            queries_per_stream=5,
            collect_resource_metrics=False,
        )
        executor = ConcurrentLoadExecutor(config)
        result = executor.run()

        assert result.overall_throughput > 0

    def test_queue_metrics_collected(self, mock_connection_factory, mock_query_factory, mock_execute_query):
        config = ConcurrentLoadConfig(
            query_factory=mock_query_factory,
            connection_factory=mock_connection_factory,
            execute_query=mock_execute_query,
            pattern=SteadyPattern(concurrency=3, duration_seconds=0.5),
            queries_per_stream=2,
            track_queue_times=True,
            collect_resource_metrics=False,
        )
        executor = ConcurrentLoadExecutor(config)
        result = executor.run()

        assert isinstance(result.queue_metrics, dict)


class TestConcurrentLoadExecutorEdgeCases:
    def test_single_query_per_stream(self, mock_connection_factory, mock_query_factory, mock_execute_query):
        config = ConcurrentLoadConfig(
            query_factory=mock_query_factory,
            connection_factory=mock_connection_factory,
            execute_query=mock_execute_query,
            pattern=SteadyPattern(concurrency=1, duration_seconds=0.5),
            queries_per_stream=1,
            collect_resource_metrics=False,
        )
        executor = ConcurrentLoadExecutor(config)
        result = executor.run()

        assert result.total_queries_executed > 0

    def test_high_concurrency(self, mock_connection_factory, mock_query_factory, mock_execute_query):
        config = ConcurrentLoadConfig(
            query_factory=mock_query_factory,
            connection_factory=mock_connection_factory,
            execute_query=mock_execute_query,
            pattern=SteadyPattern(concurrency=20, duration_seconds=0.5),
            queries_per_stream=1,
            collect_resource_metrics=False,
        )
        executor = ConcurrentLoadExecutor(config)
        result = executor.run()

        assert result.max_concurrency_reached > 0

    def test_exception_in_query_execution(self, mock_connection_factory, mock_query_factory):

        def exception_execute(connection, sql):
            raise ValueError("Unexpected error")

        config = ConcurrentLoadConfig(
            query_factory=mock_query_factory,
            connection_factory=mock_connection_factory,
            execute_query=exception_execute,
            pattern=SteadyPattern(concurrency=1, duration_seconds=0.5),
            queries_per_stream=2,
            collect_resource_metrics=False,
        )
        executor = ConcurrentLoadExecutor(config)
        result = executor.run()

        assert result.total_queries_failed > 0


class TestExecutorBoundedWaitsAndClocks:
    def _run_in_thread(self, executor, timeout):
        outcome = {}

        def target():
            outcome["result"] = executor.run()

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        thread.join(timeout)
        return thread, outcome

    def test_hung_stream_does_not_block_the_run_forever(self, mock_connection_factory, mock_query_factory):
        release = threading.Event()

        def hung_execute(connection, sql):
            release.wait(30)
            return True, 1, None

        config = ConcurrentLoadConfig(
            query_factory=mock_query_factory,
            connection_factory=mock_connection_factory,
            execute_query=hung_execute,
            pattern=SteadyPattern(concurrency=1, duration_seconds=0.3),
            queries_per_stream=1,
            query_timeout_seconds=0.3,
            collect_resource_metrics=False,
        )
        executor = ConcurrentLoadExecutor(config)
        try:
            thread, outcome = self._run_in_thread(executor, timeout=10)
            assert not thread.is_alive(), "run() blocked on a hung stream"
        finally:
            release.set()

        result = outcome["result"]
        assert result.total_streams_succeeded == 0
        assert any("abandoned" in (stream.error or "") for stream in result.streams)

    def test_query_over_the_timeout_is_recorded_as_failed(self, mock_connection_factory, mock_query_factory):
        def slow_execute(connection, sql):
            time.sleep(0.15)
            return True, 1, None

        config = ConcurrentLoadConfig(
            query_factory=mock_query_factory,
            connection_factory=mock_connection_factory,
            execute_query=slow_execute,
            pattern=SteadyPattern(concurrency=1, duration_seconds=0.2),
            queries_per_stream=1,
            query_timeout_seconds=0.05,
            collect_resource_metrics=False,
        )
        result = ConcurrentLoadExecutor(config).run()

        assert result.total_queries_executed > 0
        assert result.total_queries_succeeded == 0
        failures = [e for stream in result.streams for e in stream.query_executions]
        assert all("timeout" in (execution.error or "") for execution in failures)

    def test_run_duration_ignores_wall_clock_steps(
        self, mock_connection_factory, mock_query_factory, mock_execute_query
    ):
        ticks = iter(range(10_000, 0, -1))
        stepped_clock = types.SimpleNamespace(time=lambda: float(next(ticks)), sleep=time.sleep)

        config = ConcurrentLoadConfig(
            query_factory=mock_query_factory,
            connection_factory=mock_connection_factory,
            execute_query=mock_execute_query,
            pattern=SteadyPattern(concurrency=1, duration_seconds=0.3),
            queries_per_stream=1,
            collect_resource_metrics=False,
        )
        with patch("benchbox.experimental.load_testing.executor.time", stepped_clock):
            result = ConcurrentLoadExecutor(config).run()

        assert result.total_duration_seconds >= 0.3
        assert result.overall_throughput > 0


class TestExecutorLatencyAndAbandonedStreams:
    def test_latency_percentiles_ignore_wall_clock_steps(self, mock_connection_factory, mock_query_factory):
        def slowish(connection, sql):
            time.sleep(0.02)
            return True, 1, None

        ticks = iter(range(10_000, 0, -1))
        stepped_clock = types.SimpleNamespace(time=lambda: float(next(ticks)), sleep=time.sleep)
        config = ConcurrentLoadConfig(
            query_factory=mock_query_factory,
            connection_factory=mock_connection_factory,
            execute_query=slowish,
            pattern=SteadyPattern(concurrency=1, duration_seconds=0.3),
            queries_per_stream=2,
            collect_resource_metrics=False,
        )
        with patch("benchbox.experimental.load_testing.executor.time", stepped_clock):
            result = ConcurrentLoadExecutor(config).run()

        executions = [e for stream in result.streams for e in stream.query_executions]
        assert executions
        assert all(e.end_time < e.start_time for e in executions)
        assert all(0.01 <= e.latency_seconds < 5 for e in executions)
        assert 0.01 <= result.get_percentile_latency(50) < 5

    def test_abandoned_stream_is_stopped_between_queries_and_reported_outstanding(
        self, mock_connection_factory, mock_query_factory
    ):
        release = threading.Event()
        calls: list[int] = []

        def hung_then_fast(connection, sql):
            calls.append(1)
            release.wait(30)
            return True, 1, None

        config = ConcurrentLoadConfig(
            query_factory=mock_query_factory,
            connection_factory=mock_connection_factory,
            execute_query=hung_then_fast,
            pattern=SteadyPattern(concurrency=1, duration_seconds=0.3),
            queries_per_stream=5,
            query_timeout_seconds=0.1,
            collect_resource_metrics=False,
        )
        executor = ConcurrentLoadExecutor(config)
        try:
            outcome = {}
            thread = threading.Thread(target=lambda: outcome.setdefault("result", executor.run()), daemon=True)
            thread.start()
            thread.join(15)
            assert not thread.is_alive()
            result = outcome["result"]

            assert result.abandoned_stream_ids == [0]
            assert result.outstanding_stream_ids == [0]
            assert result.cleanup_state == "outstanding"
            assert executor._stream_stops[0].is_set()
        finally:
            release.set()

        abandoned = executor._abandoned[0]
        assert _wait_done(abandoned)
        assert len(calls) == 1
        assert [s for s in executor._abandoned if not executor._abandoned[s].done()] == []

    @pytest.mark.parametrize("timeout", [0, -1.0])
    def test_non_positive_query_timeout_is_rejected(self, mock_connection_factory, mock_query_factory, timeout):
        with pytest.raises(ValueError, match="query_timeout_seconds"):
            ConcurrentLoadConfig(
                query_factory=mock_query_factory,
                connection_factory=mock_connection_factory,
                execute_query=lambda connection, sql: (True, 1, None),
                query_timeout_seconds=timeout,
            )


def _wait_done(future, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if future.done():
            return True
        time.sleep(0.01)
    return future.done()


