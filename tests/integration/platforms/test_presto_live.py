# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.


import os
from pathlib import Path

import pytest

from benchbox.platforms.presto import PrestoAdapter

from .common import SmokeBenchmark
from .conftest import skip_unless_docker_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker_integration,
    pytest.mark.live_integration,
    pytest.mark.live_presto,
]


@pytest.fixture
def presto_adapter():
    port = int(os.getenv("PRESTO_HOST_PORT", "18081"))
    skip_unless_docker_service("localhost", port, platform="Presto")
    adapter = PrestoAdapter(
        host="localhost",
        port=port,
        user="benchbox",
        catalog="memory",
        schema="default",
    )
    adapter.skip_database_management = True
    yield adapter


class TestLivePrestoConnection:
    def test_connection(self, presto_adapter):

        connection = presto_adapter.create_connection()
        try:
            assert connection is not None
        finally:
            presto_adapter.close_connection(connection)

    def test_platform_info(self, presto_adapter):

        info = presto_adapter.get_platform_info()
        assert info is not None
        assert info["platform_type"] == "presto"


class TestLivePrestoQueryExecution:
    def test_create_schema(self, presto_adapter):

        connection = presto_adapter.create_connection()
        try:
            presto_adapter.execute_query(
                connection,
                "CREATE SCHEMA IF NOT EXISTS memory.benchbox_test",
                query_id="Q0",
                benchmark_type="tpch",
            )
            result = presto_adapter.execute_query(
                connection,
                "SHOW SCHEMAS FROM memory LIKE 'benchbox_test'",
                query_id="Q0",
                benchmark_type="tpch",
            )
            assert result is not None
        finally:
            presto_adapter.close_connection(connection)

    def test_execute_query(self, presto_adapter):
        connection = presto_adapter.create_connection()
        try:
            result = presto_adapter.execute_query(
                connection,
                "SELECT 1",
                query_id="Q0",
                benchmark_type="tpch",
            )
            assert result is not None
        finally:
            presto_adapter.close_connection(connection)


class TestLivePrestoBulkLoad:
    def test_bulk_load_external_table(self, presto_adapter, tmp_path):
        data_root = Path(os.getenv("BENCHBOX_DATA_DIR", str(tmp_path)))
        test_dir = data_root / "presto_bulk_test"
        test_dir.mkdir(parents=True, exist_ok=True)
        csv_file = test_dir / "presto_smoke_tbl.csv"
        csv_file.write_text("1,alpha\n2,beta\n", encoding="utf-8")

        benchmark = SmokeBenchmark(
            name="SmokeBenchmark",
            create_sql="CREATE TABLE presto_smoke_tbl (id INT, value VARCHAR)",
            tables={"presto_smoke_tbl": csv_file},
        )

        adapter = PrestoAdapter(
            host=presto_adapter.host,
            port=presto_adapter.port,
            user=presto_adapter.username,
            catalog="memory",
            schema="default",
            source_catalog="hive",
        )
        adapter.skip_database_management = True
        connection = adapter.create_connection()
        cursor = connection.cursor()
        try:
            cursor.execute("DROP TABLE IF EXISTS memory.default.presto_smoke_tbl")
            cursor.execute("CREATE TABLE memory.default.presto_smoke_tbl (id INT, value VARCHAR)")
            stats, _, _ = adapter.load_data(benchmark, connection, test_dir)
            assert stats.get("presto_smoke_tbl") == 2
            cursor.execute("SELECT count(*) FROM memory.default.presto_smoke_tbl")
            count = cursor.fetchone()[0]
            assert count == 2
        finally:
            cursor.execute("DROP TABLE IF EXISTS memory.default.presto_smoke_tbl")
            adapter.close_connection(connection)


