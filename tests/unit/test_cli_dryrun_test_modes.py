# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import Mock, patch

import pytest

from benchbox.cli.benchmarks import BenchmarkConfig
from benchbox.cli.database import DatabaseConfig
from benchbox.cli.dryrun import DryRunExecutor
from benchbox.cli.system import SystemProfile
from benchbox.core.dryrun import DryRunExecutor as CoreDryRunExecutor

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def mock_benchmark_config():
    config = BenchmarkConfig(
        name="tpcds",
        display_name="TPC-DS",
        scale_factor=0.1,
        test_execution_type="standard",
    )
    return config


@pytest.fixture
def mock_system_profile():
    from datetime import datetime

    profile = SystemProfile(
        os_name="Linux",
        os_version="5.4.0",
        architecture="x86_64",
        cpu_model="Intel Core i7",
        cpu_cores_physical=4,
        cpu_cores_logical=8,
        memory_total_gb=16.0,
        memory_available_gb=14.0,
        python_version="3.13.5",
        disk_space_gb=500.0,
        timestamp=datetime.now(),
    )
    return profile


@pytest.fixture
def mock_database_config():
    config = DatabaseConfig(type="duckdb", name="test.db")
    return config


@pytest.fixture
def mock_tpcds_benchmark():
    benchmark = Mock()
    benchmark._name = "tpcds"
    benchmark.scale_factor = 0.1
    benchmark.get_queries.return_value = {
        "1": "SELECT * FROM store_sales;",
        "2": "SELECT * FROM store_returns;",
        "3": "SELECT * FROM catalog_sales;",
    }

    benchmark.get_all_queries = Mock(return_value={})

    benchmark.get_create_tables_sql = Mock(return_value="CREATE TABLE test_table (id INT);")

    query_manager = Mock()
    benchmark.query_manager = query_manager

    return benchmark


