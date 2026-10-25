import json
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.results.models import BenchmarkResults
from benchbox.core.results.schema import build_result_payload
from tests.fixtures.result_dict_fixtures import write_v2_result_file
from tests.unit.mcp.public_api import build_tool_functions

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


@pytest.fixture(scope="module")
def tool_functions():
    return build_tool_functions()


class TestValidateConfigTool:
    def test_valid_duckdb_tpch_returns_valid(self, tool_functions):
        fn = tool_functions["run_benchmark"]
        result = fn(platform="duckdb", benchmark="tpch", scale_factor=1.0, validate_only=True)

        assert result["valid"] is True
        assert result["platform"] == "duckdb"
        assert result["benchmark"] == "tpch"
        assert result["errors"] == []

    def test_unknown_platform_returns_error(self, tool_functions):
        fn = tool_functions["run_benchmark"]
        result = fn(platform="nonexistent_db", benchmark="tpch", scale_factor=1.0, validate_only=True)

        assert result["valid"] is False
        assert any("Unknown platform" in e for e in result["errors"])

    def test_unknown_benchmark_returns_error(self, tool_functions):
        fn = tool_functions["run_benchmark"]
        result = fn(platform="duckdb", benchmark="fake_benchmark", scale_factor=1.0, validate_only=True)

        assert result["valid"] is False
        assert any("Unknown benchmark" in e for e in result["errors"])

    def test_negative_scale_factor_returns_error(self, tool_functions):
        fn = tool_functions["run_benchmark"]
        result = fn(platform="duckdb", benchmark="tpch", scale_factor=-1.0, validate_only=True)

        assert result["valid"] is False
        assert any("positive" in e.lower() for e in result["errors"])

    def test_cloud_platform_produces_warning(self, tool_functions):
        fn = tool_functions["run_benchmark"]
        result = fn(platform="snowflake", benchmark="tpch", scale_factor=1.0, validate_only=True)

        assert any("credential" in w.lower() for w in result["warnings"])

    def test_dataframe_unsupported_benchmark_returns_error(self, tool_functions):
        fn = tool_functions["run_benchmark"]
        result = fn(platform="polars-df", benchmark="ai_primitives", scale_factor=1.0, validate_only=True)

        assert result["valid"] is False
        assert any("DataFrame" in e for e in result["errors"])

    def test_response_contains_execution_mode(self, tool_functions):
        fn = tool_functions["run_benchmark"]
        result = fn(platform="duckdb", benchmark="tpch", scale_factor=1.0, validate_only=True)

        assert "execution_mode" in result
        assert result["execution_mode"] in ("sql", "dataframe", "data_only")


class TestGetQueryDetailsTool:
    def test_tpch_query_returns_sql(self, tool_functions):
        fn = tool_functions["get_query_details"]
        result = fn(benchmark="tpch", query_id="6")

        assert "error" not in result
        assert result["benchmark"] == "tpch"
        assert "sql" in result
        assert "select" in result["sql"].lower() or "SELECT" in result["sql"]

    def test_joinorder_query_returns_sql(self, tool_functions):
        fn = tool_functions["get_query_details"]
        result = fn(benchmark="joinorder", query_id="1a")

        assert "error" not in result
        assert result["benchmark"] == "joinorder"
        assert "sql" in result
        assert "movie_companies" in result["sql"]

    def test_query_id_with_q_prefix(self, tool_functions):
        fn = tool_functions["get_query_details"]
        result = fn(benchmark="tpch", query_id="Q6")

        assert "error" not in result
        assert result["normalized_id"] == "6"

    def test_unknown_benchmark_returns_error(self, tool_functions):
        fn = tool_functions["get_query_details"]
        result = fn(benchmark="fake_benchmark", query_id="1")

        assert result["error"] is True
        assert result["error_code"] == "RESOURCE_NOT_FOUND"

    def test_response_has_complexity_hints(self, tool_functions):
        fn = tool_functions["get_query_details"]
        result = fn(benchmark="tpch", query_id="6")

        assert "complexity_hints" in result
        assert "complexity" in result["complexity_hints"]

    def test_response_has_benchmark_info(self, tool_functions):
        fn = tool_functions["get_query_details"]
        result = fn(benchmark="tpch", query_id="1")

        assert "benchmark_info" in result
        assert "display_name" in result["benchmark_info"]
        assert result["benchmark_info"]["support_status"] == "stable"

    def test_internal_benchmark_info_omits_support_status(self):
        from benchbox.core.benchmark_registry import BENCHMARK_METADATA
        from benchbox.mcp.tools.benchmark import _build_query_details_benchmark_info

        info = _build_query_details_benchmark_info(
            "joinorder_synthetic",
            BENCHMARK_METADATA["joinorder_synthetic"],
        )

        assert info["display_name"] == "JoinOrder Synthetic"
        assert "support_status" not in info


