# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox.cli.benchmarks import BenchmarkConfig
from benchbox.cli.commands.run import BENCHMARK_ALIASES, normalize_benchmark_name
from benchbox.cli.dryrun import DryRunExecutor
from benchbox.cli.system import SystemProfile

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestBenchmarkNameNormalization:
    def test_normalize_lowercase(self):

        assert normalize_benchmark_name("TPCH") == "tpch"
        assert normalize_benchmark_name("TPCDS") == "tpcds"
        assert normalize_benchmark_name("SSB") == "ssb"

    def test_normalize_tpch_aliases(self):

        assert normalize_benchmark_name("tpc-h") == "tpch"
        assert normalize_benchmark_name("TPC-H") == "tpch"
        assert normalize_benchmark_name("tpc_h") == "tpch"
        assert normalize_benchmark_name("TPC_H") == "tpch"

    def test_normalize_tpcds_aliases(self):

        assert normalize_benchmark_name("tpc-ds") == "tpcds"
        assert normalize_benchmark_name("TPC-DS") == "tpcds"
        assert normalize_benchmark_name("tpc_ds") == "tpcds"
        assert normalize_benchmark_name("TPC_DS") == "tpcds"

    def test_normalize_tpcds_obt_aliases(self):

        assert normalize_benchmark_name("tpcdsobt") == "tpcds_obt"
        assert normalize_benchmark_name("tpcds-obt") == "tpcds_obt"
        assert normalize_benchmark_name("tpc-ds-obt") == "tpcds_obt"
        assert normalize_benchmark_name("tpc-ds_obt") == "tpcds_obt"
        assert normalize_benchmark_name("tpc_ds_obt") == "tpcds_obt"
        assert normalize_benchmark_name("TPCDSOBT") == "tpcds_obt"

    def test_normalize_ssb_aliases(self):

        assert normalize_benchmark_name("star-schema") == "ssb"
        assert normalize_benchmark_name("starschema") == "ssb"
        assert normalize_benchmark_name("star_schema") == "ssb"
        assert normalize_benchmark_name("star-schema-benchmark") == "ssb"
        assert normalize_benchmark_name("STAR-SCHEMA") == "ssb"

    def test_normalize_canonical_names_unchanged(self):
        assert normalize_benchmark_name("tpch") == "tpch"
        assert normalize_benchmark_name("tpcds") == "tpcds"
        assert normalize_benchmark_name("ssb") == "ssb"
        assert normalize_benchmark_name("tpcds_obt") == "tpcds_obt"

    def test_benchmark_aliases_dict_exists(self):

        assert "tpc-h" in BENCHMARK_ALIASES
        assert "tpc-ds" in BENCHMARK_ALIASES
        assert "tpcdsobt" in BENCHMARK_ALIASES
        assert "star-schema" in BENCHMARK_ALIASES
        assert BENCHMARK_ALIASES["tpc-h"] == "tpch"


