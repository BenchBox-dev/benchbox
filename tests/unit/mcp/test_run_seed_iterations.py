from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from mcp.shared.exceptions import MCPError

from benchbox.core.constants import GENERIC_POWER_DEFAULT_MEASUREMENT_ITERATIONS
from benchbox.mcp.schemas import MAX_SEED, MCPValidationError, validate_iterations, validate_seed
from benchbox.mcp.security import (
    DurableSecurityStore,
    Principal,
    RemoteSecurityMiddleware,
    load_remote_security_config,
)
from tests.integration.mcp._security import write_security_config

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

pytest.importorskip("mcp", reason="MCP SDK not installed. Install with: uv add benchbox --extra mcp")


class TestSeedAndIterationsValidation:
    @pytest.mark.parametrize("seed", [None, 0, 42, MAX_SEED])
    def test_valid_seed_is_returned_unchanged(self, seed):
        assert validate_seed(seed) == seed

    @pytest.mark.parametrize("seed", [-1, MAX_SEED + 1, True, 1.5, "7"])
    def test_invalid_seed_is_rejected(self, seed):
        with pytest.raises(MCPValidationError):
            validate_seed(seed)

    @pytest.mark.parametrize("iterations", [None, 1, 3, 50])
    def test_valid_iterations_are_returned_unchanged(self, iterations):
        assert validate_iterations(iterations) == iterations

    @pytest.mark.parametrize("iterations", [0, -2, True, 2.5, "3"])
    def test_invalid_iterations_are_rejected(self, iterations):
        with pytest.raises(MCPValidationError):
            validate_iterations(iterations)


class TestRunBenchmarkSeedAndIterations:
    @pytest.mark.parametrize(
        "arguments",
        [
            {"seed": -1},
            {"seed": MAX_SEED + 1},
            {"iterations": 0},
            {"iterations": -3},
        ],
    )
    @pytest.mark.parametrize("mode", [{}, {"validate_only": True}, {"dry_run": True}])
    def test_invalid_values_are_rejected_before_any_work(self, tmp_path: Path, arguments, mode):
        from benchbox.mcp import create_server
        from benchbox.mcp.tools import benchmark as benchmark_tools
        from tests.unit.mcp.public_api import call_tool

        server = create_server(results_dir=tmp_path, charts_dir=tmp_path)
        with (
            patch.object(benchmark_tools, "_run_benchmark_impl") as run_impl,
            patch.object(benchmark_tools, "_dry_run_impl") as dry_run_impl,
            patch.object(benchmark_tools, "_validate_config_impl") as validate_impl,
        ):
            response = call_tool(
                server, "run_benchmark", platform="duckdb", benchmark="tpch", scale_factor=0.01, **arguments, **mode
            )

        assert response["error"] is True
        assert response["status"] == "failed"
        assert response["error_code"] == "VALIDATION_ERROR"
        run_impl.assert_not_called()
        dry_run_impl.assert_not_called()
        validate_impl.assert_not_called()

    def test_values_reach_the_shared_run_path(self, tmp_path: Path):
        from benchbox.mcp import create_server
        from benchbox.mcp.tools import benchmark as benchmark_tools
        from tests.unit.mcp.public_api import call_tool

        server = create_server(results_dir=tmp_path, charts_dir=tmp_path)
        with patch.object(benchmark_tools, "_run_benchmark_impl", return_value={"status": "completed"}) as run_impl:
            call_tool(
                server,
                "run_benchmark",
                platform="duckdb",
                benchmark="tpch",
                scale_factor=0.01,
                seed=11,
                iterations=2,
            )
            call_tool(server, "run_benchmark", platform="duckdb", benchmark="tpch", scale_factor=0.01)

        with_values, without_values = run_impl.call_args_list
        assert (with_values.kwargs["seed"], with_values.kwargs["iterations"]) == (11, 2)
        assert (without_values.kwargs["seed"], without_values.kwargs["iterations"]) == (None, None)


def _run_core(tmp_path: Path, *, seed, iterations, phases=("load", "power"), benchmark_class=None, payload=None):
    from benchbox.mcp.tools import benchmark as benchmark_tools

    datagen_root = tmp_path / "benchmark_runs" / "datagen" / "tpch_sf001"
    benchmark_instance = SimpleNamespace(output_dir=datagen_root)
    benchmark_class = benchmark_class or Mock(return_value=benchmark_instance)

    with (
        patch.object(benchmark_tools, "get_all_benchmarks", return_value={"tpch": {"display_name": "TPC-H"}}),
        patch("benchbox.core.system.SystemProfiler") as profiler_cls,
        patch.object(benchmark_tools, "_export_and_build_payload", return_value=("result.json", payload or {})),
        patch.object(benchmark_tools, "_attach_summary_charts"),
        patch("benchbox.core.platform_config.get_platform_config", return_value={}),
        patch("benchbox.core.run_service.execute_run", return_value=SimpleNamespace()) as execute,
    ):
        profiler_cls.return_value.get_system_profile.return_value = None
        response = benchmark_tools._execute_mcp_run_via_core(
            platform="duckdb",
            benchmark="tpch",
            benchmark_class=benchmark_class,
            scale_factor=0.01,
            queries=None,
            phases=list(phases),
            resolved_mode="sql",
            capture_plans=False,
            seed=seed,
            iterations=iterations,
            normalized_platform_options={},
            results_dir=tmp_path / "results",
            execution_id="exec-1",
            start_time=100.0,
            anonymize=False,
        )
    return response, execute.call_args.kwargs, benchmark_class


