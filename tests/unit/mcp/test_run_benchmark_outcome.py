from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import anyio
import pytest

from benchbox.core.results.models import BenchmarkResults
from benchbox.mcp import create_server
from benchbox.mcp.tools.benchmark import _build_run_response
from benchbox.utils.clock import mono_time
from tests.fixtures.result_dict_fixtures import write_v2_result_file

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def _result() -> BenchmarkResults:
    return BenchmarkResults(
        benchmark_name="tpch",
        platform="duckdb",
        scale_factor=0.01,
        execution_id="exec",
        timestamp=datetime(2026, 1, 1),
        duration_seconds=1.0,
        total_queries=5,
        successful_queries=5,
        failed_queries=0,
        total_execution_time=1.0,
    )


def _build(result: Any, tmp_path: Path) -> dict[str, Any]:
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
    )


def _call_run_benchmark(**patches: Any):
    server = create_server()

    async def invoke():
        return await server.call_tool("run_benchmark", {"platform": "duckdb", "benchmark": "tpch"})

    with (
        patch("benchbox.mcp.tools.benchmark.get_public_benchmark_class", return_value=MagicMock),
        patch("benchbox.core.run_service.execute_run", **patches.pop("execute_run")),
        patch("benchbox.mcp.tools.benchmark.ResultExporter", **patches.pop("exporter")),
    ):
        return anyio.run(invoke)


class TestExportFailureIsAnError:
    def test_failed_export_response_names_the_cause(self, tmp_path: Path) -> None:
        with patch("benchbox.mcp.tools.benchmark.ResultExporter", side_effect=OSError("disk full")):
            response = _build(_result(), tmp_path)

        assert response["mcp_metadata"]["status"] == "incomplete"
        assert response["mcp_metadata"]["result_file"] is None
        assert response["error"] is True
        assert response["error_code"] == "BENCHMARK_EXECUTION_FAILED"
        assert response["details"]["exception_type"] == "OSError"
        assert "disk full" in response["details"]["exception_message"]
        assert "integrity" not in response

    def test_unreadable_written_file_is_an_incomplete_run_without_a_verdict(self, tmp_path: Path) -> None:
        result_path = tmp_path / "result.json"
        result_path.write_text("{not json", encoding="utf-8")
        exporter = MagicMock()
        exporter.export_result.return_value = {"json": result_path}

        with patch("benchbox.mcp.tools.benchmark.ResultExporter", return_value=exporter):
            response = _build(_result(), tmp_path)

        assert response["mcp_metadata"]["status"] == "incomplete"
        assert response["error"] is True
        assert response["details"]["exception_type"] == "JSONDecodeError"
        assert "integrity" not in response

    def test_a_run_without_a_result_is_an_error(self, tmp_path: Path) -> None:
        response = _build(None, tmp_path)

        assert response["mcp_metadata"]["status"] == "no_results"
        assert response["error"] is True
        assert response["error_code"] == "BENCHMARK_EXECUTION_FAILED"

    def test_tool_call_sets_is_error_when_export_fails(self) -> None:
        exporter = MagicMock()
        exporter.export_result.side_effect = OSError("disk full")

        result = _call_run_benchmark(execute_run={"return_value": _result()}, exporter={"return_value": exporter})

        assert result.is_error is True
        assert result.structured_content["mcp_metadata"]["status"] == "incomplete"
        assert result.structured_content["details"]["exception_type"] == "OSError"
        assert json.loads(result.content[0].text) == result.structured_content

    def test_tool_call_sets_is_error_when_the_run_returns_nothing(self) -> None:
        result = _call_run_benchmark(execute_run={"return_value": None}, exporter={"return_value": MagicMock()})

        assert result.is_error is True
        assert result.structured_content["mcp_metadata"]["status"] == "no_results"

    def test_tool_call_sets_is_error_when_execution_raises(self) -> None:
        result = _call_run_benchmark(
            execute_run={"side_effect": RuntimeError("row_count_validation mismatch")},
            exporter={"return_value": MagicMock()},
        )

        assert result.is_error is True
        assert result.structured_content["status"] == "failed"
        assert result.structured_content["error_code"] == "BENCHMARK_EXECUTION_FAILED"
        assert "row_count_validation mismatch" in result.structured_content["message"]

    def test_tool_call_is_not_an_error_when_the_export_succeeds(self, tmp_path: Path) -> None:
        result_path = tmp_path / "result.json"
        write_v2_result_file(result_path, execution_id="mcp_test", timestamp="2026-01-01T00:00:00")
        exporter = MagicMock()
        exporter.export_result.return_value = {"json": result_path}

        result = _call_run_benchmark(execute_run={"return_value": _result()}, exporter={"return_value": exporter})

        assert not result.is_error
        assert result.structured_content["mcp_metadata"]["status"] == "completed"
        assert "error" not in result.structured_content


