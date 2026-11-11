# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import shutil
import tempfile
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


class TestBenchmarkOrchestrator:
    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orchestrator = BenchmarkOrchestrator(base_dir=self.temp_dir)

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_orchestrator_initialization(self, tmp_path: Path):

        base_dir = tmp_path / "benchmark-output"
        orchestrator = BenchmarkOrchestrator(base_dir=base_dir)
        assert orchestrator.directory_manager.base_dir == base_dir
        assert orchestrator.custom_output_dir is None

        orchestrator_default = BenchmarkOrchestrator()
        assert orchestrator_default.directory_manager.base_dir is not None

    def test_set_custom_output_dir(self):

        test_dir = "/custom/output/path"
        self.orchestrator.set_custom_output_dir(test_dir)
        assert self.orchestrator.custom_output_dir == test_dir

    @patch("benchbox.core.run_service.run_benchmark_lifecycle")
    @patch("benchbox.cli.orchestrator.get_platform_adapter")
    @patch("benchbox.cli.orchestrator.console")
    def test_execute_benchmark_standard_mode(self, mock_console, mock_get_adapter, mock_lifecycle):

        config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.01)

        system_profile = Mock(spec=SystemProfile)
        system_profile.cpu_cores_logical = 8
        system_profile.memory_total_gb = 16

        database_config = Mock()
        database_config.type = "duckdb"
        database_config.name = "test_db"
        database_config.options = {"tuning_enabled": True}
        database_config.connection_params = {}
        database_config.model_dump.return_value = {
            "type": "duckdb",
            "name": "test_db",
            "options": {"tuning_enabled": True},
            "connection_params": {},
        }

        mock_benchmark = Mock()
        mock_benchmark._name = "tpch"
        mock_benchmark.scale_factor = 0.01
        mock_benchmark.generate_data.return_value = {"customer": "customer.csv"}

        def mock_create_enhanced_result(platform, query_results, **kwargs):
            from datetime import datetime

            from benchbox.core.results.models import BenchmarkResults

            return BenchmarkResults(
                benchmark_name="TPC-H",
                platform=platform,
                scale_factor=0.01,
                execution_id="test123",
                timestamp=datetime.now(),
                duration_seconds=kwargs.get("duration_seconds", 1.0),
                query_definitions={},
                execution_phases=kwargs.get("phases", {}),
                total_queries=1,
                successful_queries=1,
                failed_queries=0,
                total_execution_time=1.5,
                average_query_time=1.5,
                validation_status="PASSED",
                validation_details={},
            )

        mock_benchmark.create_enhanced_benchmark_result = mock_create_enhanced_result

        mock_adapter = Mock()
        mock_adapter.platform_name = "duckdb"
        mock_adapter.create_connection.return_value = Mock()
        mock_adapter.create_schema.return_value = 0.1
        mock_adapter.load_data.return_value = ({"customer": 1000}, 0.5, None)
        mock_adapter.configure_for_benchmark.return_value = None
        mock_adapter.run_benchmark.return_value = mock_benchmark.create_enhanced_benchmark_result("duckdb", [])

        mock_get_adapter.return_value = mock_adapter

        mock_lifecycle.return_value = mock_benchmark.create_enhanced_benchmark_result("duckdb", [])

        with patch.object(self.orchestrator, "_get_benchmark_instance", return_value=mock_benchmark):
            with patch.object(self.orchestrator, "_get_platform_config", return_value={}):
                result = self.orchestrator.execute_benchmark(config, system_profile, database_config)

        assert isinstance(result, BenchmarkResults)
        assert result.benchmark_name == "TPC-H"
        assert result.validation_status == "PASSED"

        mock_get_adapter.assert_called_once()

    @patch("benchbox.core.run_service.run_benchmark_lifecycle")
    @patch("benchbox.cli.orchestrator.get_platform_adapter")
    def test_execute_benchmark_enriches_driver_metadata_on_canonical_path(self, mock_get_adapter, mock_lifecycle):
        config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.01)
        system_profile = Mock(spec=SystemProfile)
        system_profile.cpu_cores_logical = 8
        system_profile.memory_total_gb = 16

        database_config = DatabaseConfig(
            type="duckdb",
            name="DuckDB",
            driver_package="duckdb",
            driver_version="1.0.0",
            driver_auto_install=True,
        )

        mock_benchmark = Mock()
        mock_benchmark._name = "tpch"
        mock_benchmark.scale_factor = 0.01
        mock_benchmark.generate_data.return_value = {"customer": "customer.csv"}

        mock_result = BenchmarkResults(
            benchmark_name="TPC-H",
            platform="duckdb",
            scale_factor=0.01,
            execution_id="test123",
            timestamp=datetime.now(),
            duration_seconds=1.0,
            query_definitions={},
            total_queries=1,
            successful_queries=1,
            failed_queries=0,
            total_execution_time=1.0,
            average_query_time=1.0,
            validation_status="PASSED",
            validation_details={},
            execution_metadata={},
        )

        mock_adapter = Mock()
        mock_adapter.driver_package = "duckdb"
        mock_adapter.driver_version_requested = "1.1.0"
        mock_adapter.driver_version_resolved = "1.1.0"
        mock_adapter.driver_version_actual = "1.1.0"
        mock_adapter.driver_runtime_strategy = "isolated-site-packages"
        mock_adapter.driver_runtime_path = "/tmp/runtime"
        mock_adapter.driver_runtime_python_executable = "/tmp/runtime/bin/python"
        mock_adapter.driver_auto_install_used = True
        mock_get_adapter.return_value = mock_adapter
        mock_lifecycle.return_value = mock_result

        with patch.object(self.orchestrator, "_get_benchmark_instance", return_value=mock_benchmark):
            result = self.orchestrator.execute_benchmark(config, system_profile, database_config)

        assert result.driver_version_resolved == "1.1.0"
        assert result.driver_runtime_strategy == "isolated-site-packages"
        assert result.execution_metadata["driver_version_resolved"] == "1.1.0"
        assert result.execution_metadata["driver_auto_install_used"] is True

    def test_execute_benchmark_data_only_mode(self):

        config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.01,
            test_execution_type="data_only",
        )

        system_profile = Mock(spec=SystemProfile)
        system_profile.cpu_cores_logical = 4
        system_profile.memory_total_gb = 8

        from benchbox.platforms.base import (
            DataGenerationPhase,
            ExecutionPhases,
            SetupPhase,
            TableGenerationStats,
        )

        setup_phase = SetupPhase(
            data_generation=DataGenerationPhase(
                duration_ms=1500,
                status="SUCCESS",
                tables_generated=8,
                total_rows_generated=100,
                total_data_size_bytes=1024 * 1024,
                per_table_stats={
                    "test_table": TableGenerationStats(
                        generation_time_ms=200,
                        status="SUCCESS",
                        rows_generated=100,
                        data_size_bytes=1024 * 1024,
                        file_path="test.csv",
                    )
                },
            )
        )

        execution_phases = ExecutionPhases(setup=setup_phase)

        mock_enhanced_result = BenchmarkResults(
            benchmark_name="TPC-H",
            platform="data_only",
            scale_factor=0.01,
            execution_id="test-exec-123",
            timestamp=datetime.now(),
            duration_seconds=1.5,
            query_definitions={},
            execution_phases=execution_phases,
            total_queries=1,
            successful_queries=1,
            failed_queries=0,
            total_execution_time=1.5,
            average_query_time=1.5,
            test_execution_type="data_only",
        )

        mock_benchmark = Mock()
        mock_benchmark._name = "tpch"
        mock_benchmark.scale_factor = 0.01
        mock_benchmark.generate_data.return_value = ["file1.csv", "file2.csv"]
        mock_benchmark.create_enhanced_benchmark_result.return_value = mock_enhanced_result

        with patch.object(self.orchestrator, "_get_benchmark_instance", return_value=mock_benchmark):
            with patch("benchbox.cli.orchestrator.console"):
                result = self.orchestrator.execute_benchmark(config, system_profile, None)

        assert isinstance(result, BenchmarkResults)
        assert result.benchmark_name == "TPC-H"

    def test_execute_benchmark_with_error(self):

        config = BenchmarkConfig(name="invalid", display_name="Invalid")
        system_profile = Mock(spec=SystemProfile)
        database_config = Mock()

        with patch.object(self.orchestrator, "_get_benchmark_instance") as mock_get_bench:
            mock_get_bench.side_effect = ValueError("Unknown benchmark: invalid")

            result = self.orchestrator.execute_benchmark(config, system_profile, database_config)

        assert result.validation_status == "FAILED"
        assert "Unknown benchmark: invalid" in result.validation_details["error"]

    def test_get_benchmark_instance_success(self):

        config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.01)
        system_profile = Mock()
        system_profile.cpu_cores_logical = 4

        mock_benchmark_class = Mock()
        mock_instance = Mock()
        mock_benchmark_class.return_value = mock_instance

        with patch.object(self.orchestrator, "_get_benchmark_class", return_value=mock_benchmark_class):
            result = self.orchestrator._get_benchmark_instance(config, system_profile)
            assert result == mock_instance

    def test_get_benchmark_instance_with_compression(self):

        config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.1,
            compress_data=True,
            compression_type="zstd",
            compression_level=9,
        )
        system_profile = Mock()
        system_profile.cpu_cores_logical = 8

        mock_benchmark_class = Mock()
        mock_instance = Mock()
        mock_benchmark_class.return_value = mock_instance

        with patch.object(self.orchestrator, "_get_benchmark_class", return_value=mock_benchmark_class):
            result = self.orchestrator._get_benchmark_instance(config, system_profile)
            assert result == mock_instance

            called_kwargs = mock_benchmark_class.call_args.kwargs
            assert called_kwargs["parallel"] == 8
            assert called_kwargs["scale_factor"] == 0.1
            assert called_kwargs["compress_data"] is True
            assert called_kwargs["compression_type"] == "zstd"
            assert called_kwargs["compression_level"] == 9

    def test_get_benchmark_instance_fallback_no_parallel(self):

        config = BenchmarkConfig(name="read_primitives", display_name="Primitives", scale_factor=1.0)
        system_profile = Mock()
        system_profile.cpu_cores_logical = 4

        class NoParallelBenchmark:
            def __init__(
                self,
                *,
                scale_factor,
                compress_data,
                compression_type,
                compression_level,
                verbose,
                quiet,
            ):
                self.kwargs = {
                    "scale_factor": scale_factor,
                    "compress_data": compress_data,
                    "compression_type": compression_type,
                    "compression_level": compression_level,
                    "verbose": verbose,
                    "quiet": quiet,
                }

        with patch.object(self.orchestrator, "_get_benchmark_class", return_value=NoParallelBenchmark):
            result = self.orchestrator._get_benchmark_instance(config, system_profile)
            assert isinstance(result, NoParallelBenchmark)
            assert result.kwargs["scale_factor"] == 1.0
            assert "parallel" not in result.kwargs

    def test_get_benchmark_instance_unknown_benchmark(self):

        config = BenchmarkConfig(name="unknown", display_name="Unknown")
        system_profile = Mock()

        with pytest.raises(ValueError) as exc_info:
            self.orchestrator._get_benchmark_instance(config, system_profile)

        assert "Benchmark 'unknown' not supported yet" in str(exc_info.value)

    def test_get_benchmark_instance_delegates_to_shared_loader(self):
        config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.01)
        system_profile = Mock()
        mock_instance = Mock()

        with patch("benchbox.cli.orchestrator.get_benchmark_instance", return_value=mock_instance) as mock_get:
            result = self.orchestrator._get_benchmark_instance(config, system_profile)
            assert result == mock_instance
            mock_get.assert_called_once()
            called_kwargs = mock_get.call_args.kwargs
            assert called_kwargs["verbose"] == self.orchestrator._verbosity.level
            assert called_kwargs["quiet"] == self.orchestrator._verbosity.quiet

    def test_get_platform_config(self):

        database_config = Mock()
        database_config.type = "duckdb"
        database_config.name = "test_db"
        database_config.connection_params = {"host": "localhost", "port": 5432}
        database_config.options = {"tuning_enabled": True, "force_recreate": False}
        database_config.model_dump.return_value = {
            "type": "duckdb",
            "name": "test_db",
            "connection_params": {"host": "localhost", "port": 5432},
            "options": {"tuning_enabled": True, "force_recreate": False},
        }

        system_profile = Mock()
        system_profile.memory_total_gb = 32.0
        system_profile.cpu_cores_logical = 16

        config = self.orchestrator._get_platform_config(database_config, system_profile)

        assert config["connection_params"]["host"] == "localhost"
        assert config["connection_params"]["port"] == 5432
        assert config["options"]["tuning_enabled"] is True
        assert config["options"]["force_recreate"] is False
        assert config["memory_limit"] == "16GB"
        assert config["thread_limit"] == 8
        assert "database_path" not in config

    @patch("benchbox.cli.orchestrator.get_adapter")
    def test_build_platform_adapter_forwards_dataframe_platform_options(self, mock_get_adapter):
        database_config = Mock()
        database_config.type = "datafusion"
        database_config.options = {"target_partitions": 4}

        from benchbox.core.runner.runner import LifecyclePhases

        adapter = self.orchestrator._build_platform_adapter(
            database_config,
            execution_mode="dataframe",
            output_root="/tmp/out",
            opts={},
            platform_cfg=None,
            benchmark=None,
            phases=LifecyclePhases(generate=False, load=False, execute=True),
            config=Mock(),
        )

        assert adapter is mock_get_adapter.return_value
        call_kwargs = mock_get_adapter.call_args.kwargs
        assert call_kwargs["target_partitions"] == 4

    @patch("benchbox.cli.orchestrator.get_adapter")
    def test_build_platform_adapter_filters_runtime_overrides_from_dataframe_options(self, mock_get_adapter):
        database_config = Mock()
        database_config.type = "datafusion"
        database_config.options = {
            "target_partitions": 4,
            "verbose": True,
            "very_verbose": True,
            "verbose_enabled": True,
            "verbose_level": 2,
            "quiet": False,
            "tuning_enabled": True,
            "force_recreate": False,
            "force_upload": False,
        }

        from benchbox.core.runner.runner import LifecyclePhases

        adapter = self.orchestrator._build_platform_adapter(
            database_config,
            execution_mode="dataframe",
            output_root="/tmp/out",
            opts={},
            platform_cfg=None,
            benchmark=None,
            phases=LifecyclePhases(generate=False, load=False, execute=True),
            config=Mock(),
        )

        assert adapter is mock_get_adapter.return_value
        call_kwargs = mock_get_adapter.call_args.kwargs
        assert call_kwargs["target_partitions"] == 4
        for runtime_only_key in (
            "tuning_enabled",
            "force_recreate",
            "force_upload",
            "verbose_enabled",
            "verbose_level",
            "quiet",
        ):
            assert runtime_only_key not in call_kwargs

    def test_prepare_run_config(self):

        config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.1,
            queries=["Q1", "Q2", "Q3"],
            concurrency=2,
            options={"unified_tuning_configuration": {"constraints": True}},
        )

        database_config = Mock()
        database_config.type = "duckdb"

        run_config = self.orchestrator._prepare_run_config(config, database_config)

        assert run_config.query_subset == ["Q1", "Q2", "Q3"]
        assert run_config.concurrent_streams == 2
        assert run_config.connection is not None
        assert "database_path" in run_config.connection

    @patch("benchbox.core.run_service.run_benchmark_lifecycle")
    def test_execute_data_only_mode_success(self, mock_lifecycle):

        config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.01, test_execution_type="data_only")
        system_profile = Mock(spec=SystemProfile)

        success = BenchmarkResults(
            benchmark_name="TPC-H",
            platform="data_only",
            scale_factor=0.01,
            execution_id="test",
            timestamp=datetime.now(),
            duration_seconds=1.0,
            query_definitions={},
            execution_phases={},
            total_queries=0,
            successful_queries=0,
            failed_queries=0,
            total_execution_time=0.0,
            average_query_time=0.0,
            test_execution_type="data_only",
        )
        mock_lifecycle.return_value = success

        with patch.object(self.orchestrator, "_get_benchmark_instance", return_value=Mock()):
            result = self.orchestrator.execute_benchmark(config, system_profile, None)
        assert isinstance(result, BenchmarkResults)
        assert result.test_execution_type == "data_only"

    @patch("benchbox.core.run_service.run_benchmark_lifecycle")
    def test_execute_data_only_mode_error(self, mock_lifecycle):

        config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.01, test_execution_type="data_only")
        system_profile = Mock(spec=SystemProfile)

        failure = BenchmarkResults(
            benchmark_name="TPC-H",
            platform="data_only",
            scale_factor=0.01,
            execution_id="test",
            timestamp=datetime.now(),
            duration_seconds=0.0,
            query_definitions={},
            execution_phases={},
            total_queries=0,
            successful_queries=0,
            failed_queries=0,
            total_execution_time=0.0,
            average_query_time=0.0,
            test_execution_type="data_only",
            validation_status="FAILED",
            validation_details={"error": "Data generation failed"},
        )
        mock_lifecycle.return_value = failure

        with patch.object(self.orchestrator, "_get_benchmark_instance", return_value=Mock()):
            result = self.orchestrator.execute_benchmark(config, system_profile, None)
        assert isinstance(result, BenchmarkResults)
        assert result.validation_status == "FAILED"


