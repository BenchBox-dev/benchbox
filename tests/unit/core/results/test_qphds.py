from __future__ import annotations

import math
from dataclasses import replace

import pytest

from benchbox.core.results.builder import BenchmarkInfoInput, ResultBuilder
from benchbox.core.results.metrics import TPCMetricsCalculator
from benchbox.core.results.models import (
    DataLoadingPhase,
    ExecutionPhases,
    MaintenanceOperation,
    MaintenanceTestPhase,
    PowerTestPhase,
    SetupPhase,
    TableLoadingStats,
    ThroughputStream,
    ThroughputTestPhase,
)
from benchbox.core.results.platform_info import PlatformInfoInput
from benchbox.core.results.qphds import evaluate_qphds
from benchbox.core.results.schema import build_result_payload
from benchbox.core.throughput.result import throughput_stream_numbering

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.tpcds]

STREAMS = 4
STATEMENTS = 103
EXPECTED_QPHDS = 11453.0


def _throughput(
    first_stream_id: int, seconds: float, *, success: bool = True, total_queries: int | None = None
) -> ThroughputTestPhase:
    return ThroughputTestPhase(
        start_time="2026-01-01T00:00:00",
        end_time="2026-01-01T00:10:00",
        duration_ms=int(seconds * 1000),
        num_streams=STREAMS,
        streams=[
            ThroughputStream(
                stream_id=stream_id,
                start_time="2026-01-01T00:00:00",
                end_time="2026-01-01T00:10:00",
                duration_ms=int(seconds * 1000),
                query_executions=[],
            )
            for stream_id in range(first_stream_id, first_stream_id + STREAMS)
        ],
        total_queries_executed=total_queries if total_queries is not None else STATEMENTS * STREAMS,
        throughput_at_size=1.0,
        success=success,
        stream_numbering=throughput_stream_numbering(first_stream_id),
    )


def _maintenance(first_set: int, seconds: float, *, success: bool = True) -> MaintenanceTestPhase:
    return MaintenanceTestPhase(
        start_time="2026-01-01T00:00:00",
        end_time="2026-01-01T00:00:10",
        duration_ms=int(seconds * 1000),
        maintenance_operations=[
            MaintenanceOperation(
                operation="RF1",
                operation_type="insert",
                table="store_sales",
                execution_time_ms=10,
                rows_affected=1,
                status="SUCCESS",
                refresh_set=refresh_set,
            )
            for refresh_set in range(first_set, first_set + STREAMS // 2)
        ],
        query_executions=[],
        success=success,
    )


def _loading(seconds: float = 1000.0, *, status: str = "SUCCESS", table_status: str = "SUCCESS") -> SetupPhase:
    return SetupPhase(
        data_loading=DataLoadingPhase(
            duration_ms=int(seconds * 1000),
            status=status,
            total_rows_loaded=1,
            tables_loaded=1,
            per_table_stats={"store_sales": TableLoadingStats(rows=1, load_time_ms=1, status=table_status)},
        )
    )


def _phases() -> ExecutionPhases:
    return ExecutionPhases(
        setup=_loading(),
        power_test=PowerTestPhase(
            start_time="2026-01-01T00:00:00",
            end_time="2026-01-01T00:01:40",
            duration_ms=100_000,
            query_executions=[],
            geometric_mean_time=1.0,
            power_at_size=100.0,
            query_count=STATEMENTS,
            success=True,
        ),
        throughput_test=_throughput(1, 200.0),
        maintenance_test=_maintenance(1, 10.0),
        throughput_test_2=_throughput(STREAMS + 1, 300.0),
        maintenance_test_2=_maintenance(STREAMS // 2 + 1, 20.0),
    )


def _evaluate(phases: ExecutionPhases, **overrides):
    arguments = {
        "scale_factor": 1.0,
        "compliance_class": "official",
        "failed_query_count": 0,
        "query_subset": None,
    }
    arguments.update(overrides)
    return evaluate_qphds(phases, **arguments)


class TestQphDSFormula:
    def test_spec_formula_terms_and_value(self) -> None:
        terms = TPCMetricsCalculator.calculate_qphds_terms(1.0, STREAMS, 100.0, [200.0, 300.0], [10.0, 20.0], 1000.0)

        assert terms == pytest.approx(
            {
                "q": 396.0,
                "t_pt_hours": 100.0 * STREAMS / 3600.0,
                "t_tt_hours": 500.0 / 3600.0,
                "t_dm_hours": 30.0 / 3600.0,
                "t_ld_hours": 0.01 * STREAMS * 1000.0 / 3600.0,
            }
        )
        value = TPCMetricsCalculator.calculate_qphds_at_size(1.0, STREAMS, 100.0, [200.0, 300.0], [10.0, 20.0], 1000.0)
        assert value == EXPECTED_QPHDS

    def test_value_is_truncated_not_rounded(self) -> None:
        unrounded = 1.0 * 396.0 / math.prod([100.0 * 4 / 3600, 500 / 3600, 30 / 3600, 0.04 * 1000 / 3600]) ** 0.25
        assert round(unrounded) == EXPECTED_QPHDS + 1
        assert math.floor(unrounded) == EXPECTED_QPHDS

    def test_value_scales_with_scale_factor(self) -> None:
        value = TPCMetricsCalculator.calculate_qphds_at_size(10.0, STREAMS, 100.0, [200.0, 300.0], [10.0, 20.0], 1000.0)
        assert value == 114536.0

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"num_streams": 3},
            {"num_streams": 2},
            {"power_seconds": 0.0},
            {"power_seconds": None},
            {"throughput_seconds": [200.0]},
            {"throughput_seconds": [200.0, float("nan")]},
            {"maintenance_seconds": [10.0, -1.0]},
            {"load_seconds": 0.0},
            {"load_seconds": float("inf")},
            {"scale_factor": 0.0},
        ],
    )
    def test_invalid_inputs_are_refused(self, kwargs: dict) -> None:
        arguments = {
            "scale_factor": 1.0,
            "num_streams": STREAMS,
            "power_seconds": 100.0,
            "throughput_seconds": [200.0, 300.0],
            "maintenance_seconds": [10.0, 20.0],
            "load_seconds": 1000.0,
        }
        arguments.update(kwargs)

        assert TPCMetricsCalculator.qphds_input_problems(**arguments)
        with pytest.raises(ValueError):
            TPCMetricsCalculator.calculate_qphds_at_size(**arguments)


