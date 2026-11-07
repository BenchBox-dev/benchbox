from __future__ import annotations

import threading
import time
from typing import Callable
from unittest.mock import Mock, patch

import pytest

from benchbox.core.tpch.throughput_test import (
    TPCHThroughputStreamResult,
    TPCHThroughputTest,
    TPCHThroughputTestConfig,
    TPCHThroughputTestResult,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]


def _make_benchmark_mock() -> Mock:
    benchmark = Mock()
    benchmark.get_query.return_value = "SELECT 1"
    return benchmark


class _SlowFetchCursor:
    def __init__(self, delay: float, rows: list) -> None:
        self._delay = delay
        self._rows = rows

    def fetchall(self):
        time.sleep(self._delay)
        return self._rows


def _make_connection_factory(registry: list[Mock]) -> Callable[[], Mock]:
    def factory() -> Mock:
        conn = Mock()
        conn.close.return_value = None

        cursor = Mock()
        cursor.fetchall.return_value = []
        conn.execute.return_value = cursor

        registry.append(conn)
        return conn

    return factory


class TestThroughputConfig:
    def test_defaults(self) -> None:
        config = TPCHThroughputTestConfig()
        assert config.scale_factor == 1.0
        assert config.num_streams == 2
        assert config.base_seed == 42
        assert config.stream_timeout == 3600
        assert config.max_workers is None
        assert config.verbose is False
        assert config.min_success_rate == 0.99
        assert config.cancel_on_timeout is False


class TestThroughputResult:
    def test_basic_dataclass_initialisation(self) -> None:
        config = TPCHThroughputTestConfig(scale_factor=0.1, num_streams=1)
        stream_result = TPCHThroughputStreamResult(
            stream_id=0,
            start_time=0.0,
            end_time=0.5,
            duration=0.5,
            queries_executed=22,
            queries_successful=22,
            queries_failed=0,
            query_results=[{"query_id": 1, "success": True}],
        )
        result = TPCHThroughputTestResult(
            config=config,
            start_time="2025-01-01T00:00:00",
            end_time="2025-01-01T00:00:01",
            total_time=1.0,
            throughput_at_size=50.0,
            streams_executed=1,
            streams_successful=1,
            stream_results=[stream_result],
            query_throughput=22.0,
            success=True,
        )

        assert result.success is True
        assert result.streams_executed == 1
        assert result.stream_results[0].queries_failed == 0


class TestThroughputExecution:
    def test_execute_stream_produces_basic_statistics(self) -> None:
        benchmark = _make_benchmark_mock()
        connections: list[Mock] = []
        factory = _make_connection_factory(connections)

        test = TPCHThroughputTest(benchmark=benchmark, connection_factory=factory, scale_factor=0.01, num_streams=1)

        stream_result = test._execute_stream(stream_id=0, seed=101, config=test.config)

        assert stream_result.stream_id == 0
        assert stream_result.queries_executed == 22
        assert stream_result.queries_failed == 0
        assert len(stream_result.query_results) == 22
        assert connections and connections[0].close.called

    def test_execute_stream_times_include_raw_cursor_row_counting(self) -> None:
        benchmark = _make_benchmark_mock()
        connections: list[Mock] = []
        delay = 0.05

        def factory() -> Mock:
            conn = Mock()
            conn.close.return_value = None
            conn.execute.return_value = _SlowFetchCursor(delay, [(1,)])
            connections.append(conn)
            return conn

        test = TPCHThroughputTest(benchmark=benchmark, connection_factory=factory, scale_factor=0.01, num_streams=1)
        stream_result = test._execute_stream(stream_id=0, seed=101, config=test.config)

        assert stream_result.queries_failed == 0
        assert len(stream_result.query_results) == 22
        for query_result in stream_result.query_results:
            assert query_result["result_count"] == 1
            assert query_result["execution_time_seconds"] >= delay

    def test_run_with_single_stream(self) -> None:
        benchmark = _make_benchmark_mock()
        connections: list[Mock] = []
        factory = _make_connection_factory(connections)

        test = TPCHThroughputTest(benchmark=benchmark, connection_factory=factory, scale_factor=0.01, num_streams=1)
        result = test.run()

        assert isinstance(result, TPCHThroughputTestResult)
        assert result.success is True
        assert result.streams_executed == 1
        assert result.stream_results[0].queries_successful == 22
        assert result.throughput_at_size > 0
        assert result.query_throughput > 0
        assert benchmark.get_query.call_count == 22

    def test_run_records_failing_streams(self) -> None:
        benchmark = _make_benchmark_mock()
        connections: list[Mock] = []
        factory = _make_connection_factory(connections)

        test = TPCHThroughputTest(benchmark=benchmark, connection_factory=factory, scale_factor=0.01, num_streams=2)

        successful_stream = TPCHThroughputStreamResult(
            stream_id=0,
            start_time=0.0,
            end_time=0.1,
            duration=0.1,
            queries_executed=22,
            queries_successful=22,
            queries_failed=0,
        )
        failing_stream = TPCHThroughputStreamResult(
            stream_id=1,
            start_time=0.0,
            end_time=0.1,
            duration=0.1,
            queries_executed=22,
            queries_successful=20,
            queries_failed=2,
            success=False,
            error="stream failure",
        )

        with patch.object(
            test,
            "_execute_stream",
            side_effect=[successful_stream, failing_stream],
        ):
            result = test.run()

        assert result.success is False
        assert result.streams_successful == 1
        assert any("failed" in error.lower() for error in result.errors)
        assert len(result.stream_results) == 2

    def test_run_partial_stream_is_fatal_despite_legacy_success_threshold(self) -> None:
        benchmark = _make_benchmark_mock()
        connections: list[Mock] = []
        factory = _make_connection_factory(connections)

        test = TPCHThroughputTest(benchmark=benchmark, connection_factory=factory, scale_factor=0.01, num_streams=2)
        config = TPCHThroughputTestConfig(scale_factor=0.01, num_streams=2, min_success_rate=0.5)

        successful_stream = TPCHThroughputStreamResult(
            stream_id=0,
            start_time=0.0,
            end_time=0.1,
            duration=0.1,
            queries_executed=22,
            queries_successful=22,
            queries_failed=0,
        )
        failing_stream = TPCHThroughputStreamResult(
            stream_id=1,
            start_time=0.0,
            end_time=0.1,
            duration=0.1,
            queries_executed=22,
            queries_successful=20,
            queries_failed=2,
            success=False,
            error="stream failure",
        )

        with patch.object(test, "_execute_stream", side_effect=[successful_stream, failing_stream]):
            result = test.run(config)

        assert result.success is False
        assert result.streams_successful == 1
        assert result.throughput_at_size == 0.0


