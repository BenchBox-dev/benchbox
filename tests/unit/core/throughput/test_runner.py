from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from unittest.mock import Mock

import pytest

from benchbox.core.results.metrics import TPCMetricsCalculator
from benchbox.core.throughput.result import ThroughputResult, ThroughputStreamResult
from benchbox.core.throughput.runner import StreamRunner

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


@dataclass
class _FakeConfig:
    num_streams: int = 1
    max_workers: Optional[int] = None
    base_seed: int = 42
    stream_timeout: float = 0
    scale_factor: float = 1.0
    verbose: bool = False
    cancel_on_timeout: bool = False


def _make_result() -> ThroughputResult:
    return ThroughputResult(
        start_time="2026-01-01T00:00:00",
        end_time="",
        total_time=0.0,
        throughput_at_size=0.0,
        streams_executed=0,
        streams_successful=0,
    )


def _make_stream_result(
    stream_id: int,
    *,
    start_time: float = 0.0,
    end_time: float = 1.0,
    queries_executed: int = 22,
    queries_successful: int = 22,
    queries_failed: int = 0,
    success: bool = True,
    error: Optional[str] = None,
) -> ThroughputStreamResult:
    return ThroughputStreamResult(
        stream_id=stream_id,
        start_time=start_time,
        end_time=end_time,
        duration=end_time - start_time,
        queries_executed=queries_executed,
        queries_successful=queries_successful,
        queries_failed=queries_failed,
        success=success,
        error=error,
    )


class TestStreamRunnerExecute:
    def test_all_streams_succeed(self) -> None:
        config = _FakeConfig(num_streams=3)
        result = _make_result()
        logger = logging.getLogger("test-throughput-runner")

        stream_fn = Mock(side_effect=lambda stream_id, seed, cfg: _make_stream_result(stream_id))

        StreamRunner.execute(stream_fn, config, result, logger)

        assert result.streams_executed == 3
        assert result.streams_successful == 3
        assert len(result.stream_results) == 3
        assert result.errors == []
        assert stream_fn.call_count == 3

    def test_partial_stream_is_forced_failed_and_recorded_as_error(self) -> None:
        config = _FakeConfig(num_streams=1)
        result = _make_result()
        logger = logging.getLogger("test-throughput-runner")

        stream_fn = Mock(
            return_value=_make_stream_result(
                0,
                queries_successful=20,
                queries_failed=2,
                success=True,
            )
        )

        StreamRunner.execute(stream_fn, config, result, logger)

        assert result.streams_executed == 1
        assert result.streams_successful == 0
        assert len(result.errors) == 1
        assert "Stream 0 failed" in result.errors[0]
        assert "20/22 queries succeeded" in result.errors[0]
        assert result.stream_results[0].success is False

    def test_exception_from_stream_fn_is_captured_as_error(self) -> None:
        config = _FakeConfig(num_streams=1)
        result = _make_result()
        logger = logging.getLogger("test-throughput-runner")

        stream_fn = Mock(side_effect=RuntimeError("stream blew up"))

        StreamRunner.execute(stream_fn, config, result, logger)

        assert result.streams_executed == 1
        assert result.streams_successful == 0
        assert result.stream_results == []
        assert len(result.errors) == 1
        assert "execution failed" in result.errors[0]
        assert "stream blew up" in result.errors[0]

    def test_zero_queries_stream_is_failed_and_visible(self) -> None:
        config = _FakeConfig(num_streams=1)
        result = _make_result()
        logger = logging.getLogger("test-throughput-runner")

        stream_fn = Mock(
            return_value=_make_stream_result(
                0,
                queries_executed=0,
                queries_successful=0,
                queries_failed=0,
                success=True,
            )
        )

        StreamRunner.execute(stream_fn, config, result, logger)

        assert result.streams_executed == 1
        assert result.streams_successful == 0
        assert result.errors == ["Stream 0 failed: 0/0 queries succeeded"]
        assert result.stream_results[0].queries_executed == 0
        assert result.stream_results[0].success is False

    def test_single_stream_fallback_when_max_workers_unset(self) -> None:
        config = _FakeConfig(num_streams=1, max_workers=None)
        result = _make_result()
        logger = logging.getLogger("test-throughput-runner")

        stream_fn = Mock(side_effect=lambda stream_id, seed, cfg: _make_stream_result(stream_id))

        StreamRunner.execute(stream_fn, config, result, logger)

        assert result.streams_executed == 1
        stream_fn.assert_called_once_with(1, config.base_seed + 1, config)

    def test_verbose_logging_does_not_affect_aggregation(self) -> None:
        config = _FakeConfig(num_streams=1, verbose=True)
        result = _make_result()
        logger = Mock(spec=logging.Logger)

        stream_fn = Mock(return_value=_make_stream_result(0))

        StreamRunner.execute(stream_fn, config, result, logger)

        assert result.streams_successful == 1
        assert logger.info.called


