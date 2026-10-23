from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import benchbox.platforms.presto as presto_module
from benchbox.core.joinorder.schema import JoinOrderSchema
from benchbox.platforms.presto import PRESTO_DIALECT, PrestoAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def presto_stubs(monkeypatch):

    mock_dbapi = Mock()
    prestodb_mock = SimpleNamespace(dbapi=mock_dbapi, __version__="0.999.0")
    auth_factory = Mock(return_value="auth-token")

    monkeypatch.setattr(presto_module, "prestodb", prestodb_mock)
    monkeypatch.setattr(presto_module, "PrestoBasicAuthentication", auth_factory)

    return prestodb_mock, auth_factory


class TestPrestoAdapter:
    def test_initialization_defaults(self, presto_stubs):

        adapter = PrestoAdapter(catalog="hive")

        assert adapter.platform_name == "Presto"
        assert adapter.get_target_dialect() == PRESTO_DIALECT
        assert adapter.host == "localhost"
        assert adapter.port == 8080
        assert adapter.catalog == "hive"
        assert adapter.schema == "default"
        assert adapter.http_scheme == "http"

    def test_initialization_without_catalog(self, presto_stubs):

        adapter = PrestoAdapter()

        assert adapter.catalog is None
        assert adapter.host == "localhost"
        assert adapter.port == 8080

    def test_get_connection_params_includes_auth_and_timeout(self, presto_stubs):

        _, auth_factory = presto_stubs
        adapter = PrestoAdapter(
            host="presto.example.com",
            port=8443,
            catalog="hive",
            schema="analytics",
            username="presto_user",
            password="secret",
            query_timeout=30,
            session_properties={"join_distribution_type": "AUTOMATIC"},
            ssl_cert_path="/tmp/cert.pem",
        )

        params = adapter._get_connection_params()

        auth_factory.assert_called_once_with("presto_user", "secret")
        assert params["host"] == "presto.example.com"
        assert params["port"] == 8443
        assert params["catalog"] == "hive"
        assert params["schema"] == "analytics"
        assert params["user"] == "presto_user"
        assert params["auth"] == "auth-token"
        assert params["session_properties"] == {"join_distribution_type": "AUTOMATIC"}
        assert params["request_timeout"] == 30
        assert params["source"] == "BenchBox"
        assert params["requests_kwargs"] == {"verify": "/tmp/cert.pem"}

    def test_check_server_database_exists_true(self, presto_stubs, monkeypatch):

        prestodb_mock, _ = presto_stubs
        mock_conn = Mock()
        mock_cursor = Mock()

        mock_cursor.fetchall.return_value = [("hive",), ("system",)]
        mock_cursor.fetchone.return_value = ("default",)
        mock_conn.cursor.return_value = mock_cursor
        prestodb_mock.dbapi.connect.return_value = mock_conn

        adapter = PrestoAdapter(catalog="hive", schema="default")

        assert adapter.check_server_database_exists(schema="default", catalog="hive") is True
        prestodb_mock.dbapi.connect.assert_called()

    def test_check_server_database_exists_false_on_error(self, presto_stubs):

        prestodb_mock, _ = presto_stubs
        prestodb_mock.dbapi.connect.side_effect = Exception("connection failed")

        adapter = PrestoAdapter(catalog="hive")

        assert adapter.check_server_database_exists(schema="missing", catalog="hive") is False

    def test_validate_catalog_raises_on_missing_catalog(self, presto_stubs):

        from benchbox.core.exceptions import ConfigurationError

        prestodb_mock, _ = presto_stubs
        mock_conn = Mock()
        mock_cursor = Mock()

        mock_cursor.fetchall.return_value = [("hive",), ("system",), ("iceberg",)]
        mock_conn.cursor.return_value = mock_cursor
        prestodb_mock.dbapi.connect.return_value = mock_conn

        adapter = PrestoAdapter(catalog="memory", schema="default")

        with pytest.raises(ConfigurationError) as exc_info:
            adapter._validate_catalog_exists("memory")

        error_msg = str(exc_info.value)
        assert "memory" in error_msg
        assert "does not exist" in error_msg
        assert "hive" in error_msg
        assert "--platform-option catalog=" in error_msg

    def test_get_available_catalogs(self, presto_stubs):

        prestodb_mock, _ = presto_stubs
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [("hive",), ("system",), ("memory",)]
        mock_conn.cursor.return_value = mock_cursor
        prestodb_mock.dbapi.connect.return_value = mock_conn

        adapter = PrestoAdapter()
        catalogs = adapter._get_available_catalogs()

        assert catalogs == ["hive", "system", "memory"]
        mock_cursor.execute.assert_called_with("SHOW CATALOGS")

    def test_auto_select_catalog_prefers_hive(self, presto_stubs):

        prestodb_mock, _ = presto_stubs
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [("memory",), ("hive",), ("system",)]
        mock_conn.cursor.return_value = mock_cursor
        prestodb_mock.dbapi.connect.return_value = mock_conn

        adapter = PrestoAdapter()
        selected = adapter._auto_select_catalog()

        assert selected == "hive"

    def test_auto_select_catalog_fallback_to_memory(self, presto_stubs):

        prestodb_mock, _ = presto_stubs
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [("memory",), ("system",)]
        mock_conn.cursor.return_value = mock_cursor
        prestodb_mock.dbapi.connect.return_value = mock_conn

        adapter = PrestoAdapter()
        selected = adapter._auto_select_catalog()

        assert selected == "memory"

    def test_auto_select_catalog_uses_first_available(self, presto_stubs):

        prestodb_mock, _ = presto_stubs
        mock_conn = Mock()
        mock_cursor = Mock()

        mock_cursor.fetchall.return_value = [("custom_catalog",), ("system",)]
        mock_conn.cursor.return_value = mock_cursor
        prestodb_mock.dbapi.connect.return_value = mock_conn

        adapter = PrestoAdapter()
        selected = adapter._auto_select_catalog()

        assert selected == "custom_catalog"

    def test_auto_select_catalog_only_system_catalogs(self, presto_stubs):

        prestodb_mock, _ = presto_stubs
        mock_conn = Mock()
        mock_cursor = Mock()

        mock_cursor.fetchall.return_value = [("jmx",), ("system",)]
        mock_conn.cursor.return_value = mock_cursor
        prestodb_mock.dbapi.connect.return_value = mock_conn

        adapter = PrestoAdapter()
        selected = adapter._auto_select_catalog()

        assert selected is None

    def test_auto_select_catalog_server_unreachable(self, presto_stubs):

        prestodb_mock, _ = presto_stubs
        prestodb_mock.dbapi.connect.side_effect = Exception("Connection refused")

        adapter = PrestoAdapter()
        selected = adapter._auto_select_catalog()

        assert selected is None

    def test_validation_auto_selects_when_none(self, presto_stubs):

        prestodb_mock, _ = presto_stubs
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [("hive",), ("memory",), ("system",)]
        mock_conn.cursor.return_value = mock_cursor
        prestodb_mock.dbapi.connect.return_value = mock_conn

        adapter = PrestoAdapter()
        validated = adapter._validate_catalog_exists(None)

        assert validated == "hive"
        assert adapter._catalog_was_auto_selected is True

    def test_validation_raises_when_server_unreachable(self, presto_stubs):

        from benchbox.core.exceptions import ConfigurationError

        prestodb_mock, _ = presto_stubs
        prestodb_mock.dbapi.connect.side_effect = Exception("Connection refused")

        adapter = PrestoAdapter()

        with pytest.raises(ConfigurationError) as exc_info:
            adapter._validate_catalog_exists(None)

        assert "server is unreachable" in str(exc_info.value)

    def test_validation_raises_when_only_system_catalogs(self, presto_stubs):

        from benchbox.core.exceptions import ConfigurationError

        prestodb_mock, _ = presto_stubs
        mock_conn = Mock()
        mock_cursor = Mock()

        mock_cursor.fetchall.return_value = [("jmx",), ("system",)]
        mock_conn.cursor.return_value = mock_cursor
        prestodb_mock.dbapi.connect.return_value = mock_conn

        adapter = PrestoAdapter()

        with pytest.raises(ConfigurationError) as exc_info:
            adapter._validate_catalog_exists(None)

        assert "No usable data catalogs found" in str(exc_info.value)
        assert "jmx, system" in str(exc_info.value)

    def test_drop_database_executes_cascade(self, presto_stubs):

        prestodb_mock, _ = presto_stubs
        mock_conn = Mock()
        mock_cursor = Mock()

        mock_cursor.fetchall.side_effect = [
            [("memory",), ("system",)],
            [("table1",), ("table2",)],
        ]
        mock_cursor.fetchone.return_value = ("test_schema",)
        mock_conn.cursor.return_value = mock_cursor
        prestodb_mock.dbapi.connect.return_value = mock_conn

        adapter = PrestoAdapter(catalog="memory", schema="test_schema")

        adapter.drop_database(schema="test_schema", catalog="memory")

        executed = " ".join(call.args[0] for call in mock_cursor.execute.call_args_list)

        assert "DROP TABLE IF EXISTS memory.test_schema.table1" in executed
        assert "DROP TABLE IF EXISTS memory.test_schema.table2" in executed
        assert "DROP SCHEMA IF EXISTS memory.test_schema" in executed

    def test_create_connection_uses_session_properties(self, presto_stubs):

        prestodb_mock, _ = presto_stubs
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor
        prestodb_mock.dbapi.connect.return_value = mock_conn

        adapter = PrestoAdapter(
            catalog="hive",
            schema="analytics",
            session_properties={"join_reordering_strategy": "AUTOMATIC"},
            query_timeout=15,
        )
        adapter.database_was_reused = True

        with patch.object(adapter, "handle_existing_database"):
            connection = adapter.create_connection()

        prestodb_mock.dbapi.connect.assert_called_with(
            host="localhost",
            port=8080,
            user="presto",
            catalog="hive",
            schema="analytics",
            http_scheme="http",
            source="BenchBox",
            session_properties={"join_reordering_strategy": "AUTOMATIC"},
            request_timeout=15,
        )
        assert connection is mock_conn

    def test_get_platform_info_fallback_version(self, presto_stubs):

        prestodb_mock, _ = presto_stubs
        mock_cursor = Mock()
        mock_connection = Mock()
        mock_connection.cursor.return_value = mock_cursor

        def execute_side_effect(sql):
            if "node_version" in sql:
                raise Exception("runtime table unavailable")
            if "SELECT version()" in sql:
                mock_cursor.fetchone.return_value = ("0.280",)
            elif "SELECT count(*)" in sql:
                mock_cursor.fetchone.return_value = (3,)
            elif "SHOW CATALOGS" in sql:
                mock_cursor.fetchall.return_value = [("hive",), ("system",)]

        mock_cursor.execute.side_effect = execute_side_effect

        adapter = PrestoAdapter(catalog="hive", schema="analytics")

        info = adapter.get_platform_info(connection=mock_connection)

        assert info["platform_version"] == "0.280"
        assert info["configuration"]["node_count"] == 3
        assert info["configuration"]["available_catalogs"] == ["hive", "system"]
        assert info["dialect"] == PRESTO_DIALECT
        assert info["configuration"]["client_source"] == "BenchBox"

    def test_load_data_qualifies_table_names(self, presto_stubs, tmp_path):

        prestodb_mock, _ = presto_stubs
        mock_cursor = Mock()
        mock_connection = Mock()
        mock_connection.cursor.return_value = mock_cursor
        prestodb_mock.dbapi.connect.return_value = mock_connection

        data_file = tmp_path / "lineitem.tbl"
        data_file.write_text("1|hello|\n2|world|\n")

        class Benchmark:
            tables = {"Orders": data_file}

        adapter = PrestoAdapter(catalog="hive", schema="analytics")

        stats, _, _ = adapter.load_data(Benchmark(), mock_connection, tmp_path)

        assert stats["orders"] == 2
        executed_sql = " ".join(call.args[0] for call in mock_cursor.execute.call_args_list)
        assert "INSERT INTO hive.analytics.orders VALUES" in executed_sql

    def test_load_data_skips_invalid_identifier(self, presto_stubs, tmp_path):

        prestodb_mock, _ = presto_stubs
        mock_cursor = Mock()
        mock_connection = Mock()
        mock_connection.cursor.return_value = mock_cursor
        prestodb_mock.dbapi.connect.return_value = mock_connection

        data_file = tmp_path / "bad.tbl"
        data_file.write_text("1|bad|\n")

        class Benchmark:
            tables = {"bad table": data_file}

        adapter = PrestoAdapter(catalog="hive", schema="analytics")

        stats, _, _ = adapter.load_data(Benchmark(), mock_connection, tmp_path)

        assert stats["bad table"] == 0
        assert mock_cursor.execute.call_count == 0

    def test_external_table_mode_requires_staging_root(self, presto_stubs):

        adapter = PrestoAdapter(catalog="hive", schema="analytics")

        with pytest.raises(ValueError, match="requires --platform-option staging_root"):
            adapter.validate_external_table_requirements()

    def test_create_external_tables_generates_location_sql(self, presto_stubs):

        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = (12,)
        mock_connection = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter = PrestoAdapter(catalog="hive", schema="analytics", staging_root="s3://benchbox/staging")

        benchmark = Mock()
        benchmark.tables = {"orders": [Path("/tmp/orders.parquet")]}
        benchmark.get_schema.return_value = {
            "orders": {
                "columns": [
                    {"name": "o_orderkey", "type": "BIGINT"},
                    {"name": "o_totalprice", "type": "DECIMAL(15,2)"},
                ]
            }
        }

        stats, _, _ = adapter.create_external_tables(benchmark, mock_connection, Path("/tmp"))

        assert stats["orders"] == 12
        executed_sql = " ".join(call.args[0] for call in mock_cursor.execute.call_args_list)
        assert "CREATE TABLE hive.analytics.orders" in executed_sql
        assert "external_location = 's3://benchbox/staging/orders/'" in executed_sql
        assert "format = 'PARQUET'" in executed_sql

    def test_load_data_does_not_invoke_external_registration(self, presto_stubs, tmp_path):

        mock_cursor = Mock()
        mock_connection = Mock()
        mock_connection.cursor.return_value = mock_cursor

        data_file = tmp_path / "orders.tbl"
        data_file.write_text("1|hello|\n")

        class Benchmark:
            tables = {"orders": data_file}

        adapter = PrestoAdapter(catalog="hive", schema="analytics")
        with patch.object(
            adapter,
            "create_external_tables",
            side_effect=AssertionError("native load_data should not call create_external_tables"),
        ):
            stats, _, _ = adapter.load_data(Benchmark(), mock_connection, tmp_path)

        assert stats["orders"] == 1

    def test_dialect_is_presto_not_trino(self, presto_stubs):

        adapter = PrestoAdapter()

        assert adapter.get_target_dialect() == PRESTO_DIALECT
        assert adapter.get_target_dialect() != "trino"

    def test_from_config_with_schema(self, presto_stubs):

        config = {
            "schema": "custom_schema",
            "benchmark": "tpch",
            "scale_factor": 1.0,
            "host": "presto.example.com",
            "port": 8443,
            "catalog": "hive",
        }

        adapter = PrestoAdapter.from_config(config)

        assert adapter.schema == "custom_schema"
        assert adapter.host == "presto.example.com"
        assert adapter.port == 8443
        assert adapter.catalog == "hive"

    def test_from_config_generates_schema_name(self, presto_stubs):

        config = {
            "benchmark": "tpch",
            "scale_factor": 1.0,
        }

        adapter = PrestoAdapter.from_config(config)

        assert adapter.schema is not None
        assert "tpch" in adapter.schema.lower()

    def test_validate_identifier_rejects_invalid(self, presto_stubs):

        adapter = PrestoAdapter()

        assert adapter._validate_identifier("valid_name") is True
        assert adapter._validate_identifier("valid-name") is True
        assert adapter._validate_identifier("ValidName123") is True
        assert adapter._validate_identifier("") is False
        assert adapter._validate_identifier("table; DROP TABLE users") is False
        assert adapter._validate_identifier("123invalid") is False
        assert adapter._validate_identifier("a" * 200) is False

    def test_execute_query_success(self, presto_stubs):

        prestodb_mock, _ = presto_stubs
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [(1, "test"), (2, "test2")]
        mock_connection = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter = PrestoAdapter()

        result = adapter.execute_query(mock_connection, "SELECT * FROM test", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 2
        assert isinstance(result["execution_time_seconds"], float)
        mock_cursor.execute.assert_called_once_with("SELECT * FROM test")

    def test_execute_query_failure(self, presto_stubs):

        prestodb_mock, _ = presto_stubs
        mock_cursor = Mock()
        mock_cursor.execute.side_effect = Exception("Query failed")
        mock_connection = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter = PrestoAdapter()

        result = adapter.execute_query(mock_connection, "INVALID SQL", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "FAILED"
        assert result["rows_returned"] == 0
        assert "Query failed" in result["error"]

    def test_get_query_plan(self, presto_stubs):

        prestodb_mock, _ = presto_stubs
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [("Scan Table",), ("Filter",)]
        mock_connection = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter = PrestoAdapter()

        plan = adapter.get_query_plan(mock_connection, "SELECT * FROM test")

        assert "Scan Table" in plan
        assert "Filter" in plan
        mock_cursor.execute.assert_called_once()

    def test_get_query_plan_error(self, presto_stubs):

        prestodb_mock, _ = presto_stubs
        mock_cursor = Mock()
        mock_cursor.execute.side_effect = Exception("EXPLAIN failed")
        mock_connection = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter = PrestoAdapter()

        plan = adapter.get_query_plan(mock_connection, "SELECT * FROM test")

        assert plan is None

    def test_close_connection(self, presto_stubs):

        mock_connection = Mock()
        adapter = PrestoAdapter()

        adapter.close_connection(mock_connection)

        mock_connection.close.assert_called_once()

    def test_close_connection_handles_none(self, presto_stubs):

        adapter = PrestoAdapter()

        adapter.close_connection(None)

    def test_test_connection_success(self, presto_stubs):

        prestodb_mock, _ = presto_stubs
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = (1,)
        mock_connection = Mock()
        mock_connection.cursor.return_value = mock_cursor
        prestodb_mock.dbapi.connect.return_value = mock_connection

        adapter = PrestoAdapter()

        result = adapter.test_connection()

        assert result is True
        prestodb_mock.dbapi.connect.assert_called()

    def test_test_connection_failure(self, presto_stubs):

        prestodb_mock, _ = presto_stubs
        prestodb_mock.dbapi.connect.side_effect = Exception("Connection refused")

        adapter = PrestoAdapter()

        result = adapter.test_connection()

        assert result is False

    def test_supports_tuning_type(self, presto_stubs):

        adapter = PrestoAdapter()

        with patch("benchbox.core.tuning.interface.TuningType") as mock_tuning_type:
            mock_tuning_type.PARTITIONING = "partitioning"
            mock_tuning_type.SORTING = "sorting"

            assert adapter.supports_tuning_type(mock_tuning_type.PARTITIONING) is True
            assert adapter.supports_tuning_type(mock_tuning_type.SORTING) is False

    def test_generate_tuning_clause_empty(self, presto_stubs):

        adapter = PrestoAdapter()

        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = False

        clause = adapter.generate_tuning_clause(mock_tuning)
        assert clause == ""

    def test_generate_tuning_clause_partitioning(self, presto_stubs):

        adapter = PrestoAdapter(table_format="hive")

        mock_col = Mock()
        mock_col.name = "date_col"
        mock_col.order = 1

        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True

        with patch("benchbox.core.tuning.interface.TuningType") as mock_tuning_type:
            mock_tuning_type.PARTITIONING = "partitioning"
            mock_tuning.get_columns_by_type.return_value = [mock_col]

            clause = adapter.generate_tuning_clause(mock_tuning)
            assert "PARTITIONED BY" in clause
            assert "date_col" in clause

    def test_configure_for_benchmark_olap(self, presto_stubs):

        prestodb_mock, _ = presto_stubs
        mock_cursor = Mock()
        mock_connection = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter = PrestoAdapter()

        adapter.configure_for_benchmark(mock_connection, "olap")

        executed_sql = [str(call) for call in mock_cursor.execute.call_args_list]
        assert any("optimize_hash_generation" in sql for sql in executed_sql)
        assert any("join_reordering_strategy" in sql for sql in executed_sql)

    def test_schema_ddl_lowercases_table_names(self, presto_stubs):

        adapter = PrestoAdapter()

        [translated] = adapter._schema_ddl('CREATE TABLE "CUSTOMER" (id INTEGER)')

        assert translated.startswith('CREATE TABLE "customer"')

    def test_schema_ddl_memory(self, presto_stubs):

        adapter = PrestoAdapter(table_format="memory")

        [translated] = adapter._schema_ddl("CREATE TABLE kind_type (id INTEGER PRIMARY KEY, kind VARCHAR(15) NOT NULL)")

        assert "PRIMARY KEY" not in translated
        assert "NOT NULL" not in translated
        assert "WITH" not in translated
        assert '"id" INTEGER' in translated

        schema_sql = JoinOrderSchema().get_create_tables_sql("duckdb")
        translated = adapter._schema_ddl(schema_sql)

        assert len(translated) == 21
        assert all("PRIMARY KEY" not in statement for statement in translated)

    def test_schema_ddl_hive(self, presto_stubs):

        adapter = PrestoAdapter(table_format="hive")

        [translated] = adapter._schema_ddl("CREATE TABLE test (id INTEGER)")
        assert translated.endswith("WITH (format='PARQUET')")

        [translated] = adapter._schema_ddl("CREATE TABLE kind_type (id INTEGER PRIMARY KEY, kind VARCHAR(15) NOT NULL)")

        assert "PRIMARY KEY" not in translated
        assert "NOT NULL" not in translated
        assert translated.endswith("WITH (format='PARQUET')")

    def test_schema_ddl_memory_catalog_name(self, presto_stubs):

        adapter = PrestoAdapter(catalog="memory", table_format="hive")

        [translated] = adapter._schema_ddl("CREATE TABLE test (id INTEGER PRIMARY KEY, kind VARCHAR(15) NOT NULL)")

        assert "PRIMARY KEY" not in translated
        assert "NOT NULL" not in translated
        assert "WITH" not in translated

    def test_analyze_table_memory_catalog(self, presto_stubs):

        prestodb_mock, _ = presto_stubs
        mock_cursor = Mock()
        mock_connection = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter = PrestoAdapter(table_format="memory")

        adapter.analyze_table(mock_connection, "test_table")

        mock_cursor.execute.assert_not_called()

    def test_analyze_table_hive_catalog(self, presto_stubs):

        prestodb_mock, _ = presto_stubs
        mock_cursor = Mock()
        mock_connection = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter = PrestoAdapter(table_format="hive", catalog="hive", schema="default")

        adapter.analyze_table(mock_connection, "test_table")

        mock_cursor.execute.assert_called()
        call_args = str(mock_cursor.execute.call_args)
        assert "ANALYZE" in call_args
        assert "test_table" in call_args

    def test_get_existing_tables(self, presto_stubs):

        prestodb_mock, _ = presto_stubs
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [("orders",), ("lineitem",)]
        mock_connection = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter = PrestoAdapter()

        tables = adapter._get_existing_tables(mock_connection)

        assert "orders" in tables
        assert "lineitem" in tables
        mock_cursor.execute.assert_called_with("SHOW TABLES")

    def test_apply_table_tunings_logs_partitioning(self, presto_stubs):

        adapter = PrestoAdapter()
        mock_connection = Mock()

        mock_col = Mock()
        mock_col.name = "date_col"
        mock_col.order = 1

        mock_tuning = Mock()
        mock_tuning.table_name = "orders"
        mock_tuning.has_any_tuning.return_value = True

        with patch("benchbox.core.tuning.interface.TuningType") as mock_tuning_type:
            mock_tuning_type.PARTITIONING = "partitioning"
            mock_tuning.get_columns_by_type.return_value = [mock_col]

            adapter.apply_table_tunings(mock_tuning, mock_connection)

    def test_extract_table_name(self, presto_stubs):

        adapter = PrestoAdapter()

        assert adapter._extract_table_name("CREATE TABLE orders (id INT)") == "orders"
        assert adapter._extract_table_name("CREATE TABLE IF NOT EXISTS orders (id INT)") == "orders"
        assert adapter._extract_table_name("INSERT INTO orders VALUES (1)") is None

    def test_load_table_data_uses_manifest_dialect_over_suffix(self, presto_stubs, tmp_path):

        from benchbox.platforms.base.data_loading import DataSource

        adapter = PrestoAdapter(catalog="hive")
        data_file = tmp_path / "orders.tbl"
        data_file.write_text("1,Alice\n")
        data_source = DataSource(
            source_type="manifest",
            tables={"orders": data_file},
            table_metadata={"orders": {"csv_delimiter": ","}},
        )
        cursor = Mock()
        with (
            patch.object(adapter, "_validate_identifier", return_value=True),
            patch("benchbox.platforms.presto_trino_adapter_base.load_file_batches") as batches,
        ):
            batches.return_value = 1
            result = adapter._load_table_data(
                cursor,
                "orders",
                data_file,
                "hive",
                "default",
                benchmark=Mock(tables={"orders": data_file}),
                data_source=data_source,
            )
        assert result == (1, 1)
        assert batches.call_args.kwargs["delimiter"] == ","

    def test_load_table_data_without_context_keeps_heuristic(self, presto_stubs, tmp_path):

        adapter = PrestoAdapter(catalog="hive")
        data_file = tmp_path / "orders.tbl"
        data_file.write_text("1|Alice\n")
        cursor = Mock()
        with (
            patch.object(adapter, "_validate_identifier", return_value=True),
            patch("benchbox.platforms.presto_trino_adapter_base.load_file_batches") as batches,
        ):
            batches.return_value = 1
            result = adapter._load_table_data(cursor, "orders", data_file, "hive", "default")
        assert result == (1, 1)
        assert batches.call_args.kwargs["delimiter"] is None
