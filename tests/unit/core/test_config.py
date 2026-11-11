# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from datetime import datetime

import pytest

from benchbox.core.schemas import (
    BenchmarkConfig,
    DatabaseConfig,
    DryRunResult,
    LibraryInfo,
    PlatformInfo,
    QueryResult,
    RunConfig,
    SystemProfile,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestQueryResult:
    def test_query_result_creation(self):

        result = QueryResult(
            query_id="q1",
            query_name="Query 1",
            sql_text="SELECT * FROM test",
            execution_time_ms=123.45,
            rows_returned=100,
            status="SUCCESS",
            error_message=None,
            resource_usage={"cpu": "50%"},
        )

        assert result.query_id == "q1"
        assert result.query_name == "Query 1"
        assert result.sql_text == "SELECT * FROM test"
        assert result.execution_time_ms == 123.45
        assert result.rows_returned == 100
        assert result.status == "SUCCESS"
        assert result.error_message is None
        assert result.resource_usage == {"cpu": "50%"}

    def test_query_result_with_error(self):

        result = QueryResult(
            query_id="q2",
            query_name="Query 2",
            sql_text="SELECT invalid FROM test",
            execution_time_ms=0.0,
            rows_returned=0,
            status="ERROR",
            error_message="Table 'invalid' doesn't exist",
        )

        assert result.status == "ERROR"
        assert result.error_message == "Table 'invalid' doesn't exist"
        assert result.rows_returned == 0


class TestBenchmarkConfig:
    def test_benchmark_config_defaults(self):

        config = BenchmarkConfig(name="tpch", display_name="TPC-H Benchmark")

        assert config.name == "tpch"
        assert config.display_name == "TPC-H Benchmark"
        assert config.scale_factor == 0.01
        assert config.concurrency is None
        assert config.compress_data is False
        assert config.test_execution_type == "standard"
        assert config.options == {}

    def test_benchmark_config_with_compression(self):

        config = BenchmarkConfig(
            name="tpcds",
            display_name="TPC-DS Benchmark",
            compress_data=True,
            compression_type="zstd",
            compression_level=3,
        )

        assert config.compress_data is True
        assert config.compression_type == "zstd"
        assert config.compression_level == 3


class TestDatabaseConfig:
    def test_database_config_creation(self):

        config = DatabaseConfig(type="duckdb", name="test.db")

        assert config.type == "duckdb"
        assert config.name == "test.db"
        assert config.connection_string is None
        assert config.options == {}

    def test_database_config_with_options(self):

        config = DatabaseConfig(
            type="postgresql",
            name="benchdb",
            connection_string="postgresql://localhost/benchdb",
            options={"threads": 4, "memory": "2GB"},
        )

        assert config.type == "postgresql"
        assert config.connection_string == "postgresql://localhost/benchdb"
        assert config.options == {"threads": 4, "memory": "2GB"}


class TestSystemProfile:
    def test_system_profile_creation(self):

        now = datetime.now()
        profile = SystemProfile(
            os_name="Linux",
            os_version="5.4.0",
            architecture="x86_64",
            cpu_model="Intel Core i7",
            cpu_cores_physical=4,
            cpu_cores_logical=8,
            memory_total_gb=16.0,
            memory_available_gb=12.0,
            python_version="3.11.0",
            disk_space_gb=500.0,
            timestamp=now,
            hostname="test-machine",
        )

        assert profile.os_name == "Linux"
        assert profile.cpu_cores_physical == 4
        assert profile.cpu_cores_logical == 8
        assert profile.memory_total_gb == 16.0
        assert profile.timestamp == now
        assert profile.hostname == "test-machine"


class TestRunConfig:
    def test_run_config_defaults(self):

        config = RunConfig()

        assert config.query_subset is None
        assert config.concurrent_streams is None
        assert config.test_execution_type == "standard"
        assert config.scale_factor == 0.01
        assert config.seed is None
        assert config.client_region is None
        assert config.client_cloud is None
        assert config.link_probe is True

    def test_run_config_power_test(self):

        config = RunConfig(test_execution_type="power", scale_factor=1.0, seed=42)

        assert config.test_execution_type == "power"
        assert config.scale_factor == 1.0
        assert config.seed == 42

    def test_run_config_client_link_fields(self):

        config = RunConfig(
            client_region="us-east-1",
            client_cloud="aws",
            link_probe=False,
        )

        assert config.client_region == "us-east-1"
        assert config.client_cloud == "aws"
        assert config.link_probe is False


class TestLibraryInfo:
    def test_library_info_available(self):

        info = LibraryInfo(name="duckdb", version="0.9.0", installed=True)

        assert info.name == "duckdb"
        assert info.version == "0.9.0"
        assert info.installed is True
        assert info.import_error is None

    def test_library_info_unavailable(self):

        info = LibraryInfo(
            name="missing_lib",
            version=None,
            installed=False,
            import_error="ModuleNotFoundError: No module named 'missing_lib'",
        )

        assert info.installed is False
        assert info.import_error is not None


class TestPlatformInfo:
    def test_platform_info_creation(self):

        libraries = [
            LibraryInfo(name="duckdb", version="0.9.0", installed=True),
            LibraryInfo(name="psycopg", version=None, installed=False),
        ]

        info = PlatformInfo(
            name="duckdb",
            display_name="DuckDB",
            description="In-memory analytical database",
            libraries=libraries,
            available=True,
            enabled=True,
            requirements=["duckdb>=0.8.0"],
            installation_command="uv add duckdb",
        )

        assert info.name == "duckdb"
        assert info.display_name == "DuckDB"
        assert info.available is True
        assert len(info.libraries) == 2
        assert info.requirements == ["duckdb>=0.8.0"]


class TestDryRunResult:
    def test_dry_run_result_creation(self):

        benchmark_config = BenchmarkConfig(name="tpch", display_name="TPC-H")
        database_config = DatabaseConfig(type="duckdb", name="test.db")
        system_profile = SystemProfile(
            os_name="Linux",
            os_version="5.4.0",
            architecture="x86_64",
            cpu_model="Intel",
            cpu_cores_physical=4,
            cpu_cores_logical=8,
            memory_total_gb=16.0,
            memory_available_gb=12.0,
            python_version="3.11.0",
            disk_space_gb=500.0,
            timestamp=datetime.now(),
        )
        platform_info = PlatformInfo(
            name="duckdb",
            display_name="DuckDB",
            description="Test",
            libraries=[],
            available=True,
            enabled=True,
            requirements=[],
            installation_command="uv add duckdb",
        )

        result = DryRunResult(
            benchmark_config=benchmark_config.model_dump(),
            database_config=database_config.model_dump(),
            system_profile=system_profile.model_dump(),
            platform_config=platform_info.model_dump(),
            queries={"q1": "SELECT 1"},
        )

        assert result.benchmark_config["name"] == "tpch"
        assert result.database_config["type"] == "duckdb"
        assert result.queries == {"q1": "SELECT 1"}
        assert result.warnings == []
        assert isinstance(result.timestamp, datetime)


