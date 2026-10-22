# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

import pytest

from tests.utilities.optional_engines import pyspark_skip_reason, require_pyspark

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


if TYPE_CHECKING:
    from benchbox.platforms.dataframe.expression_family import ExpressionFamilyAdapter


try:
    import polars as pl

    from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

    POLARS_AVAILABLE = True
except ImportError:
    POLARS_AVAILABLE = False
    pl = None
    PolarsDataFrameAdapter = None

try:
    from pyspark.sql import SparkSession

    from benchbox.platforms.dataframe.pyspark_df import PySparkDataFrameAdapter

    PYSPARK_AVAILABLE = True
except ImportError:
    PYSPARK_AVAILABLE = False
    SparkSession = None
    PySparkDataFrameAdapter = None

try:
    import datafusion

    from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

    DATAFUSION_AVAILABLE = True
except ImportError:
    DATAFUSION_AVAILABLE = False
    datafusion = None
    DataFusionDataFrameAdapter = None


@pytest.fixture(scope="module")
def polars_adapter():
    if not POLARS_AVAILABLE:
        pytest.skip("Polars not installed")
    return PolarsDataFrameAdapter()


@pytest.fixture(scope="module")
def pyspark_adapter(pyspark_test_environment):
    require_pyspark()
    adapter = PySparkDataFrameAdapter(
        master="local[2]",
        app_name="BenchBox-SmokeTest",
        driver_memory="1g",
    )
    yield adapter
    adapter.close()


@pytest.fixture(scope="module")
def datafusion_adapter():
    if not DATAFUSION_AVAILABLE:
        pytest.skip("DataFusion not installed")
    return DataFusionDataFrameAdapter()


def create_test_data_polars():
    orders = pl.DataFrame(
        {
            "o_orderkey": [1, 2, 3, 4, 5],
            "o_custkey": [100, 100, 200, 200, 300],
            "o_totalprice": [1000.0, 2000.0, 1500.0, 2500.0, 3000.0],
            "o_orderdate": [
                date(1995, 1, 1),
                date(1995, 2, 1),
                date(1995, 3, 1),
                date(1995, 4, 1),
                date(1995, 5, 1),
            ],
            "o_orderpriority": ["HIGH", "LOW", "HIGH", "MEDIUM", "LOW"],
        }
    ).lazy()

    customers = pl.DataFrame(
        {
            "c_custkey": [100, 200, 300],
            "c_name": ["Customer A", "Customer B", "Customer C"],
            "c_mktsegment": ["BUILDING", "MACHINERY", "BUILDING"],
        }
    ).lazy()

    lineitem = pl.DataFrame(
        {
            "l_orderkey": [1, 1, 2, 3, 4, 5],
            "l_quantity": [10.0, 20.0, 15.0, 25.0, 30.0, 5.0],
            "l_extendedprice": [100.0, 200.0, 150.0, 250.0, 300.0, 50.0],
            "l_discount": [0.1, 0.05, 0.0, 0.1, 0.15, 0.0],
        }
    ).lazy()

    return {"orders": orders, "customer": customers, "lineitem": lineitem}


def create_test_data_pyspark(spark: SparkSession):
    orders = spark.createDataFrame(
        [
            (1, 100, 1000.0, date(1995, 1, 1), "HIGH"),
            (2, 100, 2000.0, date(1995, 2, 1), "LOW"),
            (3, 200, 1500.0, date(1995, 3, 1), "HIGH"),
            (4, 200, 2500.0, date(1995, 4, 1), "MEDIUM"),
            (5, 300, 3000.0, date(1995, 5, 1), "LOW"),
        ],
        ["o_orderkey", "o_custkey", "o_totalprice", "o_orderdate", "o_orderpriority"],
    )

    customers = spark.createDataFrame(
        [
            (100, "Customer A", "BUILDING"),
            (200, "Customer B", "MACHINERY"),
            (300, "Customer C", "BUILDING"),
        ],
        ["c_custkey", "c_name", "c_mktsegment"],
    )

    lineitem = spark.createDataFrame(
        [
            (1, 10.0, 100.0, 0.1),
            (1, 20.0, 200.0, 0.05),
            (2, 15.0, 150.0, 0.0),
            (3, 25.0, 250.0, 0.1),
            (4, 30.0, 300.0, 0.15),
            (5, 5.0, 50.0, 0.0),
        ],
        ["l_orderkey", "l_quantity", "l_extendedprice", "l_discount"],
    )

    return {"orders": orders, "customer": customers, "lineitem": lineitem}