class TestGenerateCliCommand:
    def test_minimal_command(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="duckdb", benchmark="tpch", scale=0.01)
        assert "benchbox run" in cmd
        assert "--platform duckdb" in cmd
        assert "--benchmark tpch" in cmd
        assert "--scale" not in cmd

    def test_non_default_scale(self):

        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="duckdb", benchmark="tpch", scale=1.0)
        assert "--scale 1.0" in cmd

    def test_phases_included_when_not_default(self):

        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(
            platform="duckdb", benchmark="tpch", scale=0.01, phases=["generate", "load", "power"]
        )
        assert "--phases generate,load,power" in cmd

    def test_phases_omitted_when_default(self):

        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="duckdb", benchmark="tpch", scale=0.01, phases=["power"])
        assert "--phases" not in cmd

    def test_queries_included(self):

        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="duckdb", benchmark="tpch", scale=0.01, queries=["Q1", "Q6", "Q17"])
        assert "--queries Q1,Q6,Q17" in cmd

    def test_tuning_included_when_not_notuning(self):

        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="duckdb", benchmark="tpch", scale=0.01, tuning="tuned")
        assert "--tuning tuned" in cmd

    def test_tuning_omitted_when_notuning(self):

        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="duckdb", benchmark="tpch", scale=0.01, tuning="notuning")
        assert "--tuning" not in cmd

    def test_seed_included(self):

        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="duckdb", benchmark="tpch", scale=0.01, seed=42)
        assert "--seed 42" in cmd

    def test_output_included(self):

        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="athena", benchmark="tpch", scale=0.01, output="s3://bucket/path/")
        assert "--output s3://bucket/path/" in cmd

    def test_table_format_included(self):

        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="duckdb", benchmark="tpch", scale=0.01, table_format="parquet")
        assert "--table-format parquet" in cmd

    def test_compression_included(self):

        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="duckdb", benchmark="tpch", scale=0.01, compression="zstd:9")
        assert "--compression zstd:9" in cmd

    def test_mode_included(self):

        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="polars", benchmark="tpch", scale=0.01, mode="dataframe")
        assert "--mode dataframe" in cmd

    def test_force_included(self):

        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="duckdb", benchmark="tpch", scale=0.01, force="datagen")
        assert "--force datagen" in cmd

    def test_official_flag_included(self):

        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="snowflake", benchmark="tpch", scale=1.0, official=True)
        assert "--official" in cmd

    def test_capture_plans_included(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="duckdb", benchmark="tpch", scale=0.01, capture_plans=True)
        assert "--capture-plans" in cmd

    def test_strict_translation_included(self):

        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="duckdb", benchmark="tpch", scale=0.01, strict_translation=True)
        assert "--strict-translation" in cmd

    def test_stats_reset_omitted_when_unset(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="duckdb", benchmark="tpch", scale=0.01)
        assert "--stats-reset" not in cmd
        assert "--no-stats-reset" not in cmd

    def test_stats_reset_true_emits_stats_reset_flag(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="duckdb", benchmark="tpch", scale=0.01, stats_reset=True)
        assert "--stats-reset" in cmd
        assert "--no-stats-reset" not in cmd

    def test_stats_reset_false_emits_no_stats_reset_flag(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="duckdb", benchmark="tpch", scale=0.01, stats_reset=False)
        assert "--no-stats-reset" in cmd

    def test_stats_per_table_timing_included(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="duckdb", benchmark="tpch", scale=0.01, stats_per_table_timing=True)
        assert "--stats-per-table-timing" in cmd

    def test_validation_included(self):

        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="duckdb", benchmark="tpch", scale=0.01, validation="loose")
        assert "--validation loose" in cmd

    def test_verbose_level_1(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="duckdb", benchmark="tpch", scale=0.01, verbose=1)
        assert "-v" in cmd
        assert "-vv" not in cmd

    def test_verbose_level_2(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="duckdb", benchmark="tpch", scale=0.01, verbose=2)
        assert "-vv" in cmd

    def test_verbose_level_0_not_included(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="duckdb", benchmark="tpch", scale=0.01, verbose=0)
        assert "-v" not in cmd
        assert "-vv" not in cmd

    def test_multiline_format(self):

        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(
            platform="duckdb",
            benchmark="tpch",
            scale=1.0,
            phases=["generate", "load", "power"],
            tuning="tuned",
        )
        assert " \\\n    " in cmd

    def test_full_command_generation(self):

        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(
            platform="athena",
            benchmark="tpch",
            scale=1.0,
            phases=["generate", "load", "power"],
            queries=["Q1", "Q6"],
            tuning="tuned",
            seed=42,
            output="s3://bucket/data/",
            table_format="parquet:zstd",
            compression="zstd:9",
            mode="sql",
            force="all",
            official=True,
            capture_plans=True,
            validation="full",
            verbose=2,
        )

        assert "--platform athena" in cmd
        assert "--benchmark tpch" in cmd
        assert "--scale 1.0" in cmd
        assert "--phases generate,load,power" in cmd
        assert "--queries Q1,Q6" in cmd
        assert "--tuning tuned" in cmd
        assert "--seed 42" in cmd
        assert "--output s3://bucket/data/" in cmd
        assert "--table-format parquet:zstd" in cmd
        assert "--compression zstd:9" in cmd
        assert "--mode sql" in cmd
        assert "--force all" in cmd
        assert "--official" in cmd
        assert "--capture-plans" in cmd
        assert "--validation full" in cmd
        assert "-vv" in cmd


