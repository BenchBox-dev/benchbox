from __future__ import annotations

from unittest.mock import Mock

import pytest

from benchbox.core.tpch import power_test as module
from benchbox.core.tpch.power_test import TPCHPowerTest
from benchbox.core.tpch.streams import TPCHStreams

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


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

    def stub(self, fail_ids=()):
        calls: list[int] = []

        def get_query(query_id, **kwargs):
            if self.depth > 1:
                self.violations.append(query_id)
            if query_id in fail_ids:
                raise RuntimeError(f"generation failed for {query_id}")
            calls.append(query_id)
            return f"SELECT {query_id} /* {kwargs['seed']} {kwargs['stream_id']} {kwargs['dialect']} */"

        benchmark = Mock()
        benchmark.get_query.side_effect = get_query
        benchmark.calls = calls
        return benchmark


def _connection() -> tuple[Mock, list[str]]:
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


def _power(benchmark, connection, **kwargs) -> TPCHPowerTest:
    return TPCHPowerTest(
        benchmark=benchmark,
        connection=connection,
        scale_factor=0.01,
        seed=7,
        stream_id=0,
        dialect="duckdb",
        warm_up=False,
        validation=False,
        **kwargs,
    )


def test_no_generation_while_per_query_timer_is_open(monkeypatch):
    watch = _TimerWatch(monkeypatch)
    benchmark = watch.stub()
    connection, executed = _connection()

    result = _power(benchmark, connection).run()

    assert result.queries_successful == 22
    assert watch.violations == []
    assert len(executed) == 22


def test_generation_on_cache_miss_stays_outside_timer(monkeypatch):
    watch = _TimerWatch(monkeypatch)
    benchmark = watch.stub()
    connection, executed = _connection()
    power = _power(benchmark, connection)
    monkeypatch.setattr(power, "_preflight_validate_generation", lambda _permutation: None)

    result = power.run()

    assert result.queries_successful == 22
    assert watch.violations == []
    assert len(executed) == 22


def test_generation_failure_on_cache_miss_does_not_start_timer(monkeypatch):
    watch = _TimerWatch(monkeypatch)
    benchmark = watch.stub(fail_ids={3})
    connection, _ = _connection()
    power = _power(benchmark, connection)
    monkeypatch.setattr(power, "_preflight_validate_generation", lambda _permutation: None)

    result = power.run()

    failed = [q for q in result.query_results if q["query_id"] == 3]
    assert failed[0]["success"] is False
    assert failed[0]["execution_time_seconds"] == 0.0
    assert watch.depth == 0


def test_generation_runs_exactly_once_per_query_with_preflight(monkeypatch):
    watch = _TimerWatch(monkeypatch)
    benchmark = watch.stub()
    connection, _ = _connection()

    _power(benchmark, connection).run()

    assert sorted(benchmark.calls) == list(range(1, 23))
    assert benchmark.get_query.call_count == 22


def test_executed_text_matches_get_query_for_same_arguments(monkeypatch):
    watch = _TimerWatch(monkeypatch)
    benchmark = watch.stub()
    connection, executed = _connection()

    _power(benchmark, connection).run()

    permutation = TPCHStreams.PERMUTATION_MATRIX[0]
    expected = [
        benchmark.get_query.side_effect(q, seed=module.power_stream_seed(7, 0), stream_id=0, dialect="duckdb")
        for q in permutation
    ]
    assert executed == expected


def test_generation_failure_aborts_before_any_timer_or_execution(monkeypatch):
    watch = _TimerWatch(monkeypatch)
    benchmark = watch.stub(fail_ids={5})
    connection, executed = _connection()

    with pytest.raises(RuntimeError, match="preflight failed"):
        _power(benchmark, connection).run()

    assert executed == []
    assert watch.depth == 1