def _future_benchmark_meta(support_status: str, surface: str, *, supports_dataframe: bool = False) -> dict[str, object]:
    return {
        "display_name": f"Future {support_status}/{surface}",
        "description": "synthetic future-status fixture",
        "category": "Test",
        "num_queries": 0,
        "query_description": "n/a",
        "supports_streams": False,
        "default_scale": 1.0,
        "scale_options": [1.0],
        "min_scale": 1.0,
        "complexity": "Low",
        "estimated_time_range": (0, 0),
        "supports_dataframe": supports_dataframe,
        "support_status": support_status,
        "surface": surface,
    }


@contextmanager
def _registered_future_benchmark(benchmark_id: str, meta: dict[str, object]):
    from benchbox.core import benchmark_registry

    fixtures = {benchmark_id: meta}
    with patch.dict(benchmark_registry.BENCHMARK_METADATA, fixtures, clear=False):
        yield


class TestFutureSupportStatusVisibilityInvariants:
    def test_internal_future_status_hidden_from_mcp_discovery(self):
        from benchbox.mcp.tools.discovery import _get_benchmark_info_impl, _list_benchmarks_impl

        with _registered_future_benchmark("x_future_internal", _future_benchmark_meta("document_only", "internal")):
            listed = {row["name"] for row in _list_benchmarks_impl()["benchmarks"]}
            assert "x_future_internal" not in listed

            info = _get_benchmark_info_impl("x_future_internal")
            assert "error" in info
            assert "x_future_internal" not in info["available_benchmarks"]

    def test_internal_future_status_query_details_omit_support_status(self):
        from benchbox.mcp.tools.benchmark import _build_query_details_benchmark_info

        meta = _future_benchmark_meta("deprecated", "internal")
        with _registered_future_benchmark("x_future_internal", meta):
            info = _build_query_details_benchmark_info("x_future_internal", meta)

        assert info["display_name"] == "Future deprecated/internal"
        assert "support_status" not in info

    def test_public_future_status_exposed_and_labeled_over_mcp(self):
        from benchbox.mcp.tools.discovery import _get_benchmark_info_impl, _list_benchmarks_impl

        with _registered_future_benchmark("x_future_public", _future_benchmark_meta("deprecated", "public")):
            listed = {row["name"]: row for row in _list_benchmarks_impl()["benchmarks"]}
            assert listed["x_future_public"]["support_status"] == "deprecated"

            info = _get_benchmark_info_impl("x_future_public")
            assert "error" not in info
            assert info["support_status"] == "deprecated"

    def test_dataframe_routing_uses_capability_not_support_status(self):
        from benchbox.core import benchmark_registry
        from benchbox.core.validation.config import validate_benchmark_config as _validate_benchmark_config

        fixtures = {
            "x_experimental_df": _future_benchmark_meta("experimental", "public", supports_dataframe=True),
            "x_stable_nodf": _future_benchmark_meta("stable", "public", supports_dataframe=False),
        }
        with patch.dict(benchmark_registry.BENCHMARK_METADATA, fixtures, clear=False):
            errors: list[str] = []
            warnings: list[str] = []
            _validate_benchmark_config("x_experimental_df", "x_experimental_df", 1.0, "polars-df", errors, warnings)
            assert errors == []

            errors = []
            warnings = []
            _validate_benchmark_config("x_stable_nodf", "x_stable_nodf", 1.0, "polars-df", errors, warnings)
            assert errors == ["DataFrame mode does not support x_stable_nodf benchmark"]


