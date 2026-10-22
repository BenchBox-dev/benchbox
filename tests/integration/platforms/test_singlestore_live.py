# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.


import os

import pytest

from benchbox.platforms.singlestore import SingleStoreAdapter
from benchbox.utils.dialect_utils import translate_ddl

from .conftest import skip_unless_docker_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker_integration,
    pytest.mark.live_integration,
    pytest.mark.live_singlestore,
]

_HOST_PORT = int(os.getenv("SINGLESTORE_HOST_PORT", "13306"))
_PASSWORD = os.getenv("SINGLESTORE_PASSWORD", "benchbox")


@pytest.fixture(scope="module", autouse=True)
def ensure_database():
    try:
        import singlestoredb as s2

        conn = s2.connect(host="localhost", port=_HOST_PORT, user="root", password=_PASSWORD)
        cursor = conn.cursor()
        cursor.execute("CREATE DATABASE IF NOT EXISTS benchbox_test")
        cursor.close()
        conn.close()
    except Exception:
        pytest.skip(f"SingleStore not reachable at localhost:{_HOST_PORT}")


@pytest.fixture
def singlestore_adapter():
    skip_unless_docker_service("localhost", _HOST_PORT, platform="SingleStore")
    adapter = SingleStoreAdapter(
        host="localhost",
        port=_HOST_PORT,
        username="root",
        password=_PASSWORD,
        database="benchbox_test",
    )
    adapter.skip_database_management = True
    yield adapter


class TestLiveSingleStoreConnection:
    def test_connection(self, singlestore_adapter):

        connection = singlestore_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            assert result[0] == 1
        finally:
            singlestore_adapter.close_connection(connection)

    def test_platform_info(self, singlestore_adapter):

        info = singlestore_adapter.get_platform_info()
        assert info["platform_type"] == "singlestore"
        assert info["platform_name"] == "SingleStore"
        assert info["host"] == "localhost"
        assert info["port"] == _HOST_PORT

    def test_version_string(self, singlestore_adapter):

        connection = singlestore_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT @@memsql_version")
            result = cursor.fetchone()
            assert result is not None
            assert isinstance(result[0], str)
            assert len(result[0]) > 0
        finally:
            singlestore_adapter.close_connection(connection)

    def test_check_server_database_exists(self, singlestore_adapter):

        result = singlestore_adapter.check_server_database_exists(database="benchbox_test")
        assert result is True

    def test_check_server_database_exists_missing(self, singlestore_adapter):

        result = singlestore_adapter.check_server_database_exists(database="benchbox_does_not_exist_xyz")
        assert result is False


class TestLiveSingleStoreQueryExecution:
    def test_execute_select(self, singlestore_adapter):

        connection = singlestore_adapter.create_connection()
        try:
            result = singlestore_adapter.execute_query(
                connection,
                "SELECT 1 AS n",
                query_id="Q0",
                benchmark_type="tpch",
            )
            assert result is not None
        finally:
            singlestore_adapter.close_connection(connection)

    def test_aggregation_query(self, singlestore_adapter):
        connection = singlestore_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT COUNT(*), SUM(v) FROM (SELECT 1 AS v UNION ALL SELECT 2 UNION ALL SELECT 3) t")
            result = cursor.fetchone()
            assert result[0] == 3
            assert result[1] == 6
        finally:
            singlestore_adapter.close_connection(connection)

    def test_mysql_dialect(self, singlestore_adapter):
        connection = singlestore_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SHOW DATABASES")
            databases = [row[0] for row in cursor.fetchall()]
            assert "benchbox_test" in databases
        finally:
            singlestore_adapter.close_connection(connection)


class TestLiveSingleStoreDDL:
    def test_create_table_with_shard_and_sort_key(self, singlestore_adapter):

        connection = singlestore_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("DROP TABLE IF EXISTS `orders`")

            stmt = "CREATE TABLE `orders` (o_orderkey BIGINT NOT NULL, o_totalprice DECIMAL(15,2))"
            (transformed,) = translate_ddl(stmt, "singlestore", "standard", **singlestore_adapter._ddl_options())

            assert "SHARD KEY (`o_orderkey`)" in transformed
            assert "SORT KEY (`o_orderkey`)" in transformed
            cursor.execute(transformed)

            cursor.execute("SHOW TABLES LIKE 'orders'")
            assert len(cursor.fetchall()) == 1
        finally:
            try:
                cursor = connection.cursor()
                cursor.execute("DROP TABLE IF EXISTS `orders`")
            except Exception:
                pass
            singlestore_adapter.close_connection(connection)

    def test_create_reference_table(self, singlestore_adapter):
        connection = singlestore_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("DROP TABLE IF EXISTS `nation`")

            stmt = "CREATE TABLE `nation` (n_nationkey INT NOT NULL, n_name VARCHAR(25))"
            (transformed,) = translate_ddl(stmt, "singlestore", "standard", **singlestore_adapter._ddl_options())

            assert "CREATE REFERENCE TABLE" in transformed
            cursor.execute(transformed)

            cursor.execute("SHOW TABLES LIKE 'nation'")
            assert len(cursor.fetchall()) == 1
        finally:
            try:
                cursor = connection.cursor()
                cursor.execute("DROP TABLE IF EXISTS `nation`")
            except Exception:
                pass
            singlestore_adapter.close_connection(connection)

    def test_insert_and_query(self, singlestore_adapter):
        connection = singlestore_adapter.create_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("DROP TABLE IF EXISTS `part`")
            cursor.execute(
                "CREATE TABLE `part` (p_partkey BIGINT NOT NULL, p_name VARCHAR(55),\n"
                "SHARD KEY (p_partkey),\nSORT KEY (p_partkey)\n)"
            )
            cursor.execute("INSERT INTO `part` VALUES (1, 'brass'), (2, 'copper'), (3, 'nickel')")
            cursor.execute("SELECT COUNT(*) FROM `part`")
            assert cursor.fetchone()[0] == 3
        finally:
            try:
                cursor = connection.cursor()
                cursor.execute("DROP TABLE IF EXISTS `part`")
            except Exception:
                pass
            singlestore_adapter.close_connection(connection)
