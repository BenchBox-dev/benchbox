# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.


import pytest

from .conftest import skip_unless_docker_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker_integration,
    pytest.mark.live_integration,
    pytest.mark.live_cedardb,
]


class TestLiveCedarDBConnection:
    def test_connection(self, live_cedardb_adapter):

        connection = live_cedardb_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            assert result[0] == 1
        finally:
            live_cedardb_adapter.close_connection(connection)

    def test_platform_info(self, live_cedardb_adapter):

        connection = live_cedardb_adapter.create_connection()
        try:
            info = live_cedardb_adapter.get_platform_info(connection=connection)
            assert info["platform_type"] == "cedardb"
            assert info["platform_name"] == "CedarDB"
        finally:
            live_cedardb_adapter.close_connection(connection)

    def test_version_string(self, live_cedardb_adapter):
        connection = live_cedardb_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT version()")
            result = cursor.fetchone()
            assert result is not None
            assert isinstance(result[0], str)
            assert len(result[0]) > 0
        finally:
            live_cedardb_adapter.close_connection(connection)


class TestLiveCedarDBQueryExecution:
    def test_execute_select(self, live_cedardb_adapter):

        connection = live_cedardb_adapter.create_connection()
        try:
            result = live_cedardb_adapter.execute_query(
                connection,
                "SELECT 1 AS n",
                query_id="Q0",
                benchmark_type="tpch",
            )
            assert result is not None
        finally:
            live_cedardb_adapter.close_connection(connection)

    def test_aggregation_query(self, live_cedardb_adapter):
        connection = live_cedardb_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT COUNT(*) AS cnt, SUM(x) AS total FROM generate_series(1, 100) AS t(x)")
            result = cursor.fetchone()
            assert result[0] == 100
            assert result[1] == 5050
        finally:
            live_cedardb_adapter.close_connection(connection)

    def test_create_and_query_table(self, live_cedardb_adapter):
        connection = live_cedardb_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("DROP TABLE IF EXISTS benchbox_smoke_test")
            cursor.execute("CREATE TABLE benchbox_smoke_test (id INTEGER, label TEXT, value DOUBLE PRECISION)")
            cursor.execute(
                "INSERT INTO benchbox_smoke_test VALUES (1, 'alpha', 10.5), (2, 'beta', 20.3), (3, 'gamma', 30.1)"
            )
            cursor.execute("SELECT COUNT(*) FROM benchbox_smoke_test")
            result = cursor.fetchone()
            assert result[0] == 3

            cursor.execute("SELECT SUM(value) FROM benchbox_smoke_test")
            total = cursor.fetchone()[0]
            assert abs(total - 60.9) < 0.001
        finally:
            try:
                cursor = connection.cursor()
                cursor.execute("DROP TABLE IF EXISTS benchbox_smoke_test")
            except Exception:
                pass
            live_cedardb_adapter.close_connection(connection)

    def test_postgresql_dialect_compatibility(self, live_cedardb_adapter):
        connection = live_cedardb_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                WITH nums AS (
                    SELECT g AS n FROM generate_series(1, 5) AS t(g)
                )
                SELECT n, SUM(n) OVER (ORDER BY n) AS running_total
                FROM nums
                ORDER BY n
                """
            )
            rows = cursor.fetchall()
            assert len(rows) == 5
            assert rows[-1][1] == 15
        finally:
            live_cedardb_adapter.close_connection(connection)


class TestLiveCedarDBConstraints:
    def test_primary_key_constraint(self, live_cedardb_adapter):

        connection = live_cedardb_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("DROP TABLE IF EXISTS benchbox_pk_test")
            cursor.execute("CREATE TABLE benchbox_pk_test (id INTEGER PRIMARY KEY, name TEXT)")
            cursor.execute("INSERT INTO benchbox_pk_test VALUES (1, 'first')")

            with pytest.raises(Exception):
                cursor.execute("INSERT INTO benchbox_pk_test VALUES (1, 'duplicate')")
                connection.commit()
        finally:
            try:
                connection.rollback()
                cursor = connection.cursor()
                cursor.execute("DROP TABLE IF EXISTS benchbox_pk_test")
            except Exception:
                pass
            live_cedardb_adapter.close_connection(connection)


