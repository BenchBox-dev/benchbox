from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestRunnerMonitoringGate:
    def test_no_monitor_created_when_monitoring_unavailable(self):
        import benchbox.core.runner.runner as runner_mod

        mock_results = MagicMock()
        mock_results.execution_context = None

        with (
            patch.object(runner_mod, "_MONITORING_AVAILABLE", False),
            patch.object(runner_mod, "get_benchmark_instance", return_value=MagicMock()),
            patch.object(runner_mod, "get_platform_adapter", return_value=MagicMock()),
            patch.object(runner_mod, "_resolve_output_dir_handler", return_value=None),
            patch.object(runner_mod, "_finalize_validation_metadata", return_value=mock_results),
            patch.object(runner_mod, "_enrich_driver_runtime_metadata", return_value=mock_results),
            patch.object(runner_mod, "attach_snapshot_to_result") as mock_attach,
        ):
            from benchbox.core.runner.runner import LifecyclePhases, run_benchmark_lifecycle
            from benchbox.core.schemas import BenchmarkConfig, DatabaseConfig, SystemProfile

            benchmark_config = MagicMock(spec=BenchmarkConfig)
            benchmark_config.name = "tpch"
            benchmark_config.scale_factor = 0.01
            benchmark_config.test_execution_type = "data_only"
            benchmark_config.options = {}

            database_config = MagicMock(spec=DatabaseConfig)
            database_config.type = "duckdb"

            system_profile = MagicMock(spec=SystemProfile)

            phases = LifecyclePhases(generate=False, load=False, execute=False)

            run_benchmark_lifecycle(
                benchmark_config=benchmark_config,
                database_config=database_config,
                system_profile=system_profile,
                platform_config={},
                phases=phases,
            )

        mock_attach.assert_not_called()

    def test_monitor_none_skips_resource_monitoring(self):
        import benchbox.core.runner.runner as runner_mod

        mock_results = MagicMock()
        mock_results.execution_context = None

        with (
            patch.object(runner_mod, "_MONITORING_AVAILABLE", False),
            patch.object(runner_mod, "get_benchmark_instance", return_value=MagicMock()),
            patch.object(runner_mod, "get_platform_adapter", return_value=MagicMock()),
            patch.object(runner_mod, "_resolve_output_dir_handler", return_value=None),
            patch.object(runner_mod, "_finalize_validation_metadata", return_value=mock_results),
            patch.object(runner_mod, "_enrich_driver_runtime_metadata", return_value=mock_results),
            patch.object(runner_mod, "ResourceMonitor") as mock_resource_monitor,
        ):
            from benchbox.core.runner.runner import LifecyclePhases, run_benchmark_lifecycle
            from benchbox.core.schemas import BenchmarkConfig, DatabaseConfig, SystemProfile

            benchmark_config = MagicMock(spec=BenchmarkConfig)
            benchmark_config.name = "tpch"
            benchmark_config.scale_factor = 0.01
            benchmark_config.test_execution_type = "data_only"
            benchmark_config.options = {}

            database_config = MagicMock(spec=DatabaseConfig)
            database_config.type = "duckdb"

            system_profile = MagicMock(spec=SystemProfile)
            phases = LifecyclePhases(generate=False, load=False, execute=False)

            run_benchmark_lifecycle(
                benchmark_config=benchmark_config,
                database_config=database_config,
                system_profile=system_profile,
                platform_config={},
                phases=phases,
            )

        mock_resource_monitor.assert_not_called()


class TestProgressMonitoringGate:
    def test_monitor_is_none_when_monitoring_unavailable(self):
        from rich.console import Console

        import benchbox.cli.progress as progress_mod
        from benchbox.cli.progress import BenchmarkProgress

        console = Console()
        with patch.object(progress_mod, "_MONITORING_AVAILABLE", False):
            progress = BenchmarkProgress(console, enable_monitoring=True)

        assert progress.monitor is None

    def test_monitor_created_when_monitoring_available(self):
        from rich.console import Console

        import benchbox.cli.progress as progress_mod
        from benchbox.cli.progress import BenchmarkProgress

        console = Console()
        with patch.object(progress_mod, "_MONITORING_AVAILABLE", True):
            progress = BenchmarkProgress(console, enable_monitoring=True)

        assert progress.monitor is not None

    def test_monitor_is_none_when_explicitly_disabled(self):
        from rich.console import Console

        import benchbox.cli.progress as progress_mod
        from benchbox.cli.progress import BenchmarkProgress

        console = Console()
        with patch.object(progress_mod, "_MONITORING_AVAILABLE", True):
            progress = BenchmarkProgress(console, enable_monitoring=False)

        assert progress.monitor is None


