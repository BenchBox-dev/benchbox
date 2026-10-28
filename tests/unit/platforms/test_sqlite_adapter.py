# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from pathlib import Path
from unittest.mock import ANY, Mock, patch

import pytest

from benchbox.cli.tuning_runtime import build_baseline_unified_config
from benchbox.core.tuning.applied_ledger import AppliedTuningLedger
from benchbox.core.tuning.interface import UnifiedTuningConfiguration
from benchbox.metadata_primitives import MetadataPrimitives
from benchbox.platforms.base.result_capture import ResultCaptureMixin
from benchbox.platforms.sqlite import SQLiteAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestSQLiteAdapter:
    def test_initialization_success(self):
        adapter = SQLiteAdapter(database_path=":memory:", timeout=30.0, check_same_thread=False)
        assert adapter.platform_name == "SQLite"
        assert adapter.get_target_dialect() == "sqlite"
        assert adapter.database_path == ":memory:"
        assert adapter.timeout == 30.0
        assert adapter.check_same_thread is False

    def test_schema_only_benchmark_skips_data_loading(self, tmp_path):
        adapter = SQLiteAdapter(database_path=":memory:")
        adapter.create_schema = Mock(return_value=0.1)
        adapter.load_data = Mock(side_effect=AssertionError("data loading should be skipped"))
        benchmark = MetadataPrimitives(output_dir=tmp_path)

        result = adapter._setup_fresh_database_phases(benchmark, adapter.create_connection(), None)

        adapter.create_schema.assert_called_once()
        adapter.load_data.assert_not_called()
        assert result[2] == 0.0
        assert result[3] == {}
        assert result[4].status == "SKIPPED"
        assert result[4].tables_loaded == 0

    def test_benchmark_with_own_data_still_loads(self, tmp_path):
        class OwnDataBenchmark:
            output_dir = tmp_path

            def get_data_source_benchmark(self):
                return None

        adapter = SQLiteAdapter(database_path=":memory:")
        adapter.create_schema = Mock(return_value=0.1)
        adapter.load_data = Mock(return_value=({"t": 1}, 0.2, {}))

        result = adapter._setup_fresh_database_phases(OwnDataBenchmark(), adapter.create_connection(), None)

        adapter.load_data.assert_called_once()
        assert result[2] == 0.2
        assert result[3] == {"t": 1}

    def test_tuned_schema_executescript_captures_constraint_ddl_only(self, tmp_path):
        config = UnifiedTuningConfiguration()
        adapter = SQLiteAdapter(
            database_path=":memory:",
            tuning_enabled=True,
            unified_tuning_configuration=config,
        )
        adapter._applied_tuning_ledger = AppliedTuningLedger()
        connection = adapter.create_connection()

        class _SchemaOnlyBenchmark:
            SKIP_DATA_LOADING = True

        benchmark = _SchemaOnlyBenchmark()
        benchmark.output_dir = tmp_path
        benchmark.get_create_tables_sql = Mock(
            return_value=("CREATE TABLE baseline (id INTEGER);\nCREATE TABLE tuned (id INTEGER PRIMARY KEY);\n")
        )
        adapter.apply_unified_tuning = Mock()
        adapter.save_tuning_metadata = Mock(return_value=True)
        adapter.load_data = Mock(return_value=({}, 0.0, None))

        adapter._setup_fresh_database_phases(benchmark, connection, config)

        assert [statement.statement for statement in adapter._applied_tuning_ledger.executed_statements] == [
            'CREATE TABLE "tuned" ("id" INTEGER PRIMARY KEY);'
        ]
        assert adapter._applied_tuning_ledger.applied_ledger_hash() is not None
        assert (
            adapter._applied_tuning_ledger.overall_status(tuning_enabled=True, has_config=True) == "applied_unverified"
        )
        assert connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall() == [
            ("baseline",),
            ("benchbox_tuning_metadata",),
            ("tuned",),
        ]

    def test_fresh_setup_is_adapter_owned_not_mixin_owned(self):
        assert not hasattr(ResultCaptureMixin, "_setup_fresh_database_phases")
        assert hasattr(SQLiteAdapter, "_setup_fresh_database_phases")

    def test_initialization_with_defaults(self):
        adapter = SQLiteAdapter()
        assert adapter.platform_name == "SQLite"
        assert adapter.database_path == ":memory:"
        assert adapter.timeout == 30.0
        assert adapter.check_same_thread is False

    def test_initialization_missing_driver(self):
        with patch("benchbox.platforms.sqlite.sqlite3", None):
            with pytest.raises(ImportError, match="SQLite not available"):
                SQLiteAdapter()

    def test_get_database_path(self):
        adapter = SQLiteAdapter(database_path="/tmp/test.db")

        path = adapter.get_database_path(database_path="/tmp/override.db")
        assert path == "/tmp/override.db"

        path = adapter.get_database_path()
        assert path == "/tmp/test.db"

        adapter = SQLiteAdapter()
        path = adapter.get_database_path()
        assert path == ":memory:"

    @patch("benchbox.platforms.sqlite.sqlite3")
    def test_create_connection_memory_database(self, mock_sqlite3):
        mock_connection = Mock()
        mock_sqlite3.connect.return_value = mock_connection

        adapter = SQLiteAdapter(database_path=":memory:")
        connection = adapter.create_connection()

        assert connection == mock_connection
        mock_sqlite3.connect.assert_called_once_with(
            ":memory:", timeout=30.0, check_same_thread=False, cached_statements=0, factory=ANY
        )

        expected_pragmas = [
            "PRAGMA foreign_keys = ON",
            "PRAGMA journal_mode = WAL",
            "PRAGMA synchronous = NORMAL",
            "PRAGMA cache_size = 10000",
            "PRAGMA temp_store = MEMORY",
        ]

        for pragma in expected_pragmas:
            mock_connection.execute.assert_any_call(pragma)

    @patch("benchbox.platforms.sqlite.sqlite3")
    def test_create_connection_file_database(self, mock_sqlite3):
        mock_connection = Mock()
        mock_sqlite3.connect.return_value = mock_connection

        adapter = SQLiteAdapter(database_path="/tmp/test.db")
        connection = adapter.create_connection()

        assert connection == mock_connection
        mock_sqlite3.connect.assert_called_once_with(
            "/tmp/test.db", timeout=30.0, check_same_thread=False, cached_statements=0, factory=ANY
        )

    @patch("benchbox.platforms.sqlite.sqlite3")
    def test_create_connection_with_overrides(self, mock_sqlite3):
        mock_connection = Mock()
        mock_sqlite3.connect.return_value = mock_connection

        adapter = SQLiteAdapter()
        connection = adapter.create_connection(database_path="/tmp/override.db", timeout=60.0)

        assert connection == mock_connection
        mock_sqlite3.connect.assert_called_once_with(
            "/tmp/override.db",
            timeout=30.0,
            check_same_thread=False,
            cached_statements=0,
            factory=ANY,
        )

    @patch("benchbox.platforms.sqlite.sqlite3")
    def test_create_connection_failure(self, mock_sqlite3):
        mock_sqlite3.connect.side_effect = Exception("Connection failed")

        adapter = SQLiteAdapter()

        with pytest.raises(Exception, match="Connection failed"):
            adapter.create_connection()

    @patch("benchbox.platforms.sqlite.sqlite3")
    def test_create_schema_with_constraints(self, mock_sqlite3):
        mock_connection = Mock()
        mock_sqlite3.connect.return_value = mock_connection

        mock_benchmark = Mock()
        mock_benchmark.get_create_tables_sql.return_value = """
            CREATE TABLE table1 (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE table2 (id INTEGER, fk_id INTEGER, FOREIGN KEY(fk_id) REFERENCES table1(id));
        """

        mock_sig = Mock()
        mock_sig.parameters = {
            "enable_primary_keys": Mock(),
            "enable_foreign_keys": Mock(),
        }

        adapter = SQLiteAdapter()
        connection = adapter.create_connection()

        mock_config = Mock()
        mock_config.primary_keys.enabled = True
        mock_config.foreign_keys.enabled = True

        with (
            patch.object(adapter, "get_effective_tuning_configuration", return_value=mock_config),
            patch("inspect.signature", return_value=mock_sig),
        ):
            schema_time = adapter.create_schema(mock_benchmark, connection)

        assert isinstance(schema_time, float)
        assert schema_time >= 0

        mock_benchmark.get_create_tables_sql.assert_called_once_with(dialect="sqlite", tuning_config=mock_config)

        mock_connection.executescript.assert_called_once()
        mock_connection.commit.assert_called_once()

    @patch("benchbox.platforms.sqlite.sqlite3")
    def test_create_schema_without_constraints(self, mock_sqlite3):
        mock_connection = Mock()
        mock_sqlite3.connect.return_value = mock_connection

        mock_benchmark = Mock()
        mock_benchmark.get_create_tables_sql.return_value = "CREATE TABLE table1 (id INTEGER, name TEXT);"

        mock_sig = Mock()
        mock_sig.parameters = {
            "enable_primary_keys": Mock(),
            "enable_foreign_keys": Mock(),
        }

        adapter = SQLiteAdapter()
        connection = adapter.create_connection()

        mock_config = Mock()
        mock_config.primary_keys.enabled = False
        mock_config.foreign_keys.enabled = False

        with (
            patch.object(adapter, "get_effective_tuning_configuration", return_value=mock_config),
            patch("inspect.signature", return_value=mock_sig),
        ):
            schema_time = adapter.create_schema(mock_benchmark, connection)

        assert isinstance(schema_time, float)
        assert schema_time >= 0

        mock_benchmark.get_create_tables_sql.assert_called_once_with(dialect="sqlite", tuning_config=mock_config)

    @patch("benchbox.platforms.sqlite.sqlite3")
    def test_configure_for_benchmark_olap(self, mock_sqlite3):
        mock_connection = Mock()
        mock_sqlite3.connect.return_value = mock_connection

        adapter = SQLiteAdapter()
        connection = adapter.create_connection()

        adapter.configure_for_benchmark(connection, "olap")

        mock_connection.execute.assert_any_call("PRAGMA query_only = false")
        mock_connection.execute.assert_any_call("PRAGMA read_uncommitted = true")

    @patch("benchbox.platforms.sqlite.sqlite3")
    def test_configure_for_benchmark_oltp(self, mock_sqlite3):
        mock_connection = Mock()
        mock_sqlite3.connect.return_value = mock_connection

        adapter = SQLiteAdapter()
        connection = adapter.create_connection()

        adapter.configure_for_benchmark(connection, "oltp")

        mock_connection.execute.assert_any_call("PRAGMA synchronous = FULL")

    def test_apply_joinorder_helper_indexes(self):
        from benchbox.platforms import sqlite as sqlite_platform

        adapter = SQLiteAdapter()
        connection = Mock()
        cursor = Mock()
        connection.cursor.return_value = cursor
        cursor.fetchall.return_value = [(table,) for table in sqlite_platform._JOINORDER_TABLES]
        benchmark = Mock(benchmark_id="joinorder")

        duration = adapter._apply_benchmark_helper_indexes(benchmark, connection)

        assert duration >= 0
        connection.execute.assert_any_call("ANALYZE")
        assert connection.execute.call_count == len(sqlite_platform._JOINORDER_HELPER_INDEXES) + 1
        connection.commit.assert_called_once()

    def test_tpch_helper_indexes_cover_stage_one_query_access_paths(self):
        from benchbox.platforms import sqlite as sqlite_platform

        indexes = "\n".join(sqlite_platform._TPCH_HELPER_INDEXES)

        assert "ON customer (c_nationkey, c_custkey)" in indexes
        assert "ON orders (o_orderdate, o_orderkey, o_custkey)" in indexes
        assert "ON orders (o_custkey, o_orderdate, o_orderkey)" in indexes
        assert "ON lineitem (l_partkey, l_suppkey, l_shipdate, l_quantity)" in indexes
        assert "ON partsupp (ps_partkey, ps_suppkey, ps_supplycost)" in indexes

    def test_create_connection_registers_sqlite_compatibility_functions(self):
        adapter = SQLiteAdapter(database_path=":memory:")
        connection = adapter.create_connection()
        try:
            connection.execute("CREATE TABLE values_table (value REAL)")
            connection.executemany("INSERT INTO values_table VALUES (?)", [(1.0,), (2.0,), (3.0,), (4.0,)])
            connection.commit()

            assert connection.execute("SELECT STDDEV(value) FROM values_table").fetchone()[0] == pytest.approx(1.290994)
            assert (
                connection.execute(
                    "SELECT REGEXP_REPLACE('https://www.example.com/path', '^https?://(?:www\\.)?([^/]+)/.*$', '\\1')"
                ).fetchone()[0]
                == "example.com"
            )
            assert connection.execute("SELECT PERCENTILE_CONT(0.5, value) FROM values_table").fetchone()[
                0
            ] == pytest.approx(2.5)
        finally:
            connection.close()

    @patch("benchbox.platforms.sqlite.sqlite3")
    def test_execute_query_success(self, mock_sqlite3):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [(1, "test"), (2, "test2")]
        mock_sqlite3.connect.return_value = mock_connection

        adapter = SQLiteAdapter()
        connection = adapter.create_connection()

        result = adapter.execute_query(connection, "SELECT * FROM test", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 2
        assert result["results"] == [(1, "test"), (2, "test2")]
        assert isinstance(result["execution_time_seconds"], float)

        mock_cursor.execute.assert_called_once_with("SELECT * FROM test")
        mock_cursor.fetchall.assert_called_once()

    @patch("benchbox.platforms.sqlite.sqlite3")
    def test_execute_query_failure(self, mock_sqlite3):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("SQL syntax error")
        mock_sqlite3.connect.return_value = mock_connection

        adapter = SQLiteAdapter()
        connection = adapter.create_connection()

        result = adapter.execute_query(connection, "INVALID SQL", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "FAILED"
        assert result["rows_returned"] == 0
        assert result["error"] == "SQL syntax error"
        assert isinstance(result["execution_time_seconds"], float)

    def test_execute_query_cursor_acquisition_failure_returns_failed(self):
        adapter = SQLiteAdapter()
        connection = Mock()
        connection.cursor.side_effect = Exception("Cannot operate on a closed database.")

        result = adapter.execute_query(connection, "SELECT 1", "q1")

        assert result["status"] == "FAILED"
        assert result["query_id"] == "q1"
        assert result["rows_returned"] == 0
        assert "closed database" in result["error"]

    def test_apply_table_tunings(self):
        adapter = SQLiteAdapter()
        mock_connection = Mock()
        mock_table_tuning = Mock()

        adapter.apply_table_tunings(mock_table_tuning, mock_connection)

    def test_generate_tuning_clause(self):
        adapter = SQLiteAdapter()
        mock_table_tuning = Mock()

        clause = adapter.generate_tuning_clause(mock_table_tuning)
        assert clause == ""

    def test_apply_unified_tuning(self):
        adapter = SQLiteAdapter()
        mock_connection = Mock()
        mock_unified_config = Mock()

        adapter.apply_unified_tuning(mock_unified_config, mock_connection)

    def test_apply_platform_optimizations(self):
        adapter = SQLiteAdapter()
        mock_connection = Mock()
        mock_platform_config = Mock()

        adapter.apply_platform_optimizations(mock_platform_config, mock_connection)

    @patch("benchbox.platforms.sqlite.sqlite3")
    def test_apply_constraint_configuration_enable_foreign_keys(self, mock_sqlite3):
        mock_connection = Mock()
        mock_sqlite3.connect.return_value = mock_connection

        adapter = SQLiteAdapter()
        connection = adapter.create_connection()

        mock_foreign_key_config = Mock()
        mock_foreign_key_config.enabled = True

        mock_primary_key_config = Mock()

        adapter.apply_constraint_configuration(mock_primary_key_config, mock_foreign_key_config, connection)

        mock_connection.execute.assert_any_call("PRAGMA foreign_keys = ON")

    @patch("benchbox.platforms.sqlite.sqlite3")
    def test_apply_constraint_configuration_disable_foreign_keys(self, mock_sqlite3):
        mock_connection = Mock()
        mock_sqlite3.connect.return_value = mock_connection

        adapter = SQLiteAdapter()
        connection = adapter.create_connection()

        mock_foreign_key_config = Mock()
        mock_foreign_key_config.enabled = False

        mock_primary_key_config = Mock()

        adapter.apply_constraint_configuration(mock_primary_key_config, mock_foreign_key_config, connection)

        mock_connection.execute.assert_any_call("PRAGMA foreign_keys = OFF")

    def test_run_power_test_not_implemented(self):
        adapter = SQLiteAdapter()
        mock_benchmark = Mock()

        with pytest.raises(NotImplementedError, match="Power test not implemented for SQLite adapter"):
            adapter.run_power_test(mock_benchmark)

    def test_run_throughput_test_not_implemented(self):
        adapter = SQLiteAdapter()
        mock_benchmark = Mock()

        with pytest.raises(
            NotImplementedError,
            match="Throughput test not implemented for SQLite adapter",
        ):
            adapter.run_throughput_test(mock_benchmark)

    def test_run_maintenance_test_not_implemented(self):
        adapter = SQLiteAdapter()
        mock_benchmark = Mock()

        with pytest.raises(
            NotImplementedError,
            match="Maintenance test not implemented for SQLite adapter",
        ):
            adapter.run_maintenance_test(mock_benchmark)

    @patch("benchbox.platforms.sqlite.sqlite3")
    def test_handle_existing_database_memory(self, mock_sqlite3):
        mock_connection = Mock()
        mock_sqlite3.connect.return_value = mock_connection

        adapter = SQLiteAdapter(database_path=":memory:")

        connection = adapter.create_connection()
        assert connection == mock_connection

    @patch("benchbox.platforms.sqlite.sqlite3")
    def test_configuration_validation(self, mock_sqlite3):
        mock_connection = Mock()
        mock_sqlite3.connect.return_value = mock_connection

        adapter = SQLiteAdapter(database_path="/tmp/test.db", timeout=60.0, check_same_thread=True)

        assert adapter.database_path == "/tmp/test.db"
        assert adapter.timeout == 60.0
        assert adapter.check_same_thread is True

        connection = adapter.create_connection()
        assert connection == mock_connection

    @patch("benchbox.platforms.sqlite.sqlite3")
    def test_connection_optimization_pragmas(self, mock_sqlite3):
        mock_connection = Mock()
        mock_sqlite3.connect.return_value = mock_connection

        adapter = SQLiteAdapter()
        adapter.create_connection()

        expected_pragmas = [
            "PRAGMA foreign_keys = ON",
            "PRAGMA journal_mode = WAL",
            "PRAGMA synchronous = NORMAL",
            "PRAGMA cache_size = 10000",
            "PRAGMA temp_store = MEMORY",
        ]

        for pragma in expected_pragmas:
            mock_connection.execute.assert_any_call(pragma)

        assert mock_connection.execute.call_count >= len(expected_pragmas)

    def test_from_config_with_connection_string(self):
        config = {
            "connection_string": "/tmp/test_from_config.db",
            "benchmark": "tpch",
            "scale_factor": 0.01,
        }

        adapter = SQLiteAdapter.from_config(config)

        assert adapter.database_path == "/tmp/test_from_config.db"
        assert adapter.timeout == 30.0
        assert adapter.check_same_thread is False

    def test_from_config_with_database_path(self):
        config = {
            "database_path": "/tmp/explicit_path.db",
            "connection_string": "/tmp/ignored.db",
            "benchmark": "tpch",
            "scale_factor": 0.01,
        }

        adapter = SQLiteAdapter.from_config(config)

        assert adapter.database_path == "/tmp/explicit_path.db"

    def test_from_config_with_auto_generation(self):
        config = {
            "benchmark": "tpch",
            "scale_factor": 0.01,
        }

        adapter = SQLiteAdapter.from_config(config)

        assert adapter.database_path is not None
        assert "tpch" in adapter.database_path.lower()
        assert ".sqlite" in adapter.database_path

    def test_from_config_with_output_dir(self, tmp_path):
        custom_output = tmp_path / "custom_output"
        config = {
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "output_dir": str(custom_output),
        }

        adapter = SQLiteAdapter.from_config(config)

        assert str(custom_output) in adapter.database_path
        assert "tpch" in adapter.database_path.lower()

    def test_from_config_with_database_name(self, tmp_path):
        config = {
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "output_dir": str(tmp_path),
            "database_name": "named_run",
        }

        adapter = SQLiteAdapter.from_config(config)

        assert adapter.database_path == str(tmp_path / "databases" / "tpch_sf001" / "named_run.db")

    def test_cli_database_name_option_reaches_adapter(self, tmp_path, monkeypatch):
        from benchbox.core.platform_config import get_platform_config
        from benchbox.core.schemas import DatabaseConfig

        monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(tmp_path))
        named = {"database_name": "named_run"}
        database_config = DatabaseConfig(
            type="sqlite", name="SQLite", options={**named, "_explicit_platform_options": named}
        )

        platform_config = get_platform_config(database_config, None, benchmark_name="tpch", scale_factor=0.01)
        adapter = SQLiteAdapter.from_config(platform_config)

        assert adapter.database_path == str(tmp_path / "databases" / "tpch_sf001" / "named_run.db")

    def test_from_config_raises_on_missing_path(self):
        from benchbox.core.exceptions import ConfigurationError

        config = {}

        with pytest.raises(ConfigurationError) as excinfo:
            SQLiteAdapter.from_config(config)

        assert "database_path" in str(excinfo.value)
        assert "--benchmark" in str(excinfo.value)

    def test_from_config_with_optional_params(self):
        config = {
            "connection_string": "/tmp/test.db",
            "timeout": 60.0,
            "check_same_thread": True,
        }

        adapter = SQLiteAdapter.from_config(config)

        assert adapter.database_path == "/tmp/test.db"
        assert adapter.timeout == 60.0
        assert adapter.check_same_thread is True

    def test_from_config_rejects_nested_connection_dict(self):
        from benchbox.core.exceptions import ConfigurationError

        config = {
            "connection": {
                "database_path": "/tmp/legacy_path.db",
            },
        }

        with pytest.raises(ConfigurationError):
            SQLiteAdapter.from_config(config)

    def test_get_database_path_with_none_override(self):
        adapter = SQLiteAdapter(database_path="/tmp/instance_path.db")

        path = adapter.get_database_path(database_path=None)
        assert path == "/tmp/instance_path.db"

    def test_get_database_path_with_explicit_override(self):
        adapter = SQLiteAdapter(database_path="/tmp/instance_path.db")

        path = adapter.get_database_path(database_path="/tmp/override.db")
        assert path == "/tmp/override.db"

    def test_execute_query_with_connection_and_cursor(self):
        adapter = SQLiteAdapter(database_path=":memory:")
        conn = adapter.create_connection()
        try:
            res_conn = adapter.execute_query(conn, "SELECT 42 as val", "q_conn")
            assert res_conn["status"] == "SUCCESS"
            assert res_conn["rows_returned"] == 1
            assert res_conn["results"] == [(42,)]

            cursor = conn.cursor()
            res_cursor = adapter.execute_query(cursor, "SELECT 84 as val", "q_cursor")
            assert res_cursor["status"] == "SUCCESS"
            assert res_cursor["rows_returned"] == 1
            assert res_cursor["results"] == [(84,)]
        finally:
            conn.close()

    def test_get_query_plan_with_connection_and_cursor(self):
        adapter = SQLiteAdapter(database_path=":memory:")
        conn = adapter.create_connection()
        try:
            conn.execute("CREATE TABLE t (x INT)")
            plan_conn = adapter.get_query_plan(conn, "SELECT * FROM t WHERE x = 1")
            assert plan_conn is not None

            cursor = conn.cursor()
            plan_cursor = adapter.get_query_plan(cursor, "SELECT * FROM t WHERE x = 1")
            assert plan_cursor is not None
        finally:
            conn.close()

    def test_execute_query_failure_returns_standard_failure_payload(self):
        adapter = SQLiteAdapter(database_path=":memory:")
        conn = adapter.create_connection()
        try:
            res = adapter.execute_query(conn, "SYNTAX ERROR", "q_fail")
            assert res["status"] == "FAILED"
            assert res["error"] is not None
            assert res["rows_returned"] == 0
        finally:
            conn.close()


class TestSaveTuningMetadataMidRunSafety:
    def test_save_reuses_run_connection_and_keeps_run_tables(self, tmp_path):
        db_path = tmp_path / "bench.db"
        adapter = SQLiteAdapter(
            database_path=str(db_path),
            tuning_enabled=True,
            unified_tuning_configuration=UnifiedTuningConfiguration(),
        )
        opens = []
        raw_create = adapter.create_connection

        def counting_create(**kwargs):
            opens.append(kwargs)
            return raw_create(**kwargs)

        adapter.create_connection = counting_create

        connection = adapter.create_connection(database_path=str(db_path))
        try:
            connection.execute("CREATE TABLE t (a INTEGER)")
            connection.execute("INSERT INTO t VALUES (1)")
            connection.commit()

            assert adapter.save_tuning_metadata(connection) is True

            assert len(opens) == 1
            tables = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
                ).fetchall()
            ]
            assert "t" in tables
            assert "benchbox_tuning_metadata" in tables
        finally:
            adapter.close_connection(connection)