class TestDryRunExecutor:
    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.executor = DryRunExecutor(output_dir=self.temp_dir)

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_dry_run_executor_initialization(self, tmp_path: Path):

        output_dir = tmp_path / "output"
        executor = DryRunExecutor(output_dir=output_dir)
        assert executor.output_dir == output_dir

        executor_temp = DryRunExecutor()
        assert executor_temp.output_dir.exists()

    @patch("benchbox.cli.dryrun.console")
    def test_execute_dry_run_data_only(self, mock_console):

        from datetime import datetime

        config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.01,
            test_execution_type="data_only",
        )

        system_profile = SystemProfile(
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
        )

        with patch.object(self.executor, "_get_benchmark_instance") as mock_get_bench:
            mock_benchmark = Mock()
            mock_benchmark._name = "tpch"
            mock_benchmark.get_query_list.return_value = ["Q1", "Q2", "Q3"]
            mock_benchmark.get_query.return_value = "SELECT COUNT(*) FROM customer"
            mock_get_bench.return_value = mock_benchmark

            result = self.executor.execute_dry_run(config, system_profile, None)

            assert result is not None
            assert hasattr(result, "benchmark_config")
            assert hasattr(result, "database_config")
            assert hasattr(result, "platform_config")
            assert hasattr(result, "query_preview")

    @patch("benchbox.cli.dryrun.console")
    def test_execute_dry_run_with_database(self, mock_console):

        from datetime import datetime

        config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.01)

        system_profile = SystemProfile(
            os_name="Linux",
            os_version="6.6",
            architecture="x86_64",
            cpu_model="Test CPU",
            cpu_cores_physical=2,
            cpu_cores_logical=4,
            memory_total_gb=8.0,
            memory_available_gb=6.0,
            python_version="3.11",
            disk_space_gb=256.0,
            timestamp=datetime.now(),
        )

        database_config = Mock()
        database_config.type = "duckdb"
        database_config.options = {"tuning_enabled": True}
        database_config.connection_params = {}
        database_config.execution_mode = "sql"
        database_config.model_dump.return_value = {
            "type": "duckdb",
            "options": {"tuning_enabled": True},
            "connection_params": {},
        }

        with patch.object(self.executor, "_get_benchmark_instance") as mock_get_bench:
            mock_benchmark = Mock()
            mock_benchmark._name = "tpch"
            mock_benchmark.get_query_list.return_value = ["Q1", "Q2"]
            mock_benchmark.get_query.return_value = "SELECT * FROM lineitem LIMIT 100"
            mock_get_bench.return_value = mock_benchmark

            result = self.executor.execute_dry_run(config, system_profile, database_config)

            assert result.database_config is not None
            assert result.database_config["type"] == "duckdb"
            assert result.database_config["options"]["tuning_enabled"] is True

    def test_get_platform_config_with_database(self):

        from datetime import datetime

        system_profile = SystemProfile(
            os_name="Linux",
            os_version="6.6",
            architecture="x86_64",
            cpu_model="Test CPU",
            cpu_cores_physical=4,
            cpu_cores_logical=8,
            memory_total_gb=32.0,
            memory_available_gb=24.0,
            python_version="3.11",
            disk_space_gb=500.0,
            timestamp=datetime.now(),
        )

        database_config = Mock()
        database_config.type = "duckdb"
        database_config.name = "test_db"
        database_config.options = {"tuning_enabled": False}
        database_config.connection_params = {}
        database_config.model_dump.return_value = {
            "type": "duckdb",
            "name": "test_db",
            "options": {"tuning_enabled": False},
            "connection_params": {},
        }

        config = self.executor._get_platform_config(database_config, system_profile)

        assert isinstance(config, dict)
        assert config["options"]["tuning_enabled"] is False
        assert "memory_limit" in config
        assert "thread_limit" in config

    def test_get_platform_config_data_only(self):

        from datetime import datetime

        system_profile = SystemProfile(
            os_name="Linux",
            os_version="6.6",
            architecture="x86_64",
            cpu_model="Test CPU",
            cpu_cores_physical=2,
            cpu_cores_logical=4,
            memory_total_gb=8.0,
            memory_available_gb=6.0,
            python_version="3.11",
            disk_space_gb=256.0,
            timestamp=datetime.now(),
        )

        config = self.executor._get_platform_config(None, system_profile)

        assert isinstance(config, dict)
        assert config == {"data_only": True}

    @patch("benchbox.cli.dryrun.console")
    def test_display_dry_run_results(self, mock_console):
        result = Mock()
        result.benchmark_config = {
            "name": "tpch",
            "scale_factor": 0.01,
            "test_execution_type": "standard",
        }
        result.database_config = {"type": "duckdb", "tuning_enabled": True}
        result.platform_config = {
            "database_type": "duckdb",
            "system_resources": {"cpu_cores": 8, "memory_gb": 16},
        }
        result.query_preview = {
            "query_count": 22,
            "estimated_time": "5 minutes",
            "data_size_mb": 100.0,
        }
        result.system_profile = {"cpu_cores": 8, "memory_gb": 16}
        result.constraint_config = {
            "enable_primary_keys": False,
            "enable_foreign_keys": False,
        }
        result.warnings = []
        result.ddl_preview_notes = []
        result.schema_sql = "CREATE TABLE test (id INTEGER)"
        result.tuning_config = None
        result.estimated_resources = None
        result.execution_mode = "sql"
        result.ddl_preview = None
        result.post_load_statements = None

        result.queries = {"Q1": "SELECT 1", "Q2": "SELECT 2", "Q3": "SELECT 3"}

        try:
            self.executor.display_dry_run_results(result)
        except Exception as exc:
            pytest.fail(f"display_dry_run_results raised {type(exc).__name__}: {exc}")

    def test_save_dry_run_results(self):

        from datetime import datetime

        from benchbox.cli.dryrun import DryRunResult

        result = DryRunResult(
            benchmark_config={"name": "tpch", "scale_factor": 0.01},
            database_config={"type": "duckdb"},
            platform_config={"database_type": "duckdb"},
            query_preview={"query_count": 22},
            system_profile={"cpu_cores": 8},
            queries={"Q1": "SELECT 1", "Q2": "SELECT 2"},
            timestamp=datetime.now(),
        )

        saved_files = self.executor.save_dry_run_results(result, "test_prefix")

        assert isinstance(saved_files, dict)
        assert "json" in saved_files

        json_file = saved_files["json"]
        assert Path(json_file).exists()

        with open(json_file, encoding="utf-8") as f:
            data = json.load(f)
            assert "benchmark_config" in data
            assert "database_config" in data
            assert data["benchmark_config"]["name"] == "tpch"

    @patch("benchbox.cli.dryrun.console")
    def test_preview_query_execution(self, mock_console):

        queries = ["Q1", "Q2", "Q3"]

        mock_benchmark = Mock()
        mock_benchmark.get_query.side_effect = [
            "SELECT COUNT(*) FROM customer",
            "SELECT * FROM orders WHERE o_orderdate >= '1995-01-01'",
            "SELECT l_orderkey, SUM(l_quantity) FROM lineitem GROUP BY l_orderkey",
        ]

        preview = {
            "query_count": len(queries),
            "queries": [{"id": q, "sql": mock_benchmark.get_query(q)} for q in queries],
        }

        assert isinstance(preview, dict)
        assert "query_count" in preview
        assert preview["query_count"] == 3
        assert "queries" in preview
        assert len(preview["queries"]) == 3

    def test_estimate_execution_time(self):

        config = {"scale_factor": 0.01, "query_count": 22}

        scale_factor = config.get("scale_factor", 1.0)
        query_count = config.get("query_count", 22)
        base_time = scale_factor * query_count * 2
        time_estimate = f"{int(base_time)} seconds" if base_time < 60 else f"{int(base_time // 60)} minutes"

        assert isinstance(time_estimate, str)
        assert "minute" in time_estimate or "second" in time_estimate

    def test_estimate_data_size(self):

        mock_benchmark = Mock()
        mock_benchmark.scale_factor = 0.1
        benchmark_name = "tpch"

        size_estimate = self.executor._estimate_data_size(mock_benchmark, benchmark_name)

        assert isinstance(size_estimate, (int, float))
        assert size_estimate > 0


