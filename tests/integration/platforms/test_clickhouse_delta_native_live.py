# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.platforms.clickhouse import ClickHouseAdapter
from benchbox.platforms.clickhouse.delta_lake import (
    DELTA_TABLE_FUNCTION_NAMES,
    delta_engine_probe_sql,
    delta_function_probe_sql,
    delta_lake_count_sql,
    delta_lake_engine_ddl,
    has_native_delta_registration,
)

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


def _query_names(connection, sql: str) -> list[str]:
    rows = connection.execute(sql)
    return [str(row[0]) for row in rows] if rows else []


class TestNativeDeltaCapability:
    def test_server_version(self, clickhouse_adapter) -> None:
        connection = clickhouse_adapter.create_connection()
        try:
            rows = connection.execute("SELECT version()")
            assert rows and str(rows[0][0]).strip()
        finally:
            clickhouse_adapter.close_connection(connection)

    def test_native_delta_support(self, clickhouse_adapter) -> None:
        connection = clickhouse_adapter.create_connection()
        try:
            functions = _query_names(connection, delta_function_probe_sql())
            engines = _query_names(connection, delta_engine_probe_sql())
            version = str(connection.execute("SELECT version()")[0][0])
            missing = [name for name in DELTA_TABLE_FUNCTION_NAMES if name not in functions]
            assert not missing, f"Server {version} lacks Delta functions: {missing}"
            assert has_native_delta_registration(functions, engines), (
                f"Server {version} lacks native Delta registration: functions={functions} engines={engines}"
            )
        finally:
            clickhouse_adapter.close_connection(connection)

    def test_adapter_selects_native_for_s3(self, clickhouse_adapter) -> None:
        connection = clickhouse_adapter.create_connection()
        try:
            reader = clickhouse_adapter.delta_reader_for(connection, "s3://bucket/orders")
            assert reader.kind == "native"
            assert reader.source_sql == "deltaLake('s3://bucket/orders')"
        finally:
            clickhouse_adapter.close_connection(connection)


PUBLIC_DELTA_URL = "https://clickhouse-public-datasets.s3.amazonaws.com/delta_lake/hits/"


class TestPublicS3DeltaEndToEnd:
    def test_table_function_read(self, clickhouse_adapter) -> None:
        connection = clickhouse_adapter.create_connection()
        try:
            reader = clickhouse_adapter.delta_reader_for(connection, PUBLIC_DELTA_URL)
            assert reader.kind == "native"
            assert reader.location_kind == "s3"
            source = reader.source_sql
            rows = connection.execute(f"SELECT URL, UserAgent FROM {source} WHERE URL IS NOT NULL LIMIT 2")
            assert len(rows) == 2
            assert all(row[0] for row in rows)
            count = connection.execute(delta_lake_count_sql(source))
            assert count[0][0] > 0
        finally:
            clickhouse_adapter.close_connection(connection)

    def test_engine_attach_read(self, clickhouse_adapter) -> None:
        connection = clickhouse_adapter.create_connection()
        try:
            connection.execute(delta_lake_engine_ddl("hits_delta_e2e", PUBLIC_DELTA_URL))
            rows = connection.execute("SELECT URL FROM hits_delta_e2e WHERE URL IS NOT NULL LIMIT 2")
            assert len(rows) == 2
        finally:
            connection.execute("DROP TABLE IF EXISTS hits_delta_e2e")
            clickhouse_adapter.close_connection(connection)


