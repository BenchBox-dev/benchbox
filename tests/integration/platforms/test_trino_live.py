# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.


import os
from pathlib import Path

import pytest

from benchbox.platforms.trino import TrinoAdapter

from .common import SmokeBenchmark
from .conftest import skip_unless_docker_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker_integration,
    pytest.mark.live_integration,
    pytest.mark.live_trino,
]


@pytest.fixture
def trino_adapter():
    port = int(os.getenv("TRINO_HOST_PORT", "18080"))
    skip_unless_docker_service("localhost", port, platform="Trino")
    adapter = TrinoAdapter(
        host="localhost",
        port=port,
        user="benchbox",
        catalog="memory",
        schema="default",
    )
    adapter.skip_database_management = True
    yield adapter


class TestLiveTrinoConnection:
    def test_connection(self, trino_adapter):

        connection = trino_adapter.create_connection()
        try:
            assert hasattr(connection, "cursor")
        finally:
            trino_adapter.close_connection(connection)

    def test_platform_info(self, trino_adapter):

        info = trino_adapter.get_platform_info()
        assert info is not None
        assert info["platform_type"] == "trino"


class TestLiveTrinoQueryExecution:
    def test_create_schema(self, trino_adapter):

        connection = trino_adapter.create_connection()
        try:
            trino_adapter.execute_query(
                connection,
                "CREATE SCHEMA IF NOT EXISTS memory.benchbox_test",
                query_id="Q0",
                benchmark_type="tpch",
            )
            result = trino_adapter.execute_query(
                connection,
                "SHOW SCHEMAS FROM memory LIKE 'benchbox_test'",
                query_id="Q0",
                benchmark_type="tpch",
            )
            assert isinstance(result, dict)
        finally:
            trino_adapter.close_connection(connection)

    def test_execute_query(self, trino_adapter):
        connection = trino_adapter.create_connection()
        try:
            result = trino_adapter.execute_query(
                connection,
                "SELECT 1",
                query_id="Q0",
                benchmark_type="tpch",
            )
            assert isinstance(result, dict)
        finally:
            trino_adapter.close_connection(connection)


class TestLiveTrinoBulkLoad:
    def test_bulk_load_external_table(self, trino_adapter, tmp_path):
        data_root = Path(os.getenv("BENCHBOX_DATA_DIR", str(tmp_path)))
        test_dir = data_root / "trino_bulk_test"
        test_dir.mkdir(parents=True, exist_ok=True)
        csv_file = test_dir / "trino_smoke_tbl.csv"
        csv_file.write_text("1,alpha\n2,beta\n", encoding="utf-8")

        benchmark = SmokeBenchmark(
            name="SmokeBenchmark",
            create_sql="CREATE TABLE trino_smoke_tbl (id INT, value VARCHAR)",
            tables={"trino_smoke_tbl": csv_file},
        )

        adapter = TrinoAdapter(
            host=trino_adapter.host,
            port=trino_adapter.port,
            user=trino_adapter.username,
            catalog="memory",
            schema="default",
            source_catalog="hive",
        )
        adapter.skip_database_management = True
        connection = adapter.create_connection()
        cursor = connection.cursor()
        try:
            cursor.execute("DROP TABLE IF EXISTS memory.default.trino_smoke_tbl")
            cursor.execute("CREATE TABLE memory.default.trino_smoke_tbl (id INT, value VARCHAR)")
            stats, _, _ = adapter.load_data(benchmark, connection, test_dir)
            assert stats.get("trino_smoke_tbl") == 2
            cursor.execute("SELECT count(*) FROM memory.default.trino_smoke_tbl")
            count = cursor.fetchone()[0]
            assert count == 2
        finally:
            cursor.execute("DROP TABLE IF EXISTS memory.default.trino_smoke_tbl")
            adapter.close_connection(connection)

    def test_bulk_load_rejects_values_the_column_type_cannot_hold(self, trino_adapter, tmp_path):
        data_root = Path(os.getenv("BENCHBOX_DATA_DIR", str(tmp_path)))
        test_dir = data_root / "trino_bulk_bad_test"
        test_dir.mkdir(parents=True, exist_ok=True)
        csv_file = test_dir / "trino_bad_tbl.csv"
        csv_file.write_text("1,alpha\nnot-a-number,beta\n", encoding="utf-8")

        benchmark = SmokeBenchmark(
            name="SmokeBenchmark",
            create_sql="CREATE TABLE trino_bad_tbl (id INT, value VARCHAR(3))",
            tables={"trino_bad_tbl": csv_file},
        )
        adapter = TrinoAdapter(
            host=trino_adapter.host,
            port=trino_adapter.port,
            user=trino_adapter.username,
            catalog="memory",
            schema="default",
            source_catalog="hive",
        )
        adapter.skip_database_management = True
        connection = adapter.create_connection()
        cursor = connection.cursor()
        try:
            cursor.execute("DROP TABLE IF EXISTS memory.default.trino_bad_tbl")
            cursor.execute("CREATE TABLE memory.default.trino_bad_tbl (id INT, value VARCHAR(3))")
            stats, _, _ = adapter.load_data(benchmark, connection, test_dir)
            assert stats.get("trino_bad_tbl") == 0
            cursor.execute("SELECT count(*) FROM memory.default.trino_bad_tbl WHERE id IS NULL")
            assert cursor.fetchone()[0] == 0
        finally:
            cursor.execute("DROP TABLE IF EXISTS memory.default.trino_bad_tbl")
            adapter.close_connection(connection)
