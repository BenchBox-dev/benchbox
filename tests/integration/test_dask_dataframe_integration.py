# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.fast,
]


try:
    import dask.dataframe as dd

    dask_available = True
except ImportError:
    dask_available = False

from benchbox.platforms import DASK_AVAILABLE


@pytest.mark.integration
@pytest.mark.skipif(not dask_available, reason="Dask not installed")
class TestDaskDataFrameIntegration:
    @pytest.fixture
    def temp_working_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def dask_adapter(self, temp_working_dir):
        from benchbox.platforms.dataframe.dask_df import DaskDataFrameAdapter

        adapter = DaskDataFrameAdapter(
            working_dir=str(temp_working_dir),
            n_workers=2,
            threads_per_worker=1,
            execution_engine="threaded",
        )
        yield adapter
        adapter.close()

    @pytest.fixture
    def sample_parquet_data(self, temp_working_dir):
        from datetime import date

        import pandas as pd

        data_dir = temp_working_dir / "data"
        data_dir.mkdir()

        lineitem_df = pd.DataFrame(
            {
                "l_orderkey": [1, 1, 2, 2, 3],
                "l_partkey": [100, 101, 100, 102, 101],
                "l_suppkey": [10, 11, 10, 12, 11],
                "l_linenumber": [1, 2, 1, 2, 1],
                "l_quantity": [5.0, 10.0, 3.0, 8.0, 6.0],
                "l_extendedprice": [100.0, 200.0, 60.0, 160.0, 120.0],
                "l_discount": [0.05, 0.10, 0.0, 0.05, 0.08],
                "l_tax": [0.02, 0.02, 0.02, 0.02, 0.02],
                "l_returnflag": ["N", "R", "N", "N", "R"],
                "l_linestatus": ["O", "F", "O", "O", "F"],
                "l_shipdate": [
                    date(1998, 1, 1),
                    date(1998, 2, 1),
                    date(1998, 1, 15),
                    date(1998, 2, 15),
                    date(1998, 3, 1),
                ],
                "l_commitdate": [
                    date(1998, 1, 10),
                    date(1998, 2, 10),
                    date(1998, 1, 20),
                    date(1998, 2, 20),
                    date(1998, 3, 10),
                ],
                "l_receiptdate": [
                    date(1998, 1, 15),
                    date(1998, 2, 15),
                    date(1998, 1, 25),
                    date(1998, 2, 25),
                    date(1998, 3, 15),
                ],
                "l_shipinstruct": ["DELIVER IN PERSON"] * 5,
                "l_shipmode": ["AIR", "SHIP", "AIR", "RAIL", "TRUCK"],
                "l_comment": ["comment"] * 5,
            }
        )
        lineitem_path = data_dir / "lineitem.parquet"
        lineitem_df.to_parquet(lineitem_path)

        orders_df = pd.DataFrame(
            {
                "o_orderkey": [1, 2, 3],
                "o_custkey": [100, 101, 100],
                "o_orderstatus": ["O", "O", "F"],
                "o_totalprice": [300.0, 220.0, 120.0],
                "o_orderdate": [date(1997, 12, 1), date(1997, 12, 15), date(1998, 1, 1)],
                "o_orderpriority": ["1-URGENT", "2-HIGH", "3-MEDIUM"],
                "o_clerk": ["Clerk#1", "Clerk#2", "Clerk#1"],
                "o_shippriority": [0, 0, 0],
                "o_comment": ["order comment"] * 3,
            }
        )
        orders_path = data_dir / "orders.parquet"
        orders_df.to_parquet(orders_path)

        customer_df = pd.DataFrame(
            {
                "c_custkey": [100, 101],
                "c_name": ["Customer#100", "Customer#101"],
                "c_address": ["123 Main St", "456 Oak Ave"],
                "c_nationkey": [1, 2],
                "c_phone": ["13-555-1234", "31-555-5678"],
                "c_acctbal": [1000.0, 2000.0],
                "c_mktsegment": ["BUILDING", "AUTOMOBILE"],
                "c_comment": ["customer comment"] * 2,
            }
        )
        customer_path = data_dir / "customer.parquet"
        customer_df.to_parquet(customer_path)

        return {
            "lineitem": [lineitem_path],
            "orders": [orders_path],
            "customer": [customer_path],
        }

    def test_adapter_initialization(self, dask_adapter):

        assert dask_adapter.platform_name == "Dask"
        assert dask_adapter.family == "pandas"
        assert dask_adapter.n_workers == 2
        assert dask_adapter.threads_per_worker == 1

    def test_dask_available_flag(self):

        assert DASK_AVAILABLE is True

    def test_platform_info(self, dask_adapter):

        info = dask_adapter.get_platform_info()

        assert info["platform"] == "Dask"
        assert info["family"] == "pandas"
        assert info["n_workers"] == 2
        assert "version" in info

    def test_read_parquet(self, dask_adapter, sample_parquet_data):

        lineitem_path = sample_parquet_data["lineitem"][0]
        df = dask_adapter.read_parquet(lineitem_path)

        assert hasattr(df, "compute")
        assert hasattr(df, "npartitions")

        result = df.compute()
        assert len(result) == 5
        assert "l_quantity" in result.columns

    def test_context_creation(self, dask_adapter):

        ctx = dask_adapter.create_context()

        assert ctx is not None
        assert ctx.platform == "Dask"
        assert ctx.family == "pandas"

    def test_table_loading(self, dask_adapter, sample_parquet_data):

        ctx = dask_adapter.create_context()

        row_count = dask_adapter.load_table(ctx, "lineitem", sample_parquet_data["lineitem"])

        assert ctx.table_exists("lineitem")
        assert row_count == 5

    def test_groupby_with_nunique(self, dask_adapter, sample_parquet_data):
        lineitem_path = sample_parquet_data["lineitem"][0]
        df = dask_adapter.read_parquet(lineitem_path)

        result = dask_adapter.groupby_agg(
            df,
            by="l_returnflag",
            agg_spec={"unique_parts": ("l_partkey", "nunique"), "total_qty": ("l_quantity", "sum")},
            as_index=False,
        )

        result_pd = result.compute()

        assert "unique_parts" in result_pd.columns
        assert "total_qty" in result_pd.columns
        assert len(result_pd) == 2

    def test_groupby_without_as_index(self, dask_adapter, sample_parquet_data):
        lineitem_path = sample_parquet_data["lineitem"][0]
        df = dask_adapter.read_parquet(lineitem_path)

        result = dask_adapter.groupby_agg(
            df,
            by=["l_returnflag", "l_linestatus"],
            agg_spec={"count": ("l_orderkey", "count")},
            as_index=False,
        )

        result_pd = result.compute()

        assert "l_returnflag" in result_pd.columns
        assert "l_linestatus" in result_pd.columns
        assert "count" in result_pd.columns

    def test_unified_pandas_frame_wrapper(self, dask_adapter, sample_parquet_data):

        from benchbox.platforms.dataframe.unified_pandas_frame import UnifiedPandasFrame

        lineitem_path = sample_parquet_data["lineitem"][0]
        df = dask_adapter.read_parquet(lineitem_path)

        wrapper = UnifiedPandasFrame(df, dask_adapter)

        filtered = wrapper[wrapper["l_quantity"] > 5]
        assert hasattr(filtered, "native")

        copied = wrapper.copy()
        assert hasattr(copied, "native")

        result = wrapper.compute()
        assert len(result) == 5

    def test_lazy_evaluation(self, dask_adapter, sample_parquet_data):

        lineitem_path = sample_parquet_data["lineitem"][0]
        df = dask_adapter.read_parquet(lineitem_path)

        result = df[df["l_quantity"] > 3].groupby("l_returnflag").agg({"l_quantity": "sum"})

        assert hasattr(result, "compute")

        computed = result.compute()
        assert len(computed) == 2