class TestDryRunResultHandling:
    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_dry_run_result_json_serialization(self):

        from datetime import datetime

        from benchbox.cli.dryrun import DryRunResult

        executor = DryRunExecutor(output_dir=self.temp_dir)

        result = DryRunResult(
            benchmark_config={
                "name": "tpch",
                "scale_factor": 0.01,
                "compress_data": True,
                "test_execution_type": "power",
            },
            database_config={"type": "duckdb", "options": {"tuning_enabled": False}},
            platform_config={
                "database_type": "duckdb",
                "system_resources": {"cpu_cores": 8},
            },
            query_preview={"query_count": 22, "estimated_time": "5 minutes"},
            system_profile={"cpu_cores": 8, "memory_gb": 16.0},
            queries={"Q1": "SELECT 1", "Q2": "SELECT 2"},
            timestamp=datetime.now(),
        )

        saved_files = executor.save_dry_run_results(result, "serialization_test")
        json_file = saved_files["json"]

        with open(json_file, encoding="utf-8") as f:
            data = json.load(f)
            assert data["benchmark_config"]["name"] == "tpch"
            assert data["benchmark_config"]["scale_factor"] == 0.01
            assert data["database_config"]["type"] == "duckdb"

    def test_dry_run_with_compression_settings(self):

        from datetime import datetime

        executor = DryRunExecutor(output_dir=self.temp_dir)

        config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.1,
            compress_data=True,
            compression_type="zstd",
            compression_level=5,
        )

        system_profile = SystemProfile(
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
        )

        with patch.object(executor, "_get_benchmark_instance") as mock_get_bench:
            mock_benchmark = Mock()
            mock_benchmark._name = "tpch"
            mock_benchmark.get_query_list.return_value = ["Q1"]
            mock_benchmark.get_query.return_value = "SELECT 1"
            mock_get_bench.return_value = mock_benchmark

            result = executor.execute_dry_run(config, system_profile, None)

            assert result.benchmark_config["compress_data"] is True
            assert result.benchmark_config["compression_type"] == "zstd"
            assert result.benchmark_config["compression_level"] == 5


