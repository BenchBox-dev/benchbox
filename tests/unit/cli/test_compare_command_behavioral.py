# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import sys
import sys as _sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from benchbox.cli.app import cli

__import__("benchbox.cli.commands.compare")
_compare_module = _sys.modules["benchbox.cli.commands.compare"]

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestListPlatformsFlag:
    @patch("benchbox.platforms.list_available_platforms", return_value=["duckdb", "sqlite"])
    @patch(
        "benchbox.platforms.list_available_dataframe_platforms", return_value={"polars-df": True, "pandas-df": False}
    )
    def test_list_platforms_shows_sql_and_dataframe_platforms(self, mock_df, mock_sql):
        runner = CliRunner()
        result = runner.invoke(cli, ["compare", "--list-platforms"])

        assert result.exit_code == 0
        assert "SQL Platforms" in result.output
        assert "duckdb" in result.output
        assert "sqlite" in result.output
        assert "DataFrame Platforms" in result.output
        assert "polars-df" in result.output

    @patch("benchbox.platforms.list_available_platforms", return_value=["duckdb"])
    @patch("benchbox.platforms.list_available_dataframe_platforms", return_value={"pandas-df": False})
    def test_list_platforms_shows_not_installed(self, mock_df, mock_sql):
        runner = CliRunner()
        result = runner.invoke(cli, ["compare", "--list-platforms"])

        assert result.exit_code == 0
        assert "Not installed" in result.output
        assert "pandas-df" in result.output


class TestConflictingArguments:
    def test_platforms_and_files_together_rejected(self):
        runner = CliRunner()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp_file:
            json.dump({"test": "data"}, tmp_file)
            file_path = tmp_file.name

        try:
            result = runner.invoke(cli, ["compare", "-p", "duckdb", file_path])

            assert result.exit_code != 0
            assert "Cannot specify both" in result.output
        finally:
            Path(file_path).unlink()


class TestNonInteractiveNoArgs:
    def test_non_interactive_no_platforms_no_files_errors(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["compare", "--non-interactive"])

        assert result.exit_code != 0
        assert "No platforms or files specified" in result.output
        assert "Usage" in result.output


class TestParseThreshold:
    def test_percentage_format(self):
        from benchbox.cli.commands.compare import _parse_threshold

        assert _parse_threshold("10%") == pytest.approx(0.1)

    def test_fractional_percentage(self):
        from benchbox.cli.commands.compare import _parse_threshold

        assert _parse_threshold("5.5%") == pytest.approx(0.055)

    def test_decimal_format(self):
        from benchbox.cli.commands.compare import _parse_threshold

        assert _parse_threshold("0.1") == pytest.approx(0.1)

    def test_small_decimal_format(self):
        from benchbox.cli.commands.compare import _parse_threshold

        assert _parse_threshold("0.05") == pytest.approx(0.05)

    def test_zero_threshold(self):
        from benchbox.cli.commands.compare import _parse_threshold

        assert _parse_threshold("0") == pytest.approx(0.0)

    def test_zero_percent_threshold(self):
        from benchbox.cli.commands.compare import _parse_threshold

        assert _parse_threshold("0%") == pytest.approx(0.0)

    def test_invalid_string_returns_none(self):
        from benchbox.cli.commands.compare import _parse_threshold

        assert _parse_threshold("invalid") is None

    def test_invalid_percent_returns_none(self):
        from benchbox.cli.commands.compare import _parse_threshold

        assert _parse_threshold("abc%") is None

    def test_whitespace_stripped(self):
        from benchbox.cli.commands.compare import _parse_threshold

        assert _parse_threshold("  10%  ") == pytest.approx(0.1)

    def test_large_percentage(self):
        from benchbox.cli.commands.compare import _parse_threshold

        assert _parse_threshold("100%") == pytest.approx(1.0)

    def test_whole_number_decimal(self):
        from benchbox.cli.commands.compare import _parse_threshold

        assert _parse_threshold("1") == pytest.approx(1.0)


