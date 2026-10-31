# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.


import os

import pytest

from tests.integration._live_plan_shapes import (
    CUSTOMERS,
    ORDERS,
    SHAPE_QUERIES,
    ShapeTraits,
    assert_live_plan_shape,
    assert_plan_semantics,
    cursor_executor,
    plan_tables,
    shape_query,
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


def _assert_clickhouse_pipeline(adapter, connection, shape):
    from benchbox.core.results.query_plan_models import LogicalOperatorType
    from benchbox.platforms.base.sql_execution import join_explain_rows

    rows = connection.execute(f"EXPLAIN PIPELINE {shape_query(shape)}")
    raw = join_explain_rows(list(rows))
    assert raw.strip()
    plan = adapter.get_query_plan_parser().parse_explain_output(f"live-pipeline-{shape}", raw)
    assert plan is not None
    assert plan.verify_fingerprint()
    nodes = []
    stack = [plan.logical_root]
    while stack:
        node = stack.pop()
        nodes.append(node)
        stack.extend(node.children)
    names = {n.physical_operator.operator_type for n in nodes}
    types = {n.operator_type for n in nodes}
    reads = [n for n in nodes if n.physical_operator.operator_type == "ReadFromMergeTree"]
    assert len(reads) == (2 if shape in ("join", "union") else 1)
    assert all(n.operator_type == LogicalOperatorType.SCAN for n in reads)
    assert "MergeTreeSelect" in names
    assert (LogicalOperatorType.JOIN in types) == (shape == "join")
    assert (LogicalOperatorType.AGGREGATE in types) == (shape == "aggregate")
    assert (LogicalOperatorType.UNION in types) == (shape == "union")


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

    @pytest.mark.parametrize("shape", _SHAPES)
    def test_live_pipeline_semantics(self, adapter, connection, tables, shape):
        _assert_clickhouse_pipeline(adapter, connection, shape)


@pytest.mark.skipif(not os.environ.get("CHDB_LIVE"), reason="live chdb not requested (CHDB_LIVE unset)")
class TestLiveChdbQueryPlanCapture:
    @pytest.fixture
    def adapter(self):
        pytest.importorskip("chdb", exc_type=ImportError)
        from benchbox.platforms.clickhouse.adapter import ClickHouseAdapter

        return ClickHouseAdapter(deployment_mode="local", capture_plans=True)

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

    @pytest.mark.parametrize("shape", _SHAPES)
    def test_live_pipeline_semantics(self, adapter, connection, tables, shape):
        _assert_clickhouse_pipeline(adapter, connection, shape)


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


@pytest.mark.skipif(
    not os.environ.get("LAKESAIL_ENDPOINT"), reason="live LakeSail not available (LAKESAIL_ENDPOINT unset)"
)
class TestLiveLakeSailDataFramePlanCapture:
    @pytest.fixture
    def adapter(self):
        from benchbox.platforms.dataframe.lakesail_df import LakeSailDataFrameAdapter

        adapter = LakeSailDataFrameAdapter(endpoint=os.environ["LAKESAIL_ENDPOINT"])
        try:
            yield adapter
        finally:
            adapter.close()

    @pytest.fixture
    def tables(self, adapter, tmp_path):
        import pandas as pd

        spark = adapter._get_or_create_session()
        (tmp_path / ORDERS).mkdir()
        (tmp_path / CUSTOMERS).mkdir()
        pd.DataFrame({"o_id": [1, 2, 3], "o_cust": [1, 1, 2], "o_total": [50.0, 150.0, 250.0]}).to_parquet(
            tmp_path / ORDERS / "part.parquet"
        )
        pd.DataFrame({"c_id": [1, 2], "c_name": ["a", "b"], "c_region": ["EU", "US"]}).to_parquet(
            tmp_path / CUSTOMERS / "part.parquet"
        )
        for name in (ORDERS, CUSTOMERS):
            spark.sql(f"DROP TABLE IF EXISTS {name}")
            spark.sql(f"CREATE TABLE {name} USING parquet LOCATION '{tmp_path / name}'")
        try:
            yield spark
        finally:
            for name in (ORDERS, CUSTOMERS):
                spark.sql(f"DROP TABLE IF EXISTS {name}")

    @pytest.mark.parametrize("shape", _SHAPES)
    def test_live_dataframe_plan_semantics(self, adapter, tables, shape):
        from benchbox.core.dataframe.profiling import capture_query_plan
        from benchbox.core.query_plans.parsers.registry import get_parser_for_platform

        captured = capture_query_plan(tables.sql(shape_query(shape)), "LakeSail")
        assert captured is not None and captured.plan_text.strip()
        plan = get_parser_for_platform("lakesail").parse_explain_output(f"live-df-{shape}", captured.plan_text)
        assert plan is not None
        assert plan.verify_fingerprint()
        assert_plan_semantics(plan, shape, ShapeTraits(), ORDERS, CUSTOMERS)
