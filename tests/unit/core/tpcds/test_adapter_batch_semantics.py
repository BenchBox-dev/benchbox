from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

FAMILIES = ["expression", "pandas"]


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


def _rows(result):
    from benchbox.core.equivalence.dataframe_surface import materialize_rows

    return materialize_rows(result)


def _impl(family, number):
    from benchbox.core.tpcds.dataframe_queries import queries

    return getattr(queries, f"q{number}_{family}_impl")


def _run(family, number, parameters, tables):
    from benchbox.core.tpcds.dataframe_queries.parameters import parameter_overrides

    with parameter_overrides({number: parameters}):
        return _rows(_impl(family, number)(_context(family, tables)))


@pytest.mark.parametrize("family", FAMILIES)
def test_q1_sums_the_return_column_the_template_drew_and_keeps_a_null_total_out(family):
    tables = {
        "store_returns": {
            "sr_returned_date_sk": [1, 1, 1, 1],
            "sr_customer_sk": [1, 2, 3, 3],
            "sr_store_sk": [1, 1, 1, 1],
            "sr_fee": [1.0, 1.0, 1.0, 1.0],
            "sr_return_amt": [100.0, 10.0, None, None],
        },
        "date_dim": {"d_date_sk": [1], "d_year": [2000]},
        "store": {"s_store_sk": [1], "s_state": ["TN"]},
        "customer": {"c_customer_sk": [1, 2, 3], "c_customer_id": ["C1", "C2", "C3"]},
    }
    on_fees = _run(family, 1, {"year": 2000, "state": "TN", "agg_field": "sr_fee"}, tables)
    on_amounts = _run(family, 1, {"year": 2000, "state": "TN", "agg_field": "sr_return_amt"}, tables)

    assert on_fees == [("C3",)]
    assert on_amounts == [("C1",)]


@pytest.mark.parametrize("family", FAMILIES)
def test_q7_reports_an_average_over_only_nulls_as_none(family):
    tables = {
        "store_sales": {
            "ss_cdemo_sk": [1, 1],
            "ss_sold_date_sk": [1, 1],
            "ss_item_sk": [1, 1],
            "ss_promo_sk": [1, 1],
            "ss_quantity": [3, 5],
            "ss_list_price": [None, None],
            "ss_coupon_amt": [1.0, 3.0],
            "ss_sales_price": [2.0, 4.0],
        },
        "customer_demographics": {
            "cd_demo_sk": [1],
            "cd_gender": ["F"],
            "cd_marital_status": ["W"],
            "cd_education_status": ["Primary"],
        },
        "date_dim": {"d_date_sk": [1], "d_year": [1998]},
        "item": {"i_item_sk": [1], "i_item_id": ["I1"]},
        "promotion": {"p_promo_sk": [1], "p_channel_email": ["N"], "p_channel_event": ["Y"]},
    }
    parameters = {"year": 1998, "gender": "F", "marital_status": "W", "education": "Primary"}

    assert _run(family, 7, parameters, tables) == [("I1", 4.0, None, 2.0, 3.0)]


@pytest.mark.parametrize("family", FAMILIES)
def test_q8_filters_on_the_zip_codes_it_is_given(family):
    zips = ["10001"] * 11 + ["20002"] * 11
    tables = {
        "store_sales": {"ss_sold_date_sk": [1], "ss_store_sk": [1], "ss_net_profit": [5.0]},
        "date_dim": {"d_date_sk": [1], "d_year": [2000], "d_qoy": [1]},
        "store": {"s_store_sk": [1], "s_store_name": ["S1"], "s_zip": ["10099"]},
        "customer_address": {"ca_address_sk": list(range(22)), "ca_zip": zips},
        "customer": {
            "c_current_addr_sk": list(range(22)),
            "c_customer_sk": list(range(22)),
            "c_preferred_cust_flag": ["Y"] * 22,
        },
    }
    wanted = {"year": 2000, "qoy": 1, "zip_codes": ["10001", "99999"]}
    other = {"year": 2000, "qoy": 1, "zip_codes": ["30003"]}

    assert _run(family, 8, wanted, tables) == [("S1", 5.0)]
    assert _run(family, 8, other, tables) == []


@pytest.mark.parametrize("family", FAMILIES)
def test_q12_keeps_a_null_class_group_and_sorts_it_last(family):
    tables = {
        "web_sales": {
            "ws_item_sk": [1, 2, 3],
            "ws_sold_date_sk": [1, 1, 1],
            "ws_ext_sales_price": [10.0, 20.0, 30.0],
        },
        "item": {
            "i_item_sk": [1, 2, 3],
            "i_item_id": ["I1", "I2", "I3"],
            "i_item_desc": ["a", "b", "c"],
            "i_category": ["Books", "Books", "Books"],
            "i_class": ["arts", None, "zoo"],
            "i_current_price": [1.0, 2.0, 3.0],
        },
        "date_dim": {"d_date_sk": [1], "d_date": [__import__("datetime").date(2001, 1, 20)]},
    }
    parameters = {"item_categories": ["Books"], "sales_date": "2001-01-12"}

    rows = _run(family, 12, parameters, tables)

    assert [row[3] for row in rows] == ["arts", "zoo", None]
    assert [row[5] for row in rows] == [10.0, 30.0, 20.0]