class TestBenchmarkOrchestratorIntegration:
    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orchestrator = BenchmarkOrchestrator(base_dir=self.temp_dir)

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)


class TestBenchmarkOrchestratorErrorHandling:
    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orchestrator = BenchmarkOrchestrator(base_dir=self.temp_dir)

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_singlestore_missing_credentials_offers_guided_setup(self):
        database_config = Mock(type="singlestore")

        assert self.orchestrator._should_offer_credential_setup(
            database_config, RuntimeError("configuration requires username and password")
        )

    def test_platform_config_without_system_profile(self):

        database_config = Mock()
        database_config.type = "duckdb"
        database_config.name = "test"
        database_config.connection_params = {"host": "localhost"}
        database_config.options = {"tuning_enabled": True}
        database_config.model_dump.return_value = {
            "type": "duckdb",
            "name": "test",
            "connection_params": {"host": "localhost"},
            "options": {"tuning_enabled": True},
        }

        config = self.orchestrator._get_platform_config(database_config, None)

        assert config["connection_params"]["host"] == "localhost"
        assert config["options"]["tuning_enabled"] is True
        assert "database_path" not in config
        assert "memory_limit" not in config
        assert "thread_limit" not in config

    def test_platform_config_without_options(self):

        database_config = Mock()
        database_config.type = "sqlite"
        database_config.name = "test"
        database_config.connection_params = {}
        database_config.options = None
        database_config.model_dump.return_value = {
            "type": "sqlite",
            "name": "test",
            "connection_params": {},
            "options": None,
        }

        system_profile = Mock()
        system_profile.memory_total_gb = 8.0
        system_profile.cpu_cores_logical = 4

        config = self.orchestrator._get_platform_config(database_config, system_profile)

        assert config["memory_limit"] == "6GB"
        assert config["thread_limit"] == 4
        assert "database_path" not in config

    def test_system_profile_attribute_mapping(self):
        database_config = Mock()
        database_config.connection_params = {}
        database_config.options = {}
        database_config.type = "duckdb"
        database_config.name = "test"

        database_config.model_dump.return_value = {
            "type": "duckdb",
            "name": "test",
            "connection_params": {},
            "options": {},
        }

        system_profile = Mock()
        system_profile.memory_total_gb = 16.0
        system_profile.cpu_cores_logical = 10

        config = self.orchestrator._get_platform_config(database_config, system_profile)

        assert config["memory_limit"] == "12GB"

        assert config["thread_limit"] == 8

        system_profile.memory_total_gb = 32.0
        config_high_mem = self.orchestrator._get_platform_config(database_config, system_profile)
        assert config_high_mem["memory_limit"] == "16GB"

        system_profile.cpu_cores_logical = 32
        config_high_cpu = self.orchestrator._get_platform_config(database_config, system_profile)
        assert config_high_cpu["thread_limit"] == 8

    def test_system_profile_fallback_values(self):
        database_config = Mock()
        database_config.type = "duckdb"
        database_config.name = "test"
        database_config.connection_params = {}
        database_config.options = {}
        database_config.model_dump.return_value = {
            "type": "duckdb",
            "name": "test",
            "connection_params": {},
            "options": {},
        }

        class EmptyProfile:
            pass

        system_profile = EmptyProfile()

        config = self.orchestrator._get_platform_config(database_config, system_profile)

        assert config["memory_limit"] == "3GB"
        assert config["thread_limit"] == 2

    def test_prepare_run_config_minimal(self):

        config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=1.0)
        database_config = Mock()
        database_config.type = "duckdb"

        run_config = self.orchestrator._prepare_run_config(config, database_config)

        assert run_config.connection is not None
        assert run_config.query_subset is None or run_config.query_subset == []


if __name__ == "__main__":
    pytest.main([__file__])


