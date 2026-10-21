# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from tests.utilities.optional_engines import require_chdb

require_chdb()

from benchbox.platforms.clickhouse import ClickHouseAdapter, ClickHouseLocalClient


class TestClickHouseEmbeddedMode:
    def test_local_mode_initialization(self):
        adapter = ClickHouseAdapter(deployment_mode="local")
        assert adapter.deployment_mode == "local"
        assert adapter.platform_name == "ClickHouse (Local)"
        assert adapter.host is None
        assert adapter.port is None
        assert adapter.database is None

    def test_server_mode_initialization(self):
        try:
            adapter = ClickHouseAdapter(deployment_mode="server", host="test.com", port=9999)
            assert adapter.deployment_mode == "server"
            assert adapter.deployment_mode == "server"
            assert adapter.platform_name == "ClickHouse (Server)"
            assert adapter.host == "test.com"
            assert adapter.port == 9999
        except ImportError:
            pytest.skip("ClickHouse driver not installed - skipping server mode test")

    def test_invalid_mode_error(self):
        with pytest.raises(ValueError) as exc_info:
            ClickHouseAdapter(deployment_mode="invalid")

        assert "Invalid ClickHouse deployment mode 'invalid'" in str(exc_info.value)
        assert "local" in str(exc_info.value)
        assert "server" in str(exc_info.value)

    def test_local_mode_configuration(self):
        adapter = ClickHouseAdapter(
            deployment_mode="local",
            data_path="/tmp/test",
            max_memory_usage="2GB",
            max_threads=2,
        )

        assert adapter.deployment_mode == "local"
        assert adapter.data_path == "/tmp/test"
        assert adapter.max_memory_usage == "2GB"
        assert adapter.max_threads == 2
        assert adapter.username is None
        assert adapter.password is None
        assert adapter.secure is None


class TestClickHouseLocalClient:
    def test_client_initialization(self):
        client = ClickHouseLocalClient()
        assert client._initialized is True

    def test_simple_query_execution(self):
        client = ClickHouseLocalClient()
        result = client.execute("SELECT 1")
        assert len(result) == 1
        assert result[0] == (1,)

    def test_empty_query_result(self):
        client = ClickHouseLocalClient()
        result = client.execute("SELECT * FROM (SELECT 1 WHERE 0)")
        assert result == []

    def test_query_result_is_a_materialized_sequence(self):
        result = ClickHouseLocalClient().execute("SELECT 1")

        assert isinstance(result, Sequence)
        assert result[0] == (1,)

    def test_multi_column_query_result(self):
        client = ClickHouseLocalClient()
        result = client.execute("SELECT 1, 'hello', 3.14")

        assert len(result) == 1
        assert result[0][0] == 1
        assert result[0][1] == "hello"
        assert abs(result[0][2] - 3.14) < 0.01

    def test_csv_parsing_with_quotes(self):
        client = ClickHouseLocalClient()

        line = '"John Doe",30,"Software Engineer"'
        result = client._parse_csv_line(line)

        assert result == ("John Doe", 30, "Software Engineer")

    def test_csv_parsing_with_nulls(self):
        client = ClickHouseLocalClient()

        line = "John,,Engineer"
        result = client._parse_csv_line(line)

        assert result == ("John", None, "Engineer")

    def test_type_conversion(self):
        client = ClickHouseLocalClient()

        assert client._is_float("123") is True
        assert client._is_float("123.45") is True
        assert client._is_float("abc") is False

        line = "123,123.45,true,hello"
        result = client._parse_csv_line(line)

        assert result[0] == 123
        assert result[1] == 123.45
        assert result[2] == "true"
        assert result[3] == "hello"

    def test_disconnect(self):
        client = ClickHouseLocalClient()
        client.disconnect()


class TestClickHouseEmbeddedConnection:
    def test_create_local_connection(self):
        adapter = ClickHouseAdapter(deployment_mode="local")
        with patch.object(ClickHouseAdapter, "get_database_path", return_value=None):

            class _FakeClient:
                def __init__(self, db_path=None):
                    pass

                def execute(self, q, params=None):
                    return [(1,)]

                def close(self):
                    pass

            with patch("benchbox.platforms.clickhouse.setup.ClickHouseLocalClient", _FakeClient):
                connection = adapter.create_connection()

        assert hasattr(connection, "execute")

    def test_database_operations_local_mode(self):
        adapter = ClickHouseAdapter(deployment_mode="local")

        tmpdir = tempfile.mkdtemp()
        db_path = str(Path(tmpdir) / "clickhouse_local.chdb")
        with patch.object(ClickHouseAdapter, "get_database_path", return_value=db_path):
            assert adapter.check_server_database_exists() is False
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            Path(db_path).touch()
            assert adapter.check_server_database_exists() is True
            adapter.drop_database()

    def test_local_metadata_collection(self):
        adapter = ClickHouseAdapter(deployment_mode="local", data_path="/tmp/test")
        with patch.object(ClickHouseAdapter, "get_database_path", return_value=None):

            class _FakeClient:
                def __init__(self, db_path=None):
                    pass

                def execute(self, q, params=None):
                    return [(1,)]

                def close(self):
                    pass

            with patch("benchbox.platforms.clickhouse.setup.ClickHouseLocalClient", _FakeClient):
                connection = adapter.create_connection()

        metadata = adapter._get_platform_metadata(connection)

        assert metadata["platform"] == "ClickHouse (Local)"
        assert metadata["mode"] == "local"
        assert metadata["data_path"] == "/tmp/test"
        assert "host" not in metadata
        assert "port" not in metadata
        assert "database" not in metadata


