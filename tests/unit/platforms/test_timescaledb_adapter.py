# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import argparse
from unittest.mock import Mock, patch

import pytest

import benchbox.platforms.postgresql as postgresql_module
import benchbox.platforms.timescaledb as timescaledb_module
from benchbox.platforms.postgresql import POSTGRES_DIALECT
from benchbox.platforms.timescaledb import TimescaleDBAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def timescale_stubs(monkeypatch):
    mock_psycopg = Mock()
    mock_psycopg.__version__ = "3.1.0"

    monkeypatch.setattr(timescaledb_module, "psycopg", mock_psycopg)
    monkeypatch.setattr(postgresql_module, "psycopg", mock_psycopg)

    return mock_psycopg


class TestTimescaleDBAdapter:
    def test_initialization_defaults(self, timescale_stubs):
        adapter = TimescaleDBAdapter()

        assert adapter.platform_name == "TimescaleDB"
        assert adapter.get_target_dialect() == POSTGRES_DIALECT
        assert adapter.host == "localhost"
        assert adapter.port == 5432
        assert adapter.database == "benchbox"
        assert adapter.username == "postgres"
        assert adapter.schema == "public"
        assert adapter.chunk_interval == "1 day"
        assert adapter.compression_enabled is False
        assert adapter.compression_after == "7 days"
        assert adapter.enable_timescale is True

    def test_initialization_with_config(self, timescale_stubs):
        adapter = TimescaleDBAdapter(
            host="timescale.example.com",
            port=5433,
            database="metrics_db",
            username="custom_user",
            password="secret",
            schema="metrics",
            chunk_interval="1 week",
            compression_enabled=True,
            compression_after="30 days",
        )

        assert adapter.host == "timescale.example.com"
        assert adapter.port == 5433
        assert adapter.database == "metrics_db"
        assert adapter.username == "custom_user"
        assert adapter.password == "secret"
        assert adapter.schema == "metrics"
        assert adapter.chunk_interval == "1 week"
        assert adapter.compression_enabled is True
        assert adapter.compression_after == "30 days"

    def test_dialect_is_postgres(self, timescale_stubs):
        adapter = TimescaleDBAdapter()

        assert adapter.get_target_dialect() == POSTGRES_DIALECT
        assert adapter.get_target_dialect() == "postgres"

    def test_from_config_basic(self, timescale_stubs):
        config = {
            "host": "timescale.local",
            "port": 5433,
            "database": "test_metrics",
            "chunk_interval": "6 hours",
            "compression_enabled": True,
        }

        adapter = TimescaleDBAdapter.from_config(config)

        assert adapter.host == "timescale.local"
        assert adapter.port == 5433
        assert adapter.database == "test_metrics"
        assert adapter.chunk_interval == "6 hours"
        assert adapter.compression_enabled is True

    def test_from_config_generates_database_name(self, timescale_stubs):
        config = {
            "benchmark": "tsbs_devops",
            "scale_factor": 1.0,
        }

        adapter = TimescaleDBAdapter.from_config(config)

        assert "benchbox" in adapter.database
        assert "tsbs" in adapter.database.lower() or adapter.database == "benchbox"

    def test_from_config_uses_provided_database(self, timescale_stubs):
        config = {
            "database": "explicit_db",
            "benchmark": "tsbs_devops",
            "scale_factor": 1.0,
        }

        adapter = TimescaleDBAdapter.from_config(config)

        assert adapter.database == "explicit_db"

    def test_add_cli_arguments_registers_postgres_compatible_and_timescale_flags(self, timescale_stubs):
        parser = argparse.ArgumentParser()

        TimescaleDBAdapter.add_cli_arguments(parser)
        parsed = parser.parse_args(
            [
                "--timescale-host",
                "timescale.local",
                "--timescale-port",
                "5545",
                "--timescale-database",
                "metrics",
                "--timescale-username",
                "ts_user",
                "--timescale-password",
                "ts_secret",
                "--timescale-schema",
                "ts_schema",
                "--timescale-work-mem",
                "640MB",
                "--timescale-maintenance-work-mem",
                "2GB",
                "--timescale-chunk-interval",
                "6 hours",
                "--timescale-compression",
                "--timescale-compression-after",
                "14 days",
            ]
        )

        assert parsed.host == "timescale.local"
        assert parsed.port == 5545
        assert parsed.database == "metrics"
        assert parsed.username == "ts_user"
        assert parsed.password == "ts_secret"
        assert parsed.schema == "ts_schema"
        assert parsed.work_mem == "640MB"
        assert parsed.maintenance_work_mem == "2GB"
        assert parsed.chunk_interval == "6 hours"
        assert parsed.compression_enabled is True
        assert parsed.compression_after == "14 days"

    def test_inherits_postgresql_connection_params(self, timescale_stubs):
        adapter = TimescaleDBAdapter(
            host="timescale.example.com",
            port=5433,
            database="testdb",
            username="testuser",
            password="testpass",
            sslmode="require",
            connect_timeout=15,
        )

        params = adapter._get_connection_params()

        assert params["host"] == "timescale.example.com"
        assert params["port"] == 5433
        assert params["dbname"] == "testdb"
        assert params["user"] == "testuser"
        assert params["password"] == "testpass"
        assert params["sslmode"] == "require"
        assert params["connect_timeout"] == 15

    def test_supports_tuning_type(self, timescale_stubs):
        adapter = TimescaleDBAdapter()

        from benchbox.core.tuning.interface import TuningType

        assert adapter.supports_tuning_type(TuningType.AUTO_COMPACT) is True
        assert adapter.supports_tuning_type(TuningType.PARTITIONING) is True
        assert adapter.supports_tuning_type(TuningType.CLUSTERING) is True
        assert adapter.supports_tuning_type(TuningType.PRIMARY_KEYS) is True
        assert adapter.supports_tuning_type(TuningType.FOREIGN_KEYS) is True
        assert adapter.supports_tuning_type(TuningType.DISTRIBUTION) is True
        assert adapter.supports_tuning_type(TuningType.SORTING) is True

    def test_get_platform_info_basic(self, timescale_stubs):
        adapter = TimescaleDBAdapter(
            chunk_interval="1 week",
            compression_enabled=True,
            compression_after="14 days",
        )

        info = adapter.get_platform_info(connection=None)

        assert info["platform_type"] == "timescaledb"
        assert info["platform_name"] == "TimescaleDB"
        assert info["configuration"]["chunk_interval"] == "1 week"
        assert info["configuration"]["compression_enabled"] is True
        assert info["configuration"]["compression_after"] == "14 days"

    def test_create_connection_verifies_extension(self, timescale_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchone.side_effect = [
            None,
            None,
            ("2.13.0",),
            (1,),
        ]

        timescale_stubs.connect.return_value = mock_conn

        adapter = TimescaleDBAdapter()

        with (
            patch.object(adapter, "check_server_database_exists", return_value=True),
            patch.object(adapter, "handle_existing_database"),
        ):
            adapter.create_connection()

        calls = [str(call) for call in mock_cursor.execute.call_args_list]
        extension_check = any("timescaledb" in call.lower() for call in calls)
        assert extension_check

    def test_create_schema_rolls_back_after_statement_failure(self, timescale_stubs):
        adapter = TimescaleDBAdapter()
        benchmark = Mock()
        conn = Mock()
        cursor = Mock()
        cursor.execute.side_effect = [
            RuntimeError("first statement failed"),
            None,
        ]
        conn.cursor.return_value = cursor

        schema_sql = "CREATE TABLE bad (id INT); CREATE TABLE good (id INT);"
        with (
            patch.object(adapter, "_create_schema_with_tuning", return_value=schema_sql),
            patch.object(adapter, "_convert_to_hypertables"),
        ):
            adapter.create_schema(benchmark, conn)

        conn.rollback.assert_called_once()
        conn.commit.assert_called_once()
        assert cursor.execute.call_count == 2
        cursor.close.assert_called_once()

    def test_hypertable_tracking(self, timescale_stubs):
        adapter = TimescaleDBAdapter()

        assert len(adapter._hypertables) == 0

        adapter._hypertables.add("cpu")
        assert "cpu" in adapter._hypertables


class TestTimescaleDBRegistration:
    def test_timescaledb_in_platform_registry(self, timescale_stubs):
        from benchbox.core.platform_registry import PlatformRegistry, auto_register_platforms

        auto_register_platforms()

        assert "timescaledb" in PlatformRegistry._adapters
        assert PlatformRegistry._adapters["timescaledb"] == TimescaleDBAdapter

    def test_timescaledb_metadata(self, timescale_stubs):
        from benchbox.core.platform_registry import PlatformRegistry

        metadata = PlatformRegistry._build_platform_metadata()

        assert "timescaledb" in metadata
        assert metadata["timescaledb"]["display_name"] == "TimescaleDB"
        assert metadata["timescaledb"]["category"] == "timeseries"
        assert "timeseries" in metadata["timescaledb"]["supports"]
        assert "compression" in metadata["timescaledb"]["supports"]


class TestTimescaleDBConfigBuilder:
    def test_config_builder_basic(self, timescale_stubs):
        from benchbox.platforms.timescaledb import _build_timescaledb_config

        options = {
            "host": "localhost",
            "port": 5432,
            "chunk_interval": "12 hours",
            "compression_enabled": True,
        }
        overrides = {"scale_factor": 1.0}

        config = _build_timescaledb_config("timescaledb", options, overrides, None)

        assert config.host == "localhost"
        assert config.port == 5432
        assert config.scale_factor == 1.0

    def test_config_builder_defaults(self, timescale_stubs):
        from benchbox.platforms.timescaledb import _build_timescaledb_config

        config = _build_timescaledb_config("timescaledb", {}, {}, None)

        assert config.host == "localhost"
        assert config.port == 5432
        assert config.username == "postgres"
        assert config.admin_database == "postgres"
        assert config.sslmode == "prefer"
        assert config.work_mem == "256MB"
        assert config.maintenance_work_mem == "512MB"
        assert config.effective_cache_size == "1GB"
        assert config.max_parallel_workers_per_gather == 2
        assert config.chunk_interval == "1 day"
        assert config.compression_enabled is False
        assert config.compression_after == "7 days"
        assert config.options["schema"] == "public"


class TestTimescaleDBIntervalValidation:
    def test_valid_interval_formats(self, timescale_stubs):
        valid_intervals = [
            "1 day",
            "7 days",
            "1 week",
            "2 weeks",
            "1 hour",
            "12 hours",
            "30 minutes",
            "1 month",
            "3 months",
            "1 year",
            "2 years",
            "100 seconds",
            "500 milliseconds",
            "1000 microseconds",
        ]

        for interval in valid_intervals:
            adapter = TimescaleDBAdapter(chunk_interval=interval)
            assert adapter.chunk_interval == interval

    def test_invalid_interval_format_raises(self, timescale_stubs):
        invalid_intervals = [
            "day",
            "1",
            "1day",
            "one day",
            "1.5 days",
            "1 invalid",
            "",
            "   ",
        ]

        for interval in invalid_intervals:
            with pytest.raises(ValueError, match="Invalid chunk_interval format"):
                TimescaleDBAdapter(chunk_interval=interval)

    def test_sql_injection_in_chunk_interval_rejected(self, timescale_stubs):
        injection_payloads = [
            "1 day'; DROP TABLE users; --",
            "1 day' OR '1'='1",
            "1 day; SELECT * FROM pg_shadow",
            '1 day"; DELETE FROM hypertables; --',
        ]

        for payload in injection_payloads:
            with pytest.raises(ValueError, match="Invalid chunk_interval format"):
                TimescaleDBAdapter(chunk_interval=payload)

    def test_sql_injection_in_compression_after_rejected(self, timescale_stubs):
        injection_payloads = [
            "7 days'; DROP TABLE chunks; --",
            "7 days' UNION SELECT * FROM secrets",
        ]

        for payload in injection_payloads:
            with pytest.raises(ValueError, match="Invalid compression_after format"):
                TimescaleDBAdapter(chunk_interval="1 day", compression_after=payload)

    def test_non_string_interval_raises(self, timescale_stubs):
        with pytest.raises(ValueError, match="must be a string"):
            TimescaleDBAdapter(chunk_interval=123)

        with pytest.raises(ValueError, match="must be a string"):
            TimescaleDBAdapter(chunk_interval=["1", "day"])

    def test_interval_validation_static_method(self, timescale_stubs):
        result = TimescaleDBAdapter._validate_interval("  1 day  ", "test_param")
        assert result == "1 day"

        with pytest.raises(ValueError, match="Invalid test_param format"):
            TimescaleDBAdapter._validate_interval("invalid", "test_param")


class TestTimescaleDBCoverage:
    def test_get_platform_info_collects_timescale_metadata(self, timescale_stubs) -> None:
        adapter = TimescaleDBAdapter()

        cursor = Mock()
        cursor.fetchone.side_effect = [("2.13.0",), (3,), (9,)]
        conn = Mock()
        conn.cursor.return_value = cursor

        with patch(
            "benchbox.platforms.postgresql.PostgreSQLAdapter.get_platform_info", return_value={"configuration": {}}
        ):
            info = adapter.get_platform_info(connection=conn)

        assert info["timescaledb_version"] == "2.13.0"
        assert info["configuration"]["hypertable_count"] == 3
        assert info["configuration"]["chunk_count"] == 9

    def test_configure_for_benchmark_handles_timescaledb_setting_failure(self, timescale_stubs) -> None:
        adapter = TimescaleDBAdapter()
        cursor = Mock()
        cursor.execute.side_effect = RuntimeError("setting blocked")
        conn = Mock()
        conn.cursor.return_value = cursor

        with patch("benchbox.platforms.postgresql.PostgreSQLAdapter.configure_for_benchmark", return_value=None):
            adapter.configure_for_benchmark(conn, "olap")

        cursor.close.assert_called_once()

    def test_load_data_analyze_hypertable_failure_is_non_fatal(self, timescale_stubs) -> None:
        adapter = TimescaleDBAdapter(schema="public")
        adapter._hypertables = {"cpu"}

        cursor = Mock()
        cursor.execute.side_effect = RuntimeError("analyze failed")
        conn = Mock()
        conn.cursor.return_value = cursor

        with patch(
            "benchbox.platforms.postgresql.PostgreSQLAdapter.load_data",
            return_value=({}, 0.1, None),
        ):
            stats, loading_time, extra = adapter.load_data(benchmark=Mock(), connection=conn, data_dir="/tmp")

        assert stats == {}
        assert loading_time == 0.1
        assert extra is None


@pytest.mark.parametrize("column_type", ["integer", "text"])
def test_native_partition_rejects_non_temporal_column(timescale_stubs, column_type):
    from benchbox.core.tuning.applied_ledger import AppliedTuningLedger
    from benchbox.core.tuning.interface import TableTuning, TuningColumn

    adapter = TimescaleDBAdapter()
    adapter._applied_tuning_ledger = AppliedTuningLedger()
    connection = Mock()
    connection.cursor.return_value.fetchone.return_value = (column_type,)
    tuning = TableTuning(
        table_name="events",
        partitioning=[TuningColumn("event_time", column_type, 1)],
    )
    adapter._apply_native_table_tuning(connection, "events", tuning)
    assert "events" not in adapter._hypertables
    assert not adapter._applied_tuning_ledger.statements
    assert adapter._applied_tuning_ledger.dropped[0].intent == "partitioning:events"


def test_native_catalog_mismatch_does_not_claim_satisfaction(timescale_stubs):
    from benchbox.core.tuning.applied_ledger import AppliedTuningLedger
    from benchbox.core.tuning.interface import TuningColumn, TuningType

    adapter = TimescaleDBAdapter()
    ledger = AppliedTuningLedger()
    cursor = Mock()
    cursor.fetchall.return_value = [("other_time",)]
    requested = {
        TuningType.PARTITIONING: [TuningColumn("event_time", "DATE", 1)],
        TuningType.SORTING: [],
        TuningType.CLUSTERING: [],
        TuningType.DISTRIBUTION: [],
    }
    with pytest.raises(ValueError, match="Hypertable catalog mismatch"):
        adapter._verify_native_table_tuning(cursor, "events", requested, ledger)
    assert not ledger.satisfied