class TestDryRunTestModes:
    def test_standard_execution_mode(
        self,
        mock_benchmark_config,
        mock_system_profile,
        mock_database_config,
        mock_tpcds_benchmark,
        tmp_path,
    ):
        mock_benchmark_config.test_execution_type = "standard"

        with (
            patch.object(CoreDryRunExecutor, "_get_benchmark_instance", return_value=mock_tpcds_benchmark),
            patch.object(CoreDryRunExecutor, "_get_platform_config", return_value={"type": "duckdb"}),
            patch("benchbox.platforms.get_platform_adapter"),
        ):
            executor = DryRunExecutor(tmp_path)
            result = executor.execute_dry_run(mock_benchmark_config, mock_system_profile, mock_database_config)

        assert result.benchmark_config["test_execution_type"] == "standard"
        assert result.query_preview["test_execution_type"] == "standard"
        assert "Standard sequential execution" in result.query_preview["execution_context"]
        assert len(result.queries) == 3

    def test_power_test_execution_mode(
        self,
        mock_benchmark_config,
        mock_system_profile,
        mock_database_config,
        mock_tpcds_benchmark,
        tmp_path,
    ):
        mock_benchmark_config.test_execution_type = "power"

        with (
            patch.object(CoreDryRunExecutor, "_get_benchmark_instance", return_value=mock_tpcds_benchmark),
            patch.object(CoreDryRunExecutor, "_get_platform_config", return_value={"type": "duckdb"}),
            patch("benchbox.platforms.get_platform_adapter"),
        ):
            executor = DryRunExecutor(tmp_path)

            with patch.object(executor, "_extract_queries_via_real_test_execution") as mock_extract:
                mock_extract.return_value = {
                    "Position_01_Query_42": "SELECT * FROM web_sales WHERE ws_sold_date_sk = ?;",
                    "Position_02_Query_7": "SELECT * FROM store_sales WHERE ss_sold_date_sk = ?;",
                    "Position_03_Query_23": "SELECT * FROM catalog_sales WHERE cs_sold_date_sk = ?;",
                }

                result = executor.execute_dry_run(mock_benchmark_config, mock_system_profile, mock_database_config)

        assert result.benchmark_config["test_execution_type"] == "power"
        assert result.query_preview["test_execution_type"] == "power"
        assert "PowerTest stream permutation" in result.query_preview["execution_context"]

        query_keys = list(result.queries.keys())
        assert any("Position_01_Query_42" in key for key in query_keys), (
            f"Expected Position_01_Query_42 in {query_keys}"
        )
        assert any("Position_02_Query_7" in key for key in query_keys), f"Expected Position_02_Query_7 in {query_keys}"
        assert any("Position_03_Query_23" in key for key in query_keys), (
            f"Expected Position_03_Query_23 in {query_keys}"
        )

    def test_throughput_test_execution_mode(
        self,
        mock_benchmark_config,
        mock_system_profile,
        mock_database_config,
        mock_tpcds_benchmark,
        tmp_path,
    ):
        mock_benchmark_config.test_execution_type = "throughput"

        with (
            patch.object(CoreDryRunExecutor, "_get_benchmark_instance", return_value=mock_tpcds_benchmark),
            patch.object(CoreDryRunExecutor, "_get_platform_config", return_value={"type": "duckdb"}),
            patch("benchbox.platforms.get_platform_adapter"),
        ):
            executor = DryRunExecutor(tmp_path)

            with patch.object(executor, "_extract_queries_via_real_test_execution") as mock_extract:
                mock_extract.return_value = {
                    "Stream_1_Position_01_Query_42": "SELECT * FROM web_sales WHERE ws_sold_date_sk = ?;",
                    "Stream_1_Position_02_Query_7": "SELECT * FROM store_sales WHERE ss_sold_date_sk = ?;",
                    "Stream_1_Position_03_Query_23": "SELECT * FROM catalog_sales WHERE cs_sold_date_sk = ?;",
                    "Stream_2_Position_01_Query_42": "SELECT * FROM web_sales WHERE ws_sold_date_sk = ?;",
                    "Stream_2_Position_02_Query_7": "SELECT * FROM store_sales WHERE ss_sold_date_sk = ?;",
                    "Stream_2_Position_03_Query_23": "SELECT * FROM catalog_sales WHERE cs_sold_date_sk = ?;",
                }

                result = executor.execute_dry_run(mock_benchmark_config, mock_system_profile, mock_database_config)

        assert result.benchmark_config["test_execution_type"] == "throughput"
        assert result.query_preview["test_execution_type"] == "throughput"
        assert "ThroughputTest (4 concurrent streams" in result.query_preview["execution_context"]

        query_keys = list(result.queries.keys())
        assert any("Stream_1_" in key for key in query_keys), f"Expected Stream_1_ in {query_keys}"
        assert any("Stream_2_" in key for key in query_keys), f"Expected Stream_2_ in {query_keys}"

    def test_maintenance_test_execution_mode(
        self,
        mock_benchmark_config,
        mock_system_profile,
        mock_database_config,
        mock_tpcds_benchmark,
        tmp_path,
    ):
        mock_benchmark_config.test_execution_type = "maintenance"

        with (
            patch.object(CoreDryRunExecutor, "_get_benchmark_instance", return_value=mock_tpcds_benchmark),
            patch.object(CoreDryRunExecutor, "_get_platform_config", return_value={"type": "duckdb"}),
            patch("benchbox.platforms.get_platform_adapter"),
        ):
            executor = DryRunExecutor(tmp_path)

            with patch.object(executor, "_extract_tpcds_maintenance_operations") as mock_extract:
                mock_extract.return_value = {
                    "DM1_INSERT": "-- TPC-DS Maintenance: Insert new store sales records\nINSERT INTO STORE_SALES (...);",
                    "DM2_INSERT_RETURNS": "-- TPC-DS Maintenance: Insert store returns\nINSERT INTO STORE_RETURNS (...);",
                    "DM3_UPDATE": "-- TPC-DS Maintenance: Update customer records\nUPDATE CUSTOMER SET ...;",
                    "DM4_DELETE": "-- TPC-DS Maintenance: Delete old store sales\nDELETE FROM STORE_SALES WHERE ...;",
                }

                result = executor.execute_dry_run(mock_benchmark_config, mock_system_profile, mock_database_config)

        assert result.benchmark_config["test_execution_type"] == "maintenance"
        assert result.query_preview["test_execution_type"] == "maintenance"
        assert "MaintenanceTest (data operations: INSERT/UPDATE/DELETE)" in result.query_preview["execution_context"]

        query_keys = list(result.queries.keys())
        assert any("DM1_INSERT" in key for key in query_keys), f"Expected DM1_INSERT in {query_keys}"
        assert any("DM2_INSERT_RETURNS" in key for key in query_keys), f"Expected DM2_INSERT_RETURNS in {query_keys}"
        assert any("DM3_UPDATE" in key for key in query_keys), f"Expected DM3_UPDATE in {query_keys}"
        assert any("DM4_DELETE" in key for key in query_keys), f"Expected DM4_DELETE in {query_keys}"

        for key, operation in result.queries.items():
            if key.startswith("DM"):
                assert "INSERT" in operation or "UPDATE" in operation or "DELETE" in operation, (
                    f"Expected SQL operation in {key}: {operation}"
                )

    def test_tpch_power_test_execution_mode(self, mock_system_profile, mock_database_config, tmp_path):
        mock_benchmark_config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.1,
            test_execution_type="power",
        )

        mock_tpch_benchmark = Mock()
        mock_tpch_benchmark._name = "tpch"
        mock_tpch_benchmark.scale_factor = 0.1
        mock_tpch_benchmark.get_queries.return_value = {
            "1": "SELECT * FROM lineitem;",
            "2": "SELECT * FROM orders;",
            "3": "SELECT * FROM customer;",
        }
        mock_tpch_benchmark.get_all_queries = Mock(return_value={})

        with (
            patch.object(CoreDryRunExecutor, "_get_benchmark_instance", return_value=mock_tpch_benchmark),
            patch.object(CoreDryRunExecutor, "_get_platform_config", return_value={"type": "duckdb"}),
            patch("benchbox.platforms.get_platform_adapter"),
        ):
            executor = DryRunExecutor(tmp_path)

            with patch.object(executor, "_extract_queries_via_real_test_execution") as mock_extract:
                mock_extract.return_value = {
                    "Position_01_Query_3": "SELECT * FROM customer;",
                    "Position_02_Query_1": "SELECT * FROM lineitem;",
                    "Position_03_Query_2": "SELECT * FROM orders;",
                }

                result = executor.execute_dry_run(mock_benchmark_config, mock_system_profile, mock_database_config)

        assert result.benchmark_config["test_execution_type"] == "power"
        assert result.query_preview["test_execution_type"] == "power"
        assert "TPC-H PowerTest stream permutation" in result.query_preview["execution_context"]

        query_keys = list(result.queries.keys())
        assert any("Position_01_Query_3" in key for key in query_keys), f"Expected Position_01_Query_3 in {query_keys}"
        assert any("Position_02_Query_1" in key for key in query_keys), f"Expected Position_02_Query_1 in {query_keys}"

    def test_display_query_preview_formatting(
        self,
        mock_benchmark_config,
        mock_system_profile,
        mock_database_config,
        mock_tpcds_benchmark,
        tmp_path,
    ):
        with (
            patch.object(CoreDryRunExecutor, "_get_benchmark_instance", return_value=mock_tpcds_benchmark),
            patch.object(CoreDryRunExecutor, "_get_platform_config", return_value={"type": "duckdb"}),
            patch("benchbox.platforms.get_platform_adapter"),
        ):
            executor = DryRunExecutor(tmp_path)

            assert executor._format_test_execution_type("power") == "PowerTest (Stream Permutation)"
            assert executor._format_test_execution_type("throughput") == "ThroughputTest (Concurrent Streams)"
            assert executor._format_test_execution_type("maintenance") == "MaintenanceTest (Data Operations)"
            assert executor._format_test_execution_type("standard") == "Standard (Sequential)"

            assert executor._get_preview_title("power", 99) == "PowerTest Stream Execution Preview"
            assert executor._get_preview_title("throughput", 40) == "ThroughputTest Concurrent Stream Preview"
            assert executor._get_preview_title("maintenance", 6) == "MaintenanceTest Operations Preview"
            assert executor._get_preview_title("standard", 99) == "Query Preview"

            assert (
                executor._format_query_display_title("Position_01_Query_42", "power") == "Stream Position 01 Query 42"
            )
            assert (
                executor._format_query_display_title("Stream_1_Position_01_Query_42", "throughput")
                == "Stream 1 Position 01 Query 42"
            )
            assert (
                executor._format_query_display_title("RF1_INSERT_catalog_sales", "maintenance")
                == "Operation RF1_INSERT_catalog_sales"
            )

            assert executor._format_panel_title("Position_01_Query_42", "power") == "Stream Position 01: Query 42"
            assert (
                executor._format_panel_title("Stream_1_Position_01_Query_42", "throughput")
                == "Stream 1 Position 01: Query 42"
            )
            assert (
                executor._format_panel_title("RF1_INSERT_catalog_sales", "maintenance")
                == "Maintenance Operation: RF1_INSERT_catalog_sales"
            )

    def test_configuration_summary_shows_test_execution_type(
        self,
        mock_benchmark_config,
        mock_system_profile,
        mock_database_config,
        mock_tpcds_benchmark,
        tmp_path,
    ):
        mock_benchmark_config.test_execution_type = "power"

        with patch("benchbox.core.tpcds.streams.create_standard_streams"):
            with (
                patch.object(CoreDryRunExecutor, "_get_benchmark_instance", return_value=mock_tpcds_benchmark),
                patch.object(CoreDryRunExecutor, "_get_platform_config", return_value={"type": "duckdb"}),
                patch("benchbox.platforms.get_platform_adapter"),
            ):
                executor = DryRunExecutor(tmp_path)
                result = executor.execute_dry_run(mock_benchmark_config, mock_system_profile, mock_database_config)

        assert result.benchmark_config["test_execution_type"] == "power"
        assert result.query_preview["test_execution_type"] == "power"
        assert "PowerTest stream permutation" in result.query_preview["execution_context"]

        assert executor._format_test_execution_type("power") == "PowerTest (Stream Permutation)"


