from __future__ import annotations

from unittest.mock import patch

import pytest

from benchbox.platforms.clickhouse_cloud import ClickHouseCloudAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_adapter(**overrides):
    config = {"host": "abc123.us-east-2.aws.clickhouse.cloud", "password": "secret"}
    config.update(overrides)
    return ClickHouseCloudAdapter(**config)


def test_cloud_adapter_sets_send_receive_timeout_by_default() -> None:
    adapter = _make_adapter()

    assert adapter.send_receive_timeout == 300


def test_cloud_adapter_respects_explicit_send_receive_timeout() -> None:
    adapter = _make_adapter(send_receive_timeout=900)

    assert adapter.send_receive_timeout == 900


class _StubAdminClient:
    def __init__(self, databases: list[str]) -> None:
        self._databases = databases
        self.executed: list[str] = []

    def execute(self, statement: str):
        self.executed.append(statement)
        if statement == "SHOW DATABASES":
            return [(name,) for name in self._databases]
        return []


def test_cloud_probe_reports_real_existence() -> None:
    adapter = _make_adapter(database="benchdb")

    with patch.object(adapter, "_create_admin_client", return_value=_StubAdminClient(["default", "benchdb"])):
        assert adapter.check_server_database_exists() is True

    with patch.object(adapter, "_create_admin_client", return_value=_StubAdminClient(["default"])):
        assert adapter.check_server_database_exists() is False


def test_cloud_probe_does_not_swallow_attribute_error() -> None:
    adapter = _make_adapter()

    with patch.object(adapter, "_create_admin_client", side_effect=AttributeError("missing attr")):
        with pytest.raises(AttributeError):
            adapter.check_server_database_exists()


def test_cloud_probe_still_returns_false_on_connection_errors() -> None:
    adapter = _make_adapter()

    with patch.object(adapter, "_create_admin_client", side_effect=RuntimeError("connection refused")):
        assert adapter.check_server_database_exists() is False


def test_force_recreate_drops_existing_cloud_database_at_first_connection() -> None:
    adapter = _make_adapter(database="benchdb", force_recreate=True)
    admin_client = _StubAdminClient(["default", "benchdb"])

    with patch.object(adapter, "_create_admin_client", return_value=admin_client):
        adapter._reset_run_scoped_state()
        adapter.handle_existing_database()

    drops = [statement for statement in admin_client.executed if "DROP DATABASE" in statement]
    assert drops == ["DROP DATABASE IF EXISTS benchdb"]
    assert adapter._existing_db_decided is True


def test_second_connection_does_not_drop_again() -> None:
    adapter = _make_adapter(database="benchdb", force_recreate=True)
    admin_client = _StubAdminClient(["default", "benchdb"])

    with patch.object(adapter, "_create_admin_client", return_value=admin_client):
        adapter._reset_run_scoped_state()
        adapter.handle_existing_database()
        admin_client.executed.clear()
        adapter.handle_existing_database()

    assert admin_client.executed == []