class TestCheckRegression:
    def test_no_regression_within_threshold(self):
        from benchbox.cli.commands.compare import _check_regression

        comparison = {
            "performance_changes": {
                "total_time": {"change_percent": 5.0, "improved": False},
            },
            "query_comparisons": [
                {"query_id": "q1", "change_percent": 3.0},
            ],
        }

        assert _check_regression(comparison, 0.10) is False

    def test_regression_detected_in_overall_metrics(self):
        from benchbox.cli.commands.compare import _check_regression

        comparison = {
            "performance_changes": {
                "total_time": {"change_percent": 25.0, "improved": False},
            },
            "query_comparisons": [],
        }

        assert _check_regression(comparison, 0.10) is True

    def test_regression_detected_in_query_comparisons(self):
        from benchbox.cli.commands.compare import _check_regression

        comparison = {
            "performance_changes": {},
            "query_comparisons": [
                {"query_id": "q1", "change_percent": 2.0},
                {"query_id": "q2", "change_percent": 50.0},
            ],
        }

        assert _check_regression(comparison, 0.10) is True

    def test_no_regression_empty_comparison(self):
        from benchbox.cli.commands.compare import _check_regression

        comparison = {
            "performance_changes": {},
            "query_comparisons": [],
        }

        assert _check_regression(comparison, 0.10) is False

    def test_regression_with_zero_threshold(self):
        from benchbox.cli.commands.compare import _check_regression

        comparison = {
            "performance_changes": {
                "total_time": {"change_percent": 0.1},
            },
            "query_comparisons": [],
        }

        assert _check_regression(comparison, 0.0) is True

    def test_non_dict_metric_data_skipped(self):
        from benchbox.cli.commands.compare import _check_regression

        comparison = {
            "performance_changes": {
                "total_time": "not a dict",
            },
            "query_comparisons": [],
        }

        assert _check_regression(comparison, 0.10) is False


class TestMultiFileComparisonWarning:
    @patch.object(_compare_module, "load_result_file")
    @patch.object(_compare_module, "ResultExporter")
    def test_three_files_shows_warning(self, mock_exporter_class, mock_load):
        runner = CliRunner()

        mock_baseline = MagicMock(benchmark_name="TPC-H", platform="DuckDB", scale_factor=0.01)
        mock_current = MagicMock(benchmark_name="TPC-H", platform="DuckDB", scale_factor=0.01)
        mock_load.side_effect = [(mock_baseline, {}), (mock_current, {})]

        mock_exporter = MagicMock()
        mock_exporter.compare_results.return_value = {
            "baseline_file": "a.json",
            "current_file": "b.json",
            "performance_changes": {},
            "query_comparisons": [],
            "summary": {},
        }
        mock_exporter_class.return_value = mock_exporter

        with tempfile.TemporaryDirectory() as tmpdir:
            for name in ("a.json", "b.json", "c.json"):
                (Path(tmpdir) / name).write_text("{}")

            result = runner.invoke(
                cli,
                [
                    "compare",
                    str(Path(tmpdir) / "a.json"),
                    str(Path(tmpdir) / "b.json"),
                    str(Path(tmpdir) / "c.json"),
                ],
            )

            assert "Multi-file comparison not yet supported" in result.output or "first 2 files" in result.output


class TestRunFlagDeprecation:
    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    @patch.object(_compare_module, "_run_platform_comparison")
    def test_run_flag_shows_deprecation_warning(self, mock_platform_comparison):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["compare", "--run", "-p", "duckdb", "-p", "sqlite"],
        )

        assert "deprecated" in result.output.lower()


class TestCompareOutputFormats:
    def test_compare_help_shows_all_format_choices(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["compare", "--help"])

        assert result.exit_code == 0
        assert "text" in result.output
        assert "json" in result.output
        assert "markdown" in result.output
        assert "html" in result.output