class TestSharedRunnerReceivesSeedAndIterations:
    def test_values_land_where_the_cli_puts_them(self, tmp_path: Path):
        response, kwargs, _ = _run_core(tmp_path, seed=42, iterations=5, payload={"run": {"iterations": 5}})

        assert kwargs["config"].options["seed"] == 42
        assert kwargs["config"].options["power_iterations"] == 5
        assert kwargs["execution_context"].seed == 42
        assert response["mcp_metadata"]["seed"] == 42
        assert response["mcp_metadata"]["iterations"] == 5

    def test_omitted_values_leave_the_cli_default_configuration(self, tmp_path: Path):
        response, kwargs, _ = _run_core(tmp_path, seed=None, iterations=None, payload={"run": {"iterations": 1}})

        assert "seed" not in kwargs["config"].options
        assert "power_iterations" not in kwargs["config"].options
        assert kwargs["execution_context"].seed is None
        assert response["mcp_metadata"]["seed"] is None
        assert response["mcp_metadata"]["iterations"] == 1
        assert "warnings" not in response

    def test_iterations_without_a_power_phase_is_reported(self, tmp_path: Path):
        response, _, _ = _run_core(tmp_path, seed=None, iterations=4, phases=("load",))

        assert any("iterations has no effect" in warning for warning in response["warnings"])

    def test_seed_reaches_benchmarks_that_register_a_seed_option(self, tmp_path: Path):
        registered = {"seed": object()}
        with patch(
            "benchbox.mcp.tools.benchmark.BenchmarkHookRegistry.list_option_specs", return_value=registered
        ) as specs:
            _, _, benchmark_class = _run_core(tmp_path, seed=9, iterations=None)

        specs.assert_called_with("tpch")
        assert benchmark_class.call_args.kwargs["seed"] == 9

    def test_seed_is_not_forced_on_benchmarks_without_a_seed_option(self, tmp_path: Path):
        with patch("benchbox.mcp.tools.benchmark.BenchmarkHookRegistry.list_option_specs", return_value={}):
            _, _, benchmark_class = _run_core(tmp_path, seed=9, iterations=None)

        assert "seed" not in benchmark_class.call_args.kwargs


class TestRemoteIterationBound:
    def _middleware(self, tmp_path: Path, **overrides):
        path = write_security_config(tmp_path, tokens={"token": ("tenant-a", ("benchbox:read", "benchbox:execute"))})
        if overrides:
            import json

            policy = json.loads(path.read_text(encoding="utf-8"))
            policy.update(overrides)
            path.write_text(json.dumps(policy), encoding="utf-8")
        config = load_remote_security_config(path)
        principal = Principal("a" * 32, "client", "issuer", "a", frozenset({"benchbox:execute"}))
        return (
            config,
            RemoteSecurityMiddleware(config, DurableSecurityStore(config.state_db, config.admission)),
            principal,
        )

    @pytest.mark.parametrize("tool", ["run_benchmark", "start_benchmark"])
    def test_iterations_above_policy_are_denied(self, tmp_path: Path, tool: str):
        config, middleware, principal = self._middleware(tmp_path)
        base = {"platform": "duckdb", "benchmark": "tpch", "scale_factor": 0.01}

        middleware._authorize(principal, tool, {**base, "iterations": config.max_iterations})
        middleware._authorize(principal, tool, base)
        with pytest.raises(MCPError, match="iterations exceed policy"):
            middleware._authorize(principal, tool, {**base, "iterations": config.max_iterations + 1})

    def test_policy_value_is_configurable(self, tmp_path: Path):
        config, middleware, principal = self._middleware(tmp_path, max_iterations=4)
        base = {"platform": "duckdb", "benchmark": "tpch", "scale_factor": 0.01}

        assert config.max_iterations == 4
        middleware._authorize(principal, "start_benchmark", {**base, "iterations": 4})
        with pytest.raises(MCPError, match="iterations exceed policy"):
            middleware._authorize(principal, "start_benchmark", {**base, "iterations": 5})

    def test_policy_cannot_be_set_below_the_default_iteration_count(self, tmp_path: Path):
        with pytest.raises(ValueError, match="max_iterations"):
            self._middleware(tmp_path, max_iterations=GENERIC_POWER_DEFAULT_MEASUREMENT_ITERATIONS - 1)

    @pytest.mark.parametrize("value", [1e6, "1000000", 2.5, True, [3]])
    @pytest.mark.parametrize("tool", ["run_benchmark", "start_benchmark"])
    def test_non_integer_iterations_are_denied_at_admission(self, tmp_path: Path, tool: str, value):
        _, middleware, principal = self._middleware(tmp_path)
        base = {"platform": "duckdb", "benchmark": "tpch", "scale_factor": 0.01}

        with pytest.raises(MCPError, match="iterations must be an integer"):
            middleware._authorize(principal, tool, {**base, "iterations": value})

    def test_default_policy_allows_the_cli_default(self, tmp_path: Path):
        config, _, _ = self._middleware(tmp_path)

        assert config.max_iterations >= GENERIC_POWER_DEFAULT_MEASUREMENT_ITERATIONS
