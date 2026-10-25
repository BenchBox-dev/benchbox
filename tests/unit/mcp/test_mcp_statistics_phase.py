# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tests.fixtures.result_dict_fixtures import write_v2_result_file

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _get_tool_functions() -> dict[str, Any]:
    import benchbox.utils.printing as printing
    from benchbox.mcp import create_server
    from tests.unit.mcp.public_api import get_tool_functions

    quiet = printing._QUIET
    try:
        return get_tool_functions(create_server())
    finally:
        printing._QUIET = quiet


@pytest.fixture(scope="module")
def tool_functions() -> dict[str, Any]:
    return _get_tool_functions()


def _mock_run(
    tool_functions: dict[str, Any],
    tmp_path: Path,
    *,
    phases: str,
    extra_result_kwargs: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    fn = tool_functions["run_benchmark"]

    mock_result = MagicMock()
    mock_result.query_results = []

    captured: dict[str, Any] = {}

    def execute_run(**kwargs):
        config = kwargs["config"]
        captured.update(config.options or {})
        captured["test_execution_type"] = config.test_execution_type
        return mock_result

    result_path = tmp_path / "result.json"
    write_v2_result_file(
        result_path,
        execution_id="mcp_test",
        timestamp="2026-01-01T00:00:00",
        **(extra_result_kwargs or {}),
    )

    mock_exporter = MagicMock()
    mock_exporter.export_result.return_value = {"json": result_path}

    with (
        patch("benchbox.mcp.tools.benchmark._get_platform_adapter"),
        patch("benchbox.mcp.tools.benchmark.get_public_benchmark_class", return_value=MagicMock),
        patch("benchbox.mcp.tools.benchmark.ResultExporter", return_value=mock_exporter),
        patch("benchbox.core.run_service.execute_run", side_effect=execute_run),
    ):
        result = fn(platform="duckdb", benchmark="tpch", scale_factor=0.01, phases=phases)

    return result, captured


class TestStatisticsPhaseFlagThreading:
    def test_statistics_in_phases_forwards_gather_statistics_flag(self, tool_functions, tmp_path):
        _, call_kwargs = _mock_run(tool_functions, tmp_path, phases="load,statistics,power")

        assert call_kwargs.get("gather_statistics") is True
        assert call_kwargs.get("statistics_benchmark_name") == "tpch"

    def test_statistics_never_sets_benchmark_name(self, tool_functions, tmp_path):
        _, call_kwargs = _mock_run(tool_functions, tmp_path, phases="load,statistics,power")

        assert "benchmark_name" not in call_kwargs

    def test_statistics_absent_from_phases_omits_the_flag(self, tool_functions, tmp_path):
        _, call_kwargs = _mock_run(tool_functions, tmp_path, phases="load,power")

        assert "gather_statistics" not in call_kwargs
        assert "statistics_benchmark_name" not in call_kwargs
        assert "benchmark_name" not in call_kwargs

    def test_statistics_does_not_change_test_execution_type(self, tool_functions, tmp_path):
        _, call_kwargs = _mock_run(tool_functions, tmp_path, phases="load,statistics,power")

        assert call_kwargs.get("test_execution_type") == "power"


class TestStatisticsPhaseResponsePassThrough:
    def test_response_carries_through_a_statistics_block_when_present(self, tool_functions, tmp_path):
        result, _ = _mock_run(
            tool_functions,
            tmp_path,
            phases="load,statistics,power",
            extra_result_kwargs={
                "phases": {
                    "statistics": {"status": "COMPLETED", "stats_mode": "explicit", "tables_analyzed": 8},
                }
            },
        )

        assert result["phases"]["statistics"]["stats_mode"] == "explicit"

    def test_response_has_no_statistics_block_when_result_omits_it(self, tool_functions, tmp_path):
        result, _ = _mock_run(
            tool_functions,
            tmp_path,
            phases="load,statistics,power",
            extra_result_kwargs={
                "phases": {
                    "power_test": {"status": "COMPLETED"},
                }
            },
        )

        assert "statistics" not in result["phases"]