class TestStreamRunnerComputeMetrics:
    def test_computes_throughput_from_stream_results(self) -> None:
        config = _FakeConfig(num_streams=3, scale_factor=1.0)
        result = _make_result()
        result.stream_results = [
            _make_stream_result(0, start_time=0.0, end_time=3598.0, queries_executed=22),
            _make_stream_result(1, start_time=1.0, end_time=3599.0, queries_executed=22),
            _make_stream_result(2, start_time=2.0, end_time=3600.0, queries_executed=22),
        ]
        result.streams_executed = 3
        result.streams_successful = 3

        StreamRunner.compute_metrics(result, config, start_time=0.0)

        assert result.total_time == 3600.0
        expected_throughput_at_size = TPCMetricsCalculator.calculate_throughput_at_size(
            total_queries=66,
            total_time_seconds=result.total_time,
            scale_factor=config.scale_factor,
            num_streams=config.num_streams,
        )
        assert expected_throughput_at_size == 66.0
        assert result.throughput_at_size == expected_throughput_at_size
        assert result.query_throughput == pytest.approx(66 / 3600.0)
        assert result.end_time != ""

    def test_phase_window_follows_first_stream_start_and_last_stream_end(self) -> None:
        config = _FakeConfig(num_streams=2)
        result = _make_result()
        first = _make_stream_result(0, start_time=100.0, end_time=105.0)
        first.start_wall_time = "2026-10-04T10:00:00"
        first.end_wall_time = "2026-10-04T10:00:05"
        last = _make_stream_result(1, start_time=101.0, end_time=110.0)
        last.start_wall_time = "2026-10-04T10:00:01"
        last.end_wall_time = "2026-10-04T10:00:10"
        result.stream_results = [first, last]
        result.streams_executed = 2
        result.streams_successful = 2

        StreamRunner.compute_metrics(result, config, start_time=0.0)

        assert result.total_time == pytest.approx(10.0)
        assert result.start_time == "2026-10-04T10:00:00"
        assert result.end_time == "2026-10-04T10:00:10"

    def test_scored_queries_per_stream_overrides_executed_statement_count(self) -> None:
        config = _FakeConfig(num_streams=2, scale_factor=1.0)
        result = _make_result()
        result.stream_results = [
            _make_stream_result(0, start_time=0.0, end_time=3600.0, queries_executed=103, queries_successful=103),
            _make_stream_result(1, start_time=0.0, end_time=3600.0, queries_executed=103, queries_successful=103),
        ]
        result.streams_executed = 2
        result.streams_successful = 2

        StreamRunner.compute_metrics(result, config, start_time=0.0, queries_per_stream=99)

        assert result.throughput_at_size == pytest.approx(198.0)
        assert result.query_throughput == pytest.approx(206 / 3600.0)

    def test_zero_queries_yields_zero_query_throughput(self) -> None:
        config = _FakeConfig(num_streams=1, scale_factor=0.1)
        result = _make_result()
        result.stream_results = [_make_stream_result(0, start_time=0.0, end_time=5.0, queries_executed=0)]
        result.streams_executed = 1
        result.streams_successful = 1

        StreamRunner.compute_metrics(result, config, start_time=0.0)

        assert result.total_time == pytest.approx(5.0)
        assert result.query_throughput == 0.0
        assert result.throughput_at_size == 0.0

    def test_falls_back_to_elapsed_time_when_no_streams_ran(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "benchbox.core.throughput.runner.elapsed_seconds",
            lambda start: 7.5,
        )
        config = _FakeConfig(num_streams=1)
        result = _make_result()
        assert result.stream_results == []
        result.streams_executed = 1
        result.errors = ["Stream 0 execution failed: worker died"]

        StreamRunner.compute_metrics(result, config, start_time=0.0)

        assert result.total_time == pytest.approx(7.5)
        assert result.success is False
        assert result.throughput_at_size == 0.0
        assert result.errors == ["Stream 0 execution failed: worker died"]

    def test_zero_total_time_suppresses_throughput_metric(self) -> None:
        config = _FakeConfig(num_streams=1)
        result = _make_result()
        result.throughput_at_size = -1.0
        result.query_throughput = -1.0
        result.stream_results = [_make_stream_result(0, start_time=5.0, end_time=5.0)]
        result.streams_executed = 1
        result.streams_successful = 1

        StreamRunner.compute_metrics(result, config, start_time=0.0)

        assert result.total_time == 0.0
        assert result.throughput_at_size == 0.0
        assert result.query_throughput == 0.0
        assert result.success is False