class TestListPlatformsTool:
    def test_returns_platforms_list(self, tool_functions):
        fn = tool_functions["list_available"]
        result = fn(category="platforms")

        assert "platforms" in result
        assert "count" in result
        assert result["count"] > 0
        assert len(result["platforms"]) == result["count"]

    def test_platform_entries_have_required_fields(self, tool_functions):
        fn = tool_functions["list_available"]
        result = fn(category="platforms")

        required_fields = {"name", "display_name", "category", "available", "supports_sql"}
        for platform in result["platforms"]:
            assert required_fields.issubset(platform.keys()), f"Missing fields in {platform['name']}"

    def test_duckdb_is_available(self, tool_functions):
        fn = tool_functions["list_available"]
        result = fn(category="platforms")

        duckdb_entries = [p for p in result["platforms"] if p["name"] == "duckdb"]
        assert len(duckdb_entries) == 1
        assert duckdb_entries[0]["available"] is True

    def test_summary_has_counts(self, tool_functions):
        fn = tool_functions["list_available"]
        result = fn(category="platforms")

        assert "summary" in result
        assert "available" in result["summary"]
        assert "sql_platforms" in result["summary"]
        assert "dataframe_platforms" in result["summary"]


class TestListBenchmarksTool:
    def test_returns_benchmarks_list(self, tool_functions):
        fn = tool_functions["list_available"]
        result = fn(category="benchmarks")

        assert "benchmarks" in result
        assert "count" in result
        assert result["count"] > 0

    def test_tpch_is_listed(self, tool_functions):
        fn = tool_functions["list_available"]
        result = fn(category="benchmarks")

        names = [b["name"] for b in result["benchmarks"]]
        assert "tpch" in names

    def test_internal_benchmarks_are_hidden(self, tool_functions):
        fn = tool_functions["list_available"]
        result = fn(category="benchmarks")

        names = [b["name"] for b in result["benchmarks"]]
        assert "joinorder" in names
        assert "joinorder_synthetic" not in names

    def test_benchmark_entries_have_required_fields(self, tool_functions):
        fn = tool_functions["list_available"]
        result = fn(category="benchmarks")

        for bm in result["benchmarks"]:
            assert "name" in bm
            assert "display_name" in bm
            assert "support_status" in bm
            assert "query_count" in bm
            assert "scale_factors" in bm

    def test_benchmark_entries_project_support_status(self, tool_functions):
        fn = tool_functions["list_available"]
        result = fn(category="benchmarks")
        benchmarks = {bm["name"]: bm for bm in result["benchmarks"]}

        assert benchmarks["tpch"]["support_status"] == "stable"
        assert benchmarks["ai_primitives"]["support_status"] == "experimental"

    def test_categories_grouping(self, tool_functions):
        fn = tool_functions["list_available"]
        result = fn(category="benchmarks")

        assert "categories" in result
        assert isinstance(result["categories"], dict)


class TestGetBenchmarkInfoTool:
    def test_tpch_info_returns_details(self, tool_functions):
        fn = tool_functions["get_benchmark_info"]
        result = fn(benchmark="tpch")

        assert "error" not in result
        assert result.get("name") == "tpch" or result.get("benchmark") == "tpch"
        assert result["support_status"] == "stable"

    def test_unknown_benchmark_returns_error(self, tool_functions):
        fn = tool_functions["get_benchmark_info"]
        result = fn(benchmark="nonexistent")

        assert "error" in result

    def test_internal_benchmark_returns_not_found(self, tool_functions):
        fn = tool_functions["get_benchmark_info"]
        result = fn(benchmark="joinorder_synthetic")

        assert "error" in result
        assert "joinorder_synthetic" not in result["available_benchmarks"]


class TestRunBenchmarkToolErrors:
    def test_unknown_benchmark_returns_not_found(self, tool_functions):
        fn = tool_functions["run_benchmark"]
        result = fn(platform="duckdb", benchmark="nonexistent_bench", scale_factor=0.01)

        assert result.get("status") == "failed"
        assert result["error_code"] == "RESOURCE_NOT_FOUND"

    def test_unknown_platform_returns_error(self, tool_functions):
        fn = tool_functions["run_benchmark"]
        result = fn(platform="nonexistent_platform", benchmark="tpch", scale_factor=0.01)

        assert result.get("status") == "failed"
        assert "error" in result or "error_code" in result

    def test_response_has_execution_id(self, tool_functions):
        fn = tool_functions["run_benchmark"]
        result = fn(platform="duckdb", benchmark="nonexistent", scale_factor=0.01)

        assert "execution_id" in result
        assert result["execution_id"].startswith("mcp_")

    def test_not_found_error_has_error_code(self, tool_functions):
        fn = tool_functions["run_benchmark"]
        result = fn(platform="duckdb", benchmark="nonexistent", scale_factor=0.01)

        assert result["error_code"] == "RESOURCE_NOT_FOUND"
        assert result["status"] == "failed"