class TestCompareHelpOptions:
    def test_compare_help_shows_run_mode_options(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["compare", "--help"])

        assert result.exit_code == 0
        assert "--platform" in result.output or "-p" in result.output
        assert "--benchmark" in result.output or "-b" in result.output
        assert "--scale" in result.output or "-s" in result.output
        assert "--warmup" in result.output
        assert "--iterations" in result.output
        assert "--list-platforms" in result.output

    def test_compare_help_shows_file_mode_options(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["compare", "--help"])

        assert result.exit_code == 0
        assert "--fail-on-regression" in result.output
        assert "--show-all-queries" in result.output
        assert "--include-plans" in result.output
        assert "--plan-threshold" in result.output

    def test_compare_help_shows_mode_descriptions(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["compare", "--help"])

        assert result.exit_code == 0
        assert "RUN MODE" in result.output
        assert "FILE MODE" in result.output
        assert "INTERACTIVE MODE" in result.output


class TestCheckRegressionThresholdFunction:
    @patch.object(_compare_module, "load_result_file")
    @patch.object(_compare_module, "ResultExporter")
    def test_no_regression_shows_pass_message(self, mock_exporter_class, mock_load):
        runner = CliRunner()

        mock_baseline = MagicMock(benchmark_name="TPC-H", platform="DuckDB", scale_factor=0.01)
        mock_current = MagicMock(benchmark_name="TPC-H", platform="DuckDB", scale_factor=0.01)
        mock_load.side_effect = [(mock_baseline, {}), (mock_current, {})]

        mock_exporter = MagicMock()
        mock_exporter.compare_results.return_value = {
            "baseline_file": "a.json",
            "current_file": "b.json",
            "performance_changes": {},
            "query_comparisons": [],
            "summary": {},
        }
        mock_exporter_class.return_value = mock_exporter

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.json").write_text("{}")
            (Path(tmpdir) / "b.json").write_text("{}")

            result = runner.invoke(
                cli,
                [
                    "compare",
                    str(Path(tmpdir) / "a.json"),
                    str(Path(tmpdir) / "b.json"),
                    "--fail-on-regression",
                    "10%",
                ],
            )

            assert result.exit_code == 0
            assert "No performance regression" in result.output


class TestResultFileMetadataShortPath:
    def test_short_path_with_deep_nested_file(self):
        from benchbox.cli.commands.compare import ResultFileMetadata

        meta = ResultFileMetadata(
            path=Path("/very/deeply/nested/benchmark_runs/results/result.json"),
            benchmark="TPC-H",
            benchmark_id="tpch",
            platform="DuckDB",
            scale=0.01,
            timestamp="2026-01-01T00:00:00",
            execution_id="abc",
        )

        short = meta.short_path
        assert "result.json" in short
        parts = Path(short).parts
        assert len(parts) == 3

    def test_short_path_with_shallow_file(self):
        from benchbox.cli.commands.compare import ResultFileMetadata

        meta = ResultFileMetadata(
            path=Path("ab/result.json"),
            benchmark="TPC-H",
            benchmark_id="tpch",
            platform="DuckDB",
            scale=0.01,
            timestamp="2026-01-01T00:00:00",
            execution_id="abc",
        )

        assert meta.short_path == str(Path("ab/result.json"))


class TestDisplayResultsTable:
    def test_display_results_table_renders_rows(self):
        from benchbox.cli.commands.compare import ResultFileMetadata, _display_results_table

        results = [
            ResultFileMetadata(
                path=Path("/tmp/r1.json"),
                benchmark="TPC-H",
                benchmark_id="tpch",
                platform="DuckDB",
                scale=0.01,
                timestamp="2026-01-15T10:00:00",
                execution_id="run1",
            ),
            ResultFileMetadata(
                path=Path("/tmp/r2.json"),
                benchmark="TPC-DS",
                benchmark_id="tpcds",
                platform="SQLite",
                scale=1.0,
                timestamp="2026-01-14T10:00:00",
                execution_id="run2",
            ),
        ]

        with patch.object(_compare_module, "console") as mock_console:
            _display_results_table(results)

        mock_console.print.assert_called_once()
        from rich.table import Table

        table_arg = mock_console.print.call_args[0][0]
        assert isinstance(table_arg, Table)

    def test_display_results_table_uses_benchmark_colors(self):
        from benchbox.cli.commands.compare import ResultFileMetadata, _display_results_table

        results = [
            ResultFileMetadata(
                path=Path("/tmp/r1.json"),
                benchmark="ClickBench",
                benchmark_id="clickbench",
                platform="DuckDB",
                scale=1.0,
                timestamp="2026-01-15T10:00:00",
                execution_id="run1",
            ),
        ]

        with patch.object(_compare_module, "console") as mock_console:
            _display_results_table(results)

        mock_console.print.assert_called_once()


