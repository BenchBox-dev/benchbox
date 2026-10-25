from __future__ import annotations

import sys as _sys
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner
from rich.panel import Panel

from benchbox.cli.commands.run import run
from benchbox.cli.tuning_runtime import build_baseline_unified_config
from benchbox.core.schemas import BenchmarkConfig
from benchbox.core.tuning.interface import UnifiedTuningConfiguration

__import__("benchbox.cli.commands.run")
_run_module = _sys.modules["benchbox.cli.commands.run"]

__import__("benchbox.cli.benchmarks")
_benchmarks_module = _sys.modules["benchbox.cli.benchmarks"]

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _Config:
    config_path = "<test-config>"

    def get(self, _key: str, default=None):
        return default

    def validate_config(self) -> bool:
        return True


def _run_obj() -> dict[str, object]:
    return {"config": _Config()}


def _interactive_sys() -> SimpleNamespace:
    return SimpleNamespace(
        stdin=SimpleNamespace(isatty=lambda: True),
        stdout=SimpleNamespace(isatty=lambda: True),
    )


def _tuned_unified_config() -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    config.enable_all_constraints()
    return config


def _banner_state(*, platform: str | None, benchmark: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        iterations=None,
        phases_to_run=["power"],
        logger=None,
        platform=platform,
        benchmark=benchmark,
        quiet=False,
    )


def test_interactive_header_suppressed_for_explicit_run_args():
    state = _banner_state(platform="duckdb", benchmark="tpch")
    interactive_sys = SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True))

    with (
        patch.object(_run_module, "sys", interactive_sys),
        patch.object(_run_module, "console") as mock_console,
    ):
        _run_module._derive_exec_type_and_banner(state)

    mock_console.print.assert_not_called()


def test_interactive_header_shown_for_prompted_tty_run():
    state = _banner_state(platform=None, benchmark=None)
    interactive_sys = SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True))

    with (
        patch.object(_run_module, "sys", interactive_sys),
        patch.object(_run_module, "console") as mock_console,
    ):
        _run_module._derive_exec_type_and_banner(state)

    printed = [call.args[0] for call in mock_console.print.call_args_list]
    assert isinstance(printed[-1], Panel)
    assert sum(isinstance(item, Panel) for item in printed) == 1


def test_interactive_cloud_setup_leaves_server_credentials_to_adapter():
    state = SimpleNamespace(
        database_config=SimpleNamespace(type="postgresql"),
        non_interactive=False,
        output=None,
        ctx=Mock(),
    )

    with (
        patch.object(_run_module, "_run_stages_through_cloud_storage", return_value=False),
        patch.object(_run_module, "_run_requires_platform_credentials", return_value=True),
        patch.object(_run_module, "check_and_setup_platform_credentials") as check_credentials,
    ):
        _run_module._interactive_cloud_setup_if_needed(state)

    check_credentials.assert_not_called()
    state.ctx.exit.assert_not_called()


def test_interactive_cloud_setup_guides_singlestore_credentials():
    state = SimpleNamespace(
        database_config=SimpleNamespace(type="singlestore"),
        non_interactive=False,
        output=None,
        ctx=Mock(),
    )

    with (
        patch.object(_run_module, "_run_stages_through_cloud_storage", return_value=False),
        patch.object(_run_module, "_run_requires_platform_credentials", return_value=True),
        patch.object(_run_module, "check_and_setup_platform_credentials", return_value=True) as check_credentials,
    ):
        _run_module._interactive_cloud_setup_if_needed(state)

    check_credentials.assert_called_once_with(platform="singlestore", console_obj=_run_module.console, interactive=True)
    state.ctx.exit.assert_not_called()


def test_interactive_guided_flow_uses_prompted_values_and_saves_preferences(tmp_path: Path):
    runner = CliRunner()

    profiler = Mock()
    profiler.get_system_profile.return_value = SimpleNamespace(
        cpu_cores_logical=8,
        memory_total_gb=32,
        architecture="x86_64",
        os_type="darwin",
    )
    profiler.display_profile.return_value = None

    db_manager = Mock()
    database_config = SimpleNamespace(type="duckdb", options={}, execution_mode=None)
    db_manager.prompt_execution_style.return_value = "sql-local"
    db_manager.select_database.return_value = database_config

    benchmark_config = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=0.01,
        queries=None,
        concurrency=4,
        compress_data=True,
        compression_type="gzip",
        compression_level=6,
        options={},
    )
    bench_manager = Mock()
    bench_manager.benchmarks = {
        "tpch": {
            "display_name": "TPC-H",
            "estimated_time_range": (2, 10),
            "complexity": "medium",
            "num_queries": 22,
        }
    }
    bench_manager.select_benchmark.return_value = benchmark_config

    orchestrator = Mock()
    result_payload = SimpleNamespace(validation_status="PASSED", execution_id="exec-123", query_results=[])
    guided_config = _tuned_unified_config()

    with ExitStack() as stack:
        stack.enter_context(patch.object(_run_module, "sys", _interactive_sys()))
        stack.enter_context(patch.object(_run_module, "SystemProfiler", return_value=profiler))
        stack.enter_context(patch.object(_run_module, "DatabaseManager", return_value=db_manager))
        stack.enter_context(patch.object(_run_module, "BenchmarkManager", return_value=bench_manager))
        stack.enter_context(patch.object(_run_module, "BenchmarkOrchestrator", return_value=orchestrator))
        stack.enter_context(patch.object(_run_module, "display_system_recommendations"))
        mock_caps = stack.enter_context(patch.object(_run_module.PlatformRegistry, "get_platform_capabilities"))
        stack.enter_context(patch.object(_run_module.PlatformRegistry, "requires_cloud_storage", return_value=False))
        stack.enter_context(
            patch.object(_run_module, "normalize_output_root", return_value=str(tmp_path / "normalized"))
        )
        mock_execute = stack.enter_context(
            patch.object(_run_module, "_execute_orchestrated_run", return_value=result_payload)
        )
        stack.enter_context(
            patch.object(
                _run_module,
                "_export_orchestrated_result",
                return_value={"json": str(tmp_path / "results.json")},
            )
        )
        stack.enter_context(patch.object(_run_module, "_render_post_run_charts"))
        stack.enter_context(patch("benchbox.cli.onboarding.check_and_run_first_time_setup", return_value=False))
        stack.enter_context(patch("benchbox.cli.preferences.load_last_run_config", return_value=None))
        mock_save_last_run = stack.enter_context(patch("benchbox.cli.preferences.save_last_run_config"))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_table_mode", return_value="native"))
        stack.enter_context(
            patch.object(_benchmarks_module, "prompt_phases", return_value=["generate", "load", "power"])
        )
        stack.enter_context(patch.object(_benchmarks_module, "prompt_query_subset", return_value=["Q1", "Q6"]))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_official_mode", return_value=(False, None)))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_seed", return_value=77))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_force_regeneration", return_value="upload"))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_validation_mode", return_value="loose"))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_capture_plans", return_value=True))
        stack.enter_context(
            patch.object(_benchmarks_module, "prompt_output_location", return_value=str(tmp_path / "chosen-output"))
        )
        stack.enter_context(patch.object(_benchmarks_module, "prompt_table_format", return_value=("iceberg", "zstd")))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_verbose_output", return_value=2))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_platform_options", return_value={"threads": "8"}))
        stack.enter_context(patch("benchbox.cli.tuning.run_tuning_wizard", return_value=guided_config))
        mock_preview = stack.enter_context(patch("benchbox.cli.dryrun.display_interactive_preview"))
        mock_confirm = stack.enter_context(patch.object(_run_module.Confirm, "ask"))
        mock_caps.return_value = SimpleNamespace(
            default_mode="sql",
            supports_sql=True,
            supports_dataframe=False,
            deployment_modes={},
            default_deployment=None,
            execution_engines={},
        )

        def _confirm_side_effect(prompt, default=False):
            text = str(prompt)
            if "configure tuning options" in text:
                return True
            if "Proceed with execution?" in text:
                return True
            return default

        mock_confirm.side_effect = _confirm_side_effect

        result = runner.invoke(run, [], obj=_run_obj())

    assert result.exit_code == 0, result.output
    db_manager.prompt_execution_style.assert_called_once()
    db_manager.select_database.assert_called_once_with(style_filter="sql-local")
    assert benchmark_config.queries == ["Q1", "Q6"]
    assert benchmark_config.options["validation_mode"] == "loose"
    assert benchmark_config.options["seed"] == 77
    assert benchmark_config.options["table_format"] == "iceberg"
    assert benchmark_config.options["table_format_compression"] == "zstd"
    assert benchmark_config.options["unified_tuning_configuration"] is guided_config
    assert benchmark_config.options["tuning_enabled"] is True
    assert (
        benchmark_config.concurrency,
        benchmark_config.compress_data,
        benchmark_config.compression_type,
        benchmark_config.compression_level,
    ) == (4, True, "gzip", 6)
    assert database_config.options["tuning_enabled"] is True
    assert database_config.tuning_enabled is True
    assert database_config.tuning_source == "wizard"
    assert database_config.tuning_source_file is None
    mock_preview.assert_called_once()
    assert mock_preview.call_args.kwargs["benchmark_config"] is benchmark_config
    assert mock_preview.call_args.kwargs["benchmark_config"].concurrency == 4
    orchestrator.set_custom_output_dir.assert_called_once_with(str(tmp_path / "normalized"))
    assert mock_execute.call_args.args[1] is benchmark_config
    assert mock_execute.call_args.args[1].compression_type == "gzip"
    assert mock_execute.call_args.args[4] == ["generate", "load", "power"]
    assert mock_execute.call_args.kwargs["execution_context"].query_subset == ["Q1", "Q6"]
    mock_save_last_run.assert_called_once()
    save_kwargs = mock_save_last_run.call_args.kwargs
    assert save_kwargs["database"] == "duckdb"
    assert save_kwargs["benchmark"] == "tpch"
    assert save_kwargs["seed"] == 77
    assert save_kwargs["output"] == str(tmp_path / "chosen-output")
    assert save_kwargs["phases"] == ["generate", "load", "power"]
    assert save_kwargs["queries"] == ["Q1", "Q6"]
    assert save_kwargs["mode"] == "sql"
    assert save_kwargs["concurrency"] == 4
    assert (save_kwargs["compress_data"], save_kwargs["compression_type"], save_kwargs["compression_level"]) == (
        True,
        "gzip",
        6,
    )


