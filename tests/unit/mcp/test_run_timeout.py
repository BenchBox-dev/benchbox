from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from benchbox.mcp.jobs import derive_job_outcome
from benchbox.utils.run_deadline import TIMED_OUT_VALIDATION_STATUS

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

TIMEOUT = {"timeout_seconds": 5, "phase": "execute", "query_id": "Q2"}


def _call(tmp_path: Path, **arguments: Any) -> tuple[dict[str, Any], Mock]:
    from benchbox.mcp import create_server
    from benchbox.mcp.tools import benchmark as benchmark_tools
    from tests.unit.mcp.public_api import call_tool

    server = create_server(results_dir=tmp_path, charts_dir=tmp_path)
    with patch.object(benchmark_tools, "_run_benchmark_impl", return_value={"status": "completed"}) as run_impl:
        response = call_tool(
            server, "run_benchmark", platform="duckdb", benchmark="tpch", scale_factor=0.01, **arguments
        )
    return response, run_impl


def _run_core(tmp_path: Path, *, timeout_seconds: int | None, result: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    from benchbox.mcp.tools import benchmark as benchmark_tools

    benchmark_instance = SimpleNamespace(output_dir=tmp_path / "benchmark_runs" / "datagen" / "tpch_sf001")
    with (
        patch.object(benchmark_tools, "get_all_benchmarks", return_value={"tpch": {"display_name": "TPC-H"}}),
        patch("benchbox.core.system.SystemProfiler") as profiler_cls,
        patch.object(benchmark_tools, "_export_and_build_payload", return_value=("result.json", {}, None)),
        patch.object(benchmark_tools, "_attach_summary_charts"),
        patch("benchbox.core.platform_config.get_platform_config", return_value={}),
        patch("benchbox.core.run_service.execute_run", return_value=result) as execute,
    ):
        profiler_cls.return_value.get_system_profile.return_value = None
        response = benchmark_tools._execute_mcp_run_via_core(
            platform="duckdb",
            benchmark="tpch",
            benchmark_class=Mock(return_value=benchmark_instance),
            scale_factor=0.01,
            queries=None,
            phases=["load", "power"],
            resolved_mode="sql",
            capture_plans=False,
            seed=None,
            iterations=None,
            timeout_seconds=timeout_seconds,
            normalized_platform_options={},
            results_dir=tmp_path / "results",
            execution_id="exec-1",
            start_time=100.0,
            anonymize=False,
        )
    return response, execute.call_args.kwargs


def test_a_negative_limit_is_rejected_before_any_work(tmp_path) -> None:
    response, run_impl = _call(tmp_path, timeout_seconds=-1)

    assert response["error"] is True
    assert response["status"] == "failed"
    assert response["error_code"] == "VALIDATION_ERROR"
    run_impl.assert_not_called()


def test_a_fractional_limit_is_refused_by_the_tool_schema(tmp_path) -> None:
    with pytest.raises(Exception, match="timeout_seconds"):
        _call(tmp_path, timeout_seconds=1.5)


def test_the_limit_is_passed_to_the_run(tmp_path) -> None:
    _, with_limit = _call(tmp_path, timeout_seconds=90)
    _, without_limit = _call(tmp_path)

    assert with_limit.call_args.kwargs["timeout_seconds"] == 90
    assert without_limit.call_args.kwargs["timeout_seconds"] is None


@pytest.mark.parametrize(("value", "expected"), [(90, 90), (0, None), (None, None)])
def test_the_limit_reaches_the_shared_run_entry_as_an_option(tmp_path, value, expected) -> None:
    _, kwargs = _run_core(tmp_path, timeout_seconds=value, result=SimpleNamespace())

    assert kwargs["config"].options.get("timeout_seconds") == expected


def test_a_timed_out_run_is_reported_as_timed_out_and_the_job_is_incomplete_not_failed(tmp_path) -> None:
    result = SimpleNamespace(validation_status=TIMED_OUT_VALIDATION_STATUS, validation_details={"timeout": TIMEOUT})

    response, _ = _run_core(tmp_path, timeout_seconds=5, result=result)

    assert response["mcp_metadata"]["status"] == "timed_out"
    assert response["mcp_metadata"]["timeout"] == TIMEOUT
    assert response.get("status") != "failed"
    assert derive_job_outcome(response) == "incomplete"
