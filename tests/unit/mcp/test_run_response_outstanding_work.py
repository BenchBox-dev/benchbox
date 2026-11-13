from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from benchbox.core.results.models import (
    BenchmarkResults,
    ExecutionPhases,
    SetupPhase,
    ThroughputTestPhase,
)
from benchbox.core.results.schema import build_result_payload
from benchbox.mcp.jobs import _response_has_outstanding_work, derive_job_outcome
from benchbox.mcp.tools.benchmark import _build_run_response
from benchbox.utils.clock import mono_time

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _result(outstanding_work: Any) -> BenchmarkResults:
    throughput = ThroughputTestPhase(
        start_time="2026-01-01T00:00:00",
        end_time="2026-01-01T00:00:01",
        duration_ms=1000,
        num_streams=2,
        streams=[],
        total_queries_executed=0,
        throughput_at_size=None,
        success=False,
        outstanding_work=outstanding_work,
    )
    return BenchmarkResults(
        benchmark_name="tpch",
        platform="duckdb",
        scale_factor=0.01,
        execution_id="exec",
        timestamp=datetime(2026, 1, 1),
        duration_seconds=1.0,
        total_queries=0,
        successful_queries=0,
        failed_queries=0,
        execution_phases=ExecutionPhases(setup=SetupPhase(), throughput_test=throughput),
    )


def _build(result: BenchmarkResults, tmp_path: Path) -> dict[str, object]:
    return _build_run_response(
        result,
        execution_id="exec",
        results_dir=tmp_path,
        anonymize=True,
        start_time=mono_time(),
        platform="duckdb",
        benchmark="tpch",
        scale_factor=0.01,
        resolved_mode="sql",
        seed=None,
        iterations=3,
    )


def test_real_export_shape_with_outstanding_streams_is_detected() -> None:
    payload = build_result_payload(_result({"stream_ids": [1], "cleanup_state": "outstanding"}))

    assert payload["phases"]["throughput_test"]["outstanding_work"]["stream_ids"] == [1]
    assert _response_has_outstanding_work(payload)


def test_real_export_shape_after_quiescence_is_not_outstanding() -> None:
    payload = build_result_payload(_result({"stream_ids": [], "cleanup_state": "quiesced"}))

    assert not _response_has_outstanding_work(payload)


def test_outstanding_streams_are_detected_even_with_a_non_outstanding_cleanup_state() -> None:
    assert _response_has_outstanding_work(
        {"phases": {"throughput_test": {"outstanding_work": {"stream_ids": [2], "cleanup_state": "complete"}}}}
    )


def test_double_export_failure_still_reports_outstanding_work_from_the_result(tmp_path: Path) -> None:
    result = _result({"stream_ids": [4], "cleanup_state": "outstanding"})

    with (
        patch("benchbox.mcp.tools.benchmark.ResultExporter", side_effect=RuntimeError("export")),
        patch("benchbox.mcp.tools.benchmark.build_result_payload", side_effect=RuntimeError("payload")),
    ):
        response = _build(result, tmp_path)

    assert _response_has_outstanding_work(response)
    assert derive_job_outcome(response) == "incomplete"


def test_double_export_failure_without_outstanding_work_is_incomplete(tmp_path: Path) -> None:
    with (
        patch("benchbox.mcp.tools.benchmark.ResultExporter", side_effect=RuntimeError("export")),
        patch("benchbox.mcp.tools.benchmark.build_result_payload", side_effect=RuntimeError("payload")),
    ):
        response = _build(_result(None), tmp_path)

    assert not _response_has_outstanding_work(response)
    assert response["mcp_metadata"]["status"] == "incomplete"
    assert derive_job_outcome(response) == "incomplete"


def test_an_exported_but_empty_payload_is_not_an_export_failure(tmp_path: Path) -> None:
    with patch("benchbox.mcp.tools.benchmark._export_and_build_payload", return_value=("result.json", {})):
        response = _build(_result(None), tmp_path)

    assert response["mcp_metadata"]["status"] == "completed"
