from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox.cli.orchestrator import BenchmarkOrchestrator
from benchbox.core.results.models import BenchmarkResults
from benchbox.core.schemas import BenchmarkConfig, DatabaseConfig, SystemProfile

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _mk_system_profile():
    return SystemProfile(
        os_name="Linux",
        os_version="6.6",
        architecture="x86_64",
        cpu_model="Test CPU",
        cpu_cores_physical=4,
        cpu_cores_logical=8,
        memory_total_gb=16.0,
        memory_available_gb=12.0,
        python_version="3.11",
        disk_space_gb=256.0,
        timestamp=datetime.now(),
        hostname="host",
    )


class TestBenchmarkOrchestratorValidation:
    def setup_method(self):
        self.orchestrator = BenchmarkOrchestrator()

    def _build_config(self, **options) -> BenchmarkConfig:
        return BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=1.0,
            compress_data=False,
            compression_type="none",
            compression_level=None,
            test_execution_type="standard",
            options=options,
        )

    def test_execute_benchmark_passes_validation_flags(self, tmp_path):
        config = self._build_config(
            enable_preflight_validation=True,
            enable_postgen_manifest_validation=True,
            enable_postload_validation=True,
        )
        db_config = DatabaseConfig(type="duckdb", name="duckdb")
        bench_instance = Mock()
        bench_instance._name = "TPC-H"
        bench_instance.scale_factor = 1.0
        bench_instance.get_data_source_benchmark = Mock(return_value=None)

        with (
            patch.object(
                self.orchestrator.directory_manager,
                "get_datagen_path",
                return_value=Path(tmp_path / "tpch"),
            ),
            patch.object(
                self.orchestrator,
                "_get_benchmark_instance",
                return_value=bench_instance,
            ),
            patch("benchbox.cli.orchestrator.get_platform_adapter", return_value=Mock()) as mock_adapter,
            patch("benchbox.core.run_service.run_benchmark_lifecycle") as mock_run,
        ):
            mock_result = Mock(spec=BenchmarkResults)
            mock_run.return_value = mock_result

            result = self.orchestrator.execute_benchmark(config, _mk_system_profile(), db_config)

        assert result is mock_result
        mock_adapter.assert_called_once()
        _, kwargs = mock_run.call_args
        validation_opts = kwargs["validation_opts"]
        assert validation_opts.enable_preflight_validation is True
        assert validation_opts.enable_postgen_manifest_validation is True
        assert validation_opts.enable_postload_validation is True
        assert kwargs["output_root"].endswith("tpch")

    def test_execute_benchmark_handles_exception(self, tmp_path):
        config = self._build_config()
        db_config = DatabaseConfig(type="duckdb", name="duckdb")

        with (
            patch.object(
                self.orchestrator.directory_manager,
                "get_datagen_path",
                return_value=Path(tmp_path / "tpch"),
            ),
            patch.object(
                self.orchestrator,
                "_get_benchmark_instance",
                side_effect=RuntimeError("boom"),
            ),
        ):
            result = self.orchestrator.execute_benchmark(config, _mk_system_profile(), db_config)

        assert isinstance(result, BenchmarkResults)
        assert result.validation_status == "FAILED"
        assert "boom" in result.validation_details.get("error", "")

    def test_execute_benchmark_without_database_does_not_create_adapter(self, tmp_path):
        config = self._build_config()

        bench_instance = Mock()
        bench_instance._name = "TPC-H"
        bench_instance.get_data_source_benchmark = Mock(return_value=None)

        with (
            patch.object(
                self.orchestrator.directory_manager,
                "get_datagen_path",
                return_value=Path(tmp_path / "tpch"),
            ),
            patch.object(
                self.orchestrator,
                "_get_benchmark_instance",
                return_value=bench_instance,
            ),
            patch("benchbox.cli.orchestrator.get_platform_adapter") as mock_adapter,
            patch("benchbox.core.run_service.run_benchmark_lifecycle") as mock_run,
        ):
            mock_run.return_value = Mock(spec=BenchmarkResults)
            self.orchestrator.execute_benchmark(config, _mk_system_profile(), database_config=None)

        mock_adapter.assert_not_called()

    def test_execute_benchmark_maps_data_only_phases(self, tmp_path):
        config = self._build_config()
        bench_instance = Mock()
        bench_instance._name = "TPC-H"
        bench_instance.get_data_source_benchmark = Mock(return_value=None)

        with (
            patch.object(
                self.orchestrator.directory_manager,
                "get_datagen_path",
                return_value=Path(tmp_path / "tpch"),
            ),
            patch.object(
                self.orchestrator,
                "_get_benchmark_instance",
                return_value=bench_instance,
            ),
            patch("benchbox.core.run_service.run_benchmark_lifecycle") as mock_run,
        ):
            mock_run.return_value = Mock(spec=BenchmarkResults)
            result = self.orchestrator.execute_benchmark(
                config,
                _mk_system_profile(),
                database_config=None,
                phases_to_run=["generate"],
            )

        assert result is mock_run.return_value
        _, kwargs = mock_run.call_args
        phases = kwargs["phases"]
        assert phases.generate is True
        assert phases.load is False
        assert phases.execute is False
        assert kwargs["platform_adapter"] is None
        assert kwargs["validation_opts"].enable_postgen_manifest_validation is False

    def test_execute_benchmark_maps_load_only_phases(self, tmp_path):
        config = self._build_config(enable_postload_validation=True)
        db_config = DatabaseConfig(type="duckdb", name="duckdb")
        bench_instance = Mock()
        bench_instance._name = "TPC-H"
        bench_instance.get_data_source_benchmark = Mock(return_value=None)

        adapter = Mock()

        with (
            patch.object(
                self.orchestrator.directory_manager,
                "get_datagen_path",
                return_value=Path(tmp_path / "tpch"),
            ),
            patch.object(
                self.orchestrator,
                "_get_benchmark_instance",
                return_value=bench_instance,
            ),
            patch("benchbox.cli.orchestrator.get_platform_adapter", return_value=adapter) as mock_get_adapter,
            patch("benchbox.core.run_service.run_benchmark_lifecycle") as mock_run,
        ):
            mock_run.return_value = Mock(spec=BenchmarkResults)
            result = self.orchestrator.execute_benchmark(
                config,
                _mk_system_profile(),
                database_config=db_config,
                phases_to_run=["load"],
            )

        assert result is mock_run.return_value
        mock_get_adapter.assert_called_once()
        _, kwargs = mock_run.call_args
        phases = kwargs["phases"]
        assert phases.generate is False
        assert phases.load is True
        assert phases.execute is False
        assert kwargs["platform_adapter"] is adapter
        validation_opts = kwargs["validation_opts"]
        assert validation_opts.enable_postload_validation is True


