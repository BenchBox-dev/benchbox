# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock, Mock, patch

import pytest

from benchbox.core.tuning.interface import TuningType

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture(autouse=True)
def synapse_stubs():

    with (
        patch.dict("sys.modules", {"pyodbc": MagicMock()}),
        patch("benchbox.platforms.azure_synapse.check_platform_dependencies", return_value=(True, [])),
    ):
        yield


def _make_adapter(**kwargs):
    from benchbox.platforms.azure_synapse import AzureSynapseAdapter

    defaults = {
        "server": "test.sql.azuresynapse.net",
        "username": "admin",
        "password": "secret",
        "schema": "dbo",
    }
    defaults.update(kwargs)
    return AzureSynapseAdapter(**defaults)


class TestSynapseGenerateTuningClause:
    def test_with_distribution_col_returns_hash(self):
        adapter = _make_adapter()
        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True
        col = Mock()
        col.name = "customer_id"
        col.order = 1

        def _cols(t):
            if t == TuningType.DISTRIBUTION:
                return [col]
            return []

        mock_tuning.get_columns_by_type.side_effect = _cols
        clause = adapter.generate_tuning_clause(mock_tuning)

        assert "DISTRIBUTION = HASH([customer_id])" in clause
        assert "CLUSTERED COLUMNSTORE INDEX" in clause

    def test_no_distribution_col_uses_default(self):
        adapter = _make_adapter(distribution_default="ROUND_ROBIN")
        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True
        mock_tuning.get_columns_by_type.return_value = []
        clause = adapter.generate_tuning_clause(mock_tuning)

        assert "DISTRIBUTION = ROUND_ROBIN" in clause

    def test_with_partition_col_adds_range_right(self):
        adapter = _make_adapter()
        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True
        dist_col = Mock()
        dist_col.name = "customer_id"
        dist_col.order = 1
        part_col = Mock()
        part_col.name = "order_date"
        part_col.order = 1

        def _cols(t):
            if t == TuningType.DISTRIBUTION:
                return [dist_col]
            if t == TuningType.PARTITIONING:
                return [part_col]
            return []

        mock_tuning.get_columns_by_type.side_effect = _cols
        clause = adapter.generate_tuning_clause(mock_tuning)

        assert "PARTITION ([order_date] RANGE RIGHT FOR VALUES ())" in clause

    def test_none_returns_empty(self):
        adapter = _make_adapter()
        assert adapter.generate_tuning_clause(None) == ""


class TestSynapseCreateSchemaDdl:
    @staticmethod
    def _executed(adapter, ddl):
        cursor = MagicMock()
        connection = MagicMock()
        connection.cursor.return_value = cursor
        with patch.object(adapter, "_create_schema_with_tuning", return_value=ddl):
            adapter.create_schema(MagicMock(), connection)
        return [call[0][0] for call in cursor.execute.call_args_list]

    def test_adds_round_robin_and_schema(self):
        adapter = _make_adapter(distribution_default="ROUND_ROBIN", schema="dbo")
        executed = self._executed(adapter, "CREATE TABLE orders (id INT);")
        create = executed[-1]
        assert "[dbo].[orders]" in create
        assert "DISTRIBUTION=ROUND_ROBIN" in create

    def test_uses_configured_distribution_default(self):
        adapter = _make_adapter(distribution_default="REPLICATE", schema="dbo")
        executed = self._executed(adapter, "CREATE TABLE orders (id INT);")
        assert "DISTRIBUTION=REPLICATE" in executed[-1]

    def test_drops_existing_table_before_create(self):
        adapter = _make_adapter(schema="dbo")
        executed = self._executed(adapter, "CREATE TABLE orders (id INT);")
        assert "DROP TABLE [dbo].[orders]" in executed[0]


class TestSynapseConnectionString:
    def test_connection_string_contains_server(self):
        adapter = _make_adapter(server="myserver.sql.azuresynapse.net")
        conn_str = adapter._get_connection_string()
        assert "myserver.sql.azuresynapse.net" in conn_str

    def test_connection_string_with_database(self):
        adapter = _make_adapter(database="mydb")
        conn_str = adapter._get_connection_string(database="mydb")
        assert "mydb" in conn_str


class TestSynapseExternalTableDdl:
    def test_setup_external_table_primitives_sql(self):

        adapter = _make_adapter(
            storage_account="mystorageaccount",
            container="data",
            storage_account_key="key123",
        )
        mock_cursor = MagicMock()

        with patch.object(adapter, "_resolve_external_credential_name", return_value=None):
            data_source, file_format = adapter._setup_external_table_primitives(mock_cursor)

        executed_sql = [call.args[0] for call in mock_cursor.execute.call_args_list if call.args]
        assert any("EXTERNAL DATA SOURCE" in sql for sql in executed_sql)
        assert any("EXTERNAL FILE FORMAT" in sql for sql in executed_sql)
        assert data_source == "BENCHBOX_EXTERNAL_SOURCE"
        assert file_format == "BENCHBOX_PARQUET_FORMAT"


class TestSynapseGetTargetDialect:
    def test_returns_tsql_or_synapse(self):
        adapter = _make_adapter()
        dialect = adapter.get_target_dialect()
        assert dialect.lower() in ("tsql", "synapse", "azure_synapse")