@pytest.mark.integration
@pytest.mark.skipif(not dask_available, reason="Dask not installed")
class TestDaskTPCHQueryExecution:
    @pytest.fixture
    def temp_working_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def dask_adapter(self, temp_working_dir):
        from benchbox.platforms.dataframe.dask_df import DaskDataFrameAdapter

        adapter = DaskDataFrameAdapter(
            working_dir=str(temp_working_dir),
            execution_engine="threaded",
        )
        yield adapter
        adapter.close()

    @pytest.fixture
    def tpch_data(self, temp_working_dir):
        from datetime import date

        import pandas as pd

        data_dir = temp_working_dir / "data"
        data_dir.mkdir()

        lineitem = pd.DataFrame(
            {
                "l_orderkey": range(1, 11),
                "l_partkey": [1, 1, 2, 2, 3, 3, 4, 4, 5, 5],
                "l_suppkey": [10, 11, 10, 11, 12, 13, 10, 11, 12, 13],
                "l_linenumber": [1, 2, 1, 2, 1, 2, 1, 2, 1, 2],
                "l_quantity": [10.0, 20.0, 15.0, 25.0, 30.0, 35.0, 12.0, 18.0, 22.0, 28.0],
                "l_extendedprice": [100.0, 200.0, 150.0, 250.0, 300.0, 350.0, 120.0, 180.0, 220.0, 280.0],
                "l_discount": [0.05, 0.06, 0.04, 0.07, 0.05, 0.06, 0.04, 0.05, 0.06, 0.07],
                "l_tax": [0.02] * 10,
                "l_returnflag": ["A", "N", "R", "A", "N", "R", "A", "N", "R", "A"],
                "l_linestatus": ["F", "O", "F", "O", "F", "O", "F", "O", "F", "O"],
                "l_shipdate": [date(1998, 8, 1)] * 10,
                "l_commitdate": [date(1998, 8, 10)] * 10,
                "l_receiptdate": [date(1998, 8, 15)] * 10,
                "l_shipinstruct": ["NONE"] * 10,
                "l_shipmode": ["AIR"] * 10,
                "l_comment": [""] * 10,
            }
        )
        (data_dir / "lineitem.parquet").write_bytes(lineitem.to_parquet())

        return data_dir

    def test_q1_pricing_summary_pandas_impl(self, dask_adapter, tpch_data):
        from benchbox.core.tpch.dataframe_queries import q1_pandas_impl

        ctx = dask_adapter.create_context()
        dask_adapter.load_table(ctx, "lineitem", [tpch_data / "lineitem.parquet"])

        result = q1_pandas_impl(ctx)

        if hasattr(result, "compute"):
            result = result.compute()

        assert "l_returnflag" in result.columns
        assert "l_linestatus" in result.columns
        assert "sum_qty" in result.columns
        assert len(result) > 0

    def test_q6_forecasting_revenue_pandas_impl(self, dask_adapter, tpch_data):
        from benchbox.core.tpch.dataframe_queries import q6_pandas_impl

        ctx = dask_adapter.create_context()
        dask_adapter.load_table(ctx, "lineitem", [tpch_data / "lineitem.parquet"])

        result = q6_pandas_impl(ctx)

        if hasattr(result, "compute"):
            result = result.compute()

        assert "revenue" in result.columns
        assert len(result) == 1
