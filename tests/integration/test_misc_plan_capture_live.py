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

_DATABEND_DDL = (
    "CREATE TABLE {orders} (o_id BIGINT, o_cust BIGINT, o_total DOUBLE)",
    "CREATE TABLE {customers} (c_id BIGINT, c_name STRING, c_region STRING)",
)

_QUESTDB_DDL = (
    "CREATE TABLE {orders} (o_id LONG, o_cust LONG, o_total DOUBLE)",
    "CREATE TABLE {customers} (c_id LONG, c_name STRING, c_region STRING)",
)

_DORIS_DDL = (
    "CREATE TABLE {orders} (o_id BIGINT, o_cust BIGINT, o_total DOUBLE) "
    "DISTRIBUTED BY HASH(o_id) BUCKETS 1 PROPERTIES ('replication_num' = '1')",
    "CREATE TABLE {customers} (c_id BIGINT, c_name VARCHAR(20), c_region VARCHAR(20)) "
    "DISTRIBUTED BY HASH(c_id) BUCKETS 1 PROPERTIES ('replication_num' = '1')",
)

_SINGLESTORE_DDL = (
    "CREATE TABLE {orders} (o_id BIGINT, o_cust BIGINT, o_total DOUBLE)",
    "CREATE TABLE {customers} (c_id BIGINT, c_name VARCHAR(20), c_region VARCHAR(20))",
)


@pytest.mark.skipif(not os.environ.get("DATABEND_HOST"), reason="live Databend not available (DATABEND_HOST unset)")
class TestLiveDatabendQueryPlanCapture:
    @pytest.fixture
    def adapter(self):
        from benchbox.platforms.databend.adapter import DatabendAdapter

        return DatabendAdapter(
            host=os.environ["DATABEND_HOST"],
            port=int(os.environ.get("DATABEND_PORT", "8000")),
            username=os.environ.get("DATABEND_USER", "root"),
            password=os.environ.get("DATABEND_PASSWORD", ""),
            database=os.environ.get("DATABEND_DATABASE", "default"),
            ssl=os.environ.get("DATABEND_SSL", "false").lower() == "true",
            capture_plans=True,
        )

    @pytest.fixture
    def connection(self, adapter):
        from databend_driver import BlockingDatabendClient

        conn = adapter._get_blocking_connection(BlockingDatabendClient(adapter._build_dsn()))
        try:
            yield conn
        finally:
            adapter.close_connection(conn)

    @pytest.fixture
    def tables(self, connection):
        with plan_tables(lambda statement: connection.exec(statement), _DATABEND_DDL):
            yield

    @pytest.mark.parametrize("shape", _SHAPES)
    def test_live_plan_semantics(self, adapter, connection, tables, shape):
        assert_live_plan_shape(adapter, connection, shape)


@pytest.mark.skipif(not os.environ.get("QUESTDB_HOST"), reason="live QuestDB not available (QUESTDB_HOST unset)")
class TestLiveQuestDBQueryPlanCapture:
    @pytest.fixture
    def adapter(self):
        from benchbox.platforms.questdb import QuestDBAdapter

        return QuestDBAdapter(
            host=os.environ.get("QUESTDB_HOST", "localhost"),
            pg_port=int(os.environ.get("QUESTDB_PG_PORT", "8812")),
            username=os.environ.get("QUESTDB_USER", "admin"),
            password=os.environ.get("QUESTDB_PASSWORD", "quest"),
            database=os.environ.get("QUESTDB_DATABASE", "qdb"),
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
        with plan_tables(cursor_executor(connection, commit=True), _QUESTDB_DDL):
            yield

    @pytest.mark.parametrize("shape", _SHAPES)
    def test_live_plan_semantics(self, adapter, connection, tables, shape):
        assert_live_plan_shape(adapter, connection, shape)


@pytest.mark.skipif(not os.environ.get("DORIS_HOST"), reason="live Doris not available (DORIS_HOST unset)")
class TestLiveDorisQueryPlanCapture:
    @pytest.fixture
    def adapter(self):
        from benchbox.platforms.doris import DorisAdapter

        return DorisAdapter(
            host=os.environ["DORIS_HOST"],
            port=int(os.environ.get("DORIS_PORT", "9030")),
            username=os.environ.get("DORIS_USER", "root"),
            password=os.environ.get("DORIS_PASSWORD", ""),
            database=os.environ.get("DORIS_DATABASE", "benchbox_test"),
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
        with plan_tables(cursor_executor(connection), _DORIS_DDL):
            yield

    @pytest.mark.parametrize("shape", _SHAPES)
    def test_live_plan_semantics(self, adapter, connection, tables, shape):
        assert_live_plan_shape(adapter, connection, shape)

    @pytest.mark.parametrize("shape", _SHAPES)
    def test_live_verbose_plan_semantics(self, adapter, connection, tables, shape):
        raw = adapter.get_query_plan(connection, shape_query(shape), explain_options={"verbose": True})
        assert raw is not None and "PLAN FRAGMENT" in raw
        plan = adapter.get_query_plan_parser().parse_explain_output(f"live-verbose-{shape}", raw)
        assert plan is not None
        assert plan.verify_fingerprint()
        assert_plan_semantics(plan, shape, ShapeTraits(), ORDERS, CUSTOMERS)


@pytest.mark.skipif(
    not os.environ.get("SINGLESTORE_HOST"), reason="live SingleStore not available (SINGLESTORE_HOST unset)"
)
class TestLiveSingleStoreQueryPlanCapture:
    @pytest.fixture
    def adapter(self):
        from benchbox.platforms.singlestore import SingleStoreAdapter

        return SingleStoreAdapter(
            host=os.environ["SINGLESTORE_HOST"],
            port=int(os.environ.get("SINGLESTORE_PORT", "3306")),
            username=os.environ.get("SINGLESTORE_USER", "root"),
            password=os.environ.get("SINGLESTORE_PASSWORD", ""),
            database=os.environ.get("SINGLESTORE_DATABASE", "benchbox_test"),
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
        with plan_tables(cursor_executor(connection), _SINGLESTORE_DDL):
            yield

    @pytest.mark.parametrize("shape", _SHAPES)
    def test_live_plan_semantics(self, adapter, connection, tables, shape):
        assert_live_plan_shape(adapter, connection, shape)
