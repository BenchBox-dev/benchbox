from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from benchbox.core.run_service import (
    map_phases_to_execution_type,
    resolve_lifecycle_phases,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.mark.parametrize(
    "phases, expected_type, expected_execute",
    [
        (["load", "power"], "power", True),
        (["power"], "power", True),
        (["throughput"], "throughput", True),
        (["power", "throughput", "maintenance"], "combined", True),
        (["load"], "load_only", False),
        (["generate"], "data_only", False),
        ([], "standard", True),
        (["warmup"], "standard", True),
    ],
)
def test_execution_type_and_lifecycle_resolution_agree(phases, expected_type, expected_execute):
    execution_type = map_phases_to_execution_type(phases)
    lifecycle = resolve_lifecycle_phases(phases if phases else None)

    assert execution_type == expected_type
    assert lifecycle.execute is expected_execute
    if expected_type == "load_only":
        assert lifecycle.load is True and lifecycle.execute is False
    elif expected_type == "data_only":
        assert lifecycle.generate is True


def test_runplan_parity_cli_mcp_run_config_resolution(tmp_path):
    from pathlib import Path

    from benchbox.cli.orchestrator import BenchmarkOrchestrator
    from benchbox.core.config import BenchmarkConfig
    from benchbox.core.run_service import SilentVerbosity, resolve_run_config

    cfg = BenchmarkConfig(
        name="tpch", display_name="TPC-H", scale_factor=0.01, queries=["1"], options={"seed": 5, "power_iterations": 2}
    )
    orch = BenchmarkOrchestrator(base_dir=str(tmp_path))

    class _DB:
        type = "duckdb"

    via_cli = orch._prepare_run_config(cfg, _DB())
    via_core = resolve_run_config(
        cfg,
        database_path=orch.directory_manager.get_database_path(
            cfg.name, cfg.scale_factor, "duckdb", tuning_config=None
        ),
        verbosity=SilentVerbosity(),
    )

    cli_dump = via_cli.model_dump()
    core_dump = via_core.model_dump()
    for k in ("quiet", "verbose", "verbose_level", "verbose_enabled", "very_verbose"):
        cli_dump.pop(k, None)
        core_dump.pop(k, None)
    assert cli_dump == core_dump
    assert via_core.quiet is True
    assert via_cli.quiet is False


def test_synchronous_mcp_surface_delegates_to_execute_run(tmp_path):
    from benchbox.mcp.tools import benchmark as benchmark_tools

    benchmark_instance = SimpleNamespace(output_dir=tmp_path / "datagen")
    core_result = SimpleNamespace(execution_context=None, query_results=[])
    with (
        patch.object(benchmark_tools, "get_all_benchmarks", return_value={"tpch": {"display_name": "TPC-H"}}),
        patch.object(benchmark_tools, "get_public_benchmark_class", return_value=Mock(return_value=benchmark_instance)),
        patch("benchbox.core.system.SystemProfiler") as profiler_cls,
        patch("benchbox.core.platform_config.get_platform_config", return_value={}),
        patch("benchbox.core.run_service.execute_run", return_value=core_result) as execute_run,
        patch.object(benchmark_tools, "_export_and_build_payload", return_value=("result.json", {})),
    ):
        profiler_cls.return_value.get_system_profile.return_value = None
        response = benchmark_tools._run_benchmark_impl(
            "duckdb",
            "tpch",
            0.01,
            None,
            "load,power",
            "sql",
            results_dir=tmp_path,
            anonymize=False,
        )

    assert response["mcp_metadata"]["status"] == "completed"
    request = execute_run.call_args.kwargs
    assert request["config"].test_execution_type == "power"
    assert request["database_config"].type == "duckdb"
    assert request["phases_to_run"] == ["load", "power"]
    assert request["output_root"] == benchmark_instance.output_dir


def test_durable_data_only_surface_delegates_to_execute_run(tmp_path):
    from benchbox.mcp.jobs import DurableJobWorker

    job = SimpleNamespace(
        execution_id="mcp_job_data_only",
        request={"platform": "duckdb", "benchmark": "tpch", "scale_factor": 0.01, "mode": "data_only"},
    )
    benchmark_instance = SimpleNamespace(output_dir=tmp_path / "ignored")
    core_result = SimpleNamespace(execution_context=None, query_results=[])
    with (
        patch(
            "benchbox.core.benchmark_registry.get_public_benchmark_class",
            return_value=Mock(return_value=benchmark_instance),
        ),
        patch("benchbox.core.system.SystemProfiler") as profiler_cls,
        patch("benchbox.core.run_service.execute_run", return_value=core_result) as execute_run,
    ):
        profiler_cls.return_value.get_system_profile.return_value = None
        response = DurableJobWorker._execute_benchmark(job, tmp_path)

    assert response["mcp_metadata"]["status"] == "completed"
    request = execute_run.call_args.kwargs
    assert request["config"].test_execution_type == "data_only"
    assert request["database_config"] is None
    assert request["phases_to_run"] == ["generate"]
    assert response["data_generation"]["data_path"].startswith(str(tmp_path / "datagen"))


@pytest.mark.parametrize(
    ("platform_options", "expected_source"),
    [({}, "baseline"), ({"databricks_clustering_strategy": "liquid_clustering_auto"}, None)],
)
def test_mcp_database_config_claims_baseline_only_when_no_tuning_is_applied(
    tmp_path, platform_options, expected_source
):
    from benchbox.mcp.tools import benchmark as benchmark_tools

    benchmark_instance = SimpleNamespace(output_dir=tmp_path / "datagen")
    with (
        patch.object(benchmark_tools, "get_all_benchmarks", return_value={"tpch": {"display_name": "TPC-H"}}),
        patch("benchbox.core.system.SystemProfiler") as profiler_cls,
        patch("benchbox.core.platform_config.get_platform_config", return_value={}),
        patch("benchbox.core.run_service.execute_run", return_value=None) as execute_run,
        patch.object(benchmark_tools, "_build_run_response", return_value={}),
    ):
        profiler_cls.return_value.get_system_profile.return_value = None
        benchmark_tools._execute_mcp_run_via_core(
            platform="databricks",
            benchmark="tpch",
            benchmark_class=Mock(return_value=benchmark_instance),
            scale_factor=0.01,
            queries=None,
            phases=["load", "power"],
            resolved_mode="sql",
            capture_plans=False,
            normalized_platform_options=platform_options,
            results_dir=tmp_path,
            execution_id="mcp_test",
            start_time=0.0,
            anonymize=False,
        )

    assert execute_run.call_args.kwargs["database_config"].tuning_source == expected_source
