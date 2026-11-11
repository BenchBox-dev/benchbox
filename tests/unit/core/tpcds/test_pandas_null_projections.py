from __future__ import annotations

from datetime import date

import pytest
from test_null_aggregates import _check

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]


@pytest.mark.parametrize("family", ["expression", "pandas"])
def test_q6_counts_a_null_state_group(monkeypatch, family):
    from benchbox import TPCDS

    spec = {
        "customer_address": ("ca_address_sk INTEGER,ca_state VARCHAR", [(1, None), (2, "TX")]),
        "customer": (
            "c_customer_sk INTEGER,c_current_addr_sk INTEGER",
            [(key, 1 if key <= 10 else 2) for key in range(1, 21)],
        ),
        "date_dim": ("d_date_sk INTEGER,d_year INTEGER,d_moy INTEGER,d_month_seq INTEGER", [(1, 2000, 2, 1200)]),
        "item": ("i_item_sk INTEGER,i_category VARCHAR,i_current_price DOUBLE", [(1, "c", 100.0), (2, "c", 1.0)]),
        "store_sales": (
            "ss_customer_sk INTEGER,ss_sold_date_sk INTEGER,ss_item_sk INTEGER",
            [(key, 1, 1) for key in range(1, 21)],
        ),
    }
    sql = TPCDS(scale_factor=0.01).get_query(6)
    assert _check(monkeypatch, family, 6, {"year": 2000, "month": 2}, spec, sql) == [("TX", 10), (None, 10)]


@pytest.mark.parametrize("family", ["expression", "pandas"])
def test_q72_counts_null_group_keys_and_all_rows(monkeypatch, family):
    from benchbox import TPCDS

    spec = {
        "date_dim": (
            "d_date_sk INTEGER,d_week_seq INTEGER,d_year INTEGER,d_date DATE",
            [(1, 1, 2001, date(2001, 1, 1)), (2, 1, 2001, date(2001, 1, 2)), (3, 2, 2001, date(2001, 1, 10))],
        ),
        "item": ("i_item_sk INTEGER,i_item_desc VARCHAR", [(1, None), (2, "A"), (3, "B")]),
        "warehouse": ("w_warehouse_sk INTEGER,w_warehouse_name VARCHAR", [(1, "W"), (2, None)]),
        "inventory": (
            "inv_item_sk INTEGER,inv_date_sk INTEGER,inv_warehouse_sk INTEGER,inv_quantity_on_hand INTEGER",
            [(1, 2, 1, 0), (2, 2, 2, 0), (3, 2, 1, 0)],
        ),
        "customer_demographics": ("cd_demo_sk INTEGER,cd_marital_status VARCHAR", [(1, "M")]),
        "household_demographics": ("hd_demo_sk INTEGER,hd_buy_potential VARCHAR", [(1, "1001-5000")]),
        "promotion": ("p_promo_sk INTEGER,p_promo_id VARCHAR", [(1, "P")]),
        "catalog_returns": ("cr_item_sk INTEGER,cr_order_number INTEGER PRIMARY KEY", []),
        "catalog_sales": (
            "cs_item_sk INTEGER,cs_sold_date_sk INTEGER,cs_ship_date_sk INTEGER,cs_quantity INTEGER,"
            "cs_bill_cdemo_sk INTEGER,cs_bill_hdemo_sk INTEGER,cs_promo_sk INTEGER,cs_order_number INTEGER",
            [
                (item, 1, 3, 5, 1, 1, promo, order)
                for item, promo, order in [(1, None, 1), (1, 1, 2), (2, 1, 3), (2, 1, 4), (2, None, 5), (3, 1, 6)]
            ],
        ),
    }
    sql = TPCDS(scale_factor=0.01).get_query(72)
    assert _check(monkeypatch, family, 72, {}, spec, sql) == [
        ("A", None, 1, 1, 2, 3),
        (None, "W", 1, 1, 1, 2),
        ("B", "W", 1, 0, 1, 1),
    ]