def test_interactive_stats_controls_reach_benchmark_config(tmp_path: Path):
    runner = CliRunner()

    profiler = Mock()
    profiler.get_system_profile.return_value = SimpleNamespace(
        cpu_cores_logical=8,
        memory_total_gb=32,
        architecture="x86_64",
        os_type="darwin",
    )
    profiler.display_profile.return_value = None

    db_manager = Mock()
    database_config = SimpleNamespace(type="duckdb", options={}, execution_mode=None)
    db_manager.prompt_execution_style.return_value = "sql-local"
    db_manager.select_database.return_value = database_config

    benchmark_config = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=0.01,
        queries=None,
        concurrency=1,
        options={},
    )
    assert benchmark_config.stats_reset is None
    assert benchmark_config.stats_per_table_timing is False

    bench_manager = Mock()
    bench_manager.benchmarks = {
        "tpch": {
            "display_name": "TPC-H",
            "estimated_time_range": (2, 10),
            "complexity": "medium",
            "num_queries": 22,
        }
    }
    bench_manager.select_benchmark.return_value = benchmark_config

    orchestrator = Mock()
    result_payload = SimpleNamespace(validation_status="PASSED", execution_id="exec-456", query_results=[])
    guided_config = _tuned_unified_config()

    with ExitStack() as stack:
        stack.enter_context(patch.object(_run_module, "sys", _interactive_sys()))
        stack.enter_context(patch.object(_run_module, "SystemProfiler", return_value=profiler))
        stack.enter_context(patch.object(_run_module, "DatabaseManager", return_value=db_manager))
        stack.enter_context(patch.object(_run_module, "BenchmarkManager", return_value=bench_manager))
        stack.enter_context(patch.object(_run_module, "BenchmarkOrchestrator", return_value=orchestrator))
        stack.enter_context(patch.object(_run_module, "display_system_recommendations"))
        mock_caps = stack.enter_context(patch.object(_run_module.PlatformRegistry, "get_platform_capabilities"))
        stack.enter_context(patch.object(_run_module.PlatformRegistry, "requires_cloud_storage", return_value=False))
        stack.enter_context(
            patch.object(_run_module, "normalize_output_root", return_value=str(tmp_path / "normalized"))
        )
        mock_execute = stack.enter_context(
            patch.object(_run_module, "_execute_orchestrated_run", return_value=result_payload)
        )
        stack.enter_context(
            patch.object(
                _run_module,
                "_export_orchestrated_result",
                return_value={"json": str(tmp_path / "results.json")},
            )
        )
        stack.enter_context(patch.object(_run_module, "_render_post_run_charts"))
        stack.enter_context(patch("benchbox.cli.onboarding.check_and_run_first_time_setup", return_value=False))
        stack.enter_context(patch("benchbox.cli.preferences.load_last_run_config", return_value=None))
        stack.enter_context(patch("benchbox.cli.preferences.save_last_run_config"))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_table_mode", return_value="native"))
        stack.enter_context(
            patch.object(_benchmarks_module, "prompt_phases", return_value=["generate", "load", "statistics", "power"])
        )
        stack.enter_context(patch.object(_benchmarks_module, "prompt_query_subset", return_value=None))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_official_mode", return_value=(False, None)))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_seed", return_value=None))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_force_regeneration", return_value=None))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_validation_mode", return_value="exact"))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_capture_plans", return_value=False))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_output_location", return_value=None))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_table_format", return_value=(None, None)))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_verbose_output", return_value=0))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_platform_options", return_value=None))
        stack.enter_context(patch("benchbox.cli.tuning.run_tuning_wizard", return_value=guided_config))
        stack.enter_context(patch("benchbox.cli.dryrun.display_interactive_preview"))
        mock_confirm = stack.enter_context(patch.object(_run_module.Confirm, "ask"))
        mock_caps.return_value = SimpleNamespace(
            default_mode="sql",
            supports_sql=True,
            supports_dataframe=False,
            deployment_modes={},
            default_deployment=None,
            execution_engines={},
        )

        def _confirm_side_effect(prompt, default=False):
            text = str(prompt)
            if "configure tuning options" in text:
                return False
            if "Proceed with execution?" in text:
                return True
            return default

        mock_confirm.side_effect = _confirm_side_effect

        result = runner.invoke(
            run,
            ["--stats-reset", "--stats-per-table-timing"],
            obj=_run_obj(),
        )

    assert result.exit_code == 0, result.output
    mock_execute.assert_called_once()
    assert mock_execute.call_args.args[1] is benchmark_config
    assert benchmark_config.stats_reset is True
    assert benchmark_config.stats_per_table_timing is True


