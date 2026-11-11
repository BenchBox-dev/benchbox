# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestResolveResultPath:
    def test_returns_none_for_nonexistent_file(self, tmp_path):
        from benchbox.mcp.tools.path_utils import resolve_result_file_path

        result = resolve_result_file_path("definitely_not_a_real_file_xyz123.json", tmp_path)
        assert result is None

    def test_rejects_path_traversal_with_dotdot(self, tmp_path):
        from benchbox.mcp.tools.path_utils import resolve_result_file_path

        assert resolve_result_file_path("../etc/passwd", tmp_path) is None
        assert resolve_result_file_path("..\\etc\\passwd", tmp_path) is None
        assert resolve_result_file_path("foo/../../../etc/passwd", tmp_path) is None
        assert resolve_result_file_path("valid/../../secret.json", tmp_path) is None

    def test_rejects_absolute_paths(self, tmp_path):
        from benchbox.mcp.tools.path_utils import resolve_result_file_path

        assert resolve_result_file_path("/etc/passwd", tmp_path) is None
        assert resolve_result_file_path("/tmp/result.json", tmp_path) is None
        assert resolve_result_file_path("\\Windows\\System32\\config", tmp_path) is None

    def test_allows_valid_filename_in_results_dir(self, tmp_path):
        from benchbox.mcp.tools.path_utils import resolve_result_file_path

        results_dir = tmp_path / "benchmark_runs" / "results"
        results_dir.mkdir(parents=True)
        test_file = results_dir / "test_result.json"
        test_file.write_text("{}")

        result = resolve_result_file_path("test_result.json", results_dir)
        assert result is not None
        assert result.exists()
        assert result.name == "test_result.json"

    def test_adds_json_extension_if_missing(self, tmp_path):
        from benchbox.mcp.tools.path_utils import resolve_result_file_path

        results_dir = tmp_path / "benchmark_runs" / "results"
        results_dir.mkdir(parents=True)
        test_file = results_dir / "test_result.json"
        test_file.write_text("{}")

        result = resolve_result_file_path("test_result", results_dir)
        assert result is not None
        assert result.name == "test_result.json"


class TestListChartTemplates:
    def test_returns_templates(self):
        from benchbox.core.visualization.templates import list_templates

        templates = list_templates()

        assert len(templates) >= 5
        template_names = [t.name for t in templates]
        assert "default" in template_names
        assert "flagship" in template_names
        assert "head_to_head" in template_names
        assert "trends" in template_names
        assert "cost_optimization" in template_names

    def test_template_structure(self):
        from benchbox.core.visualization.templates import get_template

        template = get_template("default")

        assert template.name == "default"
        assert template.description
        assert len(template.chart_types) > 0
        assert len(template.formats) > 0


class TestChartTypeDescriptions:
    def test_all_chart_types_documented(self):
        from benchbox.core.visualization.chart_types import ALL_CHART_TYPES
        from benchbox.mcp.tools.visualization import CHART_TYPE_DESCRIPTIONS

        assert set(CHART_TYPE_DESCRIPTIONS.keys()) == set(ALL_CHART_TYPES)

    def test_descriptions_are_strings(self):
        from benchbox.mcp.tools.visualization import CHART_TYPE_DESCRIPTIONS

        for chart_type, description in CHART_TYPE_DESCRIPTIONS.items():
            assert isinstance(description, str), f"{chart_type} description is not a string"
            assert len(description) > 10, f"{chart_type} description is too short"


