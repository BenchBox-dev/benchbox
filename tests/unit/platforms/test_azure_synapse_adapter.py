# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import Mock, patch

import pytest

import benchbox.platforms.azure_synapse as synapse_module
from benchbox.platforms.azure_synapse import SYNAPSE_DIALECT, AzureSynapseAdapter
from benchbox.platforms.base.data_loading import DataSource

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def synapse_stubs(monkeypatch):
    mock_pyodbc = Mock()
    mock_pyodbc.connect = Mock()

    monkeypatch.setattr(synapse_module, "pyodbc", mock_pyodbc)

    monkeypatch.setattr(
        synapse_module,
        "check_platform_dependencies",
        lambda platform, packages=None: (True, []),
    )

    return mock_pyodbc


class TestAzureSynapseAdapter:
    def test_initialization_defaults(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="myworkspace.sql.azuresynapse.net",
            username="admin",
            password="secret",
        )

        assert adapter.platform_name == "Azure Synapse"
        assert adapter.get_target_dialect() == SYNAPSE_DIALECT
        assert adapter.server == "myworkspace.sql.azuresynapse.net"
        assert adapter.port == 1433
        assert adapter.database == "benchbox"
        assert adapter.username == "admin"
        assert adapter.schema == "dbo"
        assert adapter.auth_method == "sql"

    def test_initialization_with_config(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="custom.sql.azuresynapse.net",
            port=1434,
            database="custom_db",
            username="custom_user",
            password="secret",
            schema="analytics",
            auth_method="aad_password",
            resource_class="staticrc30",
        )

        assert adapter.server == "custom.sql.azuresynapse.net"
        assert adapter.port == 1434
        assert adapter.database == "custom_db"
        assert adapter.username == "custom_user"
        assert adapter.password == "secret"
        assert adapter.schema == "analytics"
        assert adapter.auth_method == "aad_password"
        assert adapter.resource_class == "staticrc30"

    def test_get_connection_string_sql_auth(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            port=1433,
            database="testdb",
            username="testuser",
            password="testpass",
            auth_method="sql",
        )

        conn_str = adapter._get_connection_string()

        assert "test.sql.azuresynapse.net" in conn_str
        assert "testdb" in conn_str
        assert "UID=testuser" in conn_str
        assert "PWD=testpass" in conn_str
        assert "Encrypt=yes" in conn_str

    def test_get_connection_string_aad_auth(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="user@domain.com",
            password="secret",
            auth_method="aad_password",
        )

        conn_str = adapter._get_connection_string()

        assert "Authentication=ActiveDirectoryPassword" in conn_str

    def test_get_connection_string_msi_auth(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            auth_method="aad_msi",
        )

        conn_str = adapter._get_connection_string()

        assert "Authentication=ActiveDirectoryMsi" in conn_str

    def test_check_server_database_exists_true(self, synapse_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = ("testdb",)
        mock_conn.cursor.return_value = mock_cursor
        synapse_stubs.connect.return_value = mock_conn

        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            database="testdb",
            username="admin",
            password="secret",
        )

        assert adapter.check_server_database_exists() is True

    def test_check_server_database_exists_false(self, synapse_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = None
        mock_conn.cursor.return_value = mock_cursor
        synapse_stubs.connect.return_value = mock_conn

        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            database="nonexistent",
            username="admin",
            password="secret",
        )

        assert adapter.check_server_database_exists() is False

    def test_check_server_database_exists_connection_error(self, synapse_stubs):
        synapse_stubs.connect.side_effect = Exception("Connection refused")

        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
        )

        assert adapter.check_server_database_exists() is False

    def test_create_connection_creates_database(self, synapse_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.side_effect = [None, ("version",)]
        mock_conn.cursor.return_value = mock_cursor
        synapse_stubs.connect.return_value = mock_conn

        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            database="newdb",
            username="admin",
            password="secret",
        )

        with (
            patch.object(adapter, "handle_existing_database"),
            patch.object(adapter, "check_server_database_exists", side_effect=[False, True]),
            patch.object(adapter, "_create_admin_connection", return_value=mock_conn),
        ):
            adapter.create_connection()

        executed_calls = mock_cursor.execute.call_args_list
        create_db_calls = [c for c in executed_calls if "CREATE DATABASE" in str(c)]
        assert len(create_db_calls) > 0

    def test_get_platform_info(self, synapse_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.side_effect = [
            ("Microsoft SQL Azure ...",),
            (100.0,),
        ]
        mock_conn.cursor.return_value = mock_cursor

        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            database="testdb",
            schema="analytics",
            username="admin",
            password="secret",
            resource_class="staticrc30",
        )

        info = adapter.get_platform_info(connection=mock_conn)

        assert info["platform_type"] == "azure_synapse"
        assert info["platform_name"] == "Azure Synapse"
        assert info["cloud_provider"] == "Azure"
        assert info["server"] == "test.sql.azuresynapse.net"
        assert info["dialect"] == SYNAPSE_DIALECT
        assert info["configuration"]["database"] == "testdb"
        assert info["configuration"]["schema"] == "analytics"
        assert info["configuration"]["resource_class"] == "staticrc30"

    def test_execute_query_success(self, synapse_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [(1, "test"), (2, "test2")]
        mock_conn.cursor.return_value = mock_cursor

        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
        )

        result = adapter.execute_query(mock_conn, "SELECT * FROM test", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 2
        assert result["first_row"] == (1, "test")
        assert isinstance(result["execution_time_seconds"], float)

    def test_execute_query_success_under_verbose_logging(self, synapse_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [(1,)]
        mock_conn.cursor.return_value = mock_cursor

        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
            verbose_level=1,
        )

        result = adapter.execute_query(mock_conn, "SELECT 1", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "SUCCESS"

    def test_execute_query_failure(self, synapse_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.side_effect = Exception("Query failed")
        mock_conn.cursor.return_value = mock_cursor

        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
        )

        result = adapter.execute_query(mock_conn, "INVALID SQL", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "FAILED"
        assert result["rows_returned"] == 0
        assert result["error"] == "Query failed"
        assert result["error_type"] == "Exception"

    def test_configure_for_benchmark_olap(self, synapse_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
            disable_result_cache=True,
        )

        adapter.configure_for_benchmark(mock_conn, "olap")

        executed = " ".join(str(call) for call in mock_cursor.execute.call_args_list)
        assert "RESULT_SET_CACHING OFF" in executed
        assert "ANSI_NULLS ON" in executed

    def test_get_existing_tables(self, synapse_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [("TABLE1",), ("table2",)]
        mock_conn.cursor.return_value = mock_cursor

        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            schema="dbo",
            username="admin",
            password="secret",
        )

        tables = adapter._get_existing_tables(mock_conn)

        assert tables == ["table1", "table2"]

    def test_test_connection_success(self, synapse_stubs):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor
        synapse_stubs.connect.return_value = mock_conn

        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
        )

        assert adapter.test_connection() is True

    def test_test_connection_failure(self, synapse_stubs):
        synapse_stubs.connect.side_effect = Exception("Connection refused")

        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
        )

        assert adapter.test_connection() is False

    def test_from_config_generates_database_name(self, synapse_stubs):
        config = {
            "server": "test.sql.azuresynapse.net",
            "username": "admin",
            "password": "secret",
            "benchmark": "tpch",
            "scale_factor": 10.0,
        }

        adapter = AzureSynapseAdapter.from_config(config)

        assert "tpch" in adapter.database.lower()

    def test_from_config_uses_provided_database(self, synapse_stubs):
        config = {
            "server": "test.sql.azuresynapse.net",
            "username": "admin",
            "password": "secret",
            "database": "my_custom_db",
            "benchmark": "tpch",
            "scale_factor": 10.0,
        }

        adapter = AzureSynapseAdapter.from_config(config)

        assert adapter.database == "my_custom_db"

    def test_supports_tuning_type(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
        )

        with patch("benchbox.core.tuning.interface.TuningType") as mock_tuning:
            mock_tuning.DISTRIBUTION = "distribution"
            mock_tuning.PARTITIONING = "partitioning"
            mock_tuning.INDEXING = "indexing"
            mock_tuning.SORTING = "sorting"

            assert adapter.supports_tuning_type(mock_tuning.DISTRIBUTION) is True
            assert adapter.supports_tuning_type(mock_tuning.PARTITIONING) is True
            assert adapter.supports_tuning_type(mock_tuning.INDEXING) is True
            assert adapter.supports_tuning_type(mock_tuning.SORTING) is False

    def test_close_connection(self, synapse_stubs):
        mock_conn = Mock()

        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
        )

        adapter.close_connection(mock_conn)

        mock_conn.close.assert_called_once()

    def test_dialect_is_tsql(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
        )

        assert adapter.get_target_dialect() == "tsql"
        assert adapter._dialect == "tsql"

    def test_extract_table_name(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
        )

        assert adapter._extract_table_name("CREATE TABLE test_table (id INT)") == "test_table"
        assert adapter._extract_table_name("CREATE TABLE [my_table] (id INT)") == "my_table"
        assert adapter._extract_table_name("SELECT * FROM test") is None

    def test_optimize_table_definition_adds_distribution(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
            distribution_default="ROUND_ROBIN",
        )

        statement = "CREATE TABLE test_table (id INT, name VARCHAR(100))"
        optimized = adapter._optimize_table_definition(statement)

        assert "DISTRIBUTION = ROUND_ROBIN" in optimized

    def test_missing_server_raises_error(self, synapse_stubs):
        from benchbox.core.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError, match="Azure Synapse SQL authentication is incomplete"):
            AzureSynapseAdapter(
                username="admin",
                password="secret",
            )

    def test_missing_password_raises_error(self, synapse_stubs):
        from benchbox.core.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError, match="Azure Synapse SQL authentication is incomplete"):
            AzureSynapseAdapter(
                server="test.sql.azuresynapse.net",
                username="admin",
            )


