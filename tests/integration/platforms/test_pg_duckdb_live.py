# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.


import os

import pytest

from .conftest import get_env_or_skip, skip_unless_docker_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker_integration,
    pytest.mark.live_integration,
    pytest.mark.live_pg_duckdb,
]


class TestLivePgDuckDBConnection:
    def test_connection(self, live_pg_duckdb_adapter):

        connection = live_pg_duckdb_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            assert result[0] == 1
        finally:
            live_pg_duckdb_adapter.close_connection(connection)

    def test_extension_loaded(self, live_pg_duckdb_adapter):

        connection = live_pg_duckdb_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'pg_duckdb'")
            result = cursor.fetchone()
            assert result is not None, "pg_duckdb extension not installed"
            assert result[0], "pg_duckdb version should be non-empty"
        finally:
            live_pg_duckdb_adapter.close_connection(connection)

    def test_platform_info(self, live_pg_duckdb_adapter):

        info = live_pg_duckdb_adapter.get_platform_info()
        assert info is not None
        assert info.get("platform_type") == "pg_duckdb"
        assert info.get("platform_name")
        assert "configuration" in info


class TestLivePgDuckDBQueryExecution:
    def test_duckdb_execution(self, live_pg_duckdb_adapter):

        connection = live_pg_duckdb_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT COUNT(*) AS cnt, SUM(x) AS total FROM generate_series(1, 100) AS t(x)")
            result = cursor.fetchone()
            assert result[0] == 100
            assert result[1] == 5050
        finally:
            live_pg_duckdb_adapter.close_connection(connection)

    def test_create_and_query_table(self, live_pg_duckdb_adapter):
        connection = live_pg_duckdb_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("DROP TABLE IF EXISTS benchbox_smoke_test")
            cursor.execute("CREATE TABLE benchbox_smoke_test (id INT, name TEXT, value DOUBLE PRECISION)")
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
            live_pg_duckdb_adapter.close_connection(connection)


class TestLivePgDuckDBMotherDuckMode:
    @pytest.fixture
    def motherduck_pg_duckdb_adapter(self):
        from benchbox.platforms.pg_duckdb import PgDuckDBAdapter

        token = os.getenv("MOTHERDUCK_TOKEN")
        if not token:
            pytest.skip("Requires MOTHERDUCK_TOKEN for MotherDuck mode")

        host = os.getenv("PG_DUCKDB_HOST", "localhost")
        port = int(os.getenv("PG_DUCKDB_PORT", "5432"))
        skip_unless_docker_service(host, port, platform="pg_duckdb")
        adapter = PgDuckDBAdapter(
            host=host,
            port=port,
            username=os.getenv("PG_DUCKDB_USER", "benchbox"),
            password=os.getenv("PG_DUCKDB_PASSWORD", "benchbox"),
            database=os.getenv("PG_DUCKDB_DATABASE", "benchbox_test"),
            deployment_mode="motherduck",
            motherduck_token=token,
        )
        yield adapter

    def test_motherduck_connection(self, motherduck_pg_duckdb_adapter):

        connection = motherduck_pg_duckdb_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            assert result[0] == 1
        finally:
            motherduck_pg_duckdb_adapter.close_connection(connection)


class TestLivePgDuckDBS3LakeQueries:
    @pytest.fixture
    def s3_test_parquet(self):
        import io

        bucket = os.getenv("BENCHBOX_S3_TEST_BUCKET")
        if not bucket:
            pytest.skip("Requires BENCHBOX_S3_TEST_BUCKET for S3 lake queries")

        access_key = get_env_or_skip("AWS_ACCESS_KEY_ID", "S3")
        secret_key = get_env_or_skip("AWS_SECRET_ACCESS_KEY", "S3")

        try:
            import boto3
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            pytest.skip(f"Requires boto3 and pyarrow: {exc}")

        key = "benchbox_test/test.parquet"
        row_count = 100

        table = pa.table({"id": list(range(row_count)), "value": [float(i) * 1.5 for i in range(row_count)]})
        buf = io.BytesIO()
        pq.write_table(table, buf)
        buf.seek(0)

        s3 = boto3.client(
            "s3",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
        s3.put_object(Bucket=bucket, Key=key, Body=buf.read())

        yield {"bucket": bucket, "key": key, "row_count": row_count}

        try:
            s3.delete_object(Bucket=bucket, Key=key)
        except Exception:
            pass

    def test_s3_parquet_query(self, live_pg_duckdb_adapter, s3_test_parquet):
        bucket = s3_test_parquet["bucket"]
        key = s3_test_parquet["key"]
        expected_rows = s3_test_parquet["row_count"]

        connection = live_pg_duckdb_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM read_parquet('s3://{bucket}/{key}')")
            result = cursor.fetchone()
            assert result[0] == expected_rows
        finally:
            live_pg_duckdb_adapter.close_connection(connection)


