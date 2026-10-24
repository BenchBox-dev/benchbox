from __future__ import annotations

from typing import Any

import pytest

try:
    import pandas as pd
    import polars as pl

    from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter
    from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

    DEPS_AVAILABLE = True
except ImportError:
    DEPS_AVAILABLE = False

try:
    import duckdb

    DUCKDB_AVAILABLE = True
except ImportError:
    DUCKDB_AVAILABLE = False

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.skipif(not DEPS_AVAILABLE, reason="Polars and/or Pandas not installed"),
]


def _to_pandas(result: Any) -> pd.DataFrame:
    from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame
    from benchbox.platforms.dataframe.unified_pandas_frame import UnifiedPandasFrame

    if isinstance(result, UnifiedLazyFrame):
        result = result.native
    if isinstance(result, UnifiedPandasFrame):
        result = result.native
    if isinstance(result, pl.LazyFrame):
        result = result.collect()
    if isinstance(result, pl.DataFrame):
        return result.to_pandas()
    if isinstance(result, pd.DataFrame):
        return result
    raise TypeError(f"Unexpected result type: {type(result)}")


def _sample_polars() -> pl.LazyFrame:
    return pl.DataFrame(
        {
            "sale_id": [1, 2, 3, 4],
            "channel": ["store", "web", "store", "catalog"],
            "has_return": ["N", "Y", "Y", "N"],
            "return_amount": [0.0, 12.5, 7.5, 0.0],
            "item_sk": [1, 2, 1, 3],
            "quantity": [2, 1, 3, 1],
            "ext_discount_amt": [1.0, 0.0, 0.5, 0.0],
            "coupon_amt": [0.0, 2.0, 0.0, 0.0],
            "net_paid": [19.0, 18.0, 29.5, 15.0],
            "net_paid_inc_tax": [20.9, 19.8, 32.45, 16.5],
            "net_profit": [5.0, 6.0, 8.0, 4.0],
        }
    ).lazy()


def _sample_pandas() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "sale_id": [1, 2, 3, 4],
            "channel": ["store", "web", "store", "catalog"],
            "has_return": ["N", "Y", "Y", "N"],
            "return_amount": [0.0, 12.5, 7.5, 0.0],
            "item_sk": [1, 2, 1, 3],
            "quantity": [2, 1, 3, 1],
            "ext_discount_amt": [1.0, 0.0, 0.5, 0.0],
            "coupon_amt": [0.0, 2.0, 0.0, 0.0],
            "net_paid": [19.0, 18.0, 29.5, 15.0],
            "net_paid_inc_tax": [20.9, 19.8, 32.45, 16.5],
            "net_profit": [5.0, 6.0, 8.0, 4.0],
        }
    )


def test_registry_exposes_seventeen_queries() -> None:
    from benchbox.core.tpcds_obt.dataframe_queries import REGISTRY

    query_ids = REGISTRY.get_query_ids()
    assert len(query_ids) == 17
    assert all(query_id.startswith("obt_") for query_id in query_ids)
    assert REGISTRY.get("obt_row_count") is not None


def test_malformed_specs_fail_at_import() -> None:
    from benchbox.core.tpcds_obt.dataframe_queries import _ObtQuerySpec

    base = {
        "query_name": "bad",
        "description": "bad",
        "categories": (),
        "sql_equivalent": "SELECT 1",
    }
    with pytest.raises(ValueError, match="Unknown aggregation func"):
        _ObtQuerySpec(aggregations=(("a", "b", "nope"),), **base)
    with pytest.raises(ValueError, match="Unknown filter op"):
        _ObtQuerySpec(filters=(("a", ">>", 1),), **base)
    with pytest.raises(ValueError, match="must match sort_keys"):
        _ObtQuerySpec(sort_keys=("a",), descending=(True, False), **base)
    with pytest.raises(ValueError, match="limit must be"):
        _ObtQuerySpec(limit=-1, **base)


def test_top_items_carries_deterministic_tiebreaker() -> None:
    from benchbox.core.tpcds_obt.dataframe_queries import _QUERY_SPECS

    q5 = next(spec for spec in _QUERY_SPECS if spec.query_name == "obt_top_items_by_quantity")
    assert q5.sort_keys == ("total_quantity", "item_sk")
    assert q5.descending == (True, False)
    assert "item_sk ASC" in q5.sql_equivalent


def test_query_outputs_match_between_families() -> None:
    from benchbox.core.tpcds_obt.dataframe_queries import get_dataframe_queries

    polars_ctx = PolarsDataFrameAdapter().create_context()
    pandas_ctx = PandasDataFrameAdapter().create_context()
    polars_ctx.register_table("tpcds_sales_returns_obt", _sample_polars())
    pandas_ctx.register_table("tpcds_sales_returns_obt", _sample_pandas())

    for query in get_dataframe_queries():
        expr_result = _to_pandas(query.expression_impl(polars_ctx))
        pandas_result = _to_pandas(query.pandas_impl(pandas_ctx))
        assert sorted(expr_result.columns.tolist()) == sorted(pandas_result.columns.tolist())
        assert len(expr_result) == len(pandas_result)


@pytest.mark.skipif(not DUCKDB_AVAILABLE, reason="duckdb not installed")
def test_query_outputs_match_sql_reference() -> None:
    from benchbox.core.tpcds_obt.dataframe_queries import get_dataframe_queries

    sample = _sample_pandas()
    conn = duckdb.connect(database=":memory:")
    conn.register("tpcds_sales_returns_obt", sample)

    pandas_ctx = PandasDataFrameAdapter().create_context()
    pandas_ctx.register_table("tpcds_sales_returns_obt", sample)

    for query in get_dataframe_queries():
        assert query.sql_equivalent is not None
        pandas_result = _to_pandas(query.pandas_impl(pandas_ctx)).sort_index(axis=1).reset_index(drop=True)
        sql_result = conn.execute(query.sql_equivalent).fetchdf().sort_index(axis=1).reset_index(drop=True)
        assert list(pandas_result.columns) == list(sql_result.columns)
        assert len(pandas_result) == len(sql_result)

        left = pandas_result.sort_values(by=list(pandas_result.columns)).reset_index(drop=True)
        right = sql_result.sort_values(by=list(sql_result.columns)).reset_index(drop=True)
        pd.testing.assert_frame_equal(left, right, check_dtype=False, check_exact=False, rtol=1e-9, atol=1e-9)


@pytest.mark.skipif(not DUCKDB_AVAILABLE, reason="duckdb not installed")
def test_empty_filter_reports_zero_not_null() -> None:

    from benchbox.core.tpcds_obt.dataframe_queries import REGISTRY

    sample = _sample_pandas()
    empty = sample[sample["net_paid"] < 0]
    assert empty.empty
    conn = duckdb.connect(database=":memory:")
    conn.register("tpcds_sales_returns_obt", empty)
    pandas_ctx = PandasDataFrameAdapter().create_context()
    pandas_ctx.register_table("tpcds_sales_returns_obt", empty)

    for query_id in ("obt_high_value_sales", "obt_coupon_sales", "obt_store_summary"):
        query = REGISTRY.get(query_id)
        assert query is not None
        pandas_result = _to_pandas(query.pandas_impl(pandas_ctx)).reset_index(drop=True)
        sql_result = conn.execute(query.sql_equivalent).fetchdf().reset_index(drop=True)
        assert len(pandas_result) == len(sql_result) == 1
        pd.testing.assert_frame_equal(pandas_result, sql_result, check_dtype=False, check_exact=False)
