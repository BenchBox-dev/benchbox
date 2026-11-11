from __future__ import annotations

from unittest.mock import Mock

import pytest

from benchbox.core.tpcds import power_test as module
from benchbox.core.tpcds.power_test import TPCDSPowerTest, TPCDSPowerTestResult

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

QUERY_IDS = [1, 2, 3, 4]


class _Benchmark:
    def __init__(self, watch, fail_ids=()):
        self.watch = watch
        self.fail_ids = fail_ids
        self.calls: list[tuple] = []

    def get_queries(self):
        return {str(i): f"SELECT {i}" for i in QUERY_IDS}

    def get_query(self, query_id, **kwargs):
        if self.watch.depth > 1:
            self.watch.violations.append(query_id)
        if query_id in self.fail_ids:
            raise RuntimeError(f"generation failed for {query_id}")
        self.calls.append((query_id, kwargs.get("variant")))
        return f"SELECT {query_id} /* {kwargs['seed']} {kwargs['dialect']} */"


class _TimerWatch:
    def __init__(self, monkeypatch):
        self.depth = 0
        self.violations: list[object] = []
        real_mono = module.mono_time
        real_elapsed = module.elapsed_seconds

        def mono():
            self.depth += 1
            return real_mono()

        def elapsed(start):
            self.depth -= 1
            return real_elapsed(start)

        monkeypatch.setattr(module, "mono_time", mono)
        monkeypatch.setattr(module, "elapsed_seconds", elapsed)


def _connection():
    executed: list[str] = []
    cursor = Mock()
    cursor.fetchall.return_value = [(1,)]
    del cursor.platform_result
    connection = Mock()

    def execute(sql):
        executed.append(sql)
        return cursor

    connection.execute.side_effect = execute
    return connection, executed


def _power(benchmark, connection) -> TPCDSPowerTest:
    return TPCDSPowerTest(
        benchmark=benchmark,
        connection_factory=lambda: connection,
        scale_factor=0.01,
        seed=7,
        stream_id=0,
        dialect="duckdb",
        warm_up=False,
        validation=False,
    )


def test_no_generation_while_per_query_timer_is_open(monkeypatch):
    watch = _TimerWatch(monkeypatch)
    benchmark = _Benchmark(watch)
    connection, executed = _connection()

    result = _power(benchmark, connection).run()

    assert result.queries_successful == len(QUERY_IDS)
    assert watch.violations == []
    assert len(executed) == len(QUERY_IDS)


def test_generation_on_cache_miss_stays_outside_timer(monkeypatch):
    watch = _TimerWatch(monkeypatch)
    benchmark = _Benchmark(watch)
    connection, executed = _connection()
    power = _power(benchmark, connection)
    monkeypatch.setattr(power, "_preflight_validate_generation", lambda _ids: None)

    result = power.run()

    assert result.queries_successful == len(QUERY_IDS)
    assert watch.violations == []


def test_variant_query_generation_stays_outside_timer(monkeypatch):
    watch = _TimerWatch(monkeypatch)
    benchmark = _Benchmark(watch)
    connection, executed = _connection()
    power = _power(benchmark, connection)
    power._query_sequence = [(14, "a"), (14, "b")]
    result = TPCDSPowerTestResult(
        config=power.config,
        start_time="",
        end_time="",
        total_time=0.0,
        power_at_size=0.0,
        queries_executed=0,
        queries_successful=0,
        query_results=[],
        success=True,
        errors=[],
    )

    watch.depth = 1
    power._execute_one_query(0, (14, "a"), connection, 5, result)

    assert watch.violations == []
    assert benchmark.calls == [(14, "a")]
    assert watch.depth == 1


def test_generation_failure_on_cache_miss_does_not_start_timer(monkeypatch):
    watch = _TimerWatch(monkeypatch)
    benchmark = _Benchmark(watch, fail_ids={2})
    connection, executed = _connection()
    power = _power(benchmark, connection)
    result = TPCDSPowerTestResult(
        config=power.config,
        start_time="",
        end_time="",
        total_time=0.0,
        power_at_size=0.0,
        queries_executed=0,
        queries_successful=0,
        query_results=[],
        success=True,
        errors=[],
    )

    watch.depth = 1
    power._execute_one_query(0, 2, connection, 5, result)

    assert result.query_results[0]["execution_time_seconds"] == 0.0
    assert result.query_results[0]["success"] is False
    assert executed == []
    assert watch.depth == 1


def test_execute_query_site_takes_text_and_never_generates(monkeypatch):
    watch = _TimerWatch(monkeypatch)
    benchmark = _Benchmark(watch)
    connection, executed = _connection()
    power = _power(benchmark, connection)
    power.connection = connection

    power._execute_query(1, "SELECT 1")

    assert benchmark.calls == []
    assert executed == ["SELECT 1"]


def test_generation_runs_exactly_once_per_query_with_preflight(monkeypatch):
    watch = _TimerWatch(monkeypatch)
    benchmark = _Benchmark(watch)
    connection, _ = _connection()

    _power(benchmark, connection).run()

    assert sorted(benchmark.calls) == [(i, None) for i in QUERY_IDS]


def test_executed_text_matches_get_query_for_same_arguments(monkeypatch):
    watch = _TimerWatch(monkeypatch)
    benchmark = _Benchmark(watch)
    connection, executed = _connection()
    power = _power(benchmark, connection)

    power.run()

    seed = module.power_parameter_seed(7, 0)
    assert executed == [f"SELECT {i} /* {seed} duckdb */" for i in QUERY_IDS]


def test_generation_failure_aborts_before_any_timer_or_execution(monkeypatch):
    watch = _TimerWatch(monkeypatch)
    benchmark = _Benchmark(watch, fail_ids={3})
    connection, executed = _connection()

    with pytest.raises(RuntimeError, match="preflight failed"):
        _power(benchmark, connection).run()

    assert executed == []
    assert watch.depth == 1