class TestThroughputResultAggregation:
    def test_defaults(self) -> None:
        result = _make_result()
        assert result.stream_results == []
        assert result.query_throughput == 0.0
        assert result.success is True
        assert result.errors == []

    def test_stream_result_defaults(self) -> None:
        stream_result = ThroughputStreamResult(
            stream_id=0,
            start_time=0.0,
            end_time=1.0,
            duration=1.0,
            queries_executed=1,
            queries_successful=1,
            queries_failed=0,
        )
        assert stream_result.query_results == []
        assert stream_result.success is True
        assert stream_result.error is None


class TestStreamRunnerTimeoutDetectionAndLeakSurfacing:
    SLOW_STREAM_SLEEP = 0.1
    TINY_TIMEOUT = 0.01

    def test_slow_stream_is_annotated_as_leaked_and_always_logged(self) -> None:
        def slow_stream_fn(stream_id: int, seed: int, cfg: _FakeConfig) -> ThroughputStreamResult:
            time.sleep(self.SLOW_STREAM_SLEEP)
            return _make_stream_result(stream_id)

        config = _FakeConfig(num_streams=1, stream_timeout=self.TINY_TIMEOUT, verbose=False)
        result = _make_result()
        logger = Mock(spec=logging.Logger)

        StreamRunner.execute(slow_stream_fn, config, result, logger)

        assert result.streams_executed == 1
        assert result.streams_successful == 0
        assert len(result.errors) == 1
        assert "timed out" in result.errors[0].lower()
        assert "leaked" in result.errors[0].lower()
        assert "cannot forcibly cancel" in result.errors[0].lower()

        logger.warning.assert_called_once_with(result.errors[0])
        logger.error.assert_not_called()

    def test_fast_stream_within_timeout_is_not_flagged_as_leaked(self) -> None:

        def fast_stream_fn(stream_id: int, seed: int, cfg: _FakeConfig) -> ThroughputStreamResult:
            return _make_stream_result(stream_id)

        config = _FakeConfig(num_streams=1, stream_timeout=5)
        result = _make_result()
        logger = Mock(spec=logging.Logger)

        StreamRunner.execute(fast_stream_fn, config, result, logger)

        assert result.streams_executed == 1
        assert result.streams_successful == 1
        assert result.errors == []
        logger.warning.assert_not_called()


