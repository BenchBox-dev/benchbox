# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import math

import pytest

from benchbox.core.pg_extension_comparison import (
    _format_metric_section,
    _format_query_comparison_section,
    _format_summary_section,
    _geometric_mean,
    generate_comparison_report,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestGeometricMean:
    def test_empty_list_returns_zero(self):
        assert _geometric_mean([]) == 0.0

    def test_all_zeros_returns_zero(self):
        assert _geometric_mean([0.0, 0.0]) == 0.0

    def test_two_values(self):
        result = _geometric_mean([2.0, 8.0])
        assert math.isclose(result, 4.0, rel_tol=1e-9)

    def test_single_value(self):
        result = _geometric_mean([1.0])
        assert math.isclose(result, 1.0, rel_tol=1e-9)

    def test_mixed_with_zero(self):
        result = _geometric_mean([0.0, 4.0, 9.0])
        assert math.isclose(result, 6.0, rel_tol=1e-9)


class TestFormatMetricSection:
    def test_normal_case_returns_ratio_line(self):
        lines = _format_metric_section("Title", "Speedup", 100.0, 50.0, "base", "ext")
        assert any("2.00x" in line for line in lines)

    def test_baseline_none_returns_empty(self):
        lines = _format_metric_section("Title", "Speedup", None, 50.0, "base", "ext")
        assert lines == []

    def test_extension_zero_returns_inf_ratio(self):
        lines = _format_metric_section("Title", "Speedup", 100.0, 0, "base", "ext")
        assert any("inf" in line.lower() for line in lines)

    def test_baseline_zero_returns_empty(self):
        lines = _format_metric_section("Title", "Speedup", 0, 50.0, "base", "ext")
        assert lines == []

    def test_both_none_returns_empty(self):
        lines = _format_metric_section("Title", "Speedup", None, None, "base", "ext")
        assert lines == []


class TestFormatQueryComparisonSection:
    def test_two_queries_shows_speedup_and_summary(self):
        baseline = {"Q1": {"execution_time": 2.0}, "Q6": {"execution_time": 4.0}}
        extension = {"Q1": {"execution_time": 1.0}, "Q6": {"execution_time": 1.0}}
        lines = _format_query_comparison_section(baseline, extension)
        text = "\n".join(lines)
        assert "Geometric mean speedup" in text
        assert "Max speedup" in text
        assert "Min speedup" in text

    def test_one_query_fails_shows_failed(self):
        baseline = {"Q1": {"execution_time": 2.0}, "Q2": {"execution_time": 0}}
        extension = {"Q1": {"execution_time": 1.0}, "Q2": {"execution_time": 0}}
        lines = _format_query_comparison_section(baseline, extension)
        text = "\n".join(lines)
        assert "Q1" in text

    def test_extension_fails_shows_failed(self):
        baseline = {"Q1": {"execution_time": 2.0}}
        extension = {"Q1": {"execution_time": 0}}
        lines = _format_query_comparison_section(baseline, extension)
        text = "\n".join(lines)
        assert "FAILED" in text

    def test_empty_dicts_return_empty(self):
        assert _format_query_comparison_section({}, {}) == []
        assert _format_query_comparison_section({}, {"Q1": {}}) == []

    def test_no_common_queries_returns_empty(self):
        baseline = {"Q1": {"execution_time": 1.0}}
        extension = {"Q2": {"execution_time": 1.0}}
        assert _format_query_comparison_section(baseline, extension) == []


class TestFormatSummarySection:
    def test_extension_faster_shows_faster_message(self):
        baseline = {"success": True, "total_execution_time": 200.0}
        extension = {"success": True, "total_execution_time": 100.0}
        lines = _format_summary_section(baseline, extension, "Postgres", "pg_duckdb")
        text = "\n".join(lines)
        assert "pg_duckdb" in text
        assert "2.0x faster" in text

    def test_baseline_faster_shows_reversed_message(self):
        baseline = {"success": True, "total_execution_time": 100.0}
        extension = {"success": True, "total_execution_time": 200.0}
        lines = _format_summary_section(baseline, extension, "Postgres", "pg_duckdb")
        text = "\n".join(lines)
        assert "Postgres" in text
        assert "faster" in text

    def test_equal_times_shows_equivalent(self):
        baseline = {"success": True, "total_execution_time": 100.0}
        extension = {"success": True, "total_execution_time": 100.0}
        lines = _format_summary_section(baseline, extension, "Postgres", "pg_duckdb")
        text = "\n".join(lines)
        assert "equivalent" in text.lower()

    def test_no_total_execution_time_returns_summary_header_only(self):
        baseline = {"success": True}
        extension = {"success": True}
        lines = _format_summary_section(baseline, extension, "Postgres", "pg_duckdb")
        assert any("Summary" in line for line in lines)
        assert not any("faster" in line for line in lines)


class TestGenerateComparisonReport:
    def _make_result(self, platform, benchmark="tpch", scale=0.01, total_time=100.0, query_results=None):
        return {
            "platform": platform,
            "benchmark": benchmark,
            "scale_factor": scale,
            "success": True,
            "total_execution_time": total_time,
            "query_results": query_results or {},
        }

    def test_full_report_contains_header(self):
        baseline = self._make_result("postgres")
        extension = self._make_result("pg_duckdb")
        report = generate_comparison_report(baseline, extension)
        assert "PostgreSQL Extension Comparison Report" in report

    def test_benchmark_line_present(self):
        baseline = self._make_result("postgres", benchmark="tpch", scale=0.01)
        extension = self._make_result("pg_duckdb")
        report = generate_comparison_report(baseline, extension)
        assert "TPCH (SF 0.01)" in report

    def test_geometric_mean_speedup_present_with_query_results(self):
        query_results_base = {
            "Q1": {"execution_time": 2.0},
            "Q6": {"execution_time": 4.0},
        }
        query_results_ext = {
            "Q1": {"execution_time": 1.0},
            "Q6": {"execution_time": 2.0},
        }
        baseline = self._make_result("postgres", query_results=query_results_base)
        extension = self._make_result("pg_duckdb", query_results=query_results_ext)
        report = generate_comparison_report(baseline, extension)
        assert "Geometric mean speedup" in report

    def test_platform_names_shown(self):
        baseline = self._make_result("postgres")
        extension = self._make_result("pg_mooncake")
        report = generate_comparison_report(baseline, extension)
        assert "postgres" in report
        assert "pg_mooncake" in report