class TestAzureSynapseDataLoading:
    def test_load_data_direct_fallback(self, synapse_stubs, tmp_path):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = (3,)
        mock_conn.cursor.return_value = mock_cursor

        csv_file = tmp_path / "test_table.csv"
        csv_file.write_text("1,alice\n2,bob\n3,charlie\n")

        class Benchmark:
            tables = {"test_table": csv_file}

        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            schema="dbo",
            username="admin",
            password="secret",
        )

        stats, load_time, _ = adapter.load_data(Benchmark(), mock_conn, tmp_path)

        assert "test_table" in stats
        assert load_time >= 0

    def test_load_data_skips_empty_files(self, synapse_stubs, tmp_path):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        empty_file = tmp_path / "empty.csv"
        empty_file.write_text("")

        class Benchmark:
            tables = {"empty_table": empty_file}

        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            schema="dbo",
            username="admin",
            password="secret",
        )

        stats, _, _ = adapter.load_data(Benchmark(), mock_conn, tmp_path)

        assert stats.get("empty_table", 0) == 0

    def test_external_table_mode_requires_storage_config(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
        )
        assert adapter.supports_external_tables is True

        with pytest.raises(ValueError, match="requires blob storage configuration"):
            adapter.validate_external_table_requirements()

    def test_create_external_tables_generates_polybase_sql(self, synapse_stubs, tmp_path):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (15,)

        parquet_file = tmp_path / "orders.parquet"
        parquet_file.write_bytes(b"PAR1")

        class Benchmark:
            tables = {"orders": [parquet_file]}

            @staticmethod
            def get_schema():
                return {
                    "orders": {
                        "columns": [
                            {"name": "o_orderkey", "type": "BIGINT"},
                            {"name": "o_totalprice", "type": "DECIMAL(15,2)"},
                        ]
                    }
                }

        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            database="testdb",
            schema="dbo",
            username="admin",
            password="secret",
            storage_account="benchboxacct",
            container="benchbox",
            storage_sas_token="sv=2025-01-01&sig=fake",
        )

        with patch.object(
            adapter, "_upload_external_parquet_to_blob", return_value="/benchbox-data/testdb_external/orders/"
        ):
            stats, _, _ = adapter.create_external_tables(Benchmark(), mock_conn, tmp_path)

        assert stats["orders"] == 15
        execute_sql = " ".join(str(call.args[0]) for call in mock_cursor.execute.call_args_list)
        assert "CREATE EXTERNAL DATA SOURCE [BENCHBOX_EXTERNAL_SOURCE]" in execute_sql
        assert "CREATE EXTERNAL FILE FORMAT [BENCHBOX_PARQUET_FORMAT]" in execute_sql
        assert "CREATE EXTERNAL TABLE [dbo].[orders]" in execute_sql
        assert "LOCATION = '/benchbox-data/testdb_external/orders/'" in execute_sql

    def test_load_data_native_path_not_routed_to_external(self, synapse_stubs, tmp_path):
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        csv_file = tmp_path / "orders.csv"
        csv_file.write_text("1,a\n")

        class Benchmark:
            tables = {"orders": [csv_file]}

        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            schema="dbo",
            username="admin",
            password="secret",
            storage_account="benchboxacct",
            container="benchbox",
        )

        with (
            patch.object(adapter, "_load_data_via_blob", return_value={"orders": 1}) as mock_blob_load,
            patch.object(
                adapter,
                "create_external_tables",
                side_effect=AssertionError("native load_data should not call create_external_tables"),
            ),
        ):
            stats, _, _ = adapter.load_data(Benchmark(), mock_conn, tmp_path)

        assert stats["orders"] == 1
        mock_blob_load.assert_called_once()

    @pytest.mark.parametrize(
        ("column_type", "expected"),
        [
            ("DECIMAL(15,2)", "DECIMAL(15,2)"),
            ("varchar(128)", "VARCHAR(128)"),
            ("bigint", "BIGINT"),
            ("smallint", "SMALLINT"),
            ("int", "INT"),
            ("double", "FLOAT"),
            ("real", "REAL"),
            ("timestamp", "DATETIME2"),
            ("bool", "BIT"),
            ("geography", "VARCHAR(8000)"),
            ("", "VARCHAR(8000)"),
        ],
    )
    def test_map_external_column_type(self, column_type, expected, synapse_stubs):
        assert AzureSynapseAdapter._map_external_column_type(column_type) == expected

    def test_build_external_column_definitions_resolves_table_name_variants(self, synapse_stubs):
        benchmark = Mock()
        benchmark.get_schema.return_value = {
            "ORDERS": {
                "columns": [
                    {"name": "o_orderkey", "type": "BIGINT"},
                    {"name": "o_orderdate", "type": "TIMESTAMP"},
                ]
            }
        }

        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
        )

        column_defs = adapter._build_external_column_definitions(benchmark, "orders")

        assert column_defs == "[o_orderkey] BIGINT, [o_orderdate] DATETIME2"

    def test_build_external_column_definitions_rejects_missing_columns(self, synapse_stubs):
        benchmark = Mock()
        benchmark.get_schema.return_value = {"orders": {"columns": []}}
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
        )

        with pytest.raises(ValueError, match="No columns found"):
            adapter._build_external_column_definitions(benchmark, "orders")

    def test_resolve_external_credential_name_prefers_existing_name(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
            storage_credential="BENCHBOX_PRECREATED_CRED",
        )

        assert adapter._resolve_external_credential_name(Mock()) == "BENCHBOX_PRECREATED_CRED"

    def test_resolve_external_credential_name_creates_sas_credential(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
            storage_account="benchboxacct",
            container="benchbox",
            storage_sas_token="sv=2025-01-01&sig=abc'123",
        )
        mock_cursor = Mock()

        credential_name = adapter._resolve_external_credential_name(mock_cursor)

        assert credential_name == "BENCHBOX_EXTERNAL_SAS_CRED"
        executed_sql = str(mock_cursor.execute.call_args.args[0])
        assert "CREATE DATABASE SCOPED CREDENTIAL [BENCHBOX_EXTERNAL_SAS_CRED]" in executed_sql
        assert "IDENTITY = 'SHARED ACCESS SIGNATURE'" in executed_sql
        assert "sv=2025-01-01&sig=abc''123" in executed_sql

    def test_resolve_external_credential_name_creates_key_credential(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
            storage_account="benchboxacct",
            container="benchbox",
            storage_account_key="account-key-123",
        )
        mock_cursor = Mock()

        credential_name = adapter._resolve_external_credential_name(mock_cursor)

        assert credential_name == "BENCHBOX_EXTERNAL_KEY_CRED"
        executed_sql = str(mock_cursor.execute.call_args.args[0])
        assert "CREATE DATABASE SCOPED CREDENTIAL [BENCHBOX_EXTERNAL_KEY_CRED]" in executed_sql
        assert "IDENTITY = 'benchboxacct'" in executed_sql
        assert "SECRET = 'account-key-123'" in executed_sql

    def test_setup_external_table_primitives_builds_data_source_with_credential(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
            storage_account="benchboxacct",
            container="benchbox",
        )
        mock_cursor = Mock()

        with (
            patch.object(adapter, "_setup_external_data_source") as mock_setup_source,
            patch.object(adapter, "_resolve_external_credential_name", return_value="BENCHBOX_EXTERNAL_SAS_CRED"),
        ):
            data_source_name, file_format_name = adapter._setup_external_table_primitives(mock_cursor)

        assert (data_source_name, file_format_name) == ("BENCHBOX_EXTERNAL_SOURCE", "BENCHBOX_PARQUET_FORMAT")
        mock_setup_source.assert_called_once_with(mock_cursor)
        execute_calls = [str(call.args[0]) for call in mock_cursor.execute.call_args_list]
        assert any("CREATE EXTERNAL DATA SOURCE [BENCHBOX_EXTERNAL_SOURCE]" in sql for sql in execute_calls)
        assert any("LOCATION = 'abfss://benchbox@benchboxacct.dfs.core.windows.net'" in sql for sql in execute_calls)
        assert any("CREDENTIAL = [BENCHBOX_EXTERNAL_SAS_CRED]" in sql for sql in execute_calls)
        assert any("CREATE EXTERNAL FILE FORMAT [BENCHBOX_PARQUET_FORMAT]" in sql for sql in execute_calls)

    def test_load_data_via_blob_uses_sas_credential_clause(self, synapse_stubs, tmp_path):
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = (2,)
        csv_file = tmp_path / "orders.csv"
        csv_file.write_text("1,a\n2,b\n")

        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            schema="dbo",
            username="admin",
            password="secret",
            storage_account="benchboxacct",
            container="benchbox",
            storage_sas_token="sv=2025-01-01&sig=fake",
        )

        ds = DataSource(source_type="test", tables={"orders": [csv_file]})
        with (
            patch.object(adapter, "_setup_external_data_source"),
            patch.object(adapter, "_upload_to_blob", return_value=["https://blob/orders.csv"]),
        ):
            stats = adapter._load_data_via_blob(mock_cursor, ds, tmp_path)

        assert stats == {"orders": 2}
        execute_sql = [str(call.args[0]) for call in mock_cursor.execute.call_args_list]
        assert any("COPY INTO [dbo].[orders]" in sql for sql in execute_sql)
        assert any("IDENTITY = 'Shared Access Signature'" in sql for sql in execute_sql)
        assert any("SECRET = 'sv=2025-01-01&sig=fake'" in sql for sql in execute_sql)
        assert any("FIELDTERMINATOR = ','" in sql for sql in execute_sql)
        assert execute_sql[-1] == "SELECT COUNT(*) FROM [dbo].[orders]"

    def test_load_data_via_blob_uses_named_storage_credential(self, synapse_stubs, tmp_path):
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = (1,)
        csv_file = tmp_path / "orders.csv"
        csv_file.write_text("1,a\n")

        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            schema="dbo",
            username="admin",
            password="secret",
            storage_account="benchboxacct",
            container="benchbox",
            storage_credential="BENCHBOX_SHARED_CRED",
        )

        ds = DataSource(source_type="test", tables={"orders": [csv_file]})
        with (
            patch.object(adapter, "_setup_external_data_source"),
            patch.object(adapter, "_upload_to_blob", return_value=["https://blob/orders.csv"]),
        ):
            stats = adapter._load_data_via_blob(mock_cursor, ds, tmp_path)

        assert stats == {"orders": 1}
        copy_sql = str(mock_cursor.execute.call_args_list[0].args[0])
        assert "CREDENTIAL = 'BENCHBOX_SHARED_CRED'" in copy_sql

    def test_generate_tuning_clause_with_distribution_and_partitioning(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
            distribution_default="ROUND_ROBIN",
        )

        dist_col = Mock()
        dist_col.name = "customer_id"
        dist_col.order = 2
        part_col = Mock()
        part_col.name = "order_date"
        part_col.order = 1

        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True

        with patch("benchbox.core.tuning.interface.TuningType") as mock_tuning_type:
            mock_tuning_type.DISTRIBUTION = "distribution"
            mock_tuning_type.PARTITIONING = "partitioning"

            def get_columns_by_type(tuning_type):
                if tuning_type == mock_tuning_type.DISTRIBUTION:
                    return [dist_col]
                if tuning_type == mock_tuning_type.PARTITIONING:
                    return [part_col]
                return []

            mock_tuning.get_columns_by_type.side_effect = get_columns_by_type

            clause = adapter.generate_tuning_clause(mock_tuning)

        assert clause == (
            "WITH (DISTRIBUTION = HASH([customer_id]), "
            "PARTITION ([order_date] RANGE RIGHT FOR VALUES ()), "
            "CLUSTERED COLUMNSTORE INDEX)"
        )


