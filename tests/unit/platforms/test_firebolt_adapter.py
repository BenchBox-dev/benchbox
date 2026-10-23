# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox.platforms.base.data_loading import DataSource
from benchbox.platforms.firebolt import FireboltAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestFireboltAdapterInitialization:
    def test_initialization_core_mode_with_url(self):
        try:
            adapter = FireboltAdapter(
                url="http://localhost:3473",
                database="test_db",
            )
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        assert adapter.deployment_mode == "core"
        assert adapter.url == "http://localhost:3473"
        assert adapter.database == "test_db"
        assert adapter.platform_name == "Firebolt (Core)"
        assert adapter.get_target_dialect() == "postgres"

    def test_initialization_core_mode_default(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        assert adapter.deployment_mode == "core"
        assert adapter.url == "http://localhost:3473"
        assert adapter.database == "benchbox"

    def test_initialization_cloud_mode(self):
        try:
            adapter = FireboltAdapter(
                client_id="test_client_id",
                client_secret="test_client_secret",
                account_name="test_account",
                engine_name="test_engine",
                database="cloud_db",
            )
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        assert adapter.deployment_mode == "cloud"
        assert adapter.client_id == "test_client_id"
        assert adapter.client_secret == "test_client_secret"
        assert adapter.account_name == "test_account"
        assert adapter.engine_name == "test_engine"
        assert adapter.database == "cloud_db"
        assert adapter.platform_name == "Firebolt (Cloud)"

    def test_initialization_cloud_mode_with_api_endpoint(self):
        try:
            adapter = FireboltAdapter(
                client_id="test_client_id",
                client_secret="test_client_secret",
                account_name="test_account",
                engine_name="test_engine",
                api_endpoint="custom.api.firebolt.io",
            )
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        assert adapter.deployment_mode == "cloud"
        assert adapter.api_endpoint == "custom.api.firebolt.io"

    def test_initialization_default_api_endpoint(self):
        try:
            adapter = FireboltAdapter(
                client_id="test_client_id",
                client_secret="test_client_secret",
                account_name="acct",
                engine_name="engine",
            )
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        assert adapter.api_endpoint == "api.app.firebolt.io"

    def test_initialization_invalid_mode(self):
        try:
            with pytest.raises(ValueError):
                FireboltAdapter(deployment_mode="invalid")
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

    def test_initialization_ambiguous_configuration(self):
        try:
            with pytest.raises(ValueError):
                FireboltAdapter(
                    url="http://localhost:3473",
                    client_id="id",
                    client_secret="secret",
                )
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

    def test_initialization_missing_cloud_fields(self):
        from benchbox.core.exceptions import ConfigurationError

        try:
            with pytest.raises(ConfigurationError, match="Firebolt Cloud configuration is incomplete"):
                FireboltAdapter(
                    deployment_mode="cloud",
                    client_id="id",
                    client_secret="secret",
                    account_name="acct",
                )
        except ImportError:
            pytest.skip("Firebolt SDK not installed")


class TestFireboltConnectionParameters:
    def test_get_connection_params_core_mode(self):
        try:
            adapter = FireboltAdapter(
                url="http://localhost:3473",
                database="test_db",
                autocommit=True,
                disable_cache=True,
            )
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        params = adapter._get_connection_params()

        assert params["url"] == "http://localhost:3473"
        assert params["database"] == "test_db"
        assert "auth" in params
        assert "account_name" not in params

    def test_get_connection_params_cloud_mode(self):
        try:
            adapter = FireboltAdapter(
                client_id="test_client_id",
                client_secret="test_client_secret",
                account_name="test_account",
                engine_name="test_engine",
                database="cloud_db",
            )
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        params = adapter._get_connection_params()

        assert params["database"] == "cloud_db"
        assert params["account_name"] == "test_account"
        assert params["engine_name"] == "test_engine"
        assert params["api_endpoint"] == "api.app.firebolt.io"
        assert "auth" in params
        assert "engine_url" not in params


class TestFireboltConnection:
    def test_create_connection_core_mode(self):
        try:
            adapter = FireboltAdapter(
                url="http://localhost:3473",
                database="test_db",
            )
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)

        with (
            patch.object(adapter, "handle_existing_database"),
            patch("benchbox.platforms.firebolt.firebolt_connect", return_value=mock_connection) as mock_connect,
        ):
            connection = adapter.create_connection()

        call_kwargs = mock_connect.call_args.kwargs
        assert call_kwargs["url"] == "http://localhost:3473"
        assert call_kwargs["database"] == "test_db"
        assert "auth" in call_kwargs

        assert connection == mock_connection
        mock_cursor.execute.assert_called_with("SELECT 1")
        mock_cursor.fetchone.assert_called_once()
        mock_cursor.close.assert_called_once()

    def test_create_connection_cloud_mode(self):
        try:
            adapter = FireboltAdapter(
                client_id="test_client_id",
                client_secret="test_client_secret",
                account_name="test_account",
                engine_name="test_engine",
                database="cloud_db",
            )
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)

        with (
            patch.object(adapter, "handle_existing_database"),
            patch("benchbox.platforms.firebolt.firebolt_connect", return_value=mock_connection) as mock_connect,
        ):
            connection = adapter.create_connection()

        assert connection == mock_connection
        mock_connect.assert_called_once()

    def test_close_connection(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_connection = Mock()

        adapter.close_connection(mock_connection)

        mock_connection.close.assert_called_once()

    def test_close_connection_none(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        adapter.close_connection(None)

    def test_test_connection_success(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)

        with patch("benchbox.platforms.firebolt.firebolt_connect", return_value=mock_connection):
            result = adapter.test_connection()

        assert result is True
        mock_cursor.execute.assert_called_with("SELECT 1")
        mock_cursor.close.assert_called_once()
        mock_connection.close.assert_called_once()

    def test_test_connection_failure(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        with patch("benchbox.platforms.firebolt.firebolt_connect", side_effect=Exception("Connection refused")):
            result = adapter.test_connection()

        assert result is False


class TestFireboltSchemaOperations:
    def test_create_schema(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        mock_benchmark = Mock()
        mock_benchmark.get_create_tables_sql.return_value = """
            CREATE TABLE table1 (id INTEGER, name VARCHAR(100));
            CREATE TABLE table2 (id INTEGER, data VARCHAR(255));
        """

        with patch.object(adapter, "translate_sql") as mock_translate:
            mock_translate.return_value = (
                "CREATE TABLE table1 (id INTEGER, name TEXT);\nCREATE TABLE table2 (id INTEGER, data TEXT);"
            )

            schema_time = adapter.create_schema(mock_benchmark, mock_connection)

        assert isinstance(schema_time, float)
        assert schema_time >= 0
        assert mock_cursor.execute.call_count >= 2
        mock_cursor.close.assert_called_once()

    def test_extract_table_name(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        sql = "CREATE TABLE customer (id INTEGER)"
        table_name = adapter._extract_table_name(sql)

        assert table_name == "customer"

    def test_extract_table_name_if_not_exists(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        sql = "CREATE TABLE IF NOT EXISTS customer (id INTEGER)"
        table_name = adapter._extract_table_name(sql)

        assert table_name == "customer"


class TestFireboltDataLoading:
    def test_load_data_csv(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        mock_benchmark = Mock()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write("1,test1\n2,test2\n")
            temp_path = Path(f.name)

        try:
            mock_benchmark.tables = {"test_table": str(temp_path)}

            table_stats, load_time, _ = adapter.load_data(mock_benchmark, mock_connection, Path("/tmp"))

            assert isinstance(table_stats, dict)
            assert isinstance(load_time, float)
            assert load_time >= 0
            assert "test_table" in table_stats
            assert table_stats["test_table"] == 2

            assert mock_cursor.executemany.called
            insert_sql, rows = mock_cursor.executemany.call_args[0]
            assert insert_sql.startswith('INSERT INTO "test_table" VALUES')
            assert rows == [("1", "test1"), ("2", "test2")]

        finally:
            temp_path.unlink()

    def test_load_data_tbl_files(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        mock_benchmark = Mock()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".tbl", delete=False, encoding="utf-8") as f:
            f.write("1|test1|\n2|test2|\n")
            temp_path = Path(f.name)

        try:
            mock_benchmark.tables = {"customer": str(temp_path)}

            table_stats, load_time, _ = adapter.load_data(mock_benchmark, mock_connection, Path("/tmp"))

            assert "customer" in table_stats
            assert table_stats["customer"] == 2

            insert_sql, rows = mock_cursor.executemany.call_args[0]
            assert insert_sql.startswith('INSERT INTO "customer" VALUES')
            assert rows == [("1", "test1"), ("2", "test2")]

        finally:
            temp_path.unlink()

    def test_load_data_escapes_quotes(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        mock_benchmark = Mock()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write("1,test's data\n")
            temp_path = Path(f.name)

        try:
            mock_benchmark.tables = {"test_table": str(temp_path)}

            adapter.load_data(mock_benchmark, mock_connection, Path("/tmp"))

            _, rows = mock_cursor.executemany.call_args[0]
            assert rows == [("1", "test's data")]

        finally:
            temp_path.unlink()


class TestFireboltQueryExecution:
    def test_execute_query_success(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [(1, "test"), (2, "test2")]

        result = adapter.execute_query(mock_connection, "SELECT * FROM test", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 2
        assert result["first_row"] == (1, "test")
        assert isinstance(result["execution_time_seconds"], float)

        mock_cursor.execute.assert_called_with("SELECT * FROM test")
        mock_cursor.close.assert_called_once()

    def test_execute_query_failure(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("Query failed")

        result = adapter.execute_query(mock_connection, "INVALID SQL", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "FAILED"
        assert result["rows_returned"] == 0
        assert result["error"] == "Query failed"
        assert result["error_type"] == "Exception"

        mock_cursor.close.assert_called_once()

    def test_execute_query_empty_result(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []

        result = adapter.execute_query(mock_connection, "SELECT * FROM empty_table", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 0
        assert result["first_row"] is None

    def test_get_query_plan(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            ("Scan table test_table",),
            ("  Columns: id, name",),
        ]

        plan = adapter.get_query_plan(mock_connection, "SELECT * FROM test_table")

        assert "Scan table test_table" in plan
        mock_cursor.execute.assert_called_once()
        mock_cursor.close.assert_called_once()


class TestFireboltPlatformInfo:
    def test_get_platform_info_core_mode(self):
        try:
            adapter = FireboltAdapter(
                url="http://localhost:3473",
                database="test_db",
            )
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("1.0.0",)

        platform_info = adapter.get_platform_info(mock_connection)

        assert platform_info["platform_type"] == "firebolt"
        assert platform_info["platform_name"] == "Firebolt (Core)"
        assert platform_info["connection_mode"] == "core"
        assert platform_info["url"] == "http://localhost:3473"
        assert platform_info["configuration"]["database"] == "test_db"

    def test_get_platform_info_cloud_mode(self):
        try:
            adapter = FireboltAdapter(
                client_id="test_client_id",
                client_secret="test_client_secret",
                account_name="test_account",
                engine_name="test_engine",
                database="cloud_db",
            )
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("1.0.0",)

        platform_info = adapter.get_platform_info(mock_connection)

        assert platform_info["platform_type"] == "firebolt"
        assert platform_info["platform_name"] == "Firebolt (Cloud)"
        assert platform_info["connection_mode"] == "cloud"
        assert platform_info["account_name"] == "test_account"
        assert platform_info["engine_name"] == "test_engine"

    def test_get_platform_info_no_connection(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        platform_info = adapter.get_platform_info(None)

        assert platform_info["platform_type"] == "firebolt"
        assert platform_info["platform_version"] is None


class TestFireboltTuning:
    def test_configure_for_benchmark_olap(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_connection = Mock()

        adapter.configure_for_benchmark(mock_connection, "olap")

    def test_supports_tuning_type(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        with patch("benchbox.core.tuning.interface.TuningType") as mock_tuning_type:
            mock_tuning_type.PARTITIONING = "partitioning"
            mock_tuning_type.SORTING = "sorting"
            mock_tuning_type.DISTRIBUTION = "distribution"

            assert adapter.supports_tuning_type(mock_tuning_type.PARTITIONING) is True
            assert adapter.supports_tuning_type(mock_tuning_type.DISTRIBUTION) is True
            assert adapter.supports_tuning_type(mock_tuning_type.SORTING) is False

    def test_generate_tuning_clause_with_partitioning(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True

        mock_partition_col = Mock()
        mock_partition_col.name = "event_date"
        mock_partition_col.order = 1

        with patch("benchbox.core.tuning.interface.TuningType") as mock_tuning_type:
            mock_tuning_type.PARTITIONING = "partitioning"

            def mock_get_columns_by_type(tuning_type):
                if tuning_type == mock_tuning_type.PARTITIONING:
                    return [mock_partition_col]
                return []

            mock_tuning.get_columns_by_type.side_effect = mock_get_columns_by_type

            clause = adapter.generate_tuning_clause(mock_tuning)

            assert "PARTITION BY" in clause
            assert "event_date" in clause

    def test_generate_tuning_clause_none(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        clause = adapter.generate_tuning_clause(None)
        assert clause == ""

    def test_apply_constraint_configuration(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_connection = Mock()
        mock_primary_key_config = Mock()
        mock_primary_key_config.enabled = True
        mock_foreign_key_config = Mock()
        mock_foreign_key_config.enabled = True

        adapter.apply_constraint_configuration(mock_primary_key_config, mock_foreign_key_config, mock_connection)


class TestFireboltFromConfig:
    def test_from_config_core_mode(self):
        config = {
            "url": "http://localhost:3473",
            "database": "test_db",
            "benchmark": "tpch",
            "scale_factor": 1.0,
        }

        try:
            adapter = FireboltAdapter.from_config(config)
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        assert adapter.deployment_mode == "core"
        assert adapter.url == "http://localhost:3473"

    def test_from_config_cloud_mode(self):
        config = {
            "client_id": "test_client_id",
            "client_secret": "test_client_secret",
            "account_name": "test_account",
            "engine_name": "test_engine",
            "database": "cloud_db",
            "benchmark": "tpch",
            "scale_factor": 1.0,
        }

        try:
            adapter = FireboltAdapter.from_config(config)
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        assert adapter.deployment_mode == "cloud"
        assert adapter.client_id == "test_client_id"
        assert adapter.account_name == "test_account"
        assert adapter.engine_name == "test_engine"
        assert adapter.database == "cloud_db"

    def test_from_config_generates_database_name(self):
        config = {
            "url": "http://localhost:3473",
            "benchmark": "tpch",
            "scale_factor": 10.0,
        }

        try:
            adapter = FireboltAdapter.from_config(config)
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        assert adapter.database is not None
        assert "tpch" in adapter.database.lower() or "sf10" in adapter.database.lower()


class TestFireboltDatabaseOperations:
    def test_check_server_database_exists_core_mode(self):
        try:
            adapter = FireboltAdapter(url="http://localhost:3473")
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        result = adapter.check_server_database_exists()
        assert result is False

    def test_drop_database_core_mode(self):
        try:
            adapter = FireboltAdapter(url="http://localhost:3473")
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        adapter.drop_database(database="test_db")

    def test_check_server_database_exists_closes_on_error(self):
        try:
            adapter = FireboltAdapter(
                client_id="id",
                client_secret="secret",
                account_name="acct",
                engine_name="engine",
            )
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("boom")

        with patch("benchbox.platforms.firebolt.firebolt_connect", return_value=mock_conn):
            result = adapter.check_server_database_exists(database="benchbox")

        assert result is False
        mock_cursor.close.assert_called_once()
        mock_conn.close.assert_called_once()

    def test_get_existing_tables(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("table1",), ("table2",), ("TABLE3",)]

        tables = adapter._get_existing_tables(mock_connection)

        assert tables == ["table1", "table2", "table3"]
        mock_cursor.execute.assert_called_with("SHOW TABLES")


class TestFireboltDialect:
    def test_target_dialect_is_postgres(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        assert adapter.get_target_dialect() == "postgres"
        assert adapter._dialect == "postgres"


class TestFireboltAnalyze:
    def test_analyze_table_skipped(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter.analyze_table(mock_connection, "test_table")

        mock_cursor.execute.assert_not_called()


class TestFireboltResultCacheControl:
    def test_disable_result_cache_cloud_mode(self):
        try:
            adapter = FireboltAdapter(
                client_id="test_client_id",
                client_secret="test_client_secret",
                account_name="test_account",
                engine_name="test_engine",
                disable_result_cache=True,
            )
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter._disable_result_cache(mock_connection)

        mock_cursor.execute.assert_called_with("SET enable_result_cache = false")
        mock_cursor.close.assert_called_once()

    def test_disable_result_cache_core_mode_skipped(self):
        try:
            adapter = FireboltAdapter(
                url="http://localhost:3473",
                disable_result_cache=True,
            )
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter._disable_result_cache(mock_connection)

        mock_cursor.execute.assert_not_called()

    def test_validate_session_cache_control_disabled(self):
        try:
            adapter = FireboltAdapter(
                client_id="test_client_id",
                client_secret="test_client_secret",
                account_name="test_account",
                engine_name="test_engine",
            )
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("false",)

        result = adapter.validate_session_cache_control(mock_connection)

        assert result is True
        mock_cursor.execute.assert_called_with("SHOW enable_result_cache")

    def test_validate_session_cache_control_enabled_warning(self):
        try:
            adapter = FireboltAdapter(
                client_id="test_client_id",
                client_secret="test_client_secret",
                account_name="test_account",
                engine_name="test_engine",
                strict_validation=False,
            )
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("true",)

        result = adapter.validate_session_cache_control(mock_connection)

        assert result is False

    def test_validate_session_cache_control_core_mode(self):
        try:
            adapter = FireboltAdapter(
                url="http://localhost:3473",
            )
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_connection = Mock()

        result = adapter.validate_session_cache_control(mock_connection)

        assert result is True

    def test_initialization_with_benchmark_options(self):
        try:
            adapter = FireboltAdapter(
                url="http://localhost:3473",
                disable_result_cache=True,
                strict_validation=True,
            )
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        assert adapter.disable_result_cache is True
        assert adapter.strict_validation is True


class TestFireboltAdminConnection:
    @patch("benchbox.platforms.firebolt.firebolt_connect")
    def test_create_admin_connection_core_mode(self, mock_connect):
        try:
            adapter = FireboltAdapter(
                url="http://localhost:3473",
                database="test_db",
            )
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_conn = Mock()
        mock_connect.return_value = mock_conn

        conn = adapter._create_admin_connection()

        assert conn == mock_conn
        mock_connect.assert_called_once()
        call_kwargs = mock_connect.call_args[1]
        assert call_kwargs["database"] == "test_db"

    @patch("benchbox.platforms.firebolt.firebolt_connect")
    def test_create_admin_connection_cloud_mode(self, mock_connect):
        try:
            adapter = FireboltAdapter(
                client_id="test_client_id",
                client_secret="test_client_secret",
                account_name="test_account",
                engine_name="test_engine",
                database="user_db",
            )
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_conn = Mock()
        mock_connect.return_value = mock_conn

        conn = adapter._create_admin_connection()

        assert conn == mock_conn
        mock_connect.assert_called_once()
        call_kwargs = mock_connect.call_args[1]
        assert call_kwargs["database"] == "information_schema"


class TestFireboltPlatformMetadata:
    def test_get_platform_metadata_core_mode(self):
        try:
            adapter = FireboltAdapter(
                url="http://localhost:3473",
                database="test_db",
            )
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("3.0.0",)

        metadata = adapter._get_platform_metadata(mock_connection)

        assert metadata["mode"] == "core"
        assert metadata["database"] == "test_db"
        assert metadata["url"] == "http://localhost:3473"
        assert metadata["version"] == "3.0.0"

    def test_get_platform_metadata_cloud_mode(self):
        try:
            adapter = FireboltAdapter(
                client_id="test_client_id",
                client_secret="test_client_secret",
                account_name="test_account",
                engine_name="test_engine",
            )
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [
            ("3.0.0",),
            ("test_engine", "GENERAL_PURPOSE", "RUNNING"),
        ]

        metadata = adapter._get_platform_metadata(mock_connection)

        assert metadata["mode"] == "cloud"
        assert metadata["account_name"] == "test_account"
        assert metadata["engine_name"] == "test_engine"
        assert metadata["version"] == "3.0.0"


class TestFireboltTuningDistribution:
    def test_supports_distribution_tuning_type(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        from benchbox.core.tuning.interface import TuningType

        assert adapter.supports_tuning_type(TuningType.DISTRIBUTION) is True
        assert adapter.supports_tuning_type(TuningType.PARTITIONING) is True

    def test_generate_tuning_clause_with_distribution(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        from benchbox.core.tuning.interface import TableTuning, TuningColumn

        table_tuning = TableTuning(
            table_name="orders",
            distribution=[
                TuningColumn(name="order_id", type="INTEGER", order=1),
                TuningColumn(name="customer_id", type="INTEGER", order=2),
            ],
        )

        clause = adapter.generate_tuning_clause(table_tuning)

        assert "PRIMARY INDEX (order_id, customer_id)" in clause

    def test_generate_tuning_clause_with_distribution_and_partitioning(self):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        from benchbox.core.tuning.interface import TableTuning, TuningColumn

        table_tuning = TableTuning(
            table_name="orders",
            distribution=[
                TuningColumn(name="order_id", type="INTEGER", order=1),
            ],
            partitioning=[
                TuningColumn(name="order_date", type="DATE", order=1),
            ],
        )

        clause = adapter.generate_tuning_clause(table_tuning)

        assert "PRIMARY INDEX (order_id)" in clause
        assert "PARTITION BY order_date" in clause


class TestFireboltFromConfigOptions:
    def test_from_config_with_benchmark_options(self):
        config = {
            "benchmark": "tpch",
            "scale_factor": 1.0,
            "url": "http://localhost:3473",
            "disable_result_cache": True,
            "strict_validation": True,
        }

        try:
            adapter = FireboltAdapter.from_config(config)
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        assert adapter.disable_result_cache is True
        assert adapter.strict_validation is True


class TestFireboltDataLoading:
    def test_resolve_data_files_returns_path_mapping(self, tmp_path):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        raw_tables = {"orders": [str(tmp_path / "orders.csv")]}
        resolver_data_source = DataSource(
            source_type="manifest_v2",
            tables=raw_tables,
            table_metadata={"orders": {"csv_delimiter": ","}},
        )

        with patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_cls:
            mock_resolver = Mock()
            mock_cls.return_value = mock_resolver
            mock_resolver.resolve.return_value = resolver_data_source

            result = adapter._resolve_data_files(Mock(), tmp_path)

        assert "orders" in result.tables
        assert all(isinstance(p, Path) for p in result.tables["orders"])
        assert result.table_metadata == {"orders": {"csv_delimiter": ","}}
        assert all(isinstance(p, str) for p in resolver_data_source.tables["orders"])
        assert mock_cls.call_args.kwargs.get("platform_name") == adapter.platform_name

    def test_resolve_data_files_raises_when_resolver_returns_none(self, tmp_path):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        with patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_cls:
            mock_resolver = Mock()
            mock_cls.return_value = mock_resolver
            mock_resolver.resolve.return_value = None

            with pytest.raises(ValueError, match="No data files found"):
                adapter._resolve_data_files(Mock(), tmp_path)

    def test_resolve_data_files_raises_when_tables_empty(self, tmp_path):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        with patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_cls:
            mock_resolver = Mock()
            mock_cls.return_value = mock_resolver
            mock_resolver.resolve.return_value = Mock(tables={})

            with pytest.raises(ValueError, match="No data files found"):
                adapter._resolve_data_files(Mock(), tmp_path)

    def test_resolve_data_files_uses_adapter_platform_name(self, tmp_path):
        try:
            adapter = FireboltAdapter()
        except ImportError:
            pytest.skip("Firebolt SDK not installed")

        with patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_cls:
            mock_resolver = Mock()
            mock_cls.return_value = mock_resolver
            mock_resolver.resolve.return_value = DataSource(source_type="manifest_v2", tables={"t": ["f"]})

            adapter._resolve_data_files(Mock(), tmp_path)

        _, kwargs = mock_cls.call_args
        assert kwargs.get("platform_name") == adapter.platform_name