class TestVisualizationHelpContract:
    def test_generate_chart_help_includes_current_templates_and_semantic_chart_ids(self):
        from benchbox.core.visualization.chart_types import ALL_CHART_TYPES
        from benchbox.core.visualization.templates import list_templates
        from benchbox.mcp.tools.visualization import MCP_GENERATE_CHART_DESCRIPTION

        for chart_type in ALL_CHART_TYPES:
            assert chart_type in MCP_GENERATE_CHART_DESCRIPTION
        for template in list_templates():
            assert template.name in MCP_GENERATE_CHART_DESCRIPTION

        assert "result-aware semantic chart IDs" in MCP_GENERATE_CHART_DESCRIPTION
        assert "textcharts primitive" in MCP_GENERATE_CHART_DESCRIPTION

    def test_suggest_charts_help_uses_semantic_namespace(self):
        from benchbox.core.visualization.chart_types import ALL_CHART_TYPES
        from benchbox.mcp.tools.visualization import MCP_SUGGEST_CHARTS_DESCRIPTION

        assert "result-aware semantic chart IDs" in MCP_SUGGEST_CHARTS_DESCRIPTION
        assert "textcharts primitive" in MCP_SUGGEST_CHARTS_DESCRIPTION
        for chart_type in ALL_CHART_TYPES:
            assert chart_type in MCP_SUGGEST_CHARTS_DESCRIPTION

    def test_registered_tool_descriptions_use_generated_help(self):
        from benchbox.mcp.tools.visualization import MCP_GENERATE_CHART_DESCRIPTION, MCP_SUGGEST_CHARTS_DESCRIPTION

        tools = _get_viz_tool_objects()

        assert tools["generate_chart"].description == MCP_GENERATE_CHART_DESCRIPTION
        assert tools["suggest_charts"].description == MCP_SUGGEST_CHARTS_DESCRIPTION

    def test_textcharts_primitive_ids_are_not_benchbox_chart_ids(self):
        from benchbox.core.visualization.chart_types import ALL_CHART_TYPES
        from benchbox.mcp.tools.visualization import _generate_chart_impl

        assert "bar" not in ALL_CHART_TYPES
        assert "heatmap" not in ALL_CHART_TYPES
        assert "query_heatmap" in ALL_CHART_TYPES

        result = _generate_chart_impl([], [], chart_type="bar", template=None)

        assert result["error"] is True
        assert "VALIDATION_ERROR" in result["error_code"]


class TestGenerateChartValidation:
    def test_invalid_chart_type_error(self):
        from benchbox.mcp.tools.visualization import CHART_TYPE_DESCRIPTIONS

        assert "invalid_type" not in CHART_TYPE_DESCRIPTIONS

    def test_valid_chart_types(self):
        from benchbox.core.visualization.chart_types import ALL_CHART_TYPES
        from benchbox.mcp.tools.visualization import CHART_TYPE_DESCRIPTIONS

        assert set(ALL_CHART_TYPES) == set(CHART_TYPE_DESCRIPTIONS.keys())


class TestGenerateChartSetValidation:
    def test_empty_result_files_parsing(self):
        result_files = ""
        file_list = [f.strip() for f in result_files.split(",") if f.strip()]
        assert len(file_list) == 0

    def test_single_result_file_parsing(self):
        result_files = "run1.json"
        file_list = [f.strip() for f in result_files.split(",") if f.strip()]
        assert len(file_list) == 1
        assert file_list[0] == "run1.json"

    def test_multiple_result_files_parsing(self):
        result_files = "run1.json, run2.json, run3.json"
        file_list = [f.strip() for f in result_files.split(",") if f.strip()]
        assert len(file_list) == 3
        assert file_list[0] == "run1.json"
        assert file_list[1] == "run2.json"
        assert file_list[2] == "run3.json"


class TestResultFilesParsingValidation:
    def test_single_file_parsing(self):
        result_files = "single.json"
        file_list = [f.strip() for f in result_files.split(",") if f.strip()]
        assert len(file_list) == 1
        assert file_list[0] == "single.json"

    def test_multiple_files_parsing(self):
        result_files = "file1.json, file2.json, file3.json"
        file_list = [f.strip() for f in result_files.split(",") if f.strip()]
        assert len(file_list) == 3
        assert file_list == ["file1.json", "file2.json", "file3.json"]

    def test_empty_string_returns_empty_list(self):
        result_files = ""
        file_list = [f.strip() for f in result_files.split(",") if f.strip()]
        assert len(file_list) == 0


class TestToolRegistration:
    def test_visualization_tools_registered(self):
        from benchbox.mcp.server import create_benchbox_server

        server = create_benchbox_server()

        from tests.unit.mcp.public_api import list_tool_names

        assert {"suggest_charts", "generate_chart"} <= list_tool_names(server)

    def test_suggest_charts_tool_registered(self):
        tools = _get_viz_tool_functions()
        assert "suggest_charts" in tools