class TestSynapseUncoveredMethods:
    def test_extract_storage_account_from_abfss_url(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
        )
        url = "abfss://container@mystorageaccount.dfs.core.windows.net/path"
        account = adapter._extract_storage_account(url)
        assert account == "mystorageaccount"

    def test_extract_storage_account_returns_none_for_invalid_url(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
        )
        assert adapter._extract_storage_account("https://example.com/path") is None

    def test_build_ctas_sort_sql_off_mode_returns_none(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
        )

        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("off", None)):
            result = adapter._build_ctas_sort_sql("orders", [])
        assert result is None

    def test_build_ctas_sort_sql_ctas_mode_is_disabled(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
            schema="dbo",
        )

        col = Mock()
        col.name = "l_shipdate"

        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("on", "ctas")):
            result = adapter._build_ctas_sort_sql("lineitem", [col])

        assert result is None
        assert "RENAME OBJECT" in adapter._ctas_sort_unsupported_reason("ctas")

    def test_analyze_table_updates_statistics(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
            schema="dbo",
        )
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.analyze_table(mock_conn, "orders")

        executed = str(mock_cursor.execute.call_args)
        assert "UPDATE STATISTICS" in executed
        assert "orders" in executed

    def test_analyze_table_raises_on_failure(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
        )
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.execute.side_effect = Exception("Statistics update failed")
        mock_conn.cursor.return_value = mock_cursor

        with pytest.raises(Exception, match="Statistics update failed"):
            adapter.analyze_table(mock_conn, "orders")
        mock_cursor.close.assert_called_once()

    def test_apply_table_tunings_swallows_analyze_table_failure(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
        )
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.execute.side_effect = Exception("Statistics update failed")
        mock_conn.cursor.return_value = mock_cursor
        table_tuning = Mock()
        table_tuning.has_any_tuning.return_value = True
        table_tuning.table_name = "orders"

        adapter.apply_table_tunings(table_tuning, mock_conn)

    def test_close_connection_calls_close(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
        )
        mock_conn = Mock()

        adapter.close_connection(mock_conn)
        mock_conn.close.assert_called_once()

    def test_close_connection_handles_none(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
        )
        adapter.close_connection(None)

    def test_get_platform_info_with_connection(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
            database="testdb",
        )
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.side_effect = [
            ("Microsoft Azure SQL Data Warehouse - 15.0.9999.1",),
            None,
        ]
        mock_conn.cursor.return_value = mock_cursor

        info = adapter.get_platform_info(connection=mock_conn)

        assert info["platform_name"] == "Azure Synapse"
        assert "Microsoft Azure" in info["platform_version"]
        assert info["configuration"]["database"] == "testdb"

    def test_create_schema_executes_table_creation(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
            schema="dbo",
        )
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        class MockBenchmark:
            pass

        ddl = "CREATE TABLE orders (o_orderkey BIGINT, o_custkey BIGINT)"
        with patch.object(adapter, "_create_schema_with_tuning", return_value=ddl):
            duration = adapter.create_schema(MockBenchmark(), mock_conn)

        assert isinstance(duration, float)
        assert duration >= 0

    def test_generate_tuning_clause_returns_empty_for_none(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
        )
        assert adapter.generate_tuning_clause(None) == ""

    def test_generate_tuning_clause_uses_default_distribution_when_no_dist_col(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
            distribution_default="ROUND_ROBIN",
        )

        from benchbox.core.tuning.interface import TuningType

        table_tuning = Mock()
        table_tuning.has_any_tuning.return_value = True
        table_tuning.get_columns_by_type.return_value = []

        result = adapter.generate_tuning_clause(table_tuning)
        assert "DISTRIBUTION = ROUND_ROBIN" in result
        assert "CLUSTERED COLUMNSTORE INDEX" in result

    def test_apply_table_tunings_no_op_when_no_tuning(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="admin",
            password="secret",
        )
        mock_conn = Mock()
        table_tuning = Mock()
        table_tuning.has_any_tuning.return_value = False

        adapter.apply_table_tunings(table_tuning, mock_conn)
        mock_conn.cursor.assert_not_called()


