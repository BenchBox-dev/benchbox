# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.


import os

import pytest

from benchbox.platforms.databend import DatabendAdapter

from .conftest import skip_unless_docker_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker_integration,
    pytest.mark.live_integration,
    pytest.mark.live_databend,
]


@pytest.fixture
def databend_adapter():
    port = int(os.getenv("DATABEND_PORT", "8000"))
    skip_unless_docker_service("localhost", port, platform="Databend")
    adapter = DatabendAdapter(
        host="localhost",
        port=port,
        username="benchbox",
        password="benchbox",
        database="default",
        ssl=False,
    )
    adapter.skip_database_management = True
    yield adapter


class TestLiveDatabendConnection:
    def test_connection(self, databend_adapter):

        connection = databend_adapter.create_connection()
        try:
            assert connection is not None
        finally:
            databend_adapter.close_connection(connection)

    def test_platform_info(self, databend_adapter):

        info = databend_adapter.get_platform_info()
        assert info is not None
        assert info["platform_type"] == "databend"


class TestLiveDatabendQueryExecution:
    def test_create_schema(self, databend_adapter):

        connection = databend_adapter.create_connection()
        try:
            databend_adapter.execute_query(
                connection,
                "CREATE DATABASE IF NOT EXISTS benchbox_test",
                query_id="Q0",
                benchmark_type="tpch",
            )
            result = databend_adapter.execute_query(
                connection,
                "SHOW DATABASES LIKE 'benchbox_test'",
                query_id="Q0",
                benchmark_type="tpch",
            )
            assert result is not None
        finally:
            databend_adapter.close_connection(connection)

    def test_execute_query(self, databend_adapter):
        connection = databend_adapter.create_connection()
        try:
            result = databend_adapter.execute_query(
                connection,
                "SELECT 1",
                query_id="Q0",
                benchmark_type="tpch",
            )
            assert result is not None
        finally:
            databend_adapter.close_connection(connection)