class TestIntegrityVerdict:
    def test_completed_run_carries_the_validator_verdict(self, tmp_path: Path) -> None:
        result_path = tmp_path / "result.json"
        payload = write_v2_result_file(result_path, execution_id="mcp_test", timestamp="2026-01-01T00:00:00")
        exporter = MagicMock()
        exporter.export_result.return_value = {"json": result_path}

        with patch("benchbox.mcp.tools.benchmark.ResultExporter", return_value=exporter):
            response = _build(_result(), tmp_path)

        integrity = response["integrity"]
        assert integrity["overall_status"] in {"PASS", "WARN", "FAIL"}
        assert "file" not in integrity
        assert {"PASS", "WARN", "FAIL"} <= set(integrity["summary"])
        assert all(check["status"] != "PASS" for check in integrity["checks"])
        assert response["benchmark"]["id"] == payload["benchmark"]["id"]

    def test_verdict_reports_a_failing_result(self, tmp_path: Path) -> None:
        result_path = tmp_path / "result.json"
        payload = write_v2_result_file(result_path, execution_id="mcp_test", timestamp="2026-01-01T00:00:00")
        del payload["queries"]
        result_path.write_text(json.dumps(payload), encoding="utf-8")
        exporter = MagicMock()
        exporter.export_result.return_value = {"json": result_path}

        with patch("benchbox.mcp.tools.benchmark.ResultExporter", return_value=exporter):
            response = _build(_result(), tmp_path)

        assert response["integrity"]["overall_status"] == "FAIL"
        assert response["mcp_metadata"]["status"] == "completed"

    def test_verdict_matches_validate_results_on_the_same_file(self, tmp_path: Path) -> None:
        from benchbox.core.results.integrity_validator import report_payload, validate_file

        result_path = tmp_path / "result.json"
        write_v2_result_file(result_path, execution_id="mcp_test", timestamp="2026-01-01T00:00:00")
        exporter = MagicMock()
        exporter.export_result.return_value = {"json": result_path}

        with patch("benchbox.mcp.tools.benchmark.ResultExporter", return_value=exporter):
            response = _build(_result(), tmp_path)

        assert {"file": str(result_path), **response["integrity"]} == report_payload(validate_file(result_path))

    def test_validator_crash_is_reported_not_raised(self, tmp_path: Path) -> None:
        result_path = tmp_path / "result.json"
        write_v2_result_file(result_path, execution_id="mcp_test", timestamp="2026-01-01T00:00:00")
        exporter = MagicMock()
        exporter.export_result.return_value = {"json": result_path}

        with (
            patch("benchbox.mcp.tools.benchmark.ResultExporter", return_value=exporter),
            patch("benchbox.mcp.tools.benchmark.ResultIntegrityValidator", side_effect=RuntimeError("boom")),
        ):
            response = _build(_result(), tmp_path)

        assert response["integrity"] == {"overall_status": "UNAVAILABLE", "error": "RuntimeError"}
        assert response["mcp_metadata"]["status"] == "completed"
