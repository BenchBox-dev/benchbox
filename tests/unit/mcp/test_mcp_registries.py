import json
import sys
from pathlib import Path

import pytest

from tests.unit.mcp.public_api import (
    get_prompt_contents,
    list_prompt_names,
    list_resource_template_uris,
    list_resource_uris,
    read_resource_text,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_mcp():
    from mcp.server.mcpserver import MCPServer

    return MCPServer("test-benchbox", version="test")


class TestRegisterAllResources:
    def test_registration_completes(self):
        from benchbox.mcp.resources.registry import register_all_resources

        mcp = _make_mcp()
        register_all_resources(mcp, results_dir=Path("benchmark_runs/results"))

    def test_list_benchmarks_resource(self):
        from benchbox.mcp.resources.registry import register_all_resources

        mcp = _make_mcp()
        register_all_resources(mcp, results_dir=Path("benchmark_runs/results"))

        assert "benchbox://benchmarks" in list_resource_uris(mcp)
        result = read_resource_text(mcp, "benchbox://benchmarks")
        data = json.loads(result)
        assert "benchmarks" in data
        assert "count" in data
        assert data["count"] > 0
        names = [b["name"] for b in data["benchmarks"]]
        assert "tpch" in names

    def test_get_benchmark_resource_valid(self):
        from benchbox.mcp.resources.registry import register_all_resources

        mcp = _make_mcp()
        register_all_resources(mcp, results_dir=Path("benchmark_runs/results"))

        template_key = "benchbox://benchmarks/{name}"
        assert template_key in list_resource_template_uris(mcp)
        result = read_resource_text(mcp, "benchbox://benchmarks/tpch")
        data = json.loads(result)
        assert data["name"] == "tpch"
        assert data["display_name"] == "TPC-H"
        assert data["query_count"] == 22
        assert "scale_factors" in data

    def test_get_benchmark_resource_invalid(self):
        from benchbox.mcp.resources.registry import register_all_resources

        mcp = _make_mcp()
        register_all_resources(mcp, results_dir=Path("benchmark_runs/results"))

        result = read_resource_text(mcp, "benchbox://benchmarks/nonexistent")
        data = json.loads(result)
        assert "error" in data
        assert "available" in data

    def test_list_platforms_resource(self):
        from benchbox.mcp.resources.registry import register_all_resources

        mcp = _make_mcp()
        register_all_resources(mcp, results_dir=Path("benchmark_runs/results"))

        assert "benchbox://platforms" in list_resource_uris(mcp)
        result = read_resource_text(mcp, "benchbox://platforms")
        data = json.loads(result)
        assert "platforms" in data
        assert data["count"] > 0

    def test_get_platform_resource_valid(self):
        from benchbox.mcp.resources.registry import register_all_resources

        mcp = _make_mcp()
        register_all_resources(mcp, results_dir=Path("benchmark_runs/results"))

        result = read_resource_text(mcp, "benchbox://platforms/duckdb")
        data = json.loads(result)
        assert data["name"] == "duckdb"
        assert "capabilities" in data

    def test_get_platform_resource_invalid(self):
        from benchbox.mcp.resources.registry import register_all_resources

        mcp = _make_mcp()
        register_all_resources(mcp, results_dir=Path("benchmark_runs/results"))

        result = read_resource_text(mcp, "benchbox://platforms/nonexistent_platform")
        data = json.loads(result)
        assert "error" in data

    def test_recent_results_no_dir(self, tmp_path):
        from benchbox.mcp.resources.registry import register_all_resources

        mcp = _make_mcp()
        register_all_resources(mcp, results_dir=tmp_path / "nonexistent")

        result = read_resource_text(mcp, "benchbox://results/recent")
        data = json.loads(result)
        assert data["count"] == 0
        assert data["runs"] == []

    def test_recent_results_with_files(self, tmp_path):
        result_data = {
            "platform": {"name": "duckdb"},
            "benchmark": {"id": "tpch", "scale_factor": 0.01},
            "run": {"timestamp": "2026-02-07T10:00:00", "id": "test-run-1"},
        }
        result_file = tmp_path / "test_result.json"
        result_file.write_text(json.dumps(result_data))

        from benchbox.mcp.resources.registry import register_all_resources

        mcp = _make_mcp()
        register_all_resources(mcp, results_dir=tmp_path)

        result = read_resource_text(mcp, "benchbox://results/recent")
        data = json.loads(result)
        assert data["count"] == 1
        assert data["runs"][0]["platform"] == "duckdb"
        assert data["runs"][0]["benchmark"] == "tpch"

    def test_system_profile_resource(self):
        from benchbox.mcp.resources.registry import register_all_resources

        mcp = _make_mcp()
        register_all_resources(mcp, results_dir=Path("benchmark_runs/results"))

        assert "benchbox://system/profile" in list_resource_uris(mcp)
        result = read_resource_text(mcp, "benchbox://system/profile")
        data = json.loads(result)
        assert "cpu" in data
        assert "memory" in data
        assert "packages" in data
        assert data["cpu"]["threads"] > 0


class TestRegisterAllPrompts:
    def test_registration_completes(self):
        from benchbox.mcp.prompts.registry import register_all_prompts

        mcp = _make_mcp()
        register_all_prompts(mcp)

    def test_analyze_results_prompt(self):
        from benchbox.mcp.prompts.registry import register_all_prompts

        mcp = _make_mcp()
        register_all_prompts(mcp)

        assert "analyze_results" in list_prompt_names(mcp)
        result = get_prompt_contents(
            mcp, "analyze_results", benchmark="tpch", platform="duckdb", focus="slowest_queries"
        )
        assert len(result) == 1
        assert result[0].text.startswith("Analyze the TPCH benchmark results")
        assert "TPCH" in result[0].text
        assert "duckdb" in result[0].text
        assert "slowest_queries" in result[0].text

    def test_analyze_results_prompt_no_focus(self):
        from benchbox.mcp.prompts.registry import register_all_prompts

        mcp = _make_mcp()
        register_all_prompts(mcp)

        result = get_prompt_contents(mcp, "analyze_results", benchmark="tpcds", platform="polars-df")
        assert len(result) == 1
        assert "TPCDS" in result[0].text
        assert "slowest_queries" not in result[0].text

    def test_compare_platforms_prompt(self):
        from benchbox.mcp.prompts.registry import register_all_prompts

        mcp = _make_mcp()
        register_all_prompts(mcp)

        result = get_prompt_contents(
            mcp, "compare_platforms", benchmark="tpch", platforms="duckdb,polars-df", scale_factor=0.1
        )
        assert len(result) == 1
        assert "duckdb" in result[0].text
        assert "polars-df" in result[0].text
        assert "0.1" in result[0].text

    def test_identify_regressions_with_runs(self):
        from benchbox.mcp.prompts.registry import register_all_prompts

        mcp = _make_mcp()
        register_all_prompts(mcp)

        result = get_prompt_contents(
            mcp,
            "identify_regressions",
            baseline_run="run1.json",
            comparison_run="run2.json",
            threshold_percent=15.0,
        )
        assert len(result) == 1
        assert "run1.json" in result[0].text
        assert "run2.json" in result[0].text
        assert "15.0" in result[0].text

    def test_identify_regressions_without_runs(self):
        from benchbox.mcp.prompts.registry import register_all_prompts

        mcp = _make_mcp()
        register_all_prompts(mcp)

        result = get_prompt_contents(mcp, "identify_regressions")
        assert len(result) == 1
        assert "recent" in result[0].text.lower()

    def test_benchmark_planning_prompt(self):
        from benchbox.mcp.prompts.registry import register_all_prompts

        mcp = _make_mcp()
        register_all_prompts(mcp)

        result = get_prompt_contents(
            mcp, "benchmark_planning", use_case="production", platforms="duckdb,snowflake", time_budget_minutes=60
        )
        assert len(result) == 1
        assert "production" in result[0].text
        assert "60 minutes" in result[0].text

    def test_troubleshoot_failure_prompt(self):
        from benchbox.mcp.prompts.registry import register_all_prompts

        mcp = _make_mcp()
        register_all_prompts(mcp)

        result = get_prompt_contents(
            mcp, "troubleshoot_failure", error_message="Connection refused", platform="snowflake", benchmark="tpch"
        )
        assert len(result) == 1
        assert "Connection refused" in result[0].text
        assert "snowflake" in result[0].text

    def test_troubleshoot_failure_no_context(self):
        from benchbox.mcp.prompts.registry import register_all_prompts

        mcp = _make_mcp()
        register_all_prompts(mcp)

        result = get_prompt_contents(mcp, "troubleshoot_failure")
        assert len(result) == 1
        assert "No specific context provided" in result[0].text

    def test_benchmark_run_prompt(self):
        from benchbox.mcp.prompts.registry import register_all_prompts

        mcp = _make_mcp()
        register_all_prompts(mcp)

        result = get_prompt_contents(
            mcp, "benchmark_run", platform="duckdb", benchmark="tpch", scale_factor=0.01, queries="Q1,Q6"
        )
        assert len(result) == 1
        assert "duckdb" in result[0].text
        assert "TPCH" in result[0].text
        assert "Q1,Q6" in result[0].text

    def test_benchmark_run_no_queries(self):
        from benchbox.mcp.prompts.registry import register_all_prompts

        mcp = _make_mcp()
        register_all_prompts(mcp)

        result = get_prompt_contents(mcp, "benchmark_run", platform="duckdb", benchmark="tpch", scale_factor=0.01)
        assert len(result) == 1
        assert "TPCH" in result[0].text

    def test_platform_tuning_prompt(self):
        from benchbox.mcp.prompts.registry import register_all_prompts

        mcp = _make_mcp()
        register_all_prompts(mcp)

        result = get_prompt_contents(mcp, "platform_tuning", platform="duckdb", workload="OLAP heavy joins")
        assert len(result) == 1
        assert "duckdb" in result[0].text
        assert "OLAP heavy joins" in result[0].text

    def test_platform_tuning_no_workload(self):
        from benchbox.mcp.prompts.registry import register_all_prompts

        mcp = _make_mcp()
        register_all_prompts(mcp)

        result = get_prompt_contents(mcp, "platform_tuning", platform="snowflake")
        assert len(result) == 1
        assert "snowflake" in result[0].text


