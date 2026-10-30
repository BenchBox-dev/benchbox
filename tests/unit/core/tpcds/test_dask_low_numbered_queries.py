from __future__ import annotations

from datetime import date

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def _rows(result):
    import pandas as pd

    if hasattr(result, "compute"):
        result = result.compute()
    frame = result.native if hasattr(result, "native") else result
    return [
        tuple(None if value is None or (not isinstance(value, str) and pd.isna(value)) else value for value in row)
        for row in frame.astype(object).itertuples(index=False, name=None)
    ]


def _run(impl_name, tables, params, monkeypatch):
    dd = pytest.importorskip("dask.dataframe")
    import pandas as pd

    from benchbox.core.tpcds.dataframe_queries import queries
    from benchbox.platforms.dataframe.dask_df import DaskDataFrameAdapter
    from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

    monkeypatch.setattr(queries, "get_parameters", lambda _query_id: params)
    impl = getattr(queries, impl_name)

    pandas_ctx = PandasDataFrameAdapter().create_context()
    dask_ctx = DaskDataFrameAdapter(execution_engine="threaded").create_context()
    for name, data in tables.items():
        frame = pd.DataFrame(data)
        pandas_ctx.register_table(name, frame)
        dask_ctx.register_table(name, dd.from_pandas(frame, npartitions=3))
    return _rows(impl(pandas_ctx)), _rows(impl(dask_ctx))


def _assert_same(impl_name, tables, params, monkeypatch, expected=None):
    pandas_rows, dask_rows = _run(impl_name, tables, params, monkeypatch)
    assert dask_rows == pandas_rows
    if expected is not None:
        assert pandas_rows == expected
    return pandas_rows


def test_q9_buckets_match_on_dask_and_empty_bucket_is_null(monkeypatch):
    tables = {
        "store_sales": {
            "ss_quantity": [1, 2, 25, 30, 45, 90, 95],
            "ss_ext_discount_amt": [1.0] * 7,
            "ss_net_profit": [10.0, 20.0, 30.0, 50.0, 70.0, 90.0, 110.0],
        }
    }
    _assert_same("q9_pandas_impl", tables, {}, monkeypatch, [(15.0, 40.0, 70.0, None, 100.0)])


def test_q28_scalar_aggregates_are_materialized_on_dask(monkeypatch):
    tables = {
        "store_sales": {
            "ss_quantity": [1, 2, 7, 12],
            "ss_list_price": [11.0, 15.0, 91.0, 66.0],
            "ss_coupon_amt": [0.0] * 4,
            "ss_wholesale_cost": [0.0] * 4,
        }
    }
    rows = _assert_same("q28_pandas_impl", tables, {}, monkeypatch)
    assert rows[0][:3] == (13.0, 2, 2)
    assert rows[0][3:6] == (91.0, 1, 1)


def test_q48_sum_is_materialized_on_dask(monkeypatch):
    tables = {
        "store_sales": {
            "ss_store_sk": [1, 1],
            "ss_cdemo_sk": [1, 1],
            "ss_addr_sk": [1, 1],
            "ss_sold_date_sk": [1, 1],
            "ss_sales_price": [120.0, 120.0],
            "ss_net_profit": [100.0, 5000.0],
            "ss_quantity": [5, 7],
        },
        "store": {"s_store_sk": [1]},
        "customer_demographics": {
            "cd_demo_sk": [1],
            "cd_marital_status": ["M"],
            "cd_education_status": ["4 yr Degree"],
        },
        "customer_address": {"ca_address_sk": [1], "ca_country": ["United States"], "ca_state": ["KY"]},
        "date_dim": {"d_date_sk": [1], "d_year": [1998]},
    }
    _assert_same("q48_pandas_impl", tables, {}, monkeypatch, [(5,)])


