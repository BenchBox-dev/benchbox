from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _AlwaysFailDSQGen:
    def generate(self, qid, **kwargs):
        from benchbox.core.tpcds.c_tools import TPCDSError

        raise TPCDSError("dsqgen binary not found")

    def get_query_variations(self, qid):
        return [str(qid)]

    def validate_query_id(self, qid):
        return False

    def generate_with_parameters(self, qid, params, **kwargs):
        from benchbox.core.tpcds.c_tools import TPCDSError

        raise TPCDSError("dsqgen binary not found")


class _DummyConn:
    def execute(self, _sql):
        return SimpleNamespace(fetchall=list)

    def commit(self):
        pass

    def close(self):
        pass


def test_get_all_queries_raises_on_zero_query_generation():
    from benchbox.core.tpcds.queries import TPCDSQueryManager

    mgr = TPCDSQueryManager()
    mgr.dsqgen = _AlwaysFailDSQGen()
    mgr.available = True

    with pytest.raises(RuntimeError, match="dsqgen failed for all"):
        mgr.get_all_queries(scale_factor=1.0)


def test_get_all_queries_partial_failure_does_not_raise():
    from benchbox.core.tpcds.c_tools import TPCDSError
    from benchbox.core.tpcds.queries import TPCDSQueryManager

    class _PartialDSQGen:
        def generate(self, qid, **kwargs):
            if qid <= 10:
                return f"SELECT {qid}"
            raise TPCDSError("not available")

        def get_query_variations(self, qid):
            return [str(qid)]

        def validate_query_id(self, qid):
            return True

        def generate_with_parameters(self, qid, params, **kwargs):
            return f"SELECT {qid}"

    mgr = TPCDSQueryManager()
    mgr.dsqgen = _PartialDSQGen()
    mgr.available = True

    result = mgr.get_all_queries(scale_factor=1.0)
    assert len(result) == 10
    assert all(qid in result for qid in range(1, 11))


def _make_empty_stream_benchmark():
    from benchbox.core.tpcds.benchmark import TPCDSBenchmark

    bench = TPCDSBenchmark(scale_factor=0.01, verbose=False)

    def _always_fail(query_id, **kwargs):
        raise RuntimeError("dsqgen unavailable")

    bench.get_query = _always_fail

    bench.get_queries = dict

    return bench


def test_zero_query_generation_returns_failed_result_not_completed():
    from benchbox.core.tpcds.power_test import TPCDSPowerTest

    bench = _make_empty_stream_benchmark()

    class _EmptyStreamManager:
        def generate_streams(self):
            return {0: []}

    def _empty_streams(*args, **kwargs):
        return _EmptyStreamManager()

    import benchbox.core.tpcds.streams as streams_module

    original_fn = streams_module.create_standard_streams
    streams_module.create_standard_streams = _empty_streams
    try:
        power = TPCDSPowerTest(
            benchmark=bench,
            connection_factory=lambda: _DummyConn(),
            scale_factor=0.01,
        )
        result = power.run()
    finally:
        streams_module.create_standard_streams = original_fn

    assert result.success is False, "Zero-query run must set success=False"
    assert result.queries_executed == 0, "Zero queries should be executed"
    assert result.errors, "At least one error message must be present"
    assert any("zero queries" in e.lower() or "dsqgen" in e.lower() for e in result.errors), (
        f"Error message should mention zero queries or dsqgen, got: {result.errors}"
    )


def _make_phases_result(query_executions):
    from benchbox.core.results.models import (
        ExecutionPhases,
        PowerTestPhase,
        SetupPhase,
    )

    power_phase = PowerTestPhase(
        start_time="2026-01-01T00:00:00",
        end_time="2026-01-01T00:00:00",
        duration_ms=0,
        query_executions=query_executions,
        geometric_mean_time=0.0,
        power_at_size=0.0,
    )
    setup = SetupPhase()
    result = Mock()
    result.execution_phases = ExecutionPhases(setup=setup, power_test=power_phase)
    result.execution_metadata = {}
    return result


def test_power_test_phase_status_is_failed_when_only_error_sentinel_ran():
    from benchbox.core.results.models import QueryExecution
    from benchbox.core.results.schema import _build_phases_block

    sentinel_execution = QueryExecution(
        query_id="power_test_error",
        stream_id="standard",
        execution_order=1,
        execution_time_ms=0.0,
        status="FAILED",
        rows_returned=None,
        error_message="Power test failed",
        run_type="measurement",
    )
    result = _make_phases_result([sentinel_execution])

    phases = _build_phases_block(result)
    assert phases["power_test"]["status"] == "FAILED", (
        f"Expected FAILED but got {phases['power_test']['status']} - zero-query runs should not report COMPLETED"
    )


def test_power_test_phase_status_is_completed_when_real_queries_ran():
    from benchbox.core.results.models import QueryExecution
    from benchbox.core.results.schema import _build_phases_block

    real_execution = QueryExecution(
        query_id="1",
        stream_id="standard",
        execution_order=1,
        execution_time_ms=1500.0,
        status="SUCCESS",
        rows_returned=100,
        error_message=None,
        run_type="measurement",
    )
    result = _make_phases_result([real_execution])

    phases = _build_phases_block(result)
    assert phases["power_test"]["status"] == "COMPLETED"


