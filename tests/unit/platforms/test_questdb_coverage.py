# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

import benchbox.platforms.questdb as questdb_module
from benchbox.platforms.base.data_loading import CsvDialect
from benchbox.platforms.questdb import (
    QuestDBAdapter,
    _date_to_epoch_ns,
    _ilp_escape_field,
    _ilp_escape_measurement,
    _ilp_escape_tag_key,
)

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


class TestMissingPsycopg:
    def test_init_raises_when_psycopg_none(self, monkeypatch):
        monkeypatch.setattr(questdb_module, "psycopg", None)
        with (
            patch(
                "benchbox.platforms.questdb.check_platform_dependencies",
                return_value=(False, ["psycopg"]),
            ),
            patch(
                "benchbox.platforms.questdb.get_dependency_error_message",
                return_value="psycopg is required for QuestDB",
            ),
            pytest.raises(ImportError, match="psycopg"),
        ):
            QuestDBAdapter()


class TestAddCliArguments:
    def test_add_cli_arguments_with_valid_parser(self):
        mock_parser = Mock()
        QuestDBAdapter.add_cli_arguments(mock_parser)

        added_args = [c[1].get("dest") for c in mock_parser.add_argument.call_args_list if c[1].get("dest")]
        assert "host" in added_args
        assert "pg_port" in added_args
        assert "http_port" in added_args
        assert "ilp_port" in added_args
        assert "username" in added_args
        assert "password" in added_args
        assert "loading_method" in added_args
        assert "partition_by" in added_args

    def test_add_cli_arguments_swallows_exception(self):
        mock_parser = Mock()
        mock_parser.add_argument.side_effect = Exception("arg conflict")
        QuestDBAdapter.add_cli_arguments(mock_parser)

    def test_add_cli_arguments_no_op_without_add_argument(self):

        class FakeParser:
            pass

        QuestDBAdapter.add_cli_arguments(FakeParser())


class TestFromConfigAdvanced:
    def test_from_config_ilp_host_override(self, questdb_stubs):
        config = {"host": "dbhost", "ilp_host": "ilphost"}
        adapter = QuestDBAdapter.from_config(config)
        assert adapter.ilp_host == "ilphost"

    def test_from_config_ilp_host_fallback_to_host(self, questdb_stubs):
        config = {"host": "myhost"}
        adapter = QuestDBAdapter.from_config(config)
        assert adapter.ilp_host == "myhost"

    def test_from_config_partition_by_set(self, questdb_stubs):
        config = {"partition_by": "DAY"}
        adapter = QuestDBAdapter.from_config(config)
        assert adapter.partition_by == "DAY"

    def test_from_config_loading_method_ilp(self, questdb_stubs):
        config = {"loading_method": "ilp"}
        adapter = QuestDBAdapter.from_config(config)
        assert adapter.loading_method == "ilp"

    def test_from_config_force_recreate(self, questdb_stubs):
        config = {"force": True}
        adapter = QuestDBAdapter.from_config(config)
        assert adapter.platform_config.get("force_recreate") is True


