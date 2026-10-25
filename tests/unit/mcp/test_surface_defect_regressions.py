from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

pytest.importorskip("mcp", reason="MCP SDK not installed. Install with: uv add benchbox --extra mcp")


class TestValidateResultsNotFoundError:
    def test_not_found_names_a_resource_type_not_the_filename(self, tmp_path: Path):
        from benchbox.mcp import create_server
        from tests.unit.mcp.public_api import call_tool

        server = create_server(results_dir=tmp_path, charts_dir=tmp_path)
        response = call_tool(server, "validate_results", result_file="myrun.json")

        assert response["error"] is True
        assert response["details"]["resource_type"] == "result_file"
        assert response["details"]["requested"] == "myrun.json"
        assert response["message"] == "Result_file 'myrun.json' not found"
        assert response["suggestion"] == 'Use get_results(format="list") to see available result files'

    def test_not_found_does_not_leak_the_server_results_root(self, tmp_path: Path):
        from benchbox.mcp import create_server
        from tests.unit.mcp.public_api import call_tool

        server = create_server(results_dir=tmp_path, charts_dir=tmp_path)
        response = call_tool(server, "validate_results", result_file="myrun.json")

        assert str(tmp_path) not in repr(response)


class TestQueryDetailsVisibilityMatrix:
    def test_public_benchmark_details_carry_support_status(self):
        from benchbox.core.benchmark_registry import get_all_benchmarks
        from benchbox.mcp.tools.benchmark import _build_query_details_benchmark_info

        meta = get_all_benchmarks()["tpch"]
        info = _build_query_details_benchmark_info("tpch", meta)

        assert info["support_status"] == meta["support_status"]

    def test_internal_benchmark_details_omit_support_status(self):
        from benchbox.core.benchmark_registry import get_all_benchmarks, get_benchmark_surface
        from benchbox.mcp.tools.benchmark import _build_query_details_benchmark_info

        benchmarks = get_all_benchmarks()
        internal = sorted(b for b in benchmarks if get_benchmark_surface(b) != "public")
        assert internal, "the matrix row is vacuous without at least one internal benchmark"

        for benchmark in internal:
            info = _build_query_details_benchmark_info(benchmark, benchmarks[benchmark])
            assert set(info) == {"display_name", "category"}, benchmark

    def test_internal_benchmarks_stay_addressable_by_explicit_id(self, tmp_path: Path):
        from benchbox.core.benchmark_registry import get_all_benchmarks, get_benchmark_surface
        from benchbox.mcp import create_server
        from tests.unit.mcp.public_api import call_tool

        internal = sorted(b for b in get_all_benchmarks() if get_benchmark_surface(b) != "public")
        server = create_server(results_dir=tmp_path, charts_dir=tmp_path)

        for benchmark in internal:
            response = call_tool(server, "get_query_details", benchmark=benchmark, query_id="1")
            assert response.get("details", {}).get("resource_type") != "benchmark", benchmark

    def test_every_registered_benchmark_matches_its_matrix_row(self):
        from benchbox.core.benchmark_registry import get_all_benchmarks, get_benchmark_surface
        from benchbox.mcp.tools.benchmark import _build_query_details_benchmark_info

        for benchmark, meta in get_all_benchmarks().items():
            info = _build_query_details_benchmark_info(benchmark, meta)
            expected_public = get_benchmark_surface(benchmark) == "public"
            assert ("support_status" in info) is expected_public, benchmark