class TestClickHouseEmbeddedIntegration:
    def test_query_execution_integration(self):
        adapter = ClickHouseAdapter(deployment_mode="local")
        with patch.object(ClickHouseAdapter, "get_database_path", return_value=None):

            class _FakeClient:
                def __init__(self, db_path=None):
                    pass

                def execute(self, q, params=None):
                    if q.strip().upper().startswith("SELECT"):
                        return [(42, "result")]
                    return []

                def close(self):
                    pass

            with patch("benchbox.platforms.clickhouse.setup.ClickHouseLocalClient", _FakeClient):
                connection = adapter.create_connection()

        result = adapter.execute_query(connection, "SELECT 42, 'result'", "test_query")

        assert result["status"] == "SUCCESS"
        assert result["query_id"] == "test_query"
        assert result["rows_returned"] == 1
        assert result["execution_time_seconds"] > 0

    def test_query_execution_error_handling(self):
        adapter = ClickHouseAdapter(deployment_mode="local")
        with patch.object(ClickHouseAdapter, "get_database_path", return_value=None):

            class _FakeClient:
                def __init__(self, db_path=None):
                    pass

                def execute(self, q, params=None):
                    if q.strip().upper().startswith("SELECT 1"):
                        return [(1,)]
                    raise RuntimeError("parse error")

                def close(self):
                    pass

            with patch("benchbox.platforms.clickhouse.setup.ClickHouseLocalClient", _FakeClient):
                connection = adapter.create_connection()

        result = adapter.execute_query(connection, "SELECT * FROM missing_table", "test_query")

        assert result["status"] == "FAILED"
        assert result["query_id"] == "test_query"
        assert result["rows_returned"] == 0
        assert "error" in result
        assert result["error_type"] == "RuntimeError"

    def test_unparseable_query_fails_before_reaching_the_engine(self):
        adapter = ClickHouseAdapter(deployment_mode="local")
        connection = Mock()

        result = adapter.execute_query(connection, "INVALID SQL QUERY HERE", "test_query")

        assert result["status"] == "FAILED"
        assert result["error_type"] == "ParseError"
        connection.execute.assert_not_called()

    def test_basic_table_operations(self):
        adapter = ClickHouseAdapter(deployment_mode="local")
        with patch.object(ClickHouseAdapter, "get_database_path", return_value=None):

            class _FakeClient:
                def __init__(self, db_path=None):
                    self.tables = set()

                def execute(self, q, params=None):
                    uq = q.strip().upper()
                    if uq.startswith("CREATE TABLE"):
                        self.tables.add("TEST_TABLE")
                        return []
                    if uq.startswith("INSERT"):
                        return []
                    if uq.startswith("SELECT"):
                        return [(1, "Alice"), (2, "Bob")]
                    if uq.startswith("DROP TABLE"):
                        self.tables.discard("TEST_TABLE")
                        return []
                    return []

                def close(self):
                    pass

            with patch("benchbox.platforms.clickhouse.setup.ClickHouseLocalClient", _FakeClient):
                connection = adapter.create_connection()

        create_result = adapter.execute_query(
            connection,
            "CREATE TABLE IF NOT EXISTS test_table (id Int32, name String) ENGINE = Memory",
            "create_test",
        )
        assert create_result["status"] == "SUCCESS"

        insert_result = adapter.execute_query(
            connection,
            "INSERT INTO test_table VALUES (1, 'Alice'), (2, 'Bob')",
            "insert_test",
        )
        assert insert_result["status"] == "SUCCESS"

        select_result = adapter.execute_query(connection, "SELECT * FROM test_table ORDER BY id", "select_test")
        assert select_result["status"] == "SUCCESS"
        assert select_result["rows_returned"] == 2

        drop_result = adapter.execute_query(connection, "DROP TABLE IF EXISTS test_table", "drop_test")
        assert drop_result["status"] == "SUCCESS"