class TestQphDSEvaluation:
    def test_valid_inputs_yield_the_spec_value(self) -> None:
        evaluation = _evaluate(_phases())

        assert evaluation.qphds_at_size == EXPECTED_QPHDS
        assert evaluation.unavailable == []
        assert evaluation.terms["t_ld_hours"] == pytest.approx(0.04 * 1000.0 / 3600.0)

    @pytest.mark.parametrize(
        ("label", "mutate", "overrides", "reason"),
        [
            ("subscale", None, {"compliance_class": "unofficial_subscale"}, "not official"),
            ("failed queries", None, {"failed_query_count": 2}, "queries failed"),
            ("subset", None, {"query_subset": ["1", "2"]}, "query subset"),
            ("no throughput 2", {"throughput_test_2": None}, {}, "Throughput Test 2 did not run"),
            ("no maintenance 2", {"maintenance_test_2": None}, {}, "Data Maintenance Test 2 did not run"),
            ("no maintenance 1", {"maintenance_test": None}, {}, "Data Maintenance Test 1 did not run"),
            (
                "failed maintenance 2",
                {"maintenance_test_2": _maintenance(3, 20.0, success=False)},
                {},
                "Data Maintenance Test 2 did not complete",
            ),
            (
                "failed throughput 1",
                {"throughput_test": _throughput(1, 200.0, success=False)},
                {},
                "Throughput Test 1 did not complete",
            ),
            (
                "second throughput reuses streams 1 to S",
                {"throughput_test_2": _throughput(1, 300.0)},
                {},
                "Throughput Test 2 did not use streams 5 to 8",
            ),
            (
                "second maintenance repeats refresh sets",
                {"maintenance_test_2": _maintenance(1, 20.0)},
                {},
                "refresh sets that follow",
            ),
            ("load not captured", {"setup": SetupPhase()}, {}, "database load time was not captured"),
            (
                "load failed",
                {"setup": _loading(status="FAILED")},
                {},
                "database load data loading did not complete",
            ),
            (
                "load reused",
                {"setup": _loading(table_status="REUSED")},
                {},
                "database load was reused",
            ),
            (
                "short streams",
                {"throughput_test_2": _throughput(STREAMS + 1, 300.0, total_queries=STATEMENTS * STREAMS - 1)},
                {},
                "streams did not execute all 99 queries",
            ),
            (
                "non contiguous refresh sets",
                {"maintenance_test": _maintenance(1, 10.0), "maintenance_test_2": _maintenance(4, 20.0)},
                {},
                "refresh sets that follow",
            ),
            (
                "legacy 99 statement power stream",
                {"power_test": replace(_phases().power_test, query_count=99)},
                {},
                "Power Test ran 99 statements instead of 103",
            ),
            (
                "legacy 99 statement throughput streams",
                {"throughput_test": _throughput(1, 200.0, total_queries=99 * STREAMS)},
                {},
                "streams did not execute all 99 queries",
            ),
            ("zero load time", {"setup": _loading(0.0)}, {}, "database load elapsed time"),
            ("no power test", {"power_test": None}, {}, "Power Test did not run"),
            (
                "short power test",
                {"power_test": replace(_phases().power_test, query_count=STATEMENTS - 1)},
                {},
                "Power Test ran 102 statements",
            ),
        ],
    )
    def test_any_invalid_input_withholds_the_metric(self, label, mutate, overrides, reason) -> None:
        phases = replace(_phases(), **(mutate or {}))

        evaluation = _evaluate(phases, **overrides)

        assert evaluation.qphds_at_size is None, label
        assert evaluation.terms is None
        assert any(reason in item for item in evaluation.unavailable), evaluation.unavailable

    def test_missing_phases_are_withheld(self) -> None:
        evaluation = _evaluate(None)

        assert evaluation.qphds_at_size is None
        assert evaluation.unavailable

    def test_two_streams_is_below_the_spec_minimum(self) -> None:
        phases = _phases()
        phases = replace(
            phases,
            throughput_test=replace(phases.throughput_test, num_streams=2, streams=phases.throughput_test.streams[:2]),
            throughput_test_2=replace(
                phases.throughput_test_2, num_streams=2, streams=phases.throughput_test_2.streams[:2]
            ),
        )

        evaluation = _evaluate(phases)

        assert evaluation.qphds_at_size is None
        assert any("even number of at least 4" in item for item in evaluation.unavailable)