class TestThroughputReferenceSeedContext:
    @pytest.mark.parametrize("seed", [42, 17039360])
    def test_reference_seed_context_false_at_every_position(self, seed: int) -> None:
        benchmark = _make_benchmark_mock()
        connections: list[Mock] = []
        factory = _make_connection_factory(connections)

        test = TPCHThroughputTest(benchmark=benchmark, connection_factory=factory, scale_factor=1.0, num_streams=1)

        calls: list[bool] = []
        with patch(
            "benchbox.core.tpch.throughput_test.set_reference_seed_context",
            side_effect=lambda v: calls.append(v),
        ):
            test._execute_stream(stream_id=0, seed=seed, config=test.config)

        assert len(calls) == 22
        assert all(v is False for v in calls)

    def test_reference_seed_context_cleared_after_every_query(self) -> None:
        benchmark = _make_benchmark_mock()
        connections: list[Mock] = []
        factory = _make_connection_factory(connections)

        test = TPCHThroughputTest(benchmark=benchmark, connection_factory=factory, scale_factor=1.0, num_streams=1)

        with patch("benchbox.core.tpch.throughput_test.clear_reference_seed_context") as mock_clear:
            test._execute_stream(stream_id=0, seed=42, config=test.config)

        assert mock_clear.call_count == 22

    def test_boundary_query_not_failed_on_stream1_with_default_seed_at_sf1(self) -> None:
        from benchbox.platforms.base.connection_wrappers import PlatformAdapterConnection
        from benchbox.platforms.duckdb import DuckDBAdapter

        benchmark = Mock()
        benchmark.get_query = Mock(side_effect=lambda query_id, **_kwargs: f"SELECT {query_id}")

        def factory() -> PlatformAdapterConnection:
            raw_connection = Mock()

            def raw_execute(query_text):
                raw_result = Mock()
                in_spec_counts = {11: 999, 16: 18_000, 18: 20, 20: 186}
                query_id = int(query_text.rsplit(" ", 1)[-1])
                row_count = in_spec_counts.get(query_id, 999)
                raw_result.fetchall = Mock(return_value=[(1,)] * row_count)
                return raw_result

            raw_connection.execute = Mock(side_effect=raw_execute)
            raw_connection.close = Mock()

            adapter = DuckDBAdapter(fetch_mode="rows")
            connection = PlatformAdapterConnection(raw_connection, adapter)
            connection.benchmark_type = "tpch"
            connection.scale_factor = 1.0
            return connection

        test = TPCHThroughputTest(benchmark=benchmark, connection_factory=factory, scale_factor=1.0, num_streams=2)

        stream0 = test._execute_stream(stream_id=0, seed=test.config.base_seed + 0, config=test.config)
        stream1 = test._execute_stream(stream_id=1, seed=test.config.base_seed + 1, config=test.config)

        boundary_ids = {11, 16, 18, 20}
        for stream_result in (stream0, stream1):
            failed_ids = {qr["query_id"] for qr in stream_result.query_results if not qr["success"]}
            assert not (failed_ids & boundary_ids), (
                f"stream {stream_result.stream_id}: boundary queries wrongly failed: {failed_ids & boundary_ids}"
            )


