# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.


import pytest

from benchbox.platforms.clickhouse import ClickHouseAdapter

from .conftest import skip_unless_docker_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker_integration,
    pytest.mark.live_integration,
    pytest.mark.live_clickhouse,
]


@pytest.fixture
def clickhouse_adapter():
    skip_unless_docker_service("localhost", 9000, platform="ClickHouse")
    adapter = ClickHouseAdapter(
        host="localhost",
        port=9000,
        deployment_mode="server",
        database="benchbox_test",
        password="benchbox",
    )
    adapter.skip_database_management = True
    yield adapter


class TestLiveClickHouseConnection:
    def test_connection(self, clickhouse_adapter):

        connection = clickhouse_adapter.create_connection()
        try:
            assert connection is not None
        finally:
            clickhouse_adapter.close_connection(connection)

    def test_platform_info(self, clickhouse_adapter):

        info = clickhouse_adapter.get_platform_info()
        assert info is not None
        assert info["platform_type"] == "clickhouse"


class TestLiveClickHouseQueryExecution:
    def test_create_schema(self, clickhouse_adapter):

        connection = clickhouse_adapter.create_connection()
        try:
            clickhouse_adapter.execute_query(
                connection,
                "CREATE DATABASE IF NOT EXISTS benchbox_test",
                query_id="Q0",
                benchmark_type="tpch",
            )
            result = clickhouse_adapter.execute_query(
                connection,
                "SHOW DATABASES LIKE 'benchbox_test'",
                query_id="Q0",
                benchmark_type="tpch",
            )
            assert result is not None
        finally:
            clickhouse_adapter.close_connection(connection)

    def test_execute_query(self, clickhouse_adapter):
        connection = clickhouse_adapter.create_connection()
        try:
            result = clickhouse_adapter.execute_query(
                connection,
                "SELECT 1",
                query_id="Q0",
                benchmark_type="tpch",
            )
            assert result is not None
        finally:
            clickhouse_adapter.close_connection(connection)


