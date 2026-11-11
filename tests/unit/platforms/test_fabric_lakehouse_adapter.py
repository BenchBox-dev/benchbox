from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

import benchbox.platforms.fabric_lakehouse as lakehouse_module
from benchbox.core.exceptions import ConfigurationError, ReadOnlyPlatformError
from benchbox.platforms.fabric_lakehouse import FabricLakehouseAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def lakehouse_stubs(monkeypatch):

    mock_pyodbc = Mock()
    mock_pyodbc.connect = Mock()
    mock_pyodbc.Error = Exception

    monkeypatch.setattr(lakehouse_module, "pyodbc", mock_pyodbc)
    monkeypatch.setattr(lakehouse_module, "check_platform_dependencies", lambda platform: (True, []))

    return mock_pyodbc


@pytest.fixture()
def lakehouse_adapter(lakehouse_stubs):

    return FabricLakehouseAdapter(
        workspace="workspace-abc",
        lakehouse="lakehouse_db",
        auth_method="service_principal",
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
    )


class TestFabricLakehouseAdapterInitialization:
    def test_requires_workspace_or_server(self, lakehouse_stubs):
        with pytest.raises(ConfigurationError, match="requires connection details"):
            FabricLakehouseAdapter(database="db1")

    def test_requires_database_or_lakehouse(self, lakehouse_stubs):
        with pytest.raises(ConfigurationError, match="requires database or lakehouse name"):
            FabricLakehouseAdapter(workspace="workspace-abc")

    def test_workspace_builds_server(self, lakehouse_stubs):
        adapter = FabricLakehouseAdapter(workspace="workspace-abc", lakehouse="lakehouse_db")
        assert adapter.server == "workspace-abc.sql.azuresynapse.net"

    def test_service_principal_requires_credentials(self, lakehouse_stubs):
        with pytest.raises(ConfigurationError, match="authentication is incomplete"):
            FabricLakehouseAdapter(workspace="workspace-abc", lakehouse="db", auth_method="service_principal")


class TestFabricLakehouseReadOnlyBehavior:
    def test_create_schema_raises_read_only_error(self, lakehouse_adapter):
        with pytest.raises(ReadOnlyPlatformError, match="read-only"):
            lakehouse_adapter.create_schema(benchmark=Mock(), connection=Mock())

    def test_load_data_raises_read_only_error(self, lakehouse_adapter):
        with pytest.raises(ReadOnlyPlatformError, match="read-only"):
            lakehouse_adapter.load_data(benchmark=Mock(), connection=Mock(), data_dir=Path("."))

    def test_execute_query_rejects_insert(self, lakehouse_adapter):
        result = lakehouse_adapter.execute_query(connection=Mock(), query="INSERT INTO t VALUES (1)", query_id="Q1")

        assert result["status"] == "FAILED"
        assert result["error_type"] == "ReadOnlyPlatformError"

    def test_execute_query_allows_select(self, lakehouse_adapter):
        connection = Mock()
        cursor = Mock()
        cursor.description = [("value",)]
        cursor.fetchall.return_value = [(1,), (2,)]
        connection.cursor.return_value = cursor

        result = lakehouse_adapter.execute_query(connection=connection, query="SELECT 1", query_id="Q2")

        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 2


class TestFabricLakehouseConnection:
    def test_create_connection_uses_access_token(self, lakehouse_adapter, lakehouse_stubs, monkeypatch):
        monkeypatch.setattr(lakehouse_adapter, "_get_access_token", lambda: "token")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = [1]
        mock_connection.cursor.return_value = mock_cursor
        lakehouse_stubs.connect.return_value = mock_connection

        connection = lakehouse_adapter.create_connection()

        assert connection is mock_connection
        call_kwargs = lakehouse_stubs.connect.call_args[1]
        assert "attrs_before" in call_kwargs

    def test_get_platform_info_marks_read_only(self, lakehouse_adapter, monkeypatch):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = ["Fabric SQL Analytics"]
        mock_connection.cursor.return_value = mock_cursor
        monkeypatch.setattr(lakehouse_adapter, "create_connection", lambda **kwargs: mock_connection)

        info = lakehouse_adapter.get_platform_info()

        assert info["platform"] == "fabric-lakehouse"
        assert info["read_only"] is True


class TestFabricLakehouseTimingContract:
    def test_method_two_with_request_count_and_result_bytes(self, lakehouse_adapter):
        from benchbox.core.results.timer_scope import TIMING_METHOD_QUERY_ONLY

        connection = Mock()
        cursor = Mock()
        cursor.description = [("value",)]
        cursor.fetchall.return_value = [(1,), (2,)]
        connection.cursor.return_value = cursor

        result = lakehouse_adapter.execute_query(connection=connection, query="SELECT 1", query_id="Q2")

        assert lakehouse_adapter.timing_method == TIMING_METHOD_QUERY_ONLY
        assert result["request_count"] == 1
        assert result["result_bytes"] == 16

    def test_rejected_write_records_no_timing_fields(self, lakehouse_adapter):
        result = lakehouse_adapter.execute_query(connection=Mock(), query="INSERT INTO t VALUES (1)", query_id="Q1")

        assert result["status"] == "FAILED"
        assert "request_count" not in result