def test_interactive_execution_type_derived_from_phases(tmp_path: Path):
    runner = CliRunner()

    profiler = Mock()
    profiler.get_system_profile.return_value = SimpleNamespace(
        cpu_cores_logical=8,
        memory_total_gb=32,
        architecture="x86_64",
        os_type="darwin",
    )
    profiler.display_profile.return_value = None

    db_manager = Mock()
    database_config = SimpleNamespace(type="duckdb", options={}, execution_mode=None)
    db_manager.prompt_execution_style.return_value = "sql-local"
    db_manager.select_database.return_value = database_config

    benchmark_config = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=0.01,
        queries=None,
        concurrency=None,
        options={},
    )
    bench_manager = Mock()
    bench_manager.benchmarks = {
        "tpch": {
            "display_name": "TPC-H",
            "estimated_time_range": (2, 10),
            "complexity": "medium",
            "num_queries": 22,
        }
    }
    bench_manager.select_benchmark.return_value = benchmark_config

    orchestrator = Mock()
    result_payload = SimpleNamespace(validation_status="PASSED", execution_id="exec-123", query_results=[])

    with ExitStack() as stack:
        stack.enter_context(patch.object(_run_module, "sys", _interactive_sys()))
        stack.enter_context(patch.object(_run_module, "SystemProfiler", return_value=profiler))
        stack.enter_context(patch.object(_run_module, "DatabaseManager", return_value=db_manager))
        stack.enter_context(patch.object(_run_module, "BenchmarkManager", return_value=bench_manager))
        stack.enter_context(patch.object(_run_module, "BenchmarkOrchestrator", return_value=orchestrator))
        stack.enter_context(patch.object(_run_module, "display_system_recommendations"))
        mock_caps = stack.enter_context(patch.object(_run_module.PlatformRegistry, "get_platform_capabilities"))
        stack.enter_context(patch.object(_run_module.PlatformRegistry, "requires_cloud_storage", return_value=False))
        stack.enter_context(
            patch.object(_run_module, "normalize_output_root", return_value=str(tmp_path / "normalized"))
        )
        mock_execute = stack.enter_context(
            patch.object(_run_module, "_execute_orchestrated_run", return_value=result_payload)
        )
        stack.enter_context(
            patch.object(
                _run_module,
                "_export_orchestrated_result",
                return_value={"json": str(tmp_path / "results.json")},
            )
        )
        stack.enter_context(patch.object(_run_module, "_render_post_run_charts"))
        stack.enter_context(patch("benchbox.cli.onboarding.check_and_run_first_time_setup", return_value=False))
        stack.enter_context(patch("benchbox.cli.preferences.load_last_run_config", return_value=None))
        stack.enter_context(patch("benchbox.cli.preferences.save_last_run_config"))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_table_mode", return_value="native"))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_phases", return_value=["throughput"]))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_query_subset", return_value=None))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_official_mode", return_value=(False, None)))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_seed", return_value=42))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_force_regeneration", return_value=None))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_validation_mode", return_value=None))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_capture_plans", return_value=False))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_output_location", return_value=None))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_table_format", return_value=(None, None)))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_verbose_output", return_value=0))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_platform_options", return_value={}))
        stack.enter_context(patch("benchbox.cli.tuning.run_tuning_wizard"))
        stack.enter_context(patch("benchbox.cli.dryrun.display_interactive_preview"))
        mock_confirm = stack.enter_context(patch.object(_run_module.Confirm, "ask"))
        mock_caps.return_value = SimpleNamespace(
            default_mode="sql",
            supports_sql=True,
            supports_dataframe=False,
            deployment_modes={},
            default_deployment=None,
            execution_engines={},
        )

        def _confirm_side_effect(prompt, default=False):
            text = str(prompt)
            if "configure tuning options" in text:
                return False
            if "Proceed with execution?" in text:
                return True
            return default

        mock_confirm.side_effect = _confirm_side_effect

        result = runner.invoke(run, [], obj=_run_obj())

    assert result.exit_code == 0, result.output
    assert mock_execute.called
    assert benchmark_config.test_execution_type == "throughput"


def test_interactive_dataframe_tuning_acceptance_applies_runtime_defaults(tmp_path: Path):
    runner = CliRunner()

    profiler = Mock()
    profiler.get_system_profile.return_value = SimpleNamespace(
        cpu_cores_logical=8,
        memory_total_gb=32,
        architecture="x86_64",
        os_type="darwin",
    )
    profiler.display_profile.return_value = None

    db_manager = Mock()
    database_config = SimpleNamespace(type="polars-df", options={}, execution_mode="dataframe")
    db_manager.prompt_execution_style.return_value = "dataframe-local"
    db_manager.select_database.return_value = database_config

    benchmark_config = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=0.01,
        queries=None,
        concurrency=1,
        options={},
    )
    bench_manager = Mock()
    bench_manager.benchmarks = {
        "tpch": {
            "display_name": "TPC-H",
            "estimated_time_range": (2, 10),
            "complexity": "medium",
            "num_queries": 22,
        }
    }
    bench_manager.select_benchmark.return_value = benchmark_config

    orchestrator = Mock()
    result_payload = SimpleNamespace(validation_status="PASSED", execution_id="exec-123", query_results=[])
    df_tuning_config = SimpleNamespace(name="df-smart-defaults")
    guided_config = _tuned_unified_config()

    with ExitStack() as stack:
        stack.enter_context(patch.object(_run_module, "sys", _interactive_sys()))
        stack.enter_context(patch.object(_run_module, "SystemProfiler", return_value=profiler))
        stack.enter_context(patch.object(_run_module, "DatabaseManager", return_value=db_manager))
        stack.enter_context(patch.object(_run_module, "BenchmarkManager", return_value=bench_manager))
        stack.enter_context(patch.object(_run_module, "BenchmarkOrchestrator", return_value=orchestrator))
        stack.enter_context(patch.object(_run_module, "display_system_recommendations"))
        mock_caps = stack.enter_context(patch.object(_run_module.PlatformRegistry, "get_platform_capabilities"))
        stack.enter_context(patch.object(_run_module.PlatformRegistry, "requires_cloud_storage", return_value=False))
        stack.enter_context(
            patch.object(_run_module, "normalize_output_root", return_value=str(tmp_path / "normalized"))
        )
        stack.enter_context(patch.object(_run_module, "_execute_orchestrated_run", return_value=result_payload))
        stack.enter_context(
            patch.object(
                _run_module,
                "_export_orchestrated_result",
                return_value={"json": str(tmp_path / "results.json")},
            )
        )
        stack.enter_context(patch.object(_run_module, "_render_post_run_charts"))
        stack.enter_context(patch("benchbox.cli.onboarding.check_and_run_first_time_setup", return_value=False))
        stack.enter_context(patch("benchbox.cli.preferences.load_last_run_config", return_value=None))
        stack.enter_context(patch("benchbox.cli.preferences.save_last_run_config"))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_table_mode", return_value="native"))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_phases", return_value=["power"]))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_query_subset", return_value=None))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_official_mode", return_value=(False, None)))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_seed", return_value=42))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_force_regeneration", return_value=None))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_validation_mode", return_value=None))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_capture_plans", return_value=False))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_output_location", return_value=None))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_table_format", return_value=(None, None)))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_verbose_output", return_value=0))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_platform_options", return_value={}))
        stack.enter_context(patch("benchbox.cli.tuning.run_tuning_wizard", return_value=guided_config))
        stack.enter_context(patch("benchbox.cli.dryrun.display_interactive_preview"))
        mock_df_defaults = stack.enter_context(
            patch("benchbox.core.dataframe.tuning.get_smart_defaults", return_value=df_tuning_config)
        )
        mock_confirm = stack.enter_context(patch.object(_run_module.Confirm, "ask"))
        mock_caps.return_value = SimpleNamespace(
            default_mode="dataframe",
            supports_sql=False,
            supports_dataframe=True,
            deployment_modes={},
            default_deployment=None,
            execution_engines={},
        )

        def _confirm_side_effect(prompt, default=False):
            text = str(prompt)
            if "configure tuning options" in text:
                return True
            if "Proceed with execution?" in text:
                return True
            return default

        mock_confirm.side_effect = _confirm_side_effect

        result = runner.invoke(run, [], obj=_run_obj())

    assert result.exit_code == 0, result.output
    mock_df_defaults.assert_called_once_with("polars-df")
    assert benchmark_config.options["df_tuning_config"] is df_tuning_config
    assert database_config.tuning_enabled is True
    assert benchmark_config.options["unified_tuning_configuration"] is guided_config
    assert database_config.tuning_source == "wizard"


