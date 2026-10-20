# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import Mock, patch

import pytest

import benchbox.platforms.pg_duckdb as pg_duckdb_module
import benchbox.platforms.postgresql as postgresql_module
from benchbox.platforms.pg_duckdb import PgDuckDBAdapter
from benchbox.platforms.postgresql import POSTGRES_DIALECT
from tests.utilities.deprecation_capture import captured_deprecations

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def pg_duckdb_stubs(monkeypatch):
    mock_psycopg = Mock()
    mock_psycopg.__version__ = "3.1.0"

    monkeypatch.setattr(pg_duckdb_module, "psycopg", mock_psycopg)
    monkeypatch.setattr(postgresql_module, "psycopg", mock_psycopg)

    return mock_psycopg


class TestPgDuckDBAdapter:
    def test_initialization_defaults(self, pg_duckdb_stubs):
        adapter = PgDuckDBAdapter()

        assert adapter.platform_name == "pg_duckdb"
        assert adapter.get_target_dialect() == POSTGRES_DIALECT
        assert adapter.host == "localhost"
        assert adapter.port == 5432
        assert adapter.database == "benchbox"
        assert adapter.username == "postgres"
        assert adapter.schema == "public"
        assert adapter.force_execution is True
        receipt = adapter.get_platform_info()["configuration"]["execution_engine"]
        assert receipt["requested"] == "default"
        assert receipt["applied"] == "duckdb"
        assert receipt["resolution"] == "platform_default"
        assert adapter.postgres_scan_threads == 0
        assert adapter.deployment_mode == "self-hosted"

    def test_initialization_with_config(self, pg_duckdb_stubs):
        adapter = PgDuckDBAdapter(
            host="pgduckdb.example.com",
            port=5433,
            database="analytics_db",
            username="custom_user",
            password="secret",
            schema="analytics",
            execution_engine="postgres",
            postgres_scan_threads=4,
        )

        assert adapter.host == "pgduckdb.example.com"
        assert adapter.port == 5433
        assert adapter.database == "analytics_db"
        assert adapter.username == "custom_user"
        assert adapter.password == "secret"
        assert adapter.schema == "analytics"
        assert adapter.force_execution is False
        assert adapter.postgres_scan_threads == 4

    def test_dialect_is_postgres(self, pg_duckdb_stubs):
        adapter = PgDuckDBAdapter()

        assert adapter.get_target_dialect() == POSTGRES_DIALECT
        assert adapter.get_target_dialect() == "postgres"

    def test_from_config_basic(self, pg_duckdb_stubs):
        config = {
            "host": "pgduckdb.local",
            "port": 5433,
            "database": "test_analytics",
            "execution_engine": "duckdb",
            "postgres_scan_threads": 8,
        }

        adapter = PgDuckDBAdapter.from_config(config)

        assert adapter.host == "pgduckdb.local"
        assert adapter.port == 5433
        assert adapter.database == "test_analytics"
        assert adapter.force_execution is True
        assert adapter.postgres_scan_threads == 8

    def test_saved_config_force_execution_alias_warns_and_maps(self, pg_duckdb_stubs, caplog):
        with captured_deprecations(caplog) as seen:
            adapter = PgDuckDBAdapter.from_config(
                {
                    "benchmark": "tpch",
                    "scale_factor": 1.0,
                    "execution_engine": "default",
                    "options": {"force_execution": False},
                }
            )

        assert len(seen) == 1
        assert "'force_execution'" in seen[0]
        receipt = adapter.get_platform_info()["configuration"]["execution_engine"]
        assert adapter.force_execution is False
        assert receipt["requested"] == "postgres"
        assert receipt["applied"] == "postgres"
        assert receipt["applied_class"] == "standard"
        assert receipt["resolution"] == "legacy_option"

    def test_from_config_generates_database_name(self, pg_duckdb_stubs):
        config = {
            "benchmark": "tpch",
            "scale_factor": 1.0,
        }

        adapter = PgDuckDBAdapter.from_config(config)

        assert "benchbox" in adapter.database

    def test_from_config_uses_provided_database(self, pg_duckdb_stubs):
        config = {
            "database": "explicit_db",
            "benchmark": "tpch",
            "scale_factor": 1.0,
        }

        adapter = PgDuckDBAdapter.from_config(config)

        assert adapter.database == "explicit_db"

    def test_inherits_postgresql_connection_params(self, pg_duckdb_stubs):
        adapter = PgDuckDBAdapter(
            host="pgduckdb.example.com",
            port=5433,
            database="testdb",
            username="testuser",
            password="testpass",
            sslmode="require",
            connect_timeout=15,
        )

        params = adapter._get_connection_params()

        assert params["host"] == "pgduckdb.example.com"
        assert params["port"] == 5433
        assert params["dbname"] == "testdb"
        assert params["user"] == "testuser"
        assert params["password"] == "testpass"
        assert params["sslmode"] == "require"
        assert params["connect_timeout"] == 15

    def test_supports_tuning_type(self, pg_duckdb_stubs):
        adapter = PgDuckDBAdapter()

        from benchbox.core.tuning.interface import TuningType

        assert adapter.supports_tuning_type(TuningType.PARTITIONING) is True
        assert adapter.supports_tuning_type(TuningType.CLUSTERING) is True
        assert adapter.supports_tuning_type(TuningType.PRIMARY_KEYS) is True
        assert adapter.supports_tuning_type(TuningType.FOREIGN_KEYS) is True
        assert adapter.supports_tuning_type(TuningType.DISTRIBUTION) is False
        assert adapter.supports_tuning_type(TuningType.SORTING) is False

    def test_get_platform_info_basic(self, pg_duckdb_stubs):
        adapter = PgDuckDBAdapter(
            execution_engine="duckdb",
            postgres_scan_threads=4,
        )

        info = adapter.get_platform_info(connection=None)

        assert info["platform_type"] == "pg_duckdb"
        assert info["platform_name"] == "pg_duckdb"
        assert info["configuration"]["force_execution"] is True
        assert info["configuration"]["postgres_scan_threads"] == 4
        assert info["configuration"]["deployment_mode"] == "self-hosted"
        assert info["configuration"]["execution_engine"]["requested"] == "duckdb"
        assert info["configuration"]["execution_engine"]["applied"] == "duckdb"

    def test_platform_factory_applies_requested_engine(self, pg_duckdb_stubs):
        from benchbox.platforms.adapter_factory import get_adapter

        adapter = get_adapter("pg-duckdb", execution_engine="postgres")
        receipt = adapter.get_platform_info()["configuration"]["execution_engine"]

        assert adapter.force_execution is False
        assert receipt["requested"] == "postgres"
        assert receipt["applied"] == "postgres"
        assert receipt["resolution"] == "explicit"


