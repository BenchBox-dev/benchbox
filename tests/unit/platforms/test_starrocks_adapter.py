# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from argparse import ArgumentParser
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox.platforms.starrocks import StarRocksAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture(autouse=True)
def starrocks_dependencies():
    with patch("benchbox.platforms.starrocks.adapter.check_platform_dependencies", return_value=(True, [])):
        yield


@pytest.fixture
def mock_pymysql():
    with (
        patch("benchbox.platforms.starrocks.setup.pymysql") as mock_mod,
        patch("benchbox.platforms.starrocks.setup.PYMYSQL_AVAILABLE", True),
    ):
        mock_mod.connect = Mock()
        yield mock_mod


class TestStarRocksAdapter:
    def test_initialization_success(self, mock_pymysql):
        adapter = StarRocksAdapter(
            host="localhost",
            port=9030,
            database="test_db",
            username="root",
            password="secret",
        )
        assert adapter.platform_name == "StarRocks"
        assert adapter.dialect == "starrocks"
        assert adapter.host == "localhost"
        assert adapter.port == 9030
        assert adapter.username == "root"
        assert adapter.database == "test_db"
        assert adapter.deployment_mode == "self-hosted"

    def test_initialization_missing_driver(self):
        with (
            patch(
                "benchbox.platforms.starrocks.adapter.check_platform_dependencies",
                return_value=(False, ["pymysql"]),
            ),
            pytest.raises(ImportError) as excinfo,
        ):
            StarRocksAdapter()

        assert "Missing dependencies for starrocks platform" in str(excinfo.value)

    def test_initialization_env_var_fallbacks(self, mock_pymysql):
        with patch.dict(
            "os.environ",
            {
                "STARROCKS_HOST": "sr-host",
                "STARROCKS_PORT": "9031",
                "STARROCKS_USER": "admin",
                "STARROCKS_PASSWORD": "pw123",
                "STARROCKS_DATABASE": "mydb",
                "STARROCKS_HTTP_PORT": "8041",
            },
        ):
            adapter = StarRocksAdapter()
            assert adapter.host == "sr-host"
            assert adapter.port == 9031
            assert adapter.username == "admin"
            assert adapter.password == "pw123"
            assert adapter.database == "mydb"
            assert adapter.http_port == 8041

    def test_add_cli_arguments(self, mock_pymysql):
        parser = ArgumentParser()
        StarRocksAdapter.add_cli_arguments(parser)

        args = parser.parse_args(["--host", "myhost", "--port", "9031"])
        assert args.host == "myhost"
        assert args.port == 9031

    def test_from_config(self, mock_pymysql):
        config = {
            "host": "sr-server",
            "port": 9030,
            "username": "user1",
            "password": "pass1",
            "database": "bench_db",
            "http_port": 8040,
        }
        adapter = StarRocksAdapter.from_config(config)
        assert adapter.host == "sr-server"
        assert adapter.port == 9030
        assert adapter.username == "user1"
        assert adapter.database == "bench_db"

    def test_platform_name(self, mock_pymysql):
        adapter = StarRocksAdapter()
        assert adapter.platform_name == "StarRocks"

    def test_dialect(self, mock_pymysql):
        adapter = StarRocksAdapter()
        assert adapter.dialect == "starrocks"

    def test_get_target_dialect(self, mock_pymysql):
        adapter = StarRocksAdapter()
        assert adapter.get_target_dialect() == "starrocks"

    def test_create_connection_success(self, mock_pymysql):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)
        mock_cursor.fetchall.return_value = [("test",)]
        mock_pymysql.connect.return_value = mock_conn

        adapter = StarRocksAdapter(host="localhost", port=9030, database="test")
        connection = adapter.create_connection()

        from benchbox.platforms.starrocks.setup import _StarRocksConnectionWrapper

        assert isinstance(connection, _StarRocksConnectionWrapper)
        assert connection._conn == mock_conn
        assert mock_pymysql.connect.call_count >= 1

    def test_create_connection_failure(self, mock_pymysql):
        mock_pymysql.connect.side_effect = Exception("Connection refused")

        adapter = StarRocksAdapter(host="localhost", port=9030)
        with pytest.raises(Exception, match="Connection refused|Failed to connect"):
            adapter.create_connection()

    def test_create_connection_pymysql_not_available(self):
        with patch("benchbox.platforms.starrocks.setup.PYMYSQL_AVAILABLE", False):
            adapter = StarRocksAdapter()
            with pytest.raises(ImportError, match="PyMySQL"):
                adapter.create_connection()

    def test_close_connection(self, mock_pymysql):
        mock_conn = Mock()
        adapter = StarRocksAdapter()
        adapter.close_connection(mock_conn)
        mock_conn.close.assert_called_once()

    def test_close_connection_error(self, mock_pymysql):
        mock_conn = Mock()
        mock_conn.close.side_effect = Exception("Close failed")
        adapter = StarRocksAdapter()
        adapter.close_connection(mock_conn)

    def test_create_schema(self, mock_pymysql):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        mock_benchmark = Mock()
        mock_benchmark.get_create_tables_sql.return_value = (
            "CREATE TABLE test (id INT, name STRING); CREATE TABLE test2 (val DOUBLE);"
        )

        adapter = StarRocksAdapter()
        schema_time = adapter.create_schema(mock_benchmark, mock_conn)

        assert isinstance(schema_time, float)
        assert schema_time >= 0
        assert mock_cursor.execute.call_count >= 2

    def test_optimize_table_definition(self, mock_pymysql):
        adapter = StarRocksAdapter()

        original = "CREATE TABLE test (id INT, name VARCHAR(100))"
        optimized = adapter._optimize_table_definition(original)
        assert "DUPLICATE KEY" in optimized
        assert "DISTRIBUTED BY HASH" in optimized
        assert "BUCKETS" in optimized

    def test_optimize_table_definition_type_conversion(self, mock_pymysql):
        adapter = StarRocksAdapter()

        original = "CREATE TABLE test (id INTEGER, name STRING, val DOUBLE PRECISION, big HUGEINT)"
        optimized = adapter._optimize_table_definition(original)
        assert "INT" in optimized
        assert "VARCHAR(65533)" in optimized
        assert "DOUBLE" in optimized
        assert "LARGEINT" in optimized

    def test_optimize_table_definition_removes_foreign_keys(self, mock_pymysql):
        adapter = StarRocksAdapter()

        original = "CREATE TABLE orders (id INT, cust_id INT, FOREIGN KEY (cust_id) REFERENCES customer(id))"
        optimized = adapter._optimize_table_definition(original)
        assert "FOREIGN KEY" not in optimized

    def test_execute_query_success(self, mock_pymysql):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [(1, "test"), (2, "test2")]

        adapter = StarRocksAdapter()
        result = adapter.execute_query(mock_conn, "SELECT * FROM test", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 2
        assert result["first_row"] == (1, "test")
        assert isinstance(result["execution_time_seconds"], float)

    def test_execute_query_failure(self, mock_pymysql):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("Syntax error")

        adapter = StarRocksAdapter()
        result = adapter.execute_query(mock_conn, "INVALID SQL", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "FAILED"
        assert result["rows_returned"] == 0
        assert "Syntax error" in result["error"]
        assert isinstance(result["execution_time_seconds"], float)

    def test_execute_query_empty_result(self, mock_pymysql):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []

        adapter = StarRocksAdapter()
        result = adapter.execute_query(mock_conn, "SELECT * FROM empty", "q2")

        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 0
        assert result["first_row"] is None

    def test_configure_for_benchmark(self, mock_pymysql):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter = StarRocksAdapter()
        adapter.configure_for_benchmark(mock_conn, "tpch")

        assert mock_cursor.execute.call_count >= 1

    def test_configure_for_benchmark_non_olap(self, mock_pymysql):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter = StarRocksAdapter()
        adapter.configure_for_benchmark(mock_conn, "read_primitives")

        assert mock_cursor.execute.call_count >= 1

    def test_get_platform_info_without_connection(self, mock_pymysql):
        adapter = StarRocksAdapter(host="myhost", port=9030, database="mydb")
        info = adapter.get_platform_info()

        assert info["platform_type"] == "starrocks"
        assert info["platform_name"] == "StarRocks"
        assert info["configuration"]["host"] == "myhost"
        assert info["configuration"]["port"] == 9030
        assert info["platform_version"] is None

    def test_get_platform_info_with_connection(self, mock_pymysql):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("3.3.0-starrocks",)

        adapter = StarRocksAdapter()
        info = adapter.get_platform_info(mock_conn)

        assert info["platform_version"] == "3.3.0-starrocks"

    def test_get_existing_tables(self, mock_pymysql):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("LINEITEM",), ("ORDERS",)]

        adapter = StarRocksAdapter()
        tables = adapter._get_existing_tables(mock_conn)

        assert tables == ["lineitem", "orders"]

    def test_validate_data_integrity_success(self, mock_pymysql):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [(1,)]

        adapter = StarRocksAdapter()
        status, details = adapter._validate_data_integrity(Mock(), mock_conn, {"lineitem": 100, "orders": 50})

        assert status == "PASSED"
        assert "accessible_tables" in details
        assert len(details["accessible_tables"]) == 2

    def test_validate_data_integrity_failure(self, mock_pymysql):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("Table not found")

        adapter = StarRocksAdapter()
        status, details = adapter._validate_data_integrity(Mock(), mock_conn, {"missing_table": 100})

        assert status == "FAILED"

    def test_sql_translation(self, mock_pymysql):
        adapter = StarRocksAdapter()

        result = adapter.translate_sql("SELECT * FROM orders", "duckdb")
        assert result == "SELECT * FROM `orders`;"

    def test_apply_constraint_configuration(self, mock_pymysql):
        mock_conn = Mock()

        adapter = StarRocksAdapter()

        pk_config = Mock()
        pk_config.enabled = True
        fk_config = Mock()
        fk_config.enabled = True

        adapter.apply_constraint_configuration(pk_config, fk_config, mock_conn)

    def test_supports_tuning_type(self, mock_pymysql):
        from benchbox.core.tuning.interface import TuningType

        adapter = StarRocksAdapter()

        assert adapter.supports_tuning_type(TuningType.PARTITIONING) is True
        assert adapter.supports_tuning_type(TuningType.SORTING) is True
        assert adapter.supports_tuning_type(TuningType.DISTRIBUTION) is True
        assert adapter.supports_tuning_type(TuningType.CLUSTERING) is False

    def test_apply_table_tunings_none(self, mock_pymysql):
        mock_conn = Mock()
        adapter = StarRocksAdapter()
        adapter.apply_table_tunings(None, mock_conn)

    def test_extract_first_column(self, mock_pymysql):
        adapter = StarRocksAdapter()

        assert adapter._extract_first_column("CREATE TABLE test (id INT, name VARCHAR)") == "id"
        assert adapter._extract_first_column("CREATE TABLE t (`col1` BIGINT)") == "col1"
        assert adapter._extract_first_column("NO PARENS") is None

    def test_optimize_table_adds_duplicate_key_after_pk_removal(self, mock_pymysql):
        adapter = StarRocksAdapter()
        original = "CREATE TABLE test (id INT, name VARCHAR(100), PRIMARY KEY (id))"
        optimized = adapter._optimize_table_definition(original)
        assert "DUPLICATE KEY" in optimized
        assert "DISTRIBUTED BY HASH" in optimized

    def test_apply_platform_optimizations_rejects_unsafe_setting(self, mock_pymysql):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter = StarRocksAdapter()

        platform_config = Mock()
        platform_config.additional_settings = {"valid_setting": "100", "DROP TABLE--": "1"}

        adapter.apply_platform_optimizations(platform_config, mock_conn)

        calls = [str(c) for c in mock_cursor.execute.call_args_list]
        assert any("valid_setting" in c for c in calls)
        assert not any("DROP" in c for c in calls)

    def test_apply_platform_optimizations_rejects_unsafe_value(self, mock_pymysql):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter = StarRocksAdapter()

        platform_config = Mock()
        platform_config.additional_settings = {"good_setting": "1; DROP TABLE users"}

        adapter.apply_platform_optimizations(platform_config, mock_conn)

        mock_cursor.execute.assert_not_called()

    def test_drop_database_rejects_invalid_name(self, mock_pymysql):
        adapter = StarRocksAdapter()
        with pytest.raises(ValueError, match="Invalid database name"):
            adapter.drop_database(database="db`; DROP TABLE--")

    def test_cursor_closed_on_execute_query_failure(self, mock_pymysql):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("Query failed")

        adapter = StarRocksAdapter()
        adapter.execute_query(mock_conn, "SELECT 1", "q1")

        mock_cursor.close.assert_called_once()

    def test_cursor_closed_on_schema_creation_failure(self, mock_pymysql):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = [None, Exception("Second statement fails")]

        mock_benchmark = Mock()
        mock_benchmark.get_create_tables_sql.return_value = "CREATE TABLE t1 (id INT); CREATE TABLE t2 (id INT);"

        adapter = StarRocksAdapter()
        with pytest.raises(Exception, match="Second statement fails"):
            adapter.create_schema(mock_benchmark, mock_conn)

        mock_cursor.close.assert_called_once()


class TestStarRocksTlsCertValidation:
    def test_defaults_verify_ssl_true_no_ca_cert(self):
        adapter = StarRocksAdapter()
        assert adapter.verify_ssl is True
        assert adapter.ca_cert_path is None

    def test_verify_ssl_false_stored(self):
        adapter = StarRocksAdapter(verify_ssl=False)
        assert adapter.verify_ssl is False

    def test_ca_cert_path_stored(self):
        adapter = StarRocksAdapter(ca_cert_path="/etc/ssl/custom-ca.crt")
        assert adapter.ca_cert_path == "/etc/ssl/custom-ca.crt"

    def test_from_config_passes_verify_ssl_and_ca_cert_path(self):
        config = {
            "host": "localhost",
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "verify_ssl": False,
            "ca_cert_path": "/etc/ssl/my-ca.crt",
        }
        adapter = StarRocksAdapter.from_config(config)
        assert adapter.verify_ssl is False
        assert adapter.ca_cert_path == "/etc/ssl/my-ca.crt"
