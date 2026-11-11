from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from benchbox.core.tpcds.throughput_test import (
    TPCDSThroughputStreamResult,
    TPCDSThroughputTest,
    TPCDSThroughputTestConfig,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class DummyConn:
    def __init__(self):
        self.closed = False

    def execute(self, _sql):
        return SimpleNamespace(fetchall=list)

    def commit(self):
        return None

    def close(self):
        self.closed = True


@dataclass
class DummyStreamQuery:
    query_id: int
    variant: str | None = None


class DummyStreamManager:
    def __init__(self, queries):
        self._queries = queries

    def generate_streams(self):
        return {0: self._queries}


def test_run_computes_metrics(monkeypatch):
    benchmark = SimpleNamespace(get_query=lambda *_args, **_kwargs: "SELECT 1")
    throughput = TPCDSThroughputTest(benchmark=benchmark, connection_factory=DummyConn, num_streams=1)

    monkeypatch.setattr(throughput, "_preflight_validate_generation", lambda _config: None)

    def fake_execute_stream(stream_id, seed, config):
        return TPCDSThroughputStreamResult(
            stream_id=stream_id,
            start_time=10.0,
            end_time=20.0,
            duration=10.0,
            queries_executed=5,
            queries_successful=5,
            queries_failed=0,
            success=True,
        )

    monkeypatch.setattr(throughput, "_execute_stream", fake_execute_stream)

    result = throughput.run(TPCDSThroughputTestConfig(scale_factor=2.0, num_streams=1, enable_preflight=False))

    assert result.success is True
    assert result.total_time == 10.0
    assert result.streams_successful == 1
    assert result.throughput_at_size > 0
    assert result.query_throughput == pytest.approx(0.5)


def test_throughput_at_size_scores_99_queries_per_stream_not_executed_statements(monkeypatch):
    benchmark = SimpleNamespace(get_query=lambda *_args, **_kwargs: "SELECT 1")
    throughput = TPCDSThroughputTest(benchmark=benchmark, connection_factory=DummyConn, num_streams=2)

    def fake_execute_stream(stream_id, seed, config):
        return TPCDSThroughputStreamResult(
            stream_id=stream_id,
            start_time=0.0,
            end_time=3600.0,
            duration=3600.0,
            queries_executed=103,
            queries_successful=103,
            queries_failed=0,
            success=True,
        )

    monkeypatch.setattr(throughput, "_execute_stream", fake_execute_stream)

    result = throughput.run(TPCDSThroughputTestConfig(scale_factor=1.0, num_streams=2, enable_preflight=False))

    assert result.success is True
    assert result.throughput_at_size == pytest.approx(99 * 2)
    assert result.query_throughput == pytest.approx(206 / 3600.0)


def test_run_without_preflight_clears_stale_pregenerated_queries(monkeypatch):
    benchmark = SimpleNamespace(get_query=lambda *_args, **_kwargs: "SELECT inline")
    throughput = TPCDSThroughputTest(benchmark=benchmark, connection_factory=DummyConn, num_streams=1)
    throughput._pregenerated_queries = {0: [(DummyStreamQuery(query_id=1), "SELECT stale")]}

    def fake_execute_stream(stream_id, seed, config):
        assert throughput._pregenerated_queries is None
        return TPCDSThroughputStreamResult(
            stream_id=stream_id,
            start_time=10.0,
            end_time=11.0,
            duration=1.0,
            queries_executed=1,
            queries_successful=1,
            queries_failed=0,
            success=True,
        )

    monkeypatch.setattr(throughput, "_execute_stream", fake_execute_stream)

    throughput.run(TPCDSThroughputTestConfig(num_streams=1, enable_preflight=False))

    assert throughput._pregenerated_queries is None


def test_validate_results_branches():
    benchmark = SimpleNamespace(get_query=lambda *_args, **_kwargs: "SELECT 1")
    throughput = TPCDSThroughputTest(benchmark=benchmark, connection_factory=DummyConn, num_streams=1)

    good = throughput.run(TPCDSThroughputTestConfig(scale_factor=1.0, num_streams=1, enable_preflight=False))
    assert throughput.validate_results(good) is False

    good.success = True
    good.streams_executed = 1
    good.streams_successful = 1
    good.config.num_streams = 1
    good.throughput_at_size = 10.0
    good.errors = []
    assert throughput.validate_results(good) is True


def test_preflight_wraps_internal_error():
    benchmark = SimpleNamespace(get_query=lambda *_args, **_kwargs: "SELECT 1")
    throughput = TPCDSThroughputTest(benchmark=benchmark, connection_factory=DummyConn, num_streams=1)

    class BadBenchmark:
        def get_query(self, *_args, **_kwargs):
            raise RuntimeError("boom")

    throughput.benchmark = BadBenchmark()

    with pytest.raises(RuntimeError, match="preflight failed"):
        throughput._preflight_validate_generation(TPCDSThroughputTestConfig(num_streams=1, verbose=False))


def test_execute_stream_handles_missing_query_manager(monkeypatch):
    benchmark = SimpleNamespace(
        get_queries=lambda: {"1": "SELECT 1"},
        get_query=lambda *_args, **_kwargs: "SELECT 1",
    )
    throughput = TPCDSThroughputTest(benchmark=benchmark, connection_factory=DummyConn, num_streams=1)

    result = throughput._execute_stream(0, 42, TPCDSThroughputTestConfig(num_streams=1, enable_preflight=False))

    assert result.success is False
    assert result.error is not None


def test_execute_stream_runs_query_path(monkeypatch):
    query_manager = object()
    benchmark = SimpleNamespace(
        query_manager=query_manager,
        get_queries=lambda: {"1": "SELECT 1"},
        get_query=lambda *_args, **_kwargs: "SELECT 1",
    )
    throughput = TPCDSThroughputTest(benchmark=benchmark, connection_factory=DummyConn, num_streams=1)

    monkeypatch.setattr(
        "benchbox.core.tpcds.streams.create_standard_streams",
        lambda **_kwargs: DummyStreamManager([DummyStreamQuery(query_id=1)]),
    )

    result = throughput._execute_stream(0, 100, TPCDSThroughputTestConfig(num_streams=1, enable_preflight=False))

    assert result.queries_executed == 1
    assert result.queries_successful == 1
    assert result.success is True


def test_build_stream_queries_uses_run_num_streams_and_base_seed_not_stream_id(monkeypatch):
    query_manager = object()
    benchmark = SimpleNamespace(
        query_manager=query_manager,
        get_queries=lambda: {"1": "SELECT 1"},
        get_query=lambda *_args, **_kwargs: "SELECT 1",
    )
    throughput = TPCDSThroughputTest(benchmark=benchmark, connection_factory=DummyConn, num_streams=4)

    captured_kwargs = []

    def _fake_create_standard_streams(**kwargs):
        captured_kwargs.append(kwargs)
        return DummyStreamManager([DummyStreamQuery(query_id=1)])

    monkeypatch.setattr(
        "benchbox.core.tpcds.streams.create_standard_streams",
        _fake_create_standard_streams,
    )

    config = TPCDSThroughputTestConfig(num_streams=4, base_seed=100, enable_preflight=False)

    throughput._build_stream_queries(stream_id=1, seed=101, config=config)
    throughput._build_stream_queries(stream_id=4, seed=104, config=config)

    assert len(captured_kwargs) == 2

    for kwargs in captured_kwargs:
        assert kwargs["num_streams"] == config.num_streams + 1 == 5
        assert kwargs["base_seed"] == config.base_seed == 100


class DummyStreamQueryWithSql:
    def __init__(self, query_id, sql, variant=None):
        self.query_id = query_id
        self.variant = variant
        self.sql = sql


def test_pregenerate_stream_queries_routes_through_dsqgen_streams(monkeypatch):
    benchmark = SimpleNamespace(
        translate_query_text=lambda sql, _src, _tgt: f"TRANSLATED::{sql}",
        _apply_target_dialect_overrides=lambda _qid, sql, _tgt: sql,
    )
    throughput = TPCDSThroughputTest(benchmark=benchmark, connection_factory=DummyConn, num_streams=2, dialect="duckdb")

    calls = []

    def _fake_generate_dsqgen_streams(*, num_streams, scale_factor, seed):
        calls.append({"num_streams": num_streams, "scale_factor": scale_factor, "seed": seed})
        return {
            0: [DummyStreamQueryWithSql(query_id=9, sql="select 9;")],
            1: [DummyStreamQueryWithSql(query_id=1, sql="select 1;")],
            2: [DummyStreamQueryWithSql(query_id=2, sql="select 2;")],
        }

    monkeypatch.setattr(
        "benchbox.core.tpcds.streams.generate_dsqgen_streams",
        _fake_generate_dsqgen_streams,
    )

    config = TPCDSThroughputTestConfig(num_streams=2, base_seed=7, scale_factor=3.0, enable_preflight=True)
    result = throughput._pregenerate_stream_queries(config)

    assert len(calls) == 1
    assert calls[0] == {"num_streams": 3, "scale_factor": 3.0, "seed": 7}

    assert sorted(result) == [1, 2]
    assert result[1][0][1] == "TRANSLATED::select 1;"
    assert result[2][0][1] == "TRANSLATED::select 2;"


def test_pregenerate_stream_queries_resolves_translation_from_wrapper_impl(monkeypatch):
    calls: list[tuple[str, object]] = []
    implementation = SimpleNamespace(
        translate_query_text=lambda sql, _src, _tgt: calls.append(("translate", sql)) or f"T::{sql}",
        _apply_target_dialect_overrides=lambda qid, sql, _tgt: calls.append(("override", qid)) or f"O::{sql}",
    )
    throughput = TPCDSThroughputTest(
        benchmark=SimpleNamespace(_impl=implementation),
        connection_factory=DummyConn,
        num_streams=1,
        dialect="duckdb",
    )
    monkeypatch.setattr(
        "benchbox.core.tpcds.streams.generate_dsqgen_streams",
        lambda **_kwargs: {1: [DummyStreamQueryWithSql(query_id=7, sql="select 7;")]},
    )

    result = throughput._pregenerate_stream_queries(TPCDSThroughputTestConfig(num_streams=1, enable_preflight=True))

    assert result[1][0][1] == "O::T::select 7;"
    assert calls == [("translate", "select 7;"), ("override", 7)]


def test_pregenerate_stream_queries_wraps_dsqgen_failure(monkeypatch):
    from benchbox.core.tpcds.streams import DSQGenStreamsError

    benchmark = SimpleNamespace(get_query=lambda *_args, **_kwargs: "SELECT 1")
    throughput = TPCDSThroughputTest(benchmark=benchmark, connection_factory=DummyConn, num_streams=1)

    def _raise(*_args, **_kwargs):
        raise DSQGenStreamsError("boom")

    monkeypatch.setattr("benchbox.core.tpcds.streams.generate_dsqgen_streams", _raise)

    config = TPCDSThroughputTestConfig(num_streams=1, enable_preflight=True)
    with pytest.raises(RuntimeError, match="dsqgen -STREAMS generation failed"):
        throughput._pregenerate_stream_queries(config)


