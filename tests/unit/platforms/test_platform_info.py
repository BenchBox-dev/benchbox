# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.cloud_import,
]


class TestPlatformInfoBase:
    def validate_platform_info_schema(self, platform_info: dict[str, Any], platform_type: str):

        assert "platform_type" in platform_info
        assert "platform_name" in platform_info
        assert "connection_mode" in platform_info
        assert "configuration" in platform_info

        assert isinstance(platform_info["platform_type"], str)
        assert isinstance(platform_info["platform_name"], str)
        assert isinstance(platform_info["connection_mode"], str)
        assert isinstance(platform_info["configuration"], dict)

        assert platform_info["platform_type"] == platform_type

        optional_fields = [
            "platform_version",
            "host",
            "port",
            "client_library_version",
            "embedded_library_version",
        ]

        for field in optional_fields:
            if field in platform_info:
                assert platform_info[field] is None or isinstance(platform_info[field], (str, int))


class TestDuckDBPlatformInfo(TestPlatformInfoBase):
    def test_duckdb_platform_info_memory_mode(self):

        from benchbox.platforms.duckdb import DuckDBAdapter

        adapter = DuckDBAdapter(database_path=":memory:", memory_limit="4GB")
        platform_info = adapter.get_platform_info()

        self.validate_platform_info_schema(platform_info, "duckdb")

        assert platform_info["platform_name"] == "DuckDB"
        assert platform_info["connection_mode"] == "memory"
        assert platform_info["configuration"]["database_path"] == ":memory:"
        assert platform_info["configuration"]["memory_limit"] == "4GB"

    def test_duckdb_platform_info_file_mode(self):

        from benchbox.platforms.duckdb import DuckDBAdapter

        adapter = DuckDBAdapter(database_path="test.db", thread_limit=4)
        platform_info = adapter.get_platform_info()

        self.validate_platform_info_schema(platform_info, "duckdb")

        assert platform_info["connection_mode"] == "file"
        assert platform_info["configuration"]["database_path"] == "test.db"
        assert platform_info["configuration"]["thread_limit"] == 4


class TestClickHousePlatformInfo(TestPlatformInfoBase):
    def test_clickhouse_platform_info_server_mode(self):

        from benchbox.platforms.clickhouse import ClickHouseAdapter

        with (
            patch("benchbox.platforms.clickhouse.setup.ClickHouseClient"),
            patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, [])),
        ):
            adapter = ClickHouseAdapter(deployment_mode="server", host="localhost", port=9000, database="test")
            platform_info = adapter.get_platform_info()

            self.validate_platform_info_schema(platform_info, "clickhouse")

            assert platform_info["platform_name"] == "ClickHouse (Server)"
            assert platform_info["connection_mode"] == "server"
            assert platform_info["configuration"]["host"] == "localhost"
            assert platform_info["configuration"]["port"] == 9000
            assert platform_info["configuration"]["database"] == "test"

    def test_clickhouse_platform_info_embedded_mode(self):

        import importlib.machinery
        import types

        from benchbox.platforms.clickhouse import ClickHouseAdapter

        fake_chdb = types.ModuleType("chdb")
        fake_chdb.chdb_version = (1, 2, 3)
        fake_chdb.__version__ = "1.2.3"

        fake_chdb.__spec__ = importlib.machinery.ModuleSpec("chdb", None)
        with patch.dict("sys.modules", {"chdb": fake_chdb}):
            adapter = ClickHouseAdapter(deployment_mode="local", data_path="/tmp/chdb")
            platform_info = adapter.get_platform_info()

            self.validate_platform_info_schema(platform_info, "clickhouse")
            assert platform_info["platform_name"] == "ClickHouse (Local)"
            assert platform_info["connection_mode"] == "local"
            assert platform_info["configuration"]["data_path"] == "/tmp/chdb"

            assert platform_info["client_library_version"] is not None


class TestSQLitePlatformInfo(TestPlatformInfoBase):
    def test_sqlite_platform_info_memory_mode(self):

        from benchbox.platforms.sqlite import SQLiteAdapter

        adapter = SQLiteAdapter(database_path=":memory:")
        platform_info = adapter.get_platform_info()

        self.validate_platform_info_schema(platform_info, "sqlite")

        assert platform_info["platform_name"] == "SQLite"
        assert platform_info["connection_mode"] == "memory"
        assert platform_info["configuration"]["database_path"] == ":memory:"

    def test_sqlite_platform_info_file_mode(self):

        from benchbox.platforms.sqlite import SQLiteAdapter

        adapter = SQLiteAdapter(database_path="test.db", timeout=60.0)
        platform_info = adapter.get_platform_info()

        self.validate_platform_info_schema(platform_info, "sqlite")

        assert platform_info["connection_mode"] == "file"
        assert platform_info["configuration"]["database_path"] == "test.db"
        assert platform_info["configuration"]["timeout"] == 60.0


