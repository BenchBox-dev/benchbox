# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import inspect
from unittest.mock import Mock, patch

import pytest

import benchbox.platforms.pg_mooncake as pg_mooncake_module
import benchbox.platforms.postgresql as postgresql_module
from benchbox.platforms.pg_mooncake import PgMooncakeAdapter
from benchbox.platforms.postgresql import POSTGRES_DIALECT, PostgreSQLAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def pg_mooncake_stubs(monkeypatch):
    mock_psycopg = Mock()
    mock_psycopg.__version__ = "3.1.0"

    monkeypatch.setattr(pg_mooncake_module, "psycopg", mock_psycopg)
    monkeypatch.setattr(postgresql_module, "psycopg", mock_psycopg)

    return mock_psycopg


class TestPgMooncakeAdapter:
    def test_initialization_defaults(self, pg_mooncake_stubs):
        adapter = PgMooncakeAdapter()

        assert adapter.platform_name == "pg_mooncake"
        assert adapter.get_target_dialect() == POSTGRES_DIALECT
        assert adapter.host == "localhost"
        assert adapter.port == 5432
        assert adapter.database == "benchbox"
        assert adapter.username == "postgres"
        assert adapter.schema == "public"
        assert adapter.storage_mode == "local"
        assert adapter.mooncake_bucket is None

    def test_initialization_with_config(self, pg_mooncake_stubs):
        adapter = PgMooncakeAdapter(
            host="mooncake.example.com",
            port=5433,
            database="analytics_db",
            username="custom_user",
            password="secret",
            schema="analytics",
            storage_mode="local",
        )

        assert adapter.host == "mooncake.example.com"
        assert adapter.port == 5433
        assert adapter.database == "analytics_db"
        assert adapter.username == "custom_user"
        assert adapter.password == "secret"
        assert adapter.schema == "analytics"
        assert adapter.storage_mode == "local"

    def test_dialect_is_postgres(self, pg_mooncake_stubs):
        adapter = PgMooncakeAdapter()

        assert adapter.get_target_dialect() == POSTGRES_DIALECT
        assert adapter.get_target_dialect() == "postgres"

    def test_from_config_basic(self, pg_mooncake_stubs):
        config = {
            "host": "mooncake.local",
            "port": 5433,
            "database": "test_analytics",
            "storage_mode": "local",
        }

        adapter = PgMooncakeAdapter.from_config(config)

        assert adapter.host == "mooncake.local"
        assert adapter.port == 5433
        assert adapter.database == "test_analytics"
        assert adapter.storage_mode == "local"

    def test_from_config_generates_database_name(self, pg_mooncake_stubs):
        config = {
            "benchmark": "tpch",
            "scale_factor": 1.0,
        }

        adapter = PgMooncakeAdapter.from_config(config)

        assert "benchbox" in adapter.database

    def test_from_config_uses_provided_database(self, pg_mooncake_stubs):
        config = {
            "database": "explicit_db",
            "benchmark": "tpch",
            "scale_factor": 1.0,
        }

        adapter = PgMooncakeAdapter.from_config(config)

        assert adapter.database == "explicit_db"

    def test_inherits_postgresql_connection_params(self, pg_mooncake_stubs):
        adapter = PgMooncakeAdapter(
            host="mooncake.example.com",
            port=5433,
            database="testdb",
            username="testuser",
            password="testpass",
            sslmode="require",
            connect_timeout=15,
        )

        params = adapter._get_connection_params()

        assert params["host"] == "mooncake.example.com"
        assert params["port"] == 5433
        assert params["dbname"] == "testdb"
        assert params["user"] == "testuser"
        assert params["password"] == "testpass"
        assert params["sslmode"] == "require"
        assert params["connect_timeout"] == 15

    def test_supports_tuning_type(self, pg_mooncake_stubs):
        adapter = PgMooncakeAdapter()

        from benchbox.core.tuning.interface import TuningType

        assert adapter.supports_tuning_type(TuningType.CLUSTERING) is True
        assert adapter.supports_tuning_type(TuningType.SORTING) is True
        assert adapter.supports_tuning_type(TuningType.PARTITIONING) is False
        assert adapter.supports_tuning_type(TuningType.DISTRIBUTION) is False
        assert adapter.supports_tuning_type(TuningType.PRIMARY_KEYS) is False
        assert adapter.supports_tuning_type(TuningType.FOREIGN_KEYS) is False

    def test_get_platform_info_basic(self, pg_mooncake_stubs):
        adapter = PgMooncakeAdapter(storage_mode="local")

        info = adapter.get_platform_info(connection=None)

        assert info["platform_type"] == "pg_mooncake"
        assert info["platform_name"] == "pg_mooncake"
        assert info["configuration"]["storage_mode"] == "local"