def create_test_data_datafusion(ctx):
    import pyarrow as pa

    orders_table = pa.table(
        {
            "o_orderkey": [1, 2, 3, 4, 5],
            "o_custkey": [100, 100, 200, 200, 300],
            "o_totalprice": [1000.0, 2000.0, 1500.0, 2500.0, 3000.0],
            "o_orderdate": [
                date(1995, 1, 1),
                date(1995, 2, 1),
                date(1995, 3, 1),
                date(1995, 4, 1),
                date(1995, 5, 1),
            ],
            "o_orderpriority": ["HIGH", "LOW", "HIGH", "MEDIUM", "LOW"],
        }
    )

    customers_table = pa.table(
        {
            "c_custkey": [100, 200, 300],
            "c_name": ["Customer A", "Customer B", "Customer C"],
            "c_mktsegment": ["BUILDING", "MACHINERY", "BUILDING"],
        }
    )

    lineitem_table = pa.table(
        {
            "l_orderkey": [1, 1, 2, 3, 4, 5],
            "l_quantity": [10.0, 20.0, 15.0, 25.0, 30.0, 5.0],
            "l_extendedprice": [100.0, 200.0, 150.0, 250.0, 300.0, 50.0],
            "l_discount": [0.1, 0.05, 0.0, 0.1, 0.15, 0.0],
        }
    )

    ctx.register_record_batches("orders", [orders_table.to_batches()[0]])
    ctx.register_record_batches("customer", [customers_table.to_batches()[0]])
    ctx.register_record_batches("lineitem", [lineitem_table.to_batches()[0]])

    return {
        "orders": ctx.table("orders"),
        "customer": ctx.table("customer"),
        "lineitem": ctx.table("lineitem"),
    }


def get_row_count(result: Any, adapter: ExpressionFamilyAdapter) -> int:
    from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame

    if isinstance(result, UnifiedLazyFrame):
        result = result.native

    return adapter.get_row_count(adapter.collect(result) if hasattr(result, "collect") else result)


def collect_result(result: Any, adapter: ExpressionFamilyAdapter) -> Any:
    from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame

    if isinstance(result, UnifiedLazyFrame):
        result = result.native

    if hasattr(result, "collect"):
        return adapter.collect(result)
    return result