class TestCooperativeCancellation:
    def test_execute_stream_stops_immediately_when_cancel_event_preset(self) -> None:
        benchmark = _make_benchmark_mock()
        connections: list[Mock] = []
        factory = _make_connection_factory(connections)

        test = TPCHThroughputTest(benchmark=benchmark, connection_factory=factory, scale_factor=0.01, num_streams=1)
        config = TPCHThroughputTestConfig(scale_factor=0.01, num_streams=1, cancel_on_timeout=True)
        config._stream_cancel_events = {0: threading.Event()}
        config._stream_cancel_events[0].set()

        stream_result = test._execute_stream(stream_id=0, seed=1, config=config)

        assert stream_result.queries_executed == 0
        assert stream_result.success is False
        assert "cancel" in (stream_result.error or "").lower()
        assert benchmark.get_query.call_count == 0

    def test_execute_stream_stops_after_cancel_event_set_mid_stream(self) -> None:
        benchmark = _make_benchmark_mock()
        cancel_event = threading.Event()

        connections: list[Mock] = []

        def factory() -> Mock:
            conn = Mock()
            conn.close.return_value = None
            cursor = Mock()
            cursor.fetchall.return_value = []

            def execute_and_cancel(_query_text: str) -> Mock:
                cancel_event.set()
                return cursor

            conn.execute.side_effect = execute_and_cancel
            connections.append(conn)
            return conn

        test = TPCHThroughputTest(benchmark=benchmark, connection_factory=factory, scale_factor=0.01, num_streams=1)
        config = TPCHThroughputTestConfig(scale_factor=0.01, num_streams=1, cancel_on_timeout=True)
        config._stream_cancel_events = {0: cancel_event}

        stream_result = test._execute_stream(stream_id=0, seed=1, config=config)

        assert stream_result.queries_executed == 1
        assert stream_result.success is False
        assert "cancel" in (stream_result.error or "").lower()

    def test_execute_stream_unaffected_when_cancel_on_timeout_disabled(self) -> None:
        benchmark = _make_benchmark_mock()
        connections: list[Mock] = []
        factory = _make_connection_factory(connections)

        test = TPCHThroughputTest(benchmark=benchmark, connection_factory=factory, scale_factor=0.01, num_streams=1)
        config = TPCHThroughputTestConfig(scale_factor=0.01, num_streams=1)

        stream_result = test._execute_stream(stream_id=0, seed=1, config=config)

        assert stream_result.queries_executed == 22
        assert stream_result.success is True


class TestStaleCancelEventDoesNotLeakAcrossReusedConfig:
    def test_stale_cancel_event_from_timed_out_run_does_not_leak_into_next_run(self) -> None:
        benchmark = _make_benchmark_mock()
        connections: list[Mock] = []

        sleep_once_state = {"slept": False}

        def factory() -> Mock:
            conn = Mock()
            conn.close.return_value = None
            cursor = Mock()
            cursor.fetchall.return_value = []

            def execute(_query_text: str) -> Mock:
                if not sleep_once_state["slept"]:
                    sleep_once_state["slept"] = True
                    time.sleep(0.05)
                return cursor

            conn.execute.side_effect = execute
            connections.append(conn)
            return conn

        test = TPCHThroughputTest(benchmark=benchmark, connection_factory=factory, scale_factor=0.01, num_streams=1)

        config = TPCHThroughputTestConfig(
            scale_factor=0.01,
            num_streams=1,
            cancel_on_timeout=True,
            stream_timeout=0.01,
        )

        run1_result = test.run(config=config)

        assert any("timed out" in e.lower() for e in run1_result.errors)
        cancel_events_after_run1 = getattr(config, "_stream_cancel_events", None)
        assert cancel_events_after_run1 is not None
        assert cancel_events_after_run1[1].is_set()

        config.cancel_on_timeout = False
        config.stream_timeout = 3600

        run2_result = test.run(config=config)

        assert run2_result.success is True
        assert run2_result.streams_successful == 1
        assert len(run2_result.stream_results) == 1
        assert run2_result.stream_results[0].queries_executed == 22
        assert run2_result.stream_results[0].success is True
        assert run2_result.stream_results[0].error is None