def test_interactive_wizard_baseline_maps_to_notuning_for_external_mode(tmp_path: Path):
    runner = CliRunner()

    profiler = Mock()
    profiler.get_system_profile.return_value = SimpleNamespace(
        cpu_cores_logical=8,
        memory_total_gb=32,
        architecture="x86_64",
        os_type="darwin",
    )
    profiler.display_profile.return_value = None

    db_manager = Mock()
    database_config = SimpleNamespace(type="duckdb", options={}, execution_mode=None)
    db_manager.prompt_execution_style.return_value = "sql-local"
    db_manager.select_database.return_value = database_config

    benchmark_config = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=0.01,
        queries=None,
        concurrency=1,
        options={},
    )
    bench_manager = Mock()
    bench_manager.benchmarks = {
        "tpch": {
            "display_name": "TPC-H",
            "estimated_time_range": (2, 10),
            "complexity": "medium",
            "num_queries": 22,
        }
    }
    bench_manager.select_benchmark.return_value = benchmark_config

    orchestrator = Mock()
    result_payload = SimpleNamespace(validation_status="PASSED", execution_id="exec-123", query_results=[])
    baseline_config = build_baseline_unified_config()

    with ExitStack() as stack:
        stack.enter_context(patch.object(_run_module, "sys", _interactive_sys()))
        stack.enter_context(patch.object(_run_module, "SystemProfiler", return_value=profiler))
        stack.enter_context(patch.object(_run_module, "DatabaseManager", return_value=db_manager))
        stack.enter_context(patch.object(_run_module, "BenchmarkManager", return_value=bench_manager))
        stack.enter_context(patch.object(_run_module, "BenchmarkOrchestrator", return_value=orchestrator))
        stack.enter_context(patch.object(_run_module, "display_system_recommendations"))
        mock_caps = stack.enter_context(patch.object(_run_module.PlatformRegistry, "get_platform_capabilities"))
        stack.enter_context(patch.object(_run_module.PlatformRegistry, "requires_cloud_storage", return_value=False))
        stack.enter_context(
            patch.object(_run_module, "normalize_output_root", return_value=str(tmp_path / "normalized"))
        )
        mock_execute = stack.enter_context(
            patch.object(_run_module, "_execute_orchestrated_run", return_value=result_payload)
        )
        stack.enter_context(
            patch.object(
                _run_module,
                "_export_orchestrated_result",
                return_value={"json": str(tmp_path / "results.json")},
            )
        )
        stack.enter_context(patch.object(_run_module, "_render_post_run_charts"))
        stack.enter_context(patch("benchbox.cli.onboarding.check_and_run_first_time_setup", return_value=False))
        stack.enter_context(patch("benchbox.cli.preferences.load_last_run_config", return_value=None))
        mock_save_last_run = stack.enter_context(patch("benchbox.cli.preferences.save_last_run_config"))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_table_mode", return_value="external"))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_phases", return_value=["power"]))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_query_subset", return_value=None))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_official_mode", return_value=(False, None)))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_seed", return_value=42))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_force_regeneration", return_value=None))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_validation_mode", return_value=None))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_capture_plans", return_value=False))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_output_location", return_value=None))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_table_format", return_value=(None, None)))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_verbose_output", return_value=0))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_platform_options", return_value={}))
        stack.enter_context(patch("benchbox.cli.tuning.run_tuning_wizard", return_value=baseline_config))
        mock_preview = stack.enter_context(patch("benchbox.cli.dryrun.display_interactive_preview"))
        mock_confirm = stack.enter_context(patch.object(_run_module.Confirm, "ask"))
        mock_caps.return_value = SimpleNamespace(
            default_mode="sql",
            supports_sql=True,
            supports_dataframe=False,
            deployment_modes={},
            default_deployment=None,
            execution_engines={},
        )

        def _confirm_side_effect(prompt, default=False):
            text = str(prompt)
            if "configure tuning options" in text:
                return True
            if "Proceed with execution?" in text:
                return True
            return default

        mock_confirm.side_effect = _confirm_side_effect

        result = runner.invoke(run, [], obj=_run_obj())

    assert result.exit_code == 0, result.output
    assert mock_execute.called
    assert benchmark_config.options["tuning_enabled"] is False
    assert database_config.tuning_enabled is False
    assert benchmark_config.options["unified_tuning_configuration"] is baseline_config
    assert database_config.tuning_source == "wizard"
    mock_preview.assert_called_once()
    assert mock_preview.call_args.kwargs["tuning"] is None
    mock_save_last_run.assert_called_once()
    assert mock_save_last_run.call_args.kwargs["tuning_mode"] == "notuning"


