from __future__ import annotations

import importlib

import pytest
from click.testing import CliRunner

import benchbox.utils.printing as printing

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


visualize_module = importlib.import_module("benchbox.cli.commands.visualize")


@pytest.fixture(autouse=True)
def _ensure_quiet_off(_hermetic_state, monkeypatch):

    monkeypatch.setattr(printing, "_QUIET", False)


SAMPLE_RESULT_JSON = """{
  "version": "2.1",
  "run": {
    "id": "test-run",
    "timestamp": "2025-01-28T14:18:32.624836",
    "total_duration_ms": 1000,
    "query_time_ms": 500
  },
  "benchmark": {
    "id": "tpch",
    "name": "TPC-H",
    "scale_factor": 0.01
  },
  "platform": {
    "name": "DuckDB",
    "version": "1.0.0"
  },
  "config": {
    "mode": "sql"
  },
  "summary": {
    "queries": {"total": 2, "passed": 2, "failed": 0},
    "timing": {"total_ms": 300, "avg_ms": 150}
  },
  "queries": [
    {"query_id": "Q1", "execution_time_ms": 100, "status": "passed"},
    {"query_id": "Q2", "execution_time_ms": 200, "status": "passed"}
  ]
}"""


def test_visualize_cli_renders_ascii(tmp_path):

    result_file = tmp_path / "test_result.json"
    result_file.write_text(SAMPLE_RESULT_JSON)

    runner = CliRunner()
    result = runner.invoke(
        visualize_module.visualize,
        [str(result_file), "--chart-type", "performance_bar"],
    )

    assert result.exit_code == 0
    assert result.exception is None


def test_visualize_cli_no_color(tmp_path):

    result_file = tmp_path / "test_result.json"
    result_file.write_text(SAMPLE_RESULT_JSON)

    runner = CliRunner()
    result = runner.invoke(
        visualize_module.visualize,
        [str(result_file), "--chart-type", "performance_bar", "--no-color"],
    )

    assert result.exit_code == 0
    assert "\033[" not in result.output


def test_visualize_cli_no_unicode(tmp_path):

    result_file = tmp_path / "test_result.json"
    result_file.write_text(SAMPLE_RESULT_JSON)

    runner = CliRunner()
    result = runner.invoke(
        visualize_module.visualize,
        [str(result_file), "--chart-type", "performance_bar", "--no-unicode"],
    )

    assert result.exit_code == 0
    assert "█" not in result.output
    assert "▏" not in result.output


def test_visualize_cli_both_no_color_and_no_unicode(tmp_path):

    result_file = tmp_path / "test_result.json"
    result_file.write_text(SAMPLE_RESULT_JSON)

    runner = CliRunner()
    result = runner.invoke(
        visualize_module.visualize,
        [str(result_file), "--chart-type", "performance_bar", "--no-color", "--no-unicode"],
    )

    assert result.exit_code == 0
    assert "\033[" not in result.output
    assert "█" not in result.output


def test_visualize_cli_multiple_chart_types(tmp_path):

    result_file = tmp_path / "test_result.json"
    result_file.write_text(SAMPLE_RESULT_JSON)

    runner = CliRunner()
    result = runner.invoke(
        visualize_module.visualize,
        [str(result_file), "--chart-type", "performance_bar", "--chart-type", "distribution_box"],
    )

    assert result.exit_code == 0
    assert result.exception is None


def test_visualize_cli_continues_after_one_renderer_fails(tmp_path, monkeypatch):

    from benchbox.core.visualization import ascii_api

    result_file = tmp_path / "test_result.json"
    result_file.write_text(SAMPLE_RESULT_JSON)

    def fake_renderer(results, chart_type, *, options=None, subtitle=None):
        if chart_type == "performance_bar":
            raise RuntimeError("boom")
        return f"rendered {chart_type}"

    monkeypatch.setattr(ascii_api, "render_ascii_chart_from_results", fake_renderer)

    runner = CliRunner()
    result = runner.invoke(
        visualize_module.visualize,
        [str(result_file), "--chart-type", "performance_bar", "--chart-type", "power_bar"],
    )

    assert result.exit_code == 0
    assert result.exception is None
    assert "rendered power_bar" in result.output
    assert "Warning: Could not render performance_bar: boom" in result.output


def test_visualize_cli_theme_option(tmp_path):

    result_file = tmp_path / "test_result.json"
    result_file.write_text(SAMPLE_RESULT_JSON)

    runner = CliRunner()
    result = runner.invoke(
        visualize_module.visualize,
        [str(result_file), "--chart-type", "performance_bar", "--theme", "dark"],
    )

    assert result.exit_code == 0


def test_visualize_cli_chart_type_all(tmp_path):

    result_file = tmp_path / "test_result.json"
    result_file.write_text(SAMPLE_RESULT_JSON)

    runner = CliRunner()
    result = runner.invoke(
        visualize_module.visualize,
        [str(result_file), "--chart-type", "all"],
    )

    assert result.exit_code == 0


def test_visualize_cli_invalid_chart_type_fails(tmp_path):

    result_file = tmp_path / "test_result.json"
    result_file.write_text(SAMPLE_RESULT_JSON)

    runner = CliRunner()
    result = runner.invoke(
        visualize_module.visualize,
        [str(result_file), "--chart-type", "not_a_chart"],
    )

    assert result.exit_code != 0
    assert "Unknown chart type" in result.output


def test_visualize_cli_comparison_chart_requires_two_results(tmp_path):

    result_file = tmp_path / "test_result.json"
    result_file.write_text(SAMPLE_RESULT_JSON)

    runner = CliRunner()
    result = runner.invoke(
        visualize_module.visualize,
        [str(result_file), "--chart-type", "comparison_bar"],
    )

    assert result.exit_code != 0
    assert "require exactly 2 results" in result.output