class TestValidateRegressionThreshold:
    def test_returns_none_when_no_threshold(self):
        from benchbox.cli.commands.compare import _validate_regression_threshold

        assert _validate_regression_threshold(None) is None

    def test_returns_parsed_value_for_valid_threshold(self):
        from benchbox.cli.commands.compare import _validate_regression_threshold

        result = _validate_regression_threshold("15%")
        assert result == pytest.approx(0.15)

    def test_invalid_threshold_exits(self):
        from benchbox.cli.commands.compare import _validate_regression_threshold

        with pytest.raises(SystemExit) as exc_info:
            _validate_regression_threshold("not_a_number")

        assert exc_info.value.code == 1


class TestComparePlatformRunMode:
    @patch.object(_compare_module, "_run_platform_comparison")
    def test_single_platform_in_run_mode_errors(self, mock_comparison):
        mock_comparison.side_effect = SystemExit(1)

        runner = CliRunner()
        result = runner.invoke(cli, ["compare", "-p", "duckdb"])

        assert result.exit_code != 0


class TestCompareHelperFunctions:
    def test_build_execution_map_keeps_first_execution_per_query(self):
        from benchbox.core.query_plans.comparison import build_execution_map

        results = SimpleNamespace(
            query_results=[
                {"query_id": "Q1", "execution_time_ms": 10.0},
                {"query_id": "Q2", "execution_time_ms": 20.0},
                {"query_id": "Q1", "execution_time_ms": 99.0},
                {"query_id": "Q3", "execution_time_ms": 30.0},
            ]
        )

        execution_map = build_execution_map(results)

        assert set(execution_map) == {"Q1", "Q2", "Q3"}
        assert execution_map["Q1"]["execution_time_ms"] == 10.0

    @patch("benchbox.core.query_plans.comparison.QueryPlanComparator")
    @patch("benchbox.core.query_plans.comparison.generate_plan_comparison_summary")
    def test_compare_plans_returns_query_level_deltas(self, mock_summary_fn, mock_comparator_class):
        from benchbox.cli.commands.compare import _compare_plans

        baseline = SimpleNamespace(
            query_results=[
                {"query_id": "Q1", "query_plan": {"a": 1}, "execution_time_ms": 100.0},
                {"query_id": "Q2", "query_plan": {"b": 2}, "execution_time_ms": 50.0},
            ]
        )
        current = SimpleNamespace(
            query_results=[
                {"query_id": "Q1", "query_plan": {"a": 2}, "execution_time_ms": 150.0},
                {"query_id": "Q2", "query_plan": {"b": 3}, "execution_time_ms": 55.0},
            ]
        )

        mock_summary_fn.return_value = SimpleNamespace(plans_compared=2, plans_unchanged=0, plans_changed=2)
        comparator = mock_comparator_class.return_value
        comparator.compare_plans.side_effect = [
            SimpleNamespace(
                similarity=SimpleNamespace(
                    overall_similarity=0.4,
                    type_mismatches=1,
                    property_mismatches=2,
                    structure_mismatches=3,
                ),
                plans_identical=False,
                summary="changed",
            ),
            SimpleNamespace(
                similarity=SimpleNamespace(
                    overall_similarity=0.9,
                    type_mismatches=0,
                    property_mismatches=0,
                    structure_mismatches=0,
                ),
                plans_identical=True,
                summary="stable",
            ),
        ]

        comparison = _compare_plans(baseline, current, plan_threshold=0.5)

        assert comparison is not None
        assert comparison["plans_compared"] == 2
        assert comparison["regressions_detected"] == 1
        assert len(comparison["query_plans"]) == 1
        query_plan = comparison["query_plans"][0]
        assert query_plan["query_id"] == "Q1"
        assert query_plan["perf_change_pct"] == pytest.approx(50.0)
        assert query_plan["is_regression"] is True

    @patch("benchbox.core.query_plans.comparison.generate_plan_comparison_summary")
    def test_compare_plans_returns_none_when_no_plans_exist(self, mock_summary_fn):
        from benchbox.cli.commands.compare import _compare_plans

        mock_summary_fn.return_value = SimpleNamespace(plans_compared=0)

        result = _compare_plans(SimpleNamespace(phases={}), SimpleNamespace(phases={}))

        assert result is None

    @patch("benchbox.core.query_plans.comparison.generate_plan_comparison_summary", side_effect=RuntimeError("boom"))
    def test_compare_plans_returns_none_when_summary_generation_fails(self, _mock_summary_fn):
        from benchbox.cli.commands.compare import _compare_plans

        result = _compare_plans(SimpleNamespace(phases={}), SimpleNamespace(phases={}))

        assert result is None

    def test_output_file_comparison_writes_requested_format(self, tmp_path: Path):
        from benchbox.cli.commands.compare import _output_file_comparison

        output_path = tmp_path / "comparison.json"
        comparison = {"summary": {"status": "ok"}}

        _output_file_comparison(
            comparison=comparison,
            baseline=SimpleNamespace(),
            current=SimpleNamespace(),
            output_format="json",
            output_file=str(output_path),
            show_all_queries=False,
        )

        assert json.loads(output_path.read_text(encoding="utf-8")) == comparison