class TestNotuningDatabaseReuse:
    def _notuning_adapter(self, db_path):
        adapter = SQLiteAdapter(database_path=str(db_path), tuning_enabled=False)
        adapter.unified_tuning_configuration = build_baseline_unified_config()
        return adapter

    def test_second_notuning_run_reuses_database(self, tmp_path):
        db_path = tmp_path / "bench.db"

        first = self._notuning_adapter(db_path)
        connection = first.create_connection(database_path=str(db_path))
        try:
            connection.execute("CREATE TABLE t (a INTEGER)")
            connection.commit()
        finally:
            first.close_connection(connection)

        second = self._notuning_adapter(db_path)
        connection = second.create_connection(database_path=str(db_path))
        try:
            assert second.database_was_reused is True
            tables = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
                ).fetchall()
            ]
            assert "t" in tables
        finally:
            second.close_connection(connection)

    def test_tuned_database_still_refused_for_notuning_run(self, tmp_path):
        db_path = tmp_path / "bench.db"

        tuned = SQLiteAdapter(database_path=str(db_path), tuning_enabled=True)
        tuned.unified_tuning_configuration = UnifiedTuningConfiguration()
        connection = tuned.create_connection(database_path=str(db_path))
        try:
            connection.execute("CREATE TABLE t (a INTEGER)")
            connection.commit()
            assert tuned.save_tuning_metadata(connection) is True
        finally:
            tuned.close_connection(connection)

        plain = self._notuning_adapter(db_path)
        connection = plain.create_connection(database_path=str(db_path))
        try:
            assert plain.database_was_reused is False
            assert plain._drift_validation_result is not None
            assert any("notuning" in error for error in plain._drift_validation_result.errors)
        finally:
            plain.close_connection(connection)