def test_interactive_dataframe_wizard_baseline_skips_runtime_defaults(tmp_path: Path):
    runner = CliRunner()

    profiler = Mock()
    profiler.get_system_profile.return_value = SimpleNamespace(
        cpu_cores_logical=8,
        memory_total_gb=32,
        architecture="x86_64",
        os_type="darwin",
    )
    profiler.display_profile.return_value = None

    db_manager = Mock()
    database_config = SimpleNamespace(type="polars-df", options={}, execution_mode="dataframe")
    db_manager.prompt_execution_style.return_value = "dataframe-local"
    db_manager.select_database.return_value = database_config

    benchmark_config = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=0.01,
        queries=None,
        concurrency=1,
        options={},
    )
    bench_manager = Mock()
    bench_manager.benchmarks = {
        "tpch": {
            "display_name": "TPC-H",
            "estimated_time_range": (2, 10),
            "complexity": "medium",
            "num_queries": 22,
        }
    }
    bench_manager.select_benchmark.return_value = benchmark_config

    orchestrator = Mock()
    result_payload = SimpleNamespace(validation_status="PASSED", execution_id="exec-123", query_results=[])
    baseline_config = build_baseline_unified_config()

    with ExitStack() as stack:
        stack.enter_context(patch.object(_run_module, "sys", _interactive_sys()))
        stack.enter_context(patch.object(_run_module, "SystemProfiler", return_value=profiler))
        stack.enter_context(patch.object(_run_module, "DatabaseManager", return_value=db_manager))
        stack.enter_context(patch.object(_run_module, "BenchmarkManager", return_value=bench_manager))
        stack.enter_context(patch.object(_run_module, "BenchmarkOrchestrator", return_value=orchestrator))
        stack.enter_context(patch.object(_run_module, "display_system_recommendations"))
        mock_caps = stack.enter_context(patch.object(_run_module.PlatformRegistry, "get_platform_capabilities"))
        stack.enter_context(patch.object(_run_module.PlatformRegistry, "requires_cloud_storage", return_value=False))
        stack.enter_context(
            patch.object(_run_module, "normalize_output_root", return_value=str(tmp_path / "normalized"))
        )
        stack.enter_context(patch.object(_run_module, "_execute_orchestrated_run", return_value=result_payload))
        stack.enter_context(
            patch.object(
                _run_module,
                "_export_orchestrated_result",
                return_value={"json": str(tmp_path / "results.json")},
            )
        )
        stack.enter_context(patch.object(_run_module, "_render_post_run_charts"))
        stack.enter_context(patch("benchbox.cli.onboarding.check_and_run_first_time_setup", return_value=False))
        stack.enter_context(patch("benchbox.cli.preferences.load_last_run_config", return_value=None))
        mock_save_last_run = stack.enter_context(patch("benchbox.cli.preferences.save_last_run_config"))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_table_mode", return_value="native"))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_phases", return_value=["power"]))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_query_subset", return_value=None))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_official_mode", return_value=(False, None)))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_seed", return_value=42))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_force_regeneration", return_value=None))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_validation_mode", return_value=None))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_capture_plans", return_value=False))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_output_location", return_value=None))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_table_format", return_value=(None, None)))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_verbose_output", return_value=0))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_platform_options", return_value={}))
        stack.enter_context(patch("benchbox.cli.tuning.run_tuning_wizard", return_value=baseline_config))
        mock_preview = stack.enter_context(patch("benchbox.cli.dryrun.display_interactive_preview"))
        mock_df_defaults = stack.enter_context(patch("benchbox.core.dataframe.tuning.get_smart_defaults"))
        mock_confirm = stack.enter_context(patch.object(_run_module.Confirm, "ask"))
        mock_caps.return_value = SimpleNamespace(
            default_mode="dataframe",
            supports_sql=False,
            supports_dataframe=True,
            deployment_modes={},
            default_deployment=None,
            execution_engines={},
        )

        def _confirm_side_effect(prompt, default=False):
            text = str(prompt)
            if "configure tuning options" in text:
                return True
            if "Proceed with execution?" in text:
                return True
            return default

        mock_confirm.side_effect = _confirm_side_effect

        result = runner.invoke(run, [], obj=_run_obj())

    assert result.exit_code == 0, result.output
    mock_df_defaults.assert_not_called()
    assert benchmark_config.options["tuning_enabled"] is False
    assert database_config.tuning_enabled is False
    assert "df_tuning_config" not in benchmark_config.options
    mock_preview.assert_called_once()
    assert mock_preview.call_args.kwargs["tuning"] is None
    mock_save_last_run.assert_called_once()
    assert mock_save_last_run.call_args.kwargs["tuning_mode"] == "notuning"


def test_fallback_wizard_baseline_reclassifies_runtime_state_for_dataframe_platform(tmp_path: Path):
    runner = CliRunner()

    profiler = Mock()
    profiler.get_system_profile.return_value = SimpleNamespace(
        cpu_cores_logical=8,
        memory_total_gb=32,
        architecture="x86_64",
        os_type="darwin",
    )
    profiler.display_profile.return_value = None

    database_config = SimpleNamespace(
        type="dask-df",
        options={},
        execution_mode="dataframe",
        driver_version_actual=None,
        driver_version_resolved=None,
    )
    db_manager = Mock()
    db_manager.create_config.return_value = database_config

    bench_manager = Mock()
    bench_manager.benchmarks = {
        "tpch": {
            "display_name": "TPC-H",
            "estimated_time_range": (2, 10),
            "complexity": "medium",
            "num_queries": 22,
        }
    }
    bench_manager.validate_scale_factor.return_value = None

    orchestrator = Mock()
    result_payload = SimpleNamespace(validation_status="PASSED", execution_id="exec-123", query_results=[])
    baseline_config = build_baseline_unified_config()

    with ExitStack() as stack:
        stack.enter_context(patch.object(_run_module, "SystemProfiler", return_value=profiler))
        stack.enter_context(patch.object(_run_module, "DatabaseManager", return_value=db_manager))
        stack.enter_context(patch.object(_run_module, "BenchmarkManager", return_value=bench_manager))
        stack.enter_context(patch.object(_run_module, "BenchmarkOrchestrator", return_value=orchestrator))
        stack.enter_context(patch.object(_run_module, "display_system_recommendations"))
        stack.enter_context(
            patch.object(
                _run_module.PlatformRegistry,
                "get_platform_capabilities",
                return_value=SimpleNamespace(
                    default_mode="dataframe",
                    supports_sql=False,
                    supports_dataframe=True,
                    deployment_modes={},
                    default_deployment=None,
                    execution_engines={},
                ),
            )
        )
        stack.enter_context(patch.object(_run_module.PlatformRegistry, "requires_cloud_storage", return_value=False))
        mock_execute = stack.enter_context(
            patch.object(_run_module, "_execute_orchestrated_run", return_value=result_payload)
        )
        stack.enter_context(
            patch.object(
                _run_module,
                "_export_orchestrated_result",
                return_value={"json": str(tmp_path / "results.json")},
            )
        )
        stack.enter_context(patch.object(_run_module, "_render_post_run_charts"))
        stack.enter_context(patch("benchbox.cli.onboarding.check_and_run_first_time_setup", return_value=False))
        stack.enter_context(patch("benchbox.cli.preferences.load_last_run_config", return_value=None))
        mock_save_last_run = stack.enter_context(patch("benchbox.cli.preferences.save_last_run_config"))
        stack.enter_context(patch("benchbox.cli.tuning.run_tuning_wizard", return_value=baseline_config))
        mock_df_defaults = stack.enter_context(patch("benchbox.core.dataframe.tuning.get_smart_defaults"))
        mock_confirm = stack.enter_context(patch.object(_run_module.Confirm, "ask"))

        def _confirm_side_effect(prompt, default=False):
            text = str(prompt)
            if "configure tuning options" in text:
                return True
            return default

        mock_confirm.side_effect = _confirm_side_effect

        result = runner.invoke(
            run,
            ["--platform", "dask-df", "--benchmark", "tpch", "--tuning", "tuned"],
            obj=_run_obj(),
        )

    assert result.exit_code == 0, result.output
    mock_df_defaults.assert_not_called()
    db_overrides = db_manager.create_config.call_args.args[2]
    assert db_overrides["tuning_enabled"] is False
    assert db_overrides["unified_tuning_configuration"] is baseline_config
    assert "df_tuning_config" not in db_overrides
    executed_benchmark_config = mock_execute.call_args.args[1]
    assert executed_benchmark_config.options["tuning_enabled"] is False
    assert executed_benchmark_config.options["unified_tuning_configuration"] is baseline_config
    assert "df_tuning_config" not in executed_benchmark_config.options
    mock_save_last_run.assert_called_once()
    assert mock_save_last_run.call_args.kwargs["tuning_mode"] == "notuning"


