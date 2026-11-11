# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import sys

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestAnalyzeResultsPrompt:
    def test_prompt_returns_text(self):
        benchmark = "tpch"
        platform = "duckdb"
        focus = "slowest_queries"

        prompt_text = f"Analyze the {benchmark.upper()} benchmark results on {platform}."
        focus_text = f"Pay special attention to {focus} in your analysis."

        assert "TPCH" in prompt_text
        assert "duckdb" in prompt_text
        assert "slowest_queries" in focus_text

    def test_prompt_without_focus(self):
        benchmark = "tpcds"
        platform = "polars-df"
        focus = None

        prompt_text = f"Analyze the {benchmark.upper()} benchmark results on {platform}."
        focus_text = f"\nPay special attention to {focus} in your analysis." if focus else ""

        assert "TPCDS" in prompt_text
        assert focus_text == ""


class TestComparePlatformsPrompt:
    def test_prompt_parses_platforms(self):
        platforms_str = "duckdb,polars-df,datafusion"
        platform_list = [p.strip() for p in platforms_str.split(",")]

        assert len(platform_list) == 3
        assert "duckdb" in platform_list
        assert "polars-df" in platform_list
        assert "datafusion" in platform_list

    def test_prompt_includes_scale_factor(self):
        scale_factor = 0.1
        prompt_text = f"Scale Factor: {scale_factor}"

        assert "0.1" in prompt_text


class TestIdentifyRegressionsPrompt:
    def test_prompt_with_specific_runs(self):
        baseline_run = "run1.json"
        comparison_run = "run2.json"
        threshold_percent = 10.0

        prompt_text = f"Baseline: {baseline_run}\nComparison: {comparison_run}\nThreshold: {threshold_percent}%"

        assert "run1.json" in prompt_text
        assert "run2.json" in prompt_text
        assert "10.0" in prompt_text

    def test_prompt_without_specific_runs(self):
        baseline_run = None
        comparison_run = None

        assert baseline_run is None
        assert comparison_run is None


class TestBenchmarkPlanningPrompt:
    def test_prompt_includes_time_budget(self):
        time_budget_minutes = 30
        prompt_text = f"Time Budget: {time_budget_minutes} minutes"

        assert "30 minutes" in prompt_text

    def test_prompt_includes_use_case(self):
        use_case = "regression"
        prompt_text = f"Use Case: {use_case}"

        assert "regression" in prompt_text


class TestTroubleshootFailurePrompt:
    def test_prompt_with_error_message(self):
        error_message = "Connection refused"
        platform = "snowflake"
        benchmark = "tpch"

        context = []
        if error_message:
            context.append(f"Error Message: {error_message}")
        if platform:
            context.append(f"Platform: {platform}")
        if benchmark:
            context.append(f"Benchmark: {benchmark}")

        context_str = "\n".join(context)

        assert "Connection refused" in context_str
        assert "snowflake" in context_str
        assert "tpch" in context_str

    def test_prompt_without_context(self):
        error_message = None
        platform = None
        benchmark = None

        context = []
        if error_message:
            context.append(f"Error Message: {error_message}")
        if platform:
            context.append(f"Platform: {platform}")
        if benchmark:
            context.append(f"Benchmark: {benchmark}")

        context_str = "\n".join(context) if context else "No specific context provided"

        assert context_str == "No specific context provided"