class TestDataFrameMaintenanceDryRun:
    def test_maintenance_dataframe_returns_error_message(
        self,
        mock_system_profile,
        tmp_path,
    ):
        df_database_config = DatabaseConfig(type="polars-df", name="polars")

        benchmark_config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.1,
            test_execution_type="maintenance",
        )

        mock_tpch_benchmark = Mock()
        mock_tpch_benchmark._name = "tpch"
        mock_tpch_benchmark.scale_factor = 0.1
        mock_tpch_benchmark.get_queries.return_value = {}
        mock_tpch_benchmark.get_all_queries = Mock(return_value={})

        with (
            patch.object(CoreDryRunExecutor, "_get_benchmark_instance", return_value=mock_tpch_benchmark),
            patch.object(CoreDryRunExecutor, "_get_platform_config", return_value={"type": "polars-df"}),
            patch("benchbox.platforms.get_platform_adapter"),
        ):
            executor = DryRunExecutor(tmp_path)
            result = executor.execute_dry_run(benchmark_config, mock_system_profile, df_database_config)

        assert "_maintenance_not_supported" in result.queries
        error_msg = result.queries["_maintenance_not_supported"]
        assert "Maintenance phase is not yet implemented for DataFrame mode" in error_msg
        assert "maintenance" in error_msg
        assert "dataframe" in error_msg

    def test_combined_dataframe_returns_error_message(
        self,
        mock_system_profile,
        tmp_path,
    ):
        df_database_config = DatabaseConfig(type="pandas-df", name="pandas")

        benchmark_config = BenchmarkConfig(
            name="tpcds",
            display_name="TPC-DS",
            scale_factor=0.1,
            test_execution_type="combined",
        )

        mock_benchmark = Mock()
        mock_benchmark._name = "tpcds"
        mock_benchmark.scale_factor = 0.1
        mock_benchmark.get_queries.return_value = {}
        mock_benchmark.get_all_queries = Mock(return_value={})

        with (
            patch.object(CoreDryRunExecutor, "_get_benchmark_instance", return_value=mock_benchmark),
            patch.object(CoreDryRunExecutor, "_get_platform_config", return_value={"type": "pandas-df"}),
            patch("benchbox.platforms.get_platform_adapter"),
        ):
            executor = DryRunExecutor(tmp_path)
            result = executor.execute_dry_run(benchmark_config, mock_system_profile, df_database_config)

        assert "_maintenance_not_supported" in result.queries
        assert "combined" in result.queries["_maintenance_not_supported"]

    def test_standard_dataframe_does_not_error(
        self,
        mock_system_profile,
        tmp_path,
    ):
        df_database_config = DatabaseConfig(type="polars-df", name="polars")

        benchmark_config = BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.1,
            test_execution_type="standard",
        )

        mock_benchmark = Mock()
        mock_benchmark._name = "tpch"
        mock_benchmark.scale_factor = 0.1
        mock_benchmark.get_queries.return_value = {"Q1": "SELECT 1;"}
        mock_benchmark.get_all_queries = Mock(return_value={})

        with (
            patch.object(CoreDryRunExecutor, "_get_benchmark_instance", return_value=mock_benchmark),
            patch.object(CoreDryRunExecutor, "_get_platform_config", return_value={"type": "polars-df"}),
            patch("benchbox.platforms.get_platform_adapter"),
        ):
            executor = DryRunExecutor(tmp_path)
            result = executor.execute_dry_run(benchmark_config, mock_system_profile, df_database_config)

        assert "_maintenance_not_supported" not in result.queries

    def test_maintenance_sql_mode_works_normally(
        self,
        mock_benchmark_config,
        mock_system_profile,
        mock_database_config,
        mock_tpcds_benchmark,
        tmp_path,
    ):
        mock_benchmark_config.test_execution_type = "maintenance"

        with (
            patch.object(CoreDryRunExecutor, "_get_benchmark_instance", return_value=mock_tpcds_benchmark),
            patch.object(CoreDryRunExecutor, "_get_platform_config", return_value={"type": "duckdb"}),
            patch("benchbox.platforms.get_platform_adapter"),
        ):
            executor = DryRunExecutor(tmp_path)

            with patch.object(executor, "_extract_tpcds_maintenance_operations") as mock_extract:
                mock_extract.return_value = {
                    "DM1_INSERT": "INSERT INTO STORE_SALES ...;",
                }
                result = executor.execute_dry_run(mock_benchmark_config, mock_system_profile, mock_database_config)

        assert "_maintenance_not_supported" not in result.queries
        assert "DM1_INSERT" in result.queries