def test_direct_dataframe_tuning_config_propagates_to_benchmark_config(tmp_path: Path):
    runner = CliRunner()

    profiler = Mock()
    profiler.get_system_profile.return_value = SimpleNamespace(
        cpu_cores_logical=8,
        memory_total_gb=32,
        architecture="x86_64",
        os_type="darwin",
    )
    profiler.display_profile.return_value = None

    database_config = SimpleNamespace(
        type="polars-df",
        options={},
        execution_mode="dataframe",
        driver_version_actual=None,
        driver_version_resolved=None,
    )
    db_manager = Mock()
    db_manager.create_config.return_value = database_config

    bench_manager = Mock()
    bench_manager.benchmarks = {
        "tpch": {
            "display_name": "TPC-H",
            "estimated_time_range": (2, 10),
            "complexity": "medium",
            "num_queries": 22,
        }
    }
    bench_manager.validate_scale_factor.return_value = None

    orchestrator = Mock()
    result_payload = SimpleNamespace(validation_status="PASSED", execution_id="exec-123", query_results=[])
    df_tuning_config = SimpleNamespace(name="df-direct-tuned")

    with ExitStack() as stack:
        stack.enter_context(patch.object(_run_module, "SystemProfiler", return_value=profiler))
        stack.enter_context(patch.object(_run_module, "DatabaseManager", return_value=db_manager))
        stack.enter_context(patch.object(_run_module, "BenchmarkManager", return_value=bench_manager))
        stack.enter_context(patch.object(_run_module, "BenchmarkOrchestrator", return_value=orchestrator))
        stack.enter_context(patch.object(_run_module, "display_system_recommendations"))
        stack.enter_context(
            patch.object(
                _run_module.PlatformRegistry,
                "get_platform_capabilities",
                return_value=SimpleNamespace(
                    default_mode="dataframe",
                    supports_sql=False,
                    supports_dataframe=True,
                    deployment_modes={},
                    default_deployment=None,
                    execution_engines={},
                ),
            )
        )
        stack.enter_context(patch.object(_run_module.PlatformRegistry, "requires_cloud_storage", return_value=False))
        mock_resolve = stack.enter_context(
            patch.object(_run_module, "resolve_dataframe_tuning_config", return_value=df_tuning_config)
        )
        mock_execute = stack.enter_context(
            patch.object(_run_module, "_execute_orchestrated_run", return_value=result_payload)
        )
        stack.enter_context(
            patch.object(
                _run_module,
                "_export_orchestrated_result",
                return_value={"json": str(tmp_path / "results.json")},
            )
        )
        stack.enter_context(patch.object(_run_module, "_render_post_run_charts"))
        stack.enter_context(patch("benchbox.cli.onboarding.check_and_run_first_time_setup", return_value=False))
        stack.enter_context(patch("benchbox.cli.preferences.load_last_run_config", return_value=None))
        stack.enter_context(patch("benchbox.cli.preferences.save_last_run_config"))

        result = runner.invoke(
            run,
            ["--platform", "polars-df", "--benchmark", "tpch", "--non-interactive"],
            obj=_run_obj(),
        )

    assert result.exit_code == 0, result.output
    assert mock_resolve.called
    executed_benchmark_config = mock_execute.call_args.args[1]
    assert executed_benchmark_config.options["df_tuning_config"] is df_tuning_config


def test_interactive_tuning_declined_sets_notuning_state(tmp_path: Path):
    runner = CliRunner()

    profiler = Mock()
    profiler.get_system_profile.return_value = SimpleNamespace(
        cpu_cores_logical=8,
        memory_total_gb=32,
        architecture="x86_64",
        os_type="darwin",
    )
    profiler.display_profile.return_value = None

    db_manager = Mock()
    database_config = SimpleNamespace(type="duckdb", options={}, execution_mode=None)
    db_manager.prompt_execution_style.return_value = "sql-local"
    db_manager.select_database.return_value = database_config

    benchmark_config = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=0.01,
        queries=None,
        concurrency=1,
        options={},
    )
    bench_manager = Mock()
    bench_manager.benchmarks = {
        "tpch": {
            "display_name": "TPC-H",
            "estimated_time_range": (2, 10),
            "complexity": "medium",
            "num_queries": 22,
        }
    }
    bench_manager.select_benchmark.return_value = benchmark_config

    orchestrator = Mock()
    result_payload = SimpleNamespace(validation_status="PASSED", execution_id="exec-123", query_results=[])

    with ExitStack() as stack:
        stack.enter_context(patch.object(_run_module, "sys", _interactive_sys()))
        stack.enter_context(patch.object(_run_module, "SystemProfiler", return_value=profiler))
        stack.enter_context(patch.object(_run_module, "DatabaseManager", return_value=db_manager))
        stack.enter_context(patch.object(_run_module, "BenchmarkManager", return_value=bench_manager))
        stack.enter_context(patch.object(_run_module, "BenchmarkOrchestrator", return_value=orchestrator))
        stack.enter_context(patch.object(_run_module, "display_system_recommendations"))
        mock_caps = stack.enter_context(patch.object(_run_module.PlatformRegistry, "get_platform_capabilities"))
        stack.enter_context(patch.object(_run_module.PlatformRegistry, "requires_cloud_storage", return_value=False))
        stack.enter_context(
            patch.object(_run_module, "normalize_output_root", return_value=str(tmp_path / "normalized"))
        )
        mock_execute = stack.enter_context(
            patch.object(_run_module, "_execute_orchestrated_run", return_value=result_payload)
        )
        stack.enter_context(
            patch.object(
                _run_module,
                "_export_orchestrated_result",
                return_value={"json": str(tmp_path / "results.json")},
            )
        )
        stack.enter_context(patch.object(_run_module, "_render_post_run_charts"))
        stack.enter_context(patch("benchbox.cli.onboarding.check_and_run_first_time_setup", return_value=False))
        stack.enter_context(patch("benchbox.cli.preferences.load_last_run_config", return_value=None))
        stack.enter_context(patch("benchbox.cli.preferences.save_last_run_config"))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_table_mode", return_value="native"))
        stack.enter_context(
            patch.object(_benchmarks_module, "prompt_phases", return_value=["generate", "load", "power"])
        )
        stack.enter_context(patch.object(_benchmarks_module, "prompt_query_subset", return_value=None))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_official_mode", return_value=(False, None)))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_seed", return_value=42))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_force_regeneration", return_value=None))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_validation_mode", return_value=None))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_capture_plans", return_value=False))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_output_location", return_value=None))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_table_format", return_value=(None, None)))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_verbose_output", return_value=0))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_platform_options", return_value={}))
        mock_wizard = stack.enter_context(patch("benchbox.cli.tuning.run_tuning_wizard"))
        mock_preview = stack.enter_context(patch("benchbox.cli.dryrun.display_interactive_preview"))
        mock_confirm = stack.enter_context(patch.object(_run_module.Confirm, "ask"))
        mock_caps.return_value = SimpleNamespace(
            default_mode="sql",
            supports_sql=True,
            supports_dataframe=False,
            deployment_modes={},
            default_deployment=None,
            execution_engines={},
        )

        def _confirm_side_effect(prompt, default=False):
            text = str(prompt)
            if "configure tuning options" in text:
                return False
            if "Proceed with execution?" in text:
                return True
            return default

        mock_confirm.side_effect = _confirm_side_effect

        result = runner.invoke(run, [], obj=_run_obj())

    assert result.exit_code == 0, result.output
    assert mock_execute.called
    mock_wizard.assert_not_called()
    assert benchmark_config.options["tuning_enabled"] is False
    assert database_config.options["tuning_enabled"] is False
    assert database_config.tuning_enabled is False
    assert database_config.tuning_source == "baseline"
    baseline_config = benchmark_config.options["unified_tuning_configuration"]
    assert baseline_config.primary_keys.enabled is False
    assert baseline_config.foreign_keys.enabled is False
    assert baseline_config.unique_constraints.enabled is False
    assert baseline_config.check_constraints.enabled is False
    assert "df_tuning_config" not in benchmark_config.options
    mock_preview.assert_called_once()
    assert mock_preview.call_args.kwargs["tuning"] is None


