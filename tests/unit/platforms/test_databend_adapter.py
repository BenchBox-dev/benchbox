# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox.platforms.databend.adapter import DatabendAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture(autouse=True)
def mock_databend_available():
    with patch("benchbox.platforms.databend.adapter.DATABEND_AVAILABLE", True):
        yield


class TestDatabendAdapterInitialization:
    def test_initialization_with_host(self):
        try:
            adapter = DatabendAdapter(
                host="localhost",
                port=8000,
                username="root",
                password="",
                database="test_db",
                ssl=False,
            )
        except ImportError:
            pytest.skip("databend-driver not installed")

        assert adapter.host == "localhost"
        assert adapter.port == 8000
        assert adapter.database == "test_db"
        assert adapter.username == "root"
        assert adapter.platform_name == "Databend"
        assert adapter.get_target_dialect() == "snowflake"

    def test_initialization_with_dsn(self):
        try:
            adapter = DatabendAdapter(
                dsn="databend+http://root:@localhost:8000/test_db",
            )
        except ImportError:
            pytest.skip("databend-driver not installed")

        assert adapter.dsn == "databend+http://root:@localhost:8000/test_db"
        assert adapter.database == "benchbox"

    def test_initialization_with_cloud_config(self):
        try:
            adapter = DatabendAdapter(
                host="tenant--warehouse.gw.databend.com",
                username="benchbox",
                password="cloud_pass",
                database="analytics",
                warehouse="my_warehouse",
            )
        except ImportError:
            pytest.skip("databend-driver not installed")

        assert adapter.host == "tenant--warehouse.gw.databend.com"
        assert adapter.warehouse == "my_warehouse"
        assert adapter.database == "analytics"

    def test_initialization_missing_config(self):
        from benchbox.core.exceptions import ConfigurationError

        try:
            with pytest.raises(ConfigurationError, match="Databend configuration is incomplete"):
                DatabendAdapter(database="test_db")
        except ImportError:
            pytest.skip("databend-driver not installed")

    def test_initialization_env_vars(self):
        env_vars = {
            "DATABEND_HOST": "env-host.example.com",
            "DATABEND_USER": "env_user",
            "DATABEND_PASSWORD": "env_pass",
            "DATABEND_DATABASE": "env_db",
        }

        try:
            with patch.dict("os.environ", env_vars):
                adapter = DatabendAdapter()
        except ImportError:
            pytest.skip("databend-driver not installed")

        assert adapter.host == "env-host.example.com"
        assert adapter.username == "env_user"
        assert adapter.password == "env_pass"
        assert adapter.database == "env_db"

    def test_default_ssl_enabled(self):
        try:
            adapter = DatabendAdapter(host="example.com", password="pass")
        except ImportError:
            pytest.skip("databend-driver not installed")

        assert adapter.ssl is True

    def test_disable_result_cache_default(self):
        try:
            adapter = DatabendAdapter(host="example.com", password="pass")
        except ImportError:
            pytest.skip("databend-driver not installed")

        assert adapter.disable_result_cache is True


class TestDatabendDSNBuilding:
    def test_build_dsn_ssl(self):
        try:
            adapter = DatabendAdapter(
                host="example.com",
                username="user",
                password="pass",
                database="mydb",
                ssl=True,
            )
        except ImportError:
            pytest.skip("databend-driver not installed")

        dsn = adapter._build_dsn()
        assert dsn.startswith("databend+https://")
        assert "user:pass@example.com" in dsn
        assert "/mydb" in dsn
        assert ":443/" in dsn

    def test_build_dsn_no_ssl(self):
        try:
            adapter = DatabendAdapter(
                host="localhost",
                username="root",
                password="",
                database="test",
                ssl=False,
            )
        except ImportError:
            pytest.skip("databend-driver not installed")

        dsn = adapter._build_dsn()
        assert dsn.startswith("databend+http://")
        assert ":8000/" in dsn
        assert "sslmode=disable" in dsn

    def test_build_dsn_with_warehouse(self):
        try:
            adapter = DatabendAdapter(
                host="cloud.example.com",
                username="user",
                password="pass",
                warehouse="my_wh",
            )
        except ImportError:
            pytest.skip("databend-driver not installed")

        dsn = adapter._build_dsn()
        assert "warehouse=my_wh" in dsn

    def test_build_dsn_explicit_port(self):
        try:
            adapter = DatabendAdapter(
                host="example.com",
                port=9000,
                username="user",
                password="pass",
            )
        except ImportError:
            pytest.skip("databend-driver not installed")

        dsn = adapter._build_dsn()
        assert ":9000/" in dsn

    def test_build_dsn_returns_explicit_dsn(self):
        try:
            adapter = DatabendAdapter(
                dsn="databend+http://custom:dsn@host/db",
            )
        except ImportError:
            pytest.skip("databend-driver not installed")

        assert adapter._build_dsn() == "databend+http://custom:dsn@host/db?sslmode=disable"

    def test_build_dsn_normalizes_legacy_explicit_dsn(self):
        try:
            adapter = DatabendAdapter(
                dsn="databend://custom:dsn@host/db",
            )
        except ImportError:
            pytest.skip("databend-driver not installed")

        assert adapter._build_dsn() == "databend+http://custom:dsn@host/db?sslmode=disable"


