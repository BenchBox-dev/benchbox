# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import Mock, patch

import pytest

import benchbox.platforms.paradedb as paradedb_module
import benchbox.platforms.postgresql as postgresql_module
from benchbox.platforms.paradedb import PARADEDB_EXTENSION, ParadeDBAdapter
from benchbox.platforms.postgresql import POSTGRES_DIALECT, PostgreSQLAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def paradedb_stubs(monkeypatch):

    mock_psycopg = Mock()
    mock_psycopg.__version__ = "3.1.0"

    monkeypatch.setattr(paradedb_module, "psycopg", mock_psycopg)
    monkeypatch.setattr(postgresql_module, "psycopg", mock_psycopg)

    return mock_psycopg


class TestParadeDBAdapter:
    def test_initialization_defaults(self, paradedb_stubs):

        adapter = ParadeDBAdapter()

        assert adapter.platform_name == "paradedb"
        assert adapter.get_target_dialect() == POSTGRES_DIALECT
        assert adapter.host == "localhost"
        assert adapter.port == 5432
        assert adapter.database == "benchbox"
        assert adapter.username == "postgres"
        assert adapter.schema == "public"

    def test_initialization_with_config(self, paradedb_stubs):

        adapter = ParadeDBAdapter(
            host="paradedb.example.com",
            port=5433,
            database="analytics_db",
            username="custom_user",
            password="secret",
            schema="analytics",
        )

        assert adapter.host == "paradedb.example.com"
        assert adapter.port == 5433
        assert adapter.database == "analytics_db"
        assert adapter.username == "custom_user"
        assert adapter.password == "secret"
        assert adapter.schema == "analytics"

    def test_dialect_is_postgres(self, paradedb_stubs):

        adapter = ParadeDBAdapter()

        assert adapter.get_target_dialect() == POSTGRES_DIALECT
        assert adapter.get_target_dialect() == "postgres"

    def test_from_config_basic(self, paradedb_stubs):

        config = {
            "host": "paradedb.local",
            "port": 5433,
            "database": "test_analytics",
        }

        adapter = ParadeDBAdapter.from_config(config)

        assert adapter.host == "paradedb.local"
        assert adapter.port == 5433
        assert adapter.database == "test_analytics"

    def test_create_connection_verifies_extension(self, paradedb_stubs):

        adapter = ParadeDBAdapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = ("0.9.0",)
        mock_cursor.closed = False
        mock_conn.cursor.return_value = mock_cursor

        with patch.object(PostgreSQLAdapter, "create_connection", return_value=mock_conn):
            result = adapter.create_connection()

        assert result is mock_conn
        executed = [call.args[0] for call in mock_cursor.execute.call_args_list]
        assert any(PARADEDB_EXTENSION in sql for sql in executed)

    def test_create_connection_creates_missing_extension(self, paradedb_stubs):

        adapter = ParadeDBAdapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.side_effect = [None, ("0.9.0",)]
        mock_cursor.closed = False
        mock_conn.cursor.return_value = mock_cursor

        with patch.object(PostgreSQLAdapter, "create_connection", return_value=mock_conn):
            adapter.create_connection()

        executed = [call.args[0] for call in mock_cursor.execute.call_args_list]
        assert any("CREATE EXTENSION" in sql and PARADEDB_EXTENSION in sql for sql in executed)

    def test_create_connection_raises_when_extension_unavailable(self, paradedb_stubs):

        adapter = ParadeDBAdapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = None
        mock_cursor.closed = False
        mock_conn.cursor.return_value = mock_cursor

        with patch.object(PostgreSQLAdapter, "create_connection", return_value=mock_conn):
            with pytest.raises(RuntimeError, match="pg_search extension is not available") as excinfo:
                adapter.create_connection()
        assert "paradedb/paradedb:0.26.2-pg18" in str(excinfo.value)

    def test_platform_info_reports_version(self, paradedb_stubs):

        adapter = ParadeDBAdapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = ("0.9.0",)
        mock_conn.cursor.return_value = mock_cursor

        info = adapter.get_platform_info(connection=mock_conn)

        assert info["platform_type"] == "paradedb"
        assert info["paradedb_version"] == "0.9.0"

    def test_no_extension_conflicts_declared(self):

        from benchbox.core.platform_registry import PlatformRegistry

        assert PlatformRegistry.get_platform_conflicts("paradedb") == []


from benchbox.platforms.postgresql import PostgreSQLAdapter as PostgreSQLAdapter