class TestDataFrameAdapterDryRun:
    def _make_benchmark(self):
        benchmark = Mock()
        benchmark.scale_factor = 0.1
        benchmark.get_queries.return_value = {"1": "SELECT 1"}
        benchmark.get_all_queries = Mock(return_value={})
        benchmark.get_schema.return_value = {}
        return benchmark

    def test_dataframe_dry_run_uses_dataframe_adapter_for_dual_registered_platform(self, mock_system_profile, tmp_path):
        database_config = DatabaseConfig(
            type="pyspark",
            name="pyspark",
            execution_mode="dataframe",
            options={"driver_memory": "4g"},
        )
        benchmark_config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.1)

        with (
            patch.object(CoreDryRunExecutor, "_get_benchmark_instance", return_value=self._make_benchmark()),
            patch.object(CoreDryRunExecutor, "_get_platform_config", return_value={"driver_memory": "4g"}),
            patch("benchbox.core.dryrun.get_adapter") as mock_dataframe_adapter,
            patch("benchbox.core.dryrun.get_platform_adapter") as mock_sql_adapter,
        ):
            mock_dataframe_adapter.return_value.deployment_selected = None
            executor = DryRunExecutor(tmp_path)
            result = executor.execute_dry_run(benchmark_config, mock_system_profile, database_config)

        mock_dataframe_adapter.assert_called_once()
        assert mock_dataframe_adapter.call_args.args[0] == "pyspark"
        assert mock_dataframe_adapter.call_args.kwargs["mode"] == "dataframe"
        assert mock_dataframe_adapter.call_args.kwargs["driver_memory"] == "4g"
        mock_sql_adapter.assert_not_called()
        assert not any("Platform adapter initialization failed" in warning for warning in result.warnings)

    def test_dataframe_only_alias_dry_run_rejects_invalid_option(self, mock_system_profile, tmp_path):
        from benchbox.platforms.dataframe.pyspark_df import PYSPARK_AVAILABLE

        if not PYSPARK_AVAILABLE:
            pytest.skip("PySpark is not available")

        database_config = DatabaseConfig(
            type="lakesail-df",
            name="lakesail",
            execution_mode="dataframe",
            options={"deployment_mode": "bogus"},
        )
        benchmark_config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.1)

        with (
            patch.object(CoreDryRunExecutor, "_get_benchmark_instance", return_value=self._make_benchmark()),
            patch.object(CoreDryRunExecutor, "_get_platform_config", return_value={"type": "lakesail-df"}),
            patch("benchbox.core.dryrun.get_platform_adapter") as mock_sql_adapter,
        ):
            executor = DryRunExecutor(tmp_path)
            result = executor.execute_dry_run(benchmark_config, mock_system_profile, database_config)

        mock_sql_adapter.assert_not_called()
        assert any(
            "Platform adapter initialization failed" in warning and "deployment mode 'bogus'" in warning
            for warning in result.warnings
        )

    def test_sql_dry_run_uses_sql_adapter(self, mock_system_profile, tmp_path):
        database_config = DatabaseConfig(type="duckdb", name="test.db")
        benchmark_config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.1)

        with (
            patch.object(CoreDryRunExecutor, "_get_benchmark_instance", return_value=self._make_benchmark()),
            patch.object(CoreDryRunExecutor, "_get_platform_config", return_value={"type": "duckdb"}),
            patch("benchbox.core.dryrun.get_adapter") as mock_dataframe_adapter,
            patch("benchbox.core.dryrun.get_platform_adapter") as mock_sql_adapter,
        ):
            mock_sql_adapter.return_value.deployment_selected = None
            executor = DryRunExecutor(tmp_path)
            result = executor.execute_dry_run(benchmark_config, mock_system_profile, database_config)

        mock_sql_adapter.assert_called_once_with("duckdb", type="duckdb", dry_run=True)
        mock_dataframe_adapter.assert_not_called()
        assert result.execution_mode == "sql"
