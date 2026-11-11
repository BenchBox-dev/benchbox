# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.


import pytest

from benchbox.platforms.influxdb import InfluxDBAdapter

from .conftest import skip_unless_docker_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker_integration,
    pytest.mark.live_integration,
    pytest.mark.live_influxdb,
]


@pytest.fixture(scope="module")
def influxdb_adapter():
    skip_unless_docker_service("localhost", 8181, platform="InfluxDB")
    adapter = InfluxDBAdapter(
        host="localhost",
        port=8181,
        token="test",
        database="benchbox_test",
        mode="core",
        ssl=False,
    )
    adapter.skip_database_management = True
    return adapter


@pytest.fixture(scope="module", autouse=True)
def seed_test_data(influxdb_adapter):
    from benchbox.platforms.influxdb.client import InfluxDBConnection

    conn = InfluxDBConnection(
        host="localhost",
        port=8181,
        token="test",
        database="benchbox_test",
        ssl=False,
    )
    conn.connect()
    try:
        conn.write_line_protocol(["cpu,host=server01 usage=42.0"])
    finally:
        conn.close()


class TestLiveInfluxDBConnection:
    def test_connection(self, influxdb_adapter):

        connection = influxdb_adapter.create_connection()
        try:
            assert connection is not None
        finally:
            influxdb_adapter.close_connection(connection)

    def test_platform_info(self, influxdb_adapter):

        info = influxdb_adapter.get_platform_info()
        assert info is not None
        assert info["platform_type"] == "influxdb"


class TestLiveInfluxDBQueryExecution:
    def test_execute_query(self, influxdb_adapter):

        connection = influxdb_adapter.create_connection()
        try:
            result = influxdb_adapter.execute_query(
                connection,
                "SELECT * FROM cpu",
                query_id="Q0",
                validate_row_count=False,
            )
            assert result["status"] == "SUCCESS"
            assert result["rows_returned"] >= 1
            assert result["fetch_mode"] == influxdb_adapter.fetch_mode
        finally:
            influxdb_adapter.close_connection(connection)


