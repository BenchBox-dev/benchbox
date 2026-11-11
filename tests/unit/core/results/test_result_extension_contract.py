from __future__ import annotations

from datetime import datetime

import pytest

from benchbox.core.results.loader import reconstruct_benchmark_results
from benchbox.core.results.models import (
    BenchmarkResults,
    ExecutionPhases,
    MigrationPhase,
    NativeComparison,
    NativeComparisonEntry,
    SetupPhase,
)
from benchbox.core.results.schema import SchemaV2Validator, build_result_payload
from benchbox.core.results.schema_policy import PUBLIC_SUBMISSION_SCHEMA_POLICY

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _extension_result() -> BenchmarkResults:
    return BenchmarkResults(
        benchmark_name="TPC-H",
        platform="pg_duckdb",
        scale_factor=0.01,
        execution_id="extension-contract",
        timestamp=datetime(2026, 5, 22, 12, 0, 0),
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
        platform_info={"platform_version": "16.2", "configuration": {"work_mem": "64MB"}},
        platform_deployment={"deployment_type": "local", "endpoint_class": "localhost_port"},
        platform_cloud={"provider": "none"},
        platform_compute={"instance_type": "developer-laptop"},
        platform_storage={"storage_type": "local_disk"},
        platform_raw_config={"host": "localhost", "port": 5432},
        platform_raw_metadata={"server_version": "16.2"},
        execution_phases=ExecutionPhases(
            setup=SetupPhase(),
            migration=MigrationPhase(
                duration_ms=2500,
                status="COMPLETED",
                tables_migrated=8,
                tables_failed=0,
                storage_before_bytes=1000,
                storage_after_bytes=700,
                storage_delta_bytes=-300,
                per_table_stats={},
            ),
        ),
        native_comparison=NativeComparison(
            generated_at="2026-05-22T12:00:00",
            scale_factor=0.01,
            total_queries=1,
            mean_delta_ms=12.5,
            max_delta_ms=12.5,
            entries=[
                NativeComparisonEntry(
                    query_id="Q1",
                    pg_duckdb_ms=42.5,
                    duckdb_ms=30.0,
                    delta_ms=12.5,
                )
            ],
        ),
    )


def test_platform_specific_extensions_keep_current_schema_v2_locations() -> None:
    payload = build_result_payload(_extension_result())

    SchemaV2Validator().validate(payload)
    assert PUBLIC_SUBMISSION_SCHEMA_POLICY.evaluate(payload["result_schema_version"]).accepted
    assert payload["version"] == "2.4"

    assert "migration" not in payload
    assert payload["phases"]["migration"] == {
        "status": "COMPLETED",
        "duration_ms": 2500,
        "tables_migrated": 8,
        "tables_failed": 0,
        "storage_before_bytes": 1000,
        "storage_after_bytes": 700,
        "storage_delta_bytes": -300,
    }
    assert payload["comparisons"]["native_duckdb"]["queries"] == [
        {"id": "Q1", "pg_duckdb_ms": 42.5, "duckdb_ms": 30.0, "delta_ms": 12.5}
    ]
    assert payload["platform"]["deployment"]["deployment_type"] == "local"
    assert payload["platform"]["cloud"]["provider"] == "none"
    assert payload["platform"]["compute"]["instance_type"] == "developer-laptop"
    assert payload["platform"]["storage"]["storage_type"] == "local_disk"
    assert payload["platform"]["raw_config"] == {"host": "localhost", "port": 5432}
    assert payload["platform"]["raw_metadata"] == {"server_version": "16.2"}


def test_platform_specific_extensions_round_trip_through_loader() -> None:
    payload = build_result_payload(_extension_result())

    reconstructed = reconstruct_benchmark_results(payload)
    round_trip = build_result_payload(reconstructed)

    assert reconstructed.execution_phases is not None
    assert reconstructed.execution_phases.migration is not None
    assert reconstructed.execution_phases.migration.tables_migrated == 8
    assert reconstructed.native_comparison is not None
    assert reconstructed.native_comparison.entries[0].delta_ms == 12.5
    assert reconstructed.platform_deployment == payload["platform"]["deployment"]
    assert reconstructed.platform_cloud == payload["platform"]["cloud"]
    assert reconstructed.platform_compute == payload["platform"]["compute"]
    assert reconstructed.platform_storage == payload["platform"]["storage"]
    assert reconstructed.platform_raw_config == payload["platform"]["raw_config"]
    assert reconstructed.platform_raw_metadata == payload["platform"]["raw_metadata"]
    assert round_trip["comparisons"] == payload["comparisons"]
    assert round_trip["phases"]["migration"] == payload["phases"]["migration"]


def test_plan_capture_error_reaches_the_exported_query_row() -> None:
    result = _extension_result()
    result.query_results[0]["plan_capture_error"] = "TypeError: unsupported operand"

    payload = build_result_payload(result)

    entry = next(q for q in payload["queries"] if q["id"] == "1")
    assert entry["plan_capture_error"] == "TypeError: unsupported operand"


def test_plan_capture_error_survives_export_load_reexport_round_trip() -> None:
    result = _extension_result()
    result.query_results[0]["plan_capture_error"] = "TypeError: unsupported operand"

    payload = build_result_payload(result)
    reconstructed = reconstruct_benchmark_results(payload)
    round_trip = build_result_payload(reconstructed)

    reconstructed_row = next(q for q in reconstructed.query_results if q.get("query_id") == "1")
    assert reconstructed_row["plan_capture_error"] == "TypeError: unsupported operand"

    round_trip_entry = next(q for q in round_trip["queries"] if q["id"] == "1")
    assert round_trip_entry["plan_capture_error"] == "TypeError: unsupported operand"