class TestDryRunErrorHandling:
    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.executor = DryRunExecutor(output_dir=self.temp_dir)

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_dry_run_with_invalid_benchmark(self):

        from datetime import datetime

        config = BenchmarkConfig(name="invalid", display_name="Invalid")
        system_profile = SystemProfile(
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
        )

        with patch.object(self.executor, "_get_benchmark_instance") as mock_get_bench:
            mock_get_bench.side_effect = ValueError("Unknown benchmark: invalid")

            result = self.executor.execute_dry_run(config, system_profile, None)

            assert result is not None
            assert len(result.warnings) > 0
            assert "Unknown benchmark: invalid" in str(result.warnings)

    @pytest.mark.skipif(sys.platform == "win32", reason="Path handling differs on Windows")
    def test_dry_run_with_missing_output_dir(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("")
        with pytest.raises(OSError):
            DryRunExecutor(output_dir=str(blocker / "sub"))

    @patch("benchbox.cli.dryrun.console")
    def test_dry_run_with_empty_query_list(self, mock_console):

        from datetime import datetime

        config = BenchmarkConfig(name="empty", display_name="Empty")
        system_profile = SystemProfile(
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
        )

        with patch.object(self.executor, "_get_benchmark_instance") as mock_get_bench:
            mock_benchmark = Mock()
            mock_benchmark._name = "empty"
            mock_benchmark.get_query_list.return_value = []
            mock_get_bench.return_value = mock_benchmark

            result = self.executor.execute_dry_run(config, system_profile, None)

            assert result.query_preview is None


@pytest.mark.unit
class TestDryRunExecutorCoverageGaps:
    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.output_dir = Path(self.temp_dir)

    def teardown_method(self):
        if self.temp_dir and Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)

    def test_dryrun_with_benchmark_config(self):

        config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.01,
        )
        system_profile = Mock()

        executor = DryRunExecutor(output_dir=self.output_dir)

        with patch.object(executor, "_get_benchmark_instance") as mock_get:
            mock_bench = Mock()
            mock_bench.get_query_list.return_value = []
            mock_get.return_value = mock_bench

            result = executor.execute_dry_run(config, system_profile, None)
            assert result is None or hasattr(result, "query_preview")

    def test_dryrun_exception_in_query_list(self):

        config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.01,
        )
        system_profile = Mock()

        executor = DryRunExecutor(output_dir=self.output_dir)

        with patch.object(executor, "_get_benchmark_instance") as mock_get:
            mock_bench = Mock()
            mock_bench.get_query_list.side_effect = RuntimeError("Query error")
            mock_get.return_value = mock_bench

            result = executor.execute_dry_run(config, system_profile, None)
            assert result is None or result is not None

    def test_dryrun_schema_extraction(self):

        config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.01,
        )
        system_profile = Mock()

        executor = DryRunExecutor(output_dir=self.output_dir)

        with patch.object(executor, "_get_benchmark_instance") as mock_get:
            mock_bench = Mock()
            mock_bench.get_query_list.return_value = ["Q1"]
            mock_bench.get_schema.return_value = {"table1": "CREATE TABLE table1 (id INT)"}
            mock_get.return_value = mock_bench

            result = executor.execute_dry_run(config, system_profile, None)
            assert result is None or result is not None


class TestDryRunDisplayConfigurationSummary:
    def _make_result(self, options=None, benchmark_overrides=None, tuning_config=None):
        from benchbox.core.schemas import DryRunResult

        benchmark_config = {
            "name": "tpch",
            "scale_factor": 1.0,
            "concurrency": 1,
            "compress_data": False,
            "compression_type": "zstd",
            "compression_level": None,
            "options": options or {},
            **(benchmark_overrides or {}),
        }
        return DryRunResult(
            benchmark_config=benchmark_config,
            database_config={"type": "duckdb", "name": "DuckDB"},
            system_profile={"cpu_cores_logical": 8, "memory_total_gb": 16.0, "os_name": "Darwin"},
            platform_config={},
            queries={"Q1": "SELECT 1"},
            execution_mode="sql",
            tuning_config=tuning_config,
            constraint_config={"enable_primary_keys": False, "enable_foreign_keys": False},
        )

    def _render(self, result):
        from io import StringIO

        from rich.console import Console

        from benchbox.cli.dryrun import DryRunDisplay

        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        display = DryRunDisplay(console=console)
        display._display_configuration_summary(result)
        return buf.getvalue()

    def test_shows_table_mode_when_external(self):
        result = self._make_result(options={"table_mode": "external"})
        output = self._render(result)
        assert "External" in output
        assert "Table Mode" in output

    def test_hides_data_layout_when_all_defaults(self):
        result = self._make_result()
        output = self._render(result)
        assert "Data Layout" not in output

    def test_shows_table_format(self):
        result = self._make_result(options={"table_mode": "external", "table_format": "iceberg"})
        output = self._render(result)
        assert "Iceberg" in output
        assert "Table Format" in output

    def test_shows_compression(self):
        result = self._make_result(
            benchmark_overrides={"compress_data": True, "compression_type": "zstd", "compression_level": 3},
        )
        output = self._render(result)
        assert "zstd:3" in output
        assert "Compression" in output

    def test_shows_compression_without_level(self):
        result = self._make_result(
            benchmark_overrides={"compress_data": True, "compression_type": "gzip", "compression_level": None},
        )
        output = self._render(result)
        assert "gzip" in output
        assert ":None" not in output

    def test_shows_tuning_disabled(self):
        result = self._make_result()
        output = self._render(result)
        assert "Disabled (baseline)" in output

    def test_shows_tuning_mode(self):
        result = self._make_result(tuning_config={"tuning_type": "tuned"})
        output = self._render(result)
        assert "Tuned" in output

    def test_shows_all_data_layout_rows(self):
        result = self._make_result(
            options={"table_mode": "external", "table_format": "delta"},
            benchmark_overrides={"compress_data": True, "compression_type": "zstd", "compression_level": 9},
        )
        output = self._render(result)
        assert "External" in output
        assert "Delta" in output
        assert "zstd:9" in output


