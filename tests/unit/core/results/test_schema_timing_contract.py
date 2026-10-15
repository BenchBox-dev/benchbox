from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from benchbox.core.results.loader import reconstruct_benchmark_results
from benchbox.core.results.metrics import percentile_ms
from benchbox.core.results.models import (
    ExecutionPhases,
    PowerTestPhase,
    SetupPhase,
    ThroughputStream,
    ThroughputTestPhase,
)
from benchbox.core.results.query_execution import QueryExecutionContractError
from benchbox.core.results.schema import build_result_payload

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _result_with_queries(query_results: list[dict[str, Any]], execution_id: str = "run_1") -> SimpleNamespace:
    return SimpleNamespace(
        query_results=query_results,
        total_rows_loaded=0,
        data_loading_time=0.0,
        validation_status=None,
        execution_id=execution_id,
        timestamp=datetime(2026, 2, 12),
        duration_seconds=1.5,
        query_subset=None,
        benchmark_name="TPC-H Benchmark",
        benchmark_id="tpch",
        scale_factor=0.01,
        test_execution_type="power",
        platform="duckdb",
        platform_info=None,
        system_profile=None,
        table_statistics=None,
        execution_phases=None,
        cost_summary=None,
        execution_context=None,
        execution_metadata=None,
        tuning_config=None,
        power_at_size=None,
        throughput_at_size=None,
        qph_at_size=None,
        throughput_qph=None,
        composite_qph=None,
        geometric_mean_execution_time=None,
        tunings_applied=None,
        tuning_source_file=None,
        tuning_config_hash=None,
    )


def test_build_result_payload_redacts_platform_metadata_credential_sentinels() -> None:

    import json

    from benchbox.core.results.models import BenchmarkResults

    gates = (
        "RAW_CONFIG_GATE",
        "RAW_METADATA_GATE",
        "DEPLOYMENT_GATE",
        "CLOUD_GATE",
        "COMPUTE_GATE",
        "STORAGE_GATE",
    )
    result = BenchmarkResults(
        benchmark_name="synthetic",
        platform="synthetic",
        scale_factor=1.0,
        execution_id="timing-meta-gate",
        timestamp=datetime(2026, 2, 12),
        duration_seconds=0.1,
        total_queries=0,
        successful_queries=0,
        failed_queries=0,
        platform_info={"sort_key": "o_orderkey", "threads": 4},
        platform_raw_config={"password": "RAW_CONFIG_GATE", "threads": 4},
        platform_raw_metadata={"password": "RAW_METADATA_GATE"},
        platform_deployment={"token": "DEPLOYMENT_GATE"},
        platform_cloud={"access_key": "CLOUD_GATE"},
        platform_compute={"connection_string": "COMPUTE_GATE"},
        platform_storage={"secret": "STORAGE_GATE"},
    )
    text = json.dumps(build_result_payload(result), default=str)
    for gate in gates:
        assert gate not in text, f"timing payload path leaked {gate}"
    assert "o_orderkey" in text


def test_build_result_payload_accepts_consistent_duration_aliases() -> None:
    result = _result_with_queries(
        [
            {
                "query_id": "Q1",
                "status": "SUCCESS",
                "execution_time_seconds": 1.25,
                "execution_time": 1.25,
                "execution_time_ms": 1250,
                "rows_returned": 10,
                "iteration": 1,
                "stream_id": 5,
                "run_type": "measurement",
            }
        ]
    )
    result.execution_phases = ExecutionPhases(
        setup=SetupPhase(),
        throughput_test=ThroughputTestPhase(
            start_time="2026-02-12T00:00:00",
            end_time="2026-02-12T00:00:01",
            duration_ms=1000,
            num_streams=1,
            streams=[
                ThroughputStream(5, "start", "end", 1000, [], success=False, error_message="worker died"),
            ],
            total_queries_executed=1,
            throughput_at_size=None,
            success=False,
            errors=["Stream 5 failed: worker died"],
        ),
    )

    payload = build_result_payload(result)

    assert payload["queries"][0]["ms"] == 1250.0
    assert payload["run"]["streams"] == 1
    assert payload["phases"]["throughput_test"] == {
        "status": "FAILED",
        "duration_ms": 1000,
        "stream_results": [
            {"stream_id": 5, "success": False, "error": "worker died"},
        ],
        "errors": ["Stream 5 failed: worker died"],
    }
    assert "throughput_at_size" not in payload["summary"].get("tpc_metrics", {})


