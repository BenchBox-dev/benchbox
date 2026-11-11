from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from benchbox.core.visualization.ascii.base import ChartOptions
from benchbox.core.visualization.ascii_runtime import render_ascii_chart_from_results
from benchbox.core.visualization.result_plotter import NormalizedResult, ResultPlotter
from benchbox.utils.printing import set_quiet
from tests.fixtures.result_dict_fixtures import make_normalized_result

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

visualize_module = importlib.import_module("benchbox.cli.commands.visualize")


@pytest.fixture(autouse=True)
def _ensure_quiet_off():
    set_quiet(False)
    yield
    set_quiet(False)


def test_power_bar_falls_back_to_query_latency_bars_without_power_metric():
    results = [
        make_normalized_result(
            platform="DuckDB",
            benchmark="joinorder",
            scale_factor=1,
            raw={"benchmark": {"test_type": "power"}},
            queries=[
                SimpleNamespace(query_id="1a", execution_time_ms=12.0),
                SimpleNamespace(query_id="1b", execution_time_ms=8.0),
                SimpleNamespace(query_id="2a", execution_time_ms=23.0),
            ],
        )
    ]
    opts = ChartOptions(use_color=False)
    output = render_ascii_chart_from_results(results, "power_bar", opts, {})
    assert output is not None
    assert "Power Run Query Latency" in output
    assert "1a" in output
    assert "1b" in output
    assert "2a" in output


def test_power_bar_ignores_non_power_runs_without_power_metric():
    results = [
        make_normalized_result(
            platform="DuckDB",
            benchmark="custom",
            scale_factor=1,
            queries=[SimpleNamespace(query_id="Q1", execution_time_ms=12.0)],
        )
    ]
    opts = ChartOptions(use_color=False)
    output = render_ascii_chart_from_results(results, "power_bar", opts, {})
    assert output is None


def test_power_bar_latency_fallback_filters_mixed_non_power_runs():
    results = [
        make_normalized_result(
            platform="PowerDB",
            benchmark="joinorder",
            scale_factor=1,
            raw={"benchmark": {"test_type": "power"}},
            queries=[SimpleNamespace(query_id="POWER_ONLY", execution_time_ms=12.0)],
        ),
        make_normalized_result(
            platform="StandardDB",
            benchmark="custom",
            scale_factor=1,
            queries=[SimpleNamespace(query_id="STANDARD_ONLY", execution_time_ms=99.0)],
        ),
    ]
    opts = ChartOptions(use_color=False)
    output = render_ascii_chart_from_results(results, "power_bar", opts, {})
    assert output is not None
    assert "Power Run Query Latency" in output
    assert "POWER_ONLY" in output
    assert "STANDARD_ONLY" not in output
    assert "StandardDB" not in output


def test_query_histogram_uses_horizontal_bars_for_long_query_names():
    results = [
        make_normalized_result(
            platform="DuckDB",
            benchmark="read_primitives",
            scale_factor=1,
            queries=[
                SimpleNamespace(query_id="aggregation_groupby_large", execution_time_ms=100.0),
                SimpleNamespace(query_id="shuffle_inner_join", execution_time_ms=200.0),
                SimpleNamespace(query_id="read_parquet_single", execution_time_ms=150.0),
            ],
        )
    ]
    opts = ChartOptions(use_color=False)
    output = render_ascii_chart_from_results(results, "query_histogram", opts, {})
    assert output is not None
    assert "Query Latency" in output
    assert "aggregation_groupby_large" in output
    assert "shuffle_inner_join" in output
    assert "read_parquet_single" in output


def test_power_bar_suggested_for_power_run_without_power_metric():
    result = NormalizedResult(
        benchmark="joinorder",
        platform="duckdb",
        scale_factor=1,
        execution_id=None,
        timestamp=None,
        total_time_ms=1000.0,
        avg_time_ms=None,
        success_rate=None,
        cost_total=None,
        raw={"benchmark": {"test_type": "power"}},
    )
    plotter = ResultPlotter.__new__(ResultPlotter)
    plotter.results = [result]

    assert "power_bar" in plotter._suggest_chart_types()


def test_visualize_cli_power_bar_renders_query_latency_without_tpc_metric(tmp_path):
    result_file = tmp_path / "joinorder_result.json"
    result_file.write_text(
        """{
  "version": "2.1",
  "run": {
    "id": "joinorder-run",
    "timestamp": "2026-05-18T16:11:32.838385",
    "total_duration_ms": 1000,
    "query_time_ms": 500
  },
  "benchmark": {
    "id": "joinorder",
    "name": "JoinOrderBenchmark",
    "scale_factor": 1,
    "test_type": "power"
  },
  "platform": {
    "name": "DuckDB",
    "version": "1.3.2"
  },
  "summary": {
    "queries": {"total": 3, "passed": 3, "failed": 0},
    "timing": {"total_ms": 43, "avg_ms": 14.3, "geometric_mean_ms": 12.9}
  },
  "queries": [
    {"id": "1a", "ms": 12.0, "status": "SUCCESS"},
    {"id": "1b", "ms": 8.0, "status": "SUCCESS"},
    {"id": "2a", "ms": 23.0, "status": "SUCCESS"}
  ]
}"""
    )

    runner = CliRunner()
    result = runner.invoke(
        visualize_module.visualize,
        [str(result_file), "--chart-type", "power_bar", "--no-color"],
    )

    assert result.exit_code == 0
    assert "Power Run Query Latency" in result.output
    assert "1a" in result.output
    assert "1b" in result.output
    assert "2a" in result.output