@pytest.mark.integration
@pytest.mark.skipif(not POLARS_AVAILABLE, reason="Polars not installed")
class TestPolarsSmoke:
    def test_join_left_on_right_on(self, polars_adapter):

        ctx = polars_adapter.create_context()
        data = create_test_data_polars()
        for name, df in data.items():
            ctx.register_table(name, df)

        orders = ctx.get_table("orders")
        customers = ctx.get_table("customer")

        result = orders.join(customers, left_on="o_custkey", right_on="c_custkey")
        collected = collect_result(result, polars_adapter)

        assert polars_adapter.get_row_count(collected) == 5
        assert "c_name" in collected.columns

    def test_group_by_with_aggregations(self, polars_adapter):

        ctx = polars_adapter.create_context()
        data = create_test_data_polars()
        ctx.register_table("orders", data["orders"])

        orders = ctx.get_table("orders")
        col = ctx.col

        result = orders.group_by("o_custkey").agg(
            col("o_totalprice").sum().alias("total_price"),
            col("o_totalprice").mean().alias("avg_price"),
            col("o_orderkey").count().alias("order_count"),
        )
        collected = collect_result(result, polars_adapter)

        assert polars_adapter.get_row_count(collected) == 3

    def test_filter_with_expressions(self, polars_adapter):

        ctx = polars_adapter.create_context()
        data = create_test_data_polars()
        ctx.register_table("orders", data["orders"])

        orders = ctx.get_table("orders")
        col = ctx.col
        lit = ctx.lit

        result = orders.filter((col("o_totalprice") > lit(1500.0)) & (col("o_orderpriority") == lit("HIGH")))
        collect_result(result, polars_adapter)

        result2 = orders.filter(col("o_totalprice") > lit(1000.0))
        collected2 = collect_result(result2, polars_adapter)
        assert polars_adapter.get_row_count(collected2) == 4

        result3 = orders.filter(col("o_totalprice") > lit(1500.0))
        collected3 = collect_result(result3, polars_adapter)
        assert polars_adapter.get_row_count(collected3) == 3

    def test_sort_multiple_columns(self, polars_adapter):

        ctx = polars_adapter.create_context()
        data = create_test_data_polars()
        ctx.register_table("orders", data["orders"])

        orders = ctx.get_table("orders")

        result1 = orders.sort("o_custkey", "o_totalprice")
        collected1 = collect_result(result1, polars_adapter)
        assert polars_adapter.get_row_count(collected1) == 5

        result2 = orders.sort(["o_custkey", "o_totalprice"], descending=[True, False])
        collected2 = collect_result(result2, polars_adapter)
        assert polars_adapter.get_row_count(collected2) == 5

    def test_unique(self, polars_adapter):

        ctx = polars_adapter.create_context()
        data = create_test_data_polars()
        ctx.register_table("orders", data["orders"])

        orders = ctx.get_table("orders")

        result = orders.select("o_custkey").unique()
        collected = collect_result(result, polars_adapter)
        assert polars_adapter.get_row_count(collected) == 3

    def test_dataframe_level_sum(self, polars_adapter):
        ctx = polars_adapter.create_context()
        data = create_test_data_polars()
        ctx.register_table("lineitem", data["lineitem"])

        lineitem = ctx.get_table("lineitem")
        col = ctx.col

        result = lineitem.select(col("l_quantity")).sum()
        collected = collect_result(result, polars_adapter)
        assert polars_adapter.get_row_count(collected) == 1


@pytest.mark.integration
@pytest.mark.skipif(pyspark_skip_reason() is not None, reason=pyspark_skip_reason() or "PySpark is usable")
class TestPySparkSmoke:
    def test_join_left_on_right_on(self, pyspark_adapter):

        ctx = pyspark_adapter.create_context()
        data = create_test_data_pyspark(pyspark_adapter.spark)
        for name, df in data.items():
            ctx.register_table(name, df)

        orders = ctx.get_table("orders")
        customers = ctx.get_table("customer")

        result = orders.join(customers, left_on="o_custkey", right_on="c_custkey")
        collected = collect_result(result, pyspark_adapter)

        assert pyspark_adapter.get_row_count(collected) == 5
        assert "c_name" in collected.columns

    def test_group_by_with_aggregations(self, pyspark_adapter):

        ctx = pyspark_adapter.create_context()
        data = create_test_data_pyspark(pyspark_adapter.spark)
        ctx.register_table("orders", data["orders"])

        orders = ctx.get_table("orders")
        col = ctx.col

        result = orders.group_by("o_custkey").agg(
            col("o_totalprice").sum().alias("total_price"),
            col("o_totalprice").mean().alias("avg_price"),
            col("o_orderkey").count().alias("order_count"),
        )
        collected = collect_result(result, pyspark_adapter)

        assert pyspark_adapter.get_row_count(collected) == 3

    def test_filter_with_expressions(self, pyspark_adapter):

        ctx = pyspark_adapter.create_context()
        data = create_test_data_pyspark(pyspark_adapter.spark)
        ctx.register_table("orders", data["orders"])

        orders = ctx.get_table("orders")
        col = ctx.col
        lit = ctx.lit

        result = orders.filter(col("o_totalprice") > lit(1000.0))
        collected = collect_result(result, pyspark_adapter)
        assert pyspark_adapter.get_row_count(collected) == 4

        result2 = orders.filter(col("o_totalprice") > lit(1500.0))
        collected2 = collect_result(result2, pyspark_adapter)
        assert pyspark_adapter.get_row_count(collected2) == 3

    def test_sort_multiple_columns(self, pyspark_adapter):

        ctx = pyspark_adapter.create_context()
        data = create_test_data_pyspark(pyspark_adapter.spark)
        ctx.register_table("orders", data["orders"])

        orders = ctx.get_table("orders")

        result1 = orders.sort("o_custkey", "o_totalprice")
        collected1 = collect_result(result1, pyspark_adapter)
        assert pyspark_adapter.get_row_count(collected1) == 5

        result2 = orders.sort(["o_custkey", "o_totalprice"], descending=[True, False])
        collected2 = collect_result(result2, pyspark_adapter)
        assert pyspark_adapter.get_row_count(collected2) == 5

    def test_unique(self, pyspark_adapter):

        ctx = pyspark_adapter.create_context()
        data = create_test_data_pyspark(pyspark_adapter.spark)
        ctx.register_table("orders", data["orders"])

        orders = ctx.get_table("orders")

        result = orders.select("o_custkey").unique()
        collected = collect_result(result, pyspark_adapter)
        assert pyspark_adapter.get_row_count(collected) == 3

    def test_dataframe_level_sum(self, pyspark_adapter):
        ctx = pyspark_adapter.create_context()
        data = create_test_data_pyspark(pyspark_adapter.spark)
        ctx.register_table("lineitem", data["lineitem"])

        lineitem = ctx.get_table("lineitem")
        col = ctx.col

        result = lineitem.select(col("l_quantity")).sum()
        collected = collect_result(result, pyspark_adapter)
        assert pyspark_adapter.get_row_count(collected) == 1

    def test_chained_operations(self, pyspark_adapter):
        ctx = pyspark_adapter.create_context()
        data = create_test_data_pyspark(pyspark_adapter.spark)
        for name, df in data.items():
            ctx.register_table(name, df)

        col = ctx.col
        lit = ctx.lit

        customers = ctx.get_table("customer")
        orders = ctx.get_table("orders")
        lineitem = ctx.get_table("lineitem")

        result = (
            customers.filter(col("c_mktsegment") == lit("BUILDING"))
            .join(orders, left_on="c_custkey", right_on="o_custkey")
            .filter(col("o_orderdate") < lit(date(1995, 3, 15)))
            .join(lineitem, left_on="o_orderkey", right_on="l_orderkey")
            .group_by("o_orderkey", "o_orderdate")
            .agg((col("l_extendedprice") * (lit(1) - col("l_discount"))).sum().alias("revenue"))
            .sort(["revenue", "o_orderdate"], descending=[True, False])
            .limit(10)
        )

        collected = collect_result(result, pyspark_adapter)
        assert pyspark_adapter.get_row_count(collected) >= 0


