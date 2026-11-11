# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.


import os

import pytest

from .conftest import get_env_or_skip, skip_unless_docker_service

try:
    from psycopg import sql as psycopg_sql
except ImportError:
    psycopg_sql = None

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker_integration,
    pytest.mark.live_integration,
    pytest.mark.live_pg_mooncake,
]


class TestLivePgMooncakeConnection:
    def test_connection(self, live_pg_mooncake_adapter):

        connection = live_pg_mooncake_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            assert result[0] == 1
        finally:
            live_pg_mooncake_adapter.close_connection(connection)

    def test_extension_loaded(self, live_pg_mooncake_adapter):

        connection = live_pg_mooncake_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'pg_mooncake'")
            result = cursor.fetchone()
            assert result is not None, "pg_mooncake extension not installed"
            assert result[0], "pg_mooncake version should be non-empty"
        finally:
            live_pg_mooncake_adapter.close_connection(connection)

    def test_platform_info(self, live_pg_mooncake_adapter):

        info = live_pg_mooncake_adapter.get_platform_info()
        assert info is not None


class TestLivePgMooncakeQueryExecution:
    def test_columnstore_table(self, live_pg_mooncake_adapter):
        connection = live_pg_mooncake_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("DROP TABLE IF EXISTS benchbox_smoke_test")
            cursor.execute(
                "CREATE TABLE benchbox_smoke_test (id INT, name TEXT, value DOUBLE PRECISION) USING columnstore"
            )
            cursor.execute(
                "INSERT INTO benchbox_smoke_test VALUES (1, 'alpha', 10.5), (2, 'beta', 20.3), (3, 'gamma', 30.1)"
            )
            cursor.execute("SELECT COUNT(*) FROM benchbox_smoke_test")
            result = cursor.fetchone()
            assert result[0] == 3
        finally:
            try:
                cursor = connection.cursor()
                cursor.execute("DROP TABLE IF EXISTS benchbox_smoke_test")
            except Exception:
                pass
            live_pg_mooncake_adapter.close_connection(connection)

    def test_aggregation_query(self, live_pg_mooncake_adapter):
        connection = live_pg_mooncake_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT COUNT(*) AS cnt, SUM(x) AS total FROM generate_series(1, 100) AS t(x)")
            result = cursor.fetchone()
            assert result[0] == 100
            assert result[1] == 5050
        finally:
            live_pg_mooncake_adapter.close_connection(connection)


class TestLivePgMooncakeObjectStorage:
    @pytest.fixture
    def s3_mooncake_bucket(self):
        bucket = os.getenv("MOONCAKE_S3_BUCKET")
        if not bucket:
            pytest.skip("Requires MOONCAKE_S3_BUCKET for object storage tests")
        get_env_or_skip("AWS_ACCESS_KEY_ID", "S3")
        get_env_or_skip("AWS_SECRET_ACCESS_KEY", "S3")
        return bucket

    def test_s3_storage_mode(self, live_pg_mooncake_adapter, s3_mooncake_bucket):
        connection = live_pg_mooncake_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(
                psycopg_sql.SQL("SET mooncake.default_bucket = {}").format(psycopg_sql.Literal(s3_mooncake_bucket))
            )
            cursor.execute("DROP TABLE IF EXISTS benchbox_s3_smoke_test")
            cursor.execute("CREATE TABLE benchbox_s3_smoke_test (id INT, value DOUBLE PRECISION) USING columnstore")
            cursor.execute("INSERT INTO benchbox_s3_smoke_test VALUES (1, 42.0)")
            cursor.execute("SELECT COUNT(*) FROM benchbox_s3_smoke_test")
            result = cursor.fetchone()
            assert result[0] == 1
        finally:
            try:
                cursor = connection.cursor()
                cursor.execute("DROP TABLE IF EXISTS benchbox_s3_smoke_test")
            except Exception:
                pass
            live_pg_mooncake_adapter.close_connection(connection)


class TestLivePgMooncakeWALReplication:
    def test_wal_level(self, live_pg_mooncake_adapter):

        connection = live_pg_mooncake_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SHOW wal_level")
            result = cursor.fetchone()
            assert result[0] in ("replica", "logical"), (
                f"WAL level is '{result[0]}', expected 'replica' or 'logical' for replication testing"
            )
        finally:
            live_pg_mooncake_adapter.close_connection(connection)

    def test_write_and_verify_wal(self, live_pg_mooncake_adapter):
        connection = live_pg_mooncake_adapter.create_connection()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("SELECT pg_current_wal_lsn()")
                before_lsn = cursor.fetchone()[0]
            except Exception as exc:
                pytest.skip(f"pg_current_wal_lsn() not accessible (requires superuser): {exc}")

            cursor.execute("DROP TABLE IF EXISTS benchbox_wal_test")
            cursor.execute("CREATE TABLE benchbox_wal_test (id INT, data TEXT)")
            cursor.execute("INSERT INTO benchbox_wal_test SELECT g, repeat('x', 100) FROM generate_series(1, 100) g")

            cursor.execute("SELECT pg_current_wal_lsn()")
            after_lsn = cursor.fetchone()[0]
            assert before_lsn != after_lsn, "WAL position should advance after writes"
        finally:
            try:
                cursor = connection.cursor()
                cursor.execute("DROP TABLE IF EXISTS benchbox_wal_test")
            except Exception:
                pass
            live_pg_mooncake_adapter.close_connection(connection)