class TestPgMooncakeColumnstoreDDL:
    def test_add_columnstore_basic(self, pg_mooncake_stubs):
        adapter = PgMooncakeAdapter()

        result = adapter._add_columnstore_access_method("CREATE TABLE foo (id INT, name TEXT);")

        assert "USING mooncake" in result
        assert result.endswith(";")

    def test_add_columnstore_no_semicolon(self, pg_mooncake_stubs):
        adapter = PgMooncakeAdapter()

        result = adapter._add_columnstore_access_method("CREATE TABLE foo (id INT)")

        assert "USING mooncake" in result

    def test_add_columnstore_already_present(self, pg_mooncake_stubs):
        adapter = PgMooncakeAdapter()

        ddl = "CREATE TABLE foo (id INT) USING mooncake;"
        result = adapter._add_columnstore_access_method(ddl)

        assert result.count("USING mooncake") == 1

    def test_add_columnstore_skips_non_create_table(self, pg_mooncake_stubs):
        adapter = PgMooncakeAdapter()

        alter = "ALTER TABLE foo ADD COLUMN bar TEXT;"
        assert adapter._add_columnstore_access_method(alter) == alter

        index = "CREATE INDEX idx ON foo (id);"
        assert adapter._add_columnstore_access_method(index) == index

    def test_create_schema_adds_columnstore(self, pg_mooncake_stubs):
        adapter = PgMooncakeAdapter()
        conn = Mock()
        cursor = Mock()
        conn.cursor.return_value = cursor
        schema_sql = (
            "CREATE TABLE foo (id INT, name TEXT);"
            "CREATE INDEX idx_foo ON foo (id);"
            "CREATE TABLE bar (x BIGINT, y DECIMAL(10,2));"
        )

        with patch.object(adapter, "_create_schema_with_tuning", return_value=schema_sql):
            adapter.create_schema(Mock(), conn)

        executed = [call.args[0] for call in cursor.execute.call_args_list]
        assert executed[0] == "CREATE TABLE foo (id INT, name TEXT)"
        assert executed[1] == "CREATE INDEX idx_foo ON foo (id)"
        assert executed[2] == "CREATE TABLE bar (x BIGINT, y DECIMAL(10,2))"
        conn.commit.assert_called_once()
        cursor.close.assert_called_once()

    def test_create_schema_signature_matches_postgresql_parent(self, pg_mooncake_stubs):
        assert inspect.signature(PgMooncakeAdapter.create_schema) == inspect.signature(
            postgresql_module.PostgreSQLAdapter.create_schema
        )

    def test_create_schema_fk_strip_retry_preserves_heap_table(self, pg_mooncake_stubs):
        adapter = PgMooncakeAdapter()
        conn = Mock()
        cursor = Mock()
        conn.cursor.return_value = cursor
        cursor.execute.side_effect = [
            Exception("FK violation"),
            None,
        ]
        schema_sql = "CREATE TABLE foo (id INT, ref_id INT, FOREIGN KEY (ref_id) REFERENCES bar(id));"

        with patch.object(adapter, "_create_schema_with_tuning", return_value=schema_sql):
            adapter.create_schema(Mock(), conn)

        executed = [call.args[0] for call in cursor.execute.call_args_list]
        assert len(executed) == 2
        assert "USING mooncake" not in executed[0]
        assert "FOREIGN KEY" in executed[0]
        assert "USING mooncake" not in executed[1]
        assert "FOREIGN KEY" not in executed[1]
        conn.rollback.assert_called_once()