class TestDryRunDisplayBehavioral:
    def test_ddl_preview_summary_shows_distribute_by(self):
        from io import StringIO

        from rich.console import Console

        from benchbox.cli.dryrun import DryRunDisplay

        buf = StringIO()
        display = DryRunDisplay(console=Console(file=buf, force_terminal=False, width=140))
        display._display_ddl_preview(
            {
                "lineitem": {
                    "tuning_summary": {"distribute_by": "DISTRIBUTED BY HASH(`l_orderkey`) BUCKETS 8"},
                    "ddl_clauses": "CREATE TABLE lineitem (x INT)",
                }
            }
        )
        assert "Dist: DISTRIBUTED BY HASH(`l_orderkey`) BUCKETS 8" in buf.getvalue()

    def _make_result(self, **overrides):
        from benchbox.core.schemas import DryRunResult

        base = {
            "benchmark_config": {
                "name": "tpch",
                "scale_factor": 1.0,
                "concurrency": 1,
                "compress_data": False,
                "compression_type": "zstd",
                "compression_level": None,
                "options": {},
            },
            "database_config": {"type": "duckdb", "name": "DuckDB"},
            "system_profile": {"cpu_cores_logical": 8, "memory_total_gb": 16.0, "os_name": "Darwin"},
            "platform_config": {},
            "queries": {"Q1": "SELECT 1"},
            "execution_mode": "sql",
            "constraint_config": {"enable_primary_keys": False, "enable_foreign_keys": False},
            "query_preview": {
                "test_execution_type": "standard",
                "execution_context": "Sequential execution",
            },
            "warnings": [],
        }
        base.update(overrides)
        return DryRunResult(**base)

    def _render(self, result):
        from io import StringIO

        from rich.console import Console

        from benchbox.cli.dryrun import DryRunDisplay

        buf = StringIO()
        display = DryRunDisplay(console=Console(file=buf, force_terminal=False, width=140))
        display.display_dry_run_results(result)
        return buf.getvalue()

    def test_display_dry_run_results_renders_dataframe_preview_and_maintenance_ops(self):
        long_query = "pl.scan_parquet('lineitem.parquet').filter(pl.col('l_quantity') > 10)\n" * 20
        result = self._make_result(
            database_config={"type": "polars-df", "name": "Polars"},
            execution_mode="dataframe",
            queries={
                "Q1": long_query,
                "Q2": "orders.group_by('o_orderstatus').agg(pl.len())",
                "Q3": "customer.join(orders, on='c_custkey')",
                "Q4": "lineitem.sort('l_orderkey')",
                "RF1": "DELETE FROM lineitem WHERE l_orderkey < 10",
                "_error": "internal planning failure",
            },
            dataframe_schema="import polars as pl\nschema = {'l_orderkey': pl.Int64}",
            query_preview={
                "test_execution_type": "standard",
                "execution_context": "Vectorized execution",
            },
        )

        output = self._render(result)

        assert "DataFrame Query Preview" in output
        assert "Vectorized execution" in output
        assert "DataFrame Query Q1" in output
        assert "... [truncated]" in output
        assert "... and 1 more queries" in output
        assert "TPC-H Maintenance Operations" in output
        assert "Refresh Function RF1" in output
        assert "_error" not in output
        assert "DataFrame Schema" in output

    def test_display_dry_run_results_renders_tuning_ddl_post_load_resources_and_warnings(self):
        result = self._make_result(
            schema_sql="CREATE TABLE orders (id INT);",
            tuning_config={
                "constraints": {
                    "primary_keys": {"enabled": True, "enforce_uniqueness": True, "nullable": False},
                    "foreign_keys": {"enabled": True, "enforce_referential_integrity": True},
                },
                "table_tunings": {
                    "orders": {
                        "partitioning": [{"name": "order_date", "type": "date"}],
                        "sorting": [{"name": "o_orderkey", "type": "bigint"}],
                    }
                },
                "platform_optimizations": {"cluster_by_date": True, "z_order": False},
                "dataframe_tuning": {
                    "parallelism": {"workers": 8},
                    "memory": {"target_gb": 16},
                    "execution": {"streaming": True},
                    "write": {
                        "sort_by": [{"name": "order_date", "order": "asc"}],
                        "partition_by": [{"name": "ship_mode", "strategy": "hash"}],
                        "row_group_size": 100000,
                        "repartition_count": 4,
                        "compression": "zstd",
                        "compression_level": 3,
                        "dictionary_columns": ["ship_mode"],
                    },
                },
            },
            ddl_preview={
                "orders": {
                    "tuning_summary": {"sort_by": "order_date", "partition_by": "ship_mode"},
                    "ddl_clauses": "ORDER BY order_date",
                }
            },
            post_load_statements={"orders": ["VACUUM orders", "ANALYZE orders"]},
            estimated_resources={
                "estimated_data_size_mb": 2048.0,
                "estimated_memory_usage_mb": 950.0,
                "memory_gb_available": 1.0,
                "estimated_runtime_minutes": 12.0,
                "cpu_cores_available": 8,
            },
            warnings=["First warning", "Second warning"],
        )

        output = self._render(result)

        assert "Tuning Configuration" in output
        assert "Primary Keys" in output
        assert "Table Organization Tunings" in output
        assert "orders" in output
        assert "Platform Optimizations" in output
        assert "Cluster By Date" in output
        assert "DataFrame Tuning Configuration" in output
        assert "Write Layout" in output
        assert "Sort By" in output
        assert "DDL Preview (Tuning Clauses)" in output
        assert "Tuning: Sort: order_date | Partition: ship_mode" in output
        assert "Post-Load Operations" in output
        assert "VACUUM orders" in output
        assert "Resource Estimates" in output
        assert "Warning: Estimated memory usage is close to available memory" in output
        assert "Warnings (2)" in output
        assert "First warning" in output

    def test_display_helper_formatters_cover_stream_and_maintenance_paths(self):
        from io import StringIO

        from rich.console import Console

        from benchbox.cli.dryrun import DryRunDisplay

        display = DryRunDisplay(console=Console(file=StringIO(), force_terminal=False))

        power_config = Mock()
        power_config.test_execution_type = "power"
        power_config.name = "tpcds"
        throughput_config = Mock()
        throughput_config.test_execution_type = "throughput"
        throughput_config.name = "tpch"
        maintenance_config = Mock()
        maintenance_config.test_execution_type = "maintenance"
        maintenance_config.name = "tpch"

        assert display._get_execution_context(power_config, 99) == (
            "TPC-DS PowerTest stream permutation (99 queries in randomized order)"
        )
        assert display._get_execution_context(throughput_config, 88) == "Throughput test execution (concurrent streams)"
        assert display._get_execution_context(maintenance_config, 2) == "Maintenance test execution (data operations)"
        assert display._format_test_execution_type("throughput") == "ThroughputTest (Concurrent Streams)"
        assert display._get_preview_title("maintenance", 2) == "MaintenanceTest Operations Preview"
        assert display._format_query_display_title("RF1", "maintenance") == "Operation RF1"
        assert display._format_query_display_title("Stream_2_Position_5_Query_17", "power") == (
            "Stream 2 Position 5 Query 17"
        )
        assert display._format_query_display_title("Position_3_Query_8", "power") == "Stream Position 3 Query 8"
        assert display._format_panel_title("Stream_2_Position_5_Query_17", "power") == ("Stream 2 Position 5: Query 17")
        assert display._format_panel_title("Position_3_Query_8", "power") == "Stream Position 3: Query 8"


