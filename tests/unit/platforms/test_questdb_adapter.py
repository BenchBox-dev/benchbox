# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import http.server
import json
import socket
import threading
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

import benchbox.platforms.questdb as questdb_module
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.platforms.base.data_loading import CsvDialect, DataSource
from benchbox.platforms.questdb import QUESTDB_DIALECT, QuestDBAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def questdb_stubs(monkeypatch):
    mock_psycopg = Mock()
    mock_psycopg.__version__ = "3.1.0"

    monkeypatch.setattr(questdb_module, "psycopg", mock_psycopg)

    return mock_psycopg


class TestQuestDBAdapter:
    def test_initialization_defaults(self, questdb_stubs):
        adapter = QuestDBAdapter()

        assert adapter.platform_name == "QuestDB"
        assert adapter.get_target_dialect() == QUESTDB_DIALECT
        assert adapter.host == "localhost"
        assert adapter.pg_port == 8812
        assert adapter.http_port == 9000
        assert adapter.database == "qdb"
        assert adapter.username == "admin"
        assert adapter.password == "quest"
        assert adapter.skip_database_management is True

    def test_initialization_with_config(self, questdb_stubs):
        adapter = QuestDBAdapter(
            host="questdb.example.com",
            pg_port=8813,
            http_port=9001,
            database="custom_db",
            username="custom_user",
            password="secret",
        )

        assert adapter.host == "questdb.example.com"
        assert adapter.pg_port == 8813
        assert adapter.http_port == 9001
        assert adapter.database == "custom_db"
        assert adapter.username == "custom_user"
        assert adapter.password == "secret"

    def test_get_connection_params(self, questdb_stubs):
        adapter = QuestDBAdapter(
            host="questdb.example.com",
            pg_port=8813,
            database="testdb",
            username="testuser",
            password="testpass",
            connect_timeout=15,
        )

        params = adapter._get_connection_params()

        assert params["host"] == "questdb.example.com"
        assert params["port"] == 8813
        assert params["dbname"] == "testdb"
        assert params["user"] == "testuser"
        assert params["password"] == "testpass"
        assert params["connect_timeout"] == 15

    def test_get_connection_params_no_password(self, questdb_stubs):
        adapter = QuestDBAdapter(password=None)

        params = adapter._get_connection_params()

        assert "password" not in params

    def test_create_connection_sets_autocommit(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor
        questdb_stubs.connect.return_value = mock_conn

        adapter = QuestDBAdapter()

        with patch.object(adapter, "handle_existing_database"):
            connection = adapter.create_connection()

        assert connection is mock_conn
        assert mock_conn.autocommit is True

    def test_create_connection_disables_auto_prepare(self, questdb_stubs):
        mock_conn = Mock()
        mock_conn.cursor.return_value.fetchone.return_value = (1,)
        questdb_stubs.connect.return_value = mock_conn

        adapter = QuestDBAdapter()

        with patch.object(adapter, "handle_existing_database"):
            adapter.create_connection()

        assert mock_conn.prepare_threshold is None

    def test_new_stream_connection_disables_auto_prepare(self, questdb_stubs):
        mock_conn = Mock()
        mock_conn.cursor.return_value.fetchone.return_value = (1,)
        questdb_stubs.connect.return_value = mock_conn

        stream_conn = QuestDBAdapter().new_stream_connection(Mock())

        assert stream_conn is mock_conn
        assert mock_conn.prepare_threshold is None

    def test_create_connection_verifies_connectivity(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor
        questdb_stubs.connect.return_value = mock_conn

        adapter = QuestDBAdapter()

        with patch.object(adapter, "handle_existing_database"):
            adapter.create_connection()

        mock_cursor.execute.assert_called_with("SELECT 1")

    def test_get_platform_info_basic(self, questdb_stubs):
        adapter = QuestDBAdapter(
            host="questdb.example.com",
            pg_port=8812,
            http_port=9000,
        )

        info = adapter.get_platform_info()

        assert info["platform_type"] == "questdb"
        assert info["platform_name"] == "QuestDB"
        assert info["host"] == "questdb.example.com"
        assert info["pg_port"] == 8812
        assert info["http_port"] == 9000
        assert info["dialect"] == QUESTDB_DIALECT

    def test_get_platform_info_with_connection(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = ("7.3.10",)
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()

        info = adapter.get_platform_info(connection=mock_conn)

        assert info["version"] == "7.3.10"

    def test_execute_query_success(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [(1, "test"), (2, "test2")]
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()

        result = adapter.execute_query(mock_conn, "SELECT * FROM test", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 2
        assert result["first_row"] == (1, "test")
        assert isinstance(result["execution_time_seconds"], float)

    def test_execute_query_failure(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.side_effect = Exception("Query failed")
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()

        result = adapter.execute_query(mock_conn, "INVALID SQL", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "FAILED"
        assert result["rows_returned"] == 0
        assert result["error"] == "Query failed"
        assert result["error_type"] == "Exception"

    def test_get_query_plan(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [
            ("Scan on test",),
            ("  Filter: (id > 5)",),
        ]
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()

        plan = adapter.get_query_plan(mock_conn, "SELECT * FROM test WHERE id > 5")

        assert "Scan" in plan
        assert "Filter" in plan
        mock_cursor.execute.assert_called_with("EXPLAIN SELECT * FROM test WHERE id > 5")

    def test_get_query_plan_failure(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.side_effect = Exception("EXPLAIN not supported")
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()

        plan = adapter.get_query_plan(mock_conn, "SELECT 1")

        assert plan is None

    def test_configure_for_benchmark(self, questdb_stubs):
        mock_conn = Mock()

        adapter = QuestDBAdapter()

        adapter.configure_for_benchmark(mock_conn, "olap")
        adapter.configure_for_benchmark(mock_conn, "oltp")

    def test_validate_identifier_valid(self, questdb_stubs):
        adapter = QuestDBAdapter()

        assert adapter._validate_identifier("my_table") is True
        assert adapter._validate_identifier("TestTable") is True
        assert adapter._validate_identifier("_private") is True
        assert adapter._validate_identifier("tbl123") is True

    def test_validate_identifier_invalid(self, questdb_stubs):
        adapter = QuestDBAdapter()

        assert adapter._validate_identifier("") is False
        assert adapter._validate_identifier(None) is False
        assert adapter._validate_identifier("123abc") is False
        assert adapter._validate_identifier("tbl-name") is False
        assert adapter._validate_identifier("a" * 128) is False
        assert adapter._validate_identifier("tbl.schema") is False

    def test_adapt_drop_table_adds_if_exists(self, questdb_stubs):
        adapter = QuestDBAdapter()

        result = adapter._adapt_drop_table("DROP TABLE lineitem")
        assert "IF EXISTS" in result
        assert "lineitem" in result

    def test_adapt_drop_table_preserves_if_exists(self, questdb_stubs):
        adapter = QuestDBAdapter()

        original = "DROP TABLE IF EXISTS lineitem"
        result = adapter._adapt_drop_table(original)
        assert result == original

    def test_close_connection(self, questdb_stubs):
        mock_conn = Mock()

        adapter = QuestDBAdapter()
        adapter.close_connection(mock_conn)

        mock_conn.close.assert_called_once()

    def test_close_connection_handles_error(self, questdb_stubs):
        mock_conn = Mock()
        mock_conn.close.side_effect = Exception("Connection already closed")

        adapter = QuestDBAdapter()
        adapter.close_connection(mock_conn)

    def test_close_connection_none(self, questdb_stubs):
        adapter = QuestDBAdapter()
        adapter.close_connection(None)

    def test_check_database_exists_true(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor
        questdb_stubs.connect.return_value = mock_conn

        adapter = QuestDBAdapter()

        assert adapter.check_database_exists() is True

    def test_check_database_exists_false(self, questdb_stubs):
        questdb_stubs.connect.side_effect = Exception("Connection refused")

        adapter = QuestDBAdapter()

        assert adapter.check_database_exists() is False

    def test_get_database_path_returns_none(self, questdb_stubs):
        adapter = QuestDBAdapter()

        assert adapter.get_database_path() is None

    def test_table_exists_true(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = ("lineitem",)
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()

        assert adapter.table_exists(mock_conn, "lineitem") is True

    def test_table_exists_false(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = None
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()

        assert adapter.table_exists(mock_conn, "nonexistent") is False

    def test_table_exists_invalid_identifier(self, questdb_stubs):
        mock_conn = Mock()

        adapter = QuestDBAdapter()

        assert adapter.table_exists(mock_conn, "invalid;name") is False

    def test_drop_table(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()
        adapter.drop_table(mock_conn, "test_table")

        mock_cursor.execute.assert_called_with('DROP TABLE IF EXISTS "test_table"')

    def test_drop_table_invalid_identifier(self, questdb_stubs):
        mock_conn = Mock()

        adapter = QuestDBAdapter()
        adapter.drop_table(mock_conn, "invalid;name")

    def test_from_config_defaults(self, questdb_stubs):
        config = {}

        adapter = QuestDBAdapter.from_config(config)

        assert adapter.host == "localhost"
        assert adapter.pg_port == 8812
        assert adapter.http_port == 9000
        assert adapter.username == "admin"
        assert adapter.database == "qdb"

    def test_from_config_custom_values(self, questdb_stubs):
        config = {
            "host": "questdb.example.com",
            "pg_port": 8813,
            "http_port": 9001,
            "username": "custom_user",
            "password": "secret",
            "database": "custom_db",
        }

        adapter = QuestDBAdapter.from_config(config)

        assert adapter.host == "questdb.example.com"
        assert adapter.pg_port == 8813
        assert adapter.http_port == 9001
        assert adapter.username == "custom_user"
        assert adapter.password == "secret"
        assert adapter.database == "custom_db"

    def test_from_config_port_fallback(self, questdb_stubs):
        config = {"port": 8813}

        adapter = QuestDBAdapter.from_config(config)

        assert adapter.pg_port == 8813

    def test_from_config_passes_tuning_config(self, questdb_stubs):
        config = {
            "tuning_enabled": True,
            "verbose_enabled": True,
            "very_verbose": True,
        }

        adapter = QuestDBAdapter.from_config(config)

        assert adapter.platform_config.get("tuning_enabled") is True

    def test_from_config_passes_tuning_provenance(self, questdb_stubs):
        config = {
            "tuning_enabled": True,
            "tuning_source": "explicit_file",
            "tuning_source_file": "examples/tunings/questdb/tpch_tuned.yaml",
        }

        adapter = QuestDBAdapter.from_config(config)

        assert adapter.tuning_source == "explicit_file"
        assert adapter.tuning_source_file == "examples/tunings/questdb/tpch_tuned.yaml"

    def test_create_schema_skips_foreign_keys(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()

        mock_benchmark = Mock()

        with patch.object(adapter, "_create_schema_with_tuning") as mock_schema:
            mock_schema.return_value = (
                "CREATE TABLE t1 (id INT);"
                "ALTER TABLE t1 ADD FOREIGN KEY (cid) REFERENCES t2(id);"
                "CREATE TABLE t2 (id INT)"
            )
            adapter.create_schema(mock_benchmark, mock_conn)

        executed_calls = [str(call) for call in mock_cursor.execute.call_args_list]
        executed_text = " ".join(executed_calls)
        assert "CREATE TABLE t1" in executed_text
        assert "CREATE TABLE t2" in executed_text
        assert "FOREIGN KEY" not in executed_text

    def test_rest_api_tbl_strips_trailing_delimiter(self, questdb_stubs, tmp_path):
        adapter = QuestDBAdapter(host="localhost", http_port=9000)
        data_file = tmp_path / "lineitem.tbl"
        data_file.write_text("1|2|\n3|4|\n", encoding="utf-8")

        captured_body = {}
        mock_requests = Mock()
        mock_response = Mock(status_code=200, text="| Rows imported | 2 |")
        mock_requests.post.return_value = mock_response

        def _post(url, **kwargs):
            f = kwargs["files"]["data"][1]
            captured_body["content"] = f.read()
            return mock_response

        mock_requests.post.side_effect = _post
        dialect = CsvDialect("|", False, "", False, None)

        with patch.dict("sys.modules", {"requests": mock_requests}):
            adapter._load_table_via_rest_api("lineitem", data_file, dialect)

        assert b"1|2\n" in captured_body["content"]
        assert b"3|4\n" in captured_body["content"]
        assert b"1|2|\n" not in captured_body["content"]

    def test_ilp_dat_keeps_trailing_null_column(self, questdb_stubs, tmp_path):
        data_file = tmp_path / "store_sales.dat"
        data_file.write_text("1|2|\n3|4|5\n7|8|9|\n", encoding="utf-8")

        received: list[bytes] = []
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)

        def _serve():
            conn, _ = server.accept()
            with conn:
                while chunk := conn.recv(4096):
                    received.append(chunk)

        thread = threading.Thread(target=_serve)
        thread.start()
        try:
            adapter = QuestDBAdapter(host="localhost")
            adapter.ilp_host = "127.0.0.1"
            adapter.ilp_port = server.getsockname()[1]
            dialect = CsvDialect("|", False, "", False, None)
            with patch.object(adapter, "_get_table_columns", return_value=["a", "b", "c"]):
                rows_sent = adapter._load_table_via_ilp("store_sales", data_file, dialect)
        finally:
            thread.join(timeout=10)
            server.close()

        lines = b"".join(received).decode().splitlines()
        assert rows_sent == 3
        assert len(lines) == 3
        assert "c=" not in lines[0]
        assert "c=" in lines[1]
        assert "c=" in lines[2]

    @pytest.fixture
    def loopback_questdb(self):
        captured = {"imports": []}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"dataset": [["a"], ["b"], ["c"]]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                captured["imports"].append(self.rfile.read(int(self.headers["Content-Length"])))
                body = b"| Rows imported | 2 |"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                return

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            yield server.server_address[1], captured
        finally:
            server.shutdown()
            thread.join(timeout=10)
            server.server_close()

    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            (b"1|2|3|\n4|5|6|\n", b"1|2|3\n4|5|6\n"),
            (b"1|2|\n4|5|6\n", b"1|2|\n4|5|6\n"),
        ],
    )
    def test_rest_api_dat_strips_only_a_terminating_delimiter(
        self, questdb_stubs, loopback_questdb, tmp_path, content, expected
    ):
        port, captured = loopback_questdb
        adapter = QuestDBAdapter(host="127.0.0.1", http_port=port)
        data_file = tmp_path / "store_sales.dat"
        data_file.write_bytes(content)

        adapter._load_table_via_rest_api("store_sales", data_file, CsvDialect("|", False, "", False, None))

        assert expected in captured["imports"][0]

    def test_rest_api_csv_passes_data_verbatim(self, questdb_stubs, tmp_path):
        adapter = QuestDBAdapter(host="localhost", http_port=9000)
        data_file = tmp_path / "hits.csv"
        data_file.write_bytes(b"1,hello\n2,world\n")

        captured_body = {}
        mock_requests = Mock()
        mock_response = Mock(status_code=200, text="| Rows imported | 2 |")

        def _post(url, **kwargs):
            f = kwargs["files"]["data"][1]
            captured_body["content"] = f.read()
            return mock_response

        mock_requests.post.side_effect = _post
        dialect = CsvDialect(",", False, None, False, None)

        with patch.dict("sys.modules", {"requests": mock_requests}):
            adapter._load_table_via_rest_api("hits", data_file, dialect)

        assert b"1,hello\n" in captured_body["content"]
        assert b"2,world\n" in captured_body["content"]

    def test_cursor_closed_on_execute_query_failure(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.execute.side_effect = Exception("Query failed")
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()
        adapter.execute_query(mock_conn, "SELECT 1", "q1")

        mock_cursor.close.assert_called_once()

    def test_cursor_closed_on_execute_query_success(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [(1,)]
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()
        adapter.execute_query(mock_conn, "SELECT 1", "q1")

        mock_cursor.close.assert_called_once()

    def test_cursor_closed_on_create_schema_failure(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.execute.side_effect = [None, KeyboardInterrupt("interrupted")]
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()

        with patch.object(adapter, "_create_schema_with_tuning") as mock_schema:
            mock_schema.return_value = "CREATE TABLE t1 (id INT); CREATE TABLE t2 (id INT)"
            with pytest.raises(KeyboardInterrupt):
                adapter.create_schema(Mock(), mock_conn)

        mock_cursor.close.assert_called_once()

    def test_create_schema_raises_on_create_table_failure(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.execute.side_effect = Exception("Syntax error")
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()

        with (
            patch.object(adapter, "_create_schema_with_tuning") as mock_schema,
            pytest.raises(RuntimeError, match="critical CREATE TABLE"),
        ):
            mock_schema.return_value = "CREATE TABLE lineitem (id INT)"
            adapter.create_schema(Mock(), mock_conn)

    def test_create_schema_strips_fk_constraints(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()

        with patch.object(adapter, "_create_schema_with_tuning") as mock_schema:
            mock_schema.return_value = (
                "CREATE TABLE orders (id INT, cust_id INT, FOREIGN KEY (cust_id) REFERENCES customer(id))"
            )
            adapter.create_schema(Mock(), mock_conn)

        executed_sql = mock_cursor.execute.call_args[0][0]
        assert "CREATE TABLE" in executed_sql
        assert "FOREIGN KEY" not in executed_sql

    def test_create_schema_pre_drops_schema_defined_tables(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()
        benchmark = Mock()
        benchmark.tables = {"orders": "orders.tbl"}

        with patch.object(adapter, "_create_schema_with_tuning") as mock_schema:
            mock_schema.return_value = (
                "CREATE TABLE orders (id INT);"
                "CREATE TABLE batch_metadata (batch_id INT);"
                "CREATE TABLE ddl_truncate_target (o_orderkey INT)"
            )
            adapter.create_schema(benchmark, mock_conn)

        executed = [call[0][0] for call in mock_cursor.execute.call_args_list]
        drops = [sql for sql in executed if sql.startswith("DROP TABLE IF EXISTS")]
        assert drops == [
            'DROP TABLE IF EXISTS "orders"',
            'DROP TABLE IF EXISTS "batch_metadata"',
            'DROP TABLE IF EXISTS "ddl_truncate_target"',
        ]
        first_create = next(i for i, sql in enumerate(executed) if sql.startswith("CREATE TABLE"))
        assert executed.index(drops[-1]) < first_create

    def test_create_schema_maps_time_columns_to_varchar(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()

        with patch.object(adapter, "_create_schema_with_tuning") as mock_schema:
            mock_schema.return_value = "CREATE TABLE dimtime (id BIGINT, timevalue TIME, tm TIME)"
            adapter.create_schema(Mock(), mock_conn)

        executed_sql = mock_cursor.execute.call_args[0][0]
        assert executed_sql.upper() == "CREATE TABLE DIMTIME (ID BIGINT, TIMEVALUE VARCHAR, TM VARCHAR)"

    def test_create_schema_maps_decimal_columns_to_double(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()

        with patch.object(adapter, "_create_schema_with_tuning") as mock_schema:
            mock_schema.return_value = (
                "CREATE TABLE lineitem (l_orderkey BIGINT, l_discount DECIMAL(15,2), l_tax DECIMAL)"
            )
            adapter.create_schema(Mock(), mock_conn)

        executed_sql = mock_cursor.execute.call_args[0][0]
        assert executed_sql.upper() == "CREATE TABLE LINEITEM (L_ORDERKEY BIGINT, L_DISCOUNT DOUBLE, L_TAX DOUBLE)"

    def test_platform_registry_requirements_include_requests(self):
        info = PlatformRegistry.get_platform_info("questdb")
        assert info is not None
        assert "requests>=2.28.0" in info.requirements
        assert info.installation_command == "uv add benchbox --extra questdb"

    def test_handle_existing_database_dry_run(self, questdb_stubs):
        adapter = QuestDBAdapter()
        adapter.dry_run = True
        adapter.database_was_reused = None

        adapter.handle_existing_database()

        questdb_stubs.connect.assert_not_called()
        assert adapter.database_was_reused is None

    def test_handle_existing_database_force_recreate(self, questdb_stubs):
        adapter = QuestDBAdapter()
        adapter.force_recreate = True

        adapter.handle_existing_database()

        assert adapter.database_was_reused is False
        questdb_stubs.connect.assert_not_called()

    def test_handle_existing_database_empty_tables(self, questdb_stubs):
        mock_cursor = MagicMock()
        mock_cursor.__enter__.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []
        mock_conn = Mock()
        mock_conn.cursor.return_value = mock_cursor
        questdb_stubs.connect.return_value = mock_conn

        mock_benchmark = Mock()
        mock_benchmark.tables = {"region": None, "nation": None, "lineitem": None}

        adapter = QuestDBAdapter()
        adapter.benchmark = mock_benchmark

        adapter.handle_existing_database()

        assert adapter.database_was_reused is False
        mock_conn.close.assert_called_once()

    def test_handle_existing_database_full_tables_match(self, questdb_stubs):
        mock_cursor = MagicMock()
        mock_cursor.__enter__.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("region",), ("nation",), ("lineitem",)]
        mock_conn = Mock()
        mock_conn.cursor.return_value = mock_cursor
        questdb_stubs.connect.return_value = mock_conn

        mock_benchmark = Mock()
        mock_benchmark.tables = {"region": None, "nation": None, "lineitem": None}

        adapter = QuestDBAdapter()
        adapter.benchmark = mock_benchmark

        with patch.object(adapter, "check_benchmark_tables_exist", wraps=adapter.check_benchmark_tables_exist) as hook:
            adapter.handle_existing_database()

        hook.assert_called_once()
        assert adapter.database_was_reused is True
        mock_conn.close.assert_called_once()

    @pytest.mark.parametrize(
        ("existing_columns", "expected_reuse"),
        [
            ([("customer", "c_custkey"), ("customer", "c_name"), ("customer", "extra")], True),
            ([("customer", "c_customer_sk"), ("customer", "c_name")], False),
            ([("customer", "c_custkey")], False),
            ([], False),
        ],
    )
    def test_handle_existing_database_checks_column_names(self, questdb_stubs, existing_columns, expected_reuse):
        mock_cursor = MagicMock()
        mock_cursor.__enter__.return_value = mock_cursor
        mock_cursor.fetchall.side_effect = [[("customer",)], existing_columns]
        mock_conn = Mock()
        mock_conn.cursor.return_value = mock_cursor
        questdb_stubs.connect.return_value = mock_conn

        mock_benchmark = Mock()
        mock_benchmark.get_schema.return_value = {
            "customer": {"name": "customer", "columns": [{"name": "c_custkey"}, {"name": "c_name"}]}
        }

        adapter = QuestDBAdapter()
        adapter.benchmark = mock_benchmark

        adapter.handle_existing_database()

        assert adapter.database_was_reused is expected_reuse

    def test_handle_existing_database_partial_stale_tables(self, questdb_stubs):
        mock_cursor = MagicMock()
        mock_cursor.__enter__.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("region",), ("nation",)]
        mock_conn = Mock()
        mock_conn.cursor.return_value = mock_cursor
        questdb_stubs.connect.return_value = mock_conn

        mock_benchmark = Mock()
        mock_benchmark.tables = {"region": None, "nation": None, "lineitem": None}

        adapter = QuestDBAdapter()
        adapter.benchmark = mock_benchmark

        adapter.handle_existing_database()

        assert adapter.database_was_reused is False
        mock_conn.close.assert_called_once()

    def test_handle_existing_database_connection_error(self, questdb_stubs):
        questdb_stubs.Error = Exception
        questdb_stubs.connect.side_effect = questdb_stubs.Error("Connection refused")

        mock_benchmark = Mock()
        mock_benchmark.tables = {"region": None}

        adapter = QuestDBAdapter()
        adapter.benchmark = mock_benchmark

        adapter.handle_existing_database()

        assert adapter.database_was_reused is False

    def test_handle_existing_database_psycopg_none_raises(self, monkeypatch):
        monkeypatch.setattr(questdb_module, "psycopg", None)

        adapter = QuestDBAdapter.__new__(QuestDBAdapter)
        adapter.dry_run = False
        adapter.force_recreate = False
        adapter.skip_database_management = True

        with pytest.raises(ImportError, match="psycopg is required"):
            adapter.handle_existing_database()

    def test_handle_existing_database_benchmark_not_set(self, questdb_stubs):
        mock_conn = Mock()
        questdb_stubs.connect.return_value = mock_conn

        adapter = QuestDBAdapter()

        adapter.handle_existing_database()

        assert adapter.database_was_reused is False
        mock_conn.close.assert_called_once()

    def test_handle_existing_database_empty_tables_dict(self, questdb_stubs):
        mock_conn = Mock()
        questdb_stubs.connect.return_value = mock_conn

        mock_benchmark = Mock()
        mock_benchmark.tables = {}

        adapter = QuestDBAdapter()
        adapter.benchmark = mock_benchmark

        adapter.handle_existing_database()

        assert adapter.database_was_reused is False
        mock_conn.close.assert_called_once()


class TestQuestDBTlsUrls:
    def test_use_tls_defaults_to_false(self, questdb_stubs):
        adapter = QuestDBAdapter(host="myhost", http_port=9000)
        assert adapter.use_tls is False

    def test_use_tls_can_be_enabled(self, questdb_stubs):
        adapter = QuestDBAdapter(host="myhost", http_port=9000, use_tls=True)
        assert adapter.use_tls is True

    def test_imp_url_uses_http_by_default(self, questdb_stubs, tmp_path):
        adapter = QuestDBAdapter(host="myhost", http_port=9000)
        mock_requests = Mock()
        mock_response = Mock(status_code=200, text="| Rows imported | 1 |")
        mock_requests.post.return_value = mock_response

        data_file = tmp_path / "data.csv"
        data_file.write_text("1,test\n", encoding="utf-8")
        dialect = CsvDialect(",", False, None, False, None)

        with patch.dict("sys.modules", {"requests": mock_requests}):
            adapter._load_table_via_rest_api("test_table", data_file, dialect)
            url = mock_requests.post.call_args[0][0]
            assert url == "http://myhost:9000/imp"

    def test_imp_url_uses_https_when_tls_enabled(self, questdb_stubs, tmp_path):
        adapter = QuestDBAdapter(host="myhost", http_port=9000, use_tls=True)
        mock_requests = Mock()
        mock_response = Mock(status_code=200, text="| Rows imported | 1 |")
        mock_requests.post.return_value = mock_response

        data_file = tmp_path / "data.csv"
        data_file.write_text("1,test\n", encoding="utf-8")
        dialect = CsvDialect(",", False, None, False, None)

        with patch.dict("sys.modules", {"requests": mock_requests}):
            adapter._load_table_via_rest_api("test_table", data_file, dialect)
            url = mock_requests.post.call_args[0][0]
            assert url == "https://myhost:9000/imp"

    def test_exec_url_uses_https_when_tls_enabled(self, questdb_stubs):
        adapter = QuestDBAdapter(host="myhost", http_port=9000, use_tls=True)
        mock_requests = Mock()
        mock_response = Mock(status_code=200)
        mock_response.json.return_value = {"dataset": [[42]]}
        mock_requests.get.return_value = mock_response

        with patch.dict("sys.modules", {"requests": mock_requests}):
            adapter._count_table_rows_via_http("test_table")
            url = mock_requests.get.call_args[0][0]
            assert url == "https://myhost:9000/exec"

    def test_from_config_passes_use_tls(self, questdb_stubs):
        config = {
            "host": "localhost",
            "use_tls": True,
            "benchmark": "tpch",
            "scale_factor": 0.01,
        }
        adapter = QuestDBAdapter.from_config(config)
        assert adapter.use_tls is True