class TestSuggestCharts:
    def test_returns_suggestions_for_valid_results(self, tmp_path):
        results_dir = tmp_path / "benchmark_runs" / "results"
        results_dir.mkdir(parents=True)

        result1 = results_dir / "duckdb_result.json"
        result1.write_text("""{
            "benchmark": {"name": "TPC-H", "scale_factor": 1},
            "platform": {"name": "DuckDB"},
            "config": {"mode": "sql"},
            "results": {
                "queries": {"details": [{"id": "Q1", "execution_time_ms": 100}]},
                "timing": {"total_ms": 1000, "avg_ms": 100}
            }
        }""")

        result2 = results_dir / "postgres_result.json"
        result2.write_text("""{
            "benchmark": {"name": "TPC-H", "scale_factor": 1},
            "platform": {"name": "Postgres"},
            "config": {"mode": "sql"},
            "results": {
                "queries": {"details": [{"id": "Q1", "execution_time_ms": 150}]},
                "timing": {"total_ms": 1500, "avg_ms": 150}
            }
        }""")

        tools = _get_viz_tool_functions(results_dir=results_dir)
        suggest_charts = tools.get("suggest_charts")
        assert suggest_charts is not None

        result = suggest_charts(result_files="duckdb_result.json,postgres_result.json")

        assert "suggestions" in result
        assert "primary" in result
        assert "data_profile" in result

        chart_types = [s["chart_type"] for s in result["suggestions"]]
        assert "performance_bar" in chart_types
        assert "query_heatmap" in chart_types

        assert result["data_profile"]["result_count"] == 2
        assert "DuckDB" in result["data_profile"]["platforms"]
        assert "Postgres" in result["data_profile"]["platforms"]

    def test_returns_error_for_missing_files(self):
        tools = _get_viz_tool_functions()
        suggest_charts = tools.get("suggest_charts")
        assert suggest_charts is not None

        result = suggest_charts(result_files="nonexistent_file.json")

        assert result["error"] is True
        assert "RESOURCE_NOT_FOUND" in result["error_code"]

    def test_returns_error_for_empty_input(self):
        tools = _get_viz_tool_functions()
        suggest_charts = tools.get("suggest_charts")
        assert suggest_charts is not None

        result = suggest_charts(result_files="")

        assert result["error"] is True
        assert "VALIDATION_ERROR" in result["error_code"]

    def test_single_result_suggests_distribution_box_as_primary(self, tmp_path):
        results_dir = tmp_path / "benchmark_runs" / "results"
        results_dir.mkdir(parents=True)

        result1 = results_dir / "single_result.json"
        result1.write_text("""{
            "benchmark": {"name": "TPC-H", "scale_factor": 1},
            "platform": {"name": "DuckDB"},
            "config": {"mode": "sql"},
            "results": {
                "queries": {"details": [
                    {"id": "Q1", "execution_time_ms": 100},
                    {"id": "Q2", "execution_time_ms": 200},
                    {"id": "Q3", "execution_time_ms": 150}
                ]},
                "timing": {"total_ms": 450, "avg_ms": 150}
            }
        }""")

        tools = _get_viz_tool_functions(results_dir=results_dir)
        suggest_charts = tools.get("suggest_charts")
        result = suggest_charts(result_files="single_result.json")

        assert result["primary"]["chart_type"] == "distribution_box"
        chart_types = [s["chart_type"] for s in result["suggestions"]]
        assert "query_heatmap" not in chart_types
        assert "distribution_box" in chart_types
        assert result["data_profile"]["result_count"] == 1

    def test_results_without_query_data_suggests_only_performance_bar(self, tmp_path):
        results_dir = tmp_path / "benchmark_runs" / "results"
        results_dir.mkdir(parents=True)

        result1 = results_dir / "no_queries.json"
        result1.write_text("""{
            "benchmark": {"name": "TPC-H", "scale_factor": 1},
            "platform": {"name": "DuckDB"},
            "config": {"mode": "sql"},
            "results": {
                "queries": {"details": []},
                "timing": {"total_ms": 1000, "avg_ms": 100}
            }
        }""")

        tools = _get_viz_tool_functions(results_dir=results_dir)
        suggest_charts = tools.get("suggest_charts")
        result = suggest_charts(result_files="no_queries.json")

        assert result["primary"]["chart_type"] == "performance_bar"
        chart_types = [s["chart_type"] for s in result["suggestions"]]
        assert chart_types == ["performance_bar"]
        assert result["data_profile"]["total_queries"] == 0
        assert result["data_profile"]["has_cost_data"] is False

    def test_results_with_cost_data_suggests_cost_scatter(self, tmp_path):
        results_dir = tmp_path / "benchmark_runs" / "results"
        results_dir.mkdir(parents=True)

        result1 = results_dir / "with_cost.json"
        result1.write_text("""{
            "benchmark": {"name": "TPC-H", "scale_factor": 1},
            "platform": {"name": "Snowflake"},
            "config": {"mode": "sql"},
            "cost_summary": {"total_cost": 0.05},
            "results": {
                "queries": {"details": [{"id": "Q1", "execution_time_ms": 100}]},
                "timing": {"total_ms": 1000, "avg_ms": 100}
            }
        }""")

        tools = _get_viz_tool_functions(results_dir=results_dir)
        suggest_charts = tools.get("suggest_charts")
        result = suggest_charts(result_files="with_cost.json")

        chart_types = [s["chart_type"] for s in result["suggestions"]]
        assert "cost_scatter" in chart_types
        assert result["data_profile"]["has_cost_data"] is True

    def test_power_run_suggests_power_bar(self, tmp_path):
        results_dir = tmp_path / "benchmark_runs" / "results"
        results_dir.mkdir(parents=True)

        result_file = results_dir / "power_result.json"
        result_file.write_text("""{
            "version": "2.1",
            "benchmark": {"id": "joinorder", "name": "JoinOrder", "scale_factor": 1, "test_type": "power"},
            "platform": {"name": "DuckDB"},
            "summary": {"queries": {"total": 1, "passed": 1, "failed": 0}, "timing": {"total_ms": 100}},
            "queries": [{"id": "1a", "ms": 12.0, "status": "SUCCESS"}]
        }""")

        tools = _get_viz_tool_functions(results_dir=results_dir)
        suggest_charts = tools.get("suggest_charts")
        result = suggest_charts(result_files="power_result.json")

        chart_types = [suggestion["chart_type"] for suggestion in result["suggestions"]]
        assert "power_bar" in chart_types
        assert result["primary"]["chart_type"] == "power_bar"
        assert result["data_profile"]["has_power_data"] is True

    def test_multiple_results_with_timestamps_suggests_time_series(self, tmp_path):
        results_dir = tmp_path / "benchmark_runs" / "results"
        results_dir.mkdir(parents=True)

        for i, ts in enumerate(["2026-01-26T10:00:00", "2026-01-27T10:00:00", "2026-01-28T10:00:00"]):
            result_file = results_dir / f"run_{i}.json"
            result_file.write_text(f"""{{
                "benchmark": {{"name": "TPC-H", "scale_factor": 1}},
                "platform": {{"name": "DuckDB"}},
                "config": {{"mode": "sql"}},
                "execution": {{"timestamp": "{ts}"}},
                "results": {{
                    "queries": {{"details": [{{"id": "Q1", "execution_time_ms": {100 + i * 10}}}]}},
                    "timing": {{"total_ms": {1000 + i * 100}, "avg_ms": 100}}
                }}
            }}""")

        tools = _get_viz_tool_functions(results_dir=results_dir)
        suggest_charts = tools.get("suggest_charts")
        result = suggest_charts(result_files="run_0.json,run_1.json,run_2.json")

        chart_types = [s["chart_type"] for s in result["suggestions"]]
        assert "time_series" in chart_types
        assert result["data_profile"]["has_timestamps"] is True
        assert result["data_profile"]["result_count"] == 3


