# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import argparse
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import benchbox.platforms.postgresql as postgresql_module
from benchbox.platforms.base.data_loading import DataSource
from benchbox.platforms.postgresql import POSTGRES_DIALECT, PostgreSQLAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def postgres_stubs(monkeypatch):
    mock_psycopg = Mock()
    mock_psycopg.__version__ = "3.1.0"

    monkeypatch.setattr(postgresql_module, "psycopg", mock_psycopg)

    return mock_psycopg


class TestPostgreSQLAdapter:
    def test_initialization_defaults(self, postgres_stubs):
        adapter = PostgreSQLAdapter()

        assert adapter.platform_name == "PostgreSQL"
        assert adapter.get_target_dialect() == POSTGRES_DIALECT
        assert adapter.host == "localhost"
        assert adapter.port == 5432
        assert adapter.database == "benchbox"
        assert adapter.username == "postgres"
        assert adapter.schema == "public"
        assert adapter.sslmode == "prefer"

    def test_initialization_with_config(self, postgres_stubs):
        adapter = PostgreSQLAdapter(
            host="pg.example.com",
            port=5433,
            database="custom_db",
            username="custom_user",
            password="secret",
            schema="analytics",
            work_mem="512MB",
        )

        assert adapter.host == "pg.example.com"
        assert adapter.port == 5433
        assert adapter.database == "custom_db"
        assert adapter.username == "custom_user"
        assert adapter.password == "secret"
        assert adapter.schema == "analytics"
        assert adapter.work_mem == "512MB"

    def test_get_connection_params(self, postgres_stubs):
        adapter = PostgreSQLAdapter(
            host="pg.example.com",
            port=5433,
            database="testdb",
            username="testuser",
            password="testpass",
            sslmode="require",
            connect_timeout=15,
        )

        params = adapter._get_connection_params()

        assert params["host"] == "pg.example.com"
        assert params["port"] == 5433
        assert params["dbname"] == "testdb"
        assert params["user"] == "testuser"
        assert params["password"] == "testpass"
        assert params["sslmode"] == "require"
        assert params["connect_timeout"] == 15

    def test_get_connection_params_custom_database(self, postgres_stubs):
        adapter = PostgreSQLAdapter(database="default_db")

        params = adapter._get_connection_params(database="override_db")

        assert params["dbname"] == "override_db"

    def test_new_stream_connection_opens_independent_session(self, postgres_stubs):
        stream_connection = Mock()
        stream_cursor = Mock()
        stream_connection.cursor.return_value = stream_cursor
        postgres_stubs.connect.return_value = stream_connection
        adapter = PostgreSQLAdapter(
            host="pg.example.com",
            port=5433,
            database="testdb",
            username="testuser",
            password="testpass",
            schema="analytics",
            tuning_enabled=True,
        )
        shared_connection = Mock()

        result = adapter.new_stream_connection(shared_connection, benchmark_type="olap")

        assert result is stream_connection
        postgres_stubs.connect.assert_called_once_with(**adapter._get_connection_params())
        assert stream_cursor.execute.call_args_list == [
            (("SET work_mem = '256MB'",), {}),
            (("SET maintenance_work_mem = '512MB'",), {}),
            (("SET effective_cache_size = '1GB'",), {}),
            (("SET max_parallel_workers_per_gather = 2",), {}),
            (('SET search_path TO "analytics", public',), {}),
            (("SET enable_seqscan = on",), {}),
            (("SET enable_hashjoin = on",), {}),
            (("SET enable_mergejoin = on",), {}),
            (("SET random_page_cost = 1.1",), {}),
            (("SET cpu_tuple_cost = 0.01",), {}),
        ]
        assert stream_connection.commit.call_count == 2
        stream_cursor.close.assert_called_with()
        shared_connection.cursor.assert_not_called()

    def test_new_stream_connection_without_benchmark_type_skips_tuning_replay(self, postgres_stubs):
        stream_connection = Mock()
        stream_cursor = Mock()
        stream_connection.cursor.return_value = stream_cursor
        postgres_stubs.connect.return_value = stream_connection
        adapter = PostgreSQLAdapter(schema="analytics")

        result = adapter.new_stream_connection(Mock())

        assert result is stream_connection
        assert stream_cursor.execute.call_args_list == [
            (("SET work_mem = '256MB'",), {}),
            (("SET maintenance_work_mem = '512MB'",), {}),
            (("SET effective_cache_size = '1GB'",), {}),
            (("SET max_parallel_workers_per_gather = 2",), {}),
            (('SET search_path TO "analytics", public',), {}),
        ]
        stream_connection.commit.assert_called_once_with()

    def test_add_cli_arguments_registers_postgres_compatible_flags(self, postgres_stubs):
        parser = argparse.ArgumentParser()

        PostgreSQLAdapter.add_cli_arguments(parser)
        parsed = parser.parse_args(
            [
                "--postgres-host",
                "pg.local",
                "--postgres-port",
                "5544",
                "--postgres-database",
                "benchbox_db",
                "--postgres-username",
                "benchbox",
                "--postgres-password",
                "secret",
                "--postgres-schema",
                "analytics",
                "--postgres-work-mem",
                "768MB",
                "--postgres-maintenance-work-mem",
                "1GB",
                "--postgres-enable-timescale",
            ]
        )

        assert parsed.host == "pg.local"
        assert parsed.port == 5544
        assert parsed.database == "benchbox_db"
        assert parsed.username == "benchbox"
        assert parsed.password == "secret"
        assert parsed.schema == "analytics"
        assert parsed.work_mem == "768MB"
        assert parsed.maintenance_work_mem == "1GB"
        assert parsed.enable_timescale is True

    def test_check_server_database_exists_true(self, postgres_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor
        postgres_stubs.connect.return_value = mock_conn

        adapter = PostgreSQLAdapter(database="testdb")

        assert adapter.check_server_database_exists() is True
        postgres_stubs.connect.assert_called()

    def test_check_server_database_exists_false(self, postgres_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = None
        mock_conn.cursor.return_value = mock_cursor
        postgres_stubs.connect.return_value = mock_conn

        adapter = PostgreSQLAdapter(database="nonexistent")

        assert adapter.check_server_database_exists() is False

    def test_check_server_database_exists_connection_error(self, postgres_stubs):
        postgres_stubs.connect.side_effect = Exception("Connection refused")

        adapter = PostgreSQLAdapter()

        assert adapter.check_server_database_exists() is False

    def test_drop_database_success(self, postgres_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        postgres_stubs.connect.return_value = mock_conn

        adapter = PostgreSQLAdapter(database="to_drop")

        adapter.drop_database(database="to_drop")

        executed = " ".join(str(call) for call in mock_cursor.execute.call_args_list)
        assert "pg_terminate_backend" in executed
        assert "DROP DATABASE" in executed

    def test_drop_database_rejects_invalid_identifier(self, postgres_stubs):
        adapter = PostgreSQLAdapter()

        with pytest.raises(ValueError, match="Invalid database identifier"):
            adapter.drop_database(database="test; DROP TABLE users")

    def test_validate_identifier_valid(self, postgres_stubs):
        adapter = PostgreSQLAdapter()

        assert adapter._validate_identifier("my_database") is True
        assert adapter._validate_identifier("TestDB") is True
        assert adapter._validate_identifier("_private") is True
        assert adapter._validate_identifier("db123") is True

    def test_validate_identifier_invalid(self, postgres_stubs):
        adapter = PostgreSQLAdapter()

        assert adapter._validate_identifier("") is False
        assert adapter._validate_identifier(None) is False
        assert adapter._validate_identifier("123abc") is False
        assert adapter._validate_identifier("db-name") is False
        assert adapter._validate_identifier("a" * 64) is False
        assert adapter._validate_identifier("db.schema") is False

    def test_create_connection_applies_settings(self, postgres_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.side_effect = [(1,), (1,)]
        mock_conn.cursor.return_value = mock_cursor
        postgres_stubs.connect.return_value = mock_conn

        adapter = PostgreSQLAdapter(
            database="testdb",
            work_mem="512MB",
            maintenance_work_mem="1GB",
        )

        with (
            patch.object(adapter, "handle_existing_database"),
            patch.object(adapter, "check_server_database_exists", return_value=True),
        ):
            connection = adapter.create_connection()

        assert connection is mock_conn

        executed = " ".join(str(call) for call in mock_cursor.execute.call_args_list)
        assert "work_mem" in executed
        assert "maintenance_work_mem" in executed

    def test_create_connection_creates_database(self, postgres_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.side_effect = [None, (1,)]
        mock_conn.cursor.return_value = mock_cursor
        postgres_stubs.connect.return_value = mock_conn

        adapter = PostgreSQLAdapter(database="newdb")

        with (
            patch.object(adapter, "handle_existing_database"),
            patch.object(adapter, "_create_database") as mock_create,
            patch.object(adapter, "check_server_database_exists", side_effect=[False, True]),
        ):
            adapter.create_connection()

        mock_create.assert_called_once()

    def test_get_platform_info(self, postgres_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.side_effect = [
            ("PostgreSQL 15.2 on x86_64",),
            None,
            ("100 MB",),
        ]
        mock_conn.cursor.return_value = mock_cursor

        adapter = PostgreSQLAdapter(
            host="pg.example.com",
            port=5432,
            database="testdb",
            schema="analytics",
            work_mem="256MB",
        )

        info = adapter.get_platform_info(connection=mock_conn)

        assert info["platform_type"] == "postgresql"
        assert info["platform_name"] == "PostgreSQL"
        assert info["host"] == "pg.example.com"
        assert info["port"] == 5432
        assert info["dialect"] == POSTGRES_DIALECT
        assert info["configuration"]["database"] == "testdb"
        assert info["configuration"]["schema"] == "analytics"
        assert info["configuration"]["work_mem"] == "256MB"

    def test_execute_query_success(self, postgres_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [(1, "test"), (2, "test2")]
        mock_conn.cursor.return_value = mock_cursor

        adapter = PostgreSQLAdapter()

        result = adapter.execute_query(mock_conn, "SELECT * FROM test", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 2
        assert result["first_row"] == (1, "test")
        assert isinstance(result["execution_time_seconds"], float)

    def test_execute_query_failure(self, postgres_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.side_effect = Exception("Query failed")
        mock_conn.cursor.return_value = mock_cursor

        adapter = PostgreSQLAdapter()

        result = adapter.execute_query(mock_conn, "INVALID SQL", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "FAILED"
        assert result["rows_returned"] == 0
        assert result["error"] == "Query failed"
        assert result["error_type"] == "Exception"

    def test_get_query_plan(self, postgres_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [
            ('[{"Plan": {"Node Type": "Seq Scan", "Filter": "(id > 5)"}}]',),
        ]
        mock_conn.cursor.return_value = mock_cursor

        adapter = PostgreSQLAdapter()

        plan = adapter.get_query_plan(mock_conn, "SELECT * FROM test WHERE id > 5")

        assert plan is not None
        assert "Seq Scan" in plan
        explain_sql = mock_cursor.execute.call_args[0][0]
        assert "FORMAT JSON" in explain_sql.upper()
        assert "FORMAT TEXT" not in explain_sql.upper()

    def test_configure_for_benchmark_olap(self, postgres_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter = PostgreSQLAdapter(tuning_enabled=True)

        adapter.configure_for_benchmark(mock_conn, "olap")

        executed = " ".join(str(call) for call in mock_cursor.execute.call_args_list)
        assert "enable_hashjoin" in executed
        assert "random_page_cost" in executed

    def test_configure_for_benchmark_oltp(self, postgres_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter = PostgreSQLAdapter(tuning_enabled=True)

        adapter.configure_for_benchmark(mock_conn, "oltp")

        executed = " ".join(str(call) for call in mock_cursor.execute.call_args_list)
        assert "synchronous_commit" in executed

    def test_analyze_table(self, postgres_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter = PostgreSQLAdapter(schema="public")

        adapter.analyze_table(mock_conn, "test_table")

        mock_cursor.execute.assert_called()
        executed = str(mock_cursor.execute.call_args)
        assert "ANALYZE" in executed

    def test_get_existing_tables(self, postgres_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [("table1",), ("TABLE2",)]
        mock_conn.cursor.return_value = mock_cursor

        adapter = PostgreSQLAdapter(schema="public")

        tables = adapter._get_existing_tables(mock_conn)

        assert tables == ["table1", "table2"]

    def test_test_connection_success(self, postgres_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor
        postgres_stubs.connect.return_value = mock_conn

        adapter = PostgreSQLAdapter()

        assert adapter.test_connection() is True

    def test_test_connection_failure(self, postgres_stubs):
        postgres_stubs.connect.side_effect = Exception("Connection refused")

        adapter = PostgreSQLAdapter()

        assert adapter.test_connection() is False

    def test_from_config_generates_database_name(self, postgres_stubs):
        config = {
            "host": "pg.example.com",
            "benchmark": "tpch",
            "scale_factor": 10.0,
        }

        adapter = PostgreSQLAdapter.from_config(config)

        assert "tpch" in adapter.database.lower()
        assert adapter.host == "pg.example.com"

    def test_from_config_uses_provided_database(self, postgres_stubs):
        config = {
            "host": "pg.example.com",
            "database": "my_custom_db",
            "benchmark": "tpch",
            "scale_factor": 10.0,
        }

        adapter = PostgreSQLAdapter.from_config(config)

        assert adapter.database == "my_custom_db"

    def test_supports_tuning_type(self, postgres_stubs):
        adapter = PostgreSQLAdapter()

        from benchbox.core.tuning.interface import TuningType

        assert adapter.supports_tuning_type(TuningType.PARTITIONING) is True
        assert adapter.supports_tuning_type(TuningType.SORTING) is False
        assert adapter.supports_tuning_type(TuningType.PRIMARY_KEYS) is True
        assert adapter.supports_tuning_type(TuningType.FOREIGN_KEYS) is True
        assert adapter.supports_tuning_type(TuningType.CLUSTERING) is True

    def test_close_connection(self, postgres_stubs):
        mock_conn = Mock()

        adapter = PostgreSQLAdapter()

        adapter.close_connection(mock_conn)

        mock_conn.close.assert_called_once()

    def test_dialect_is_postgres(self, postgres_stubs):
        adapter = PostgreSQLAdapter()

        assert adapter.get_target_dialect() == "postgres"
        assert adapter._dialect == "postgres"


class TestPostgreSQLDataLoading:
    @staticmethod
    def _install_copy_context(mock_cursor):
        copy_cm = MagicMock()
        copy_cm.__enter__.return_value = copy_cm
        copy_cm.__exit__.return_value = False
        mock_cursor.copy.return_value = copy_cm
        return copy_cm

    def test_load_data_with_csv(self, postgres_stubs, tmp_path):
        mock_conn = Mock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (3,)
        mock_conn.cursor.return_value = mock_cursor
        self._install_copy_context(mock_cursor)

        csv_file = tmp_path / "test_table.csv"
        csv_file.write_text("1,alice\n2,bob\n3,charlie\n")

        class Benchmark:
            tables = {"test_table": csv_file}

        adapter = PostgreSQLAdapter(schema="public")

        stats, load_time, _ = adapter.load_data(Benchmark(), mock_conn, tmp_path)

        assert stats["test_table"] == 3
        assert load_time >= 0

        assert mock_cursor.copy.called

    def test_load_data_with_tbl(self, postgres_stubs, tmp_path):
        mock_conn = Mock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (2,)
        mock_conn.cursor.return_value = mock_cursor
        self._install_copy_context(mock_cursor)

        tbl_file = tmp_path / "orders.tbl"
        tbl_file.write_text("1|alice|\n2|bob|\n")

        class Benchmark:
            tables = {"orders": tbl_file}

        adapter = PostgreSQLAdapter(schema="public")

        stats, load_time, _ = adapter.load_data(Benchmark(), mock_conn, tmp_path)

        assert stats["orders"] == 2
        assert mock_cursor.copy.called

    def test_load_data_preserves_dat_trailing_empty_fields(self, postgres_stubs, tmp_path):
        mock_conn = Mock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor
        copy_cm = self._install_copy_context(mock_cursor)

        dat_file = tmp_path / "catalog_page.dat"
        dat_file.write_text("160|AAAAAAAAAKAAAAAA|2450997|||||\n")

        class Benchmark:
            tables = {"catalog_page": dat_file}

        adapter = PostgreSQLAdapter(schema="public")

        stats, _, _ = adapter.load_data(Benchmark(), mock_conn, tmp_path)

        assert stats["catalog_page"] == 1
        assert any(call.args[0].endswith("|||||\n") for call in copy_cm.write.call_args_list)

    def test_load_data_streams_chunks_through_one_copy_session(self, postgres_stubs, tmp_path):
        mock_conn = Mock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (6,)
        mock_conn.cursor.return_value = mock_cursor
        copy_cm = self._install_copy_context(mock_cursor)

        chunk_a = tmp_path / "lineitem_0.csv"
        chunk_b = tmp_path / "lineitem_1.csv"
        chunk_a.write_text("1,alice\n2,bob\n3,charlie\n")
        chunk_b.write_text("4,dave\n5,eve\n6,frank\n")

        class Benchmark:
            tables = {"lineitem": [chunk_a, chunk_b]}

        adapter = PostgreSQLAdapter(schema="public")
        stats, _, _ = adapter.load_data(Benchmark(), mock_conn, tmp_path)

        assert stats["lineitem"] == 6
        assert mock_cursor.copy.call_count == 1
        written = "".join(call.args[0] for call in copy_cm.write.call_args_list)
        assert "1,alice" in written
        assert "6,frank" in written
        assert mock_conn.commit.call_count == 2

    def test_load_data_preserves_record_boundary_without_trailing_newline(self, postgres_stubs, tmp_path):
        mock_conn = Mock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (4,)
        mock_conn.cursor.return_value = mock_cursor
        copy_cm = self._install_copy_context(mock_cursor)

        chunk_a = tmp_path / "lineitem_0.csv"
        chunk_b = tmp_path / "lineitem_1.csv"
        chunk_a.write_text("1,alice\n2,bob")
        chunk_b.write_text("3,charlie\n4,dave\n")

        class Benchmark:
            tables = {"lineitem": [chunk_a, chunk_b]}

        adapter = PostgreSQLAdapter(schema="public")
        stats, _, _ = adapter.load_data(Benchmark(), mock_conn, tmp_path)

        assert stats["lineitem"] == 4
        assert mock_cursor.copy.call_count == 1
        written = "".join(call.args[0] for call in copy_cm.write.call_args_list)
        assert "2,bob\n3,charlie" in written
        assert "2,bob3,charlie" not in written

    def test_load_data_skips_repeat_headers_in_shared_session(self, postgres_stubs, tmp_path):
        mock_conn = Mock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (2,)
        mock_conn.cursor.return_value = mock_cursor
        copy_cm = self._install_copy_context(mock_cursor)

        chunk_a = tmp_path / "trips_0.csv"
        chunk_b = tmp_path / "trips_1.csv"
        chunk_a.write_text("id,name\n1,alice\n")
        chunk_b.write_text("id,name\n2,bob\n")

        fake_ds = DataSource(
            source_type="manifest_v2",
            tables={"trips": [chunk_a, chunk_b]},
            table_metadata={"trips": {"csv_delimiter": ",", "csv_has_header": True}},
        )

        adapter = PostgreSQLAdapter(schema="public")
        with patch("benchbox.platforms.postgresql.DataSourceResolver") as mock_resolver_cls:
            mock_resolver_cls.return_value.resolve.return_value = fake_ds
            stats, _, _ = adapter.load_data(Mock(), mock_conn, tmp_path)

        assert stats["trips"] == 2
        assert mock_cursor.copy.call_count == 1
        written = "".join(call.args[0] for call in copy_cm.write.call_args_list)
        assert written.count("id,name") == 1
        assert "1,alice" in written
        assert "2,bob" in written

    def test_load_data_splits_sessions_on_dialect_change(self, postgres_stubs, tmp_path):
        mock_conn = Mock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (2,)
        mock_conn.cursor.return_value = mock_cursor
        self._install_copy_context(mock_cursor)

        pipe_file = tmp_path / "events.tbl"
        csv_file = tmp_path / "events.csv"
        pipe_file.write_text("1|alice|\n")
        csv_file.write_text("2,bob\n")

        class Benchmark:
            tables = {"events": [pipe_file, csv_file]}

        adapter = PostgreSQLAdapter(schema="public")
        stats, _, _ = adapter.load_data(Benchmark(), mock_conn, tmp_path)

        assert stats["events"] == 2
        assert mock_cursor.copy.call_count == 2

    def test_load_data_skips_invalid_identifier(self, postgres_stubs, tmp_path):
        mock_conn = Mock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        self._install_copy_context(mock_cursor)

        csv_file = tmp_path / "test.csv"
        csv_file.write_text("1,test\n")

        class Benchmark:
            tables = {"invalid table; DROP TABLE": csv_file}

        adapter = PostgreSQLAdapter()

        stats, _, _ = adapter.load_data(Benchmark(), mock_conn, tmp_path)

        assert list(stats.values())[0] == 0
        assert not mock_cursor.copy.called

    def test_copy_sql_tbl_uses_format_text_with_null(self, postgres_stubs, tmp_path):
        mock_conn = Mock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor
        self._install_copy_context(mock_cursor)

        tbl_file = tmp_path / "lineitem.tbl"
        tbl_file.write_text("1|foo|bar|\n")

        fake_ds = DataSource(
            source_type="manifest_v2",
            tables={"lineitem": tbl_file},
            table_metadata={"lineitem": {"csv_delimiter": "|", "csv_null_marker": ""}},
        )

        adapter = PostgreSQLAdapter(schema="public")
        with patch("benchbox.platforms.postgresql.DataSourceResolver") as mock_resolver_cls:
            mock_resolver_cls.return_value.resolve.return_value = fake_ds
            adapter.load_data(Mock(), mock_conn, tmp_path)

        assert mock_cursor.copy.called, "cursor.copy() was not called"
        copy_sql = mock_cursor.copy.call_args.args[0]
        assert "FORMAT text" in copy_sql
        assert "NULL ''" in copy_sql
        assert "FORMAT csv" not in copy_sql

    def test_copy_sql_csv_with_header_uses_header_true(self, postgres_stubs, tmp_path):
        mock_conn = Mock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (2,)
        mock_conn.cursor.return_value = mock_cursor
        self._install_copy_context(mock_cursor)

        csv_file = tmp_path / "trips.csv"
        csv_file.write_text("time,lat,lon\n2026-01-01,1.0,2.0\n2026-01-02,3.0,4.0\n")

        fake_ds = DataSource(
            source_type="manifest_v2",
            tables={"trips": csv_file},
            table_metadata={"trips": {"csv_has_header": True, "csv_delimiter": ","}},
        )

        adapter = PostgreSQLAdapter(schema="public")
        with patch("benchbox.platforms.postgresql.DataSourceResolver") as mock_resolver_cls:
            mock_resolver_cls.return_value.resolve.return_value = fake_ds
            adapter.load_data(Mock(), mock_conn, tmp_path)

        assert mock_cursor.copy.called, "cursor.copy() was not called"
        copy_sql = mock_cursor.copy.call_args.args[0]
        assert "FORMAT csv" in copy_sql
        assert "HEADER true" in copy_sql

    def test_copy_sql_csv_with_empty_null_marker_stays_csv(self, postgres_stubs, tmp_path):
        mock_conn = Mock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor
        self._install_copy_context(mock_cursor)

        csv_file = tmp_path / "flights.csv"
        csv_file.write_text("flight_id,carrier_delay\n1,\n")

        fake_ds = DataSource(
            source_type="manifest_v2",
            tables={"flights": csv_file},
            table_metadata={"flights": {"csv_has_header": True, "csv_delimiter": ",", "csv_null_marker": ""}},
        )

        adapter = PostgreSQLAdapter(schema="public")
        with patch("benchbox.platforms.postgresql.DataSourceResolver") as mock_resolver_cls:
            mock_resolver_cls.return_value.resolve.return_value = fake_ds
            adapter.load_data(Mock(), mock_conn, tmp_path)

        assert mock_cursor.copy.called, "cursor.copy() was not called"
        copy_sql = mock_cursor.copy.call_args.args[0]
        assert "FORMAT csv" in copy_sql
        assert "HEADER true" in copy_sql
        assert "NULL ''" in copy_sql

    def test_copy_sql_csv_no_header_preserves_empty_strings(self, postgres_stubs, tmp_path):
        mock_conn = Mock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor
        self._install_copy_context(mock_cursor)

        csv_file = tmp_path / "hits.csv"
        csv_file.write_text("1,,bar\n")

        fake_ds = DataSource(
            source_type="manifest_v2",
            tables={"hits": csv_file},
            table_metadata={"hits": {"csv_has_header": False, "csv_delimiter": ",", "csv_null_marker": None}},
        )

        adapter = PostgreSQLAdapter(schema="public")
        with patch("benchbox.platforms.postgresql.DataSourceResolver") as mock_resolver_cls:
            mock_resolver_cls.return_value.resolve.return_value = fake_ds
            adapter.load_data(Mock(), mock_conn, tmp_path)

        assert mock_cursor.copy.called, "cursor.copy() was not called"
        copy_sql = mock_cursor.copy.call_args.args[0]
        assert "FORMAT csv" in copy_sql
        assert "HEADER" not in copy_sql
        assert "NULL '__BENCHBOX_NO_NULL__'" in copy_sql

    def test_copy_sql_quoted_dialect_uses_csv_with_null_marker(self, postgres_stubs, tmp_path):
        mock_conn = Mock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor
        self._install_copy_context(mock_cursor)

        dat_file = tmp_path / "hits.dat"
        dat_file.write_text('1|""|bar\n2|__NULL__|baz\n')

        fake_ds = DataSource(
            source_type="manifest_v2",
            tables={"hits": dat_file},
            table_metadata={
                "hits": {
                    "csv_has_header": False,
                    "csv_delimiter": "|",
                    "csv_null_marker": "__NULL__",
                    "csv_quote": '"',
                }
            },
        )

        adapter = PostgreSQLAdapter(schema="public")
        with patch("benchbox.platforms.postgresql.DataSourceResolver") as mock_resolver_cls:
            mock_resolver_cls.return_value.resolve.return_value = fake_ds
            adapter.load_data(Mock(), mock_conn, tmp_path)

        assert mock_cursor.copy.called, "cursor.copy() was not called"
        copy_sql = mock_cursor.copy.call_args.args[0]
        assert "FORMAT csv" in copy_sql
        assert "FORMAT text" not in copy_sql
        assert "HEADER" not in copy_sql
        assert "NULL '__NULL__'" in copy_sql

    def test_load_data_converts_parquet_to_csv_copy(self, postgres_stubs, tmp_path):
        mock_conn = Mock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (2,)
        mock_conn.cursor.return_value = mock_cursor
        copy_cm = self._install_copy_context(mock_cursor)

        parquet_file = tmp_path / "title.parquet"
        table = pa.table({"id": [1, 2], "name": ["", None]})
        pq.write_table(table, parquet_file)

        class Benchmark:
            tables = {"title": parquet_file}

        adapter = PostgreSQLAdapter(schema="public")
        stats, _, _ = adapter.load_data(Benchmark(), mock_conn, tmp_path)

        assert stats["title"] == 2
        copy_sql = mock_cursor.copy.call_args.args[0]
        assert "FORMAT csv" in copy_sql
        assert "HEADER true" in copy_sql
        assert "NULL ''" in copy_sql
        written = b"".join(call.args[0] for call in copy_cm.write.call_args_list).decode()
        assert '"id","name"' in written
        assert '"1",""' in written


class TestPostgreSQLCreateDatabase:
    def test_create_database_calls_create_if_not_exists(self, postgres_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = None
        mock_conn.cursor.return_value = mock_cursor
        postgres_stubs.connect.return_value = mock_conn

        adapter = PostgreSQLAdapter(database="newdb")
        adapter._create_database()

        executed = " ".join(str(c) for c in mock_cursor.execute.call_args_list)
        assert "CREATE DATABASE" in executed
        assert "newdb" in executed

    def test_create_database_skips_if_already_exists(self, postgres_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor
        postgres_stubs.connect.return_value = mock_conn

        adapter = PostgreSQLAdapter(database="existingdb")
        adapter._create_database()

        executed = " ".join(str(c) for c in mock_cursor.execute.call_args_list)
        assert "CREATE DATABASE" not in executed

    def test_create_database_rejects_invalid_identifier(self, postgres_stubs):
        adapter = PostgreSQLAdapter(database="valid_db")
        adapter.database = "invalid; DROP TABLE users"

        with pytest.raises(ValueError, match="Invalid database identifier"):
            adapter._create_database()


class TestPostgreSQLCreateSchema:
    def test_create_schema_executes_statements(self, postgres_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        class MockBenchmark:
            def get_schema_sql(self):
                return "CREATE TABLE foo (id INT); CREATE TABLE bar (id INT)"

        adapter = PostgreSQLAdapter()

        with patch.object(
            adapter, "_create_schema_with_tuning", return_value="CREATE TABLE foo (id INT); CREATE TABLE bar (id INT)"
        ):
            duration = adapter.create_schema(MockBenchmark(), mock_conn)

        assert isinstance(duration, float)
        assert duration >= 0
        mock_conn.commit.assert_called()

    def test_create_schema_continues_on_statement_failure(self, postgres_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.execute.side_effect = [Exception("syntax error"), None]
        mock_conn.cursor.return_value = mock_cursor

        class MockBenchmark:
            pass

        adapter = PostgreSQLAdapter()

        fk_stmt = "CREATE TABLE foo (id INT, FOREIGN KEY (id) REFERENCES bar(id))"
        with patch.object(adapter, "_create_schema_with_tuning", return_value=fk_stmt):
            duration = adapter.create_schema(MockBenchmark(), mock_conn)

        assert isinstance(duration, float)
        mock_conn.commit.assert_called()

    def test_create_schema_creates_vector_extension_before_vector_tables(self, postgres_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = None
        mock_conn.cursor.return_value = mock_cursor
        adapter = PostgreSQLAdapter()
        schema_sql = "CREATE TABLE vectors (id BIGINT, embedding VECTOR(128))"

        with patch.object(adapter, "_create_schema_with_tuning", return_value=schema_sql):
            with pytest.raises(RuntimeError, match="vector extension is not available"):
                adapter.create_schema(Mock(), mock_conn)

        executed = [call.args[0] for call in mock_cursor.execute.call_args_list]
        assert "CREATE EXTENSION IF NOT EXISTS vector" in executed
        assert not any(sql.startswith("CREATE TABLE vectors") for sql in executed)

    def test_create_schema_skips_vector_extension_for_plain_tables(self, postgres_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        adapter = PostgreSQLAdapter()

        with patch.object(adapter, "_create_schema_with_tuning", return_value="CREATE TABLE foo (id INT)"):
            adapter.create_schema(Mock(), mock_conn)

        executed = [call.args[0] for call in mock_cursor.execute.call_args_list]
        assert not any("EXTENSION" in sql for sql in executed)


class TestPostgreSQLValidatePlatformCapabilities:
    def test_valid_capabilities_with_psycopg(self, postgres_stubs):
        postgres_stubs.__version__ = "3.1.0"

        adapter = PostgreSQLAdapter(work_mem="256MB")
        result = adapter.validate_platform_capabilities("tpch")

        assert result is not None
        assert result.is_valid is True
        assert result.details["psycopg_available"] is True
        assert result.details["benchmark_type"] == "tpch"

    def test_warns_on_low_work_mem(self, postgres_stubs):
        adapter = PostgreSQLAdapter(work_mem="32MB")
        result = adapter.validate_platform_capabilities("tpch")

        assert result is not None
        warning_messages = " ".join(result.warnings)
        assert "work_mem" in warning_messages

    def test_warns_on_gb_work_mem_not_low(self, postgres_stubs):
        adapter = PostgreSQLAdapter(work_mem="1GB")
        result = adapter.validate_platform_capabilities("tpch")

        assert result is not None
        work_mem_warnings = [w for w in result.warnings if "work_mem" in w.lower()]
        assert len(work_mem_warnings) == 0


class TestPostgreSQLValidateConnectionHealth:
    def test_healthy_connection(self, postgres_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.side_effect = [
            (1,),
            ("PostgreSQL 15.2 on x86_64-pc-linux-gnu",),
            ("256MB",),
            None,
        ]
        mock_conn.cursor.return_value = mock_cursor

        adapter = PostgreSQLAdapter()
        result = adapter.validate_connection_health(mock_conn)

        assert result is not None
        assert result.is_valid is True
        assert result.details["basic_query_test"] == "passed"
        assert "PostgreSQL" in result.details["server_version"]

    def test_warns_on_old_postgres_version(self, postgres_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.side_effect = [
            (1,),
            ("PostgreSQL 11.19 on x86_64",),
            ("256MB",),
            None,
        ]
        mock_conn.cursor.return_value = mock_cursor

        adapter = PostgreSQLAdapter()
        result = adapter.validate_connection_health(mock_conn)

        assert result is not None
        warning_messages = " ".join(result.warnings)
        assert "11" in warning_messages or "older" in warning_messages

    def test_failed_connection_returns_invalid_result(self, postgres_stubs):
        mock_conn = Mock()
        mock_conn.cursor.side_effect = Exception("Connection lost")

        adapter = PostgreSQLAdapter()
        result = adapter.validate_connection_health(mock_conn)

        assert result is not None
        assert result.is_valid is False
        assert len(result.errors) > 0


class TestBuildPostgreSQLConfig:
    def test_default_values(self, postgres_stubs):
        from benchbox.platforms.postgresql import _build_postgresql_config

        with patch("benchbox.security.credentials.CredentialManager") as mock_cm_cls:
            mock_cm = MagicMock()
            mock_cm.get_platform_credentials.return_value = {}
            mock_cm_cls.return_value = mock_cm

            result = _build_postgresql_config("postgresql", {}, {}, None)

        assert result.host == "localhost"
        assert result.port == 5432
        assert result.username == "postgres"
        assert result.sslmode == "prefer"
        assert result.options.get("work_mem") == "256MB" or result.options.get("work_mem") is None

    def test_platform_options_override_defaults(self, postgres_stubs):
        from benchbox.platforms.postgresql import _build_postgresql_config

        explicit = {"host": "pg.example.com", "port": 5433, "work_mem": "1GB"}
        with patch("benchbox.security.credentials.CredentialManager") as mock_cm_cls:
            mock_cm = MagicMock()
            mock_cm.get_platform_credentials.return_value = {"host": "saved-host", "username": "saved_user"}
            mock_cm_cls.return_value = mock_cm

            result = _build_postgresql_config(
                "postgresql",
                explicit,
                {"_explicit_platform_options": explicit},
                None,
            )

        assert result.host == "pg.example.com"
        assert result.port == 5433

    def test_benchmark_config_merged(self, postgres_stubs):
        from benchbox.platforms.postgresql import _build_postgresql_config

        result = _build_postgresql_config("postgresql", {}, {"scale_factor": 10.0, "benchmark": "tpch"}, None)

        assert result.scale_factor == 10.0
        assert result.benchmark == "tpch"


class TestPostgreSQLStatementTimeoutOption:
    def test_platform_option_parses_to_integer_milliseconds(self):
        from benchbox.core.hooks.platform_hooks import PlatformHookRegistry

        parsed = PlatformHookRegistry.parse_options("postgresql", [("statement_timeout", "300000")])

        assert parsed["statement_timeout"] == 300000

    def test_statement_timeout_unset_by_default(self):
        from benchbox.core.hooks.platform_hooks import PlatformHookRegistry

        assert PlatformHookRegistry.get_default_options("postgresql")["statement_timeout"] is None
        assert "statement_timeout" not in PlatformHookRegistry.parse_options("postgresql", [])

    def test_option_reaches_connection_params_through_config_builder(self, postgres_stubs):
        from benchbox.platforms.postgresql import _build_postgresql_config

        explicit = {"statement_timeout": 300000}
        with patch("benchbox.security.credentials.CredentialManager") as mock_cm_cls:
            mock_cm_cls.return_value.get_platform_credentials.return_value = {}
            config = _build_postgresql_config("postgresql", explicit, {"_explicit_platform_options": explicit}, None)

        adapter = PostgreSQLAdapter.from_config(config.model_dump())

        assert adapter.statement_timeout == 300000
        assert adapter._get_connection_params()["options"] == "-c statement_timeout=300000"

    def test_connection_options_empty_without_statement_timeout(self, postgres_stubs):
        from benchbox.platforms.postgresql import _build_postgresql_config

        with patch("benchbox.security.credentials.CredentialManager") as mock_cm_cls:
            mock_cm_cls.return_value.get_platform_credentials.return_value = {}
            config = _build_postgresql_config("postgresql", {}, {}, None)

        adapter = PostgreSQLAdapter.from_config(config.model_dump())

        assert "options" not in adapter._get_connection_params()