@pytest.mark.parametrize("family", ["expression", "pandas"])
@pytest.mark.parametrize("query_id", [30, 81])
def test_return_customers_preserve_numeric_and_string_nulls(monkeypatch, family, query_id):
    from benchbox import TPCDS

    spec = {
        "customer": (
            "c_customer_sk INTEGER,c_current_addr_sk INTEGER,c_customer_id VARCHAR,c_salutation VARCHAR,"
            "c_first_name VARCHAR,c_last_name VARCHAR,c_preferred_cust_flag VARCHAR,c_birth_day INTEGER,"
            "c_birth_month INTEGER,c_birth_year INTEGER,c_birth_country VARCHAR,c_login VARCHAR,"
            "c_email_address VARCHAR,c_last_review_date_sk INTEGER",
            [
                (1, 1, "A", None, "First", None, None, None, 2, None, None, None, None, None),
                (2, 2, "B", "Mr.", "Other", "Name", "Y", 1, 2, 1990, "US", "b", "b@x", 1),
                (3, 2, "C", "Mr.", "Third", "Name", "N", 2, 3, 1991, "US", "c", "c@x", 2),
            ],
        ),
        "customer_address": (
            "ca_address_sk INTEGER,ca_state VARCHAR,ca_street_number VARCHAR,ca_street_name VARCHAR,"
            "ca_street_type VARCHAR,ca_suite_number VARCHAR,ca_city VARCHAR,ca_county VARCHAR,ca_zip VARCHAR,"
            "ca_country VARCHAR,ca_gmt_offset DOUBLE,ca_location_type VARCHAR",
            [
                (1, "IL", None, None, "Court", None, None, None, None, None, None, None),
                (2, "IL", "1", "Main", "St", "A", "City", "County", "12345", "US", -5.0, "residential"),
            ],
        ),
        "date_dim": ("d_date_sk INTEGER,d_year INTEGER", [(1, 2002 if query_id == 30 else 1998)]),
    }
    table, prefix, amount = (
        ("web_returns", "wr", "return_amt") if query_id == 30 else ("catalog_returns", "cr", "return_amt_inc_tax")
    )
    spec[table] = (
        f"{prefix}_returned_date_sk INTEGER,{prefix}_returning_addr_sk INTEGER,"
        f"{prefix}_returning_customer_sk INTEGER,{prefix}_{amount} DOUBLE",
        [(1, 1, 1, 1000.0), (1, 2, 2, 1.0), (1, 2, 3, None)],
    )
    expected = (
        ("A", None, "First", None, None, None, 2, None, None, None, None, None, 1000.0)
        if query_id == 30
        else ("A", None, "First", None, None, None, "Court", None, None, None, "IL", None, None, None, None, 1000.0)
    )
    sql = TPCDS(scale_factor=0.01).get_query(query_id)
    assert _check(monkeypatch, family, query_id, {}, spec, sql) == [expected]


@pytest.mark.parametrize("family", ["expression", "pandas"])
def test_q74_preserves_null_customer_names(monkeypatch, family):
    from benchbox import TPCDS

    spec = {
        "customer": (
            "c_customer_sk INTEGER,c_customer_id VARCHAR,c_first_name VARCHAR,c_last_name VARCHAR",
            [(1, "A", "First", None), (2, "B", None, "Last"), (3, "C", "First", "Last")],
        ),
        "date_dim": ("d_date_sk INTEGER,d_year INTEGER", [(1, 2001), (2, 2002)]),
    }
    for table, customer, date_key, amount, current in (
        ("store_sales", "ss_customer_sk", "ss_sold_date_sk", "ss_net_paid", 110.0),
        ("web_sales", "ws_bill_customer_sk", "ws_sold_date_sk", "ws_net_paid", 200.0),
    ):
        spec[table] = (
            f"{customer} INTEGER,{date_key} INTEGER,{amount} DOUBLE",
            [(key, year_key, 100.0 if year_key == 1 else current) for key in (1, 2, 3) for year_key in (1, 2)],
        )
    sql = TPCDS(scale_factor=0.01).get_query(74)
    assert _check(monkeypatch, family, 74, {}, spec, sql) == [
        ("A", "First", None),
        ("C", "First", "Last"),
        ("B", None, "Last"),
    ]