class TestDatabendConnection:
    def test_create_connection(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        mock_client = Mock()
        mock_client.query_row.return_value = Mock()

        with (
            patch.object(adapter, "handle_existing_database"),
            patch("benchbox.platforms.databend.adapter.databend_driver") as mock_dd,
        ):
            mock_dd.BlockingDatabendClient.return_value = mock_client

            with patch.dict("sys.modules", {"databend_driver": mock_dd}):
                connection = adapter.create_connection()

        assert connection == mock_client
        mock_client.query_row.assert_called_with("SELECT 1")

    def test_create_connection_with_get_conn(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        mock_connection = Mock(spec=["query_row", "query_iter", "exec", "close"])
        mock_connection.query_row.return_value = Mock()
        mock_client = Mock(spec=["get_conn"])
        mock_client.get_conn.return_value = mock_connection

        with (
            patch.object(adapter, "handle_existing_database"),
            patch("benchbox.platforms.databend.adapter.databend_driver") as mock_dd,
        ):
            mock_dd.BlockingDatabendClient.return_value = mock_client
            with patch.dict("sys.modules", {"databend_driver": mock_dd}):
                connection = adapter.create_connection()

        assert connection == mock_connection
        mock_client.get_conn.assert_called_once()
        mock_connection.query_row.assert_called_with("SELECT 1")

    def test_create_connection_logs_in_to_admin_database_before_creating_benchmark_database(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="", database="benchbox")
        except ImportError:
            pytest.skip("databend-driver not installed")

        mock_client = Mock()
        mock_client.query_row.return_value = Mock()

        with (
            patch.object(adapter, "handle_existing_database"),
            patch("benchbox.platforms.databend.adapter.databend_driver") as mock_dd,
        ):
            mock_dd.BlockingDatabendClient.return_value = mock_client
            with patch.dict("sys.modules", {"databend_driver": mock_dd}):
                adapter.create_connection()

        dsn = mock_dd.BlockingDatabendClient.call_args.args[0]
        assert dsn.endswith("@localhost:8000/default?sslmode=disable")
        statements = [call.args[0] for call in mock_client.exec.call_args_list]
        assert statements == ["CREATE DATABASE IF NOT EXISTS `benchbox`", "USE `benchbox`"]

    def test_admin_database_connections_use_admin_dsn(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="", database="benchbox")
        except ImportError:
            pytest.skip("databend-driver not installed")

        assert adapter._build_dsn(adapter.admin_database).startswith("databend+http://benchbox@localhost:8000/default?")
        assert adapter._build_dsn().startswith("databend+http://benchbox@localhost:8000/benchbox?")

    def test_close_connection(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        mock_connection = Mock()
        adapter.close_connection(mock_connection)
        mock_connection.close.assert_called_once()

    def test_close_connection_none(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        adapter.close_connection(None)

    def test_test_connection_success(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        mock_client = Mock()
        mock_client.query_row.return_value = Mock()

        with patch("benchbox.platforms.databend.adapter.databend_driver") as mock_dd:
            mock_dd.BlockingDatabendClient.return_value = mock_client
            with patch.dict("sys.modules", {"databend_driver": mock_dd}):
                result = adapter.test_connection()

        assert result is True

    def test_test_connection_failure(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        with patch("benchbox.platforms.databend.adapter.databend_driver") as mock_dd:
            mock_dd.BlockingDatabendClient.side_effect = Exception("Connection refused")
            with patch.dict("sys.modules", {"databend_driver": mock_dd}):
                result = adapter.test_connection()

        assert result is False


class TestDatabendSchemaOperations:
    def test_create_schema(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        mock_connection = Mock()

        mock_benchmark = Mock()
        mock_benchmark.get_create_tables_sql.return_value = """
            CREATE TABLE table1 (id INTEGER, name VARCHAR(100));
            CREATE TABLE table2 (id INTEGER, data VARCHAR(255));
        """

        with patch.object(adapter, "translate_sql") as mock_translate:
            mock_translate.return_value = (
                "CREATE TABLE table1 (id INTEGER, name VARCHAR(100));\n"
                "CREATE TABLE table2 (id INTEGER, data VARCHAR(255));"
            )

            schema_time = adapter.create_schema(mock_benchmark, mock_connection)

        assert isinstance(schema_time, float)
        assert schema_time >= 0
        assert mock_connection.exec.call_count >= 4

    def test_translate_schema_ddl_char_to_varchar(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        (optimized,) = adapter._translate_schema_ddl("CREATE TABLE test (id INTEGER, code CHAR(10), name VARCHAR(100))")

        import re

        assert not re.search(r"\bCHAR\s*\(", optimized, re.IGNORECASE), f"Standalone CHAR( found in: {optimized}"
        assert "VARCHAR(10)" in optimized
        assert "VARCHAR(100)" in optimized

    def test_translate_schema_ddl_removes_primary_key(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        (optimized,) = adapter._translate_schema_ddl(
            "CREATE TABLE test (id INTEGER, name VARCHAR(100), PRIMARY KEY (id))"
        )

        assert "PRIMARY KEY" not in optimized

    def test_translate_schema_ddl_removes_foreign_key(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        (optimized,) = adapter._translate_schema_ddl(
            "CREATE TABLE test (id INTEGER, other_id INTEGER, FOREIGN KEY (other_id) REFERENCES other(id))"
        )

        assert "FOREIGN KEY" not in optimized
        assert "REFERENCES" not in optimized

    def test_extract_table_name(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        sql = "CREATE TABLE customer (id INTEGER)"
        table_name = adapter._extract_table_name(sql)
        assert table_name == "customer"

    def test_extract_table_name_if_not_exists(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        sql = "CREATE TABLE IF NOT EXISTS customer (id INTEGER)"
        table_name = adapter._extract_table_name(sql)
        assert table_name == "customer"


class TestDatabendDataLoading:
    def test_load_data_csv(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        mock_connection = Mock()
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

            exec_calls = mock_connection.exec.call_args_list
            assert any("USE" in str(call) for call in exec_calls)
            assert any("INSERT INTO" in str(call) for call in exec_calls)

        finally:
            temp_path.unlink()

    def test_load_data_tbl_files(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        mock_connection = Mock()
        mock_benchmark = Mock()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".tbl", delete=False, encoding="utf-8") as f:
            f.write("1|test1|\n2|test2|\n")
            temp_path = Path(f.name)

        try:
            mock_benchmark.tables = {"customer": str(temp_path)}

            table_stats, load_time, _ = adapter.load_data(mock_benchmark, mock_connection, Path("/tmp"))

            assert "customer" in table_stats
            assert table_stats["customer"] == 2

        finally:
            temp_path.unlink()

    def test_load_data_escapes_quotes(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        mock_connection = Mock()
        mock_benchmark = Mock()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write("1,test's data\n")
            temp_path = Path(f.name)

        try:
            mock_benchmark.tables = {"test_table": str(temp_path)}

            adapter.load_data(mock_benchmark, mock_connection, Path("/tmp"))

            exec_calls = [str(call) for call in mock_connection.exec.call_args_list]
            insert_calls = [c for c in exec_calls if "INSERT INTO" in c]
            assert len(insert_calls) > 0
            assert "test''s data" in insert_calls[0]

        finally:
            temp_path.unlink()


class TestDatabendQueryExecution:
    def test_execute_query_success(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        mock_connection = Mock()
        mock_connection.query_iter.return_value = [(1, "test"), (2, "test2")]

        result = adapter.execute_query(mock_connection, "SELECT * FROM test", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 2
        assert isinstance(result["execution_time_seconds"], float)

    def test_execute_query_use_database_before_timing(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        mock_connection = Mock()
        mock_connection.query_iter.return_value = [(1,)]

        result = adapter.execute_query(mock_connection, "SELECT 1", "q1")

        exec_calls = [str(c) for c in mock_connection.exec.call_args_list]
        assert any("USE" in c for c in exec_calls), "USE database should be called"

        assert mock_connection.exec.call_count >= 1
        assert result["status"] == "SUCCESS"

    def test_execute_query_failure(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        mock_connection = Mock()
        mock_connection.exec.return_value = None
        mock_connection.query_iter.side_effect = Exception("Query failed")

        result = adapter.execute_query(mock_connection, "INVALID SQL", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "FAILED"
        assert result["rows_returned"] == 0
        assert result["error"] == "Query failed"
        assert result["error_type"] == "Exception"

    def test_execute_query_empty_result(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        mock_connection = Mock()
        mock_connection.query_iter.return_value = []

        result = adapter.execute_query(mock_connection, "SELECT * FROM empty_table", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 0

    def test_get_query_plan(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        mock_connection = Mock()
        mock_row1 = Mock()
        mock_row1.values.return_value = ["Scan table test_table"]
        mock_row2 = Mock()
        mock_row2.values.return_value = ["  Columns: id, name"]
        mock_connection.query_iter.return_value = [mock_row1, mock_row2]

        plan = adapter.get_query_plan(mock_connection, "SELECT * FROM test_table")

        assert "Scan table test_table" in plan


class TestDatabendPlatformInfo:
    def test_get_platform_info(self):
        try:
            adapter = DatabendAdapter(
                host="example.com",
                database="test_db",
                password="pass",
            )
        except ImportError:
            pytest.skip("databend-driver not installed")

        mock_connection = Mock()
        mock_row = Mock()
        mock_row.values.return_value = ["v1.2.3"]
        mock_connection.query_row.return_value = mock_row

        platform_info = adapter.get_platform_info(mock_connection)

        assert platform_info["platform_type"] == "databend"
        assert platform_info["platform_name"] == "Databend"
        assert platform_info["configuration"]["database"] == "test_db"
        assert platform_info["configuration"]["host"] == "example.com"
        assert platform_info["platform_version"] == "v1.2.3"

    def test_get_platform_info_with_warehouse(self):
        try:
            adapter = DatabendAdapter(
                host="example.com",
                password="pass",
                warehouse="my_wh",
            )
        except ImportError:
            pytest.skip("databend-driver not installed")

        platform_info = adapter.get_platform_info(None)

        assert platform_info["warehouse"] == "my_wh"
        assert platform_info["platform_version"] is None

    def test_get_platform_info_no_connection(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        platform_info = adapter.get_platform_info(None)

        assert platform_info["platform_type"] == "databend"
        assert platform_info["platform_version"] is None


class TestDatabendDialect:
    def test_target_dialect_is_snowflake(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        assert adapter.get_target_dialect() == "snowflake"
        assert adapter._dialect == "snowflake"


class TestDatabendTuning:
    def test_configure_for_benchmark_olap(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        mock_connection = Mock()

        adapter.configure_for_benchmark(mock_connection, "olap")

    def test_configure_for_benchmark_disables_result_cache(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        assert adapter.disable_result_cache is True

        mock_connection = Mock()
        adapter.configure_for_benchmark(mock_connection, "tpch")

        mock_connection.exec.assert_called_once_with("SET enable_query_result_cache = 0")

    def test_configure_for_benchmark_cache_disable_respects_flag(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="", disable_result_cache=False)
        except ImportError:
            pytest.skip("databend-driver not installed")

        mock_connection = Mock()
        adapter.configure_for_benchmark(mock_connection, "tpch")

        mock_connection.exec.assert_not_called()

    def test_generate_tuning_clause_none(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        clause = adapter.generate_tuning_clause(None)
        assert clause == ""

    def test_apply_constraint_configuration(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        mock_connection = Mock()
        mock_primary_key_config = Mock()
        mock_primary_key_config.enabled = True
        mock_foreign_key_config = Mock()
        mock_foreign_key_config.enabled = True

        adapter.apply_constraint_configuration(mock_primary_key_config, mock_foreign_key_config, mock_connection)

    def test_analyze_table_skipped(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        mock_connection = Mock()

        adapter.analyze_table(mock_connection, "test_table")

        mock_connection.exec.assert_not_called()


class TestDatabendFromConfig:
    def test_from_config_with_host(self):
        config = {
            "host": "localhost",
            "port": 8000,
            "username": "root",
            "password": "",
            "database": "test_db",
            "ssl": False,
            "benchmark": "tpch",
            "scale_factor": 1.0,
        }

        try:
            adapter = DatabendAdapter.from_config(config)
        except ImportError:
            pytest.skip("databend-driver not installed")

        assert adapter.host == "localhost"
        assert adapter.port == 8000
        assert adapter.database == "test_db"

    def test_from_config_with_dsn(self):
        config = {
            "dsn": "databend+http://root:@localhost:8000/mydb",
            "benchmark": "tpch",
            "scale_factor": 1.0,
        }

        try:
            adapter = DatabendAdapter.from_config(config)
        except ImportError:
            pytest.skip("databend-driver not installed")

        assert adapter.dsn == "databend+http://root:@localhost:8000/mydb"

    def test_from_config_generates_database_name(self):
        config = {
            "host": "localhost",
            "ssl": False,
            "password": "",
            "benchmark": "tpch",
            "scale_factor": 10.0,
        }

        try:
            adapter = DatabendAdapter.from_config(config)
        except ImportError:
            pytest.skip("databend-driver not installed")

        assert adapter.database is not None
        assert "tpch" in adapter.database.lower() or "sf10" in adapter.database.lower()


class TestDatabendQuoteIdentifier:
    def test_quote_identifier_simple(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        assert adapter._quote_identifier("table_name") == "`table_name`"

    def test_quote_identifier_with_backtick(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        assert adapter._quote_identifier("my`table") == "`my``table`"

    def test_quote_identifier_empty_raises(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        with pytest.raises(ValueError, match="Identifier must be a non-empty string"):
            adapter._quote_identifier("")


class TestDatabendCoerceBool:
    def test_coerce_bool_none_returns_default(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        assert adapter._coerce_bool(None, True) is True
        assert adapter._coerce_bool(None, False) is False

    def test_coerce_bool_string_values(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        assert adapter._coerce_bool("true", False) is True
        assert adapter._coerce_bool("false", True) is False
        assert adapter._coerce_bool("0", True) is False
        assert adapter._coerce_bool("yes", False) is True
        assert adapter._coerce_bool("no", True) is False

    def test_coerce_bool_native_bool(self):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        assert adapter._coerce_bool(True, False) is True
        assert adapter._coerce_bool(False, True) is False


class TestDatabendResourceCleanup:
    def test_test_connection_closes_client(self):
        mock_client = Mock()
        mock_client.query_row.return_value = (1,)

        try:
            adapter = DatabendAdapter(host="localhost")
        except ImportError:
            pytest.skip("databend-driver not installed")

        with patch("benchbox.platforms.databend.adapter.databend_driver") as mock_dd:
            mock_dd.BlockingDatabendClient.return_value = mock_client
            with patch.dict("sys.modules", {"databend_driver": mock_dd}):
                result = adapter.test_connection()

        assert result is True
        mock_client.close.assert_called_once()


class TestDatabendDataResolution:
    def test_resolve_data_files_delegates_to_resolver(self, tmp_path):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        expected = {"orders": [tmp_path / "orders.tbl"]}

        with patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_cls:
            mock_resolver = Mock()
            mock_cls.return_value = mock_resolver
            mock_resolver.resolve.return_value = Mock(tables=expected)

            result = adapter._resolve_data_files(Mock(), tmp_path)

        assert result.tables == expected
        assert mock_cls.call_args.kwargs.get("platform_name") == adapter.platform_name

    def test_resolve_data_files_raises_when_resolver_returns_none(self, tmp_path):
        try:
            adapter = DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")

        with patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_cls:
            mock_resolver = Mock()
            mock_cls.return_value = mock_resolver
            mock_resolver.resolve.return_value = None

            with pytest.raises(ValueError, match="No data files found"):
                adapter._resolve_data_files(Mock(), tmp_path)

    def test_test_connection_closes_client_on_failure(self):
        mock_client = Mock()
        mock_client.query_row.side_effect = Exception("Connection refused")

        try:
            adapter = DatabendAdapter(host="localhost")
        except ImportError:
            pytest.skip("databend-driver not installed")

        with patch("benchbox.platforms.databend.adapter.databend_driver") as mock_dd:
            mock_dd.BlockingDatabendClient.return_value = mock_client
            with patch.dict("sys.modules", {"databend_driver": mock_dd}):
                result = adapter.test_connection()

        assert result is False
        mock_client.close.assert_called_once()

    def test_test_connection_closes_connection_from_get_conn(self):
        mock_connection = Mock(spec=["query_row", "close"])
        mock_connection.query_row.return_value = (1,)
        mock_client = Mock(spec=["get_conn"])
        mock_client.get_conn.return_value = mock_connection

        try:
            adapter = DatabendAdapter(host="localhost")
        except ImportError:
            pytest.skip("databend-driver not installed")

        with patch("benchbox.platforms.databend.adapter.databend_driver") as mock_dd:
            mock_dd.BlockingDatabendClient.return_value = mock_client
            with patch.dict("sys.modules", {"databend_driver": mock_dd}):
                result = adapter.test_connection()

        assert result is True
        mock_client.get_conn.assert_called_once()
        mock_connection.close.assert_called_once()

    def test_check_server_database_exists_closes_client(self):
        mock_row = Mock()
        mock_row.values.return_value = ["default"]
        mock_client = Mock()
        mock_client.query_iter.return_value = [mock_row]

        try:
            adapter = DatabendAdapter(host="localhost", database="default")
        except ImportError:
            pytest.skip("databend-driver not installed")

        with patch("benchbox.platforms.databend.adapter.databend_driver") as mock_dd:
            mock_dd.BlockingDatabendClient.return_value = mock_client
            with patch.dict("sys.modules", {"databend_driver": mock_dd}):
                result = adapter.check_server_database_exists()

        assert result is True
        mock_client.close.assert_called_once()


class TestDatabendDelimiterValidation:
    def test_safe_delimiters_accepted(self):
        safe_delimiters = [",", "|", "\t"]
        for d in safe_delimiters:
            if d not in (",", "|", "\t"):
                raise AssertionError(f"Unexpectedly rejected: {d!r}")

    def test_unsafe_delimiter_rejected(self):
        unsafe_chars = [";", "'", '"', " ", "\n"]
        for char in unsafe_chars:
            assert char not in (",", "|", "\t"), f"Expected {char!r} to be unsafe"


class TestDatabendConfigBuilder:
    def test_config_builder_importable_from_package(self):
        from benchbox.platforms.databend import _build_databend_config

        assert callable(_build_databend_config)

    def test_config_builder_getattr_resolution(self):
        import benchbox.platforms.databend as databend_module

        builder = getattr(databend_module, "_build_databend_config", None)
        assert builder is not None
        assert callable(builder)


class TestDatabendDsnConstruction:
    def test_dsn_defaults_to_ssl(self):
        try:
            adapter = DatabendAdapter(host="myhost", username="user", password="pass", database="db")
        except ImportError:
            pytest.skip("databend-driver not installed")
        dsn = adapter._build_dsn()
        assert dsn.startswith("databend+https://")
        assert ":443/" in dsn

    def test_dsn_uses_plain_scheme_when_ssl_disabled(self):
        try:
            adapter = DatabendAdapter(host="myhost", username="user", password="pass", database="db", ssl=False)
        except ImportError:
            pytest.skip("databend-driver not installed")
        dsn = adapter._build_dsn()
        assert dsn.startswith("databend+http://")
        assert ":8000/" in dsn
        assert "sslmode=disable" in dsn

    def test_dsn_url_encodes_password_with_special_chars(self):
        try:
            adapter = DatabendAdapter(host="myhost", username="user", password="p@ss:w/rd#", database="db")
        except ImportError:
            pytest.skip("databend-driver not installed")
        dsn = adapter._build_dsn()
        assert "p%40ss%3Aw%2Frd%23" in dsn
        assert "p@ss:" not in dsn

    def test_dsn_url_encodes_username_with_special_chars(self):
        try:
            adapter = DatabendAdapter(host="myhost", username="user@domain", password="pass", database="db")
        except ImportError:
            pytest.skip("databend-driver not installed")
        dsn = adapter._build_dsn()
        assert "user%40domain:" in dsn

    def test_dsn_plain_credentials_unchanged(self):
        try:
            adapter = DatabendAdapter(host="myhost", username="admin", password="secret123", database="db")
        except ImportError:
            pytest.skip("databend-driver not installed")
        dsn = adapter._build_dsn()
        assert "admin:secret123@" in dsn


def test_databend_compose_pins_the_image_to_a_digest():
    import re

    compose = (Path(__file__).resolve().parents[3] / "docker/databend/docker-compose.yml").read_text(encoding="utf-8")
    assert re.search(r"image: \$\{DATABEND_IMAGE:-datafuselabs/databend:v[\d.]+@sha256:[0-9a-f]{64}\}", compose)
    assert ":latest" not in compose


class _CursorlessConnection:
    def __init__(self, tables):
        self.tables = tables
        self.statements = []

    def exec(self, sql):
        self.statements.append(sql)

    def query_row(self, sql):
        return (1,)

    def query_iter(self, sql):
        self.statements.append(sql)
        if "system.tables" in sql:
            return [_Row((name,)) for name in self.tables]
        if "benchbox_tuning_metadata" in sql:
            raise RuntimeError("Unknown table 'benchbox_tuning_metadata'")
        if "COUNT(*)" in sql:
            return [_Row((7,))]
        return [_Row((1,))]

    def close(self):
        pass


class _Row:
    def __init__(self, values):
        self._values = values

    def values(self):
        return self._values


class TestDatabendCursorlessConnection:
    def _adapter(self):
        return DatabendAdapter(host="localhost", ssl=False, password="")

    def test_tuning_metadata_load_does_not_touch_cursor(self):
        from benchbox.core.tuning.metadata import TuningMetadataManager

        adapter = self._adapter()
        connection = _CursorlessConnection(["lineitem"])
        manager = TuningMetadataManager(adapter, connection=connection)

        assert manager.load_unified_tunings() is None
        assert manager.last_load_error is None

    def test_tuning_metadata_reads_existing_table_rows(self):
        from benchbox.core.tuning.metadata import TuningMetadataManager

        adapter = self._adapter()
        connection = _CursorlessConnection(["lineitem"])
        manager = TuningMetadataManager(adapter, connection=connection)

        assert manager._fetch_all(connection, "SELECT name FROM system.tables") == [("lineitem",)]
        assert manager._fetch_one(connection, "SELECT name FROM system.tables") == ("lineitem",)

    def test_post_load_validation_passes_without_cursor(self):
        adapter = self._adapter()
        benchmark = Mock()
        benchmark.get_schema.return_value = {"LINEITEM": {}, "ORDERS": {}}
        connection = _CursorlessConnection(["lineitem", "orders"])

        phase = adapter._create_enhanced_validation_phase(
            benchmark=benchmark, connection=connection, table_stats={"lineitem": 5, "orders": 3}
        )

        assert phase.schema_validation == "PASSED"
        assert phase.data_integrity_checks == "PASSED"
        assert phase.validation_details["schema_valid"] is True

    def test_table_row_count_uses_query_iter(self):
        adapter = self._adapter()
        assert adapter.get_table_row_count(_CursorlessConnection([]), "LINEITEM") == 7

    def test_integrity_flags_unreadable_table(self):
        adapter = self._adapter()
        connection = _CursorlessConnection([])
        connection.query_iter = Mock(side_effect=RuntimeError("no such table"))

        status, details = adapter._validate_data_integrity(Mock(), connection, {"lineitem": 5})

        assert status == "FAILED"
        assert details["inaccessible_tables"] == ["lineitem"]