class TestStreamRunnerCooperativeCancellation:
    SLOW_STREAM_SLEEP = 0.1
    TINY_TIMEOUT = 0.01

    def test_disabled_by_default_no_cancel_events_attached(self) -> None:
        config = _FakeConfig(num_streams=2)
        result = _make_result()
        logger = logging.getLogger("test-throughput-runner")
        stream_fn = Mock(side_effect=lambda stream_id, seed, cfg: _make_stream_result(stream_id))

        StreamRunner.execute(stream_fn, config, result, logger)

        assert getattr(config, "_stream_cancel_events", None) == {}

    def test_enabled_but_streams_finish_normally_leaves_events_unset(self) -> None:
        config = _FakeConfig(num_streams=2, cancel_on_timeout=True, stream_timeout=5)
        result = _make_result()
        logger = logging.getLogger("test-throughput-runner")
        stream_fn = Mock(side_effect=lambda stream_id, seed, cfg: _make_stream_result(stream_id))

        StreamRunner.execute(stream_fn, config, result, logger)

        assert result.streams_successful == 2
        assert result.errors == []
        cancel_events = getattr(config, "_stream_cancel_events", None)
        assert cancel_events is not None and len(cancel_events) == 2
        assert all(not event.is_set() for event in cancel_events.values())

    def test_timeout_signals_that_streams_own_cancel_event_only(self) -> None:

        def slow_stream_fn(stream_id: int, seed: int, cfg: _FakeConfig) -> ThroughputStreamResult:
            time.sleep(self.SLOW_STREAM_SLEEP)
            return _make_stream_result(stream_id)

        config = _FakeConfig(num_streams=1, stream_timeout=self.TINY_TIMEOUT, cancel_on_timeout=True)
        result = _make_result()
        logger = Mock(spec=logging.Logger)

        StreamRunner.execute(slow_stream_fn, config, result, logger)

        cancel_events = getattr(config, "_stream_cancel_events", None)
        assert cancel_events is not None and len(cancel_events) == 1
        assert cancel_events[1].is_set()
        assert "cooperative cancellation has been signalled" in result.errors[0]

    def test_stale_cancel_events_reset_when_reusing_config_with_cancel_disabled(self) -> None:

        def slow_stream_fn(stream_id: int, seed: int, cfg: _FakeConfig) -> ThroughputStreamResult:
            time.sleep(self.SLOW_STREAM_SLEEP)
            return _make_stream_result(stream_id)

        config = _FakeConfig(num_streams=1, stream_timeout=self.TINY_TIMEOUT, cancel_on_timeout=True)
        result1 = _make_result()
        logger = logging.getLogger("test-throughput-runner")

        StreamRunner.execute(slow_stream_fn, config, result1, logger)

        stale_cancel_events = getattr(config, "_stream_cancel_events", None)
        assert stale_cancel_events is not None and stale_cancel_events[1].is_set()

        config.cancel_on_timeout = False
        config.stream_timeout = 5
        result2 = _make_result()

        def fast_stream_fn(stream_id: int, seed: int, cfg: _FakeConfig) -> ThroughputStreamResult:
            return _make_stream_result(stream_id)

        StreamRunner.execute(fast_stream_fn, config, result2, logger)

        refreshed_cancel_events = getattr(config, "_stream_cancel_events", None)
        assert refreshed_cancel_events == {}
        assert refreshed_cancel_events is not stale_cancel_events
        assert result2.streams_successful == 1
        assert result2.errors == []


