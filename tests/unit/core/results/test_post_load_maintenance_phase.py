from __future__ import annotations

from datetime import datetime

import pytest

from benchbox.core.results.loader import reconstruct_benchmark_results
from benchbox.core.results.models import (
    BenchmarkResults,
    DataLoadingPhase,
    ExecutionPhases,
    PostLoadMaintenancePhase,
    SetupPhase,
)
from benchbox.core.results.schema import SchemaV2Validator, build_result_payload

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _result(maintenance: PostLoadMaintenancePhase | None) -> BenchmarkResults:
    return BenchmarkResults(
        benchmark_name="TPC-H",
        platform="clickhouse",
        scale_factor=0.01,
        execution_id="post-load-maintenance",
        timestamp=datetime(2026, 10, 4, 12, 0, 0),
        duration_seconds=12.5,
        total_queries=1,
        successful_queries=1,
        failed_queries=0,
        query_results=[
            {
                "query_id": "Q1",
                "status": "SUCCESS",
                "execution_time_seconds": 1.25,
                "rows_returned": 1,
                "iteration": 1,
                "stream_id": 0,
                "run_type": "measurement",
            }
        ],
        execution_phases=ExecutionPhases(
            setup=SetupPhase(
                data_loading=DataLoadingPhase(
                    duration_ms=3500, status="SUCCESS", total_rows_loaded=10, tables_loaded=8, per_table_stats={}
                ),
                post_load_maintenance=maintenance,
            )
        ),
    )


def test_phase_is_exported_next_to_data_loading_and_validates() -> None:
    payload = build_result_payload(
        _result(PostLoadMaintenancePhase(duration_ms=4200, status="SUCCESS", tables_processed=8))
    )

    SchemaV2Validator().validate(payload)
    assert payload["phases"]["post_load_maintenance"] == {
        "status": "SUCCESS",
        "duration_ms": 4200,
        "tables_processed": 8,
    }
    assert payload["phases"]["data_loading"]["duration_ms"] == 3500


def test_phase_is_omitted_when_no_maintenance_ran() -> None:
    payload = build_result_payload(_result(None))

    SchemaV2Validator().validate(payload)
    assert "post_load_maintenance" not in payload["phases"]


def test_a_bundle_with_the_phase_still_loads() -> None:
    payload = build_result_payload(
        _result(PostLoadMaintenancePhase(duration_ms=4200, status="SUCCESS", tables_processed=8))
    )

    reconstructed = reconstruct_benchmark_results(payload)

    assert reconstructed.benchmark_name == "TPC-H"
    assert reconstructed.platform == "clickhouse"


def test_phase_is_ordered_between_data_loading_and_validation() -> None:
    payload = build_result_payload(
        _result(PostLoadMaintenancePhase(duration_ms=4200, status="SUCCESS", tables_processed=8))
    )

    order = list(payload["phases"])

    assert order.index("data_loading") < order.index("post_load_maintenance") < order.index("validation")