def test_interactive_cloud_platform_stops_when_credentials_are_missing():
    runner = CliRunner()

    profiler = Mock()
    profiler.get_system_profile.return_value = SimpleNamespace(
        cpu_cores_logical=8,
        memory_total_gb=16,
        architecture="x86_64",
        os_type="darwin",
    )
    profiler.display_profile.return_value = None

    db_manager = Mock()
    db_manager.prompt_execution_style.return_value = "cloud-sql"
    db_manager.select_database.return_value = SimpleNamespace(type="snowflake", options={}, execution_mode=None)
    bench_manager = Mock()

    with ExitStack() as stack:
        stack.enter_context(patch.object(_run_module, "sys", _interactive_sys()))
        stack.enter_context(patch.object(_run_module, "SystemProfiler", return_value=profiler))
        stack.enter_context(patch.object(_run_module, "DatabaseManager", return_value=db_manager))
        stack.enter_context(patch.object(_run_module, "BenchmarkManager", return_value=bench_manager))
        stack.enter_context(patch.object(_run_module, "display_system_recommendations"))
        stack.enter_context(patch.object(_benchmarks_module, "prompt_platform_options", return_value={}))
        mock_caps = stack.enter_context(patch.object(_run_module.PlatformRegistry, "get_platform_capabilities"))
        stack.enter_context(patch.object(_run_module.PlatformRegistry, "requires_cloud_storage", return_value=True))
        stack.enter_context(patch.object(_run_module, "check_and_setup_platform_credentials", return_value=False))
        stack.enter_context(patch("benchbox.cli.onboarding.check_and_run_first_time_setup", return_value=False))
        stack.enter_context(patch("benchbox.cli.preferences.load_last_run_config", return_value=None))
        mock_caps.return_value = SimpleNamespace(
            default_mode="sql",
            supports_sql=True,
            supports_dataframe=False,
            deployment_modes={},
            default_deployment=None,
            execution_engines={},
        )

        result = runner.invoke(run, [], obj=_run_obj())

    assert result.exit_code == 1
    assert "Cannot proceed without SNOWFLAKE credentials" in result.output
    bench_manager.select_benchmark.assert_not_called()


def _non_interactive_base_patches():
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch("benchbox.cli.onboarding.check_and_run_first_time_setup", return_value=False))
    stack.enter_context(patch("benchbox.cli.preferences.load_last_run_config", return_value=None))
    stack.enter_context(patch.object(_run_module, "SystemProfiler"))
    stack.enter_context(patch.object(_run_module, "display_system_recommendations"))
    return stack