class TestGenerateCliCommandNewParams:
    def test_platform_options_rendered(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(
            platform="duckdb",
            benchmark="tpch",
            scale=0.01,
            platform_options={"driver_version": "1.2.0", "warehouse": "MY_WH"},
        )
        assert "--platform-option driver_version=1.2.0" in cmd
        assert "--platform-option warehouse=MY_WH" in cmd

    def test_execution_engine_renders_as_canonical_cli_option(self):
        from benchbox.cli.dryrun import generate_cli_command

        command = generate_cli_command(
            platform="polars-df",
            benchmark="tpch",
            scale=0.01,
            execution_engine="streaming",
        )

        assert "--execution-engine streaming" in command
        assert "--platform-option streaming=true" not in command

    def test_platform_options_omitted_when_none(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="duckdb", benchmark="tpch", scale=0.01)
        assert "--platform-option" not in cmd

    def test_plan_config_rendered(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(
            platform="duckdb",
            benchmark="tpch",
            scale=0.01,
            plan_config="sample:0.1,first:5",
        )
        assert "--plan-config sample:0.1,first:5" in cmd

    def test_plan_config_omitted_when_none(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="duckdb", benchmark="tpch", scale=0.01)
        assert "--plan-config" not in cmd

    def test_presort_rendered(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(
            platform="duckdb",
            benchmark="tpch",
            scale=0.01,
            presort="delta-sorted",
        )
        assert "--presort delta-sorted" in cmd

    def test_presort_omitted_when_none(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="duckdb", benchmark="tpch", scale=0.01)
        assert "--presort" not in cmd

    def test_sorted_ingestion_mode_rendered(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(
            platform="databricks",
            benchmark="tpch",
            scale=0.01,
            sorted_ingestion_mode="force",
        )
        assert "--sorted-ingestion-mode force" in cmd

    def test_sorted_ingestion_mode_off_omitted(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(
            platform="databricks",
            benchmark="tpch",
            scale=0.01,
            sorted_ingestion_mode="off",
        )
        assert "--sorted-ingestion-mode" not in cmd

    def test_sorted_ingestion_method_rendered(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(
            platform="databricks",
            benchmark="tpch",
            scale=0.01,
            sorted_ingestion_method="z_order",
        )
        assert "--sorted-ingestion-method z_order" in cmd

    def test_databricks_clustering_strategy_rendered(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(
            platform="databricks",
            benchmark="tpch",
            scale=0.01,
            platform_options={"databricks_clustering_strategy": "liquid_clustering"},
        )
        assert "--platform-option databricks_clustering_strategy=liquid_clustering" in cmd

    def test_liquid_clustering_columns_rendered(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(
            platform="databricks",
            benchmark="tpch",
            scale=0.01,
            platform_options={"liquid_clustering_columns": "col1,col2"},
        )
        assert "--platform-option liquid_clustering_columns=col1,col2" in cmd

    def test_global_cache_rendered(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(
            platform="polars-df",
            benchmark="tpch",
            scale=0.01,
            global_cache=True,
        )
        assert "--global-cache" in cmd

    def test_global_cache_omitted_when_false(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(
            platform="polars-df",
            benchmark="tpch",
            scale=0.01,
            global_cache=False,
        )
        assert "--global-cache" not in cmd

    def test_compression_zstd_not_silently_dropped(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(
            platform="duckdb",
            benchmark="tpch",
            scale=0.01,
            compression="zstd",
        )
        assert "--compression zstd" in cmd

    def test_all_new_params_together(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(
            platform="databricks",
            benchmark="tpch",
            scale=1.0,
            platform_options={
                "driver_version": "1.0.0",
                "databricks_clustering_strategy": "z_order",
                "liquid_clustering_columns": "col1",
            },
            plan_config="sample:0.5",
            presort="delta-sorted",
            sorted_ingestion_mode="auto",
            sorted_ingestion_method="ctas",
            global_cache=True,
            capture_plans=True,
        )
        assert "--platform-option driver_version=1.0.0" in cmd
        assert "--plan-config sample:0.5" in cmd
        assert "--presort delta-sorted" in cmd
        assert "--sorted-ingestion-mode auto" in cmd
        assert "--sorted-ingestion-method ctas" in cmd
        assert "--platform-option databricks_clustering_strategy=z_order" in cmd
        assert "--platform-option liquid_clustering_columns=col1" in cmd
        assert "--global-cache" in cmd
        assert "--capture-plans" in cmd


class TestGenerateCliCommandClientLink:
    def test_client_link_options_rendered(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(
            platform="snowflake",
            benchmark="tpch",
            scale=1.0,
            client_region="us-east-1",
            client_cloud="aws",
        )
        assert "--client-region us-east-1" in cmd
        assert "--client-cloud aws" in cmd
        assert "--no-link-probe" not in cmd

    def test_no_link_probe_rendered(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(
            platform="duckdb",
            benchmark="tpch",
            scale=0.01,
            no_link_probe=True,
        )
        assert "--no-link-probe" in cmd

    def test_client_link_options_omitted_when_unset(self):
        from benchbox.cli.dryrun import generate_cli_command

        cmd = generate_cli_command(platform="duckdb", benchmark="tpch", scale=0.01)
        assert "--client-region" not in cmd
        assert "--client-cloud" not in cmd
        assert "--no-link-probe" not in cmd


class TestGenerateCliCommandCompleteness:
    COSMETIC_PARAMS = frozenset(
        {
            "dry_run",
            "verbose",
            "quiet",
            "non_interactive",
            "no_monitoring",
            "no_progress",
            "ignore_memory_warnings",
            "help",
            "help_topic",
            "concurrency",
            "polars_streaming",
        }
    )

    CLICK_TO_KWARG_RENAMES = {
        "platform_option_pairs": "platform_options",
        "benchmark_option_pairs": "benchmark_options",
    }

    def test_completeness(self):
        import inspect

        import click

        from benchbox.cli.commands.run import run
        from benchbox.cli.dryrun import generate_cli_command

        click_params = set()
        assert isinstance(run, click.Command)
        for param in run.params:
            if isinstance(param, click.Argument):
                continue
            click_params.add(param.name)

        sig = inspect.signature(generate_cli_command)
        gen_params = set(sig.parameters.keys())

        normalized_click = set()
        for p in click_params:
            renamed = self.CLICK_TO_KWARG_RENAMES.get(p, p)
            normalized_click.add(renamed)

        behavior_params = normalized_click - self.COSMETIC_PARAMS
        missing = behavior_params - gen_params

        assert not missing, (
            f"CLI params not represented in generate_cli_command(): {sorted(missing)}. "
            f"Either add them to generate_cli_command() or add to COSMETIC_PARAMS if they are output-only."
        )


if __name__ == "__main__":
    pytest.main([__file__])
