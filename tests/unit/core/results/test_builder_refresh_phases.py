from __future__ import annotations

import pytest

from benchbox.core.results.builder import BenchmarkInfoInput, ResultBuilder
from benchbox.core.results.metrics import TPCMetricsCalculator
from benchbox.core.results.models import ThroughputStream, ThroughputTestPhase
from benchbox.core.results.platform_info import PlatformInfoInput
from benchbox.core.results.query_normalizer import QueryResultInput

pytestmark = [pytest.mark.unit, pytest.mark.fast]

POWER_SECONDS = 0.5


def _builder(test_type: str = "combined") -> ResultBuilder:
    return ResultBuilder(
        benchmark=BenchmarkInfoInput(name="TPC-H", scale_factor=1.0, test_type=test_type, compliance_class="official"),
        platform=PlatformInfoInput(name="Polars", execution_mode="dataframe"),
    )


def _row(query: int, *, iteration: int, test_type: str, stream_id: int = 0, run_type: str = "measurement"):
    return QueryResultInput(
        query_id=f"Q{query}",
        execution_time_seconds=POWER_SECONDS,
        rows_returned=1,
        status="SUCCESS",
        iteration=iteration,
        run_type=run_type,
        test_type=test_type,
        stream_id=stream_id,
    )


def _functions(set_id: int, rf1_ms: int = 40, rf2_ms: int = 60) -> list[dict]:
    return [
        {"function": "RF1", "update_set": set_id, "duration_ms": rf1_ms, "status": "SUCCESS", "rows_inserted": 9},
        {"function": "RF2", "update_set": set_id, "duration_ms": rf2_ms, "status": "SUCCESS", "orders_deleted": 3},
    ]


def _throughput(first_set: int = 2, streams: int = 2) -> ThroughputTestPhase:
    pairs = [
        {"update_set": first_set + offset, "functions": _functions(first_set + offset), "status": "SUCCESS"}
        for offset in range(streams)
    ]
    return ThroughputTestPhase(
        start_time="2026-11-17T10:00:00",
        end_time="2026-11-17T10:00:10",
        duration_ms=10_000,
        num_streams=streams,
        streams=[
            ThroughputStream(stream_id=i + 1, start_time="a", end_time="b", duration_ms=10_000, query_executions=[])
            for i in range(streams)
        ],
        total_queries_executed=22 * streams,
        throughput_at_size=123.0,
        success=True,
        refresh_stream={
            "first_set": first_set,
            "pair_count": streams,
            "pairs": pairs,
            "start_time": "2026-11-17T10:00:00",
            "end_time": "2026-11-17T10:00:10",
            "status": "COMPLETED",
            "error": None,
        },
    )


def _full_run(builder: ResultBuilder, *, power_set: int = 1) -> None:
    for query in range(1, 23):
        builder.add_query_result(_row(query, iteration=0, test_type="power", run_type="warmup"))
        builder.add_query_result(_row(query, iteration=1, test_type="power"))
        builder.add_query_result(_row(query, iteration=2, test_type="power"))
    for stream_id in (1, 2):
        for query in range(1, 23):
            builder.add_query_result(_row(query, iteration=1, test_type="throughput", stream_id=stream_id))
    builder.set_power_refresh(power_set, _functions(power_set))
    builder.set_throughput_phase(_throughput(first_set=power_set + 1))


class TestRefreshPhases:
    def test_power_at_size_includes_the_refresh_terms_of_the_final_iteration(self) -> None:
        builder = _builder()
        _full_run(builder)

        result = builder.build()

        expected = TPCMetricsCalculator.calculate_power_at_size([0.04, *([POWER_SECONDS] * 22), 0.06], 1.0)
        assert result.execution_phases.power_test.power_at_size == pytest.approx(expected)
        assert result.power_at_size == pytest.approx(expected)

    def test_the_power_phase_carries_the_refresh_evidence_and_query_count(self) -> None:
        builder = _builder()
        _full_run(builder)

        power = builder.build().execution_phases.power_test

        assert power.refresh_first_set == 1
        assert [function["function"] for function in power.refresh_functions] == ["RF1", "RF2"]
        assert power.query_count == 22
        assert power.success is True

    def test_throughput_rows_stay_out_of_the_power_phase(self) -> None:
        builder = _builder()
        _full_run(builder)

        power = builder.build().execution_phases.power_test

        assert {execution.stream_id for execution in power.query_executions} == {"0"}
        assert len(power.query_executions) == 22 * 3

    def test_qphh_is_derived_when_both_phases_carry_valid_refresh_evidence(self) -> None:
        builder = _builder()
        _full_run(builder)

        result = builder.build()

        assert result.refresh_included is True
        assert result.qph_at_size and result.qph_at_size > 0
        assert not result.qphh_unavailable

    def test_a_failed_rf2_withholds_power_and_qphh(self) -> None:
        builder = _builder()
        _full_run(builder)
        failed = _functions(1)
        failed[1]["status"] = "FAILED"
        builder.set_power_refresh(1, failed)

        result = builder.build()

        assert not result.power_at_size
        assert result.qphh_unavailable
        assert not result.qph_at_size

    def test_throughput_sets_must_follow_the_power_set(self) -> None:
        builder = _builder()
        _full_run(builder)
        builder.set_throughput_phase(_throughput(first_set=5))

        result = builder.build()

        assert not result.qph_at_size
        assert any("immediately after the Power set" in reason for reason in result.qphh_unavailable)

    def test_a_throughput_only_run_has_no_power_phase(self) -> None:
        builder = _builder(test_type="throughput")
        for stream_id in (1, 2):
            for query in range(1, 23):
                builder.add_query_result(_row(query, iteration=1, test_type="throughput", stream_id=stream_id))
        builder.set_throughput_phase(_throughput())

        result = builder.build()

        assert result.execution_phases.power_test is None
        assert result.execution_phases.throughput_test is not None
        assert result.throughput_at_size == 123.0
