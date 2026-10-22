# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import gzip
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox.platforms.base.data_loading import CsvDialect, DataSource
from benchbox.platforms.doris import DorisAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestDorisIdentifierValidation:
    def test_init_rejects_invalid_database(self):
        with pytest.raises(ValueError, match="Invalid database identifier"):
            DorisAdapter(database="DROP TABLE; --")

    def test_init_accepts_valid_database(self):
        adapter = DorisAdapter(database="benchbox_tpch")
        assert adapter.database == "benchbox_tpch"


class TestDorisAdapterInitialization:
    def test_initialization_default_config(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        assert adapter.host == "localhost"
        assert adapter.port == 9030
        assert adapter.http_port == 8030
        assert adapter.username == "root"
        assert adapter.database == "benchbox"
        assert adapter.platform_name == "Apache Doris"
        assert adapter.get_target_dialect() == "doris"

    def test_initialization_custom_config(self):
        try:
            adapter = DorisAdapter(
                host="doris-fe.example.com",
                port=9031,
                http_port=8031,
                username="admin",
                password="secret",
                database="my_benchmark",
            )
        except ImportError:
            pytest.skip("pymysql not installed")

        assert adapter.host == "doris-fe.example.com"
        assert adapter.port == 9031
        assert adapter.http_port == 8031
        assert adapter.username == "admin"
        assert adapter.password == "secret"
        assert adapter.database == "my_benchmark"

    def test_init_uses_simple_defaults_not_env_vars(self):
        try:
            with patch.dict(
                "os.environ",
                {
                    "DORIS_HOST": "env-host",
                    "DORIS_PORT": "9032",
                    "DORIS_HTTP_PORT": "8032",
                    "DORIS_USER": "env-user",
                    "DORIS_PASSWORD": "env-pass",
                },
            ):
                adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        assert adapter.host == "localhost"
        assert adapter.port == 9030
        assert adapter.http_port == 8030
        assert adapter.username == "root"
        assert adapter.password == ""

    def test_builder_resolves_env_vars(self):
        from benchbox.platforms.doris import _build_doris_config

        with patch.dict(
            "os.environ",
            {
                "DORIS_HOST": "env-host",
                "DORIS_PORT": "9032",
                "DORIS_HTTP_PORT": "8032",
                "DORIS_USER": "env-user",
                "DORIS_PASSWORD": "env-pass",
            },
        ):
            config = _build_doris_config("doris", {}, {}, None)

        assert config.host == "env-host"
        assert config.port == 9032
        assert config.http_port == 8032
        assert config.username == "env-user"
        assert config.password == "env-pass"

    def test_dialect_is_doris(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        assert adapter.get_target_dialect() == "doris"
        assert adapter._dialect == "doris"


class TestDorisFromConfig:
    def test_from_config_basic(self):
        config = {
            "host": "my-doris-host",
            "port": 9030,
            "database": "test_db",
            "username": "root",
            "password": "pass",
        }

        try:
            adapter = DorisAdapter.from_config(config)
        except ImportError:
            pytest.skip("pymysql not installed")

        assert adapter.host == "my-doris-host"
        assert adapter.port == 9030
        assert adapter.database == "test_db"

    def test_from_config_generates_database_name(self):
        config = {
            "benchmark": "tpch",
            "scale_factor": 0.01,
        }

        try:
            adapter = DorisAdapter.from_config(config)
        except ImportError:
            pytest.skip("pymysql not installed")

        assert adapter.database is not None
        assert "benchbox" in adapter.database.lower()
        assert "tpch" in adapter.database.lower()

    def test_from_config_default_database(self):
        config = {}

        try:
            adapter = DorisAdapter.from_config(config)
        except ImportError:
            pytest.skip("pymysql not installed")

        assert adapter.database == "benchbox"


class TestDorisConnection:
    def test_create_connection(self):
        try:
            adapter = DorisAdapter(
                host="localhost",
                port=9030,
                database="test_db",
                username="root",
                password="",
            )
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)

        with (
            patch.object(adapter, "handle_existing_database"),
            patch.object(adapter, "check_server_database_exists", return_value=True),
            patch("benchbox.platforms.doris.pymysql") as mock_pymysql,
        ):
            mock_pymysql.connect.return_value = mock_connection
            connection = adapter.create_connection()

        from benchbox.platforms.doris import _DorisConnectionWrapper

        assert isinstance(connection, _DorisConnectionWrapper)
        assert connection._conn == mock_connection
        mock_pymysql.connect.assert_called_once()
        call_kwargs = mock_pymysql.connect.call_args.kwargs
        assert call_kwargs["host"] == "localhost"
        assert call_kwargs["port"] == 9030
        assert call_kwargs["database"] == "test_db"
        assert call_kwargs["user"] == "root"
        mock_cursor.execute.assert_called_with("SELECT 1")
        mock_cursor.fetchone.assert_called_once()
        mock_cursor.close.assert_called_once()

    def test_create_connection_creates_database_if_missing(self):
        try:
            adapter = DorisAdapter(database="new_db")
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)

        with (
            patch.object(adapter, "handle_existing_database"),
            patch.object(adapter, "check_server_database_exists", return_value=False),
            patch.object(adapter, "_create_database") as mock_create_db,
            patch("benchbox.platforms.doris.pymysql") as mock_pymysql,
        ):
            mock_pymysql.connect.return_value = mock_connection
            adapter.create_connection()

        mock_create_db.assert_called_once()

    def test_close_connection(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_connection = Mock()
        adapter.close_connection(mock_connection)
        mock_connection.close.assert_called_once()

    def test_close_connection_none(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        adapter.close_connection(None)

    def test_test_connection_success(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)

        with patch("benchbox.platforms.doris.pymysql") as mock_pymysql:
            mock_pymysql.connect.return_value = mock_connection
            result = adapter.test_connection()

        assert result is True
        mock_cursor.execute.assert_called_with("SELECT 1")
        mock_cursor.close.assert_called_once()
        mock_connection.close.assert_called_once()

    def test_test_connection_failure(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        with patch("benchbox.platforms.doris.pymysql") as mock_pymysql:
            mock_pymysql.connect.side_effect = Exception("Connection refused")
            result = adapter.test_connection()

        assert result is False


class TestDorisDatabaseOperations:
    def test_check_server_database_exists_true(self):
        try:
            adapter = DorisAdapter(database="test_db")
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("information_schema",), ("test_db",), ("other_db",)]

        with patch("benchbox.platforms.doris.pymysql") as mock_pymysql:
            mock_pymysql.connect.return_value = mock_connection
            result = adapter.check_server_database_exists()

        assert result is True
        mock_cursor.execute.assert_called_with("SHOW DATABASES")

    def test_check_server_database_exists_false(self):
        try:
            adapter = DorisAdapter(database="nonexistent_db")
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("information_schema",), ("other_db",)]

        with patch("benchbox.platforms.doris.pymysql") as mock_pymysql:
            mock_pymysql.connect.return_value = mock_connection
            result = adapter.check_server_database_exists()

        assert result is False

    def test_check_server_database_exists_connection_error(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        with patch("benchbox.platforms.doris.pymysql") as mock_pymysql:
            mock_pymysql.connect.side_effect = Exception("Connection refused")
            result = adapter.check_server_database_exists()

        assert result is False

    def test_drop_database(self):
        try:
            adapter = DorisAdapter(database="drop_me")
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        with patch("benchbox.platforms.doris.pymysql") as mock_pymysql:
            mock_pymysql.connect.return_value = mock_connection
            adapter.drop_database()

        mock_cursor.execute.assert_called_with("DROP DATABASE IF EXISTS `drop_me`")

    def test_drop_database_invalid_identifier(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        with pytest.raises(ValueError, match="Invalid database identifier"):
            adapter.drop_database(database="invalid; DROP TABLE")

    def test_create_database(self):
        try:
            adapter = DorisAdapter(database="new_db")
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        with patch("benchbox.platforms.doris.pymysql") as mock_pymysql:
            mock_pymysql.connect.return_value = mock_connection
            adapter._create_database()

        mock_cursor.execute.assert_called_with("CREATE DATABASE IF NOT EXISTS `new_db`")

    def test_get_existing_tables(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("CUSTOMER",), ("orders",), ("LineItem",)]

        tables = adapter._get_existing_tables(mock_connection)

        assert tables == ["customer", "orders", "lineitem"]
        mock_cursor.execute.assert_called_with("SHOW TABLES")


class TestDorisSchemaOperations:
    def test_create_schema(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

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
                "CREATE TABLE table1 (id INT, name VARCHAR(100));\nCREATE TABLE table2 (id INT, data VARCHAR(255));"
            )
            schema_time = adapter.create_schema(mock_benchmark, mock_connection)

        assert isinstance(schema_time, float)
        assert schema_time >= 0
        assert mock_cursor.execute.call_count >= 2
        mock_cursor.close.assert_called_once()

    def test_create_schema_raises_on_create_table_failure(self):
        adapter = DorisAdapter()

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("Syntax error")

        mock_benchmark = Mock()

        with (
            patch.object(adapter, "_create_schema_with_tuning", return_value="CREATE TABLE lineitem (id INT)"),
            pytest.raises(RuntimeError, match="critical CREATE TABLE"),
        ):
            adapter.create_schema(mock_benchmark, mock_connection)

    def test_create_schema_continues_on_non_critical_failure(self):
        adapter = DorisAdapter()

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = [None, Exception("Index error")]

        mock_benchmark = Mock()

        with patch.object(
            adapter,
            "_create_schema_with_tuning",
            return_value="CREATE TABLE t1 (id INT); ALTER TABLE t1 ADD INDEX idx (id)",
        ):
            schema_time = adapter.create_schema(mock_benchmark, mock_connection)

        assert isinstance(schema_time, float)


class TestDorisDataLoading:
    def test_load_data_stream_load(self):
        try:
            adapter = DorisAdapter(
                host="localhost",
                http_port=8030,
                username="root",
                password="",
                database="test_db",
            )
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_benchmark = Mock()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", encoding="utf-8", delete=False) as f:
            f.write("1,test1\n2,test2\n3,test3\n")
            temp_path = Path(f.name)

        try:
            fake_ds = DataSource(
                source_type="benchmark_tables",
                tables={"test_table": [temp_path]},
                table_metadata={"test_table": {"csv_delimiter": ","}},
            )

            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "Status": "Success",
                "NumberLoadedRows": 3,
            }

            with (
                patch("benchbox.platforms.doris.DataSourceResolver") as mock_resolver_cls,
                patch("benchbox.platforms.doris._requests") as mock_requests,
            ):
                mock_resolver_cls.return_value.resolve.return_value = fake_ds
                mock_requests.put.return_value = mock_response
                mock_requests.__bool__ = Mock(return_value=True)
                table_stats, load_time, per_table = adapter.load_data(mock_benchmark, Mock(), Path("/tmp"))

            assert isinstance(table_stats, dict)
            assert isinstance(load_time, float)
            assert "test_table" in table_stats
            assert table_stats["test_table"] == 3

        finally:
            temp_path.unlink()

    def test_load_data_insert_fallback(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        mock_benchmark = Mock()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", encoding="utf-8", delete=False) as f:
            f.write("1,test1\n2,test2\n")
            temp_path = Path(f.name)

        try:
            fake_ds = DataSource(
                source_type="benchmark_tables",
                tables={"test_table": [temp_path]},
                table_metadata={"test_table": {"csv_delimiter": ","}},
            )

            with (
                patch("benchbox.platforms.doris.DataSourceResolver") as mock_resolver_cls,
                patch("benchbox.platforms.doris._requests", None),
            ):
                mock_resolver_cls.return_value.resolve.return_value = fake_ds
                table_stats, load_time, per_table = adapter.load_data(mock_benchmark, mock_connection, Path("/tmp"))

            assert isinstance(table_stats, dict)
            assert isinstance(load_time, float)
            assert "test_table" in table_stats
            assert table_stats["test_table"] == 2
            assert mock_cursor.executemany.called

        finally:
            temp_path.unlink()

    def test_load_data_tbl_files_insert_fallback(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        mock_benchmark = Mock()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".tbl", encoding="utf-8", delete=False) as f:
            f.write("1|test1|\n2|test2|\n")
            temp_path = Path(f.name)

        try:
            fake_ds = DataSource(
                source_type="benchmark_tables",
                tables={"customer": [temp_path]},
                table_metadata={"customer": {"csv_delimiter": "|", "csv_null_marker": ""}},
            )

            with (
                patch("benchbox.platforms.doris.DataSourceResolver") as mock_resolver_cls,
                patch("benchbox.platforms.doris._requests", None),
            ):
                mock_resolver_cls.return_value.resolve.return_value = fake_ds
                table_stats, load_time, _ = adapter.load_data(mock_benchmark, mock_connection, Path("/tmp"))

            assert "customer" in table_stats
            assert table_stats["customer"] == 2

            assert mock_cursor.executemany.called
            insert_sql, rows = mock_cursor.executemany.call_args[0]
            assert "INSERT INTO `customer`" in insert_sql
            assert rows == [["1", "test1"], ["2", "test2"]]

        finally:
            temp_path.unlink()

    def test_load_data_missing_file(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_benchmark = Mock()
        fake_ds = DataSource(
            source_type="benchmark_tables",
            tables={"missing_table": [Path("/nonexistent/path/data.csv")]},
            table_metadata={},
        )

        with (
            patch("benchbox.platforms.doris.DataSourceResolver") as mock_resolver_cls,
            patch("benchbox.platforms.doris._requests", None),
        ):
            mock_resolver_cls.return_value.resolve.return_value = fake_ds
            table_stats, load_time, _ = adapter.load_data(mock_benchmark, Mock(), Path("/tmp"))

        assert table_stats["missing_table"] == 0

    def test_stream_load_failure_status(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "Status": "Fail",
            "Message": "Table not found",
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", encoding="utf-8", delete=False) as f:
            f.write("1,test1\n")
            temp_path = Path(f.name)

        try:
            csv_dialect = CsvDialect(
                delimiter=",", has_header=False, null_marker=None, normalize_booleans=False, quote=None
            )
            with patch("benchbox.platforms.doris._requests") as mock_requests:
                mock_requests.put.return_value = mock_response

                with pytest.raises(RuntimeError, match="Stream Load failed"):
                    adapter._stream_load_file("test_table", temp_path, csv_dialect)
        finally:
            temp_path.unlink()

    def test_stream_load_http_error(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_response = Mock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", encoding="utf-8", delete=False) as f:
            f.write("1,test1\n")
            temp_path = Path(f.name)

        try:
            csv_dialect = CsvDialect(
                delimiter=",", has_header=False, null_marker=None, normalize_booleans=False, quote=None
            )
            with patch("benchbox.platforms.doris._requests") as mock_requests:
                mock_requests.put.return_value = mock_response

                with pytest.raises(RuntimeError, match="status 503"):
                    adapter._stream_load_file("test_table", temp_path, csv_dialect)
        finally:
            temp_path.unlink()


class TestDorisQueryExecution:
    def test_execute_query_success(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

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
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

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

    def test_execute_query_empty_result(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

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
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            ("OlapScanNode",),
            ("  TABLE: customer",),
            ("  PREDICATES: c_custkey > 100",),
        ]

        plan = adapter.get_query_plan(mock_connection, "SELECT * FROM customer WHERE c_custkey > 100")

        assert "OlapScanNode" in plan
        assert "customer" in plan
        mock_cursor.execute.assert_called_once()
        mock_cursor.close.assert_called_once()

    def test_get_query_plan_verbose(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("VERBOSE PLAN",)]

        adapter.get_query_plan(mock_connection, "SELECT 1", explain_options={"verbose": True})

        call_args = mock_cursor.execute.call_args[0][0]
        assert call_args.startswith("EXPLAIN VERBOSE")

    def test_get_query_plan_failure(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("EXPLAIN not supported")

        plan = adapter.get_query_plan(mock_connection, "SELECT 1")

        assert plan is None


class TestDorisPlatformInfo:
    def test_get_platform_info_with_connection(self):
        try:
            adapter = DorisAdapter(
                host="doris-host",
                port=9030,
                http_port=8030,
                database="test_db",
            )
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [
            ("5.7.99",),
            ("doris version doris-4.0.3-rc03-e9096296b8b",),
            ("test_db",),
        ]

        platform_info = adapter.get_platform_info(mock_connection)

        assert platform_info["platform_type"] == "doris"
        assert platform_info["platform_name"] == "Apache Doris"
        assert platform_info["host"] == "doris-host"
        assert platform_info["port"] == 9030
        assert platform_info["dialect"] == "doris"
        assert platform_info["platform_version"] == "4.0.3-rc03"
        assert platform_info["configuration"]["database"] == "test_db"
        assert platform_info["configuration"]["http_port"] == 8030
        assert platform_info["configuration"]["mysql_protocol_version"] == "5.7.99"
        assert platform_info["configuration"]["version_comment"] == "doris version doris-4.0.3-rc03-e9096296b8b"

    def test_get_platform_info_no_connection(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        platform_info = adapter.get_platform_info(None)

        assert platform_info["platform_type"] == "doris"
        assert platform_info["platform_name"] == "Apache Doris"
        assert "platform_version" not in platform_info


class TestDorisTuning:
    def test_configure_for_benchmark_olap(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter.configure_for_benchmark(mock_connection, "olap")

        calls = [str(c) for c in mock_cursor.execute.call_args_list]
        assert any("exec_mem_limit" in c for c in calls)

    def test_configure_for_benchmark_uses_session_level_set(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter.configure_for_benchmark(mock_connection, "olap")

        calls = [str(c) for c in mock_cursor.execute.call_args_list]
        for call in calls:
            assert "global" not in call.lower(), f"SET global found in: {call}"

        assert any("enable_sql_cache" in c for c in calls)
        assert any("parallel_fragment_exec_instance_num" in c for c in calls)

    def test_configure_for_benchmark_generic(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter.configure_for_benchmark(mock_connection, "generic")

    def test_supports_tuning_type(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        from benchbox.core.tuning.interface import TuningType

        assert adapter.supports_tuning_type(TuningType.DISTRIBUTION) is True
        assert adapter.supports_tuning_type(TuningType.PARTITIONING) is True
        assert adapter.supports_tuning_type(TuningType.SORTING) is True
        assert adapter.supports_tuning_type(TuningType.PRIMARY_KEYS) is True
        assert adapter.supports_tuning_type(TuningType.FOREIGN_KEYS) is False
        assert adapter.supports_tuning_type(TuningType.CLUSTERING) is False

    def test_generate_tuning_clause_none(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        clause = adapter.generate_tuning_clause(None)
        assert clause == ""

    def test_apply_constraint_configuration(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_connection = Mock()
        mock_pk_config = Mock()
        mock_pk_config.enabled = True
        mock_fk_config = Mock()
        mock_fk_config.enabled = True

        adapter.apply_constraint_configuration(mock_pk_config, mock_fk_config, mock_connection)


class TestDorisAnalyze:
    def test_analyze_table(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter.analyze_table(mock_connection, "customer")

        mock_cursor.execute.assert_called_with("ANALYZE TABLE `customer`")

    def test_analyze_table_invalid_name(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter.analyze_table(mock_connection, "invalid; DROP TABLE")

        mock_cursor.execute.assert_not_called()


class TestDorisIdentifierValidation:
    def test_valid_identifiers(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        assert adapter._validate_identifier("customer") is True
        assert adapter._validate_identifier("order_items") is True
        assert adapter._validate_identifier("TPC_H_lineitem") is True
        assert adapter._validate_identifier("_private") is True

    def test_invalid_identifiers(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        assert adapter._validate_identifier("") is False
        assert adapter._validate_identifier("123abc") is False
        assert adapter._validate_identifier("drop; table") is False
        assert adapter._validate_identifier("name with spaces") is False
        assert adapter._validate_identifier(None) is False


class TestDorisValidation:
    def test_validate_platform_capabilities(self):
        try:
            import pymysql
        except ImportError:
            pytest.skip("pymysql not installed")

        adapter = DorisAdapter()
        result = adapter.validate_platform_capabilities("tpch")

        assert result is not None
        assert result.is_valid is True
        assert result.details["platform"] == "Apache Doris"
        assert result.details["pymysql_available"] is True

    def test_validate_connection_health(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [
            (1,),
            ("5.7.99",),
            ("doris version doris-4.0.3-rc03-e9096296b8b",),
            ("test_db",),
        ]

        result = adapter.validate_connection_health(mock_connection)

        assert result is not None
        assert result.is_valid is True
        assert result.details["basic_query_test"] == "passed"
        assert result.details["server_version"] == "4.0.3-rc03"
        assert result.details["mysql_protocol_version"] == "5.7.99"
        assert result.details["version_comment"] == "doris version doris-4.0.3-rc03-e9096296b8b"

    def test_validate_connection_health_failure(self):
        try:
            adapter = DorisAdapter()
        except ImportError:
            pytest.skip("pymysql not installed")

        mock_connection = Mock()
        mock_connection.cursor.side_effect = Exception("Connection lost")

        result = adapter.validate_connection_health(mock_connection)

        assert result is not None
        assert result.is_valid is False
        assert len(result.errors) > 0


class TestDorisBuildConfig:
    def test_build_config_from_options(self):
        from benchbox.platforms.doris import _build_doris_config

        options = {
            "host": "my-doris",
            "port": 9030,
            "http_port": 8030,
            "username": "admin",
            "password": "secret",
            "database": "my_db",
        }

        config = _build_doris_config("doris", options, {}, None)

        assert config.host == "my-doris"
        assert config.port == 9030
        assert config.http_port == 8030
        assert config.username == "admin"
        assert config.password == "secret"
        assert config.database == "my_db"
        assert config.type == "doris"

    def test_build_config_defaults(self):
        from benchbox.platforms.doris import _build_doris_config

        config = _build_doris_config("doris", {}, {}, None)

        assert config.host == "localhost"
        assert config.port == 9030
        assert config.http_port == 8030
        assert config.username == "root"

    def test_build_config_returns_database_config(self):
        from benchbox.core.schemas import DatabaseConfig
        from benchbox.platforms.doris import _build_doris_config

        config = _build_doris_config("doris", {}, {}, None)

        assert isinstance(config, DatabaseConfig)
        assert config.type == "doris"
        assert config.name == "Apache Doris"


class TestDorisTlsUrls:
    def test_stream_load_url_defaults_to_http(self):
        adapter = DorisAdapter(host="myhost", http_port=8030, be_http_port=8040, database="test_db")
        assert adapter.use_tls is False

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"Status": "Success", "NumberLoadedRows": 1}

        with (
            tempfile.NamedTemporaryFile(mode="w", suffix=".csv", encoding="utf-8", delete=False) as f,
        ):
            f.write("1,test\n")
            temp_path = f.name

        try:
            with patch("benchbox.platforms.doris._requests") as mock_requests:
                mock_requests.put.return_value = mock_response
                adapter._stream_load_file("test_table", temp_path, CsvDialect(",", False, None, False, None))
                url = mock_requests.put.call_args[0][0]
                assert url.startswith("http://")
                assert "myhost:8030" in url
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_stream_load_url_uses_https_when_tls_enabled(self):
        adapter = DorisAdapter(host="myhost", http_port=8030, be_http_port=8040, database="test_db", use_tls=True)
        assert adapter.use_tls is True

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"Status": "Success", "NumberLoadedRows": 1}

        with (
            tempfile.NamedTemporaryFile(mode="w", suffix=".csv", encoding="utf-8", delete=False) as f,
        ):
            f.write("1,test\n")
            temp_path = f.name

        try:
            with patch("benchbox.platforms.doris._requests") as mock_requests:
                mock_requests.put.return_value = mock_response
                adapter._stream_load_file("test_table", temp_path, CsvDialect(",", False, None, False, None))
                url = mock_requests.put.call_args[0][0]
                assert url.startswith("https://")
                assert "myhost:8030" in url
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_from_config_passes_use_tls(self):
        config = {
            "host": "localhost",
            "use_tls": True,
            "benchmark": "tpch",
            "scale_factor": 0.01,
        }
        adapter = DorisAdapter.from_config(config)
        assert adapter.use_tls is True


class TestDorisChunkedLoading:
    def test_small_file_uses_single_load(self):
        adapter = DorisAdapter(database="test_db", stream_load_chunk_size=1024)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"Status": "Success", "NumberLoadedRows": 3}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", encoding="utf-8", delete=False) as f:
            f.write("1,a\n2,b\n3,c\n")
            temp_path = Path(f.name)

        try:
            with patch("benchbox.platforms.doris._requests") as mock_requests:
                mock_requests.put.return_value = mock_response
                rows = adapter._stream_load_file("test_table", temp_path, CsvDialect(",", False, None, False, None))

            assert rows == 3
            assert mock_requests.put.call_count == 1
        finally:
            temp_path.unlink()

    def test_large_file_uses_chunked_load(self):
        adapter = DorisAdapter(database="test_db", stream_load_chunk_size=20)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"Status": "Success", "NumberLoadedRows": 2}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", encoding="utf-8", delete=False) as f:
            f.write("1,aaaaaaa\n2,bbbbbbb\n3,ccccccc\n4,ddddddd\n")
            temp_path = Path(f.name)

        try:
            with patch("benchbox.platforms.doris._requests") as mock_requests:
                mock_requests.put.return_value = mock_response
                rows = adapter._stream_load_file("test_table", temp_path, CsvDialect(",", False, None, False, None))

            assert mock_requests.put.call_count > 1
            assert rows == 2 * mock_requests.put.call_count
        finally:
            temp_path.unlink()

    def test_chunked_load_tpc_format_preserves_trailing_separator(self):
        adapter = DorisAdapter(database="test_db", stream_load_chunk_size=15)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"Status": "Success", "NumberLoadedRows": 1}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".tbl", encoding="utf-8", delete=False) as f:
            f.write("1|val1|\n2|val2|\n")
            temp_path = Path(f.name)

        try:
            with patch("benchbox.platforms.doris._requests") as mock_requests:
                mock_requests.put.return_value = mock_response
                adapter._stream_load_file("test_table", temp_path, CsvDialect("|", False, "", False, None))

            sent = b"".join(call.kwargs["data"] for call in mock_requests.put.call_args_list)
            lines = sent.decode("utf-8").split("\n")
            assert lines == ["1|val1|", "2|val2|"], f"Rows were modified before sending - got: {lines}"
        finally:
            temp_path.unlink()

    def test_chunked_load_preserves_rows_without_trailing_separator(self):
        adapter = DorisAdapter(database="test_db", stream_load_chunk_size=50)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"Status": "Success", "NumberLoadedRows": 1}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".tbl", encoding="utf-8", delete=False) as f:
            f.write("1|val1|\n2|val2\n")
            temp_path = Path(f.name)

        try:
            with patch("benchbox.platforms.doris._requests") as mock_requests:
                mock_requests.put.return_value = mock_response
                adapter._stream_load_file("test_table", temp_path, CsvDialect("|", False, "", False, None))

            sent = b"".join(call.kwargs["data"] for call in mock_requests.put.call_args_list)
            lines = sent.decode("utf-8").split("\n")
            assert "1|val1|" in lines, "Trailing | stripped from NULL-last-column row"
            assert "2|val2" in lines, "Non-trailing-| row was modified"
        finally:
            temp_path.unlink()

    def test_chunked_load_handles_failure(self):
        adapter = DorisAdapter(database="test_db", stream_load_chunk_size=10)

        mock_response = Mock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", encoding="utf-8", delete=False) as f:
            f.write("1,aaaaaaa\n2,bbbbbbb\n")
            temp_path = Path(f.name)

        try:
            with patch("benchbox.platforms.doris._requests") as mock_requests:
                mock_requests.put.return_value = mock_response
                with pytest.raises(RuntimeError, match="chunk.*failed"):
                    adapter._stream_load_file("test_table", temp_path, CsvDialect(",", False, None, False, None))
        finally:
            temp_path.unlink()

    def test_stream_load_chunk_size_config(self):
        adapter = DorisAdapter(stream_load_chunk_size=50 * 1024 * 1024)
        assert adapter.stream_load_chunk_size == 50 * 1024 * 1024

    def test_stream_load_chunk_size_default(self):
        adapter = DorisAdapter()
        assert adapter.stream_load_chunk_size == 10 * 1024 * 1024

    def test_from_config_passes_chunk_size(self):
        config = {"stream_load_chunk_size": 200 * 1024 * 1024}
        adapter = DorisAdapter.from_config(config)
        assert adapter.stream_load_chunk_size == 200 * 1024 * 1024


class TestDorisTableModel:
    def test_default_table_model_is_duplicate(self):
        adapter = DorisAdapter()
        assert adapter.table_model == "duplicate"

    def test_table_model_aggregate(self):
        adapter = DorisAdapter(table_model="aggregate")
        assert adapter.table_model == "aggregate"

    def test_table_model_unique(self):
        adapter = DorisAdapter(table_model="unique")
        assert adapter.table_model == "unique"

    def test_table_model_case_insensitive(self):
        adapter = DorisAdapter(table_model="DUPLICATE")
        assert adapter.table_model == "duplicate"

    def test_invalid_table_model_raises(self):
        with pytest.raises(ValueError, match="Invalid table_model"):
            DorisAdapter(table_model="invalid")

    @pytest.mark.parametrize(
        ("model", "table", "expected"),
        [
            ("duplicate", "lineitem", "DUPLICATE KEY(l_orderkey)"),
            ("aggregate", "orders", "AGGREGATE KEY(o_orderkey)"),
            ("unique", "customer", "UNIQUE KEY(c_custkey)"),
        ],
    )
    def test_translated_ddl_uses_table_model_and_tpch_keys(self, model, table, expected):
        from benchbox.core.tpch.schema import get_create_all_tables_sql

        adapter = DorisAdapter(table_model=model)
        statements = get_create_all_tables_sql().split(";")
        statement = next(
            s.strip() for s in statements if f"CREATE TABLE {table} " in s or f"CREATE TABLE {table}(" in s
        )
        translated = adapter._translate_create_table(statement)
        flat = translated.replace("`", "").replace(" (", "(")
        assert expected in flat

    def test_from_config_passes_table_model(self):
        config = {"table_model": "unique"}
        adapter = DorisAdapter.from_config(config)
        assert adapter.table_model == "unique"


class TestDorisDistribution:
    def test_default_buckets(self):
        adapter = DorisAdapter()
        assert adapter.default_buckets == 10

    def test_custom_buckets(self):
        adapter = DorisAdapter(default_buckets=32)
        assert adapter.default_buckets == 32

    def test_translated_ddl_distributes_by_tpch_key_with_default_buckets(self):
        adapter = DorisAdapter(default_buckets=8)
        translated = adapter._translate_create_table("CREATE TABLE customer (c_custkey INT, c_name VARCHAR(25))")
        flat = translated.replace("`", "").replace(" (", "(")
        assert "DISTRIBUTED BY HASH(c_custkey) BUCKETS 8" in flat

    def test_bucket_count_scales_with_sf(self):
        adapter = DorisAdapter(default_buckets=4)
        assert adapter._compute_bucket_count("lineitem", 100) > adapter._compute_bucket_count("lineitem", 0.01)

    def test_bucket_count_capped_at_128(self):
        adapter = DorisAdapter()
        assert adapter._compute_bucket_count("lineitem", 10000) <= 128

    def test_from_config_passes_default_buckets(self):
        config = {"default_buckets": 16}
        adapter = DorisAdapter.from_config(config)
        assert adapter.default_buckets == 16


class TestDorisPartitioning:
    def test_partitioning_disabled_by_default(self):
        adapter = DorisAdapter()
        assert adapter.enable_partitioning is False

    def test_partition_clause_disabled(self):
        adapter = DorisAdapter(enable_partitioning=False)
        clause = adapter.get_partition_clause("lineitem")
        assert clause == ""

    def test_partition_clause_lineitem(self):
        adapter = DorisAdapter(enable_partitioning=True)
        clause = adapter.get_partition_clause("lineitem")
        assert "PARTITION BY RANGE(l_shipdate)" in clause
        assert "p1992" in clause
        assert "p1998" in clause
        assert "VALUES LESS THAN" in clause

    def test_partition_clause_orders(self):
        adapter = DorisAdapter(enable_partitioning=True)
        clause = adapter.get_partition_clause("orders")
        assert "PARTITION BY RANGE(o_orderdate)" in clause

    def test_partition_clause_small_table(self):
        adapter = DorisAdapter(enable_partitioning=True)
        assert adapter.get_partition_clause("customer") == ""
        assert adapter.get_partition_clause("nation") == ""
        assert adapter.get_partition_clause("region") == ""

    def test_partition_clause_unknown_table(self):
        adapter = DorisAdapter(enable_partitioning=True)
        assert adapter.get_partition_clause("unknown") == ""

    def test_from_config_passes_enable_partitioning(self):
        config = {"enable_partitioning": True}
        adapter = DorisAdapter.from_config(config)
        assert adapter.enable_partitioning is True


class TestDorisCacheValidation:
    def test_cache_validation_passes(self):
        adapter = DorisAdapter()
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("enable_sql_cache", "false")

        adapter.configure_for_benchmark(mock_connection, "olap")

        calls = [str(c) for c in mock_cursor.execute.call_args_list]
        assert any("SHOW VARIABLES" in c for c in calls)

    def test_cache_validation_warns_on_failure(self, caplog):
        import logging

        adapter = DorisAdapter()
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("enable_sql_cache", "true")

        with caplog.at_level(logging.WARNING):
            adapter.configure_for_benchmark(mock_connection, "generic")
            assert any("Cache disable validation failed" in msg for msg in caplog.messages)

    def test_cache_validation_no_global_set(self):
        adapter = DorisAdapter()
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("enable_sql_cache", "false")

        adapter.configure_for_benchmark(mock_connection, "olap")

        calls = [str(c) for c in mock_cursor.execute.call_args_list]
        for call in calls:
            assert "global" not in call.lower(), f"SET global found: {call}"


class TestDorisBloomFilterIndex:
    def test_create_bloom_filter_indexes(self):
        adapter = DorisAdapter()
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        stmts = adapter.create_bloom_filter_indexes(mock_connection)

        assert len(stmts) > 0
        for stmt in stmts:
            assert "USING BLOOM_FILTER" in stmt
            assert "CREATE INDEX" in stmt

    def test_bloom_filter_on_specific_tables(self):
        adapter = DorisAdapter()
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        stmts = adapter.create_bloom_filter_indexes(mock_connection, tables=["lineitem"])

        assert len(stmts) > 0
        for stmt in stmts:
            assert "lineitem" in stmt

    def test_bloom_filter_index_names(self):
        adapter = DorisAdapter()
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        stmts = adapter.create_bloom_filter_indexes(mock_connection, tables=["lineitem"])

        for stmt in stmts:
            assert "idx_bloom_" in stmt

    def test_bloom_filter_handles_execute_failure(self):
        adapter = DorisAdapter()
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("Index already exists")

        stmts = adapter.create_bloom_filter_indexes(mock_connection, tables=["lineitem"])
        assert len(stmts) == 0


class TestDorisBitmapIndex:
    def test_create_bitmap_indexes(self):
        adapter = DorisAdapter()
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        stmts = adapter.create_bitmap_indexes(mock_connection)

        assert len(stmts) > 0
        for stmt in stmts:
            assert "USING BITMAP" in stmt
            assert "CREATE INDEX" in stmt

    def test_bitmap_on_specific_tables(self):
        adapter = DorisAdapter()
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        stmts = adapter.create_bitmap_indexes(mock_connection, tables=["orders"])

        assert len(stmts) > 0
        for stmt in stmts:
            assert "orders" in stmt

    def test_bitmap_index_names(self):
        adapter = DorisAdapter()
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        stmts = adapter.create_bitmap_indexes(mock_connection, tables=["lineitem"])

        for stmt in stmts:
            assert "idx_bitmap_" in stmt

    def test_bitmap_correct_columns(self):
        adapter = DorisAdapter()
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        stmts = adapter.create_bitmap_indexes(mock_connection, tables=["lineitem"])

        stmt_text = " ".join(stmts)
        assert "l_returnflag" in stmt_text
        assert "l_linestatus" in stmt_text

    def test_bitmap_handles_execute_failure(self):
        adapter = DorisAdapter()
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("Index already exists")

        stmts = adapter.create_bitmap_indexes(mock_connection, tables=["lineitem"])
        assert len(stmts) == 0

    def test_bitmap_no_columns_for_unknown_table(self):
        adapter = DorisAdapter()
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        stmts = adapter.create_bitmap_indexes(mock_connection, tables=["unknown_table"])
        assert len(stmts) == 0


class TestDorisPlatformInfoNewFields:
    def test_platform_info_includes_table_model(self):
        adapter = DorisAdapter(table_model="unique")
        info = adapter.get_platform_info()
        assert info["configuration"]["table_model"] == "unique"

    def test_platform_info_includes_default_buckets(self):
        adapter = DorisAdapter(default_buckets=32)
        info = adapter.get_platform_info()
        assert info["configuration"]["default_buckets"] == 32

    def test_platform_info_includes_chunk_size(self):
        adapter = DorisAdapter(stream_load_chunk_size=50 * 1024 * 1024)
        info = adapter.get_platform_info()
        assert info["configuration"]["stream_load_chunk_size"] == 50 * 1024 * 1024

    def test_platform_info_includes_partitioning(self):
        adapter = DorisAdapter(enable_partitioning=True)
        info = adapter.get_platform_info()
        assert info["configuration"]["enable_partitioning"] is True

    def test_platform_info_includes_index_configs(self):
        adapter = DorisAdapter(enable_bloom_filter=True, enable_bitmap_index=True)
        info = adapter.get_platform_info()
        assert info["configuration"]["enable_bloom_filter"] is True
        assert info["configuration"]["enable_bitmap_index"] is True

    def test_platform_info_includes_stream_load_max_filter_ratio(self):
        adapter = DorisAdapter(stream_load_max_filter_ratio=0.001)
        info = adapter.get_platform_info()
        assert info["configuration"]["stream_load_max_filter_ratio"] == "0.001"


class TestDorisTlsCertValidation:
    def test_defaults_verify_ssl_true_no_ca_cert(self):
        adapter = DorisAdapter()
        assert adapter.verify_ssl is True
        assert adapter.ca_cert_path is None

    def test_verify_ssl_false_disables_validation(self):
        adapter = DorisAdapter(database="test_db", verify_ssl=False)
        assert adapter.verify_ssl is False

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"Status": "Success", "NumberLoadedRows": 1}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", encoding="utf-8", delete=False) as f:
            f.write("1,test\n")
            temp_path = f.name

        try:
            with patch("benchbox.platforms.doris._requests") as mock_requests:
                mock_requests.put.return_value = mock_response
                adapter._stream_load_file("test_table", temp_path, CsvDialect(",", False, None, False, None))
                _, kwargs = mock_requests.put.call_args
                assert kwargs["verify"] is False
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_ca_cert_path_passed_as_verify(self):
        adapter = DorisAdapter(database="test_db", ca_cert_path="/etc/ssl/custom-ca.crt")
        assert adapter.ca_cert_path == "/etc/ssl/custom-ca.crt"

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"Status": "Success", "NumberLoadedRows": 1}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", encoding="utf-8", delete=False) as f:
            f.write("1,test\n")
            temp_path = f.name

        try:
            with patch("benchbox.platforms.doris._requests") as mock_requests:
                mock_requests.put.return_value = mock_response
                adapter._stream_load_file("test_table", temp_path, CsvDialect(",", False, None, False, None))
                _, kwargs = mock_requests.put.call_args
                assert kwargs["verify"] == "/etc/ssl/custom-ca.crt"
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_chunked_load_passes_verify_ssl(self):
        adapter = DorisAdapter(database="test_db", stream_load_chunk_size=10, verify_ssl=False)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"Status": "Success", "NumberLoadedRows": 5}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", encoding="utf-8", delete=False) as f:
            for i in range(20):
                f.write(f"{i},value_{i}\n")
            temp_path = f.name

        try:
            with patch("benchbox.platforms.doris._requests") as mock_requests:
                mock_requests.put.return_value = mock_response
                adapter._stream_load_file("test_table", temp_path, CsvDialect(",", False, None, False, None))
                for call in mock_requests.put.call_args_list:
                    _, kwargs = call
                    assert kwargs["verify"] is False
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_from_config_passes_verify_ssl_and_ca_cert_path(self):
        config = {
            "host": "localhost",
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "verify_ssl": False,
            "ca_cert_path": "/etc/ssl/my-ca.crt",
        }
        adapter = DorisAdapter.from_config(config)
        assert adapter.verify_ssl is False
        assert adapter.ca_cert_path == "/etc/ssl/my-ca.crt"


class TestDorisStreamLoadFilterRatio:
    def test_invalid_stream_load_max_filter_ratio_raises(self):
        with pytest.raises(
            ValueError,
            match="stream_load_max_filter_ratio must be between 0 and 1 inclusive",
        ):
            DorisAdapter(stream_load_max_filter_ratio=1.5)

    def test_from_config_passes_max_filter_ratio(self):
        adapter = DorisAdapter.from_config({"stream_load_max_filter_ratio": 0.001})
        assert adapter.stream_load_max_filter_ratio == "0.001"

    def test_filtered_rows_raise_by_default(self):
        adapter = DorisAdapter(database="test_db")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "Status": "Success",
            "NumberLoadedRows": 2,
            "NumberFilteredRows": 1,
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", encoding="utf-8", delete=False) as f:
            f.write("1,a\n2,b\n3,c\n")
            temp_path = Path(f.name)

        try:
            with patch("benchbox.platforms.doris._requests") as mock_requests:
                mock_requests.put.return_value = mock_response
                with pytest.raises(RuntimeError, match="refusing silent partial load"):
                    adapter._stream_load_file("test_table", temp_path, CsvDialect(",", False, None, False, None))
        finally:
            temp_path.unlink()

    def test_filtered_rows_can_be_allowed_explicitly(self, caplog):
        import logging

        adapter = DorisAdapter(database="test_db", stream_load_max_filter_ratio=0.001)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "Status": "Success",
            "NumberLoadedRows": 2,
            "NumberFilteredRows": 1,
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", encoding="utf-8", delete=False) as f:
            f.write("1,a\n2,b\n3,c\n")
            temp_path = Path(f.name)

        try:
            with patch("benchbox.platforms.doris._requests") as mock_requests:
                mock_requests.put.return_value = mock_response
                with caplog.at_level(logging.WARNING):
                    rows = adapter._stream_load_file("test_table", temp_path, CsvDialect(",", False, None, False, None))

            assert rows == 2
            assert "filtered 1 row(s)" in caplog.text
        finally:
            temp_path.unlink()


class TestDorisParquetStreamLoad:
    def test_parquet_uses_streaming_upload_with_parquet_format(self, tmp_path):
        adapter = DorisAdapter(database="test_db", stream_load_chunk_size=1)
        parquet_bytes = b"PAR1benchbox-parquetPAR1"
        data_file = tmp_path / "rows.parquet"
        data_file.write_bytes(parquet_bytes)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"Status": "Success", "NumberLoadedRows": 7}

        captured: dict[str, object] = {}

        def _put(url, **kwargs):
            captured["url"] = url
            captured["headers"] = dict(kwargs["headers"])
            captured["payload"] = kwargs["data"].read()
            return mock_response

        with patch("benchbox.platforms.doris._requests") as mock_requests:
            mock_requests.put.side_effect = _put
            rows = adapter._stream_load_file("test_table", data_file, CsvDialect(",", False, None, False, None))

        assert rows == 7
        assert mock_requests.put.call_count == 1
        assert captured["url"] == "http://localhost:8030/api/test_db/test_table/_stream_load"
        assert captured["headers"] == {
            "Expect": "100-continue",
            "format": "parquet",
            "max_filter_ratio": "0",
        }
        assert captured["payload"] == parquet_bytes

    def test_gzipped_parquet_is_decompressed_before_upload(self, tmp_path):
        adapter = DorisAdapter(database="test_db")
        parquet_bytes = b"PAR1compressed-parquetPAR1"
        data_file = tmp_path / "rows.parquet.gz"
        data_file.write_bytes(gzip.compress(parquet_bytes))

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"Status": "Success", "NumberLoadedRows": 5}

        captured: dict[str, object] = {}

        def _put(_url, **kwargs):
            captured["headers"] = dict(kwargs["headers"])
            captured["payload"] = kwargs["data"].read()
            return mock_response

        with patch("benchbox.platforms.doris._requests") as mock_requests:
            mock_requests.put.side_effect = _put
            rows = adapter._stream_load_file("test_table", data_file, CsvDialect(",", False, None, False, None))

        assert rows == 5
        assert mock_requests.put.call_count == 1
        assert captured["headers"]["format"] == "parquet"
        assert captured["payload"] == parquet_bytes


class TestDorisDdlAndIntegrityValidation:
    def test_translate_create_table_strips_array_dimensions(self):
        adapter = DorisAdapter()
        ddl = """\
CREATE TABLE vectors (
    id BIGINT PRIMARY KEY,
    embedding FLOAT[128] NOT NULL,
    tag VARCHAR(32)
);
"""

        rewritten = adapter._translate_create_table(ddl)

        assert "[128]" not in rewritten
        assert "ARRAY<" in rewritten
        assert "VARCHAR(32)" in rewritten

    def test_translate_create_table_strips_table_level_pk_with_leading_comma(self):
        adapter = DorisAdapter()
        ddl = "CREATE TABLE t (id BIGINT, name VARCHAR(32), PRIMARY KEY (id))"

        rewritten = adapter._translate_create_table(ddl)

        assert "PRIMARY KEY" not in rewritten

    def test_translate_create_table_strips_bare_table_level_pk(self):
        adapter = DorisAdapter()
        ddl = "CREATE TABLE t (PRIMARY KEY (id), id BIGINT, name VARCHAR(32))"

        rewritten = adapter._translate_create_table(ddl)

        assert "PRIMARY KEY" not in rewritten
        assert "(," not in rewritten

    def test_translate_create_table_strips_column_level_pk(self):
        adapter = DorisAdapter()
        ddl = "CREATE TABLE t (id BIGINT PRIMARY KEY, name VARCHAR(32))"

        rewritten = adapter._translate_create_table(ddl)

        assert "PRIMARY KEY" not in rewritten

    def test_validate_data_integrity_quotes_mixed_case_table_names(self):
        adapter = DorisAdapter()
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        status, details = adapter._validate_data_integrity(
            None,
            mock_connection,
            {"DimCustomer": 3},
        )

        assert status == "PASSED"
        assert details["accessible_tables"] == ["DimCustomer"]
        mock_cursor.execute.assert_called_once_with("SELECT 1 FROM `DimCustomer` LIMIT 1")
        mock_cursor.close.assert_called_once()