class TestQuerySeverityAndChange:
    def _sev(self, improved, change_pct):
        from benchbox.cli.commands.compare import _query_severity_and_change

        return _query_severity_and_change(improved, change_pct)

    def test_improved(self):
        label, change = self._sev(True, 15.0)
        assert "FASTER" in label
        assert "-" in change

    def test_critical_regression(self):
        label, change = self._sev(False, 60.0)
        assert "CRITICAL" in label
        assert "+" in change

    def test_major_regression(self):
        label, change = self._sev(False, 30.0)
        assert "MAJOR" in label

    def test_minor_regression(self):
        label, change = self._sev(False, 15.0)
        assert "MINOR" in label

    def test_slight_regression(self):
        label, change = self._sev(False, 3.0)
        assert "SLIGHT" in label

    def test_same(self):
        label, change = self._sev(False, 0.5)
        assert "SAME" in label
        assert "0.00%" in change


class TestPlanStatusLabel:
    def _label(self, identical, similarity, is_regression):
        from benchbox.cli.commands.compare import _plan_status_label

        return _plan_status_label(identical, similarity, is_regression)

    def test_identical(self):
        assert "Identical" in self._label(True, 1.0, False)

    def test_nearly_identical(self):
        assert "Nearly" in self._label(False, 0.97, False)

    def test_similar(self):
        assert "Similar" in self._label(False, 0.80, False)

    def test_regression(self):
        assert "REGRESSION" in self._label(False, 0.50, True)

    def test_different(self):
        assert "Different" in self._label(False, 0.50, False)