class TestStreamRunnerNonBlockingShutdown:
    TINY_TIMEOUT = 0.05
    HANG_SLEEP = 2.0

    def test_execute_returns_near_timeout_not_after_full_hang(self) -> None:

        def hung_stream_fn(stream_id: int, seed: int, cfg: _FakeConfig) -> ThroughputStreamResult:
            time.sleep(self.HANG_SLEEP)
            return _make_stream_result(stream_id)

        config = _FakeConfig(num_streams=1, stream_timeout=self.TINY_TIMEOUT, cancel_on_timeout=False)
        result = _make_result()
        logger = logging.getLogger("test-throughput-runner")

        start = time.monotonic()
        StreamRunner.execute(hung_stream_fn, config, result, logger)
        elapsed = time.monotonic() - start

        assert elapsed < 1.0, f"execute() blocked for {elapsed:.3f}s -- did not return near the timeout"

    def test_leaked_stream_from_non_blocking_path_still_surfaced_in_errors(self) -> None:

        def hung_stream_fn(stream_id: int, seed: int, cfg: _FakeConfig) -> ThroughputStreamResult:
            time.sleep(self.HANG_SLEEP)
            return _make_stream_result(stream_id)

        config = _FakeConfig(num_streams=1, stream_timeout=self.TINY_TIMEOUT, cancel_on_timeout=False)
        result = _make_result()
        logger = Mock(spec=logging.Logger)

        StreamRunner.execute(hung_stream_fn, config, result, logger)

        assert result.streams_executed == 1
        assert result.streams_successful == 0
        assert len(result.errors) == 1
        assert "timed out" in result.errors[0].lower()
        assert "leaked" in result.errors[0].lower()
        logger.warning.assert_called_once_with(result.errors[0])

    def test_healthy_all_streams_complete_path_unaffected(self) -> None:
        config = _FakeConfig(num_streams=4, stream_timeout=5)
        result = _make_result()
        logger = logging.getLogger("test-throughput-runner")
        stream_fn = Mock(side_effect=lambda stream_id, seed, cfg: _make_stream_result(stream_id))

        StreamRunner.execute(stream_fn, config, result, logger)

        assert result.streams_executed == 4
        assert result.streams_successful == 4
        assert result.errors == []
        assert stream_fn.call_count == 4

    def test_cooperative_cancel_with_real_set_event_still_cancels(self) -> None:

        def slow_stream_fn(stream_id: int, seed: int, cfg: _FakeConfig) -> ThroughputStreamResult:
            cancel_event = cfg._stream_cancel_events[stream_id]
            assert cancel_event.wait(timeout=1.0), "StreamRunner did not signal cooperative cancellation"
            return _make_stream_result(stream_id)

        config = _FakeConfig(num_streams=1, stream_timeout=self.TINY_TIMEOUT, cancel_on_timeout=True)
        result = _make_result()
        logger = logging.getLogger("test-throughput-runner")

        StreamRunner.execute(slow_stream_fn, config, result, logger)

        cancel_events = getattr(config, "_stream_cancel_events", None)
        assert cancel_events is not None
        assert isinstance(cancel_events[1], threading.Event)
        assert cancel_events[1].is_set()

    def test_cooperative_cancel_disabled_by_default_never_cancels(self) -> None:

        def hung_stream_fn(stream_id: int, seed: int, cfg: _FakeConfig) -> ThroughputStreamResult:
            time.sleep(self.HANG_SLEEP)
            return _make_stream_result(stream_id)

        config = _FakeConfig(num_streams=1, stream_timeout=self.TINY_TIMEOUT)
        assert config.cancel_on_timeout is False
        result = _make_result()
        logger = logging.getLogger("test-throughput-runner")

        StreamRunner.execute(hung_stream_fn, config, result, logger)

        assert getattr(config, "_stream_cancel_events", None) == {}

    def test_mock_truthy_config_healthy_path_unaffected(self) -> None:
        stream_fn = Mock(side_effect=lambda stream_id, seed, cfg: _make_stream_result(stream_id))
        result = _make_result()
        logger = logging.getLogger("test-throughput-runner")

        config = Mock()
        config.num_streams = 1
        config.max_workers = None
        config.base_seed = 1
        config.stream_timeout = 5
        config.scale_factor = 1.0
        config.verbose = False

        StreamRunner.execute(stream_fn, config, result, logger)

        assert result.streams_successful == 1
        assert result.errors == []
        cancel_events = getattr(config, "_stream_cancel_events", None)
        assert isinstance(cancel_events, dict) and len(cancel_events) == 1
        assert all(not event.is_set() for event in cancel_events.values())

    def test_queued_stream_never_starts_after_max_workers_throttled_timeout(self) -> None:
        started = threading.Event()

        def stream_fn(stream_id: int, seed: int, cfg: _FakeConfig) -> ThroughputStreamResult:
            if stream_id == 1:
                time.sleep(self.HANG_SLEEP)
            else:
                started.set()
            return _make_stream_result(stream_id)

        config = _FakeConfig(num_streams=2, max_workers=1, stream_timeout=self.TINY_TIMEOUT, cancel_on_timeout=False)
        result = _make_result()
        logger = logging.getLogger("test-throughput-runner")

        StreamRunner.execute(stream_fn, config, result, logger)

        assert result.streams_executed == 1
        assert result.cancelled_stream_ids == [2]
        assert len(result.errors) == 2

        assert not started.wait(timeout=self.HANG_SLEEP + 1.0)


EXIT_PROBE_PATH = Path(__file__).with_name("hung_stream_exit_probe.py")