class TestRunCommandValidation:
    def test_quiet_and_verbose_flags_conflict(self):
        runner = CliRunner()
        with _non_interactive_base_patches():
            result = runner.invoke(
                run,
                ["--quiet", "-v", "--platform", "duckdb", "--benchmark", "tpch", "--non-interactive"],
                obj=_run_obj(),
            )
        assert result.exit_code != 0
        assert "--quiet cannot be used with -v/-vv flags" in result.output

    def test_official_mode_rejects_non_compliant_scale_factor(self):
        runner = CliRunner()
        with _non_interactive_base_patches():
            result = runner.invoke(
                run,
                ["--official", "--scale", "0.5", "--platform", "duckdb", "--benchmark", "tpch", "--non-interactive"],
                obj=_run_obj(),
            )
        assert result.exit_code != 0
        assert "not a TPC-specified scale factor" in result.output

    def test_official_mode_accepts_compliant_scale_factor(self):
        runner = CliRunner()
        with _non_interactive_base_patches() as stack:
            stack.enter_context(patch.object(_run_module, "_execute_orchestrated_run", side_effect=SystemExit(0)))
            stack.enter_context(
                patch.object(
                    _run_module.PlatformRegistry,
                    "get_platform_capabilities",
                    return_value=SimpleNamespace(
                        default_mode="sql",
                        supports_sql=True,
                        supports_dataframe=False,
                        deployment_modes={},
                        default_deployment=None,
                        execution_engines={},
                    ),
                )
            )
            stack.enter_context(
                patch.object(_run_module.PlatformRegistry, "requires_cloud_storage", return_value=False)
            )
            result = runner.invoke(
                run,
                ["--official", "--scale", "1", "--platform", "duckdb", "--benchmark", "tpch", "--non-interactive"],
                obj=_run_obj(),
            )
        assert "not a TPC-specified scale factor" not in result.output

    @pytest.mark.parametrize("benchmark_name", ["tpcds", "tpc-ds", "tpc_ds"])
    def test_tpcds_official_mode_rejects_tpch_only_scale_factor(self, benchmark_name):
        runner = CliRunner()
        with _non_interactive_base_patches():
            result = runner.invoke(
                run,
                [
                    "--official",
                    "--scale",
                    "30",
                    "--platform",
                    "duckdb",
                    "--benchmark",
                    benchmark_name,
                    "--non-interactive",
                ],
                obj=_run_obj(),
            )

        assert result.exit_code != 0
        assert "Scale factor 30.0 is not a TPC-specified scale factor" in result.output
        assert "30.0" not in result.output.split("Allowed scale factors:", 1)[-1]

    def test_invalid_phases_rejected(self):
        runner = CliRunner()
        with _non_interactive_base_patches():
            result = runner.invoke(
                run,
                [
                    "--phases",
                    "generate,badphase,power",
                    "--platform",
                    "duckdb",
                    "--benchmark",
                    "tpch",
                    "--non-interactive",
                ],
                obj=_run_obj(),
            )
        assert result.exit_code != 0
        assert "Invalid phases" in result.output or "badphase" in result.output

    def test_queries_empty_string_rejected(self):
        runner = CliRunner()
        with _non_interactive_base_patches():
            result = runner.invoke(
                run,
                ["--queries", "  ,  ", "--platform", "duckdb", "--benchmark", "tpch", "--non-interactive"],
                obj=_run_obj(),
            )
        assert result.exit_code != 0
        assert "--queries flag provided but no valid query IDs found" in result.output

    def test_queries_too_many_rejected(self):
        runner = CliRunner()
        many_queries = ",".join(f"Q{i}" for i in range(1, 103))
        with _non_interactive_base_patches():
            result = runner.invoke(
                run,
                ["--queries", many_queries, "--platform", "duckdb", "--benchmark", "tpch", "--non-interactive"],
                obj=_run_obj(),
            )
        assert result.exit_code != 0
        assert "Too many queries" in result.output

    def test_queries_too_long_id_rejected(self):
        from benchbox.utils.input_validation import MAX_QUERY_ID_LENGTH

        runner = CliRunner()
        long_id = "Q" + "x" * MAX_QUERY_ID_LENGTH
        with _non_interactive_base_patches():
            result = runner.invoke(
                run,
                ["--queries", long_id, "--platform", "duckdb", "--benchmark", "tpch", "--non-interactive"],
                obj=_run_obj(),
            )
        assert result.exit_code != 0
        assert f"Query ID too long (max {MAX_QUERY_ID_LENGTH} chars)" in result.output

    def test_non_interactive_missing_benchmark_rejected(self):
        runner = CliRunner()
        with _non_interactive_base_patches():
            result = runner.invoke(
                run,
                ["--non-interactive", "--platform", "duckdb"],
                obj=_run_obj(),
            )
        assert result.exit_code != 0
        assert "--benchmark" in result.output or "Missing" in result.output

    def test_non_interactive_missing_platform_for_power_phase_rejected(self):
        runner = CliRunner()
        with _non_interactive_base_patches():
            result = runner.invoke(
                run,
                ["--non-interactive", "--benchmark", "tpch", "--phases", "power"],
                obj=_run_obj(),
            )
        assert result.exit_code != 0
        assert "--platform" in result.output or "Missing" in result.output

    def test_platform_option_without_platform_rejected(self):
        runner = CliRunner()
        with _non_interactive_base_patches():
            result = runner.invoke(
                run,
                ["--platform-option", "key=value", "--benchmark", "tpch", "--non-interactive"],
                obj=_run_obj(),
            )
        assert result.exit_code != 0
        assert "Platform options require a --platform selection" in result.output

    def test_official_with_seed_shows_derived_banner(self):
        runner = CliRunner()
        with _non_interactive_base_patches() as stack:
            stack.enter_context(patch.object(_run_module, "_execute_orchestrated_run", side_effect=SystemExit(0)))
            stack.enter_context(
                patch.object(
                    _run_module.PlatformRegistry,
                    "get_platform_capabilities",
                    return_value=SimpleNamespace(
                        default_mode="sql",
                        supports_sql=True,
                        supports_dataframe=False,
                        deployment_modes={},
                        default_deployment=None,
                        execution_engines={},
                    ),
                )
            )
            stack.enter_context(
                patch.object(_run_module.PlatformRegistry, "requires_cloud_storage", return_value=False)
            )
            result = runner.invoke(
                run,
                [
                    "--official",
                    "--scale",
                    "1",
                    "--seed",
                    "42",
                    "--platform",
                    "duckdb",
                    "--benchmark",
                    "tpch",
                    "--non-interactive",
                ],
                obj=_run_obj(),
            )
        assert "TPC Official Mode Benchmark Run" in result.output
        assert "do not comply with the TPC-H Specification" in " ".join(result.output.split())
        assert "Seed: 42" in result.output

    def test_official_without_seed_warns_about_reproducibility(self):
        runner = CliRunner()
        with _non_interactive_base_patches() as stack:
            stack.enter_context(patch.object(_run_module, "_execute_orchestrated_run", side_effect=SystemExit(0)))
            stack.enter_context(
                patch.object(
                    _run_module.PlatformRegistry,
                    "get_platform_capabilities",
                    return_value=SimpleNamespace(
                        default_mode="sql",
                        supports_sql=True,
                        supports_dataframe=False,
                        deployment_modes={},
                        default_deployment=None,
                        execution_engines={},
                    ),
                )
            )
            stack.enter_context(
                patch.object(_run_module.PlatformRegistry, "requires_cloud_storage", return_value=False)
            )
            result = runner.invoke(
                run,
                ["--official", "--scale", "1", "--platform", "duckdb", "--benchmark", "tpch", "--non-interactive"],
                obj=_run_obj(),
            )
        assert "No --seed specified" in result.output or "seed" in result.output.lower()

    def test_compression_flag_accepted_zstd(self):
        runner = CliRunner()
        with _non_interactive_base_patches() as stack:
            stack.enter_context(patch.object(_run_module, "_execute_orchestrated_run", side_effect=SystemExit(0)))
            stack.enter_context(
                patch.object(
                    _run_module.PlatformRegistry,
                    "get_platform_capabilities",
                    return_value=SimpleNamespace(
                        default_mode="sql",
                        supports_sql=True,
                        supports_dataframe=False,
                        deployment_modes={},
                        default_deployment=None,
                        execution_engines={},
                    ),
                )
            )
            stack.enter_context(
                patch.object(_run_module.PlatformRegistry, "requires_cloud_storage", return_value=False)
            )
            result = runner.invoke(
                run,
                [
                    "--compression",
                    "zstd",
                    "--platform",
                    "duckdb",
                    "--benchmark",
                    "tpch",
                    "--non-interactive",
                ],
                obj=_run_obj(),
            )
        assert "Invalid value for '--compression'" not in result.output
        assert result.exit_code == 0

    def test_compression_flag_accepted_none(self):
        runner = CliRunner()
        with _non_interactive_base_patches() as stack:
            stack.enter_context(patch.object(_run_module, "_execute_orchestrated_run", side_effect=SystemExit(0)))
            stack.enter_context(
                patch.object(
                    _run_module.PlatformRegistry,
                    "get_platform_capabilities",
                    return_value=SimpleNamespace(
                        default_mode="sql",
                        supports_sql=True,
                        supports_dataframe=False,
                        deployment_modes={},
                        default_deployment=None,
                        execution_engines={},
                    ),
                )
            )
            stack.enter_context(
                patch.object(_run_module.PlatformRegistry, "requires_cloud_storage", return_value=False)
            )
            result = runner.invoke(
                run,
                ["--compression", "none", "--platform", "duckdb", "--benchmark", "tpch", "--non-interactive"],
                obj=_run_obj(),
            )
        assert "Invalid value for '--compression'" not in result.output
        assert result.exit_code == 0

    def test_validation_flag_accepted_loose(self):
        runner = CliRunner()
        with _non_interactive_base_patches() as stack:
            stack.enter_context(patch.object(_run_module, "_execute_orchestrated_run", side_effect=SystemExit(0)))
            stack.enter_context(
                patch.object(
                    _run_module.PlatformRegistry,
                    "get_platform_capabilities",
                    return_value=SimpleNamespace(
                        default_mode="sql",
                        supports_sql=True,
                        supports_dataframe=False,
                        deployment_modes={},
                        default_deployment=None,
                        execution_engines={},
                    ),
                )
            )
            stack.enter_context(
                patch.object(_run_module.PlatformRegistry, "requires_cloud_storage", return_value=False)
            )
            result = runner.invoke(
                run,
                ["--validation", "loose", "--platform", "duckdb", "--benchmark", "tpch", "--non-interactive"],
                obj=_run_obj(),
            )
        assert "Invalid value for '--validation'" not in result.output
        assert result.exit_code == 0

    def test_tuning_flag_accepted_notuning(self):
        runner = CliRunner()
        with _non_interactive_base_patches() as stack:
            stack.enter_context(patch.object(_run_module, "_execute_orchestrated_run", side_effect=SystemExit(0)))
            stack.enter_context(
                patch.object(
                    _run_module.PlatformRegistry,
                    "get_platform_capabilities",
                    return_value=SimpleNamespace(
                        default_mode="sql",
                        supports_sql=True,
                        supports_dataframe=False,
                        deployment_modes={},
                        default_deployment=None,
                        execution_engines={},
                    ),
                )
            )
            stack.enter_context(
                patch.object(_run_module.PlatformRegistry, "requires_cloud_storage", return_value=False)
            )
            result = runner.invoke(
                run,
                ["--tuning", "notuning", "--platform", "duckdb", "--benchmark", "tpch", "--non-interactive"],
                obj=_run_obj(),
            )
        assert "Invalid value for '--tuning'" not in result.output
        assert result.exit_code == 0

    def test_platform_option_with_platform_parsed(self):
        runner = CliRunner()
        with _non_interactive_base_patches() as stack:
            stack.enter_context(patch.object(_run_module, "_execute_orchestrated_run", side_effect=SystemExit(0)))
            stack.enter_context(
                patch.object(
                    _run_module.PlatformRegistry,
                    "get_platform_capabilities",
                    return_value=SimpleNamespace(
                        default_mode="sql",
                        supports_sql=True,
                        supports_dataframe=False,
                        deployment_modes={},
                        default_deployment=None,
                        execution_engines={},
                    ),
                )
            )
            stack.enter_context(
                patch.object(_run_module.PlatformRegistry, "requires_cloud_storage", return_value=False)
            )
            result = runner.invoke(
                run,
                [
                    "--platform-option",
                    "driver_version=1.2.0",
                    "--platform",
                    "duckdb",
                    "--benchmark",
                    "tpch",
                    "--non-interactive",
                ],
                obj=_run_obj(),
            )
        assert "Platform options require a --platform selection" not in result.output
        assert result.exit_code != 2