def _payload(phases: ExecutionPhases, scale_factor: float, compliance_class: str) -> dict:
    builder = ResultBuilder(
        benchmark=BenchmarkInfoInput(
            name="TPC-DS",
            scale_factor=scale_factor,
            test_type="combined",
            benchmark_id="tpcds",
            compliance_class=compliance_class,
        ),
        platform=PlatformInfoInput(name="DuckDB", execution_mode="sql"),
    )
    builder.set_execution_phases(phases)
    return build_result_payload(builder.build())


class TestQphDSExport:
    def test_official_result_exports_qphds_with_terms_and_both_second_phases(self) -> None:
        payload = _payload(_phases(), 1.0, "official")

        metrics = payload["summary"]["tpc_metrics"]
        assert metrics["qphds_at_size"] == EXPECTED_QPHDS
        assert metrics["qphds_terms"]["q"] == 396.0
        assert "qphds_unavailable" not in metrics
        assert payload["phases"]["throughput_test_2"]["status"] == "COMPLETED"
        assert payload["phases"]["throughput_test_2"]["stream_numbering"]["first_stream_id"] == STREAMS + 1
        assert payload["phases"]["maintenance_test_2"]["status"] == "COMPLETED"

    def test_subscale_result_is_not_exported_as_official(self) -> None:
        payload = _payload(_phases(), 0.01, "unofficial_subscale")

        metrics = payload["summary"]["tpc_metrics"]
        assert "qphds_at_size" not in metrics
        assert any("not official" in reason for reason in metrics["qphds_unavailable"])

    def test_missing_second_throughput_test_is_not_exported(self) -> None:
        payload = _payload(replace(_phases(), throughput_test_2=None), 1.0, "official")

        metrics = payload["summary"]["tpc_metrics"]
        assert "qphds_at_size" not in metrics
        assert any("Throughput Test 2 did not run" in reason for reason in metrics["qphds_unavailable"])


