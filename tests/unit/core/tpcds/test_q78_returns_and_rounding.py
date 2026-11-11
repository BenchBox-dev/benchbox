from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

FAMILIES = ["expression", "pandas"]


def _tables():
    return {
        "store_sales": {
            "ss_ticket_number": [1, 2],
            "ss_item_sk": [1, 2],
            "ss_customer_sk": [1, 1],
            "ss_sold_date_sk": [1, 1],
            "ss_quantity": [30, 5],
            "ss_wholesale_cost": [10.0, 1.0],
            "ss_sales_price": [11.0, 1.0],
        },
        "store_returns": {"sr_ticket_number": [2, 99], "sr_item_sk": [2, 99], "sr_returned_date_sk": [None, 1]},
        "web_sales": {
            "ws_order_number": [10, 11],
            "ws_item_sk": [1, 2],
            "ws_bill_customer_sk": [1, 1],
            "ws_sold_date_sk": [1, 1],
            "ws_quantity": [48, 5],
            "ws_wholesale_cost": [4.0, 1.0],
            "ws_sales_price": [6.0, 1.0],
        },
        "web_returns": {"wr_order_number": [99], "wr_item_sk": [99], "wr_returned_date_sk": [1]},
        "catalog_sales": {
            "cs_order_number": [20],
            "cs_item_sk": [3],
            "cs_bill_customer_sk": [9],
            "cs_sold_date_sk": [1],
            "cs_quantity": [1],
            "cs_wholesale_cost": [1.0],
            "cs_sales_price": [1.0],
        },
        "catalog_returns": {"cr_order_number": [99], "cr_item_sk": [99], "cr_returned_date_sk": [1]},
        "date_dim": {"d_date_sk": [1], "d_year": [2000]},
    }


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


@pytest.mark.parametrize("family", FAMILIES)
def test_q78_drops_a_returned_sale_even_when_the_return_date_is_null_and_rounds_halves_up(family, monkeypatch):
    from benchbox.core.equivalence.dataframe_surface import materialize_rows
    from benchbox.core.tpcds.dataframe_queries import queries

    monkeypatch.setattr(queries, "get_parameters", lambda _query_id: {"year": 2000})
    impl = queries.q78_expression_impl if family == "expression" else queries.q78_pandas_impl

    rows = materialize_rows(impl(_context(family, _tables())))

    assert rows == [(2000, 1, 1, 0.63, 30, 10.0, 11.0, 48, 4.0, 6.0)]


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize(
    "select_columns,expected_keys",
    [
        ("ss_sold_year", (2000,)),
        ("ss_item_sk", (1,)),
        ("ss_customer_sk", (1,)),
        ("ss_sold_year, ss_item_sk, ss_customer_sk", (2000, 1, 1)),
    ],
)
def test_q78_projects_the_drawn_columns_in_template_order(family, select_columns, expected_keys, monkeypatch):
    from benchbox.core.equivalence.dataframe_surface import materialize_rows
    from benchbox.core.tpcds.dataframe_queries import queries

    monkeypatch.setattr(queries, "get_parameters", lambda _: {"year": 2000, "select_columns": select_columns})
    impl = queries.q78_expression_impl if family == "expression" else queries.q78_pandas_impl

    assert materialize_rows(impl(_context(family, _tables()))) == [(*expected_keys, 0.63, 30, 10.0, 11.0, 48, 4.0, 6.0)]


@pytest.mark.parametrize("select_columns", ["", "ss_unknown", "ss_item_sk, ss_unknown"])
def test_q78_refuses_unknown_drawn_columns(select_columns):
    from benchbox.core.tpcds.dataframe_queries import queries

    with pytest.raises(ValueError, match="Q78 select_columns"):
        queries._q78_select_columns({"select_columns": select_columns})