class TestCreateSchemaBranches:
    def test_create_schema_adapts_drop_table(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()

        with patch.object(adapter, "_create_schema_with_tuning") as mock_schema:
            mock_schema.return_value = "DROP TABLE lineitem; CREATE TABLE lineitem (id INT)"
            adapter.create_schema(Mock(), mock_conn)

        executed_calls = [str(c) for c in mock_cursor.execute.call_args_list]
        executed_text = " ".join(executed_calls)
        assert "IF EXISTS" in executed_text

    def test_create_schema_applies_questdb_enhancements(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()

        with patch.object(adapter, "_create_schema_with_tuning") as mock_schema:
            mock_schema.return_value = "CREATE TABLE lineitem (l_shipdate DATE, l_returnflag VARCHAR(1))"
            adapter.create_schema(Mock(), mock_conn)

        executed_sql = mock_cursor.execute.call_args[0][0]
        assert "TIMESTAMP" in executed_sql or "SYMBOL" in executed_sql

    def test_create_schema_non_create_table_stmt_failure_does_not_raise(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.execute.side_effect = Exception("Some non-critical error")
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()

        with patch.object(adapter, "_create_schema_with_tuning") as mock_schema:
            mock_schema.return_value = "SELECT 1"
            adapter.create_schema(Mock(), mock_conn)

    def test_extract_table_name_with_if_not_exists(self, questdb_stubs):
        adapter = QuestDBAdapter()
        name = adapter._extract_table_name("CREATE TABLE IF NOT EXISTS my_table (id INT)")
        assert name == "my_table"

    def test_extract_table_name_returns_none_for_non_create(self, questdb_stubs):
        adapter = QuestDBAdapter()
        assert adapter._extract_table_name("SELECT * FROM t") is None

    def test_table_layout_unknown_table_is_plain(self, questdb_stubs):
        adapter = QuestDBAdapter()
        layout = adapter._questdb_table_layout("CREATE TABLE unknown_table (col1 INT, col2 TEXT)")
        assert layout["symbol_columns"] == []
        assert layout["designated_timestamp"] is None
        assert layout["time_partition"] is None

    def test_table_layout_no_table_name(self, questdb_stubs):
        assert QuestDBAdapter()._questdb_table_layout("SELECT 1") == {}

    def test_get_partition_for_table_with_override(self, questdb_stubs):
        adapter = QuestDBAdapter(partition_by="YEAR")
        assert adapter._get_partition_for_table("lineitem") == "YEAR"
        assert adapter._get_partition_for_table("orders") == "YEAR"

    def test_get_partition_for_table_none_returns_none(self, questdb_stubs):
        adapter = QuestDBAdapter()
        result = adapter._get_partition_for_table("customer")
        assert result == "NONE"

    @staticmethod
    def _executed(adapter, schema_sql):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        with patch.object(adapter, "_create_schema_with_tuning", return_value=schema_sql):
            adapter.create_schema(Mock(), mock_conn)
        return [call.args[0] for call in mock_cursor.execute.call_args_list]

    @pytest.mark.parametrize(
        "stmt",
        [
            "CREATE TABLE t (id INT, name VARCHAR(50), PRIMARY KEY (id))",
            "CREATE TABLE t (id INT, CONSTRAINT pk_t PRIMARY KEY (id))",
            "CREATE TABLE t (id INT PRIMARY KEY, name VARCHAR(50))",
            "CREATE TABLE t (id INT, b INT, c INT, PRIMARY KEY (id, b))",
            'CREATE TABLE t (id INT, CONSTRAINT "pk_t" PRIMARY KEY (id))',
            "CREATE TABLE t (id INT, cust_id INT REFERENCES customer(id), PRIMARY KEY (id))",
            "CREATE TABLE t (id INT, cust_id INT, FOREIGN KEY (cust_id) REFERENCES customer(id))",
        ],
    )
    def test_create_schema_strips_key_constraints(self, questdb_stubs, stmt):
        (executed,) = self._executed(QuestDBAdapter(), stmt)
        upper = executed.upper()
        assert "PRIMARY KEY" not in upper
        assert "REFERENCES" not in upper
        assert "FOREIGN KEY" not in upper
        assert "CONSTRAINT" not in upper
        assert "id INT" in executed


class TestLoadDataBranches:
    def test_load_data_invalid_identifier_skipped(self, questdb_stubs, tmp_path):
        data_file = tmp_path / "bad;name.csv"
        data_file.write_text("col1\n1\n", encoding="utf-8")

        adapter = QuestDBAdapter()
        mock_benchmark = Mock()
        mock_benchmark.tables = {"bad;name": str(data_file)}

        stats, duration, _ = adapter.load_data(mock_benchmark, Mock(), tmp_path)
        assert stats.get("bad;name") == 0

    @staticmethod
    def _make_benchmark(tables: dict[str, Any]) -> Mock:
        mock_benchmark = Mock()
        mock_benchmark.tables = tables
        mock_benchmark.csv_delimiter = None
        mock_benchmark.csv_has_header = None
        mock_benchmark.csv_normalize_booleans = None
        return mock_benchmark

    def test_load_data_missing_file_skipped(self, questdb_stubs, tmp_path):
        adapter = QuestDBAdapter()
        mock_benchmark = self._make_benchmark({"lineitem": str(tmp_path / "nonexistent.csv")})

        stats, duration, _ = adapter.load_data(mock_benchmark, Mock(), tmp_path)
        assert stats.get("lineitem") == 0

    def test_load_data_rest_method_success(self, questdb_stubs, tmp_path):
        data_file = tmp_path / "lineitem.csv"
        data_file.write_text("col1\n1\n", encoding="utf-8")

        adapter = QuestDBAdapter(loading_method="rest")
        mock_benchmark = self._make_benchmark({"lineitem": str(data_file)})

        with patch.object(adapter, "_load_table_via_rest_api", return_value=100) as mock_rest:
            stats, duration, _ = adapter.load_data(mock_benchmark, Mock(), tmp_path)

        assert mock_rest.call_count == 1
        assert mock_rest.call_args.args[:2] == ("lineitem", data_file)
        assert stats["lineitem"] == 100

    def test_load_data_ilp_method_success(self, questdb_stubs, tmp_path):
        data_file = tmp_path / "lineitem.csv"
        data_file.write_text("col1\n1\n", encoding="utf-8")

        adapter = QuestDBAdapter(loading_method="ilp")
        mock_benchmark = self._make_benchmark({"lineitem": str(data_file)})

        with patch.object(adapter, "_load_table_via_ilp", return_value=50) as mock_ilp:
            stats, duration, _ = adapter.load_data(mock_benchmark, Mock(), tmp_path)

        assert mock_ilp.call_count == 1
        assert mock_ilp.call_args.args[:2] == ("lineitem", data_file)
        assert stats["lineitem"] == 50

    def test_load_data_rest_fails_records_zero_rows(self, questdb_stubs, tmp_path):
        data_file = tmp_path / "lineitem.csv"
        data_file.write_text("col1\n1\n", encoding="utf-8")

        adapter = QuestDBAdapter(loading_method="rest")
        mock_benchmark = self._make_benchmark({"lineitem": str(data_file)})

        with patch.object(adapter, "_load_table_via_rest_api", side_effect=Exception("HTTP 500")):
            stats, duration, _ = adapter.load_data(mock_benchmark, Mock(), tmp_path)

        assert stats["lineitem"] == 0


class TestPopulateTransactionalStagingTables:
    def test_skipped_when_benchmark_lacks_staging_attributes(self, questdb_stubs):
        adapter = QuestDBAdapter()
        mock_benchmark = Mock()
        del mock_benchmark._staging_tables
        connection = Mock()
        adapter._populate_transactional_staging_tables(mock_benchmark, connection)
        connection.execute.assert_not_called()

    def test_skipped_when_staging_tables_is_not_dict(self, questdb_stubs):
        adapter = QuestDBAdapter()
        mock_benchmark = Mock()
        mock_benchmark._staging_tables = Mock()
        connection = Mock()
        adapter._populate_transactional_staging_tables(mock_benchmark, connection)
        connection.execute.assert_not_called()

    def test_skipped_for_staging_table_not_in_benchmark(self, questdb_stubs):
        adapter = QuestDBAdapter()
        mock_benchmark = Mock()
        mock_benchmark._staging_tables = {}
        mock_benchmark._populate_staging_table = Mock()
        connection = Mock()
        adapter._populate_transactional_staging_tables(mock_benchmark, connection)
        mock_benchmark._populate_staging_table.assert_not_called()

    def test_populates_empty_staging_tables(self, questdb_stubs):
        adapter = QuestDBAdapter()
        mock_benchmark = Mock()
        mock_benchmark._staging_tables = {
            "txn_orders": {},
            "txn_lineitem": {},
            "txn_customer": {},
        }
        mock_benchmark._populate_staging_table = Mock()

        mock_connection = Mock()
        count_result = Mock()
        count_result.__bool__ = Mock(return_value=True)
        count_result.__getitem__ = Mock(return_value=0)
        mock_connection.execute.return_value.fetchone.return_value = count_result

        adapter._populate_transactional_staging_tables(mock_benchmark, mock_connection)
        assert mock_benchmark._populate_staging_table.call_count == 3

    def test_already_populated_staging_tables_skipped(self, questdb_stubs):
        adapter = QuestDBAdapter()
        mock_benchmark = Mock()
        mock_benchmark._staging_tables = {"txn_orders": {}}
        mock_benchmark._populate_staging_table = Mock()

        mock_connection = Mock()
        count_result = Mock()
        count_result.__bool__ = Mock(return_value=True)
        count_result.__getitem__ = Mock(return_value=9999)
        mock_connection.execute.return_value.fetchone.return_value = count_result

        adapter._populate_transactional_staging_tables(mock_benchmark, mock_connection)
        mock_benchmark._populate_staging_table.assert_not_called()

    def test_population_failure_is_warning_not_exception(self, questdb_stubs):
        adapter = QuestDBAdapter()
        mock_benchmark = Mock()
        mock_benchmark._staging_tables = {"txn_orders": {}}
        mock_benchmark._populate_staging_table = Mock(side_effect=Exception("INSERT failed"))

        mock_connection = Mock()
        count_result = Mock()
        count_result.__bool__ = Mock(return_value=True)
        count_result.__getitem__ = Mock(return_value=0)
        mock_connection.execute.return_value.fetchone.return_value = count_result

        adapter._populate_transactional_staging_tables(mock_benchmark, mock_connection)

    def test_populates_when_count_query_throws(self, questdb_stubs):
        adapter = QuestDBAdapter()
        mock_benchmark = Mock()
        mock_benchmark._staging_tables = {"txn_orders": {}}
        mock_benchmark._populate_staging_table = Mock()

        mock_connection = Mock()
        mock_connection.execute.side_effect = Exception("table 'txn_orders' does not exist")

        adapter._populate_transactional_staging_tables(mock_benchmark, mock_connection)
        mock_benchmark._populate_staging_table.assert_called_once()


class TestLoadTableViaRestApi:
    def test_rest_api_rows_imported_from_text_response(self, questdb_stubs, tmp_path):
        data_file = tmp_path / "test.csv"
        data_file.write_text("col1\n1\n2\n", encoding="utf-8")

        adapter = QuestDBAdapter(host="myhost", http_port=9000)
        dialect = CsvDialect(",", False, None, False, None)

        questdb_text_response = (
            "+-----------+\n"
            "|  Location:  |  test_table  |\n"
            "|  Rows handled  |  42  |\n"
            "|  Rows imported  |  42  |\n"
            "+-----------+\n"
        )
        mock_response = Mock()
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.text = questdb_text_response
        mock_requests = Mock()
        mock_requests.post.return_value = mock_response

        with patch.dict("sys.modules", {"requests": mock_requests}):
            count = adapter._load_table_via_rest_api("test_table", data_file, dialect)

        assert count == 42

    def test_rest_api_text_no_match_falls_back_to_count(self, questdb_stubs, tmp_path):
        data_file = tmp_path / "test.csv"
        data_file.write_text("col1\n1\n", encoding="utf-8")

        adapter = QuestDBAdapter(host="myhost", http_port=9000)
        dialect = CsvDialect(",", False, None, False, None)

        mock_response = Mock()
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.text = "unexpected response format"
        mock_requests = Mock()
        mock_requests.post.return_value = mock_response

        with (
            patch.dict("sys.modules", {"requests": mock_requests}),
            patch.object(adapter, "_count_table_rows_via_http", return_value=5) as mock_count,
        ):
            count = adapter._load_table_via_rest_api("test_table", data_file, dialect)

        mock_count.assert_called_once_with("test_table")
        assert count == 5


class TestCountTableRowsViaHttp:
    def test_count_rows_success(self, questdb_stubs):
        adapter = QuestDBAdapter(host="myhost", http_port=9000)

        mock_response = Mock()
        mock_response.json.return_value = {"dataset": [[123]]}
        mock_requests = Mock()
        mock_requests.get.return_value = mock_response

        with patch.dict("sys.modules", {"requests": mock_requests}):
            count = adapter._count_table_rows_via_http("lineitem")

        assert count == 123

    def test_count_rows_empty_dataset(self, questdb_stubs):
        adapter = QuestDBAdapter(host="myhost", http_port=9000)

        mock_response = Mock()
        mock_response.json.return_value = {"dataset": []}
        mock_requests = Mock()
        mock_requests.get.return_value = mock_response

        with patch.dict("sys.modules", {"requests": mock_requests}):
            count = adapter._count_table_rows_via_http("lineitem")

        assert count == 0

    def test_count_rows_invalid_identifier(self, questdb_stubs):
        adapter = QuestDBAdapter(host="myhost", http_port=9000)
        count = adapter._count_table_rows_via_http("bad;name")
        assert count == 0

    def test_count_rows_request_exception(self, questdb_stubs):
        adapter = QuestDBAdapter(host="myhost", http_port=9000)

        mock_requests = Mock()
        mock_requests.get.side_effect = Exception("Connection refused")

        with patch.dict("sys.modules", {"requests": mock_requests}):
            count = adapter._count_table_rows_via_http("lineitem")

        assert count == 0


class TestGetTableColumns:
    def test_get_table_columns_success(self, questdb_stubs):
        adapter = QuestDBAdapter(host="myhost", http_port=9000)

        mock_response = Mock()
        mock_response.json.return_value = {"dataset": [["col1", "INT"], ["col2", "VARCHAR"]]}
        mock_requests = Mock()
        mock_requests.get.return_value = mock_response

        with patch.dict("sys.modules", {"requests": mock_requests}):
            cols = adapter._get_table_columns("lineitem")

        assert cols == ["col1", "col2"]

    def test_get_table_columns_invalid_identifier(self, questdb_stubs):
        adapter = QuestDBAdapter(host="myhost", http_port=9000)
        cols = adapter._get_table_columns("bad;name")
        assert cols == []

    def test_get_table_columns_request_failure(self, questdb_stubs):
        adapter = QuestDBAdapter(host="myhost", http_port=9000)

        mock_requests = Mock()
        mock_requests.get.side_effect = Exception("Connection refused")

        with patch.dict("sys.modules", {"requests": mock_requests}):
            cols = adapter._get_table_columns("lineitem")

        assert cols == []

    def test_get_table_columns_uses_https_when_tls(self, questdb_stubs):
        adapter = QuestDBAdapter(host="myhost", http_port=9000, use_tls=True)

        mock_response = Mock()
        mock_response.json.return_value = {"dataset": []}
        mock_requests = Mock()
        mock_requests.get.return_value = mock_response

        with patch.dict("sys.modules", {"requests": mock_requests}):
            adapter._get_table_columns("test_table")

        url = mock_requests.get.call_args[0][0]
        assert url.startswith("https://")


class TestLoadTableViaIlp:
    def test_ilp_raises_when_no_columns(self, questdb_stubs, tmp_path):
        data_file = tmp_path / "test.csv"
        data_file.write_text("1|2|3\n", encoding="utf-8")

        adapter = QuestDBAdapter(ilp_host="localhost", ilp_port=9009)
        dialect = CsvDialect("|", False, None, False, None)

        with patch.object(adapter, "_get_table_columns", return_value=[]):
            with pytest.raises(RuntimeError, match="Cannot determine column names"):
                adapter._load_table_via_ilp("unknown_table", data_file, dialect)

    def test_ilp_sends_rows_via_socket(self, questdb_stubs, tmp_path):
        data_file = tmp_path / "test.csv"
        data_file.write_text("1,hello,3.14\n2,world,2.71\n", encoding="utf-8")

        adapter = QuestDBAdapter(ilp_host="localhost", ilp_port=9009)
        dialect = CsvDialect(",", False, None, False, None)

        mock_socket = Mock()
        with (
            patch.object(adapter, "_get_table_columns", return_value=["id", "name", "val"]),
            patch("benchbox.platforms.questdb.socket.socket", return_value=mock_socket),
        ):
            rows = adapter._load_table_via_ilp("test_table", data_file, dialect)

        assert rows == 2
        assert mock_socket.connect.called
        assert mock_socket.sendall.called

    def test_ilp_skips_rows_with_wrong_column_count(self, questdb_stubs, tmp_path):
        data_file = tmp_path / "test.csv"
        data_file.write_text("1,hello\n2,world\n", encoding="utf-8")

        adapter = QuestDBAdapter(ilp_host="localhost", ilp_port=9009)
        dialect = CsvDialect(",", False, None, False, None)

        mock_socket = Mock()
        with (
            patch.object(adapter, "_get_table_columns", return_value=["id", "name", "extra"]),
            patch("benchbox.platforms.questdb.socket.socket", return_value=mock_socket),
        ):
            rows = adapter._load_table_via_ilp("test_table", data_file, dialect)

        assert rows == 0

    def test_ilp_flushes_in_batches(self, questdb_stubs, tmp_path):
        lines = "\n".join(f"{i},val" for i in range(1001)) + "\n"
        data_file = tmp_path / "test.csv"
        data_file.write_text(lines, encoding="utf-8")

        adapter = QuestDBAdapter(ilp_host="localhost", ilp_port=9009)
        dialect = CsvDialect(",", False, None, False, None)

        mock_socket = Mock()
        with (
            patch.object(adapter, "_get_table_columns", return_value=["id", "name"]),
            patch("benchbox.platforms.questdb.socket.socket", return_value=mock_socket),
        ):
            rows = adapter._load_table_via_ilp("test_table", data_file, dialect)

        assert rows == 1001
        assert mock_socket.sendall.call_count >= 2

    def test_ilp_socket_closed_on_error(self, questdb_stubs, tmp_path):
        data_file = tmp_path / "test.csv"
        data_file.write_text("1,hello\n", encoding="utf-8")

        adapter = QuestDBAdapter(ilp_host="localhost", ilp_port=9009)
        dialect = CsvDialect(",", False, None, False, None)

        mock_socket = Mock()
        mock_socket.connect.side_effect = Exception("Connection refused")

        with (
            patch.object(adapter, "_get_table_columns", return_value=["id", "name"]),
            patch("benchbox.platforms.questdb.socket.socket", return_value=mock_socket),
            pytest.raises(Exception, match="Connection refused"),
        ):
            adapter._load_table_via_ilp("test_table", data_file, dialect)

        mock_socket.close.assert_called_once()

    def test_ilp_strips_trailing_pipe_for_tbl_files(self, questdb_stubs, tmp_path):
        from contextlib import contextmanager

        from benchbox.platforms.base.data_loading import prepare_local_load_file as real_plf

        data_file = tmp_path / "lineitem.tbl"
        data_file.write_text("1|2|\n", encoding="utf-8")

        dialect = CsvDialect("|", False, "", False, None)
        adapter = QuestDBAdapter(ilp_host="localhost", ilp_port=9009)

        captured_strip: list[bool] = []

        @contextmanager
        def fake_plf(path, *, dialect, strip_trailing_delim):
            captured_strip.append(strip_trailing_delim)
            yield path

        mock_socket = Mock()
        with (
            patch.object(adapter, "_get_table_columns", return_value=["a", "b"]),
            patch("benchbox.platforms.questdb.socket.socket", return_value=mock_socket),
            patch("benchbox.platforms.questdb.prepare_local_load_file", new=fake_plf),
        ):
            adapter._load_table_via_ilp("lineitem", data_file, dialect)

        assert captured_strip == [True]

    def test_ilp_does_not_strip_trailing_delim_for_csv_files(self, questdb_stubs, tmp_path):
        from contextlib import contextmanager

        data_file = tmp_path / "title.csv"
        data_file.write_text("1,Comedy Adventure,,4,1957,,,,,,,\n", encoding="utf-8")

        dialect = CsvDialect(",", False, "", False, None)
        adapter = QuestDBAdapter(ilp_host="localhost", ilp_port=9009)

        captured_strip: list[bool] = []

        @contextmanager
        def fake_plf(path, *, dialect, strip_trailing_delim):
            captured_strip.append(strip_trailing_delim)
            yield path

        mock_socket = Mock()
        with (
            patch.object(
                adapter,
                "_get_table_columns",
                return_value=[
                    "id",
                    "title",
                    "kind_id",
                    "prod_year",
                    "imdb_id",
                    "phonetic",
                    "ep_of",
                    "season",
                    "episode",
                    "series_yrs",
                    "md5",
                    "imdb_idx",
                ],
            ),
            patch("benchbox.platforms.questdb.socket.socket", return_value=mock_socket),
            patch("benchbox.platforms.questdb.prepare_local_load_file", new=fake_plf),
        ):
            adapter._load_table_via_ilp("title", data_file, dialect)

        assert captured_strip == [False]


class TestRowToIlpLine:
    def test_row_to_ilp_line_basic(self):
        line = QuestDBAdapter._row_to_ilp_line(
            "test_table",
            ["id", "name", "val"],
            ["42", "hello", "3.14"],
            None,
        )
        assert line is not None
        assert "test_table" in line
        assert "id=42i" in line
        assert 'name="hello"' in line
        assert "val=3.14" in line

    def test_row_to_ilp_line_with_timestamp(self):
        line = QuestDBAdapter._row_to_ilp_line(
            "orders",
            ["order_id", "o_orderdate"],
            ["1", "1994-01-01"],
            "o_orderdate",
        )
        assert line is not None
        parts = line.split(" ")
        assert len(parts) >= 2
        assert parts[-1].isdigit()

    def test_row_to_ilp_line_empty_fields_returns_none(self):
        line = QuestDBAdapter._row_to_ilp_line(
            "test",
            ["col1", "col2"],
            ["", ""],
            None,
        )
        assert line is None

    def test_row_to_ilp_line_skips_empty_values(self):
        line = QuestDBAdapter._row_to_ilp_line(
            "test",
            ["col1", "col2", "col3"],
            ["42", "", "hello"],
            None,
        )
        assert line is not None
        assert "col1=42i" in line
        assert "col2" not in line

    def test_row_to_ilp_line_measurement_escape(self):
        line = QuestDBAdapter._row_to_ilp_line(
            "my table",
            ["val"],
            ["1"],
            None,
        )
        assert line is not None
        assert r"my\ table" in line


class TestPrepareLocalLoadFileForQuestDB:
    def test_csv_file_no_transform_yields_original_path(self, questdb_stubs, tmp_path):
        from benchbox.platforms.base.data_loading import prepare_local_load_file

        data_file = tmp_path / "data.csv"
        data_file.write_text("col1,col2\n1,hello\n", encoding="utf-8")

        dialect = CsvDialect(",", False, None, False, None)
        with prepare_local_load_file(data_file, dialect=dialect, strip_trailing_delim=False) as load_path:
            assert load_path == data_file

    def test_tbl_dialect_strips_trailing_pipe(self, questdb_stubs, tmp_path):
        from benchbox.platforms.base.data_loading import prepare_local_load_file

        data_file = tmp_path / "lineitem.tbl"
        data_file.write_text("1|2|3|\n4|5|6|\n", encoding="utf-8")

        dialect = CsvDialect("|", False, "", False, None)
        with prepare_local_load_file(data_file, dialect=dialect, strip_trailing_delim=True) as load_path:
            content = load_path.read_text(encoding="utf-8")

        assert content == "1|2|3\n4|5|6\n"

    def test_rest_api_strips_trailing_pipe_for_tbl_files(self, questdb_stubs, tmp_path):
        data_file = tmp_path / "lineitem.tbl"
        data_file.write_text("1|2|\n3|4|\n", encoding="utf-8")

        dialect = CsvDialect("|", False, "", False, None)
        adapter = QuestDBAdapter(host="localhost", http_port=9000)

        captured: list[bytes] = []

        class _FakeResp:
            text = "| Rows imported | 2 |"

            def raise_for_status(self):
                pass

        class _FakeRequests:
            def post(self, url, *, params=None, files=None, timeout=None):
                f = files["data"][1]
                captured.append(f.read())
                return _FakeResp()

        with patch.dict("sys.modules", {"requests": _FakeRequests()}):
            adapter._load_table_via_rest_api("lineitem", data_file, dialect)

        assert b"1|2\n" in captured[0]
        assert b"3|4\n" in captured[0]
        assert b"1|2|\n" not in captured[0]

    def test_rest_api_does_not_strip_trailing_delim_for_csv_files(self, questdb_stubs, tmp_path):
        data_file = tmp_path / "title.csv"
        data_file.write_bytes(b"1,Comedy Adventure,,4,1957,,,,,,,\n")

        dialect = CsvDialect(",", False, "", False, None)
        adapter = QuestDBAdapter(host="localhost", http_port=9000)

        captured: list[bytes] = []

        class _FakeResp:
            text = "| Rows imported | 1 |"

            def raise_for_status(self):
                pass

        class _FakeRequests:
            def post(self, url, *, params=None, files=None, timeout=None):
                f = files["data"][1]
                captured.append(f.read())
                return _FakeResp()

        with patch.dict("sys.modules", {"requests": _FakeRequests()}):
            adapter._load_table_via_rest_api("title", data_file, dialect)

        assert b"1,Comedy Adventure,,4,1957,,,,,,,\n" in captured[0]


class TestConfigureForBenchmarkBranches:
    @pytest.mark.parametrize("benchmark_type", ["tpch", "tpcds", "olap", "timeseries"])
    def test_configure_for_benchmark_sets_parallel_filter(self, questdb_stubs, benchmark_type):
        adapter = QuestDBAdapter()
        mock_cursor = Mock()
        mock_conn = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.configure_for_benchmark(mock_conn, benchmark_type)

        executed = [c[0][0] for c in mock_cursor.execute.call_args_list]
        assert any("parallel.filter" in s for s in executed)

    def test_configure_for_benchmark_olap_sets_page_frame(self, questdb_stubs):
        adapter = QuestDBAdapter()
        mock_cursor = Mock()
        mock_conn = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.configure_for_benchmark(mock_conn, "tpch")

        executed = [c[0][0] for c in mock_cursor.execute.call_args_list]
        assert any("page.frame.max.rows" in s for s in executed)

    def test_configure_for_benchmark_swallows_set_errors(self, questdb_stubs):
        adapter = QuestDBAdapter()
        mock_cursor = Mock()
        mock_cursor.execute.side_effect = Exception("SET not supported")
        mock_conn = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.configure_for_benchmark(mock_conn, "tpch")
        mock_cursor.close.assert_called_once()

    def test_configure_for_benchmark_cursor_closed_always(self, questdb_stubs):
        adapter = QuestDBAdapter()
        mock_cursor = Mock()
        mock_conn = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.configure_for_benchmark(mock_conn, "unknown_type")

        mock_cursor.close.assert_called_once()


class TestApplyPlatformOptimizations:
    def test_apply_platform_optimizations_sets_jit_and_parallel(self, questdb_stubs):
        adapter = QuestDBAdapter()
        mock_cursor = Mock()
        mock_conn = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.apply_platform_optimizations(Mock(), mock_conn)

        executed = [c[0][0] for c in mock_cursor.execute.call_args_list]
        assert any("parallel.filter" in s for s in executed)
        assert any("jit" in s.lower() for s in executed)
        assert any("page.frame.max.rows" in s for s in executed)

    def test_apply_platform_optimizations_swallows_errors(self, questdb_stubs):
        adapter = QuestDBAdapter()
        mock_cursor = Mock()
        mock_cursor.execute.side_effect = Exception("SET not supported")
        mock_conn = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.apply_platform_optimizations(Mock(), mock_conn)
        mock_cursor.close.assert_called_once()


class TestApplyConstraintConfiguration:
    def test_apply_constraint_configuration_is_noop(self, questdb_stubs):
        adapter = QuestDBAdapter()
        mock_conn = Mock()
        adapter.apply_constraint_configuration(Mock(), Mock(), mock_conn)
        mock_conn.cursor.assert_not_called()


class TestExecuteQueryWithValidation:
    def test_execute_query_with_validation_calls_validator(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [(1,), (2,)]
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()

        mock_validator_instance = Mock()
        mock_validator_instance.validate_query_result.return_value = None
        mock_validator_cls = Mock(return_value=mock_validator_instance)

        with patch("benchbox.core.validation.query_validation.QueryValidator", mock_validator_cls):
            adapter.execute_query(
                mock_conn,
                "SELECT * FROM lineitem",
                "Q1",
                benchmark_type="tpch",
                scale_factor=1.0,
                validate_row_count=True,
            )

        mock_validator_instance.validate_query_result.assert_called_once()

    def test_execute_query_no_validation_when_no_benchmark_type(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [(1,)]
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()

        result = adapter.execute_query(
            mock_conn,
            "SELECT 1",
            "Q1",
            validate_row_count=True,
            benchmark_type=None,
        )
        assert result["status"] == "SUCCESS"


class TestGetPlatformInfoVersionError:
    def test_get_platform_info_version_query_fails(self, questdb_stubs):
        adapter = QuestDBAdapter()

        mock_cursor = Mock()
        mock_cursor.execute.side_effect = Exception("Column not found")
        mock_conn = Mock()
        mock_conn.cursor.return_value = mock_cursor

        info = adapter.get_platform_info(connection=mock_conn)

        assert info["version"] == "unknown"

    def test_get_platform_info_configuration_block(self, questdb_stubs):
        adapter = QuestDBAdapter(
            loading_method="ilp",
            partition_by="DAY",
            ilp_port=9009,
        )

        info = adapter.get_platform_info()

        assert info["configuration"]["loading_method"] == "ilp"
        assert info["configuration"]["partition_by"] == "DAY"
        assert info["configuration"]["ilp_port"] == 9009


class TestTableOperationErrorBranches:
    def test_table_exists_exception_returns_false(self, questdb_stubs):
        mock_cursor = Mock()
        mock_cursor.execute.side_effect = Exception("Query failed")
        mock_conn = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()
        result = adapter.table_exists(mock_conn, "lineitem")
        assert result is False

    def test_drop_table_exception_logs_warning(self, questdb_stubs):
        mock_cursor = Mock()
        mock_cursor.execute.side_effect = Exception("Drop failed")
        mock_conn = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()
        adapter.drop_table(mock_conn, "lineitem")


class TestIlpHelpers:
    def test_ilp_escape_measurement_special_chars(self):
        assert _ilp_escape_measurement("my,table") == r"my\,table"
        assert _ilp_escape_measurement("my table") == r"my\ table"
        assert _ilp_escape_measurement("my=table") == r"my\=table"

    def test_ilp_escape_measurement_clean_name(self):
        assert _ilp_escape_measurement("lineitem") == "lineitem"

    def test_ilp_escape_tag_key_special_chars(self):
        assert _ilp_escape_tag_key("my,key") == r"my\,key"
        assert _ilp_escape_tag_key("my=key") == r"my\=key"
        assert _ilp_escape_tag_key("my key") == r"my\ key"

    def test_ilp_escape_field_integer(self):
        result = _ilp_escape_field("count", "42")
        assert result == "count=42i"

    def test_ilp_escape_field_float(self):
        result = _ilp_escape_field("price", "3.14")
        assert result == "price=3.14"

    def test_ilp_escape_field_string(self):
        result = _ilp_escape_field("name", "hello")
        assert result == 'name="hello"'

    def test_ilp_escape_field_empty_returns_none(self):
        result = _ilp_escape_field("name", "")
        assert result is None

    def test_ilp_escape_field_string_with_quotes(self):
        result = _ilp_escape_field("name", 'say "hello"')
        assert result == 'name="say \\"hello\\""'

    def test_ilp_escape_field_negative_integer(self):
        result = _ilp_escape_field("delta", "-5")
        assert result == "delta=-5i"

    def test_ilp_escape_field_scientific_notation(self):
        result = _ilp_escape_field("val", "1.23e10")
        assert result is not None
        assert "val=" in result


class TestDateToEpochNs:
    def test_date_format_yyyy_mm_dd(self):
        result = _date_to_epoch_ns("1994-01-01")
        assert result is not None
        assert result.isdigit()
        assert int(result) > 0

    def test_date_format_datetime(self):
        result = _date_to_epoch_ns("1994-01-01 12:30:00")
        assert result is not None
        assert result.isdigit()

    def test_date_format_iso_datetime(self):
        result = _date_to_epoch_ns("1994-01-01T12:30:00")
        assert result is not None
        assert result.isdigit()

    def test_invalid_date_returns_none(self):
        result = _date_to_epoch_ns("not-a-date")
        assert result is None

    def test_empty_string_returns_none(self):
        result = _date_to_epoch_ns("   ")
        assert result is None

    def test_epoch_ns_is_nanoseconds(self):
        result = _date_to_epoch_ns("2000-01-01")
        assert result is not None
        assert int(result) > 9 * 10**17


class TestAdapterInitEdgeCases:
    def test_ilp_host_defaults_to_host(self, questdb_stubs):
        adapter = QuestDBAdapter(host="myhost")
        assert adapter.ilp_host == "myhost"

    def test_ilp_host_explicit_override(self, questdb_stubs):
        adapter = QuestDBAdapter(host="pghost", ilp_host="ilphost")
        assert adapter.ilp_host == "ilphost"

    def test_default_loading_method(self, questdb_stubs):
        adapter = QuestDBAdapter()
        assert adapter.loading_method == "rest"

    def test_ilp_loading_method(self, questdb_stubs):
        adapter = QuestDBAdapter(loading_method="ilp")
        assert adapter.loading_method == "ilp"

    def test_partition_by_none_by_default(self, questdb_stubs):
        adapter = QuestDBAdapter()
        assert adapter.partition_by is None

    def test_connect_timeout_default(self, questdb_stubs):
        adapter = QuestDBAdapter()
        assert adapter.connect_timeout == 10

    def test_connect_timeout_custom(self, questdb_stubs):
        adapter = QuestDBAdapter(connect_timeout=30)
        assert adapter.connect_timeout == 30

    def test_parquet_chunk_rows_default(self, questdb_stubs):
        adapter = QuestDBAdapter()
        assert adapter.parquet_chunk_rows == 200_000

    def test_parquet_chunk_rows_custom(self, questdb_stubs):
        adapter = QuestDBAdapter(parquet_chunk_rows=50_000)
        assert adapter.parquet_chunk_rows == 50_000

    def test_parquet_chunk_rows_from_config(self, questdb_stubs):
        adapter = QuestDBAdapter.from_config({"parquet_chunk_rows": 10_000})
        assert adapter.parquet_chunk_rows == 10_000


class TestCreateSchemaPreDrop:
    def _make_benchmark(self, tables: dict[str, object]) -> Mock:
        benchmark = Mock()
        benchmark.tables = tables
        return benchmark

    def _collect_drops(self, mock_cursor: Mock) -> list[str]:
        return [call.args[0] for call in mock_cursor.execute.call_args_list if "DROP TABLE" in call.args[0]]

    def test_pre_drop_runs_when_not_reused(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()
        adapter.database_was_reused = False
        benchmark = self._make_benchmark({"lineitem": Mock(), "orders": Mock()})

        with patch.object(adapter, "_create_schema_with_tuning", return_value=""):
            adapter.create_schema(benchmark, mock_conn)

        drops = self._collect_drops(mock_cursor)
        assert any('"lineitem"' in d for d in drops)
        assert any('"orders"' in d for d in drops)
        assert all("IF EXISTS" in d for d in drops)

    def test_pre_drop_skipped_when_reused(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()
        adapter.database_was_reused = True
        benchmark = self._make_benchmark({"lineitem": Mock()})

        with patch.object(adapter, "_create_schema_with_tuning", return_value=""):
            adapter.create_schema(benchmark, mock_conn)

        assert self._collect_drops(mock_cursor) == []

    def test_pre_drop_skipped_in_dry_run(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter(dry_run=True)
        adapter.database_was_reused = False
        benchmark = self._make_benchmark({"lineitem": Mock()})

        with patch.object(adapter, "_create_schema_with_tuning", return_value=""):
            adapter.create_schema(benchmark, mock_conn)

        assert self._collect_drops(mock_cursor) == []

    def test_pre_drop_filters_invalid_identifiers(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()
        adapter.database_was_reused = False
        benchmark = self._make_benchmark({"good_table": Mock(), "bad;name": Mock()})

        with patch.object(adapter, "_create_schema_with_tuning", return_value=""):
            adapter.create_schema(benchmark, mock_conn)

        drops = self._collect_drops(mock_cursor)
        assert any('"good_table"' in d for d in drops)
        assert not any("bad;name" in d for d in drops)

    def test_pre_drop_skipped_when_tables_not_dict(self, questdb_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter = QuestDBAdapter()
        adapter.database_was_reused = False

        with patch.object(adapter, "_create_schema_with_tuning", return_value=""):
            adapter.create_schema(Mock(), mock_conn)

        assert self._collect_drops(mock_cursor) == []


class TestLoadDataMultiChunk:
    def test_rest_method_sums_rows_across_chunks(self, questdb_stubs, tmp_path):
        chunk_a = tmp_path / "lineitem_0.csv"
        chunk_b = tmp_path / "lineitem_1.csv"
        chunk_a.write_text("col1\n1\n", encoding="utf-8")
        chunk_b.write_text("col1\n1\n", encoding="utf-8")

        adapter = QuestDBAdapter(loading_method="rest")
        mock_benchmark = Mock()
        mock_benchmark.tables = {"lineitem": [str(chunk_a), str(chunk_b)]}

        with patch.object(adapter, "_load_table_via_rest_api", side_effect=[30, 70]) as mock_rest:
            stats, _, _ = adapter.load_data(mock_benchmark, Mock(), tmp_path)

        assert mock_rest.call_count == 2
        assert stats["lineitem"] == 100

    def test_ilp_method_sums_rows_across_chunks(self, questdb_stubs, tmp_path):
        chunk_a = tmp_path / "orders_0.csv"
        chunk_b = tmp_path / "orders_1.csv"
        chunk_a.write_text("col1\n1\n", encoding="utf-8")
        chunk_b.write_text("col1\n1\n", encoding="utf-8")

        adapter = QuestDBAdapter(loading_method="ilp")
        mock_benchmark = Mock()
        mock_benchmark.tables = {"orders": [str(chunk_a), str(chunk_b)]}

        with patch.object(adapter, "_load_table_via_ilp", side_effect=[5, 7]) as mock_ilp:
            stats, _, _ = adapter.load_data(mock_benchmark, Mock(), tmp_path)

        assert mock_ilp.call_count == 2
        assert stats["orders"] == 12

    def test_one_failing_chunk_does_not_abort_table(self, questdb_stubs, tmp_path):
        chunk_a = tmp_path / "lineitem_0.csv"
        chunk_b = tmp_path / "lineitem_1.csv"
        chunk_a.write_text("col1\n1\n", encoding="utf-8")
        chunk_b.write_text("col1\n1\n", encoding="utf-8")

        adapter = QuestDBAdapter(loading_method="rest")
        mock_benchmark = Mock()
        mock_benchmark.tables = {"lineitem": [str(chunk_a), str(chunk_b)]}

        with patch.object(
            adapter,
            "_load_table_via_rest_api",
            side_effect=[42, Exception("HTTP 500")],
        ):
            stats, _, _ = adapter.load_data(mock_benchmark, Mock(), tmp_path)

        assert stats["lineitem"] == 42

    def test_single_path_still_loads(self, questdb_stubs, tmp_path):
        data_file = tmp_path / "region.csv"
        data_file.write_text("col1\n1\n", encoding="utf-8")

        adapter = QuestDBAdapter(loading_method="rest")
        mock_benchmark = Mock()
        mock_benchmark.tables = {"region": str(data_file)}

        with patch.object(adapter, "_load_table_via_rest_api", return_value=5):
            stats, _, _ = adapter.load_data(mock_benchmark, Mock(), tmp_path)

        assert stats["region"] == 5

    def test_missing_chunks_are_skipped(self, questdb_stubs, tmp_path):
        chunk_a = tmp_path / "lineitem_0.csv"
        chunk_a.write_text("col1\n1\n", encoding="utf-8")

        adapter = QuestDBAdapter(loading_method="rest")
        mock_benchmark = Mock()
        mock_benchmark.tables = {
            "lineitem": [str(chunk_a), str(tmp_path / "missing.csv")],
        }

        with patch.object(adapter, "_load_table_via_rest_api", return_value=9) as mock_rest:
            stats, _, _ = adapter.load_data(mock_benchmark, Mock(), tmp_path)

        assert mock_rest.call_count == 1
        assert stats["lineitem"] == 9


class TestHandleExistingDatabase:
    def _make_adapter_with_benchmark(self, questdb_stubs, table_names: list[str]) -> QuestDBAdapter:
        adapter = QuestDBAdapter()
        mock_benchmark = Mock()
        mock_benchmark.tables = {name: Mock() for name in table_names}
        adapter.benchmark = mock_benchmark
        return adapter

    def _mock_connection(self, questdb_stubs, existing_tables: list[str], empty_tables: set[str]):
        mock_conn = MagicMock()
        mock_conn.autocommit = True

        tables_cursor = MagicMock()
        tables_cursor.__enter__ = lambda s: s
        tables_cursor.__exit__ = MagicMock(return_value=False)
        tables_cursor.fetchall.return_value = [(t,) for t in existing_tables]

        row_cursor = MagicMock()
        row_cursor.__enter__ = lambda s: s
        row_cursor.__exit__ = MagicMock(return_value=False)

        def fetchone_for(sql, *args, **kwargs):
            for tname in empty_tables:
                if f'"{tname}"' in sql:
                    row_cursor._last_empty = True
                    return
            row_cursor._last_empty = False

        row_cursor.execute.side_effect = fetchone_for
        row_cursor.fetchone.side_effect = lambda: None if getattr(row_cursor, "_last_empty", False) else (1,)

        mock_conn.cursor.side_effect = [tables_cursor, row_cursor]
        questdb_stubs.connect.return_value = mock_conn
        return mock_conn

    def test_reuse_when_all_tables_have_data(self, questdb_stubs):
        tables = ["region", "nation", "lineitem"]
        adapter = self._make_adapter_with_benchmark(questdb_stubs, tables)
        self._mock_connection(questdb_stubs, existing_tables=tables, empty_tables=set())

        adapter.handle_existing_database()

        assert adapter.database_was_reused is True

    def test_fresh_when_tables_exist_but_all_empty(self, questdb_stubs):
        tables = ["region", "nation", "lineitem"]
        adapter = self._make_adapter_with_benchmark(questdb_stubs, tables)
        self._mock_connection(
            questdb_stubs,
            existing_tables=tables,
            empty_tables=set(tables),
        )

        adapter.handle_existing_database()

        assert adapter.database_was_reused is False

    def test_fresh_when_some_tables_missing(self, questdb_stubs):
        tables = ["region", "nation", "lineitem"]
        adapter = self._make_adapter_with_benchmark(questdb_stubs, tables)
        self._mock_connection(
            questdb_stubs,
            existing_tables=["region", "nation"],
            empty_tables=set(),
        )

        adapter.handle_existing_database()

        assert adapter.database_was_reused is False

    def test_force_recreate_skips_check(self, questdb_stubs):
        adapter = QuestDBAdapter(force_recreate=True)

        adapter.handle_existing_database()

        assert adapter.database_was_reused is False
        questdb_stubs.connect.assert_not_called()


class TestBenchmarkGating:
    def test_questdb_tpch_is_not_gated(self):
        from benchbox.core.platform_registry import PlatformRegistry

        caps = PlatformRegistry.get_platform_capabilities("questdb")
        assert caps is not None
        assert "tpch" not in caps.unsupported_benchmarks, (
            "tpch should no longer be gated - Phase 2 SQL dialect rewriter handles comma-JOINs"
        )

    def test_questdb_comma_join_benchmarks_not_gated(self):
        from benchbox.core.platform_registry import PlatformRegistry

        caps = PlatformRegistry.get_platform_capabilities("questdb")
        assert caps is not None
        for bench in ("tpch", "tpcds", "ssb", "tpchavoc", "tpch_skew"):
            assert bench not in caps.unsupported_benchmarks, (
                f"'{bench}' should not be gated - Phase 2 SQL dialect rewriter handles comma-JOINs"
            )

    def test_questdb_vector_search_is_gated(self):
        from benchbox.core.platform_registry import PlatformRegistry

        caps = PlatformRegistry.get_platform_capabilities("questdb")
        assert caps is not None
        assert "vector_search" in caps.unsupported_benchmarks

    @pytest.mark.parametrize("benchmark_name", ["vector_search"])
    def test_all_expected_benchmarks_are_gated(self, benchmark_name):
        from benchbox.core.platform_registry import PlatformRegistry

        caps = PlatformRegistry.get_platform_capabilities("questdb")
        assert caps is not None
        assert benchmark_name in caps.unsupported_benchmarks, (
            f"Expected '{benchmark_name}' to be gated on QuestDB but it is not in unsupported_benchmarks"
        )

    def test_clean_benchmarks_not_gated(self):
        from benchbox.core.platform_registry import PlatformRegistry

        clean = [
            "tpcds_obt",
            "clickbench",
            "h2odb",
            "coffeeshop",
            "amplab",
            "nyctaxi",
            "flightdata",
            "tpcdi",
            "ai_primitives",
        ]
        caps = PlatformRegistry.get_platform_capabilities("questdb")
        assert caps is not None
        for bench in clean:
            assert bench not in caps.unsupported_benchmarks, (
                f"'{bench}' should pass on QuestDB but is wrongly listed as unsupported"
            )


class _UploadServer:
    def __init__(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from urllib.parse import parse_qs, urlparse

        self.requests: list[dict] = []
        self.reply = "| Rows imported | 1 |"
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                body = self.rfile.read(length)
                query = {key: values[0] for key, values in parse_qs(urlparse(self.path).query).items()}
                outer.requests.append({"query": query, "body": body})
                payload = outer.reply.encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.port = self._server.server_address[1]

    def close(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()


class TestLoadParquetViaChunkedCsv:
    @pytest.fixture
    def upload_server(self):
        server = _UploadServer()
        yield server
        server.close()

    @staticmethod
    def _parquet(tmp_path, rows, name="obt.parquet"):
        import pyarrow as pa
        import pyarrow.parquet as pq

        data_file = tmp_path / name
        pq.write_table(pa.table({"id": list(range(rows)), "label": [f"row{i}" for i in range(rows)]}), data_file)
        return data_file

    def test_all_chunks_use_overwrite_false(self, questdb_stubs, tmp_path, upload_server):
        data_file = self._parquet(tmp_path, 4)
        adapter = QuestDBAdapter(host="127.0.0.1", http_port=upload_server.port, parquet_chunk_rows=2)
        upload_server.reply = "| Rows imported | 100 |"

        rows = adapter._load_parquet_via_chunked_csv("obt_table", data_file)

        assert len(upload_server.requests) == 2
        for request in upload_server.requests:
            assert request["query"]["overwrite"] == "false", (
                "overwrite=true on any chunk risks discarding QuestDB schema enhancements"
            )
        assert rows == 200

    def test_header_on_every_chunk(self, questdb_stubs, tmp_path, upload_server):
        data_file = self._parquet(tmp_path, 4)
        adapter = QuestDBAdapter(host="127.0.0.1", http_port=upload_server.port, parquet_chunk_rows=2)

        adapter._load_parquet_via_chunked_csv("obt_table", data_file)

        assert len(upload_server.requests) == 2
        for request in upload_server.requests:
            assert "id,label" in request["body"].decode().replace('"', "").splitlines()

    def test_force_header_param_on_every_chunk(self, questdb_stubs, tmp_path, upload_server):
        data_file = self._parquet(tmp_path, 4)
        adapter = QuestDBAdapter(host="127.0.0.1", http_port=upload_server.port, parquet_chunk_rows=2)

        adapter._load_parquet_via_chunked_csv("obt_table", data_file)

        for request in upload_server.requests:
            assert request["query"].get("forceHeader") == "true", (
                "forceHeader=true missing - QuestDB may map headerless CSV by position "
                "and silently corrupt column order"
            )

    def test_row_count_fallback_uses_batch_len(self, questdb_stubs, tmp_path, upload_server):
        data_file = self._parquet(tmp_path, 3)
        adapter = QuestDBAdapter(host="127.0.0.1", http_port=upload_server.port)
        upload_server.reply = "unexpected response format"

        assert adapter._load_parquet_via_chunked_csv("obt_table", data_file) == 3

    def test_missing_pyarrow_raises_import_error(self, questdb_stubs, tmp_path):
        data_file = tmp_path / "obt.parquet"
        data_file.write_bytes(b"")

        adapter = QuestDBAdapter()

        with (
            patch.dict("sys.modules", {"pyarrow.parquet": None}),
            pytest.raises(ImportError, match="pyarrow is required"),
        ):
            adapter._load_parquet_via_chunked_csv("obt_table", data_file)

    def test_custom_chunk_rows_set_the_upload_size(self, questdb_stubs, tmp_path, upload_server):
        data_file = self._parquet(tmp_path, 5)
        adapter = QuestDBAdapter(host="127.0.0.1", http_port=upload_server.port, parquet_chunk_rows=2)

        adapter._load_parquet_via_chunked_csv("obt_table", data_file)

        assert len(upload_server.requests) == 3

    def test_nullable_integers_are_posted_without_a_decimal_point(self, questdb_stubs, tmp_path, upload_server):
        import pyarrow as pa
        import pyarrow.parquet as pq

        data_file = tmp_path / "title.parquet"
        pq.write_table(
            pa.table({"id": [1, 2, 3], "production_year": [1994, None, 2006], "title": ["a", "b,c", None]}),
            data_file,
        )
        adapter = QuestDBAdapter(host="127.0.0.1", http_port=upload_server.port)
        upload_server.reply = "| Rows imported | 3 |"

        rows = adapter._load_parquet_via_chunked_csv("title", data_file)

        assert rows == 3
        body = b"".join(request["body"] for request in upload_server.requests).decode()
        assert "1994," in body
        assert "1994.0" not in body
        assert "2006," in body
        assert "2,,b" in body.replace('"', "")


class TestBenchmarkCompatibilityGateCli:
    @staticmethod
    def _make_state(platform_key: str, benchmark: str):
        import types as _t

        s = _t.SimpleNamespace()
        s.platform_key = platform_key
        s.benchmark = benchmark
        s.logger = None
        s.ctx = MagicMock()
        return s

    def test_gated_benchmark_calls_ctx_exit_1(self):
        from benchbox.cli.commands.run import _check_benchmark_platform_compatibility

        s = self._make_state("questdb", "vector_search")
        _check_benchmark_platform_compatibility(s)
        s.ctx.exit.assert_called_once_with(1)

    def test_ungated_benchmark_does_not_exit(self):
        from benchbox.cli.commands.run import _check_benchmark_platform_compatibility

        s = self._make_state("questdb", "clickbench")
        _check_benchmark_platform_compatibility(s)
        s.ctx.exit.assert_not_called()

    def test_unknown_platform_does_not_exit(self):
        from benchbox.cli.commands.run import _check_benchmark_platform_compatibility

        s = self._make_state("nonexistent_platform_xyz", "tpch")
        _check_benchmark_platform_compatibility(s)
        s.ctx.exit.assert_not_called()

    def test_empty_platform_key_does_not_exit(self):
        from benchbox.cli.commands.run import _check_benchmark_platform_compatibility

        s = self._make_state("", "tpch")
        _check_benchmark_platform_compatibility(s)
        s.ctx.exit.assert_not_called()

    def test_logger_receives_error_for_gated_benchmark(self):
        from benchbox.cli.commands.run import _check_benchmark_platform_compatibility

        s = self._make_state("questdb", "vector_search")
        s.logger = MagicMock()
        _check_benchmark_platform_compatibility(s)
        s.logger.error.assert_called_once()
        msg = s.logger.error.call_args[0][0]
        assert "vector_search" in msg and "questdb" in msg
