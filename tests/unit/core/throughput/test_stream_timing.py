from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace

import pytest

from benchbox.core.tpcds.throughput_test import TPCDSThroughputTest, TPCDSThroughputTestConfig
from benchbox.core.tpch.throughput_test import TPCHThroughputTest, TPCHThroughputTestConfig
from benchbox.platforms.base.execution import TestDriversMixin
from benchbox.platforms.base.result_capture import ResultCaptureMixin

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SETUP_SECONDS = 0.2
TEARDOWN_SECONDS = 0.2


class _SlowSetupConnection:
    def __init__(self):
        time.sleep(SETUP_SECONDS)

    def execute(self, _sql):
        return SimpleNamespace(fetchall=list)

    def commit(self):
        return None

    def close(self):
        time.sleep(TEARDOWN_SECONDS)


class _PhaseBuilder(ResultCaptureMixin, TestDriversMixin):
    pass


@dataclass
class _StreamQuery:
    query_id: int
    variant: str | None = None


def _assert_plausible_wall_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    assert parsed.year >= 2025
    assert abs((datetime.now() - parsed).total_seconds()) < 120
    return parsed


def _assert_window_excludes_setup_and_teardown(stream) -> None:
    assert stream.duration == pytest.approx(stream.end_time - stream.start_time)
    assert stream.duration < SETUP_SECONDS / 2
    start = _assert_plausible_wall_time(stream.start_wall_time)
    end = _assert_plausible_wall_time(stream.end_wall_time)
    assert end >= start
    assert (end - start).total_seconds() < SETUP_SECONDS / 2


def test_tpch_stream_window_excludes_connection_setup_and_close():
    benchmark = SimpleNamespace(get_query=lambda query_id, **_kwargs: f"SELECT {query_id}")
    test = TPCHThroughputTest(benchmark=benchmark, connection_factory=_SlowSetupConnection, num_streams=1)

    stream = test._execute_stream(0, 42, TPCHThroughputTestConfig(num_streams=1))

    assert stream.queries_successful == 22
    _assert_window_excludes_setup_and_teardown(stream)


def test_tpcds_stream_window_excludes_connection_setup_and_close():
    benchmark = SimpleNamespace(get_query=lambda *_args, **_kwargs: "SELECT 1")
    test = TPCDSThroughputTest(benchmark=benchmark, connection_factory=_SlowSetupConnection, num_streams=1)
    test._pregenerated_queries = {0: [(_StreamQuery(query_id=1), "SELECT 1")]}

    stream = test._execute_stream(0, 42, TPCDSThroughputTestConfig(num_streams=1))

    assert stream.queries_successful == 1
    _assert_window_excludes_setup_and_teardown(stream)


def test_failed_connection_still_exports_real_wall_times():
    def failing_factory():
        raise RuntimeError("connect failed")

    benchmark = SimpleNamespace(get_query=lambda *_args, **_kwargs: "SELECT 1")
    test = TPCHThroughputTest(benchmark=benchmark, connection_factory=failing_factory, num_streams=1)

    stream = test._execute_stream(0, 42, TPCHThroughputTestConfig(num_streams=1))

    assert stream.success is False
    _assert_plausible_wall_time(stream.start_wall_time)
    _assert_plausible_wall_time(stream.end_wall_time)


def test_exported_stream_and_phase_timestamps_are_wall_clock_dates():
    benchmark = SimpleNamespace(get_query=lambda query_id, **_kwargs: f"SELECT {query_id}")
    test = TPCHThroughputTest(
        benchmark=benchmark,
        connection_factory=lambda: SimpleNamespace(
            execute=lambda _sql: SimpleNamespace(fetchall=list), commit=lambda: None, close=lambda: None
        ),
        num_streams=2,
    )
    test._pregenerated_queries = None

    result = test.run(TPCHThroughputTestConfig(num_streams=2, scale_factor=0.01))
    phase = _PhaseBuilder()._create_throughput_phase(result)

    assert phase is not None and result.success
    for stream in phase.streams:
        _assert_plausible_wall_time(stream.start_time)
        _assert_plausible_wall_time(stream.end_time)
    phase_start = _assert_plausible_wall_time(phase.start_time)
    phase_end = _assert_plausible_wall_time(phase.end_time)
    first_stream_start = min(datetime.fromisoformat(s.start_time) for s in phase.streams)
    last_stream_end = max(datetime.fromisoformat(s.end_time) for s in phase.streams)
    assert phase_start == first_stream_start
    assert phase_end == last_stream_end
    assert phase.duration_ms == pytest.approx((phase_end - phase_start).total_seconds() * 1000, abs=50)


