from __future__ import annotations

from datetime import date

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

FAMILIES = ["expression", "pandas"]
WAREHOUSE_NAMES = ["Alpha", None]


def _context(family, tables):
    if family == "expression":
        pl = pytest.importorskip("polars")
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        ctx = PolarsDataFrameAdapter().create_context()
        for name, data in tables.items():
            ctx.register_table(name, pl.DataFrame(data).lazy())
    else:
        import pandas as pd

        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        ctx = PandasDataFrameAdapter().create_context()
        for name, data in tables.items():
            ctx.register_table(name, pd.DataFrame(data))
    return ctx


def _run(family, query_id, tables, monkeypatch, params):
    from benchbox.core.equivalence.dataframe_surface import materialize_rows
    from benchbox.core.tpcds.dataframe_queries import queries

    monkeypatch.setattr(queries, "get_parameters", lambda _query_id: params)
    impl = getattr(queries, f"q{query_id}_{family}_impl")
    return materialize_rows(impl(_context(family, tables)))


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize(
    "query_id, sales, prefix, dim_table, dim_key, dim_name_col, dim_name",
    [
        (62, "web_sales", "ws", "web_site", "web_site_sk", "web_name", "site_0"),
        (99, "catalog_sales", "cs", "call_center", "cc_call_center_sk", "cc_name", "center_0"),
    ],
)
def test_delivery_queries_keep_and_order_a_null_warehouse_last(
    family, query_id, sales, prefix, dim_table, dim_key, dim_name_col, dim_name, monkeypatch
):
    site_col = "ws_web_site_sk" if prefix == "ws" else "cs_call_center_sk"
    tables = {
        sales: {
            f"{prefix}_ship_date_sk": [10, 10],
            f"{prefix}_sold_date_sk": [5, 5],
            f"{prefix}_warehouse_sk": [1, 2],
            f"{prefix}_ship_mode_sk": [1, 1],
            site_col: [1, 1],
        },
        "date_dim": {"d_date_sk": [10], "d_month_seq": [1200]},
        "warehouse": {"w_warehouse_sk": [1, 2], "w_warehouse_name": WAREHOUSE_NAMES},
        "ship_mode": {"sm_ship_mode_sk": [1], "sm_type": ["EXPRESS"]},
        dim_table: {dim_key: [1], dim_name_col: [dim_name]},
    }

    rows = _run(family, query_id, tables, monkeypatch, {"dms": 1200})

    assert rows == [
        ("Alpha", "EXPRESS", dim_name, 1, 0, 0, 0, 0),
        (None, "EXPRESS", dim_name, 1, 0, 0, 0, 0),
    ]


@pytest.mark.parametrize("family", FAMILIES)
def test_q21_keeps_and_orders_a_null_warehouse_last(family, monkeypatch):
    tables = {
        "inventory": {
            "inv_item_sk": [1, 1, 1, 1],
            "inv_warehouse_sk": [1, 1, 2, 2],
            "inv_date_sk": [1, 2, 1, 2],
            "inv_quantity_on_hand": [10, 10, 10, 10],
        },
        "item": {"i_item_sk": [1], "i_item_id": ["ITEM1"], "i_current_price": [1.0]},
        "warehouse": {"w_warehouse_sk": [1, 2], "w_warehouse_name": WAREHOUSE_NAMES},
        "date_dim": {"d_date_sk": [1, 2], "d_date": [date(1998, 3, 20), date(1998, 4, 20)]},
    }

    rows = _run(family, 21, tables, monkeypatch, {})

    assert rows == [("Alpha", "ITEM1", 10, 10), (None, "ITEM1", 10, 10)]


@pytest.mark.parametrize("family", FAMILIES)
def test_q66_keeps_and_orders_a_null_warehouse_last(family, monkeypatch):
    def warehouse_key_columns(prefix):
        return {
            f"{prefix}_warehouse_sk": [1, 2],
            f"{prefix}_sold_date_sk": [1, 1],
            f"{prefix}_sold_time_sk": [1, 1],
            f"{prefix}_ship_mode_sk": [1, 1],
            f"{prefix}_quantity": [2, 2],
        }

    tables = {
        "web_sales": {**warehouse_key_columns("ws"), "ws_sales_price": [10.0, 10.0], "ws_net_paid_inc_tax": [1.0, 1.0]},
        "catalog_sales": {
            **warehouse_key_columns("cs"),
            "cs_sales_price": [5.0, 5.0],
            "cs_net_paid_inc_ship_tax": [1.0, 1.0],
        },
        "warehouse": {
            "w_warehouse_sk": [1, 2],
            "w_warehouse_name": WAREHOUSE_NAMES,
            "w_warehouse_sq_ft": [100, None],
            "w_city": ["Fairview", "Fairview"],
            "w_county": ["Williamson County", "Williamson County"],
            "w_state": ["TN", "TN"],
            "w_country": ["United States", "United States"],
        },
        "date_dim": {"d_date_sk": [1], "d_year": [2002], "d_moy": [3]},
        "time_dim": {"t_time_sk": [1], "t_time": [49530]},
        "ship_mode": {"sm_ship_mode_sk": [1], "sm_carrier": ["DIAMOND"]},
    }
    params = {"year": 2002, "ship_carriers": ["DIAMOND"], "time_start": 49530}

    rows = _run(family, 66, tables, monkeypatch, params)

    march_sales, march_per_sq_foot = 8 + 2, 8 + 12 + 2
    assert [row[:2] for row in rows] == [("Alpha", 100), (None, None)]
    assert rows[0][march_sales] == 30.0
    assert rows[0][march_per_sq_foot] == pytest.approx(0.3)
    assert rows[1][march_sales] == 30.0
    assert rows[1][march_per_sq_foot] is None


def test_none_for_null_converts_only_columns_that_hold_a_null():
    import pandas as pd

    from benchbox.core.tpcds.dataframe_queries.queries import _none_for_null

    frame = pd.DataFrame({"clean": [1.5, 2.5], "holey": [1.5, float("nan")], "name": ["a", None]})

    result = _none_for_null(frame, ["clean", "holey", "name"])

    assert result["clean"].dtype == "float64"
    assert list(result["holey"]) == [1.5, None]
    assert list(result["name"]) == ["a", None]
    assert frame["holey"].dtype == "float64"