class TestRunBenchmarkToolSuccess:
    def test_successful_run_returns_completed(self, tool_functions, tmp_path):
        fn = tool_functions["run_benchmark"]

        mock_result = MagicMock()
        mock_result.query_results = []

        mock_instance = MagicMock()
        mock_instance.run_with_platform.return_value = mock_result

        mock_bm_class = MagicMock(return_value=mock_instance)

        result_path = tmp_path / "result.json"
        write_v2_result_file(result_path, execution_id="mcp_test", timestamp="2026-01-01T00:00:00")

        mock_exporter = MagicMock()
        mock_exporter.export_result.return_value = {"json": result_path}

        with (
            patch("benchbox.mcp.tools.benchmark._get_platform_adapter"),
            patch("benchbox.mcp.tools.benchmark.get_public_benchmark_class", return_value=mock_bm_class),
            patch("benchbox.mcp.tools.benchmark.ResultExporter", return_value=mock_exporter),
        ):
            result = fn(platform="duckdb", benchmark="tpch", scale_factor=0.01)

        assert result["mcp_metadata"]["status"] == "completed"
        assert result["platform"]["name"] == "duckdb"
        assert result["benchmark"]["id"] == "tpch"
        assert result["summary"]["queries"]["total"] == 22
        assert result["summary"]["queries"]["passed"] == 22
        assert result["mcp_metadata"]["result_file"] == str(result_path)

    def test_query_subset_forwarded(self, tool_functions):
        fn = tool_functions["run_benchmark"]

        with patch(
            "benchbox.mcp.tools.benchmark._execute_mcp_run_via_core",
            return_value={"mcp_metadata": {"status": "completed"}},
        ) as run_core:
            fn(platform="duckdb", benchmark="tpch", scale_factor=0.01, queries="1,6,17")

        assert run_core.call_args.kwargs["queries"] == "1,6,17"

    def test_result_exported_with_execution_id(self, tool_functions):
        fn = tool_functions["run_benchmark"]

        mock_result = MagicMock()
        mock_result.total_queries = 1
        mock_result.successful_queries = 1
        mock_result.failed_queries = 0
        mock_result.total_execution_time = 1.0
        mock_result.query_results = []

        mock_exporter = MagicMock()
        mock_exporter.export_result.return_value = {"json": Path("/tmp/result.json")}

        with (
            patch("benchbox.mcp.tools.benchmark._get_platform_adapter"),
            patch("benchbox.mcp.tools.benchmark.get_public_benchmark_class", return_value=MagicMock),
            patch("benchbox.mcp.tools.benchmark.ResultExporter", return_value=mock_exporter),
            patch("benchbox.core.run_service.execute_run", return_value=mock_result),
        ):
            fn(platform="duckdb", benchmark="tpch", scale_factor=0.01)

        assert mock_result.execution_id.startswith("mcp_")
        mock_exporter.export_result.assert_called_once_with(mock_result, formats=["json"])

    def test_export_failure_does_not_break_response(self, tool_functions):
        fn = tool_functions["run_benchmark"]

        real_result = BenchmarkResults(
            benchmark_name="tpch",
            platform="duckdb",
            scale_factor=0.01,
            execution_id="exec",
            timestamp=datetime(2026, 1, 1),
            duration_seconds=2.0,
            total_queries=5,
            successful_queries=5,
            failed_queries=0,
            total_execution_time=2.0,
        )

        mock_exporter = MagicMock()
        mock_exporter.export_result.side_effect = OSError("disk full")

        with (
            patch("benchbox.mcp.tools.benchmark.get_public_benchmark_class", return_value=MagicMock),
            patch("benchbox.mcp.tools.benchmark.ResultExporter", return_value=mock_exporter),
            patch("benchbox.core.run_service.execute_run", return_value=real_result),
        ):
            result = fn(platform="duckdb", benchmark="tpch", scale_factor=0.01)

        mock_exporter.export_result.assert_called_once()
        assert type(result) is dict
        assert result["result_schema_version"] == build_result_payload(real_result)["result_schema_version"]
        assert result["benchmark"]["id"] == "tpch"
        assert result["run"]["id"] == result["mcp_metadata"]["execution_id"]
        assert result["mcp_metadata"]["status"] == "incomplete"
        assert result["mcp_metadata"]["result_file"] is None
        assert "MagicMock" not in json.dumps(result)

    def test_all_query_results_included(self, tool_functions, tmp_path):
        fn = tool_functions["run_benchmark"]

        mock_result = MagicMock()
        mock_result.query_results = [
            {"query_id": f"Q{i}", "execution_time": 0.1 * i, "status": "success"} for i in range(1, 100)
        ]

        mock_instance = MagicMock()
        mock_instance.run_with_platform.return_value = mock_result

        mock_bm_class = MagicMock(return_value=mock_instance)

        tpcds_queries = [
            {"id": str(i), "ms": 100 + i, "status": "SUCCESS", "run_type": "measurement"} for i in range(1, 100)
        ]
        result_path = tmp_path / "result.json"
        write_v2_result_file(
            result_path,
            execution_id="mcp_test",
            timestamp="2026-01-01T00:00:00",
            benchmark_id="tpcds",
            benchmark_name="TPC-DS",
            total_queries=99,
            passed_queries=99,
            queries=tpcds_queries,
        )

        mock_exporter = MagicMock()
        mock_exporter.export_result.return_value = {"json": result_path}

        with (
            patch("benchbox.mcp.tools.benchmark._get_platform_adapter"),
            patch("benchbox.mcp.tools.benchmark.get_public_benchmark_class", return_value=mock_bm_class),
            patch("benchbox.mcp.tools.benchmark.ResultExporter", return_value=mock_exporter),
        ):
            result = fn(platform="duckdb", benchmark="tpcds", scale_factor=0.01)

        assert result["mcp_metadata"]["status"] == "completed"
        assert len(result["queries"]) == 99
        assert result["queries"][0]["id"] == "1"
        assert result["queries"][98]["id"] == "99"

    def test_dataframe_platform_uses_dataframe_execution_path(self, tool_functions, tmp_path):
        fn = tool_functions["run_benchmark"]
        from benchbox.core.runner.runner import LifecyclePhases

        mock_df_result = MagicMock()
        mock_df_result.query_results = [
            {"query_id": f"Q{i}", "execution_time": 0.1, "status": "SUCCESS"} for i in range(1, 23)
        ]

        polars_queries = [
            {"id": str(i), "ms": 100 + i, "status": "SUCCESS", "run_type": "measurement"} for i in range(1, 23)
        ]
        result_path = tmp_path / "result.json"
        write_v2_result_file(
            result_path,
            execution_id="mcp_test",
            timestamp="2026-01-01T00:00:00",
            platform="polars-df",
            queries=polars_queries,
        )

        mock_exporter = MagicMock()
        mock_exporter.export_result.return_value = {"json": result_path}

        def execute_run(**kwargs):
            kwargs["adapter_factory"](
                execution_mode="dataframe",
                output_root=tmp_path,
                phases=LifecyclePhases(load=True, execute=True),
            )
            return mock_df_result

        with (
            patch("benchbox.platforms.get_adapter", return_value=MagicMock()) as get_adapter,
            patch("benchbox.mcp.tools.benchmark.get_public_benchmark_class", return_value=MagicMock),
            patch("benchbox.mcp.tools.benchmark.ResultExporter", return_value=mock_exporter),
            patch("benchbox.core.run_service.execute_run", side_effect=execute_run),
        ):
            result = fn(platform="polars-df", benchmark="tpch", scale_factor=0.01)

        assert get_adapter.call_args.kwargs["mode"] == "dataframe"

        assert result["mcp_metadata"]["status"] == "completed"
        assert result["platform"]["name"] == "polars-df"
        assert result["summary"]["queries"]["total"] == 22


