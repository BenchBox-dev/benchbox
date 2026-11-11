# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.


import os

import pymysql
import pytest

from benchbox.platforms.doris import DorisAdapter

from .conftest import skip_unless_docker_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker_integration,
    pytest.mark.live_integration,
    pytest.mark.live_doris,
]


@pytest.fixture(scope="module", autouse=True)
def ensure_database():
    port = int(os.getenv("DORIS_HOST_PORT", "19031"))
    try:
        conn = pymysql.connect(host="localhost", port=port, user="root", password="", autocommit=True)
        cursor = conn.cursor()
        cursor.execute("CREATE DATABASE IF NOT EXISTS benchbox_test")
        cursor.close()
        conn.close()
    except Exception:
        pytest.skip(f"Doris not reachable at localhost:{port}")


@pytest.fixture
def doris_adapter():
    port = int(os.getenv("DORIS_HOST_PORT", "19031"))
    http_port = int(os.getenv("DORIS_HTTP_PORT", "18030"))
    skip_unless_docker_service("localhost", port, platform="Doris")
    adapter = DorisAdapter(
        host="localhost",
        port=port,
        user="root",
        password="",
        database="benchbox_test",
        http_port=http_port,
    )
    adapter.skip_database_management = True
    yield adapter


class TestLiveDorisConnection:
    def test_connection(self, doris_adapter):

        connection = doris_adapter.create_connection()
        try:
            assert hasattr(connection, "cursor")
        finally:
            doris_adapter.close_connection(connection)

    def test_platform_info(self, doris_adapter):

        info = doris_adapter.get_platform_info()
        assert info is not None
        assert info["platform_type"] == "doris"

    def test_platform_version_is_4_x(self, doris_adapter):
        connection = doris_adapter.create_connection()
        try:
            info = doris_adapter.get_platform_info(connection)
            version = str(info.get("platform_version", ""))
            assert version.startswith("4."), f"Expected Doris 4.x from docker/doris/docker-compose.yml, got {version!r}"
        finally:
            doris_adapter.close_connection(connection)


class TestLiveDorisQueryExecution:
    def test_create_schema(self, doris_adapter):

        connection = doris_adapter.create_connection()
        try:
            doris_adapter.execute_query(
                connection,
                "CREATE DATABASE IF NOT EXISTS benchbox_test",
                query_id="Q0",
                benchmark_type="tpch",
            )
            result = doris_adapter.execute_query(
                connection,
                "SHOW DATABASES LIKE 'benchbox_test'",
                query_id="Q0",
                benchmark_type="tpch",
            )
            assert isinstance(result, dict)
        finally:
            doris_adapter.close_connection(connection)

    def test_execute_query(self, doris_adapter):
        connection = doris_adapter.create_connection()
        try:
            result = doris_adapter.execute_query(
                connection,
                "SELECT 1",
                query_id="Q0",
                benchmark_type="tpch",
            )
            assert isinstance(result, dict)
        finally:
            doris_adapter.close_connection(connection)