class TestQphDSLoadTime:
    def test_load_time_sums_every_load_side_phase(self) -> None:
        from benchbox.core.results.models import (
            MergeSettlePhase,
            PostLoadMaintenancePhase,
            SchemaCreationPhase,
            StatisticsGatheringPhase,
        )

        setup = _loading(1000.0)
        setup.schema_creation = SchemaCreationPhase(
            duration_ms=100_000,
            status="SUCCESS",
            tables_created=1,
            constraints_applied=0,
            indexes_created=0,
            per_table_creation={},
        )
        setup.statistics_gathering = StatisticsGatheringPhase(duration_ms=200_000, status="COMPLETED", stats_mode="all")
        setup.post_load_maintenance = PostLoadMaintenancePhase(duration_ms=300_000)
        setup.merge_settle = MergeSettlePhase(settled=True, waited_seconds=400.0, active_parts=0, timeout_seconds=900.0)

        evaluation = _evaluate(replace(_phases(), setup=setup))

        assert evaluation.terms["t_ld_hours"] == pytest.approx(0.01 * STREAMS * 2000.0 / 3600.0)

    def test_failed_load_side_phase_withholds_the_metric(self) -> None:
        from benchbox.core.results.models import StatisticsGatheringPhase

        setup = _loading(1000.0)
        setup.statistics_gathering = StatisticsGatheringPhase(duration_ms=1, status="FAILED", stats_mode="all")

        evaluation = _evaluate(replace(_phases(), setup=setup))

        assert evaluation.qphds_at_size is None
        assert any("statistics gathering did not complete" in item for item in evaluation.unavailable)

    def test_skipped_schema_creation_withholds_the_metric(self) -> None:
        from benchbox.core.results.models import SchemaCreationPhase

        setup = _loading(1000.0)
        setup.schema_creation = SchemaCreationPhase(
            duration_ms=0,
            status="SKIPPED",
            tables_created=0,
            constraints_applied=0,
            indexes_created=0,
            per_table_creation={},
        )

        evaluation = _evaluate(replace(_phases(), setup=setup))

        assert evaluation.qphds_at_size is None


class TestExactFloor:
    def test_exact_integer_boundary_is_not_floored_down(self) -> None:
        assert TPCMetricsCalculator.floor_qphds(1.0, 396.0, 0.0001) == 3960.0
        assert TPCMetricsCalculator.floor_qphds(1.0, 396.0, 16.0) == 198.0

    def test_just_below_the_boundary_floors_down(self) -> None:
        assert TPCMetricsCalculator.floor_qphds(1.0, 396.0, 16.000001) == 197.0


def test_exported_result_round_trips_through_save_and_load(tmp_path) -> None:
    import json

    from benchbox.core.results.loader import reconstruct_benchmark_results

    first = _payload(_phases(), 1.0, "official")
    reloaded = reconstruct_benchmark_results(first)
    second = build_result_payload(reloaded)

    for phase in ("throughput_test", "throughput_test_2", "maintenance_test", "maintenance_test_2"):
        assert second["phases"][phase] == first["phases"][phase], phase
    assert second["summary"]["tpc_metrics"]["qphds_at_size"] == EXPECTED_QPHDS
    json.dumps(second)


def test_reused_database_with_statistics_gathering_withholds_the_metric() -> None:
    from benchbox.core.results.models import StatisticsGatheringPhase

    setup = _loading(0.0)
    setup.database_reused = True
    setup.statistics_gathering = StatisticsGatheringPhase(duration_ms=200_000, status="COMPLETED", stats_mode="all")

    evaluation = _evaluate(replace(_phases(), setup=setup))

    assert evaluation.qphds_at_size is None
    assert any("database load was reused" in item for item in evaluation.unavailable)


def test_statement_count_is_derived_from_the_stream_generator_constant() -> None:
    from benchbox.core.tpcds.streams import MULTI_PART_QUERY_IDS

    assert 99 + len(MULTI_PART_QUERY_IDS) == STATEMENTS
    assert _evaluate(_phases()).qphds_at_size == EXPECTED_QPHDS