def test_refresh_evidence_is_serialized_as_phase_data_not_query_rows() -> None:
    result = _result_with_queries(
        [
            {
                "query_id": "Q1",
                "status": "SUCCESS",
                "execution_time_seconds": 1.0,
                "rows_returned": 4,
                "iteration": 1,
                "stream_id": 0,
                "run_type": "measurement",
            }
        ]
    )
    power_functions = [
        {"function": "RF1", "update_set": 3, "duration_ms": 12, "rows": 8, "status": "SUCCESS"},
        {"function": "RF2", "update_set": 3, "duration_ms": 13, "rows": 4, "status": "SUCCESS"},
    ]
    refresh_stream = {
        "first_set": 4,
        "pair_count": 2,
        "pairs": [{"update_set": set_id, "status": "SUCCESS", "functions": []} for set_id in (4, 5)],
        "start_time": "2026-02-12T00:00:00",
        "end_time": "2026-02-12T00:00:01",
        "status": "COMPLETED",
        "error": None,
    }
    result.execution_phases = ExecutionPhases(
        setup=SetupPhase(),
        power_test=PowerTestPhase(
            start_time="2026-02-12T00:00:00",
            end_time="2026-02-12T00:00:01",
            duration_ms=1000,
            query_executions=[],
            geometric_mean_time=1.0,
            power_at_size=1.0,
            refresh_first_set=3,
            refresh_functions=power_functions,
            query_count=22,
            success=True,
        ),
        throughput_test=ThroughputTestPhase(
            start_time="2026-02-12T00:00:00",
            end_time="2026-02-12T00:00:01",
            duration_ms=1000,
            num_streams=2,
            streams=[],
            total_queries_executed=44,
            throughput_at_size=1.0,
            refresh_stream=refresh_stream,
        ),
    )

    payload = build_result_payload(result)

    assert len(payload["queries"]) == 1
    assert payload["phases"]["power_test"]["refresh_functions"] == power_functions
    assert payload["phases"]["throughput_test"]["refresh_stream"] == refresh_stream


def test_throughput_outstanding_work_survives_schema_round_trip_without_query_rows() -> None:
    result = _result_with_queries([])
    result.execution_phases = ExecutionPhases(
        setup=SetupPhase(),
        throughput_test=ThroughputTestPhase(
            start_time="2026-02-12T00:00:00",
            end_time="2026-02-12T00:00:01",
            duration_ms=1000,
            num_streams=2,
            streams=[],
            total_queries_executed=0,
            throughput_at_size=None,
            success=False,
            errors=["deadline expired"],
            outstanding_work={"stream_ids": [0, 1], "cleanup_state": "outstanding"},
        ),
    )

    payload = build_result_payload(result)

    assert payload["queries"] == []
    assert payload["phases"]["throughput_test"]["outstanding_work"] == {
        "stream_ids": [0, 1],
        "cleanup_state": "outstanding",
    }
    reloaded = build_result_payload(reconstruct_benchmark_results(payload))
    assert reloaded["phases"]["throughput_test"] == payload["phases"]["throughput_test"]


def test_throughput_stream_numbering_survives_schema_round_trip() -> None:
    numbering = {"basis": "tpc_spec_throughput_streams_1_to_s", "first_stream_id": 1}
    result = _result_with_queries([])
    result.execution_phases = ExecutionPhases(
        setup=SetupPhase(),
        throughput_test=ThroughputTestPhase(
            start_time="2026-02-12T00:00:00",
            end_time="2026-02-12T00:00:01",
            duration_ms=1000,
            num_streams=2,
            streams=[
                ThroughputStream(1, "start", "end", 500, [], success=True),
                ThroughputStream(2, "start", "end", 500, [], success=True),
            ],
            total_queries_executed=0,
            throughput_at_size=None,
            success=True,
            stream_numbering=numbering,
        ),
    )

    payload = build_result_payload(result)

    assert payload["phases"]["throughput_test"]["stream_numbering"] == numbering
    assert [stream["stream_id"] for stream in payload["phases"]["throughput_test"]["stream_results"]] == [1, 2]
    reloaded = build_result_payload(reconstruct_benchmark_results(payload))
    assert reloaded["phases"]["throughput_test"] == payload["phases"]["throughput_test"]


def _combined_rows(throughput_streams: int) -> list[dict[str, Any]]:
    power = [
        {
            "query_id": str(query),
            "status": "SUCCESS",
            "execution_time_seconds": 0.5,
            "rows_returned": 1,
            "iteration": iteration,
            "stream_id": 0,
            "run_type": "measurement",
            "test_type": "power",
        }
        for iteration in (1, 2)
        for query in (1, 2)
    ]
    throughput = [
        {
            "query_id": str(query),
            "status": "SUCCESS",
            "execution_time_seconds": 1.0,
            "rows_returned": 1,
            "iteration": 1,
            "stream_id": stream,
            "run_type": "measurement",
            "test_type": "throughput",
        }
        for stream in range(1, throughput_streams + 1)
        for query in (1, 2)
    ]
    return power + throughput


def test_combined_run_counts_only_throughput_streams() -> None:
    payload = build_result_payload(_result_with_queries(_combined_rows(4)))

    assert payload["run"]["streams"] == 4


def test_power_only_run_reports_one_stream() -> None:
    rows = [row for row in _combined_rows(2) if row["test_type"] == "power"]

    assert build_result_payload(_result_with_queries(rows))["run"]["streams"] == 1