def test_q16_scalar_aggregates_are_materialized_on_dask(monkeypatch):
    import pandas as pd
    import pyarrow as pa

    tables = {
        "catalog_sales": {
            "cs_warehouse_sk": [1, 2, 1],
            "cs_order_number": [10, 10, 20],
            "cs_ship_date_sk": [1, 1, 1],
            "cs_ship_addr_sk": [1, 1, 1],
            "cs_call_center_sk": [1, 1, 1],
            "cs_ext_ship_cost": [5.0, 7.0, 11.0],
            "cs_net_profit": [1.0, 2.0, 4.0],
        },
        "catalog_returns": {"cr_order_number": [20]},
        "date_dim": {"d_date_sk": [1], "d_date": pd.Series([date(1999, 2, 15)], dtype=pd.ArrowDtype(pa.date32()))},
        "customer_address": {"ca_address_sk": [1], "ca_state": ["IL"]},
        "call_center": {"cc_call_center_sk": [1], "cc_county": ["Williamson County"]},
    }
    params = {"year": 1999, "month": 2, "state": "IL", "counties": ["Williamson County"]}
    _assert_same("q16_pandas_impl", tables, params, monkeypatch, [(1, 12.0, 3.0)])


def test_q32_excess_discount_runs_on_dask_with_datetime_dates(monkeypatch):
    import pandas as pd

    tables = {
        "catalog_sales": {
            "cs_item_sk": [1, 1, 1],
            "cs_sold_date_sk": [1, 1, 1],
            "cs_ext_discount_amt": [10.0, 10.0, 100.0],
        },
        "item": {"i_item_sk": [1], "i_manufact_id": [269]},
        "date_dim": {"d_date_sk": [1], "d_date": [pd.Timestamp("1998-03-20")]},
    }
    params = {"sales_date": "1998-03-18", "manufact_id": 269}
    _assert_same("q32_pandas_impl", tables, params, monkeypatch, [(100.0,)])


def test_q6_named_aggregate_runs_on_dask(monkeypatch):
    tables = {
        "customer_address": {"ca_address_sk": [1], "ca_state": ["CA"]},
        "customer": {"c_customer_sk": [1], "c_current_addr_sk": [1]},
        "store_sales": {
            "ss_customer_sk": [1] * 11,
            "ss_sold_date_sk": [1] * 11,
            "ss_item_sk": [1] * 10 + [2],
        },
        "date_dim": {"d_date_sk": [1], "d_year": [2001], "d_moy": [1]},
        "item": {"i_item_sk": [1, 2], "i_category": ["Books", "Books"], "i_current_price": [100.0, 10.0]},
    }
    _assert_same("q6_pandas_impl", tables, {"year": 2001, "month": 1}, monkeypatch, [("CA", 10)])


def test_q15_keeps_a_null_group_last_on_dask(monkeypatch):
    tables = {
        "catalog_sales": {
            "cs_bill_customer_sk": [1, 2, 3],
            "cs_sold_date_sk": [1, 1, 1],
            "cs_sales_price": [10.0, 20.0, 30.0],
        },
        "customer": {"c_customer_sk": [1, 2, 3], "c_current_addr_sk": [1, 2, 3]},
        "customer_address": {
            "ca_address_sk": [1, 2, 3],
            "ca_zip": ["85669", None, "86197"],
            "ca_state": ["CA", "CA", "CA"],
        },
        "date_dim": {"d_date_sk": [1], "d_qoy": [2], "d_year": [2000]},
    }
    rows = _assert_same("q15_pandas_impl", tables, {}, monkeypatch)
    assert [row[0] for row in rows][-1] is None


def test_q36_rollup_ranks_match_on_dask(monkeypatch):
    tables = {
        "store_sales": {
            "ss_sold_date_sk": [1] * 4,
            "ss_item_sk": [1, 2, 3, 4],
            "ss_store_sk": [1] * 4,
            "ss_net_profit": [10.0, 20.0, 30.0, 5.0],
            "ss_ext_sales_price": [100.0, 100.0, 100.0, 100.0],
        },
        "date_dim": {"d_date_sk": [1], "d_year": [2000]},
        "item": {
            "i_item_sk": [1, 2, 3, 4],
            "i_category": ["A", "A", "B", "B"],
            "i_class": ["x", "y", "x", "y"],
        },
        "store": {"s_store_sk": [1], "s_state": ["TN"]},
    }
    rows = _assert_same("q36_pandas_impl", tables, {"year": 2000, "states": ["TN"]}, monkeypatch)
    assert len(rows) == 7