class TestHungStreamDoesNotBlockInterpreterExit:
    def test_process_exits_while_a_timed_out_stream_is_still_running(self) -> None:
        started = time.monotonic()
        completed = subprocess.run(
            [sys.executable, str(EXIT_PROBE_PATH)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        elapsed = time.monotonic() - started

        assert completed.returncode == 0, completed.stderr
        assert "OUTSTANDING [1, 2]" in completed.stdout
        assert elapsed < 30


class _RacingFuture(Future):
    def __init__(self, outcome: ThroughputStreamResult) -> None:
        super().__init__()
        self._outcome = outcome

    def cancel(self) -> bool:
        self.set_result(self._outcome)
        return super().cancel()


class _ScriptedExecutor:
    def __init__(self, futures: list[Future], fail_on_submit: int | None = None) -> None:
        self._futures = futures
        self._fail_on_submit = fail_on_submit
        self.submitted = 0
        self.shutdown_calls: list[tuple[bool, bool]] = []

    def submit(self, fn, /, *args, **kwargs) -> Future:
        index = self.submitted
        if self._fail_on_submit is not None and index == self._fail_on_submit:
            raise RuntimeError("can't start new thread")
        self.submitted += 1
        return self._futures[index]

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        self.shutdown_calls.append((wait, cancel_futures))


class TestStreamRunnerSettlementEdges:
    def test_future_finishing_while_deadline_is_settled_is_recorded_as_completed(self, monkeypatch) -> None:
        racing = _RacingFuture(_make_stream_result(1))
        done = Future()
        done.set_result(_make_stream_result(0))
        executor = _ScriptedExecutor([done, racing])
        monkeypatch.setattr("benchbox.core.throughput.runner.DaemonStreamExecutor", lambda max_workers: executor)
        config = _FakeConfig(num_streams=2, stream_timeout=0.05)
        result = _make_result()

        StreamRunner.execute(Mock(), config, result, logging.getLogger("test-throughput-runner"))

        assert result.streams_executed == 2
        assert result.streams_successful == 2
        assert result.errors == []
        assert result.cancelled_stream_ids == []
        assert result.outstanding_stream_ids == []
        assert result.cleanup_state == "complete"

    def test_stream_cancelled_before_start_is_not_counted_as_executed(self) -> None:
        release = threading.Event()
        started = threading.Event()

        def stream_fn(stream_id: int, seed: int, cfg: _FakeConfig) -> ThroughputStreamResult:
            if stream_id == 1:
                release.wait(timeout=10)
            else:
                started.set()
            return _make_stream_result(stream_id)

        config = _FakeConfig(num_streams=3, max_workers=1, stream_timeout=0.1)
        result = _make_result()

        try:
            StreamRunner.execute(stream_fn, config, result, logging.getLogger("test-throughput-runner"))
        finally:
            release.set()

        assert result.streams_executed == 1
        assert result.outstanding_stream_ids == [1]
        assert result.cancelled_stream_ids == [2, 3]
        assert not started.wait(timeout=0.5)

    def test_submit_failure_surfaces_original_error_and_tracks_submitted_streams(self, monkeypatch) -> None:
        running = Future()
        running.set_running_or_notify_cancel()
        executor = _ScriptedExecutor([running], fail_on_submit=1)
        monkeypatch.setattr("benchbox.core.throughput.runner.DaemonStreamExecutor", lambda max_workers: executor)
        config = _FakeConfig(num_streams=2, stream_timeout=5)
        result = _make_result()

        with pytest.raises(RuntimeError, match="can't start new thread"):
            StreamRunner.execute(Mock(), config, result, logging.getLogger("test-throughput-runner"))

        assert result.outstanding_stream_ids == [1]
        assert result.cleanup_state == "outstanding"
        assert executor.shutdown_calls == [(False, True)]
        assert "abandoned" in result.errors[0]

    def test_submit_failure_cancels_streams_that_never_started(self, monkeypatch) -> None:
        queued = Future()
        executor = _ScriptedExecutor([queued], fail_on_submit=1)
        monkeypatch.setattr("benchbox.core.throughput.runner.DaemonStreamExecutor", lambda max_workers: executor)
        config = _FakeConfig(num_streams=2, stream_timeout=5)
        result = _make_result()

        with pytest.raises(RuntimeError, match="can't start new thread"):
            StreamRunner.execute(Mock(), config, result, logging.getLogger("test-throughput-runner"))

        assert queued.cancelled()
        assert result.cancelled_stream_ids == [1]
        assert result.streams_executed == 0
        assert result.cleanup_state == "complete"
