from __future__ import annotations

import io
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_run_benchmark_impl_suppresses_transitive_stdout() -> None:
    from benchbox.mcp.tools.benchmark import _run_benchmark_impl

    captured = io.StringIO()
    original_stdout = sys.stdout
    try:
        sys.stdout = captured
        with (
            patch("benchbox.mcp.tools.benchmark.get_all_benchmarks", return_value={"tpch": {"name": "tpch"}}),
            patch("benchbox.mcp.tools.benchmark.get_public_benchmark_class", return_value=MagicMock),
            patch("benchbox.core.run_service.execute_run", side_effect=lambda **_: print("LEAK: benchmark run output")),
        ):
            response = _run_benchmark_impl(
                "duckdb",
                "tpch",
                0.01,
                None,
                "load,power",
                "sql",
                results_dir=Path("benchmark_runs/results"),
                anonymize=False,
            )
        assert response["mcp_metadata"]["status"] in {"completed", "no_results"}
        assert captured.getvalue() == ""
    finally:
        sys.stdout = original_stdout


def test_data_only_core_run_suppresses_transitive_stdout(tmp_path: Path) -> None:
    from benchbox.mcp.tools.benchmark import _run_benchmark_impl

    def build_dummy_benchmark(scale_factor: float, output_dir: Path, **_: object) -> SimpleNamespace:
        return SimpleNamespace(scale_factor=scale_factor, output_dir=output_dir)

    captured = io.StringIO()
    original_stdout = sys.stdout
    try:
        sys.stdout = captured
        with (
            patch("benchbox.mcp.tools.benchmark.get_all_benchmarks", return_value={"tpch": {"display_name": "TPC-H"}}),
            patch("benchbox.mcp.tools.benchmark.get_public_benchmark_class", return_value=build_dummy_benchmark),
            patch(
                "benchbox.core.run_service.execute_run",
                side_effect=lambda **_: (print("LEAK: data generation output"), MagicMock())[1],
            ),
        ):
            response = _run_benchmark_impl(
                "duckdb",
                "tpch",
                0.01,
                None,
                None,
                "data_only",
                results_dir=tmp_path,
                execution_id="mcp_test_id",
                anonymize=False,
            )
        assert response["mcp_metadata"]["status"] == "completed"
        assert captured.getvalue() == ""
    finally:
        sys.stdout = original_stdout


def test_silence_output_suppresses_emit_calls() -> None:
    from benchbox.utils.printing import emit, silence_output

    captured = io.StringIO()
    original_stdout = sys.stdout
    try:
        sys.stdout = captured
        with silence_output(enabled=True):
            emit("LEAK: emit output should be suppressed by silence_output")
        assert captured.getvalue() == "", "emit() leaked past silence_output()"
    finally:
        sys.stdout = original_stdout
