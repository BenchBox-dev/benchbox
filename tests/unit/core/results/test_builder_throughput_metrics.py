from __future__ import annotations

import pytest

from benchbox.core.results.builder import BenchmarkInfoInput, ResultBuilder
from benchbox.core.results.models import (
    ExecutionPhases,
    SetupPhase,
    ThroughputStream,
    ThroughputTestPhase,
    ValidationPhase,
)
from benchbox.core.results.platform_info import PlatformInfoInput
from benchbox.core.results.query_normalizer import QueryResultInput
from benchbox.core.results.schema import build_result_payload

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _builder(benchmark: str = "TPC-H", test_type: str = "combined", compliance_class: str | None = None):
    return ResultBuilder(
        benchmark=BenchmarkInfoInput(
            name=benchmark, scale_factor=1.0, test_type=test_type, compliance_class=compliance_class
        ),
        platform=PlatformInfoInput(name="DuckDB"),
    )


def _row(query_id: str, seconds: float, *, test_type: str | None, iteration: int = 1, status: str = "SUCCESS"):
    return QueryResultInput(
        query_id=query_id,
        execution_time_seconds=seconds,
        rows_returned=1,
        status=status,
        iteration=iteration,
        run_type="measurement",
        test_type=test_type,
    )


def _throughput_phase(*, success: bool, throughput_at_size: float | None) -> ThroughputTestPhase:
    return ThroughputTestPhase(
        start_time="2026-10-04T10:00:00",
        end_time="2026-10-04T10:00:10",
        duration_ms=10_000,
        num_streams=2,
        streams=[
            ThroughputStream(stream_id=0, start_time="a", end_time="b", duration_ms=10_000, query_executions=[]),
            ThroughputStream(stream_id=1, start_time="a", end_time="b", duration_ms=10_000, query_executions=[]),
        ],
        total_queries_executed=44,
        throughput_at_size=throughput_at_size,
        success=success,
    )


def _with_phase(builder: ResultBuilder, phase: ThroughputTestPhase, validation: ValidationPhase | None = None):
    builder.set_execution_phases(
        ExecutionPhases(setup=SetupPhase(validation=validation), power_test=None, throughput_test=phase)
    )


class TestThroughputMetricFollowsPhase:
    def test_failed_phase_without_failed_rows_exports_no_throughput(self) -> None:
        builder = _builder(test_type="throughput")
        for i in range(1, 23):
            builder.add_query_result(_row(str(i), 1.0, test_type="throughput"))
        _with_phase(builder, _throughput_phase(success=False, throughput_at_size=None))

        result = builder.build()

        assert result.failed_queries == 0
        assert result.throughput_at_size is None

    def test_failed_phase_discards_a_positive_phase_value(self) -> None:
        builder = _builder(test_type="throughput")
        builder.add_query_result(_row("1", 1.0, test_type="throughput"))
        _with_phase(builder, _throughput_phase(success=False, throughput_at_size=7920.0))

        assert builder.build().throughput_at_size is None

    def test_successful_phase_exports_the_driver_value_verbatim(self) -> None:
        builder = _builder(test_type="throughput")
        builder.add_query_result(_row("1", 1.0, test_type="throughput"))
        _with_phase(builder, _throughput_phase(success=True, throughput_at_size=1234.5))

        assert builder.build().throughput_at_size == 1234.5

    def test_tpcds_value_is_not_rescaled_from_executed_statement_count(self) -> None:
        builder = _builder(benchmark="TPC-DS", test_type="throughput")
        builder.add_query_result(_row("1", 1.0, test_type="throughput"))
        phase = _throughput_phase(success=True, throughput_at_size=99 * 2 * 3600 / 10)
        phase.total_queries_executed = 206
        _with_phase(builder, phase)

        assert builder.build().throughput_at_size == pytest.approx(99 * 2 * 3600 / 10)


