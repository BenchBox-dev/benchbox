# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

from typing import Any

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

try:
    import pandas as pd
    import polars as pl

    from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

    DEPS_AVAILABLE = True
except ImportError:
    pd = None
    pl = None
    PolarsDataFrameAdapter = None
    DEPS_AVAILABLE = False

pytestmark.append(pytest.mark.skipif(not DEPS_AVAILABLE, reason="pandas/polars not installed"))

ALL_QUERY_IDS = [
    "obt_row_count",
    "obt_channel_distribution",
    "obt_returns_summary",
    "obt_channel_revenue",
    "obt_top_items_by_quantity",
    "obt_avg_ticket_by_channel",
    "obt_discounted_sales",
    "obt_profit_by_channel",
    "obt_sales_by_channel_return_flag",
    "obt_high_value_sales",
    "obt_item_channel_quantity",
    "obt_ticket_extremes",
    "obt_distinct_items",
    "obt_bulk_sales",
    "obt_coupon_sales",
    "obt_store_summary",
    "obt_overall_totals",
]

TABLE_NAME = "tpcds_sales_returns_obt"


class _PandasContext:
    def __init__(self, tables: dict[str, Any]) -> None:
        self._tables = tables

    def get_table(self, name: str) -> Any:
        return self._tables[name.lower()]


def _make_obt() -> Any:

    return pd.DataFrame(
        {
            "channel": [
                "store",
                "web",
                "catalog",
                "store",
                "web",
                "store",
                "catalog",
                "web",
                "store",
                "catalog",
            ],
            "sale_id": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
            "item_sk": [1, 2, 1, 3, 2, 4, 3, 1, 5, 2],
            "quantity": [2, 1, 3, 1, 2, 1, 4, 1, 2, 1],
            "sales_price": [10.0, 20.0, 10.0, 15.0, 20.0, 30.0, 15.0, 10.0, 25.0, 20.0],
            "ext_discount_amt": [1.0, 0.0, 2.0, 0.0, 0.0, 3.0, 0.0, 0.0, 1.5, 0.0],
            "coupon_amt": [0.0, 2.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.5, 0.0, 0.0],
            "net_paid": [19.0, 18.0, 28.0, 15.0, 39.0, 27.0, 60.0, 9.5, 48.5, 20.0],
            "net_paid_inc_tax": [20.9, 19.8, 30.8, 16.5, 42.9, 29.7, 66.0, 10.45, 53.35, 22.0],
            "net_profit": [5.0, 6.0, 8.0, 4.0, 10.0, 9.0, 15.0, 2.5, 12.0, 6.0],
            "has_return": ["N", "Y", "N", "N", "Y", "N", "N", "N", "Y", "N"],
            "return_amount": [0.0, 18.0, 0.0, 0.0, 39.0, 0.0, 0.0, 0.0, 48.5, 0.0],
        }
    )


def _to_pandas_result(result: Any) -> Any:

    if hasattr(result, "collect"):
        result = result.collect()
    if isinstance(result, pl.DataFrame):
        return result.to_pandas()
    if isinstance(result, pd.DataFrame):
        return result.copy()
    raise TypeError(f"Unsupported result type: {type(result)!r}")


def _normalized(frame: Any) -> Any:

    frame = frame.copy()
    return frame.sort_values(by=list(frame.columns)).reset_index(drop=True)


class TestObtQueryExecution:
    @pytest.fixture(scope="class")
    def obt(self):
        return _make_obt()

    @pytest.fixture(scope="class")
    def expr_ctx(self, obt):
        adapter = PolarsDataFrameAdapter()
        ctx = adapter.create_context()
        ctx.register_table(TABLE_NAME, pl.from_pandas(obt).lazy())
        return ctx

    @pytest.fixture(scope="class")
    def pandas_ctx(self, obt):
        return _PandasContext({TABLE_NAME: obt})

    @pytest.mark.parametrize("query_id", ALL_QUERY_IDS)
    def test_backends_agree(self, query_id, expr_ctx, pandas_ctx):
        from benchbox.core.tpcds_obt.dataframe_queries import REGISTRY

        query = REGISTRY.get(query_id)
        assert query is not None

        expr_result = _normalized(_to_pandas_result(query.expression_impl(expr_ctx)))
        pandas_result = _normalized(_to_pandas_result(query.pandas_impl(pandas_ctx)))

        assert expr_result.columns.tolist() == pandas_result.columns.tolist()
        pd.testing.assert_frame_equal(expr_result, pandas_result, check_dtype=False)

    def test_row_count_counts_all_rows(self, expr_ctx, pandas_ctx):
        from benchbox.core.tpcds_obt.dataframe_queries import REGISTRY

        query = REGISTRY.get("obt_row_count")
        assert query is not None
        assert _to_pandas_result(query.expression_impl(expr_ctx))["row_count"].tolist() == [10]
        assert _to_pandas_result(query.pandas_impl(pandas_ctx))["row_count"].tolist() == [10]

    def test_channel_revenue(self, expr_ctx, pandas_ctx):
        from benchbox.core.tpcds_obt.dataframe_queries import REGISTRY

        query = REGISTRY.get("obt_channel_revenue")
        assert query is not None
        expected = {"catalog": 108.0, "store": 109.5, "web": 66.5}
        for impl, ctx in ((query.expression_impl, expr_ctx), (query.pandas_impl, pandas_ctx)):
            result = _to_pandas_result(impl(ctx))
            assert dict(zip(result["channel"], result["revenue"])) == expected

    def test_distinct_items(self, expr_ctx, pandas_ctx):
        from benchbox.core.tpcds_obt.dataframe_queries import REGISTRY

        query = REGISTRY.get("obt_distinct_items")
        assert query is not None
        assert _to_pandas_result(query.expression_impl(expr_ctx))["distinct_items"].tolist() == [5]
        assert _to_pandas_result(query.pandas_impl(pandas_ctx))["distinct_items"].tolist() == [5]

    def test_all_seventeen_registered(self):
        from benchbox.core.tpcds_obt.dataframe_queries import get_dataframe_queries

        actual = [query.query_id for query in get_dataframe_queries()]
        assert sorted(actual) == sorted(ALL_QUERY_IDS)