def _get_viz_tool_functions(*, results_dir=None, charts_dir=None):
    return {
        name: tool.fn for name, tool in _get_viz_tool_objects(results_dir=results_dir, charts_dir=charts_dir).items()
    }


def _get_viz_tool_objects(*, results_dir=None, charts_dir=None):
    from benchbox.mcp import create_server
    from tests.unit.mcp.public_api import get_tool, list_tool_names

    kwargs = {}
    if results_dir is not None:
        kwargs["results_dir"] = results_dir
    if charts_dir is not None:
        kwargs["charts_dir"] = charts_dir
    server = create_server(**kwargs)
    return {
        name: get_tool(server, name) for name in ("suggest_charts", "generate_chart") if name in list_tool_names(server)
    }


class TestGenerateChartIntegration:
    def test_generates_ascii_chart_with_valid_inputs(self, tmp_path):
        results_dir = tmp_path / "benchmark_runs" / "results"
        results_dir.mkdir(parents=True)

        result_file = results_dir / "test_result.json"
        result_file.write_text("""{
            "benchmark": {"name": "TPC-H", "scale_factor": 1},
            "platform": {"name": "DuckDB"},
            "config": {"mode": "sql"},
            "results": {
                "queries": {"details": [{"id": "Q1", "execution_time_ms": 100}]},
                "timing": {"total_ms": 1000, "avg_ms": 100}
            }
        }""")

        tools = _get_viz_tool_functions(results_dir=results_dir)
        generate_chart = tools.get("generate_chart")
        assert generate_chart is not None

        result = generate_chart(
            result_files="test_result.json",
            chart_type="performance_bar",
        )

        assert result["status"] == "generated"
        assert result["chart_type"] == "performance_bar"
        assert result["format"] == "ascii"
        assert "content" in result

    def test_file_output_is_explicitly_rejected(self, tmp_path):
        tools = _get_viz_tool_functions(results_dir=tmp_path / "results")
        result = tools["generate_chart"](result_files="missing.json", output_dir="../escape")

        assert result["error"] is True
        assert result["error_code"] == "VALIDATION_ERROR"
        assert result["details"]["output_mode"] == "inline"
        assert "escape" not in str(result)

    def test_non_ascii_formats_are_explicitly_rejected(self, tmp_path):
        tools = _get_viz_tool_functions(results_dir=tmp_path / "results")
        result = tools["generate_chart"](result_files="missing.json", format="png")

        assert result["error"] is True
        assert result["error_code"] == "VALIDATION_INVALID_FORMAT"
        assert result["details"]["supported_formats"] == ["ascii"]

    def test_rejects_invalid_chart_type(self, tmp_path):
        results_dir = tmp_path / "benchmark_runs" / "results"
        results_dir.mkdir(parents=True)

        result_file = results_dir / "test_result.json"
        result_file.write_text("{}")

        tools = _get_viz_tool_functions(results_dir=results_dir)
        generate_chart = tools.get("generate_chart")
        assert generate_chart is not None

        result = generate_chart(
            result_files="test_result.json",
            chart_type="invalid_chart_type",
        )

        assert result["error"] is True
        assert "VALIDATION_ERROR" in result["error_code"]

    def test_known_inapplicable_chart_is_not_reported_as_unsupported(self, tmp_path):
        results_dir = tmp_path / "benchmark_runs" / "results"
        results_dir.mkdir(parents=True)

        result_file = results_dir / "test_result.json"
        result_file.write_text("""{
            "benchmark": {"name": "TPC-H", "scale_factor": 1},
            "platform": {"name": "DuckDB"},
            "config": {"mode": "sql"},
            "results": {
                "queries": {"details": [{"id": "Q1", "execution_time_ms": 100}]},
                "timing": {"total_ms": 1000, "avg_ms": 100}
            }
        }""")

        tools = _get_viz_tool_functions(results_dir=results_dir)
        generate_chart = tools.get("generate_chart")

        result = generate_chart(result_files="test_result.json", chart_type="stacked_phase")

        assert result["error"] is True
        assert "VALIDATION_ERROR" in result["error_code"]
        assert "not applicable" in result["message"]
        assert "Unsupported chart type" not in result["message"]
        assert result["details"]["skip"]["chart_type"] == "stacked_phase"

    def test_renderer_failure_returns_internal_error_for_single_chart(self, tmp_path, monkeypatch):
        from benchbox.core.visualization import ascii_api

        results_dir = tmp_path / "benchmark_runs" / "results"
        results_dir.mkdir(parents=True)

        result_file = results_dir / "test_result.json"
        result_file.write_text("""{
            "benchmark": {"name": "TPC-H", "scale_factor": 1},
            "platform": {"name": "DuckDB"},
            "config": {"mode": "sql"},
            "results": {
                "queries": {"details": [{"id": "Q1", "execution_time_ms": 100}]},
                "timing": {"total_ms": 1000, "avg_ms": 100}
            }
        }""")

        def failing_renderer(results, chart_type, *, options=None, subtitle=None):
            raise RuntimeError("boom")

        monkeypatch.setattr(ascii_api, "render_ascii_chart_from_results", failing_renderer)

        tools = _get_viz_tool_functions(results_dir=results_dir)
        generate_chart = tools.get("generate_chart")

        result = generate_chart(result_files="test_result.json", chart_type="performance_bar")

        assert result["error"] is True
        assert "INTERNAL_ERROR" in result["error_code"]
        assert "boom" in result["message"]
        assert result["details"]["skip"]["reason"] == "renderer failed"

    def test_rejects_pairwise_chart_without_two_results(self, tmp_path):
        results_dir = tmp_path / "benchmark_runs" / "results"
        results_dir.mkdir(parents=True)

        result_file = results_dir / "test_result.json"
        result_file.write_text("""{
            "benchmark": {"name": "TPC-H", "scale_factor": 1},
            "platform": {"name": "DuckDB"},
            "config": {"mode": "sql"},
            "results": {
                "queries": {"details": [{"id": "Q1", "execution_time_ms": 100}]},
                "timing": {"total_ms": 1000, "avg_ms": 100}
            }
        }""")

        tools = _get_viz_tool_functions(results_dir=results_dir)
        generate_chart = tools.get("generate_chart")
        assert generate_chart is not None

        result = generate_chart(result_files="test_result.json", chart_type="comparison_bar")

        assert result["error"] is True
        assert "VALIDATION_ERROR" in result["error_code"]

    def test_generates_chart_with_multiple_files(self, tmp_path):
        results_dir = tmp_path / "benchmark_runs" / "results"
        results_dir.mkdir(parents=True)

        result_file1 = results_dir / "result1.json"
        result_file1.write_text("""{
            "benchmark": {"name": "TPC-H", "scale_factor": 1},
            "platform": {"name": "DuckDB"},
            "config": {"mode": "sql"},
            "results": {
                "queries": {"details": [{"id": "Q1", "execution_time_ms": 100}]},
                "timing": {"total_ms": 1000, "avg_ms": 100}
            }
        }""")
        result_file2 = results_dir / "result2.json"
        result_file2.write_text("""{
            "benchmark": {"name": "TPC-H", "scale_factor": 1},
            "platform": {"name": "Postgres"},
            "config": {"mode": "sql"},
            "results": {
                "queries": {"details": [{"id": "Q1", "execution_time_ms": 150}]},
                "timing": {"total_ms": 1500, "avg_ms": 150}
            }
        }""")

        tools = _get_viz_tool_functions(results_dir=results_dir)
        generate_chart = tools.get("generate_chart")

        result = generate_chart(
            result_files="result1.json,result2.json",
            chart_type="performance_bar",
        )

        assert result["status"] == "generated"
        assert result["chart_type"] == "performance_bar"
        assert result["format"] == "ascii"
        assert len(result["source_files"]) == 2

    def test_generates_chart_set_with_template(self, tmp_path):
        results_dir = tmp_path / "benchmark_runs" / "results"
        results_dir.mkdir(parents=True)

        result_file = results_dir / "test_result.json"
        result_file.write_text("""{
            "benchmark": {"name": "TPC-H", "scale_factor": 1},
            "platform": {"name": "DuckDB"},
            "config": {"mode": "sql"},
            "results": {
                "queries": {"details": [{"id": "Q1", "execution_time_ms": 100}]},
                "timing": {"total_ms": 1000, "avg_ms": 100}
            }
        }""")

        tools = _get_viz_tool_functions(results_dir=results_dir)
        generate_chart = tools.get("generate_chart")

        result = generate_chart(
            result_files="test_result.json",
            template="default",
        )

        assert result["status"] == "generated"
        assert result["template"] == "default"
        assert result["chart_count"] >= 1
        assert result["format"] == "ascii"
        assert "content" in result
        assert "skipped_chart_types" in result
        assert isinstance(result["skipped_chart_types"], list)
        for skipped in result["skipped_chart_types"]:
            assert "chart_type" in skipped
            assert "reason" in skipped


