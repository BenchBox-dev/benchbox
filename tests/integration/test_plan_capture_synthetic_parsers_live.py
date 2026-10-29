# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.


import os

import pytest

from tests.integration._live_plan_shapes import (
    SHAPE_QUERIES,
    ShapeTraits,
    assert_live_plan_shape,
    cursor_executor,
    plan_tables,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]

_SHAPES = tuple(SHAPE_QUERIES)

_CURSOR_DDL = (
    "CREATE TABLE {orders} (o_id BIGINT, o_cust BIGINT, o_total DOUBLE)",
    "CREATE TABLE {customers} (c_id BIGINT, c_name VARCHAR, c_region VARCHAR)",
)

_CLICKHOUSE_DDL = (
    "CREATE TABLE {orders} (o_id Int64, o_cust Int64, o_total Float64) ENGINE = MergeTree ORDER BY o_id",
    "CREATE TABLE {customers} (c_id Int64, c_name String, c_region String) ENGINE = MergeTree ORDER BY c_id",
)

_SPARK_DDL = (
    "CREATE TABLE {orders} (o_id BIGINT, o_cust BIGINT, o_total DOUBLE) USING parquet",
    "CREATE TABLE {customers} (c_id BIGINT, c_name STRING, c_region STRING) USING parquet",
)


def _plan_tables_fixture(execute, ddl):
    with plan_tables(execute, ddl):
        yield


@pytest.mark.skipif(not os.environ.get("PRESTO_HOST"), reason="live Presto not available (PRESTO_HOST unset)")
class TestLivePrestoQueryPlanCapture:
    @pytest.fixture
    def adapter(self):
        from benchbox.platforms.presto import PrestoAdapter

        return PrestoAdapter(
            host=os.environ["PRESTO_HOST"],
            port=int(os.environ.get("PRESTO_PORT", "8080")),
            username=os.environ.get("PRESTO_USER", "benchbox"),
            catalog=os.environ.get("PRESTO_CATALOG", "memory"),
            schema=os.environ.get("PRESTO_SCHEMA", "default"),
            capture_plans=True,
        )

    @pytest.fixture
    def connection(self, adapter):
        conn = adapter.create_connection()
        try:
            yield conn
        finally:
            adapter.close_connection(conn)

    @pytest.fixture
    def tables(self, connection):
        yield from _plan_tables_fixture(cursor_executor(connection), _CURSOR_DDL)

    @pytest.mark.parametrize("shape", _SHAPES)
    def test_live_plan_semantics(self, adapter, connection, tables, shape):
        assert_live_plan_shape(adapter, connection, shape, traits=ShapeTraits(union_operator=False))


@pytest.mark.skipif(not os.environ.get("TRINO_HOST"), reason="live Trino not available (TRINO_HOST unset)")
class TestLiveTrinoQueryPlanCapture:
    @pytest.fixture
    def adapter(self):
        from benchbox.platforms.trino import TrinoAdapter

        return TrinoAdapter(
            host=os.environ["TRINO_HOST"],
            port=int(os.environ.get("TRINO_PORT", "8080")),
            username=os.environ.get("TRINO_USER", "benchbox"),
            catalog=os.environ.get("TRINO_CATALOG", "memory"),
            schema=os.environ.get("TRINO_SCHEMA", "default"),
            capture_plans=True,
        )

    @pytest.fixture
    def connection(self, adapter):
        conn = adapter.create_connection()
        try:
            yield conn
        finally:
            adapter.close_connection(conn)

    @pytest.fixture
    def tables(self, connection):
        yield from _plan_tables_fixture(cursor_executor(connection), _CURSOR_DDL)

    @pytest.mark.parametrize("shape", _SHAPES)
    def test_live_plan_semantics(self, adapter, connection, tables, shape):
        assert_live_plan_shape(adapter, connection, shape, traits=ShapeTraits(union_operator=False))


@pytest.mark.skipif(
    not os.environ.get("CLICKHOUSE_HOST"), reason="live ClickHouse not available (CLICKHOUSE_HOST unset)"
)
class TestLiveClickHouseQueryPlanCapture:
    @pytest.fixture
    def adapter(self):
        from benchbox.platforms.clickhouse.adapter import ClickHouseAdapter

        return ClickHouseAdapter(
            deployment_mode="server",
            host=os.environ["CLICKHOUSE_HOST"],
            port=int(os.environ.get("CLICKHOUSE_PORT", "9000")),
            username=os.environ.get("CLICKHOUSE_USER", "default"),
            password=os.environ.get("CLICKHOUSE_PASSWORD", ""),
            database=os.environ.get("CLICKHOUSE_DATABASE", "default"),
            capture_plans=True,
        )

    @pytest.fixture
    def connection(self, adapter):
        conn = adapter.create_connection()
        try:
            yield conn
        finally:
            adapter.close_connection(conn)

    @pytest.fixture
    def tables(self, connection):
        yield from _plan_tables_fixture(connection.execute, _CLICKHOUSE_DDL)

    @pytest.mark.parametrize("shape", _SHAPES)
    def test_live_plan_semantics(self, adapter, connection, tables, shape):
        assert_live_plan_shape(adapter, connection, shape)


@pytest.mark.skipif(not os.environ.get("SPARK_MASTER"), reason="live Spark not available (SPARK_MASTER unset)")
class TestLiveSparkQueryPlanCapture:
    @pytest.fixture
    def adapter(self):
        from benchbox.platforms.spark import SparkAdapter

        return SparkAdapter(
            master=os.environ["SPARK_MASTER"],
            capture_plans=True,
        )

    @pytest.fixture
    def connection(self, adapter, tmp_path):
        from pyspark.sql import SparkSession

        conn = (
            SparkSession.builder.master(os.environ["SPARK_MASTER"])
            .config("spark.sql.warehouse.dir", str(tmp_path / "warehouse"))
            .config("spark.ui.enabled", "false")
            .getOrCreate()
        )
        try:
            yield conn
        finally:
            adapter.close_connection(conn)

    @pytest.fixture
    def tables(self, connection):
        yield from _plan_tables_fixture(lambda statement: connection.sql(statement).collect(), _SPARK_DDL)

    @pytest.mark.parametrize("shape", _SHAPES)
    def test_live_plan_semantics(self, adapter, connection, tables, shape):
        assert_live_plan_shape(adapter, connection, shape)
