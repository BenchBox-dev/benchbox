from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from benchbox.platforms.clickhouse.diagnostics import ClickHouseDiagnosticsMixin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _DiagnosticsHarness(ClickHouseDiagnosticsMixin):
    def __init__(self, deployment_mode: str) -> None:
        self.deployment_mode = deployment_mode
        self.platform_name = "ClickHouse Harness"
        self.host = "localhost"
        self.port = 9000
        self.database = "benchbox"
        self.secure = False
        self.compression = True
        self.max_memory_usage = "1GB"
        self.max_threads = 8
        self.disable_result_cache = False
        self.data_path = "/tmp/chdb"
        self.memory_limit = "2GB"
        self.logger = MagicMock()

    def get_database_path(self, **connection_config):
        return connection_config.get("db_path")

    def _create_admin_client(self, **connection_config):
        raise AssertionError("_create_admin_client should be patched in tests")


def test_check_server_database_exists_handles_local_and_server_modes(tmp_path) -> None:
    local_harness = _DiagnosticsHarness("local")
    db_path = tmp_path / "benchbox.chdb"
    db_path.touch()

    assert local_harness.check_server_database_exists(db_path=str(db_path)) is True
    assert local_harness.check_server_database_exists() is False

    server_harness = _DiagnosticsHarness("server")
    admin_client = MagicMock()
    admin_client.execute.return_value = [("default",), ("benchbox",)]

    with patch.object(server_harness, "_create_admin_client", return_value=admin_client):
        assert server_harness.check_server_database_exists() is True
        assert server_harness.check_server_database_exists(database="missing") is False


def test_check_server_database_exists_returns_false_on_admin_client_errors() -> None:
    harness = _DiagnosticsHarness("server")

    with patch.object(harness, "_create_admin_client", side_effect=RuntimeError("connection refused")):
        assert harness.check_server_database_exists(database="benchbox") is False


def test_drop_database_uses_admin_client_and_local_mode_is_noop() -> None:
    server_harness = _DiagnosticsHarness("server")
    admin_client = MagicMock()

    with patch.object(server_harness, "_create_admin_client", return_value=admin_client):
        server_harness.drop_database(database="benchbox")

    admin_client.execute.assert_called_once_with("DROP DATABASE IF EXISTS benchbox")

    local_harness = _DiagnosticsHarness("local")
    with patch.object(local_harness, "_create_admin_client", side_effect=AssertionError("should not be called")):
        local_harness.drop_database()


def test_drop_database_wraps_admin_client_errors() -> None:
    harness = _DiagnosticsHarness("server")

    with patch.object(harness, "_create_admin_client", side_effect=RuntimeError("permission denied")):
        with pytest.raises(RuntimeError, match="Failed to drop ClickHouse database: permission denied"):
            harness.drop_database(database="benchbox")


def test_get_table_info_returns_schema_stats_and_zero_defaults() -> None:
    harness = _DiagnosticsHarness("server")
    connection = MagicMock()
    connection.execute.side_effect = [
        [("id", "UInt64"), ("name", "String")],
        [],
    ]

    info = harness.get_table_info(connection, "lineitem")

    assert info["columns"] == [("id", "UInt64"), ("name", "String")]
    assert info["row_count"] == 0
    assert info["bytes_on_disk"] == 0
    assert info["compressed_size"] == 0


def test_get_platform_metadata_server_captures_settings_and_database_stats() -> None:
    harness = _DiagnosticsHarness("server")
    connection = MagicMock()
    connection.execute.side_effect = [
        [("24.3.1",)],
        [("max_memory_usage", "1073741824"), ("max_threads", "8")],
        [("benchbox", 8 * 1024 * 1024, 3)],
    ]

    metadata = harness._get_platform_metadata(connection)

    assert metadata["platform"] == "ClickHouse Harness"
    assert metadata["deployment_mode"] == "server"
    assert metadata["mode"] == "server"
    assert metadata["result_cache_enabled"] is True
    assert metadata["host"] == "localhost"
    assert metadata["port"] == 9000
    assert metadata["database"] == "benchbox"
    assert metadata["clickhouse_version"] == "24.3.1"
    assert metadata["current_settings"] == {"max_memory_usage": "1073741824", "max_threads": "8"}
    assert metadata["database_stats"] == {
        "total_bytes": 8 * 1024 * 1024,
        "total_mb": 8.0,
        "table_count": 3,
    }


def test_get_platform_metadata_local_mode_records_data_path_and_unknown_version() -> None:
    harness = _DiagnosticsHarness("local")
    connection = MagicMock()
    connection.execute.side_effect = [
        [],
        [],
        [],
    ]

    metadata = harness._get_platform_metadata(connection)

    assert metadata["platform"] == "ClickHouse Harness"
    assert metadata["deployment_mode"] == "local"
    assert metadata["mode"] == "local"
    assert metadata["data_path"] == "/tmp/chdb"
    assert metadata["clickhouse_version"] == "unknown"
    assert metadata["current_settings"] == {}
    assert "database_stats" not in metadata


def test_get_platform_metadata_captures_errors_without_raising() -> None:
    harness = _DiagnosticsHarness("server")
    connection = MagicMock()
    connection.execute.side_effect = RuntimeError("metadata unavailable")

    metadata = harness._get_platform_metadata(connection)

    assert metadata["metadata_error"] == "metadata unavailable"


def test_get_table_info_returns_error_dict_on_failure() -> None:
    harness = _DiagnosticsHarness("server")
    connection = MagicMock()
    connection.execute.side_effect = RuntimeError("missing table")

    info = harness.get_table_info(connection, "lineitem")

    assert info == {"error": "missing table"}


def test_optimize_table_logs_success_and_failure() -> None:
    harness = _DiagnosticsHarness("server")
    connection = MagicMock()

    harness.optimize_table(connection, "lineitem")

    connection.execute.assert_called_once_with("OPTIMIZE TABLE lineitem FINAL")
    harness.logger.info.assert_called_once_with("Optimized table lineitem")

    failing_connection = MagicMock()
    failing_connection.execute.side_effect = RuntimeError("optimize failed")

    harness.optimize_table(failing_connection, "lineitem")

    harness.logger.warning.assert_called_once_with("Failed to optimize table lineitem: optimize failed")