class TestCloudPlatformInfo(TestPlatformInfoBase):
    def test_bigquery_platform_info(self):

        from benchbox.platforms.bigquery import BigQueryAdapter

        with (
            patch("benchbox.platforms.bigquery.bigquery"),
            patch("benchbox.platforms.bigquery.check_platform_dependencies", return_value=(True, [])),
        ):
            adapter = BigQueryAdapter(
                project_id="test-project",
                dataset_id="test_dataset",
                location="US",
                storage_bucket="test-bucket",
            )
            platform_info = adapter.get_platform_info()

            self.validate_platform_info_schema(platform_info, "bigquery")

            assert platform_info["platform_name"] == "BigQuery"
            assert platform_info["connection_mode"] == "remote"
            assert platform_info["configuration"]["project_id"] == "test-project"
            assert platform_info["configuration"]["dataset_id"] == "test_dataset"
            assert platform_info["configuration"]["location"] == "US"

    def test_snowflake_platform_info(self):

        from benchbox.platforms.snowflake import SnowflakeAdapter

        with (
            patch("benchbox.platforms.snowflake.snowflake"),
            patch("benchbox.platforms.snowflake.check_platform_dependencies", return_value=(True, [])),
        ):
            adapter = SnowflakeAdapter(
                account="test-account",
                warehouse="COMPUTE_WH",
                database="TEST_DB",
                schema="PUBLIC",
                username="testuser",
                password="testpass",
            )
            platform_info = adapter.get_platform_info()

            self.validate_platform_info_schema(platform_info, "snowflake")

            assert platform_info["platform_name"] == "Snowflake"
            assert platform_info["connection_mode"] == "remote"
            assert platform_info["configuration"]["account"] == "test-account"
            assert platform_info["configuration"]["warehouse"] == "COMPUTE_WH"
            assert platform_info["configuration"]["database"] == "TEST_DB"

    def test_redshift_platform_info(self):

        import types

        from benchbox.platforms.redshift import RedshiftAdapter

        fake_redshift_connector = types.ModuleType("redshift_connector")
        with (
            patch.dict("sys.modules", {"redshift_connector": fake_redshift_connector}),
            patch("benchbox.platforms.redshift.check_platform_dependencies", return_value=(True, [])),
        ):
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                port=5439,
                database="test",
                username="testuser",
                password="testpass",
                s3_bucket="test-bucket",
            )
            platform_info = adapter.get_platform_info()

            self.validate_platform_info_schema(platform_info, "redshift")

            assert platform_info["platform_name"] == "Redshift"
            assert platform_info["connection_mode"] == "remote"
            assert platform_info["host"] == "test-cluster.redshift.amazonaws.com"
            assert platform_info["port"] == 5439
            assert platform_info["configuration"]["database"] == "test"
            assert platform_info["configuration"]["s3_bucket"] == "test-bucket"

    def test_databricks_platform_info(self):

        import types

        from benchbox.platforms.databricks import DatabricksAdapter

        fake_databricks = types.ModuleType("databricks")
        fake_databricks_sql = types.ModuleType("databricks.sql")
        fake_databricks.sql = fake_databricks_sql
        with (
            patch.dict(
                "sys.modules",
                {
                    "databricks": fake_databricks,
                    "databricks.sql": fake_databricks_sql,
                    "databricks.sdk": MagicMock(),
                },
            ),
            patch("benchbox.platforms.databricks.adapter.check_platform_dependencies", return_value=(True, [])),
        ):
            adapter = DatabricksAdapter(
                server_hostname="test.cloud.databricks.com",
                http_path="/sql/1.0/warehouses/test",
                access_token="test-token",
                catalog="main",
                schema="test",
            )
            platform_info = adapter.get_platform_info()

            self.validate_platform_info_schema(platform_info, "databricks")

            assert platform_info["platform_name"] == "Databricks"
            assert platform_info["connection_mode"] == "remote"
            assert platform_info["host"] == "test.cloud.databricks.com"
            assert platform_info["configuration"]["catalog"] == "main"
            assert platform_info["configuration"]["schema"] == "test"


class TestPlatformInfoIntegration:
    def test_benchmark_results_includes_platform_info(self):

        from datetime import datetime

        from benchbox.platforms.base import BenchmarkResults

        platform_info = {
            "platform_type": "duckdb",
            "platform_name": "DuckDB",
            "connection_mode": "memory",
            "configuration": {"database_path": ":memory:"},
        }

        results = BenchmarkResults(
            benchmark_name="TestBenchmark",
            platform="DuckDB",
            scale_factor=0.1,
            execution_id="test123",
            timestamp=datetime.now(),
            duration_seconds=10.5,
            total_queries=5,
            successful_queries=5,
            failed_queries=0,
            query_results=[],
            total_execution_time=8.5,
            average_query_time=1.7,
            data_loading_time=1.0,
            schema_creation_time=0.5,
            total_rows_loaded=1000,
            data_size_mb=10.0,
            table_statistics={"test_table": 1000},
            platform_info=platform_info,
        )

        assert results.platform_info is not None
        assert results.platform_info["platform_type"] == "duckdb"
        assert results.platform_info["platform_name"] == "DuckDB"
        assert results.platform_info["connection_mode"] == "memory"

    def test_platform_info_collection_with_connection(self):

        from benchbox.platforms.duckdb import DuckDBAdapter

        adapter = DuckDBAdapter(memory_limit="2GB")
        mock_connection = Mock()

        platform_info = adapter.get_platform_info(mock_connection)

        assert platform_info["platform_type"] == "duckdb"
        assert platform_info["configuration"]["memory_limit"] == "2GB"

    def test_platform_info_error_handling(self):

        from benchbox.platforms.duckdb import DuckDBAdapter

        adapter = DuckDBAdapter()
        platform_info = adapter.get_platform_info()

        assert "platform_type" in platform_info
        assert "platform_name" in platform_info
        assert "connection_mode" in platform_info
        assert "configuration" in platform_info