class TestFormatTextComparison:
    def _make_comparison(self):
        return {
            "baseline_file": "/tmp/baseline.json",
            "current_file": "/tmp/current.json",
            "summary": {
                "total_queries_compared": 5,
                "improved_queries": 2,
                "regressed_queries": 1,
                "unchanged_queries": 2,
                "overall_assessment": "improved",
            },
            "performance_changes": {
                "geometric_mean_time": {
                    "baseline": 1.5,
                    "current": 1.2,
                    "change_percent": -20.0,
                    "improved": True,
                },
                "execution_time": {
                    "baseline": 3.0,
                    "current": 3.3,
                    "change_percent": 10.0,
                    "improved": False,
                },
            },
            "query_comparisons": [
                {
                    "query_id": "Q1",
                    "baseline_time_ms": 100.0,
                    "current_time_ms": 80.0,
                    "change_percent": -20.0,
                    "improved": True,
                },
                {
                    "query_id": "Q2",
                    "baseline_time_ms": 200.0,
                    "current_time_ms": 260.0,
                    "change_percent": 30.0,
                    "improved": False,
                },
            ],
        }

    def _make_baseline(self):
        return SimpleNamespace(
            benchmark_name="tpch",
            platform="duckdb",
            scale_factor=0.01,
        )

    def test_format_text_comparison_contains_key_sections(self):
        from benchbox.cli.commands.compare import _format_text_comparison

        comparison = self._make_comparison()
        text = _format_text_comparison(comparison, self._make_baseline(), SimpleNamespace(), show_all=True)

        assert "BENCHMARK COMPARISON REPORT" in text
        assert "OVERALL SUMMARY" in text
        assert "PERFORMANCE METRICS" in text
        assert "tpch" in text
        assert "duckdb" in text

    def test_format_summary_section_empty(self):
        from benchbox.cli.commands.compare import _format_summary_section

        lines: list[str] = []
        _format_summary_section(lines, {})
        assert lines == []

    def test_format_performance_metrics_all_branches(self):
        from benchbox.cli.commands.compare import _format_performance_metrics_section

        perf_data = {
            "improved_metric": {"baseline": 2.0, "current": 1.5, "change_percent": -25.0, "improved": True},
            "major_regression": {"baseline": 1.0, "current": 1.5, "change_percent": 50.0, "improved": False},
            "minor_regression": {"baseline": 1.0, "current": 1.08, "change_percent": 8.0, "improved": False},
            "unchanged": {"baseline": 1.0, "current": 1.0, "change_percent": 0.0, "improved": False},
            "non_dict_value": "should be skipped",
        }
        lines: list[str] = []
        _format_performance_metrics_section(lines, {"performance_changes": perf_data})
        text = "\n".join(lines)
        assert "🟢" in text
        assert "🔴" in text
        assert "🟡" in text

    def test_format_plan_analysis_section_with_plans(self):
        from benchbox.cli.commands.compare import _format_plan_analysis_section

        plan_data = {
            "plans_compared": 3,
            "plans_unchanged": 2,
            "plans_changed": 1,
            "threshold_applied": 0.95,
            "query_plans": [
                {
                    "query_id": "Q1",
                    "similarity": 0.85,
                    "type_mismatches": 1,
                    "property_mismatches": 2,
                    "perf_change_pct": 15.0,
                    "is_regression": False,
                    "plans_identical": False,
                },
            ],
            "regressions": [
                {"query_id": "Q2", "perf_change_pct": 25.0, "similarity": 0.6},
            ],
        }
        lines: list[str] = []
        _format_plan_analysis_section(lines, {"plan_comparison": plan_data})
        text = "\n".join(lines)

        assert "QUERY PLAN ANALYSIS" in text
        assert "Plans Compared: 3" in text
        assert "Q1" in text
        assert "REGRESSION" in text

    def test_format_plan_analysis_section_no_plans(self):
        from benchbox.cli.commands.compare import _format_plan_analysis_section

        lines: list[str] = []
        _format_plan_analysis_section(lines, {})
        assert lines == []

    def test_format_html_comparison_returns_html(self):
        from benchbox.cli.commands.compare import _format_html_comparison

        comparison = self._make_comparison()
        html = _format_html_comparison(comparison, self._make_baseline(), SimpleNamespace())
        assert "<!DOCTYPE html>" in html
        assert "</html>" in html

    def test_format_geometric_mean_section_empty(self):
        from benchbox.cli.commands.compare import _format_geometric_mean_section

        lines: list[str] = []
        _format_geometric_mean_section(lines, [])
        assert lines == []

    def test_format_geometric_mean_with_data(self):
        from benchbox.cli.commands.compare import _format_geometric_mean_section

        lines: list[str] = []
        queries = [
            {"baseline_time_ms": 100.0, "current_time_ms": 80.0},
            {"baseline_time_ms": 200.0, "current_time_ms": 220.0},
        ]
        _format_geometric_mean_section(lines, queries)
        assert len(lines) > 0