@pytest.mark.integration
@pytest.mark.skipif(not DATAFUSION_AVAILABLE, reason="DataFusion not installed")
class TestDataFusionSmoke:
    def test_adapter_creation(self, datafusion_adapter):

        assert datafusion_adapter is not None
        assert datafusion_adapter.platform_name == "DataFusion"

    def test_context_creation(self, datafusion_adapter):

        ctx = datafusion_adapter.create_context()
        assert ctx is not None
        assert ctx.platform == "DataFusion"


@pytest.mark.integration
class TestCrossPlatformConsistency:
    @pytest.mark.skipif(not (POLARS_AVAILABLE and PYSPARK_AVAILABLE), reason="Both Polars and PySpark required")
    def test_filter_produces_same_row_count(self, polars_adapter, pyspark_adapter):

        polars_ctx = polars_adapter.create_context()
        polars_data = create_test_data_polars()
        polars_ctx.register_table("orders", polars_data["orders"])

        polars_orders = polars_ctx.get_table("orders")
        polars_col = polars_ctx.col
        polars_lit = polars_ctx.lit
        polars_result = polars_orders.filter(polars_col("o_totalprice") > polars_lit(1500.0))
        polars_count = get_row_count(polars_result, polars_adapter)

        pyspark_ctx = pyspark_adapter.create_context()
        pyspark_data = create_test_data_pyspark(pyspark_adapter.spark)
        pyspark_ctx.register_table("orders", pyspark_data["orders"])

        pyspark_orders = pyspark_ctx.get_table("orders")
        pyspark_col = pyspark_ctx.col
        pyspark_lit = pyspark_ctx.lit
        pyspark_result = pyspark_orders.filter(pyspark_col("o_totalprice") > pyspark_lit(1500.0))
        pyspark_count = get_row_count(pyspark_result, pyspark_adapter)

        assert polars_count == pyspark_count, (
            f"Filter row counts differ: Polars={polars_count}, PySpark={pyspark_count}"
        )