class TestSynapseMasterKeyPassword:
    def test_setup_external_data_source_uses_random_password(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            database="testdb",
            username="testuser",
            password="testpass",
            storage_account="teststorage",
            container="testcontainer",
        )
        cursor1 = Mock()
        cursor2 = Mock()

        adapter._setup_external_data_source(cursor1)
        adapter._setup_external_data_source(cursor2)

        sql1 = cursor1.execute.call_args[0][0]
        sql2 = cursor2.execute.call_args[0][0]

        assert "CREATE MASTER KEY" in sql1
        assert "CREATE MASTER KEY" in sql2

        assert "BenchBox#Temp123!" not in sql1
        assert "BenchBox#Temp123!" not in sql2


class TestAzureSynapseDataLoading:
    def test_resolve_data_files_returns_tables_from_resolver(self, tmp_path, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="u",
            password="p",
        )
        expected_tables = {"customer": [tmp_path / "customer.tbl"]}
        fake_ds = DataSource(source_type="test", tables=expected_tables)

        with patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_cls:
            mock_resolver = Mock()
            mock_cls.return_value = mock_resolver
            mock_resolver.resolve.return_value = fake_ds

            result = adapter._resolve_data_files(Mock(), tmp_path)

        assert result.tables == expected_tables
        assert mock_cls.call_args.kwargs.get("platform_name") == adapter.platform_name

    def test_resolve_data_files_raises_when_resolver_returns_none(self, tmp_path, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="u",
            password="p",
        )

        with patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_cls:
            mock_resolver = Mock()
            mock_cls.return_value = mock_resolver
            mock_resolver.resolve.return_value = None

            with pytest.raises(ValueError, match="No data files found"):
                adapter._resolve_data_files(Mock(), tmp_path)

    def test_resolve_data_files_raises_when_tables_empty(self, tmp_path, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="u",
            password="p",
        )

        with patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_cls:
            mock_resolver = Mock()
            mock_cls.return_value = mock_resolver
            mock_resolver.resolve.return_value = Mock(tables={})

            with pytest.raises(ValueError, match="No data files found"):
                adapter._resolve_data_files(Mock(), tmp_path)

    def test_load_data_delegates_to_resolve_data_files(self, tmp_path, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="u",
            password="p",
        )
        data_files = {"customer": [tmp_path / "customer.tbl"]}

        with (
            patch.object(adapter, "_resolve_data_files", return_value=data_files) as mock_resolve,
            patch.object(adapter, "_load_data_direct", return_value={"customer": 10}),
        ):
            mock_conn = Mock()
            mock_conn.cursor.return_value = Mock()
            adapter.load_data(Mock(), mock_conn, tmp_path)

        mock_resolve.assert_called_once()

    def test_load_data_propagates_resolve_error(self, tmp_path, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="test.sql.azuresynapse.net",
            username="u",
            password="p",
        )

        with patch.object(adapter, "_resolve_data_files", side_effect=ValueError("No data files found")):
            mock_conn = Mock()
            mock_conn.cursor.return_value = Mock()
            with pytest.raises(ValueError, match="No data files found"):
                adapter.load_data(Mock(), mock_conn, tmp_path)