class TestPgMooncakeExtensionVerification:
    def test_create_connection_verifies_extension(self, pg_mooncake_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchone.side_effect = [
            None,
            None,
            ("0.5.0",),
            (1,),
        ]

        pg_mooncake_stubs.connect.return_value = mock_conn

        adapter = PgMooncakeAdapter()

        with (
            patch.object(adapter, "check_server_database_exists", return_value=True),
            patch.object(adapter, "handle_existing_database"),
        ):
            adapter.create_connection()

        calls = [str(call) for call in mock_cursor.execute.call_args_list]
        extension_check = any("pg_mooncake" in call.lower() for call in calls)
        assert extension_check

    def test_create_connection_raises_when_extension_missing(self, pg_mooncake_stubs):
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

        pg_mooncake_stubs.connect.return_value = mock_conn

        adapter = PgMooncakeAdapter()

        with (
            patch.object(adapter, "check_server_database_exists", return_value=True),
            patch.object(adapter, "handle_existing_database"),
            pytest.raises(RuntimeError, match="pg_mooncake extension is not available"),
        ):
            adapter.create_connection()

    def test_create_connection_uses_cascade_when_creating_extension(self, pg_mooncake_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchone.side_effect = [
            (1,),
            None,
            ("0.5.0",),
        ]

        pg_mooncake_stubs.connect.return_value = mock_conn

        adapter = PgMooncakeAdapter()

        with (
            patch.object(adapter, "check_server_database_exists", return_value=True),
            patch.object(adapter, "handle_existing_database"),
        ):
            adapter.create_connection()

        calls = [str(call) for call in mock_cursor.execute.call_args_list]
        assert any("CREATE EXTENSION IF NOT EXISTS pg_mooncake CASCADE" in call for call in calls)


class TestPgMooncakeLoadPromotion:
    def test_load_data_promotes_loaded_tables_to_mooncake_mirrors(self, pg_mooncake_stubs, tmp_path):
        adapter = PgMooncakeAdapter()
        conn = Mock()
        cursor = Mock()
        cursor.fetchone.side_effect = [(2,), (3,)]
        conn.cursor.return_value = cursor

        with patch.object(
            postgresql_module.PostgreSQLAdapter,
            "load_data",
            return_value=({"orders": 2, "lineitem": 3, "empty": 0}, 1.25, None),
        ) as parent_load:
            stats, load_time, extra = adapter.load_data(Mock(), conn, tmp_path)

        parent_load.assert_called_once()
        assert load_time == 1.25
        assert extra is None
        assert stats == {"orders": 2, "lineitem": 3, "empty": 0}

        executed = [call.args for call in cursor.execute.call_args_list]
        assert ('ALTER TABLE "orders" RENAME TO "__bb_moon_src_orders"',) in executed
        assert ("CALL mooncake.create_table(%s, %s)", ("orders", "__bb_moon_src_orders")) in executed
        assert ('SELECT COUNT(*) FROM "orders"',) in executed
        assert ('ALTER TABLE "lineitem" RENAME TO "__bb_moon_src_lineitem"',) in executed
        assert ("CALL mooncake.create_table(%s, %s)", ("lineitem", "__bb_moon_src_lineitem")) in executed
        assert ('SELECT COUNT(*) FROM "lineitem"',) in executed
        assert all("empty" not in str(args) for args in executed)
        cursor.close.assert_called_once()

    def test_load_data_rolls_back_on_promotion_failure(self, pg_mooncake_stubs, tmp_path):
        adapter = PgMooncakeAdapter()
        conn = Mock()
        cursor = Mock()
        cursor.execute.side_effect = RuntimeError("promotion failed")
        conn.cursor.return_value = cursor

        with (
            patch.object(
                postgresql_module.PostgreSQLAdapter,
                "load_data",
                return_value=({"orders": 2}, 1.25, None),
            ),
            pytest.raises(RuntimeError, match="promotion failed"),
        ):
            adapter.load_data(Mock(), conn, tmp_path)

        conn.rollback.assert_called()
        cursor.close.assert_called_once()

    def test_rename_is_committed_before_the_mirror_is_created(self, pg_mooncake_stubs, tmp_path):
        adapter = PgMooncakeAdapter()
        conn = Mock()
        cursor = Mock()
        events: list[str] = []
        cursor.execute.side_effect = lambda *args: events.append(f"execute:{args[0].split('(')[0].split(' RENAME')[0]}")
        conn.commit.side_effect = lambda: events.append("commit")
        cursor.fetchone.return_value = (2,)
        conn.cursor.return_value = cursor

        with patch.object(postgresql_module.PostgreSQLAdapter, "load_data", return_value=({"orders": 2}, 1.25, None)):
            adapter.load_data(Mock(), conn, tmp_path)

        assert events[:3] == ['execute:ALTER TABLE "orders"', "commit", "execute:CALL mooncake.create_table"]

    def test_failed_mirror_restores_the_heap_table_name(self, pg_mooncake_stubs, tmp_path):
        adapter = PgMooncakeAdapter()
        conn = Mock()
        cursor = Mock()
        cursor.execute.side_effect = [None, RuntimeError("mirror creation failed"), None]
        conn.cursor.return_value = cursor

        with (
            patch.object(
                postgresql_module.PostgreSQLAdapter,
                "load_data",
                return_value=({"orders": 2}, 1.25, None),
            ),
            pytest.raises(RuntimeError, match="mirror creation failed"),
        ):
            adapter.load_data(Mock(), conn, tmp_path)

        executed = [call.args for call in cursor.execute.call_args_list]
        assert executed == [
            ('ALTER TABLE "orders" RENAME TO "__bb_moon_src_orders"',),
            ("CALL mooncake.create_table(%s, %s)", ("orders", "__bb_moon_src_orders")),
            ('ALTER TABLE "__bb_moon_src_orders" RENAME TO "orders"',),
        ]
        cursor.close.assert_called_once()

    def test_load_data_rejects_invalid_schema_identifier(self, pg_mooncake_stubs, tmp_path):
        adapter = PgMooncakeAdapter(schema='public";drop')
        conn = Mock()

        with (
            patch.object(
                postgresql_module.PostgreSQLAdapter,
                "load_data",
                return_value=({"orders": 2}, 1.25, None),
            ),
            pytest.raises(ValueError, match="Invalid pg_mooncake schema identifier"),
        ):
            adapter.load_data(Mock(), conn, tmp_path)

        conn.cursor.assert_not_called()


class TestPgMooncakeValidationCatalog:
    def test_execute_query_commits_around_mooncake_scan(self, pg_mooncake_stubs):
        adapter = PgMooncakeAdapter()
        conn = Mock()
        expected = {"query_id": "1", "status": "SUCCESS"}

        with patch.object(postgresql_module.PostgreSQLAdapter, "execute_query", return_value=expected) as parent:
            result = adapter.execute_query(conn, "SELECT * FROM rankings", "1")

        assert result == expected
        parent.assert_called_once_with(
            conn,
            "SELECT * FROM rankings",
            "1",
            benchmark_type=None,
            scale_factor=None,
            validate_row_count=True,
            stream_id=None,
        )
        assert conn.commit.call_count == 2
        conn.rollback.assert_not_called()

    def test_execute_query_retries_duckdb_function_transaction_error(self, pg_mooncake_stubs):
        adapter = PgMooncakeAdapter()
        conn = Mock()
        failed = {
            "query_id": "1",
            "status": "FAILED",
            "error": "DuckDB execution is not supported inside functions",
        }
        expected = {"query_id": "1", "status": "SUCCESS"}

        with patch.object(
            postgresql_module.PostgreSQLAdapter,
            "execute_query",
            side_effect=[failed, expected],
        ) as parent:
            result = adapter.execute_query(conn, "SELECT * FROM rankings", "1")

        assert result == expected
        assert parent.call_count == 2
        conn.rollback.assert_called_once()
        assert conn.commit.call_count == 2

    def test_get_existing_tables_commits_catalog_transaction(self, pg_mooncake_stubs):
        adapter = PgMooncakeAdapter()
        conn = Mock()
        cursor = Mock()
        cursor.fetchall.return_value = [("Customer",), ("__bb_moon_src_customer",)]
        conn.cursor.return_value = cursor

        tables = adapter._get_existing_tables(conn)

        assert tables == ["customer", "__bb_moon_src_customer"]
        cursor.execute.assert_called_once()
        conn.commit.assert_called_once()
        conn.rollback.assert_not_called()
        cursor.close.assert_called_once()

    def test_get_existing_tables_rolls_back_catalog_failure(self, pg_mooncake_stubs):
        adapter = PgMooncakeAdapter()
        conn = Mock()
        cursor = Mock()
        cursor.execute.side_effect = RuntimeError("catalog failed")
        conn.cursor.return_value = cursor

        tables = adapter._get_existing_tables(conn)

        assert tables == []
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()
        cursor.close.assert_called_once()


class TestPgMooncakeStorageConfig:
    def test_s3_mode_requires_bucket(self, pg_mooncake_stubs, monkeypatch):
        monkeypatch.delenv("MOONCAKE_S3_BUCKET", raising=False)

        with pytest.raises(ValueError, match="S3 storage mode requires bucket configuration"):
            PgMooncakeAdapter(storage_mode="s3")

    def test_s3_mode_accepts_config_bucket(self, pg_mooncake_stubs, monkeypatch):
        monkeypatch.delenv("MOONCAKE_S3_BUCKET", raising=False)

        adapter = PgMooncakeAdapter(
            storage_mode="s3",
            mooncake_bucket="s3://my-bucket/mooncake-data",
        )

        assert adapter.storage_mode == "s3"
        assert adapter.mooncake_bucket == "s3://my-bucket/mooncake-data"

    def test_s3_mode_accepts_env_bucket(self, pg_mooncake_stubs, monkeypatch):
        monkeypatch.setenv("MOONCAKE_S3_BUCKET", "s3://env-bucket/data")

        adapter = PgMooncakeAdapter(storage_mode="s3")

        assert adapter.storage_mode == "s3"
        assert adapter.mooncake_bucket == "s3://env-bucket/data"

    def test_invalid_storage_mode_raises(self, pg_mooncake_stubs):
        with pytest.raises(ValueError, match="Invalid pg_mooncake storage mode"):
            PgMooncakeAdapter(storage_mode="gcs")

    def test_create_connection_sets_bucket(self, pg_mooncake_stubs, monkeypatch):
        monkeypatch.delenv("MOONCAKE_S3_BUCKET", raising=False)

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchone.side_effect = [
            None,
            None,
            ("0.5.0",),
            (1,),
        ]

        pg_mooncake_stubs.connect.return_value = mock_conn

        adapter = PgMooncakeAdapter(
            storage_mode="s3",
            mooncake_bucket="s3://test-bucket/data",
        )

        with (
            patch.object(adapter, "check_server_database_exists", return_value=True),
            patch.object(adapter, "handle_existing_database"),
        ):
            adapter.create_connection()

        calls = [str(call) for call in mock_cursor.execute.call_args_list]
        bucket_set = any("default_bucket" in call for call in calls)
        assert bucket_set

    def test_create_connection_bucket_set_uses_sql_literal(self, pg_mooncake_stubs, monkeypatch):
        monkeypatch.delenv("MOONCAKE_S3_BUCKET", raising=False)

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchone.side_effect = [
            None,
            None,
            ("0.5.0",),
            (1,),
        ]

        pg_mooncake_stubs.connect.return_value = mock_conn

        adapter = PgMooncakeAdapter(
            storage_mode="s3",
            mooncake_bucket="s3://test-bucket/data",
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
        assert len(set_calls) >= 1, "Expected at least one psycopg.sql.Composed execute call for bucket SET"


class TestPgMooncakeRegistration:
    def test_pg_mooncake_in_platform_registry(self, pg_mooncake_stubs):
        from benchbox.core.platform_registry import PlatformRegistry, auto_register_platforms

        auto_register_platforms()

        assert "pg-mooncake" in PlatformRegistry._adapters
        assert PlatformRegistry._adapters["pg-mooncake"] == PgMooncakeAdapter

    def test_pg_mooncake_metadata(self, pg_mooncake_stubs):
        from benchbox.core.platform_registry import PlatformRegistry

        metadata = PlatformRegistry._build_platform_metadata()

        assert "pg-mooncake" in metadata
        assert metadata["pg-mooncake"]["display_name"] == "pg_mooncake"
        assert metadata["pg-mooncake"]["category"] == "olap"
        assert "columnstore" in metadata["pg-mooncake"]["supports"]


class TestPgMooncakeConfigBuilder:
    def test_config_builder_basic(self, pg_mooncake_stubs):
        from benchbox.platforms.pg_mooncake import _build_pg_mooncake_config

        options = {
            "host": "localhost",
            "port": 5432,
            "storage_mode": "local",
        }
        overrides = {"scale_factor": 1.0}

        config = _build_pg_mooncake_config("pg-mooncake", options, overrides, None)

        assert config.host == "localhost"
        assert config.port == 5432
        assert config.storage_mode == "local"
        assert config.scale_factor == 1.0

    def test_config_builder_defaults(self, pg_mooncake_stubs):
        from benchbox.platforms.pg_mooncake import _build_pg_mooncake_config

        config = _build_pg_mooncake_config("pg-mooncake", {}, {}, None)

        assert config.host == "localhost"
        assert config.port == 5432
        assert config.username == "postgres"
        assert config.admin_database == "postgres"
        assert config.sslmode == "prefer"
        assert config.work_mem == "256MB"
        assert config.maintenance_work_mem == "512MB"
        assert config.effective_cache_size == "1GB"
        assert config.max_parallel_workers_per_gather == 2
        assert config.storage_mode == "local"
        assert config.mooncake_bucket is None
        assert config.options["schema"] == "public"


class TestPgMooncakeMigrationPhase:
    def _make_mock_conn(self, table_names: list[str], storage_before: int = 8192, storage_after: int = 4096):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        fetchall_response = [(t,) for t in table_names]
        fetchone_responses = []
        for _ in table_names:
            fetchone_responses.append((storage_before,))
            fetchone_responses.append((storage_after,))

        mock_cursor.fetchall.return_value = fetchall_response
        mock_cursor.fetchone.side_effect = fetchone_responses
        return mock_conn, mock_cursor

    def test_returns_none_when_no_tables(self, pg_mooncake_stubs):
        adapter = PgMooncakeAdapter()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []

        result = adapter.run_migration_phase(mock_conn, table_names=[])

        assert result is None

    def test_returns_none_when_auto_discovery_finds_nothing(self, pg_mooncake_stubs):
        adapter = PgMooncakeAdapter()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []

        result = adapter.run_migration_phase(mock_conn, table_names=None)

        assert result is None

    def test_migrates_provided_tables(self, pg_mooncake_stubs):
        from benchbox.core.results.models import MigrationPhase

        adapter = PgMooncakeAdapter()
        mock_conn, mock_cursor = self._make_mock_conn(["lineitem", "orders"])

        result = adapter.run_migration_phase(mock_conn, table_names=["lineitem", "orders"])

        assert isinstance(result, MigrationPhase)
        assert result.tables_migrated == 2
        assert result.tables_failed == 0
        assert result.status == "completed"
        assert "lineitem" in result.per_table_stats
        assert "orders" in result.per_table_stats

    def test_storage_delta_computed_correctly(self, pg_mooncake_stubs):
        adapter = PgMooncakeAdapter()
        mock_conn, mock_cursor = self._make_mock_conn(["lineitem"], storage_before=8192, storage_after=4096)

        result = adapter.run_migration_phase(mock_conn, table_names=["lineitem"])

        assert result.storage_before_bytes == 8192
        assert result.storage_after_bytes == 4096
        assert result.storage_delta_bytes == -4096
        tbl = result.per_table_stats["lineitem"]
        assert tbl.storage_before_bytes == 8192
        assert tbl.storage_after_bytes == 4096
        assert tbl.storage_delta_bytes == -4096

    def test_failed_table_recorded_as_partial(self, pg_mooncake_stubs):
        from benchbox.core.results.models import MigrationPhase

        adapter = PgMooncakeAdapter()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchone.side_effect = [
            (8192,),
            (4096,),
            (8192,),
        ]

        call_count = [0]

        def execute_side_effect(sql, *args):
            if "SET ACCESS METHOD" in str(sql) and "orders" in str(sql):
                raise Exception("columnstore not available")
            call_count[0] += 1

        mock_cursor.execute.side_effect = execute_side_effect

        result = adapter.run_migration_phase(mock_conn, table_names=["lineitem", "orders"])

        assert isinstance(result, MigrationPhase)
        assert result.tables_migrated == 1
        assert result.tables_failed == 1
        assert result.status == "partial"
        assert result.per_table_stats["orders"].status == "failed"
        assert result.per_table_stats["orders"].error_message is not None

    def test_all_failed_status_is_failed(self, pg_mooncake_stubs):
        adapter = PgMooncakeAdapter()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (8192,)

        def execute_side_effect(sql, *args):
            if "SET ACCESS METHOD" in str(sql):
                raise Exception("columnar error")

        mock_cursor.execute.side_effect = execute_side_effect

        result = adapter.run_migration_phase(mock_conn, table_names=["lineitem"])

        assert result.status == "failed"
        assert result.tables_migrated == 0
        assert result.tables_failed == 1


class _MirrorCursor:
    def __init__(
        self,
        *,
        location="/var/lib/postgresql/18/docker/pg_mooncake",
        registered=True,
        readback=(4, 50000, 0.0001),
        fail_create=None,
        fail_count=False,
    ):
        self.executed: list[str] = []
        self.calls: list[tuple] = []
        self._location = location
        self._registered = registered
        self._readback = readback
        self._fail_create = fail_create
        self._fail_count = fail_count
        self._rows: list[tuple] = []
        self.closed = False

    def execute(self, sql, params=()):
        self.executed.append(sql)
        self.calls.append((sql, params))
        if "CALL mooncake.create_table" in sql and params and params[0] == self._fail_create:
            raise RuntimeError("mirror creation failed")
        if "SELECT COUNT(*)" in sql:
            if self._fail_count:
                raise RuntimeError("count failed")
            self._rows = [(self._readback[1],)]
        elif "mooncake.list_tables" in sql:
            self._rows = [(self._location,)] if self._registered else []
        elif "current_database" in sql:
            self._rows = [("benchbox",)]
        elif "duckdb.query" in sql:
            if isinstance(self._readback, Exception):
                raise self._readback
            self._rows = [self._readback]

    def fetchone(self):
        return self._rows[0]

    def fetchall(self):
        return self._rows

    def close(self):
        self.closed = True


def _mirror_adapter(pg_mooncake_stubs, *tables):
    from benchbox.core.tuning.applied_ledger import PHASE_POST_LOAD, AppliedTuningLedger
    from benchbox.core.tuning.interface import TableTuning, TuningColumn, UnifiedTuningConfiguration

    adapter = PgMooncakeAdapter()
    config = UnifiedTuningConfiguration()
    config.disable_all_constraints()
    ledger = AppliedTuningLedger()
    for table in tables:
        config.table_tunings[table] = TableTuning(
            table_name=table, sorting=[TuningColumn(name=f"{table[0]}_key", type="INTEGER", order=1)]
        )
        ledger.record(f"CREATE INDEX idx_{table} ON {table} ({table[0]}_key)", PHASE_POST_LOAD, table=table)
    adapter._applied_tuning_ledger = ledger
    return adapter, config, ledger


class TestPgMooncakeMirrorOrder:
    def test_clustered_heap_is_not_claimed_until_the_mirror_is_read_back(self, pg_mooncake_stubs):
        adapter, config, ledger = _mirror_adapter(pg_mooncake_stubs, "lineitem")

        adapter._record_cluster_satisfied(ledger, "lineitem", config, ["l_key"])

        assert ledger.satisfied == []
        assert ledger.dropped == []

    def test_ordered_mirror_files_satisfy_the_intent(self, pg_mooncake_stubs):
        adapter, config, ledger = _mirror_adapter(pg_mooncake_stubs, "lineitem")
        adapter._record_cluster_satisfied(ledger, "lineitem", config, ["l_key"])
        connection = Mock()
        connection.cursor.return_value = _MirrorCursor(readback=(4, 60175, 0.0002))

        adapter._settle_cluster_claims(connection, {"lineitem"}, {"lineitem": 60175})

        [satisfied] = ledger.satisfied
        assert satisfied.intent == "sorting:lineitem (l_key)"
        assert "mirror Parquet readback: 60175 rows in 4 data files" in satisfied.reason
        assert "indisclustered" in satisfied.reason
        assert ledger.dropped == []

    def test_unordered_mirror_files_drop_the_intent(self, pg_mooncake_stubs):
        adapter, config, ledger = _mirror_adapter(pg_mooncake_stubs, "lineitem")
        adapter._record_cluster_satisfied(ledger, "lineitem", config, ["l_key"])
        connection = Mock()
        connection.cursor.return_value = _MirrorCursor(readback=(4, 60175, 0.92))

        adapter._settle_cluster_claims(connection, {"lineitem"}, {"lineitem": 60175})

        assert ledger.satisfied == []
        assert "not ordered by ['l_key']" in ledger.dropped[0].reason

    def test_tables_that_were_not_mirrored_drop_the_intent(self, pg_mooncake_stubs):
        adapter, config, ledger = _mirror_adapter(pg_mooncake_stubs, "region")
        adapter._record_cluster_satisfied(ledger, "region", config, ["r_key"])
        connection = Mock()
        connection.cursor.return_value = _MirrorCursor()

        adapter._settle_cluster_claims(connection, set(), {})

        assert ledger.satisfied == []
        assert "was not mirrored" in ledger.dropped[0].reason

    @pytest.mark.parametrize(
        "cursor, reason",
        [
            (_MirrorCursor(registered=False), "not registered with moonlink"),
            (_MirrorCursor(readback=(0, 0, 0.0)), "no mirror data files found"),
            (_MirrorCursor(readback=RuntimeError("s3 access denied")), "s3 access denied"),
            (_MirrorCursor(location="/tmp/$evil"), "cannot be read back safely"),
        ],
    )
    def test_an_unreadable_mirror_is_never_claimed(self, pg_mooncake_stubs, cursor, reason):
        adapter, config, ledger = _mirror_adapter(pg_mooncake_stubs, "lineitem")
        adapter._record_cluster_satisfied(ledger, "lineitem", config, ["l_key"])
        connection = Mock()
        connection.cursor.return_value = cursor

        adapter._settle_cluster_claims(connection, {"lineitem"}, {"lineitem": 50000})

        assert ledger.satisfied == []
        assert reason in ledger.dropped[0].reason
        assert "columnstore mirror readback failed" in ledger.dropped[0].reason

    def test_readback_checks_the_whole_key_in_the_mirror_directory(self, pg_mooncake_stubs):
        adapter, _config, _ledger = _mirror_adapter(pg_mooncake_stubs)
        cursor = _MirrorCursor()

        adapter._mirror_order_evidence(cursor, "lineitem", ["l_orderkey", "l_linenumber"])

        query = next(sql for sql in cursor.executed if "duckdb.query" in sql)
        assert "/var/lib/postgresql/18/docker/pg_mooncake/benchbox/public.lineitem/data/*.parquet" in query
        assert "'c0': \"l_orderkey\", 'c1': \"l_linenumber\"" in query

    def test_a_mirror_holding_other_rows_than_the_table_is_never_claimed(self, pg_mooncake_stubs):
        adapter, config, ledger = _mirror_adapter(pg_mooncake_stubs, "lineitem")
        adapter._record_cluster_satisfied(ledger, "lineitem", config, ["l_key"])
        connection = Mock()
        connection.cursor.return_value = _MirrorCursor(readback=(8, 120350, 0.0001))

        adapter._settle_cluster_claims(connection, {"lineitem"}, {"lineitem": 60175})

        assert ledger.satisfied == []
        assert "hold 120350 rows, expected 60175" in ledger.dropped[0].reason

    @pytest.mark.parametrize("column", ['a"b', "a b", "a$mooncake_order$b", "a%s"])
    def test_columns_that_are_not_plain_identifiers_are_refused(self, pg_mooncake_stubs, column):
        adapter, _config, _ledger = _mirror_adapter(pg_mooncake_stubs)

        with pytest.raises(ValueError, match="not a plain identifier"):
            adapter._mirror_order_evidence(_MirrorCursor(), "lineitem", [column])

    def test_a_claim_with_no_recorded_index_statement_is_dropped_not_lost(self, pg_mooncake_stubs):
        adapter, config, ledger = _mirror_adapter(pg_mooncake_stubs, "lineitem")
        adapter._record_cluster_satisfied(ledger, "lineitem", config, ["l_key"])
        ledger.statements.clear()
        connection = Mock()
        connection.cursor.return_value = _MirrorCursor(readback=(4, 60175, 0.0001))

        adapter._settle_cluster_claims(connection, {"lineitem"}, {"lineitem": 60175})

        assert ledger.satisfied == []
        assert "no recorded CREATE INDEX statement" in ledger.dropped[0].reason

    def test_a_promotion_failure_still_resolves_every_claim(self, pg_mooncake_stubs):
        adapter, config, ledger = _mirror_adapter(pg_mooncake_stubs, "lineitem", "orders")
        cursor = _MirrorCursor(readback=(4, 60175, 0.0001), fail_create="orders")
        connection = Mock()
        connection.cursor.return_value = cursor

        def base_load(self, benchmark, connection, data_dir):
            adapter._record_cluster_satisfied(ledger, "lineitem", config, ["l_key"])
            adapter._record_cluster_satisfied(ledger, "orders", config, ["o_key"])
            return {"lineitem": 60175, "orders": 15000}, 1.0, None

        with (
            patch.object(PostgreSQLAdapter, "load_data", base_load),
            pytest.raises(RuntimeError, match="mirror creation failed"),
        ):
            adapter.load_data(Mock(), connection, "data")

        assert [item.intent for item in ledger.satisfied] == ["sorting:lineitem (l_key)"]
        assert "was not mirrored" in next(item for item in ledger.dropped if "orders" in item.intent).reason
        assert adapter._pending_cluster_claims == {}

    def test_only_tables_mirrored_by_this_load_can_be_claimed(self, pg_mooncake_stubs):
        adapter, config, ledger = _mirror_adapter(pg_mooncake_stubs, "lineitem")
        connection = Mock()
        connection.cursor.return_value = _MirrorCursor(readback=(4, 60175, 0.0001))

        def base_load(self, benchmark, connection, data_dir):
            adapter._record_cluster_satisfied(ledger, "lineitem", config, ["l_key"])
            return {"lineitem": 60175}, 1.0, None

        with (
            patch.object(PostgreSQLAdapter, "load_data", base_load),
            patch.object(
                adapter, "_promote_loaded_tables_to_mooncake", lambda connection, tables, stats, mirrored: stats
            ),
        ):
            adapter.load_data(Mock(), connection, "data")

        assert ledger.satisfied == []
        assert "was not mirrored" in ledger.dropped[0].reason

    def test_a_failure_after_the_mirror_exists_does_not_rename_the_heap_back(self, pg_mooncake_stubs):
        adapter, _config, _ledger = _mirror_adapter(pg_mooncake_stubs)
        connection = Mock()
        cursor = _MirrorCursor(fail_count=True)
        connection.cursor.return_value = cursor

        mirrored: set[str] = set()
        with pytest.raises(RuntimeError, match="count failed"):
            adapter._promote_loaded_tables_to_mooncake(connection, ["orders"], {"orders": 5}, mirrored)

        assert not any(sql.endswith('RENAME TO "orders"') for sql in cursor.executed)
        assert mirrored == {"orders"}