class TestRemoteModeAnonymization:
    @staticmethod
    def _security_runtime(tmp_path: Path):
        from benchbox.mcp.security import RemoteSecurityRuntime
        from tests.integration.mcp._security import write_security_config

        config = write_security_config(
            tmp_path,
            tokens={"token": ("tenant", ("benchbox:read", "benchbox:execute"))},
        )
        return RemoteSecurityRuntime.from_file(config)

    def test_local_stdio_server_does_not_anonymize(self, tmp_path: Path):
        import benchbox.mcp.server as server_module

        with (
            patch.object(server_module, "register_benchmark_tools") as benchmark_tools,
            patch.object(server_module, "register_analytics_tools") as analytics_tools,
        ):
            server_module.create_benchbox_server(results_dir=tmp_path, charts_dir=tmp_path)

        assert benchmark_tools.call_args.kwargs["anonymize_results"] is False
        assert analytics_tools.call_args.kwargs["anonymize_results"] is False

    def test_remote_authenticated_server_anonymizes(self, tmp_path: Path):
        import benchbox.mcp.server as server_module

        with (
            patch.object(server_module, "register_benchmark_tools") as benchmark_tools,
            patch.object(server_module, "register_analytics_tools") as analytics_tools,
        ):
            server_module.create_benchbox_server(
                results_dir=tmp_path,
                charts_dir=tmp_path,
                remote_security=self._security_runtime(tmp_path),
            )

        assert benchmark_tools.call_args.kwargs["anonymize_results"] is True
        assert analytics_tools.call_args.kwargs["anonymize_results"] is True

    def test_durable_job_execution_anonymizes(self, tmp_path: Path):
        from unittest.mock import MagicMock

        from benchbox.mcp.jobs import DurableJobWorker, JobRecord

        record = JobRecord(
            execution_id="mcp_job_test",
            principal_id="p",
            state="running",
            request={"platform": "duckdb", "benchmark": "tpch"},
            idempotency_key=None,
            attempts=1,
            lease_owner="w",
            lease_expires_at=None,
            lease_version=1,
            lease_generation=0,
            cancel_requested=False,
            artifact_path=None,
            error_code=None,
            created_at="2026-08-04T00:00:00Z",
            updated_at="2026-08-04T00:00:00Z",
            completed_at=None,
        )

        with (
            patch("benchbox.mcp.jobs._execute_mcp_run_via_core", return_value={}) as run_core,
            patch(
                "benchbox.core.benchmark_registry.get_public_benchmark_class",
                return_value=MagicMock,
            ),
            patch("benchbox.mcp.jobs.get_all_benchmarks", return_value={"tpch": {"display_name": "TPC-H"}}),
        ):
            DurableJobWorker._execute_benchmark(record, tmp_path)

        assert run_core.call_args.kwargs["anonymize"] is True

    def test_compare_results_threads_the_anonymization_decision(self, tmp_path: Path):
        from benchbox.mcp.tools import analytics as analytics_module

        (tmp_path / "a.json").write_text('{"platform": {"name": "duckdb"}, "benchmark": {"id": "tpch"}}')
        (tmp_path / "b.json").write_text('{"platform": {"name": "duckdb"}, "benchmark": {"id": "tpch"}}')
        with patch("benchbox.core.results.analytics.compare_results") as core_compare:
            core_compare.return_value = {"status": "success", "query_comparisons": []}
            analytics_module._compare_results_impl("a.json", "b.json", 10.0, tmp_path, anonymize=True)

        assert core_compare.call_args.kwargs["anonymize"] is True

    @pytest.mark.parametrize(
        ("module_path", "function_name"),
        [
            ("benchbox.mcp.tools.benchmark", "_run_benchmark_impl"),
            ("benchbox.mcp.tools.benchmark", "_export_and_build_payload"),
            ("benchbox.mcp.tools.analytics", "_compare_results_impl"),
        ],
    )
    def test_anonymize_has_no_permissive_default(self, module_path: str, function_name: str):
        import importlib
        import inspect

        parameter = inspect.signature(getattr(importlib.import_module(module_path), function_name)).parameters[
            "anonymize"
        ]

        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty


class TestChartOutputContract:
    def test_renderer_is_constructed_without_color(self):
        from benchbox.mcp.tools import visualization as visualization_module

        source = inspect.getsource(visualization_module._generate_ascii_chart)

        assert "ChartOptions(use_color=False)" in source

    def test_generate_chart_description_matches_the_renderer(self, tmp_path: Path):
        from benchbox.mcp import create_server
        from tests.unit.mcp.public_api import list_tools_by_name

        server = create_server(results_dir=tmp_path, charts_dir=tmp_path)
        description = list_tools_by_name(server)["generate_chart"].description

        assert description is not None
        assert "Generate ASCII chart output" in description
        assert "non-ASCII formats are rejected" in description
        assert "ANSI colors for terminal display" not in description
