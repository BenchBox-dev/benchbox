# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.


import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker_integration,
    pytest.mark.live_integration,
    pytest.mark.live_firebolt_core,
]


class TestLiveFireboltCoreConnection:
    def test_connection(self, live_firebolt_core_adapter):

        connection = live_firebolt_core_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            assert result[0] == 1
        finally:
            live_firebolt_core_adapter.close_connection(connection)

    def test_platform_info(self, live_firebolt_core_adapter):

        info = live_firebolt_core_adapter.get_platform_info()
        assert info is not None
        assert info.get("platform_type") == "firebolt"
        assert info.get("connection_mode") == "core"
        assert "url" in info


class TestLiveFireboltCoreQueryExecution:
    def test_execute_query_via_adapter(self, live_firebolt_core_adapter):
        connection = live_firebolt_core_adapter.create_connection()
        try:
            result = live_firebolt_core_adapter.execute_query(
                connection,
                "SELECT 1",
                query_id="Q0",
                benchmark_type="tpch",
            )
            assert result is not None
        finally:
            live_firebolt_core_adapter.close_connection(connection)

    def test_aggregation_query(self, live_firebolt_core_adapter):
        connection = live_firebolt_core_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS cnt, SUM(x) AS total FROM (SELECT 1 AS x UNION ALL SELECT 2 UNION ALL SELECT 3)"
            )
            result = cursor.fetchone()
            assert result[0] == 3
            assert result[1] == 6
        finally:
            live_firebolt_core_adapter.close_connection(connection)

    def test_create_and_query_table(self, live_firebolt_core_adapter):
        connection = live_firebolt_core_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("DROP TABLE IF EXISTS benchbox_smoke_test")
            cursor.execute("CREATE TABLE benchbox_smoke_test (id INT, name TEXT, value DOUBLE PRECISION)")
            cursor.execute(
                "INSERT INTO benchbox_smoke_test VALUES (1, 'alpha', 10.5), (2, 'beta', 20.3), (3, 'gamma', 30.1)"
            )
            cursor.execute("SELECT COUNT(*) FROM benchbox_smoke_test")
            assert cursor.fetchone()[0] == 3
        finally:
            try:
                cursor = connection.cursor()
                cursor.execute("DROP TABLE IF EXISTS benchbox_smoke_test")
            except Exception:
                pass
            live_firebolt_core_adapter.close_connection(connection)


class TestLiveFireboltCoreSpecificFeatures:
    def test_result_cache_set(self, live_firebolt_core_adapter):
        connection = live_firebolt_core_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SET enable_result_cache = false")
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            assert result[0] == 1
        finally:
            live_firebolt_core_adapter.close_connection(connection)

    def test_drop_table_is_synchronous(self, live_firebolt_core_adapter):
        connection = live_firebolt_core_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("CREATE TABLE IF NOT EXISTS benchbox_cleanup_test (id INT)")
            cursor.execute("DROP TABLE IF EXISTS benchbox_cleanup_test")
            try:
                cursor.execute("SELECT COUNT(*) FROM benchbox_cleanup_test")
                pytest.fail("Table should have been dropped")
            except Exception:
                pass
        finally:
            live_firebolt_core_adapter.close_connection(connection)