def test_q44_ranks_items_on_dask(monkeypatch):
    tables = {
        "store_sales": {
            "ss_store_sk": [4] * 5,
            "ss_addr_sk": [None] * 5,
            "ss_item_sk": [1, 2, 3, 4, 5],
            "ss_net_profit": [10.0, 20.0, 30.0, 40.0, 50.0],
        },
        "item": {"i_item_sk": [1, 2, 3, 4, 5], "i_product_name": ["a", "b", "c", "d", "e"]},
    }
    _assert_same(
        "q44_pandas_impl",
        tables,
        {"store_sk": 4, "null_col": "ss_addr_sk"},
        monkeypatch,
        [(1, "c", "e"), (2, "d", "d"), (3, "e", "c")],
    )


def test_q49_return_ratios_match_on_dask(monkeypatch):
    def channel(prefix, return_prefix, order_col, return_order_col, amt_col):
        sales = {
            order_col: [1, 2],
            f"{prefix}_item_sk": [1, 2],
            f"{prefix}_sold_date_sk": [1, 1],
            f"{prefix}_net_paid": [5.0, 5.0],
            f"{prefix}_net_profit": [3.0, 3.0],
            f"{prefix}_quantity": [10, 10],
        }
        returns = {
            return_order_col: [1, 2],
            f"{return_prefix}_item_sk": [1, 2],
            f"{return_prefix}_return_quantity": [2, 4],
            amt_col: [20000.0, 30000.0],
        }
        return sales, returns

    tables = {"date_dim": {"d_date_sk": [1], "d_year": [2001], "d_moy": [12]}}
    for sales_name, returns_name, args in (
        ("web_sales", "web_returns", ("ws", "wr", "ws_order_number", "wr_order_number", "wr_return_amt")),
        ("catalog_sales", "catalog_returns", ("cs", "cr", "cs_order_number", "cr_order_number", "cr_return_amount")),
        ("store_sales", "store_returns", ("ss", "sr", "ss_ticket_number", "sr_ticket_number", "sr_return_amt")),
    ):
        tables[sales_name], tables[returns_name] = channel(*args)

    rows = _assert_same("q49_pandas_impl", tables, {"year": 2001, "month": 12}, monkeypatch)
    assert len(rows) == 6
    assert rows[0] == ("catalog", 1, 0.2, 1.0, 1.0)


def test_q23_total_is_materialized_on_dask(monkeypatch):
    import pandas as pd

    sales = 12
    tables = {
        "store_sales": {
            "ss_sold_date_sk": [1] * sales,
            "ss_item_sk": [1] * sales,
            "ss_customer_sk": [1] * sales,
            "ss_quantity": [1] * sales,
            "ss_sales_price": [10.0] * sales,
        },
        "catalog_sales": {
            "cs_sold_date_sk": [1],
            "cs_item_sk": [1],
            "cs_bill_customer_sk": [1],
            "cs_quantity": [2],
            "cs_list_price": [5.0],
        },
        "web_sales": {
            "ws_sold_date_sk": [1],
            "ws_item_sk": [1],
            "ws_bill_customer_sk": [1],
            "ws_quantity": [3],
            "ws_list_price": [5.0],
        },
        "customer": {"c_customer_sk": [1]},
        "item": {"i_item_sk": [1], "i_item_desc": ["desc"]},
        "date_dim": {"d_date_sk": [1], "d_year": [1999], "d_moy": [1], "d_date": [pd.Timestamp("1999-01-05")]},
    }
    _assert_same("q23_pandas_impl", tables, {"year": 1999, "month": 1, "top_percent": 95}, monkeypatch, [(25.0,)])


def test_dask_groupby_agg_accepts_repeated_aggregate_specs():
    dd = pytest.importorskip("dask.dataframe")
    import pandas as pd

    from benchbox.platforms.dataframe.dask_df import DaskDataFrameAdapter

    adapter = DaskDataFrameAdapter(execution_engine="threaded")
    frame = pd.DataFrame({"k": ["a", "a", "b"], "v": [1, 5, 3], "w": [2, 2, 4]})
    result = adapter.groupby_agg(
        dd.from_pandas(frame, npartitions=2),
        ["k"],
        {"hi": ("v", "max"), "hi_again": ("v", "max"), "lo": ("w", "min"), "hi_once_more": ("v", "max")},
        as_index=False,
    ).compute()

    assert list(result.columns) == ["k", "hi", "hi_again", "lo", "hi_once_more"]
    assert sorted(result.itertuples(index=False, name=None)) == [("a", 5, 5, 2, 5), ("b", 3, 3, 4, 3)]