class TestGetResultsImpl:
    def test_missing_file_returns_not_found(self):
        from benchbox.mcp.tools.results import _get_results_impl

        result = _get_results_impl("missing_file.json", results_dir=Path("/nonexistent/path"))

        assert result["error"] is True
        assert result["error_code"] == "RESOURCE_NOT_FOUND"

    def test_valid_json_file_returns_data(self, tmp_path):
        from benchbox.mcp.tools.results import _get_results_impl

        result_file = tmp_path / "test_run.json"
        write_v2_result_file(
            result_file,
            execution_id="test_run",
            timestamp="2026-01-01T00:00:00",
            total_queries=2,
            passed_queries=2,
            total_ms=5.0,
        )

        result = _get_results_impl("test_run.json", results_dir=tmp_path)

        assert "error" not in result
        assert result["benchmark"]["id"] == "tpch"
        assert result["benchmark"]["scale_factor"] == 0.01
        assert result["summary"]["timing"]["total_ms"] == 5.0

    def test_invalid_json_returns_format_error(self, tmp_path):
        from benchbox.mcp.tools.results import _get_results_impl

        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not valid json {{{")

        result = _get_results_impl("bad.json", results_dir=tmp_path)

        assert result["error"] is True
        assert result["error_code"] == "RESOURCE_INVALID_FORMAT"

    def test_includes_query_results_when_requested(self, tmp_path):
        from benchbox.mcp.tools.results import _get_results_impl

        query_list = [
            {"id": "1", "ms": 100, "status": "SUCCESS", "run_type": "measurement"},
            {"id": "6", "ms": 50, "status": "SUCCESS", "run_type": "measurement"},
        ]
        result_file = tmp_path / "with_queries.json"
        write_v2_result_file(
            result_file,
            execution_id="test_run",
            timestamp="2026-01-01T00:00:00",
            total_duration_ms=150,
            query_time_ms=150,
            total_queries=2,
            passed_queries=2,
            total_ms=150,
            queries=query_list,
        )

        result = _get_results_impl("with_queries.json", include_queries=True, results_dir=tmp_path)

        assert len(result["queries"]) == 2
        assert result["queries"][0]["id"] == "1"

    def test_auto_appends_json_extension(self, tmp_path):
        from benchbox.mcp.tools.results import _get_results_impl

        result_file = tmp_path / "run.json"
        write_v2_result_file(
            result_file,
            execution_id="test_run",
            timestamp="2026-01-01T00:00:00",
            total_duration_ms=0,
            query_time_ms=0,
            total_queries=0,
            passed_queries=0,
            total_ms=0,
        )

        result = _get_results_impl("run", results_dir=tmp_path)

        assert "error" not in result


class TestCheckDependenciesTool:
    def test_returns_platform_status(self, tool_functions):
        fn = tool_functions["check_dependencies"]
        result = fn()

        assert isinstance(result, dict)

    def test_specific_platform_check(self, tool_functions):
        fn = tool_functions["check_dependencies"]
        result = fn(platform="duckdb")

        assert isinstance(result, dict)


class TestSystemProfileTool:
    def test_returns_system_info(self, tool_functions):
        fn = tool_functions["system_profile"]
        result = fn()

        assert isinstance(result, dict)
        assert len(result) > 0


class TestModeParameterValidation:
    def test_validate_config_with_valid_sql_mode(self, tool_functions):
        fn = tool_functions["run_benchmark"]
        result = fn(platform="duckdb", benchmark="tpch", scale_factor=1.0, mode="sql", validate_only=True)

        assert result["valid"] is True
        assert result["execution_mode"] == "sql"

    def test_validate_config_with_valid_dataframe_mode(self, tool_functions):
        fn = tool_functions["run_benchmark"]
        result = fn(platform="polars", benchmark="tpch", scale_factor=1.0, mode="dataframe", validate_only=True)

        assert result["valid"] is True
        assert result["execution_mode"] == "dataframe"

    def test_validate_config_with_invalid_mode_value(self, tool_functions):
        fn = tool_functions["run_benchmark"]
        result = fn(platform="duckdb", benchmark="tpch", scale_factor=1.0, mode="invalid", validate_only=True)

        assert result["valid"] is False
        assert any("Invalid mode" in e for e in result["errors"])

    def test_validate_config_unsupported_mode_for_platform(self, tool_functions):
        fn = tool_functions["run_benchmark"]
        result = fn(platform="sqlite", benchmark="tpch", scale_factor=1.0, mode="dataframe", validate_only=True)

        assert result["valid"] is False
        assert any("doesn't support dataframe mode" in e for e in result["errors"])

    def test_validate_config_default_mode_when_not_specified(self, tool_functions):
        fn = tool_functions["run_benchmark"]
        result = fn(platform="duckdb", benchmark="tpch", scale_factor=1.0, validate_only=True)

        assert result["execution_mode"] == "sql"

    def test_validate_config_returns_execution_mode(self, tool_functions):
        fn = tool_functions["run_benchmark"]
        result = fn(platform="duckdb", benchmark="tpch", scale_factor=1.0, validate_only=True)

        assert "execution_mode" in result

    def test_run_benchmark_rejects_unsupported_mode(self, tool_functions):
        fn = tool_functions["run_benchmark"]
        result = fn(platform="sqlite", benchmark="tpch", scale_factor=0.01, mode="dataframe")

        assert result.get("status") == "failed"
        assert result.get("error_code") == "VALIDATION_UNSUPPORTED_MODE"
        assert "execution_id" in result

    def test_run_benchmark_accepts_valid_mode(self, tool_functions, tmp_path):
        fn = tool_functions["run_benchmark"]

        mock_result = MagicMock()
        mock_result.query_results = []

        mock_instance = MagicMock()
        mock_instance.run_with_platform.return_value = mock_result

        mock_bm_class = MagicMock(return_value=mock_instance)

        result_path = tmp_path / "result.json"
        write_v2_result_file(result_path, execution_id="mcp_test", timestamp="2026-01-01T00:00:00")

        mock_exporter = MagicMock()
        mock_exporter.export_result.return_value = {"json": result_path}

        with (
            patch("benchbox.mcp.tools.benchmark._get_platform_adapter"),
            patch("benchbox.mcp.tools.benchmark.get_public_benchmark_class", return_value=mock_bm_class),
            patch("benchbox.mcp.tools.benchmark.ResultExporter", return_value=mock_exporter),
        ):
            result = fn(platform="duckdb", benchmark="tpch", scale_factor=0.01, mode="sql")

        assert result["mcp_metadata"]["status"] == "completed"
        assert result["mcp_metadata"]["execution_mode"] == "sql"

    def test_dry_run_with_mode_parameter(self, tool_functions):
        fn = tool_functions["run_benchmark"]
        result = fn(platform="duckdb", benchmark="tpch", scale_factor=0.01, mode="sql", dry_run=True)

        assert result["status"] == "dry_run"
        assert result["execution_mode"] == "sql"

    def test_dry_run_rejects_unsupported_mode(self, tool_functions):
        fn = tool_functions["run_benchmark"]
        result = fn(platform="sqlite", benchmark="tpch", scale_factor=0.01, mode="dataframe", dry_run=True)

        assert result.get("status") == "error"
        assert result.get("error_code") == "VALIDATION_UNSUPPORTED_MODE"

    def test_mode_case_insensitive(self, tool_functions):
        fn = tool_functions["run_benchmark"]
        result = fn(platform="duckdb", benchmark="tpch", scale_factor=1.0, mode="SQL", validate_only=True)

        assert result["valid"] is True
        assert result["execution_mode"] == "sql"

    def test_dual_mode_platform_accepts_both_modes(self, tool_functions):
        fn = tool_functions["run_benchmark"]

        result_sql = fn(platform="datafusion", benchmark="tpch", scale_factor=1.0, mode="sql", validate_only=True)
        assert result_sql["valid"] is True
        assert result_sql["execution_mode"] == "sql"

        result_df = fn(platform="datafusion", benchmark="tpch", scale_factor=1.0, mode="dataframe", validate_only=True)
        assert result_df["valid"] is True
        assert result_df["execution_mode"] == "dataframe"

    def test_validate_config_accepts_data_only_mode(self, tool_functions):
        fn = tool_functions["run_benchmark"]
        result = fn(platform="duckdb", benchmark="tpch", scale_factor=1.0, mode="data_only", validate_only=True)

        assert result["valid"] is True
        assert result["execution_mode"] == "data_only"

    def test_validate_config_accepts_datagen_alias(self, tool_functions):
        fn = tool_functions["run_benchmark"]
        result = fn(platform="duckdb", benchmark="tpch", scale_factor=1.0, mode="datagen", validate_only=True)

        assert result["valid"] is True
        assert result["execution_mode"] == "data_only"

    def test_run_benchmark_data_only_mode(self, tool_functions, tmp_path):
        fn = tool_functions["run_benchmark"]

        mock_bm = MagicMock()
        mock_bm_class = MagicMock(return_value=mock_bm)

        with (
            patch("benchbox.mcp.tools.benchmark.get_public_benchmark_class", return_value=mock_bm_class),
            patch("benchbox.core.run_service.execute_run", return_value=MagicMock()) as execute_run,
        ):
            result = fn(platform="duckdb", benchmark="tpch", scale_factor=0.01, mode="data_only")

        assert result["mcp_metadata"]["status"] == "completed"
        assert result["mcp_metadata"]["execution_mode"] == "data_only"
        assert "data_generation" in result
        assert result["data_generation"]["benchmark"] == "tpch"
        assert result["data_generation"]["scale_factor"] == 0.01
        assert execute_run.call_args.kwargs["database_config"] is None
        assert execute_run.call_args.kwargs["phases_to_run"] == ["generate"]
        assert execute_run.call_args.kwargs["config"].test_execution_type == "data_only"

    def test_dry_run_accepts_data_only_mode(self, tool_functions):
        fn = tool_functions["run_benchmark"]
        result = fn(platform="duckdb", benchmark="tpch", scale_factor=0.01, mode="data_only", dry_run=True)

        assert result["status"] == "dry_run"
        assert result["execution_mode"] == "data_only"


class TestPhasesMapping:
    def test_power_phase_maps_to_power_type(self):
        from benchbox.core.run_service import map_phases_to_execution_type as _map_phases_to_test_execution_type

        assert _map_phases_to_test_execution_type(["power"]) == "power"
        assert _map_phases_to_test_execution_type(["load", "power"]) == "power"
        assert _map_phases_to_test_execution_type(["warmup", "power"]) == "power"

    def test_throughput_phase_maps_to_throughput_type(self):
        from benchbox.core.run_service import map_phases_to_execution_type as _map_phases_to_test_execution_type

        assert _map_phases_to_test_execution_type(["throughput"]) == "throughput"
        assert _map_phases_to_test_execution_type(["load", "throughput"]) == "throughput"

    def test_combined_phases_map_to_combined_type(self):
        from benchbox.core.run_service import map_phases_to_execution_type as _map_phases_to_test_execution_type

        assert _map_phases_to_test_execution_type(["power", "throughput", "maintenance"]) == "combined"

    def test_load_only_phase_maps_to_load_only_type(self):
        from benchbox.core.run_service import map_phases_to_execution_type as _map_phases_to_test_execution_type

        assert _map_phases_to_test_execution_type(["load"]) == "load_only"

    def test_generate_only_phase_maps_to_data_only_type(self):
        from benchbox.core.run_service import map_phases_to_execution_type as _map_phases_to_test_execution_type

        assert _map_phases_to_test_execution_type(["generate"]) == "data_only"

    def test_empty_phases_maps_to_standard(self):
        from benchbox.core.run_service import map_phases_to_execution_type as _map_phases_to_test_execution_type

        assert _map_phases_to_test_execution_type([]) == "standard"
        assert _map_phases_to_test_execution_type(["warmup"]) == "standard"

    def test_run_benchmark_passes_test_execution_type(self, tool_functions, tmp_path):
        fn = tool_functions["run_benchmark"]

        mock_result = MagicMock()
        mock_result.query_results = []

        result_path = tmp_path / "result.json"
        write_v2_result_file(result_path, execution_id="test", timestamp="2026-01-01T00:00:00")

        mock_exporter = MagicMock()
        mock_exporter.export_result.return_value = {"json": result_path}

        with (
            patch("benchbox.mcp.tools.benchmark._get_platform_adapter"),
            patch("benchbox.mcp.tools.benchmark.get_public_benchmark_class", return_value=MagicMock),
            patch("benchbox.mcp.tools.benchmark.ResultExporter", return_value=mock_exporter),
            patch("benchbox.core.run_service.execute_run", return_value=mock_result) as execute_run,
        ):
            fn(platform="duckdb", benchmark="tpch", scale_factor=0.01, phases="power")

        assert execute_run.call_args.kwargs["config"].test_execution_type == "power"

    def test_run_benchmark_passes_mode_to_adapter(self, tool_functions, tmp_path):
        fn = tool_functions["run_benchmark"]
        from benchbox.core.runner.runner import LifecyclePhases

        mock_result = MagicMock()
        mock_result.query_results = []

        result_path = tmp_path / "result.json"
        write_v2_result_file(
            result_path,
            execution_id="test",
            timestamp="2026-01-01T00:00:00",
            platform="datafusion",
        )

        mock_exporter = MagicMock()
        mock_exporter.export_result.return_value = {"json": result_path}

        mock_get_adapter = MagicMock()

        def execute_run(**kwargs):
            kwargs["adapter_factory"](
                execution_mode="dataframe",
                output_root=tmp_path,
                phases=LifecyclePhases(load=True, execute=True),
            )
            return mock_result

        with (
            patch("benchbox.platforms.get_adapter", mock_get_adapter),
            patch("benchbox.mcp.tools.benchmark.get_public_benchmark_class", return_value=MagicMock),
            patch("benchbox.mcp.tools.benchmark.ResultExporter", return_value=mock_exporter),
            patch("benchbox.core.run_service.execute_run", side_effect=execute_run),
        ):
            fn(platform="datafusion", benchmark="tpch", scale_factor=0.01, mode="dataframe")

        mock_get_adapter.assert_called_once()
        call_kwargs = mock_get_adapter.call_args[1]
        assert call_kwargs.get("mode") == "dataframe"
