# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from .common import install_lakesail_stub

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.mark.platform_smoke
class TestLakeSailStubConnection:
    def test_create_and_close_connection(self, monkeypatch):

        state = install_lakesail_stub(monkeypatch)

        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter(
            endpoint=state.endpoint,
            database=state.database,
        )
        connection = adapter.create_connection()
        assert connection is not None

        adapter.close_connection(connection)

    def test_platform_info(self, monkeypatch):

        state = install_lakesail_stub(monkeypatch)

        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter(
            endpoint=state.endpoint,
            database=state.database,
        )
        connection = adapter.create_connection()
        try:
            info = adapter.get_platform_info(connection=connection)
            assert info["platform_type"] == "lakesail"
            assert info["platform_name"] == "LakeSail Sail"
            assert info["endpoint"] == state.endpoint
            assert info["platform_version"] == "3.5.0-sail"
        finally:
            adapter.close_connection(connection)

    def test_execute_query(self, monkeypatch):

        state = install_lakesail_stub(monkeypatch)

        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter(
            endpoint=state.endpoint,
            database=state.database,
        )
        connection = adapter.create_connection()
        try:
            result = adapter.execute_query(connection, "SELECT 1 AS value", query_id="Q0", benchmark_type="tpch")
            assert result["status"] == "SUCCESS"
            assert "SELECT 1 AS value" in state.statements
        finally:
            adapter.close_connection(connection)

    def test_check_database_exists(self, monkeypatch):

        state = install_lakesail_stub(monkeypatch)

        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter(
            endpoint=state.endpoint,
            database=state.database,
        )
        assert adapter.check_server_database_exists(database="benchbox_test") is True
        assert adapter.check_server_database_exists(database="nonexistent_db") is False

    def test_spark_connect_endpoint_configured(self, monkeypatch):

        state = install_lakesail_stub(monkeypatch, endpoint="sc://custom:9999")

        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter(
            endpoint="sc://custom:9999",
            database="test_db",
        )
        connection = adapter.create_connection()
        try:
            assert state.endpoint == "sc://custom:9999"
        finally:
            adapter.close_connection(connection)