class TestSynapseStagingRootAcceptsEveryAzureSpelling:
    @pytest.mark.parametrize(
        ("staging_root", "storage_account"),
        [
            ("az://container/benchbox", "explicit-account"),
            ("azure://container/benchbox", "explicit-account"),
            ("abfss://container@account.dfs.core.windows.net/benchbox", None),
            ("abfs://container@account.dfs.core.windows.net/benchbox", None),
        ],
    )
    def test_azure_staging_roots_are_accepted(self, synapse_stubs, staging_root, storage_account):
        adapter = AzureSynapseAdapter(
            server="myworkspace.sql.azuresynapse.net",
            username="admin",
            password="secret",
            staging_root=staging_root,
            **({"storage_account": storage_account} if storage_account else {}),
        )
        assert adapter.container == "container"
        assert adapter.storage_account == (storage_account or "account")

    def test_adls_uri_account_takes_precedence_over_explicit_account(self, synapse_stubs):
        adapter = AzureSynapseAdapter(
            server="myworkspace.sql.azuresynapse.net",
            username="admin",
            password="secret",
            staging_root="abfss://container@uri-account.dfs.core.windows.net/benchbox",
            storage_account="explicit-account",
        )
        assert adapter.storage_account == "uri-account"

    def test_blob_staging_root_requires_account_or_explicit_option(self, synapse_stubs):
        with pytest.raises(ValueError, match="must include a storage account"):
            AzureSynapseAdapter(
                server="myworkspace.sql.azuresynapse.net",
                username="admin",
                password="secret",
                staging_root="azure://container/benchbox",
            )

    @pytest.mark.parametrize("staging_root", ["s3://bucket/benchbox", "gs://bucket/benchbox"])
    def test_non_azure_staging_roots_are_still_rejected(self, synapse_stubs, staging_root):
        with pytest.raises(ValueError, match="requires Azure storage"):
            AzureSynapseAdapter(
                server="myworkspace.sql.azuresynapse.net",
                username="admin",
                password="secret",
                staging_root=staging_root,
            )