class TestPgDuckDBExtensionVerification:
    def test_create_connection_verifies_extension(self, pg_duckdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchone.side_effect = [
            None,
            None,
            ("1.1.0",),
            (1,),
        ]

        pg_duckdb_stubs.connect.return_value = mock_conn

        adapter = PgDuckDBAdapter()

        with (
            patch.object(adapter, "check_server_database_exists", return_value=True),
            patch.object(adapter, "handle_existing_database"),
        ):
            adapter.create_connection()

        calls = [str(call) for call in mock_cursor.execute.call_args_list]
        extension_check = any("pg_duckdb" in call.lower() for call in calls)
        assert extension_check

    def test_create_connection_sets_force_execution(self, pg_duckdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchone.side_effect = [
            None,
            None,
            ("1.0.0",),
            (1,),
        ]

        pg_duckdb_stubs.connect.return_value = mock_conn

        adapter = PgDuckDBAdapter(execution_engine="duckdb")

        with (
            patch.object(adapter, "check_server_database_exists", return_value=True),
            patch.object(adapter, "handle_existing_database"),
        ):
            adapter.create_connection()

        calls = [str(call) for call in mock_cursor.execute.call_args_list]
        force_exec_set = any("force_execution" in call for call in calls)
        assert force_exec_set

    def test_postgres_engine_sets_force_execution_false(self, pg_duckdb_stubs):
        adapter = PgDuckDBAdapter(execution_engine="postgres", tuning_enabled=True)
        cursor = Mock()
        adapter._apply_pgduckdb_session_gucs(cursor)
        cursor.execute.assert_called_once_with("SET duckdb.force_execution = false")

        connection = Mock()
        connection.cursor.return_value = cursor
        cursor.reset_mock()
        with patch.object(postgresql_module.PostgreSQLAdapter, "configure_for_benchmark"):
            adapter.configure_for_benchmark(connection, "olap")
        cursor.execute.assert_called_once_with("SET duckdb.force_execution = false")

    def test_create_connection_sets_thread_count(self, pg_duckdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchone.side_effect = [
            None,
            None,
            ("1.0.0",),
            (1,),
        ]

        pg_duckdb_stubs.connect.return_value = mock_conn

        adapter = PgDuckDBAdapter(postgres_scan_threads=8)

        with (
            patch.object(adapter, "check_server_database_exists", return_value=True),
            patch.object(adapter, "handle_existing_database"),
        ):
            adapter.create_connection()

        calls = [str(call) for call in mock_cursor.execute.call_args_list]
        threads_set = any("threads_for_postgres_scan" in call for call in calls)
        assert threads_set

    def test_new_stream_connection_reapplies_force_execution(self, pg_duckdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        pg_duckdb_stubs.connect.return_value = mock_conn

        adapter = PgDuckDBAdapter(execution_engine="duckdb")

        stream_conn = adapter.new_stream_connection(Mock())

        assert stream_conn is mock_conn
        calls = [str(call) for call in mock_cursor.execute.call_args_list]
        assert any("duckdb.force_execution" in call for call in calls), calls

    def test_create_connection_raises_when_extension_missing(self, pg_duckdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchone.side_effect = [
            None,
            None,
            None,
            None,
            (1,),
        ]

        pg_duckdb_stubs.connect.return_value = mock_conn

        adapter = PgDuckDBAdapter()

        with (
            patch.object(adapter, "check_server_database_exists", return_value=True),
            patch.object(adapter, "handle_existing_database"),
            pytest.raises(RuntimeError, match="pg_duckdb extension is not available"),
        ):
            adapter.create_connection()


class TestPgDuckDBMotherDuckMode:
    def test_motherduck_mode_requires_token(self, pg_duckdb_stubs, monkeypatch):
        monkeypatch.delenv("MOTHERDUCK_TOKEN", raising=False)

        with pytest.raises(ValueError, match="MotherDuck deployment mode requires authentication token"):
            PgDuckDBAdapter(deployment_mode="motherduck")

    def test_motherduck_mode_accepts_config_token(self, pg_duckdb_stubs, monkeypatch):
        monkeypatch.delenv("MOTHERDUCK_TOKEN", raising=False)

        adapter = PgDuckDBAdapter(
            deployment_mode="motherduck",
            motherduck_token="test_token_123",
        )

        assert adapter.deployment_mode == "motherduck"
        assert adapter.motherduck_token == "test_token_123"

    def test_motherduck_mode_accepts_env_token(self, pg_duckdb_stubs, monkeypatch):
        monkeypatch.setenv("MOTHERDUCK_TOKEN", "env_token_456")

        adapter = PgDuckDBAdapter(deployment_mode="motherduck")

        assert adapter.deployment_mode == "motherduck"
        assert adapter.motherduck_token == "env_token_456"

    def test_invalid_deployment_mode_raises(self, pg_duckdb_stubs):
        with pytest.raises(ValueError, match="does not support deployment mode"):
            PgDuckDBAdapter(deployment_mode="invalid")

    def test_create_connection_sets_motherduck_token(self, pg_duckdb_stubs, monkeypatch):
        monkeypatch.delenv("MOTHERDUCK_TOKEN", raising=False)

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchone.side_effect = [
            None,
            None,
            ("1.0.0",),
            (1,),
        ]

        pg_duckdb_stubs.connect.return_value = mock_conn

        adapter = PgDuckDBAdapter(
            deployment_mode="motherduck",
            motherduck_token="md_token_789",
        )

        with (
            patch.object(adapter, "check_server_database_exists", return_value=True),
            patch.object(adapter, "handle_existing_database"),
        ):
            adapter.create_connection()

        calls = [str(call) for call in mock_cursor.execute.call_args_list]
        token_set = any("motherduck_token" in call for call in calls)
        assert token_set

    def test_create_connection_token_set_uses_sql_literal(self, pg_duckdb_stubs, monkeypatch):
        monkeypatch.delenv("MOTHERDUCK_TOKEN", raising=False)

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchone.side_effect = [
            None,
            None,
            ("1.0.0",),
            (1,),
        ]

        pg_duckdb_stubs.connect.return_value = mock_conn

        adapter = PgDuckDBAdapter(
            deployment_mode="motherduck",
            motherduck_token="md_token_789",
        )

        with (
            patch.object(adapter, "check_server_database_exists", return_value=True),
            patch.object(adapter, "handle_existing_database"),
        ):
            adapter.create_connection()

        from psycopg import sql as psycopg_sql

        set_calls = [
            call
            for call in mock_cursor.execute.call_args_list
            if isinstance(call[0][0], (psycopg_sql.Composed, psycopg_sql.SQL))
        ]
        assert len(set_calls) >= 1, "Expected at least one psycopg.sql.Composed execute call for token SET"


class TestPgDuckDBRegistration:
    def test_pg_duckdb_in_platform_registry(self, pg_duckdb_stubs):
        from benchbox.core.platform_registry import PlatformRegistry, auto_register_platforms

        auto_register_platforms()

        assert "pg-duckdb" in PlatformRegistry._adapters
        assert PlatformRegistry._adapters["pg-duckdb"] == PgDuckDBAdapter

    def test_pg_duckdb_metadata(self, pg_duckdb_stubs):
        from benchbox.core.platform_registry import PlatformRegistry

        metadata = PlatformRegistry._build_platform_metadata()

        assert "pg-duckdb" in metadata
        assert metadata["pg-duckdb"]["display_name"] == "pg_duckdb"
        assert metadata["pg-duckdb"]["category"] == "olap"
        assert "olap" in metadata["pg-duckdb"]["supports"]
        assert "analytics" in metadata["pg-duckdb"]["supports"]


class TestPgDuckDBConfigBuilder:
    def test_config_builder_basic(self, pg_duckdb_stubs):
        from benchbox.platforms.pg_duckdb import _build_pg_duckdb_config

        options = {
            "host": "localhost",
            "port": 5432,
            "force_execution": True,
            "postgres_scan_threads": 4,
        }
        overrides = {"scale_factor": 1.0}

        config = _build_pg_duckdb_config("pg-duckdb", options, overrides, None)

        assert config.host == "localhost"
        assert config.port == 5432
        assert config.force_execution is True
        assert config.postgres_scan_threads == 4
        assert config.scale_factor == 1.0

    def test_config_builder_defaults(self, pg_duckdb_stubs):
        from benchbox.platforms.pg_duckdb import _build_pg_duckdb_config

        config = _build_pg_duckdb_config("pg-duckdb", {}, {}, None)

        assert config.host == "localhost"
        assert config.port == 5432
        assert config.username == "postgres"
        assert config.admin_database == "postgres"
        assert config.sslmode == "prefer"
        assert config.work_mem == "256MB"
        assert config.maintenance_work_mem == "512MB"
        assert config.effective_cache_size == "1GB"
        assert config.max_parallel_workers_per_gather == 2
        assert config.force_execution is None
        assert config.postgres_scan_threads == 0

    def test_platform_option_defaults_keep_numeric_types(self, pg_duckdb_stubs):
        from benchbox.cli.platform_hooks import PlatformHookRegistry
        from benchbox.platforms.pg_duckdb import _build_pg_duckdb_config

        options = PlatformHookRegistry.parse_options("pg-duckdb", [])

        assert options["port"] == 5432
        assert "force_execution" not in options
        assert options["postgres_scan_threads"] == 0
        adapter = PgDuckDBAdapter.from_config(options)
        assert adapter.postgres_scan_threads == 0
        assert adapter.compare_native is False
        config = _build_pg_duckdb_config("pg-duckdb", options, {}, None)
        assert config.compare_native is False
        assert config.options["schema"] == "public"


class TestPgDuckDBFromConfigPassthrough:
    def test_from_config_passes_deployment_mode(self, pg_duckdb_stubs):
        from benchbox.platforms.pg_duckdb import PgDuckDBAdapter

        config = {"deployment_mode": "motherduck", "motherduck_token": "test_token_123"}
        adapter = PgDuckDBAdapter.from_config(config)

        assert adapter.deployment_mode == "motherduck"
        assert adapter.motherduck_token == "test_token_123"

    def test_from_config_default_deployment_mode(self, pg_duckdb_stubs):
        from benchbox.platforms.pg_duckdb import PgDuckDBAdapter

        adapter = PgDuckDBAdapter.from_config({})

        assert adapter.deployment_mode == "self-hosted"


class TestPgDuckDBCreateConnectionReraise:
    def test_create_connection_reraises_on_guc_failure(self, pg_duckdb_stubs):
        from benchbox.platforms.pg_duckdb import PgDuckDBAdapter

        adapter = PgDuckDBAdapter(host="localhost", database="test")

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("0.1.0",)
        mock_cursor.execute.side_effect = [
            None,
            Exception("GUC not available"),
        ]

        with patch.object(type(adapter).__bases__[0], "create_connection", return_value=mock_conn):
            with pytest.raises(RuntimeError, match="pg_duckdb configuration failed"):
                adapter.create_connection()


class TestPgDuckDBNativeComparison:
    def test_returns_none_when_compare_native_false(self, pg_duckdb_stubs):
        adapter = PgDuckDBAdapter()
        assert adapter.compare_native is False

        result = adapter.run_native_comparison([], {}, scale_factor=1.0)

        assert result is None

    def test_compare_native_flag_from_string_true(self, pg_duckdb_stubs):
        adapter = PgDuckDBAdapter.from_config({"compare_native": "true"})

        assert adapter.compare_native is True

    def test_compare_native_flag_from_string_false(self, pg_duckdb_stubs):
        adapter = PgDuckDBAdapter.from_config({"compare_native": "false"})

        assert adapter.compare_native is False

    def test_duckdb_db_path_stored(self, pg_duckdb_stubs):
        adapter = PgDuckDBAdapter.from_config({"duckdb_db_path": "/tmp/test.duckdb"})

        assert adapter.duckdb_db_path == "/tmp/test.duckdb"

    def test_returns_none_when_no_matching_query_results(self, pg_duckdb_stubs):
        adapter = PgDuckDBAdapter(compare_native=True)

        query_results = [{"query_id": "Q99", "ms": 100.0, "run_type": "measurement"}]
        query_sql_map = {"Q1": "SELECT 1"}

        mock_duckdb = Mock()
        mock_conn = Mock()
        mock_duckdb.connect.return_value = mock_conn

        with patch.dict("sys.modules", {"duckdb": mock_duckdb}):
            result = adapter.run_native_comparison(query_results, query_sql_map, scale_factor=1.0)

        assert result is None

    def test_config_builder_passes_compare_native(self, pg_duckdb_stubs):
        from benchbox.platforms.pg_duckdb import _build_pg_duckdb_config

        config = _build_pg_duckdb_config(
            "pg-duckdb",
            {"compare_native": "true", "duckdb_db_path": "/data/tpch.duckdb"},
            {},
            None,
        )

        assert config.compare_native == "true"
        assert config.duckdb_db_path == "/data/tpch.duckdb"

    def test_native_comparison_with_duckdb(self, pg_duckdb_stubs):
        from benchbox.core.results.models import NativeComparison

        adapter = PgDuckDBAdapter(compare_native=True)

        query_results = [
            {"query_id": "Q1", "ms": 100.0, "run_type": "measurement"},
            {"query_id": "Q6", "ms": 50.0, "run_type": "measurement"},
        ]
        query_sql_map = {"Q1": "SELECT 1", "Q6": "SELECT 2+2"}

        mock_duckdb = Mock()
        mock_conn = Mock()
        mock_duckdb.connect.return_value = mock_conn
        mock_conn.execute.return_value.fetchall.return_value = []

        with patch.dict("sys.modules", {"duckdb": mock_duckdb}):
            result = adapter.run_native_comparison(query_results, query_sql_map, scale_factor=1.0)

        assert isinstance(result, NativeComparison)
        assert result.total_queries == 2
        assert result.scale_factor == 1.0
        assert len(result.entries) == 2
        entry_ids = {e.query_id for e in result.entries}
        assert entry_ids == {"Q1", "Q6"}

    def test_warmup_rows_excluded_from_comparison(self, pg_duckdb_stubs):
        adapter = PgDuckDBAdapter(compare_native=True)

        query_results = [
            {"query_id": "Q1", "ms": 200.0, "run_type": "warmup"},
            {"query_id": "Q1", "ms": 100.0, "run_type": "measurement"},
        ]
        query_sql_map = {"Q1": "SELECT 1"}

        mock_duckdb = Mock()
        mock_conn = Mock()
        mock_duckdb.connect.return_value = mock_conn
        mock_conn.execute.return_value.fetchall.return_value = []

        with patch.dict("sys.modules", {"duckdb": mock_duckdb}):
            result = adapter.run_native_comparison(query_results, query_sql_map, scale_factor=0.1)

        assert result is not None
        assert result.entries[0].pg_duckdb_ms == 100.0

    def test_returns_none_when_duckdb_not_importable(self, pg_duckdb_stubs):
        adapter = PgDuckDBAdapter(compare_native=True)
        query_results = [{"query_id": "Q1", "ms": 100.0, "run_type": "measurement"}]
        query_sql_map = {"Q1": "SELECT 1"}

        with patch.dict("sys.modules", {"duckdb": None}):
            result = adapter.run_native_comparison(query_results, query_sql_map, scale_factor=1.0)

        assert result is None