class TestGenerateChartErrorHandling:
    def test_returns_error_for_missing_files(self, tmp_path):
        results_dir = tmp_path / "benchmark_runs" / "results"
        results_dir.mkdir(parents=True)

        tools = _get_viz_tool_functions(results_dir=results_dir)
        generate_chart = tools.get("generate_chart")
        assert generate_chart is not None

        result = generate_chart(
            result_files="nonexistent_file.json",
            chart_type="performance_bar",
        )

        assert result["error"] is True
        assert "RESOURCE_NOT_FOUND" in result["error_code"]

    def test_returns_error_for_empty_result_files(self):
        tools = _get_viz_tool_functions()
        generate_chart = tools.get("generate_chart")
        assert generate_chart is not None

        result = generate_chart(
            result_files="",
            chart_type="performance_bar",
        )

        assert result["error"] is True
        assert "VALIDATION_ERROR" in result["error_code"]

    def test_returns_error_for_invalid_template(self, tmp_path):
        results_dir = tmp_path / "benchmark_runs" / "results"
        results_dir.mkdir(parents=True)

        result_file = results_dir / "test_result.json"
        result_file.write_text("{}")

        tools = _get_viz_tool_functions(results_dir=results_dir)
        generate_chart = tools.get("generate_chart")

        result = generate_chart(
            result_files="test_result.json",
            template="nonexistent_template",
        )

        assert result["error"] is True
        assert "VALIDATION_ERROR" in result["error_code"]


