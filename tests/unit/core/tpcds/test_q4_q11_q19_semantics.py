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


def _sales(prefix, customer_key, totals):
    rows = [(customer, date_sk, price) for customer, by_year in totals.items() for date_sk, price in by_year.items()]
    return {
        f"{prefix}_{customer_key}": [row[0] for row in rows],
        f"{prefix}_sold_date_sk": [row[1] for row in rows],
        f"{prefix}_ext_list_price": [float(row[2]) for row in rows],
        f"{prefix}_ext_wholesale_cost": [0.0] * len(rows),
        f"{prefix}_ext_discount_amt": [0.0] * len(rows),
        f"{prefix}_ext_sales_price": [float(row[2]) for row in rows],
    }


def _customers():
    return {
        "c_customer_sk": [1, 2],
        "c_customer_id": ["A", "B"],
        "c_first_name": ["Ann", None],
        "c_last_name": ["Lee", "Roe"],
        "c_preferred_cust_flag": ["Y", "N"],
        "c_birth_country": ["FRANCE", "PERU"],
        "c_login": [None, None],
        "c_email_address": ["ann@example.com", "bob@example.com"],
    }


_DATE_DIM = {"d_date_sk": [1, 2], "d_year": [2001, 2002]}


def _q4_tables():
    flat = {1: {1: 10, 2: 10}, 2: {1: 10, 2: 10}}
    catalog = {1: {1: 10, 2: 30}, 2: {1: 10, 2: 10}}
    return {
        "customer": _customers(),
        "date_dim": _DATE_DIM,
        "store_sales": _sales("ss", "customer_sk", flat),
        "catalog_sales": _sales("cs", "bill_customer_sk", catalog),
        "web_sales": _sales("ws", "bill_customer_sk", flat),
    }


def _q11_tables():
    web = {1: {1: 10, 2: 30}, 2: {1: 10, 2: 10}}
    flat = {1: {1: 10, 2: 10}, 2: {1: 10, 2: 10}}
    return {
        "customer": _customers(),
        "date_dim": _DATE_DIM,
        "store_sales": _sales("ss", "customer_sk", flat),
        "web_sales": _sales("ws", "bill_customer_sk", web),
    }


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize(
    "select_one,expected",
    [
        ("t_s_secyear.customer_preferred_cust_flag", "Y"),
        ("t_s_secyear.customer_birth_country", "FRANCE"),
        ("t_s_secyear.customer_login", None),
        ("t_s_secyear.customer_email_address", "ann@example.com"),
    ],
)
@pytest.mark.parametrize("query_id", [4, 11])
def test_q4_q11_return_the_drawn_column_and_keep_null_group_keys(monkeypatch, family, select_one, expected, query_id):
    from benchbox.core.tpcds.dataframe_queries import queries

    monkeypatch.setattr(queries, "get_parameters", lambda _query_id: {"year": 2001, "select_one": select_one})
    tables = _q4_tables() if query_id == 4 else _q11_tables()
    impl = getattr(queries, f"q{query_id}_{family}_impl")

    assert _rows(impl(_context(family, tables))) == [("A", "Ann", "Lee", expected)]


@pytest.mark.parametrize("family", FAMILIES)
def test_q4_rejects_an_unknown_selectone(monkeypatch, family):
    from benchbox.core.tpcds.dataframe_queries import queries

    monkeypatch.setattr(queries, "get_parameters", lambda _query_id: {"year": 2001, "select_one": "t_s_secyear.nope"})
    with pytest.raises(ValueError, match="SELECTONE"):
        getattr(queries, f"q4_{family}_impl")(_context(family, _q4_tables()))


@pytest.mark.parametrize("family", FAMILIES)
def test_q19_drops_a_row_whose_customer_zip_is_null(monkeypatch, family):
    from benchbox.core.tpcds.dataframe_queries import queries

    monkeypatch.setattr(queries, "get_parameters", lambda _query_id: {"manager_id": 7, "month": 11, "year": 1999})
    tables = {
        "date_dim": {"d_date_sk": [1], "d_moy": [11], "d_year": [1999]},
        "store_sales": {
            "ss_sold_date_sk": [1, 1, 1],
            "ss_item_sk": [1, 2, 3],
            "ss_customer_sk": [1, 2, 3],
            "ss_store_sk": [1, 1, 1],
            "ss_ext_sales_price": [5.0, 7.0, 11.0],
        },
        "item": {
            "i_item_sk": [1, 2, 3],
            "i_manager_id": [7, 7, 7],
            "i_brand_id": [10, 20, 30],
            "i_brand": ["brand1", "brand2", "brand3"],
            "i_manufact_id": [100, 200, 300],
            "i_manufact": ["m1", "m2", "m3"],
        },
        "customer": {"c_customer_sk": [1, 2, 3], "c_current_addr_sk": [1, 2, 3]},
        "customer_address": {"ca_address_sk": [1, 2, 3], "ca_zip": ["11111", None, "22222"]},
        "store": {"s_store_sk": [1], "s_zip": ["22222"]},
    }

    assert _rows(
        queries.q19_pandas_impl(_context(family, tables))
        if family == "pandas"
        else queries.q19_expression_impl(_context(family, tables))
    ) == [(10, "brand1", 100, "m1", 5.0)]


