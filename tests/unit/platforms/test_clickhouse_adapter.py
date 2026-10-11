# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import gzip
import logging
from pathlib import Path
from types import GeneratorType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from benchbox.platforms.clickhouse import ClickHouseAdapter
from benchbox.platforms.clickhouse.merge_settle import MergeSettleResult
from tests.utilities.optional_engines import chdb_skip_reason, chdb_usable

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


CHDB_AVAILABLE = chdb_usable()
CHDB_SKIP_REASON = chdb_skip_reason() or "chDB is usable"


@pytest.fixture(autouse=True)
def clickhouse_dependencies():

    with patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, [])):
        yield


class TestClickHouseAdapter:
    def test_tpcds_q35_is_removed_from_local_memory_incompatibilities(self):
        assert 35 not in ClickHouseAdapter.KNOWN_INCOMPATIBLE_QUERIES["tpcds"]

    def test_initialization_success(self):
        with patch("benchbox.platforms.clickhouse.setup.ClickHouseClient"):
            adapter = ClickHouseAdapter(
                deployment_mode="server",
                host="localhost",
                port=9000,
                database="test",
                username="test",
                password="test",
            )
            assert adapter.platform_name == "ClickHouse (Server)"
            assert adapter.dialect == "clickhouse"
            assert adapter.host == "localhost"
            assert adapter.port == 9000
            assert adapter.deployment_mode == "server"
            assert adapter.insert_block_size == 65536

    def test_server_insert_block_size_rejects_legacy_batch_size(self):
        with (
            patch("benchbox.platforms.clickhouse.setup.ClickHouseClient"),
            pytest.raises(ValueError, match="other than 1000"),
        ):
            ClickHouseAdapter(deployment_mode="server", insert_block_size=1000)

    def test_initialization_missing_driver(self):
        with (
            patch(
                "benchbox.platforms.clickhouse.adapter.check_platform_dependencies",
                return_value=(False, ["clickhouse-driver"]),
            ),
            pytest.raises(ImportError) as excinfo,
        ):
            ClickHouseAdapter(deployment_mode="server")

        assert "Missing dependencies for clickhouse platform" in str(excinfo.value)

    @patch("benchbox.platforms.clickhouse.setup.ClickHouseClient")
    def test_create_connection_success(self, mock_client_class):
        mock_client = Mock()
        mock_client_class.return_value = mock_client
        mock_client.execute.side_effect = [
            [],
            None,
            None,
        ]

        adapter = ClickHouseAdapter(deployment_mode="server", host="localhost", port=9000)
        connection = adapter.create_connection()

        assert connection == mock_client
        assert mock_client_class.call_count == 3
        assert mock_client.execute.call_count == 3
        assert mock_client_class.call_args_list[2].kwargs["send_receive_timeout"] == 300
        assert mock_client_class.call_args_list[2].kwargs["sync_request_timeout"] == 300

    @patch("benchbox.platforms.clickhouse.setup.ClickHouseClient")
    def test_create_connection_uses_configured_server_timeout(self, mock_client_class):
        mock_client_class.return_value = Mock()
        mock_client_class.return_value.execute.side_effect = [[], None, None]

        adapter = ClickHouseAdapter(deployment_mode="server", send_receive_timeout=900)
        adapter.create_connection()

        assert mock_client_class.call_args_list[2].kwargs["send_receive_timeout"] == 900
        assert mock_client_class.call_args_list[2].kwargs["sync_request_timeout"] == 900

    @patch("benchbox.platforms.clickhouse.setup.ClickHouseClient")
    def test_create_connection_bootstraps_database_before_scoped_client(self, mock_client_class):
        check_client = Mock()
        create_client = Mock()
        main_client = Mock()
        check_client.execute.return_value = []
        mock_client_class.side_effect = [check_client, create_client, main_client]

        adapter = ClickHouseAdapter(
            deployment_mode="server",
            host="localhost",
            port=9000,
            database="benchbox_run",
            username="default",
            password="benchbox",
        )

        assert adapter.create_connection() == main_client

        create_client.execute.assert_called_once_with("CREATE DATABASE IF NOT EXISTS `benchbox_run`")
        assert "database" not in mock_client_class.call_args_list[1].kwargs
        assert mock_client_class.call_args_list[2].kwargs["database"] == "benchbox_run"

    @patch("benchbox.platforms.clickhouse.setup.ClickHouseClient")
    def test_create_connection_rejects_unsafe_database_identifier(self, mock_client_class):
        check_client = Mock()
        check_client.execute.return_value = []
        mock_client_class.return_value = check_client

        adapter = ClickHouseAdapter(deployment_mode="server", database="bad-name")

        with pytest.raises(ValueError, match="Invalid database identifier"):
            adapter.create_connection()

    @patch("benchbox.platforms.clickhouse.setup.ClickHouseClient")
    def test_create_connection_failure(self, mock_client_class):
        mock_client_class.side_effect = Exception("Connection failed")

        adapter = ClickHouseAdapter(deployment_mode="server")

        with pytest.raises(Exception, match="Connection failed"):
            adapter.create_connection()

    def test_sql_translation(self):
        with patch("benchbox.platforms.clickhouse.setup.ClickHouseClient"):
            adapter = ClickHouseAdapter(deployment_mode="server")

            with patch("sqlglot.transpile") as mock_transpile:
                mock_transpile.return_value = ['SELECT * FROM "table"']

                result = adapter.translate_sql("SELECT * FROM table", "duckdb")
                assert result == 'SELECT * FROM "table";'
                mock_transpile.assert_called_once_with(
                    "SELECT * FROM table", read="duckdb", write="clickhouse", identify=False
                )

    @patch("benchbox.platforms.clickhouse.setup.ClickHouseClient")
    def test_create_schema(self, mock_client_class):
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        mock_benchmark = Mock()
        mock_benchmark.get_create_tables_sql.return_value = (
            "CREATE TABLE test (id INT NOT NULL, optional DECIMAL(10,2)); CREATE TABLE test2 (name STRING);"
        )
        mock_benchmark.get_schema.return_value = {
            "test": {
                "columns": [
                    {"name": "id", "type": "INT", "nullable": False},
                    {"name": "optional", "type": "DECIMAL(10,2)", "nullable": True},
                ]
            },
            "test2": {"columns": {"name": {"type": "STRING", "nullable": False}}},
        }

        adapter = ClickHouseAdapter(deployment_mode="server")
        connection = adapter.create_connection()

        schema_time = adapter.create_schema(mock_benchmark, connection)

        assert isinstance(schema_time, float)
        assert schema_time >= 0
        assert mock_client.execute.call_count >= 2
        executed_ddl = "\n".join(str(call.args[0]) for call in mock_client.execute.call_args_list)
        assert "id INT NOT NULL" in executed_ddl
        assert "optional Nullable(DECIMAL(10,2))" in executed_ddl
        assert "name STRING" in executed_ddl

    @patch("benchbox.platforms.clickhouse.setup.ClickHouseClient")
    def test_load_data_with_tables(self, mock_client_class):
        mock_client = Mock()
        mock_client_class.return_value = mock_client
        execute_results = iter([[], None, None])

        def execute(*args, **kwargs):
            if len(args) > 1 and isinstance(args[1], GeneratorType):
                list(args[1])
                return None
            return next(execute_results)

        mock_client.execute.side_effect = execute

        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write("1,test\n2,test2\n")
            temp_path = Path(f.name)

        try:
            mock_benchmark = Mock(spec=["tables", "get_schema"])
            tables_dict = {"test_table": str(temp_path)}
            mock_benchmark.tables = tables_dict
            mock_benchmark.get_schema.return_value = {}

            adapter = ClickHouseAdapter(deployment_mode="server")
            connection = adapter.create_connection()

            with patch(
                "benchbox.platforms.clickhouse.workload.wait_for_merges_to_settle",
                return_value=MergeSettleResult(True, 0.0, 1),
            ):
                table_stats, load_time, _ = adapter.load_data(mock_benchmark, connection, Path("/tmp"))

            assert isinstance(table_stats, dict)
            assert isinstance(load_time, float)
            assert load_time >= 0
            assert "test_table" in table_stats
            assert table_stats["test_table"] == 2

        finally:
            temp_path.unlink()

    @patch("benchbox.platforms.clickhouse.setup.ClickHouseClient")
    def test_execute_query_success(self, mock_client_class):
        mock_client = Mock()
        mock_client_class.return_value = mock_client
        mock_client.execute.return_value = [[1, "test"], [2, "test2"]]

        adapter = ClickHouseAdapter(deployment_mode="server")
        connection = adapter.create_connection()

        result = adapter.execute_query(connection, "SELECT * FROM test", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 2
        assert result["first_row"] == [1, "test"]
        assert isinstance(result["execution_time_seconds"], float)

    @patch("benchbox.platforms.clickhouse.setup.ClickHouseClient")
    def test_execute_query_failure(self, mock_client_class):
        mock_client = Mock()
        mock_client_class.return_value = mock_client
        mock_client.execute.side_effect = [
            [],
            None,
            None,
            Exception("Query failed"),
        ]

        adapter = ClickHouseAdapter(deployment_mode="server")
        connection = adapter.create_connection()

        result = adapter.execute_query(connection, "INVALID SQL", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "FAILED"
        assert result["rows_returned"] == 0
        assert result["error"] == "Query failed"
        assert isinstance(result["execution_time_seconds"], float)

    @patch("benchbox.platforms.clickhouse.setup.ClickHouseClient")
    def test_configure_for_benchmark_tuning_disabled_is_baseline_only(self, mock_client_class):
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        adapter = ClickHouseAdapter(deployment_mode="server", strict_validation=False)
        adapter.tuning_enabled = False
        connection = adapter.create_connection()
        mock_client.reset_mock()

        adapter.configure_for_benchmark(connection, "olap")

        assert mock_client.execute.call_count == 11
        executed_sql = " ".join(call[0][0] for call in mock_client.execute.call_args_list)
        assert "grace_hash" not in executed_sql
        assert "optimize_aggregation_in_order" not in executed_sql
        assert "join_use_nulls" in executed_sql

        mock_client.reset_mock()
        adapter.configure_for_benchmark(connection, "read_primitives")
        assert mock_client.execute.call_count == 11

    @patch("benchbox.platforms.clickhouse.setup.ClickHouseClient")
    def test_configure_for_benchmark_tuning_enabled_applies_olap_pack(self, mock_client_class):
        mock_client = Mock()
        mock_client_class.return_value = mock_client
        mock_client.execute.side_effect = [[], None, None]

        adapter = ClickHouseAdapter(deployment_mode="server", strict_validation=False)
        adapter.tuning_enabled = True
        connection = adapter.create_connection()

        mock_client.reset_mock()

        adapter.configure_for_benchmark(connection, "olap")

        assert mock_client.execute.call_count > 5
        executed_sql = " ".join(call[0][0] for call in mock_client.execute.call_args_list)
        assert "grace_hash" not in executed_sql
        assert "optimize_aggregation_in_order" in executed_sql

        mock_client.reset_mock()
        adapter.configure_for_benchmark(connection, "read_primitives")
        assert mock_client.execute.call_count == 11

    @patch("benchbox.platforms.clickhouse.setup.ClickHouseClient")
    def test_configure_for_benchmark_join_memory_uses_50_pct_multiplier(self, mock_client_class):
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        adapter = ClickHouseAdapter(deployment_mode="server", strict_validation=False)
        adapter.tuning_enabled = True

        connection = adapter.create_connection()
        mock_client.reset_mock()
        adapter.configure_for_benchmark(connection, "tpch")

        executed_statements = [call[0][0] for call in mock_client.execute.call_args_list]
        join_stmt = next((s for s in executed_statements if "max_bytes_in_join" in s), None)
        assert join_stmt is not None, "max_bytes_in_join setting was not applied"

        import re

        m = re.search(r"max_bytes_in_join\s*=\s*(\d+)", join_stmt)
        assert m is not None, f"Could not parse max_bytes_in_join value from: {join_stmt}"
        join_limit = int(m.group(1))

        memory_bytes = adapter._parse_memory_setting(adapter.max_memory_usage)
        expected_50pct = int(memory_bytes * 0.5)
        assert join_limit == expected_50pct, (
            f"max_bytes_in_join should be 50% of max_memory_usage ({expected_50pct}), got {join_limit}"
        )

        assert not any("join_algorithm" in s for s in executed_statements)

    @pytest.mark.skipif(not CHDB_AVAILABLE, reason=f"{CHDB_SKIP_REASON} (required for embedded mode test)")
    def test_configure_for_benchmark_embedded_mode(self):
        mock_connection = Mock()

        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = ClickHouseAdapter(
                deployment_mode="local",
                strict_validation=False,
                data_path=tmpdir,
            )
            adapter.tuning_enabled = True

            adapter.configure_for_benchmark(mock_connection, "olap")

            executed_statements = [call[0][0] for call in mock_connection.execute.call_args_list]
            executed_sql = " ".join(executed_statements)

            assert "join_algorithm" not in executed_sql
            assert "enable_multiple_joins_emulation" not in executed_sql

            assert "max_memory_usage" in executed_sql
            assert "max_threads" in executed_sql

    @pytest.mark.skipif(not CHDB_AVAILABLE, reason=f"{CHDB_SKIP_REASON} (required to execute real ClickHouse DDL)")
    def test_tuned_ddl_executes_against_real_chdb(self, tmp_path):
        from benchbox.core.tpcds.benchmark.runner import TPCDSBenchmark
        from benchbox.core.tuning.interface import TableTuning, TuningColumn

        adapter = ClickHouseAdapter(deployment_mode="local", strict_validation=False, data_path=str(tmp_path))
        connection = adapter.create_connection()

        table_tuning = TableTuning(
            table_name="lineitem",
            partitioning=[TuningColumn(name="l_shipdate", type="DATE", order=1)],
            sorting=[TuningColumn(name="l_orderkey", type="INTEGER", order=1)],
        )
        table_tunings = {"lineitem": table_tuning}

        statement = "CREATE TABLE lineitem (l_orderkey Int32, l_shipdate Date)"
        rendered = adapter._optimize_table_definition(statement, table_tunings)

        assert "PARTITION BY (toYYYYMM(l_shipdate))" in rendered
        assert "ORDER BY (l_orderkey)" in rendered

        connection.execute(rendered)

        show_create_rows = connection.execute("SHOW CREATE TABLE lineitem").fetchall()
        ddl_text = show_create_rows[0][0]

        assert "PARTITION BY toYYYYMM(l_shipdate)" in ddl_text
        assert "ORDER BY l_orderkey" in ddl_text

        connection.execute("INSERT INTO lineitem VALUES (1, '2026-01-15'), (2, '2026-02-01')")
        count_rows = connection.execute("SELECT count(*) FROM lineitem").fetchall()
        assert count_rows[0][0] == 2

        import yaml

        from benchbox.core.tuning.packaged_templates import packaged_template_path

        benchmark = TPCDSBenchmark(scale_factor=0.01, output_dir=tmp_path / "tpcds")
        nullable_by_table = adapter._get_nullable_columns_by_table(benchmark)
        template_path = packaged_template_path("clickhouse", "tpcds")
        assert template_path.exists(), f"missing curated template: {template_path}"
        payload = yaml.safe_load(template_path.read_text(encoding="utf-8")) or {}
        tpcds_tunings = {
            name: TableTuning.from_dict(entry) for name, entry in (payload.get("table_tunings") or {}).items()
        }
        assert tpcds_tunings, f"curated template carries no table tunings: {template_path}"
        created_tables: list[str] = []
        tuned_tables: list[str] = []
        statements = [
            statement.strip()
            for statement in benchmark.get_create_tables_sql(dialect="duckdb").split(";")
            if statement.strip()
        ]
        for statement in statements:
            table_name = adapter._extract_table_name(statement)
            assert table_name is not None
            nullable_columns = nullable_by_table.get(table_name.lower(), set())
            tuning_clauses = adapter._resolve_tuned_ddl_clauses(statement, tpcds_tunings)
            key_columns = adapter._resolve_key_columns(statement, tuning_clauses, nullable_columns)
            rendered = adapter._optimize_table_definition(
                statement,
                tpcds_tunings,
                nullable_columns=nullable_columns,
            )
            for key_column in key_columns:
                assert f"{key_column} Nullable(" not in rendered
            if tuning_clauses is not None:
                tuned_tables.append(table_name)
            connection.execute(rendered)
            created_tables.append(table_name)

        assert len([table for table in created_tables if table != "dbgen_version"]) == 24
        assert len(tuned_tables) == len(tpcds_tunings), (
            f"every curated template table must render tuned DDL, got tuned={sorted(tuned_tables)}"
        )

    def test_get_database_path_server_mode(self):
        with patch("benchbox.platforms.clickhouse.setup.ClickHouseClient"):
            adapter = ClickHouseAdapter(deployment_mode="server")

            result = adapter.get_database_path(database_path="some/path.duckdb")
            assert result is None

    @pytest.mark.skipif(not CHDB_AVAILABLE, reason=f"{CHDB_SKIP_REASON} (required for embedded mode)")
    def test_apply_setting_with_validation_embedded_mode(self):
        with patch("benchbox.platforms.clickhouse.setup.ClickHouseClient"):
            mock_connection = Mock()

            adapter = ClickHouseAdapter(deployment_mode="local")

            result = adapter._apply_setting_with_validation(mock_connection, "join_algorithm", "hash")
            assert result is False
            mock_connection.execute.assert_not_called()

            mock_connection.reset_mock()

            result = adapter._apply_setting_with_validation(mock_connection, "enable_multiple_joins_emulation", 1)
            assert result is False
            mock_connection.execute.assert_not_called()

            mock_connection.reset_mock()
            result = adapter._apply_setting_with_validation(mock_connection, "max_threads", 4)
            assert result is True
            mock_connection.execute.assert_called_once_with("SET max_threads = 4")

    def test_apply_setting_with_validation_server_mode(self):
        with patch("benchbox.platforms.clickhouse.setup.ClickHouseClient"):
            mock_connection = Mock()

            adapter = ClickHouseAdapter(deployment_mode="server")

            result = adapter._apply_setting_with_validation(mock_connection, "join_algorithm", "hash")
            assert result is True
            mock_connection.execute.assert_called_once_with("SET join_algorithm = 'hash'")

            mock_connection.reset_mock()
            mock_connection.execute.side_effect = Exception("Setting not supported")

            result = adapter._apply_setting_with_validation(mock_connection, "some_setting", "value")
            assert result is False
            mock_connection.execute.assert_called_once_with("SET some_setting = 'value'")

    def test_memory_setting_parsing(self):
        with patch("benchbox.platforms.clickhouse.setup.ClickHouseClient"):
            adapter = ClickHouseAdapter(deployment_mode="server")

            assert adapter._parse_memory_setting("8GB") == 8 * 1024 * 1024 * 1024
            assert adapter._parse_memory_setting("512MB") == 512 * 1024 * 1024
            assert adapter._parse_memory_setting("1024KB") == 1024 * 1024
            assert adapter._parse_memory_setting(1024) == 1024

    def test_table_optimization(self):
        with patch("benchbox.platforms.clickhouse.setup.ClickHouseClient"):
            adapter = ClickHouseAdapter(deployment_mode="server")

            original = "CREATE TABLE test (id INT, name STRING)"
            optimized = adapter._optimize_table_definition(original)
            expected = "CREATE TABLE test (id INT, name STRING) ENGINE = MergeTree() ORDER BY tuple()"
            assert optimized == expected

            with_engine = "CREATE TABLE test (id INT) ENGINE = ReplacingMergeTree()"
            optimized_with_engine = adapter._optimize_table_definition(with_engine)
            expected_with_engine = "CREATE TABLE test (id INT) ENGINE = ReplacingMergeTree() ORDER BY tuple()"
            assert optimized_with_engine == expected_with_engine

            duckdb_array = "CREATE TABLE vectors (id BIGINT, embedding FLOAT[128])"
            assert "Array(Float32)" in adapter._optimize_table_definition(duckdb_array)
            assert "FLOAT[" not in adapter._optimize_table_definition(duckdb_array)

            duckdb_double_array = "CREATE TABLE vectors (id BIGINT, embedding DOUBLE[256])"
            assert "Array(Float64)" in adapter._optimize_table_definition(duckdb_double_array)

            from benchbox.core.vector_search.benchmark import VectorSearchBenchmark

            vector_benchmark = VectorSearchBenchmark(scale_factor=0.01)
            assert adapter._get_nullable_columns_by_table(vector_benchmark) == {}
            vector_ddl = vector_benchmark.get_create_tables_sql(dialect="duckdb")
            rendered = [
                adapter._optimize_table_definition(statement.strip())
                for statement in vector_ddl.split(";")
                if statement.strip()
            ]
            assert all("Nullable(Array(" not in statement for statement in rendered)
            assert any("embedding Array(Float32)" in statement for statement in rendered)
            assert any("query_vector Array(Float32)" in statement for statement in rendered)

            mixed = "CREATE TABLE t (vec FLOAT[10], my_float_col INT)"
            mixed_out = adapter._optimize_table_definition(mixed)
            assert "vec Array(Float32)" in mixed_out
            assert "my_float_col INT" in mixed_out
            assert "FLOAT[" not in mixed_out

            time_ddl = (
                "CREATE TABLE metrics (time TIMESTAMP, metric_time TIME, hostname VARCHAR(64), "
                "PRIMARY KEY (time, hostname))"
            )
            time_out = adapter._optimize_table_definition(time_ddl)
            assert "time TIMESTAMP" in time_out
            assert "metric_time String" in time_out
            assert "PRIMARY KEY (time, hostname)" in time_out
            assert "String TIMESTAMP" not in time_out

            compact = "CREATE TABLE t (amount DECIMAL(10,2), next_col INTEGER, tail VARCHAR(5))"
            compact_out = adapter._optimize_table_definition(
                compact,
                nullable_columns={"amount", "next_col"},
            )
            assert "amount Nullable(DECIMAL(10,2)), next_col Nullable(INTEGER), tail VARCHAR(5)" in compact_out

            with pytest.raises(ValueError, match="source-nullable column 'amount'"):
                adapter._optimize_table_definition(
                    "CREATE TABLE t (amount DECIMAL(10,2) NOT NULL)",
                    nullable_columns={"amount"},
                )

            from benchbox.core.tuning.interface import TableTuning, TuningColumn

            tuned = {
                "events": TableTuning(
                    table_name="events",
                    partitioning=[TuningColumn(name="event_date", type="DATE", order=1)],
                    sorting=[TuningColumn(name="event_id", type="INTEGER", order=1)],
                )
            }
            with pytest.raises(ValueError, match="tuned key columns of table 'events'"):
                adapter._optimize_table_definition(
                    "CREATE TABLE events (event_id INTEGER, event_date DATE, payload VARCHAR(50))",
                    tuned,
                    nullable_columns={"event_id", "event_date", "payload"},
                )
            keyed = adapter._optimize_table_definition(
                "CREATE TABLE events (event_id INTEGER, event_date DATE, payload VARCHAR(50))",
                tuned,
                nullable_columns={"payload"},
            )
            assert "ORDER BY (event_id)" in keyed
            assert "payload Nullable(VARCHAR(50))" in keyed
            assert "event_id Nullable" not in keyed
            assert "event_date Nullable" not in keyed

    def test_array_field_parsed_to_list_not_float(self):
        from benchbox.platforms.base.data_loading import ClickHouseNativeHandler

        handler = ClickHouseNativeHandler(delimiter=",", adapter=None, benchmark=None)

        for type_name in ("Array(Float32)", "FLOAT_ARRAY", "Array(Float64)"):
            converted = handler._convert_field_for_clickhouse("[0.1,0.2]", type_name)
            assert converted == [0.1, 0.2], f"{type_name} should parse to a list"

        assert handler._convert_field_for_clickhouse("0.5", "FLOAT") == 0.5
        assert handler._convert_field_for_clickhouse("\\N", "Array(Float32)") is None

    def test_vector_bracket_type_parsed_to_list_not_float(self):
        from benchbox.platforms.base.data_loading import ClickHouseNativeHandler

        handler = ClickHouseNativeHandler(delimiter=",", adapter=None, benchmark=None)

        assert handler._convert_field_for_clickhouse("[0.02,0.5,0.9]", "FLOAT[3]") == [0.02, 0.5, 0.9]
        assert handler._convert_field_for_clickhouse("[1,2,3]", "INTEGER[]") == [1, 2, 3]
        assert handler._convert_field_for_clickhouse("[0.1,0.2]", "DOUBLE[2]") == [0.1, 0.2]
        assert handler._convert_field_for_clickhouse("", "FLOAT[3]") is None
        assert handler._convert_field_for_clickhouse("\\N", "FLOAT[3]") is None

    def test_datetime_and_timestamp_conversion(self):
        from datetime import date as date_cls, datetime as dt

        from benchbox.platforms.base.data_loading import ClickHouseNativeHandler

        handler = ClickHouseNativeHandler(delimiter=",", adapter=None, benchmark=None)
        conv = handler._convert_field_for_clickhouse

        assert conv("2020-01-15 12:30:00", "DATETIME") == dt(2020, 1, 15, 12, 30, 0)
        assert conv("2020-01-15 12:30:00", "TIMESTAMP") == dt(2020, 1, 15, 12, 30, 0)
        assert conv("2016-01-01T00:00:00Z", "DateTime64(3)").year == 2016
        assert conv("2020-01-15", "DATE") == date_cls(2020, 1, 15)
        assert conv("", "DATETIME") is None
        assert conv("", "TIMESTAMP") is None

    def test_datetime_z_suffix_parses_on_python_3_10(self):
        from datetime import datetime as dt, timezone

        from benchbox.platforms.base.data_loading import ClickHouseNativeHandler

        handler = ClickHouseNativeHandler(delimiter=",", adapter=None, benchmark=None)
        conv = handler._convert_field_for_clickhouse

        assert conv("2016-01-01T00:00:00Z", "DATETIME") == dt(2016, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert conv("2016-01-01T00:00:00Z", "TIMESTAMP") == dt(2016, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    def test_empty_numeric_date_fields_become_null_not_default(self):
        from benchbox.platforms.base.data_loading import ClickHouseNativeHandler

        handler = ClickHouseNativeHandler(delimiter=",", adapter=None, benchmark=None)
        conv = handler._convert_field_for_clickhouse

        for type_name in ("INTEGER", "BIGINT", "DECIMAL(10,2)", "DOUBLE", "DATE", "DATETIME"):
            assert conv("", type_name) is None, f"empty {type_name} must become NULL"
        assert conv("", "VARCHAR") == ""
        assert conv("", "CHAR(16)") == ""

    def test_object_schema_table_column_count_and_types(self):
        from benchbox.core.datavault import schema as dv
        from benchbox.platforms.base.data_loading import ClickHouseNativeHandler, SchemaInspector

        class _ObjectSchemaBenchmark:
            def get_schema(self):
                return dv.TABLES_BY_NAME.copy()

        benchmark = _ObjectSchemaBenchmark()
        table_name, table = next(iter(dv.TABLES_BY_NAME.items()))

        count = SchemaInspector.get_column_count(benchmark, table_name, file_handle=None, delimiter=",")
        assert count == len(table.columns)

        handler = ClickHouseNativeHandler.__new__(ClickHouseNativeHandler)
        type_names = handler._get_column_type_names(benchmark, table_name)
        assert len(type_names) == len(table.columns)
        assert type_names == [col.get_sql_type().upper() for col in table.columns]

        class _DictSchemaBenchmark:
            def get_schema(self):
                return {"t": {"columns": [{"type": "INTEGER"}, {"type": "VARCHAR"}]}}

        dict_bm = _DictSchemaBenchmark()
        assert SchemaInspector.get_column_count(dict_bm, "t", file_handle=None, delimiter=",") == 2
        assert handler._get_column_type_names(dict_bm, "t") == ["INTEGER", "VARCHAR"]

    @patch("benchbox.platforms.clickhouse.setup.ClickHouseClient")
    def test_get_platform_metadata(self, mock_client_class):
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        mock_client.execute.return_value = [["21.8.0"]]

        adapter = ClickHouseAdapter(deployment_mode="server", host="test", port=9000, database="test")
        connection = adapter.create_connection()

        metadata = adapter._get_platform_metadata(connection)

        assert metadata["platform"] == "ClickHouse (Server)"
        assert metadata["host"] == "test"
        assert metadata["port"] == 9000
        assert metadata["database"] == "test"
        assert "clickhouse_version" in metadata

    @patch("benchbox.platforms.clickhouse.setup.ClickHouseClient")
    def test_get_table_info(self, mock_client_class):
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        mock_client.execute.side_effect = [
            [],
            None,
            None,
            [("id", "Int32"), ("name", "String")],
            [(1000, 1024000, 512000)],
        ]

        adapter = ClickHouseAdapter(deployment_mode="server")
        connection = adapter.create_connection()

        info = adapter.get_table_info(connection, "test_table")

        assert info["columns"] == [("id", "Int32"), ("name", "String")]
        assert info["row_count"] == 1000
        assert info["bytes_on_disk"] == 1024000
        assert info["compressed_size"] == 512000

    @patch("benchbox.platforms.clickhouse.setup.ClickHouseClient")
    def test_optimize_table(self, mock_client_class):
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        adapter = ClickHouseAdapter(deployment_mode="server")
        connection = adapter.create_connection()

        adapter.optimize_table(connection, "test_table")

        mock_client.execute.assert_called_with("OPTIMIZE TABLE test_table FINAL")

    @patch("benchbox.platforms.clickhouse.setup.ClickHouseClient")
    def test_close_connection(self, mock_client_class):
        created = []

        def _factory(*args, **kwargs):
            client = Mock()
            created.append(client)
            return client

        mock_client_class.side_effect = _factory

        adapter = ClickHouseAdapter(deployment_mode="server")
        connection = adapter.create_connection()

        adapter.close_connection(connection)

        assert len(created) >= 2
        for client in created:
            client.disconnect.assert_called_once()

    def test_test_connection(self):
        with patch("benchbox.platforms.clickhouse.setup.ClickHouseClient") as mock_client_class:
            mock_client = Mock()
            mock_client_class.return_value = mock_client

            adapter = ClickHouseAdapter(deployment_mode="server")

            assert adapter.test_connection() is True

            mock_client_class.side_effect = Exception("Connection failed")
            assert adapter.test_connection() is False

    @patch("benchbox.platforms.clickhouse.setup.ClickHouseClient")
    def test_apply_table_tunings_with_sorting(self, mock_client_class):
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        mock_tuning = Mock()
        mock_tuning.table_name = "test_table"

        mock_sort_col = Mock()
        mock_sort_col.name = "id"
        mock_sort_col.order = 1

        def mock_get_columns_by_type(tuning_type):
            from benchbox.core.tuning.interface import TuningType

            if str(tuning_type) == str(TuningType.SORTING):
                return [mock_sort_col]
            return []

        mock_tuning.get_columns_by_type.side_effect = mock_get_columns_by_type

        mock_tuning.sorting = [mock_sort_col]
        mock_tuning.clustering = None
        mock_tuning.partitioning = None
        mock_tuning.distribution = None

        adapter = ClickHouseAdapter(deployment_mode="server")
        connection = adapter.create_connection()

        adapter.apply_table_tunings(mock_tuning, connection)

        optimize_calls = [call for call in mock_client.execute.call_args_list if "OPTIMIZE" in str(call)]
        assert optimize_calls == []

    @patch("benchbox.platforms.clickhouse.setup.ClickHouseClient")
    def test_apply_table_tunings_with_clustering(self, mock_client_class):
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        mock_tuning = Mock()
        mock_tuning.table_name = "test_table"

        mock_cluster_col = Mock()
        mock_cluster_col.name = "cluster_key"
        mock_cluster_col.order = 1

        def mock_get_columns_by_type(tuning_type):
            from benchbox.core.tuning.interface import TuningType

            if str(tuning_type) == str(TuningType.CLUSTERING):
                return [mock_cluster_col]
            return []

        mock_tuning.get_columns_by_type.side_effect = mock_get_columns_by_type

        mock_tuning.sorting = None
        mock_tuning.clustering = [mock_cluster_col]
        mock_tuning.partitioning = None
        mock_tuning.distribution = None

        adapter = ClickHouseAdapter(deployment_mode="server")
        connection = adapter.create_connection()

        adapter.apply_table_tunings(mock_tuning, connection)

        optimize_final_calls = [
            call for call in mock_client.execute.call_args_list if "OPTIMIZE TABLE test_table FINAL" in str(call)
        ]
        assert optimize_final_calls == []

        adapter.platform_config["optimize_after_load"] = True
        adapter.tuning_enabled = True
        adapter.apply_post_load_tunings("test_table", Mock(table_tunings={}), connection)

        post_load_calls = [
            call for call in mock_client.execute.call_args_list if "OPTIMIZE TABLE test_table FINAL" in str(call)
        ]
        assert len(post_load_calls) > 0

    @patch("benchbox.platforms.clickhouse.setup.ClickHouseClient")
    def test_apply_table_tunings_none(self, mock_client_class):
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        adapter = ClickHouseAdapter(deployment_mode="server")
        connection = adapter.create_connection()

        adapter.apply_table_tunings(None, connection)


class TestClickHouseNativeHandlerBulk:
    def _make_handler(self, dry_run: bool = False, server_mode: bool = False):
        from benchbox.platforms.base.data_loading import ClickHouseNativeHandler

        adapter = Mock(spec=[])
        if server_mode:
            adapter.deployment_mode = "server"
        if dry_run:
            adapter.dry_run_mode = True
            adapter.capture_sql = Mock()
        benchmark = Mock(spec=[])
        return ClickHouseNativeHandler("|", adapter, benchmark)

    def _make_bulk_connection(self, before: int, after: int) -> Mock:
        connection = Mock()
        connection.execute.side_effect = [[[before]], None, [[after]]]
        return connection

    def test_bulk_load_same_dir_uses_glob_sql(self, tmp_path):
        shards = [tmp_path / f"lineitem.tbl.{i}" for i in range(1, 5)]
        for s in shards:
            s.touch()

        handler = self._make_handler()
        connection = self._make_bulk_connection(before=0, after=4000)

        result = handler.load_table_bulk("lineitem", shards, connection, Mock(), Mock())

        assert result == 4000
        assert connection.execute.call_count == 3
        insert_sql = connection.execute.call_args_list[1][0][0]
        assert "INSERT INTO lineitem" in insert_sql
        assert "lineitem.tbl.*" in insert_sql
        assert "file(" in insert_sql
        assert "format_csv_delimiter" in insert_sql

    def test_bulk_load_different_dirs_falls_back(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        shards = [dir_a / "orders.tbl.1", dir_b / "orders.tbl.2"]
        for s in shards:
            s.touch()

        handler = self._make_handler()

        connection = Mock()
        connection.execute.side_effect = [
            [[0]],
            None,
            [[500]],
            [[500]],
            None,
            [[1000]],
        ]

        result = handler.load_table_bulk("orders", shards, connection, Mock(), Mock())

        assert connection.execute.call_count == 6
        assert result == 1000

    def test_bulk_load_single_shard_delegates_to_load_table(self, tmp_path):
        shard = tmp_path / "region.tbl"
        shard.touch()

        handler = self._make_handler()
        connection = self._make_bulk_connection(before=0, after=5)

        result = handler.load_table_bulk("region", [shard], connection, Mock(), Mock())

        assert result == 5

    def test_load_table_parquet_zst_uses_parquet_sql(self, tmp_path):
        parquet_file = tmp_path / "lineitem.parquet.zst"
        parquet_file.touch()

        handler = self._make_handler()
        connection = self._make_bulk_connection(before=0, after=6000)

        result = handler.load_table("lineitem", parquet_file, connection, Mock(), Mock())

        assert result == 6000
        insert_sql = connection.execute.call_args_list[1][0][0]
        assert "file(" in insert_sql
        assert "'Parquet'" in insert_sql
        assert "format_csv_delimiter" not in insert_sql

    def test_bulk_load_parquet_shards_use_parquet_sql(self, tmp_path):
        shards = [tmp_path / f"lineitem.parquet.{i}.zst" for i in range(1, 3)]
        for shard in shards:
            shard.touch()

        handler = self._make_handler()
        connection = self._make_bulk_connection(before=0, after=6000)

        result = handler.load_table_bulk("lineitem", shards, connection, Mock(), Mock())

        assert result == 6000
        insert_sql = connection.execute.call_args_list[1][0][0]
        assert "lineitem.parquet.*" in insert_sql
        assert "'Parquet'" in insert_sql
        assert "format_csv_delimiter" not in insert_sql

    def test_bulk_load_dry_run_returns_placeholder(self, tmp_path):
        shards = [tmp_path / f"customer.tbl.{i}" for i in range(1, 5)]
        for s in shards:
            s.touch()

        handler = self._make_handler(dry_run=True)
        connection = Mock()

        result = handler.load_table_bulk("customer", shards, connection, Mock(), Mock())

        assert result == 4000
        assert not connection.execute.called
        handler.adapter.capture_sql.assert_called_once()

    def test_server_mode_loads_host_files_with_client_batches(self, tmp_path):
        shard = tmp_path / "region.tbl"
        shard.write_text("1|AFRICA\n2|AMERICA\n", encoding="utf-8")

        handler = self._make_handler(server_mode=True)
        connection = Mock()
        connection.execute.side_effect = lambda query, params=None, **kwargs: (
            list(params) if params is not None else None
        )
        benchmark = Mock()
        benchmark.get_schema.return_value = {
            "region": {
                "columns": [
                    {"name": "r_regionkey", "type": "INTEGER"},
                    {"name": "r_name", "type": "CHAR(25)"},
                ]
            }
        }

        with patch("benchbox.platforms.base.data_loading.RowBatchProcessor") as batch_processor:
            result = handler.load_table_bulk("region", [shard], connection, benchmark, Mock())

        assert result == 2
        batch_processor.assert_not_called()
        query, params = connection.execute.call_args.args[:2]
        assert query == "INSERT INTO region VALUES"
        assert isinstance(params, GeneratorType)
        assert connection.execute.call_args.kwargs["settings"]["insert_block_size"] > 1000
        assert "file(" not in connection.execute.call_args[0][0]

    def test_server_mode_uses_one_generator_across_shards(self, tmp_path):
        shards = [tmp_path / "region.tbl.1", tmp_path / "region.tbl.2"]
        shards[0].write_text("1|AFRICA\n", encoding="utf-8")
        shards[1].write_text("2|AMERICA\n", encoding="utf-8")

        handler = self._make_handler(server_mode=True)
        benchmark = Mock()
        benchmark.get_schema.return_value = {
            "region": {
                "columns": [
                    {"name": "r_regionkey", "type": "INTEGER"},
                    {"name": "r_name", "type": "CHAR(25)"},
                ]
            }
        }
        connection = Mock()
        seen: dict[str, object] = {}

        def execute(query, params=None, **kwargs):
            seen["query"] = query
            seen["params_type"] = type(params)
            seen["generator"] = params
            seen["rows"] = list(params)
            seen["settings"] = kwargs["settings"]

        connection.execute.side_effect = execute

        result = handler.load_table_bulk("region", shards, connection, benchmark, Mock())

        assert result == 2
        assert seen["query"] == "INSERT INTO region VALUES"
        assert seen["params_type"] is GeneratorType
        assert seen["rows"] == [(1, "AFRICA"), (2, "AMERICA")]
        assert seen["settings"] == {"insert_block_size": 65536}
        assert seen["generator"].gi_frame is None

    def test_server_mode_dry_run_is_explicit_and_not_a_batch_placeholder(self, tmp_path):
        shard = tmp_path / "region.tbl"
        shard.write_text("1|AFRICA\n", encoding="utf-8")
        handler = self._make_handler(dry_run=True, server_mode=True)
        connection = Mock()

        result = handler.load_table_bulk("region", [shard], connection, Mock(), Mock())

        assert result == 0
        assert not connection.execute.called
        handler.adapter.capture_sql.assert_called_once()
        assert "INSERT INTO region VALUES" in handler.adapter.capture_sql.call_args.args[0]

    def test_server_mode_preserves_header_blank_and_width_normalization(self, tmp_path):
        shard = tmp_path / "events.csv.gz"
        with gzip.open(shard, "wt", encoding="utf-8") as file_handle:
            file_handle.write("id,name\n\n1,alpha,ignored\n2\n")

        handler = self._make_handler(server_mode=True)
        handler.delimiter_char = ","
        handler.has_header = True
        benchmark = Mock()
        benchmark.get_schema.return_value = {"events": {"columns": [{"type": "INTEGER"}, {"type": "STRING"}]}}
        connection = Mock()
        seen_rows: list[tuple[object, ...]] = []

        def execute(query, params=None, **kwargs):
            assert isinstance(params, GeneratorType)
            seen_rows.extend(params)

        connection.execute.side_effect = execute

        result = handler.load_table_bulk("events", [shard], connection, benchmark, Mock())

        assert result == 2
        assert seen_rows == [(1, "alpha"), (2, "")]

    def test_server_mode_parquet_is_bounded_and_not_materialized(self, tmp_path):
        pa = pytest.importorskip("pyarrow")
        import pyarrow.parquet as pq

        shard = tmp_path / "events.parquet"
        pq.write_table(pa.table({"id": [1, 2], "name": ["alpha", "beta"]}), shard)
        handler = self._make_handler(server_mode=True)
        connection = Mock()
        seen: dict[str, object] = {}

        def execute(query, params=None, **kwargs):
            seen["type"] = type(params)
            seen["rows"] = list(params)
            seen["settings"] = kwargs["settings"]

        connection.execute.side_effect = execute

        result = handler.load_table_bulk("events", [shard], connection, Mock(), Mock())

        assert result == 2
        assert seen == {
            "type": GeneratorType,
            "rows": [(1, "alpha"), (2, "beta")],
            "settings": {"insert_block_size": 65536},
        }

    def test_server_mode_failure_is_typed_and_terminal(self, tmp_path):
        from benchbox.platforms.base.data_loading import ClickHouseServerLoadError

        shard = tmp_path / "events.tbl"
        shard.write_text("1|alpha\n2|beta\n", encoding="utf-8")
        handler = self._make_handler(server_mode=True)
        connection = Mock()

        def execute(query, params=None, **kwargs):
            list(params)
            raise RuntimeError("server timed out")

        connection.execute.side_effect = execute

        with pytest.raises(ClickHouseServerLoadError) as exc_info:
            handler.load_table_bulk("events", [shard], connection, Mock(), Mock())

        error = exc_info.value
        assert error.table_name == "events"
        assert error.source_files == (shard,)
        assert error.rows_attempted == 2
        assert "server timed out" in str(error)


from benchbox.platforms.clickhouse.metadata import ClickHouseMetadataMixin


class _DummyClickHouseMetadata(ClickHouseMetadataMixin):
    def __init__(self, deployment_mode="local"):
        self.deployment_mode = deployment_mode
        self.data_path = "/tmp/ch"
        self.host = "localhost"
        self.port = 9000
        self.database = "bench"
        self.disable_result_cache = False
        self.logger = SimpleNamespace(debug=lambda *a, **k: None)


class TestClickHouseMetadataCoverage:
    def test_platform_name_and_target_dialect(self):
        assert _DummyClickHouseMetadata(deployment_mode="local").platform_name == "ClickHouse (Local)"
        assert _DummyClickHouseMetadata(deployment_mode="server").get_target_dialect() == "clickhouse"

    def test_get_database_path_variants(self, tmp_path):
        adapter = _DummyClickHouseMetadata(deployment_mode="local")

        duckdb_path = adapter.get_database_path(database_path="/tmp/db.duckdb")
        bare_path = adapter.get_database_path(database_path="/tmp/db")
        assert duckdb_path is not None
        assert bare_path is not None
        assert duckdb_path.endswith(".chdb")
        assert bare_path.endswith(".chdb")

        assert adapter.get_database_path(database_name="bench_local") is None

        server_adapter = _DummyClickHouseMetadata(deployment_mode="server")
        assert server_adapter.get_database_path(database_path="/tmp/db.duckdb") is None

    def test_get_platform_info_local_no_connection(self):
        adapter = _DummyClickHouseMetadata(deployment_mode="local")
        info = adapter.get_platform_info(connection=None)
        assert info["platform_type"] == "clickhouse"
        assert info["platform_version"] is None
        assert info["configuration"]["deployment_mode"] == "local"

    def test_get_platform_info_local_connection(self):
        adapter = _DummyClickHouseMetadata(deployment_mode="local")

        class LocalConn:
            def query(self, _sql):
                return "24.10.1.1234\n"

        info = adapter.get_platform_info(connection=LocalConn())
        assert info["platform_version"] == "24.10.1.1234"

    def test_get_platform_info_server_connection(self):
        adapter = _DummyClickHouseMetadata(deployment_mode="server")

        class Cursor:
            def __init__(self):
                self.calls = 0

            def execute(self, _sql):
                self.calls += 1

            def fetchone(self):
                return ("24.9",)

            def fetchall(self):
                if self.calls == 2:
                    return [("max_threads", "8")]
                if self.calls == 3:
                    return [("BUILD_TYPE", "Release")]
                return []

            def close(self):
                return None

        class Conn:
            def cursor(self):
                return Cursor()

        info = adapter.get_platform_info(connection=Conn())
        assert info["platform_version"] == "24.9"
        assert info["compute_configuration"]["system_settings"]["max_threads"] == "8"
        assert info["compute_configuration"]["build_options"]["BUILD_TYPE"] == "Release"


class TestClickHouseWorkloadCoverage:
    @staticmethod
    def _adapter(deployment_mode: str = "server") -> ClickHouseAdapter:
        adapter = ClickHouseAdapter.__new__(ClickHouseAdapter)
        adapter.deployment_mode = deployment_mode
        logger = logging.getLogger(f"benchbox.tests.clickhouse.{deployment_mode}")
        logger.debug = Mock()
        adapter.logger = logger
        adapter.verbose_enabled = False
        adapter.very_verbose = False
        adapter.log_verbose = Mock()
        adapter.log_very_verbose = Mock()
        return adapter

    def test_local_tpchavoc_resource_variants_get_statement_level_grace_hash(self):
        adapter = self._adapter(deployment_mode="local")
        connection = Mock()
        connection.execute.return_value = [(1,)]

        result = adapter.execute_query(
            connection,
            "SELECT 1",
            "5_v8",
            benchmark_type="tpchavoc",
            validate_row_count=False,
        )

        assert result["status"] == "SUCCESS"
        statement = connection.execute.call_args.args[0]
        assert "join_algorithm = 'grace_hash'" in statement
        assert "grace_hash_join_initial_buckets = 8" in statement

    def test_server_tpchavoc_resource_variants_keep_session_policy_unchanged(self):
        adapter = self._adapter(deployment_mode="server")
        connection = Mock()
        connection.execute.return_value = [(1,)]

        adapter.execute_query(connection, "SELECT 1", "5_v7", benchmark_type="tpchavoc", validate_row_count=False)

        statement = connection.execute.call_args.args[0]
        assert "join_algorithm" not in statement

    @pytest.mark.parametrize("query_id", ["21", "Q21"])
    def test_tuned_server_tpch_q21_uses_statement_level_hash_join(self, query_id):
        adapter = self._adapter(deployment_mode="server")
        adapter.tuning_enabled = True
        connection = Mock()
        connection.execute.return_value = [(1,)]

        adapter.execute_query(connection, "SELECT 1", query_id, benchmark_type="tpch", validate_row_count=False)

        assert "join_algorithm = 'hash'" in connection.execute.call_args.args[0]

    @pytest.mark.parametrize(
        ("deployment_mode", "tuning_enabled", "benchmark_type", "query_id"),
        [
            ("server", False, "tpch", "21"),
            ("server", True, "tpch", "20"),
            ("server", True, "tpcds", "21"),
            ("local", True, "tpch", "21"),
        ],
    )
    def test_statement_level_hash_join_is_limited_to_tuned_server_tpch_q21(
        self, deployment_mode, tuning_enabled, benchmark_type, query_id
    ):
        adapter = self._adapter(deployment_mode=deployment_mode)
        adapter.tuning_enabled = tuning_enabled
        connection = Mock()
        connection.execute.return_value = [(1,)]

        adapter.execute_query(connection, "SELECT 1", query_id, benchmark_type=benchmark_type, validate_row_count=False)

        assert "join_algorithm" not in connection.execute.call_args.args[0]

    def test_extract_primary_key_columns_handles_inline_and_composite_keys(self):
        adapter = self._adapter()

        columns = adapter._extract_primary_key_columns(
            "CREATE TABLE orders (id INT PRIMARY KEY, customer_id INT, PRIMARY KEY (customer_id, order_id))"
        )

        assert "id" in columns
        assert "customer_id" in columns
        assert "order_id" in columns

    def test_get_existing_tables_handles_local_server_and_errors(self):
        local_adapter = self._adapter(deployment_mode="local")
        local_connection = Mock()
        local_connection.execute.return_value = [("Orders",), "LineItem"]
        assert local_adapter._get_existing_tables(local_connection) == ["orders", "lineitem"]

        server_adapter = self._adapter()
        server_connection = Mock()
        server_connection.execute.return_value = [("Orders",), ("LineItem",)]
        assert server_adapter._get_existing_tables(server_connection) == ["orders", "lineitem"]

        failing_connection = Mock()
        failing_connection.execute.side_effect = RuntimeError("boom")
        assert server_adapter._get_existing_tables(failing_connection) == []

    def test_get_table_row_count_uses_execute_not_cursor(self):
        adapter = self._adapter(deployment_mode="local")

        conn = Mock()
        conn.execute.return_value = [(42,)]
        assert adapter.get_table_row_count(conn, "orders") == 42
        conn.execute.assert_called_once_with("SELECT COUNT(*) FROM orders")

        conn2 = Mock(spec=[])
        conn2.execute = Mock(return_value=[(99,)])
        assert adapter.get_table_row_count(conn2, "orders") == 99

        conn3 = Mock()
        conn3.execute.side_effect = RuntimeError("boom")
        assert adapter.get_table_row_count(conn3, "orders") == 0

    def test_get_constraint_configuration_uses_effective_config(self):
        adapter = self._adapter()
        adapter.get_effective_tuning_configuration = Mock(
            return_value=SimpleNamespace(foreign_keys=SimpleNamespace(enabled=True))
        )

        assert adapter._get_constraint_configuration() == (True, True)

    def test_validate_data_integrity_covers_success_failure_and_missing_execute(self):
        adapter = self._adapter()

        healthy_connection = Mock()
        healthy_connection.execute.return_value = None
        status, details = adapter._validate_data_integrity(None, healthy_connection, {"orders": 1})
        assert status == "PASSED"
        assert details["accessible_tables"] == ["orders"]
        assert details["constraints_enabled"] is True

        def _execute(sql):
            if "lineitem" in sql:
                raise RuntimeError("missing")
            return None

        flaky_connection = SimpleNamespace(execute=_execute)
        status, details = adapter._validate_data_integrity(None, flaky_connection, {"orders": 1, "lineitem": 2})
        assert status == "FAILED"
        assert details["inaccessible_tables"] == ["lineitem"]
        assert details["constraints_enabled"] is False

        status, details = adapter._validate_data_integrity(None, SimpleNamespace(execute=None), {"orders": 1})
        assert status == "FAILED"
        assert "execute() method" in details["integrity_error"]

    def test_execute_query_adds_row_count_validation_metadata(self):
        adapter = self._adapter()
        adapter.verbose_enabled = True
        adapter.very_verbose = True
        connection = Mock()
        connection.execute.return_value = [(1,)]

        transformer = Mock()
        transformer.transform.return_value = "SELECT 1"
        transformer.add_query_settings.return_value = "SELECT 1 SETTINGS joined_subquery_requires_alias = 0"
        transformer.get_transformations_applied.return_value = ["expanded_final"]
        validation_result = SimpleNamespace(
            is_valid=True,
            warning_message="expected row count unavailable",
            error_message=None,
            expected_row_count=None,
        )

        with (
            patch("benchbox.platforms.clickhouse.workload.ClickHouseQueryTransformer", return_value=transformer),
            patch("benchbox.core.validation.query_validation.QueryValidator") as validator_cls,
        ):
            validator_cls.return_value.validate_query_result.return_value = validation_result
            result = adapter.execute_query(
                connection,
                "SELECT * FROM table",
                "Q1",
                benchmark_type="tpch",
                scale_factor=1.0,
            )

        assert result["status"] == "SUCCESS"
        assert result["row_count_validation"]["status"] == "PASSED"
        assert result["row_count_validation"]["warning"] == "expected row count unavailable"
        adapter.log_verbose.assert_any_call("Query Q1: Applied transformations: expanded_final")

    def test_execute_query_turns_validation_failures_into_failed_results(self):
        adapter = self._adapter()
        connection = Mock()
        connection.execute.return_value = [(1,), (2,)]

        transformer = Mock()
        transformer.transform.return_value = "SELECT 1"
        transformer.add_query_settings.return_value = "SELECT 1 SETTINGS joined_subquery_requires_alias = 0"
        transformer.get_transformations_applied.return_value = []
        validation_result = SimpleNamespace(
            is_valid=False,
            warning_message=None,
            error_message="row count mismatch",
            expected_row_count=5,
        )

        with (
            patch("benchbox.platforms.clickhouse.workload.ClickHouseQueryTransformer", return_value=transformer),
            patch("benchbox.core.validation.query_validation.QueryValidator") as validator_cls,
        ):
            validator_cls.return_value.validate_query_result.return_value = validation_result
            result = adapter.execute_query(connection, "SELECT * FROM table", "Q2", benchmark_type="tpch")

        assert result["status"] == "FAILED"
        assert result["row_count_validation"]["status"] == "FAILED"
        assert result["row_count_validation"]["error"] == "row count mismatch"
        assert result["error"] == "row count mismatch"

    def test_execute_query_returns_failure_payload_on_exceptions(self):
        adapter = self._adapter()
        connection = Mock()
        connection.execute.side_effect = RuntimeError("boom")

        transformer = Mock()
        transformer.transform.return_value = "SELECT 1"
        transformer.add_query_settings.return_value = "SELECT 1 SETTINGS joined_subquery_requires_alias = 0"
        transformer.get_transformations_applied.return_value = []

        with patch("benchbox.platforms.clickhouse.workload.ClickHouseQueryTransformer", return_value=transformer):
            result = adapter.execute_query(connection, "SELECT * FROM table", "Q3")

        assert result["status"] == "FAILED"
        assert result["error"] == "boom"
        assert result["error_type"] == "RuntimeError"

    def test_execute_query_error_message_capture_preserves_exception_text(self):
        adapter = self._adapter()
        connection = Mock()
        driver_error = (
            "ClickHouse local query failed: Code: 47. DB::Exception: "
            "Unknown expression identifier `foo_invalid`. (UNKNOWN_IDENTIFIER)"
        )
        connection.execute.side_effect = RuntimeError(driver_error)

        transformer = Mock()
        transformer.transform.return_value = "SELECT foo_invalid"
        transformer.add_query_settings.return_value = "SELECT foo_invalid"
        transformer.get_transformations_applied.return_value = []

        with patch("benchbox.platforms.clickhouse.workload.ClickHouseQueryTransformer", return_value=transformer):
            result = adapter.execute_query(connection, "SELECT foo_invalid", "Q_err")

        assert result["status"] == "FAILED"
        assert result["error"] == driver_error
        assert "UNKNOWN_IDENTIFIER" in result["error"]
        assert result["error_type"] == "RuntimeError"

    def test_execute_query_error_capture_falls_back_when_str_is_empty(self):
        adapter = self._adapter()
        connection = Mock()
        connection.execute.side_effect = RuntimeError()

        transformer = Mock()
        transformer.transform.return_value = "SELECT 1"
        transformer.add_query_settings.return_value = "SELECT 1"
        transformer.get_transformations_applied.return_value = []

        with patch("benchbox.platforms.clickhouse.workload.ClickHouseQueryTransformer", return_value=transformer):
            result = adapter.execute_query(connection, "SELECT 1", "Q_empty")

        assert result["status"] == "FAILED"
        assert result["error"]
        assert result["error_type"] == "RuntimeError"


class TestClickHouseQueryTransformerDecimalLiterals:
    def _transformer(self):
        from benchbox.platforms.clickhouse.query_transformer import ClickHouseQueryTransformer

        return ClickHouseQueryTransformer()

    def test_arithmetic_non_zero_decimal_not_corrupted(self):
        t = self._transformer()
        for expr in ("col + 0.06", "col - 0.01", "col * 0.05", "col / 0.07"):
            result = t.fix_type_casts(expr)
            assert result == expr, f"fix_type_casts corrupted {expr!r} -> {result!r}"

    def test_arithmetic_pure_zero_decimal_is_cast(self):
        t = self._transformer()
        for expr in ("col + 0.0", "col - 0.00"):
            result = t.fix_type_casts(expr)
            assert "CAST(0 AS Decimal" in result, f"fix_type_casts should cast pure-zero in {expr!r}"
            assert result.count("CAST") == 1, f"Unexpected extra CASTs in {result!r}"

    def test_then_non_zero_decimal_not_corrupted(self):
        t = self._transformer()
        for expr in ("THEN 0.06", "THEN 0.05", "THEN 0.07"):
            result = t.fix_type_casts(expr)
            assert result == expr, f"fix_type_casts corrupted {expr!r} -> {result!r}"

    def test_else_non_zero_decimal_not_corrupted(self):
        t = self._transformer()
        for expr in ("ELSE 0.06", "ELSE 0.07", "ELSE 0.01"):
            result = t.fix_type_casts(expr)
            assert result == expr, f"fix_type_casts corrupted {expr!r} -> {result!r}"

    def test_tpchavoc_q6_between_clause_preserved(self):
        t = self._transformer()
        sql = "WHERE l_discount between 0.06 - 0.01 and 0.06 + 0.01"
        result = t.fix_type_casts(sql)
        assert result == sql, f"fix_type_casts corrupted Q6 BETWEEN clause: {result!r}"

    def test_full_transform_preserves_q6_decimal_literals(self):
        t = self._transformer()
        sql = (
            "SELECT SUM(l_extendedprice * l_discount) AS revenue "
            "FROM lineitem "
            "WHERE l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01"
        )
        result = t.transform(sql)
        assert "0.06" in result, f"0.06 was stripped from SQL: {result!r}"
        assert "0.01" in result, f"0.01 was stripped from SQL: {result!r}"
        import re

        assert not re.search(r"Decimal\(15,2\)\)\d", result), f"Dangling digit after CAST: {result!r}"


class TestClickHouseQueryTransformerSettings:
    def _transformer(self):
        from benchbox.platforms.clickhouse.query_transformer import ClickHouseQueryTransformer

        return ClickHouseQueryTransformer()

    def test_settings_not_appended_to_non_select(self):
        t = self._transformer()
        ddl = "CREATE TABLE foo (id Int32) ENGINE = MergeTree() ORDER BY id"
        assert t.add_query_settings(ddl) == ddl

    def test_settings_include_joined_subquery_alias(self):
        t = self._transformer()
        result = t.add_query_settings("SELECT 1")
        assert "joined_subquery_requires_alias = 0" in result

    def test_additional_settings_share_the_statement_settings_clause(self):
        t = self._transformer()
        result = t.add_query_settings(
            "SELECT 1",
            (("join_algorithm", "grace_hash"), ("grace_hash_join_initial_buckets", 8)),
        )

        assert result.count(" SETTINGS ") == 1
        assert "join_algorithm = 'grace_hash'" in result
        assert "grace_hash_join_initial_buckets = 8" in result

    def test_enable_analyzer_not_added_for_plain_select(self):
        t = self._transformer()
        result = t.add_query_settings("SELECT count(*) FROM store_sales")
        assert "enable_analyzer" not in result

    def test_enable_analyzer_not_added_for_avg_sum_window_pattern(self):
        t = self._transformer()
        q47_like = (
            "WITH v1 AS (SELECT d_year, SUM(ss_sales_price) AS sum_sales, "
            "AVG(SUM(ss_sales_price)) OVER (PARTITION BY i_category, d_year) AS avg_sales "
            "FROM store_sales GROUP BY d_year, i_category) SELECT * FROM v1"
        )
        result = t.add_query_settings(q47_like)
        assert "enable_analyzer" not in result

    def test_enable_analyzer_not_added_for_q66_alias_aggregate_pattern(self):
        t = self._transformer()
        q66_like = (
            "SELECT w_warehouse_name, sum(jan_sales) as jan_sales "
            "FROM (SELECT w_warehouse_name, sum(CASE WHEN d_moy=1 THEN ws_ext_list_price ELSE 0 END) as jan_sales "
            "FROM web_sales JOIN date_dim ON ws_sold_date_sk = d_date_sk "
            "UNION ALL SELECT w_warehouse_name, sum(CASE WHEN d_moy=1 THEN cs_ext_list_price ELSE 0 END) as jan_sales "
            "FROM catalog_sales JOIN date_dim ON cs_sold_date_sk = d_date_sk) "
            "GROUP BY w_warehouse_name"
        )
        result = t.add_query_settings(q66_like)
        assert "enable_analyzer" not in result


class TestClickHouseQueryTransformerDecimalDivision:
    def _transformer(self):
        from benchbox.platforms.clickhouse.query_transformer import ClickHouseQueryTransformer

        return ClickHouseQueryTransformer()

    def test_nullable_decimal_divisor_is_wrapped(self):
        t = self._transformer()
        sql = "SELECT a / CAST(b AS Nullable(Decimal(15, 4))) FROM t"
        result = t.fix_decimal_division_by_zero(sql)
        assert "NULLIF(CAST(b AS Nullable(Decimal(15, 4))), 0)" in result

    def test_non_nullable_decimal_not_wrapped(self):
        t = self._transformer()
        sql = "SELECT a / CAST(b AS Decimal(15, 4)) FROM t"
        result = t.fix_decimal_division_by_zero(sql)
        assert "NULLIF" not in result
        assert result == sql

    def test_transformation_recorded(self):
        t = self._transformer()
        sql = "SELECT x / CAST(y AS Nullable(Decimal(18, 2))) FROM t"
        t.fix_decimal_division_by_zero(sql)
        assert "decimal_division_fix" in t.transformations_applied

    def test_no_transformation_when_pattern_absent(self):
        t = self._transformer()
        sql = "SELECT a / b FROM t"
        t.fix_decimal_division_by_zero(sql)
        assert "decimal_division_fix" not in t.transformations_applied


class TestClickHouseQueryTransformerSafeDivision:
    def _transformer(self):
        from benchbox.platforms.clickhouse.query_transformer import ClickHouseQueryTransformer

        return ClickHouseQueryTransformer()

    def test_parenthesized_divisor_is_wrapped_as_a_whole(self):
        t = self._transformer()
        sql = "SELECT revenue / ((store_sales + web_sales) / 2) FROM metrics"
        result = t.safe_division(sql)
        assert "revenue / NULLIF(((store_sales + web_sales) / 2), 0)" in result

    def test_numeric_literal_divisor_stays_untouched(self):
        t = self._transformer()
        sql = "SELECT total_sales / 12 FROM metrics"
        result = t.safe_division(sql)
        assert result == sql

    def test_function_call_divisor_is_wrapped(self):
        t = self._transformer()
        sql = "SELECT total / COUNT(*) FROM t"
        result = t.safe_division(sql)
        assert "total / NULLIF(COUNT(*), 0)" in result

    def test_sum_function_divisor_is_wrapped(self):
        t = self._transformer()
        sql = "SELECT revenue / SUM(quantity) FROM t"
        result = t.safe_division(sql)
        assert "revenue / NULLIF(SUM(quantity), 0)" in result

    def test_already_nullif_wrapped_divisor_stays_untouched(self):
        t = self._transformer()
        sql = "SELECT a / NULLIF(b, 0) FROM t"
        result = t.safe_division(sql)
        assert "a / NULLIF(b, 0)" in result
        assert "NULLIF(NULLIF" not in result

    def test_multiple_divisions_in_one_query(self):
        t = self._transformer()
        sql = "SELECT a / b, c / d FROM t"
        result = t.safe_division(sql)
        assert "a / NULLIF(b, 0)" in result
        assert "c / NULLIF(d, 0)" in result

    def test_division_inside_string_literal_not_transformed(self):
        t = self._transformer()
        sql = "SELECT 'a/b' AS label, x / y FROM t"
        result = t.safe_division(sql)
        assert "'a/b'" in result
        assert "x / NULLIF(y, 0)" in result

    def test_windowed_divisor_wraps_whole_window_expression(self):
        t = self._transformer()
        sql = "SELECT mkt / SUM(volume) OVER (PARTITION BY o_year) FROM t"
        result = t.safe_division(sql)
        assert "mkt / NULLIF(SUM(volume) OVER (PARTITION BY o_year), 0)" in result
        assert "NULLIF(SUM(volume), 0) OVER" not in result


class TestClickHouseQueryTransformerCaseNormalization:
    def _transformer(self):
        from benchbox.platforms.clickhouse.query_transformer import ClickHouseQueryTransformer

        return ClickHouseQueryTransformer()

    def test_q1_fee_column_lowercased(self):
        t = self._transformer()
        result = t.normalize_case("SELECT SUM(SR_FEE) AS ctr_total_return FROM store_returns")
        assert "SUM(sr_fee)" in result
        assert "SR_FEE" not in result

    def test_q1_agg_field_variants_lowercased(self):
        t = self._transformer()
        for column in (
            "SR_RETURN_AMT",
            "SR_FEE",
            "SR_REFUNDED_CASH",
            "SR_RETURN_AMT_INC_TAX",
            "SR_REVERSED_CHARGE",
            "SR_STORE_CREDIT",
            "SR_RETURN_TAX",
        ):
            result = t.normalize_case(f"SELECT SUM({column}) FROM store_returns")
            assert f"SUM({column.lower()})" in result
            assert column not in result