class TestPowerMetricUsesPowerRowsOnly:
    def test_combined_run_with_one_iteration_ignores_throughput_rows(self) -> None:
        builder = _builder()
        for i in range(1, 23):
            builder.add_query_result(_row(str(i), 1.0, test_type="power"))
        for stream in range(2):
            for i in range(1, 23):
                builder.add_query_result(_row(str(i), 50.0, test_type="throughput"))
        _with_phase(builder, _throughput_phase(success=True, throughput_at_size=100.0))

        result = builder.build()

        assert result.power_at_size == pytest.approx(3600.0)
        assert result.throughput_at_size == 100.0

    def test_untyped_power_rows_still_count(self) -> None:
        builder = _builder(test_type="power")
        for i in range(1, 5):
            builder.add_query_result(_row(str(i), 1.0, test_type=None))

        assert builder.build().power_at_size == pytest.approx(3600.0)


class TestNoCompositeMetric:
    @pytest.mark.parametrize("benchmark_name", ["TPC-H", "TPC-DS"])
    def test_combined_run_exports_no_composite(self, benchmark_name: str) -> None:
        builder = _builder(benchmark=benchmark_name)
        for i in range(1, 5):
            builder.add_query_result(_row(str(i), 1.0, test_type="power"))
        _with_phase(builder, _throughput_phase(success=True, throughput_at_size=100.0))

        result = builder.build()
        tpc_metrics = build_result_payload(result)["summary"]["tpc_metrics"]

        assert result.power_at_size == pytest.approx(3600.0)
        assert result.throughput_at_size == 100.0
        assert result.qph_at_size is None
        assert set(tpc_metrics) == {"power_at_size", "throughput_at_size"}


class TestValidationPhaseStatus:
    def _payload(self, statuses: list[str], row_count_status: str = "PASSED") -> dict:
        builder = _builder(test_type="throughput")
        for i, status in enumerate(statuses, start=1):
            builder.add_query_result(_row(str(i), 1.0, test_type="throughput", status=status))
        validation = ValidationPhase(
            duration_ms=5,
            row_count_validation=row_count_status,
            schema_validation="PASSED",
            data_integrity_checks="PASSED",
        )
        _with_phase(builder, _throughput_phase(success=False, throughput_at_size=None), validation)
        return build_result_payload(builder.build())

    def test_partial_run_cannot_report_a_passed_validation_phase(self) -> None:
        payload = self._payload(["SUCCESS"] * 3 + ["FAILED"] * 2)

        assert payload["summary"]["validation"] == "partial"
        assert payload["phases"]["validation"]["status"] == "PARTIAL"

    def test_clean_run_keeps_a_passed_validation_phase(self) -> None:
        payload = self._payload(["SUCCESS"] * 3)

        assert payload["summary"]["validation"] == "passed"
        assert payload["phases"]["validation"]["status"] == "PASSED"

    def test_failed_setup_validation_is_not_downgraded_by_a_milder_overall_status(self) -> None:
        payload = self._payload(["SUCCESS"] * 3 + ["FAILED"] * 2, row_count_status="FAILED")

        assert payload["summary"]["validation"] == "partial"
        assert payload["phases"]["validation"]["status"] == "FAILED"

    def test_partial_setup_validation_is_raised_to_a_failed_overall_status(self) -> None:
        builder = _builder(test_type="throughput")
        builder.add_query_result(_row("1", 1.0, test_type="throughput"))
        builder.set_validation_status("FAILED")
        validation = ValidationPhase(
            duration_ms=5,
            row_count_validation="PARTIAL",
            schema_validation="PASSED",
            data_integrity_checks="PASSED",
        )
        _with_phase(builder, _throughput_phase(success=True, throughput_at_size=1.0), validation)

        payload = build_result_payload(builder.build())

        assert payload["summary"]["validation"] == "failed"
        assert payload["phases"]["validation"]["status"] == "FAILED"

    def test_unrecognised_setup_status_is_passed_through(self) -> None:
        payload = self._payload(["SUCCESS"] * 3 + ["FAILED"] * 2, row_count_status="SKIPPED")

        assert payload["phases"]["validation"]["status"] == "SKIPPED"