def test_throughput_only_run_counts_every_stream() -> None:
    rows = [row for row in _combined_rows(3) if row["test_type"] == "throughput"]

    assert build_result_payload(_result_with_queries(rows))["run"]["streams"] == 3


def test_combined_run_qphh_reports_the_throughput_stream_count() -> None:
    from benchbox.core.results.metrics import TPCMetricsCalculator

    payload = build_result_payload(_result_with_queries(_combined_rows(4)))
    power_data = {"benchmark": {"scale_factor": 1.0}, "summary": {"tpc_metrics": {"power_at_size": 10.0}}}
    throughput_data = {
        "benchmark": {"scale_factor": 1.0},
        "run": payload["run"],
        "summary": {"tpc_metrics": {"throughput_at_size": 5.0}},
    }

    result = TPCMetricsCalculator.compute_qphh_result(power_data, throughput_data, scale_factor=1.0)

    assert result["num_streams"] == 4


def test_build_result_payload_rejects_conflicting_duration_aliases() -> None:
    result = _result_with_queries(
        [
            {
                "query_id": "Q1",
                "status": "SUCCESS",
                "execution_time_seconds": 1.25,
                "execution_time": 9.99,
                "rows_returned": 10,
            }
        ]
    )

    with pytest.raises(QueryExecutionContractError, match="Conflicting query duration representations"):
        build_result_payload(result)


def test_build_result_payload_preserves_positive_sub_ms_measurements() -> None:
    result = _result_with_queries(
        [
            {
                "query_id": "Q1",
                "status": "SUCCESS",
                "execution_time_seconds": 0.00004,
                "rows_returned": 1,
                "iteration": 1,
                "stream_id": 0,
                "run_type": "measurement",
            }
        ],
        execution_id="run_sub_ms",
    )

    payload = build_result_payload(result)

    assert payload["queries"][0]["ms"] == 0.04
    assert payload["summary"]["timing"]["min_ms"] == 0.04
    assert payload["summary"]["timing"]["max_ms"] == 0.04


def test_build_result_payload_uses_shared_nearest_rank_percentiles() -> None:
    times_ms = [float(value) for value in range(1, 101)]
    result = _result_with_queries(
        [
            {
                "query_id": f"Q{index}",
                "status": "SUCCESS",
                "execution_time_seconds": value / 1000,
                "rows_returned": 1,
            }
            for index, value in enumerate(times_ms, start=1)
        ]
    )

    timing = build_result_payload(result)["summary"]["timing"]

    assert timing["p90_ms"] == percentile_ms(times_ms, 0.90)
    assert timing["p95_ms"] == percentile_ms(times_ms, 0.95)
    assert timing["p99_ms"] == percentile_ms(times_ms, 0.99)


def test_build_result_payload_run_type_contract_and_compat_fallback() -> None:
    result = _result_with_queries(
        [
            {
                "query_id": "Q1",
                "status": "SUCCESS",
                "execution_time_seconds": 1.25,
                "rows_returned": 10,
                "iteration": 1,
                "stream_id": 0,
                "run_type": "warmup",
            },
            {
                "query_id": "Q2",
                "status": "SUCCESS",
                "execution_time_seconds": 1.0,
                "rows_returned": 8,
                "iteration": 0,
                "stream_id": 0,
            },
        ],
        execution_id="run_2",
    )

    payload = build_result_payload(result)
    queries = {q["id"]: q for q in payload["queries"]}

    assert queries["1"]["run_type"] == "warmup"

    assert queries["2"]["run_type"] == "warmup"


def test_build_result_payload_preserves_dataframe_skip_summary() -> None:
    result = _result_with_queries(
        [
            {
                "query_id": "DF_SKIP_SUMMARY",
                "status": "SUCCESS",
                "execution_time_seconds": 0.0,
                "rows_returned": 100,
                "iteration": 0,
                "stream_id": 0,
                "run_type": "summary",
                "dataframe_skip_summary": {
                    "executed_total": 13,
                    "skipped_total": 100,
                    "executed_by_category": {},
                    "skipped_by_category": {},
                },
            }
        ],
        execution_id="run_df_skip_summary",
    )

    payload = build_result_payload(result)

    assert payload["queries"][0]["dataframe_skip_summary"] == {
        "executed_total": 13,
        "skipped_total": 100,
        "executed_by_category": {},
        "skipped_by_category": {},
    }


def test_build_result_payload_counts_skipped_queries_separately() -> None:
    result = _result_with_queries(
        [
            {
                "query_id": "Q1",
                "status": "SUCCESS",
                "execution_time_seconds": 0.1,
                "iteration": 1,
                "stream_id": 0,
                "run_type": "measurement",
            },
            {
                "query_id": "Q2",
                "status": "SKIPPED",
                "execution_time_seconds": 0.0,
                "iteration": 1,
                "stream_id": 0,
                "run_type": "measurement",
            },
        ],
        execution_id="run_skipped_queries",
    )

    payload = build_result_payload(result)

    assert payload["summary"]["queries"] == {"total": 2, "passed": 1, "failed": 0, "skipped": 1}
    assert "errors" not in payload
