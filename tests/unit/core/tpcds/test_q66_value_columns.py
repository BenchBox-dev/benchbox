from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

FAMILIES = ["expression", "pandas"]
MAR_SALES, MAR_NET = 10, 34


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


def _tables():
    return {
        "warehouse": {
            "w_warehouse_sk": [1],
            "w_warehouse_name": ["Main"],
            "w_warehouse_sq_ft": [10],
            "w_city": ["Fairview"],
            "w_county": ["Williamson County"],
            "w_state": ["TN"],
            "w_country": ["United States"],
        },
        "date_dim": {"d_date_sk": [1], "d_year": [2002], "d_moy": [3]},
        "time_dim": {"t_time_sk": [1], "t_time": [50000]},
        "ship_mode": {"sm_ship_mode_sk": [1], "sm_carrier": ["DIAMOND"]},
        "web_sales": {
            "ws_warehouse_sk": [1],
            "ws_sold_date_sk": [1],
            "ws_sold_time_sk": [1],
            "ws_ship_mode_sk": [1],
            "ws_quantity": [2],
            "ws_sales_price": [10.0],
            "ws_ext_sales_price": [100.0],
            "ws_ext_list_price": [1000.0],
            "ws_net_paid": [6.0],
            "ws_net_paid_inc_tax": [7.0],
            "ws_net_paid_inc_ship": [60.0],
            "ws_net_paid_inc_ship_tax": [70.0],
            "ws_net_profit": [600.0],
        },
        "catalog_sales": {
            "cs_warehouse_sk": [1],
            "cs_sold_date_sk": [1],
            "cs_sold_time_sk": [1],
            "cs_ship_mode_sk": [1],
            "cs_quantity": [3],
            "cs_sales_price": [5.0],
            "cs_ext_sales_price": [50.0],
            "cs_ext_list_price": [500.0],
            "cs_net_paid": [8.0],
            "cs_net_paid_inc_tax": [9.0],
            "cs_net_paid_inc_ship": [80.0],
            "cs_net_paid_inc_ship_tax": [90.0],
            "cs_net_profit": [800.0],
        },
    }


def _run(family, monkeypatch, **params):
    from benchbox.core.equivalence.dataframe_surface import materialize_rows
    from benchbox.core.tpcds.dataframe_queries import queries

    monkeypatch.setattr(queries, "get_parameters", lambda _query_id: params)
    impl = queries.q66_expression_impl if family == "expression" else queries.q66_pandas_impl
    (row,) = materialize_rows(impl(_context(family, _tables())))
    return row


@pytest.mark.parametrize("family", FAMILIES)
def test_q66_defaults_keep_the_previous_columns(family, monkeypatch):
    row = _run(family, monkeypatch)

    assert row[MAR_SALES] == pytest.approx(10.0 * 2 + 5.0 * 3)
    assert row[MAR_NET] == pytest.approx(7.0 * 2 + 90.0 * 3)


@pytest.mark.parametrize("family", FAMILIES)
def test_q66_reads_the_sales_and_net_columns_the_template_drew(family, monkeypatch):
    row = _run(
        family,
        monkeypatch,
        web_sales_col="ws_ext_list_price",
        catalog_sales_col="cs_ext_sales_price",
        web_net_col="ws_net_paid",
        catalog_net_col="cs_net_paid_inc_tax",
    )

    assert row[MAR_SALES] == pytest.approx(1000.0 * 2 + 50.0 * 3)
    assert row[MAR_NET] == pytest.approx(6.0 * 2 + 9.0 * 3)


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("param", ["web_sales_col", "catalog_sales_col", "web_net_col", "catalog_net_col"])
def test_q66_rejects_a_column_the_template_cannot_draw(family, monkeypatch, param):
    with pytest.raises(ValueError, match="Q66"):
        _run(family, monkeypatch, **{param: "ws_quantity"})


