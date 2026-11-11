from __future__ import annotations

from decimal import Decimal

import pytest
from test_null_aggregates import _check

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]


def _draw(query_id):
    from benchbox import TPCDS
    from benchbox.core.tpcds.c_tools import DSQGenBinary
    from benchbox.core.tpcds.dataframe_queries.parameter_adapters import bind_parameters

    dsqgen = DSQGenBinary()
    binding = bind_parameters(query_id, scale_factor=0.01, seed=None, stream_id=0, dsqgen=dsqgen)
    raw = dsqgen.generate_with_parameters(query_id, dict(binding.logged), scale_factor=0.01, seed=binding.seed)
    sql = TPCDS(scale_factor=0.01)._impl.translate_query_text(raw, "netezza", "duckdb")
    return dict(binding.parameters), sql


_AMOUNTS = [None, None, None, Decimal("0.00"), None, Decimal("5.00")]


@pytest.mark.parametrize("family", ["expression", "pandas"])
def test_q19_sums_preserve_null_zero_and_populated_groups(monkeypatch, family):
    params, sql = _draw(19)
    spec = {
        "date_dim": ("d_date_sk INTEGER,d_year INTEGER,d_moy INTEGER", [(1, params["year"], params["month"])]),
        "item": (
            "i_item_sk INTEGER,i_manager_id INTEGER,i_brand_id INTEGER,i_brand VARCHAR,"
            "i_manufact_id INTEGER,i_manufact VARCHAR",
            [(key, params["manager_id"], key * 10, f"brand{key}", key * 100, f"m{key}") for key in (1, 2, 3)],
        ),
        "customer": ("c_customer_sk INTEGER,c_current_addr_sk INTEGER", [(1, 1)]),
        "customer_address": ("ca_address_sk INTEGER,ca_zip VARCHAR", [(1, "11111")]),
        "store": ("s_store_sk INTEGER,s_zip VARCHAR", [(1, "22222")]),
        "store_sales": (
            "ss_ticket_number INTEGER,ss_item_sk INTEGER,ss_sold_date_sk INTEGER,ss_customer_sk INTEGER,"
            "ss_store_sk INTEGER,ss_ext_sales_price DECIMAL(15,2)",
            [(row + 1, row // 2 + 1, 1, 1, 1, value) for row, value in enumerate(_AMOUNTS)],
        ),
    }
    assert _check(monkeypatch, family, 19, params, spec, sql, float_money=True) == [
        (10, "brand1", 100, "m1", None),
        (30, "brand3", 300, "m3", 5),
        (20, "brand2", 200, "m2", 0),
    ]


@pytest.mark.parametrize("family", ["expression", "pandas"])
def test_q15_sums_preserve_null_zero_and_populated_groups(monkeypatch, family):
    params, sql = _draw(15)
    spec = {
        "date_dim": ("d_date_sk INTEGER,d_year INTEGER,d_qoy INTEGER", [(1, params["year"], params["quarter"])]),
        "customer": ("c_customer_sk INTEGER,c_current_addr_sk INTEGER", [(key, key) for key in (1, 2, 3)]),
        "customer_address": (
            "ca_address_sk INTEGER,ca_zip VARCHAR,ca_state VARCHAR",
            [(1, "11111", "CA"), (2, "22222", "CA"), (3, None, "CA")],
        ),
        "catalog_sales": (
            "cs_order_number INTEGER,cs_item_sk INTEGER,cs_bill_customer_sk INTEGER,"
            "cs_sold_date_sk INTEGER,cs_sales_price DECIMAL(15,2)",
            [(row + 1, 1, row // 2 + 1, 1, value) for row, value in enumerate(_AMOUNTS)],
        ),
    }
    assert _check(monkeypatch, family, 15, params, spec, sql, float_money=True) == [
        ("11111", None),
        ("22222", 0),
        (None, 5),
    ]


@pytest.mark.parametrize("family", ["expression", "pandas"])
def test_q91_sums_preserve_null_zero_and_populated_groups(monkeypatch, family):
    params, sql = _draw(91)
    spec = {
        "date_dim": ("d_date_sk INTEGER,d_year INTEGER,d_moy INTEGER", [(1, params["year"], params["month"])]),
        "call_center": (
            "cc_call_center_sk INTEGER,cc_call_center_id VARCHAR,cc_name VARCHAR,cc_manager VARCHAR",
            [(key, f"CC{key}", f"Center{key}", f"Manager{key}") for key in (1, 2, 3)],
        ),
        "customer": (
            "c_customer_sk INTEGER,c_current_cdemo_sk INTEGER,c_current_hdemo_sk INTEGER,c_current_addr_sk INTEGER",
            [(1, 1, 1, 1)],
        ),
        "customer_demographics": (
            "cd_demo_sk INTEGER,cd_marital_status VARCHAR,cd_education_status VARCHAR",
            [(1, "M", "Unknown")],
        ),
        "household_demographics": ("hd_demo_sk INTEGER,hd_buy_potential VARCHAR", [(1, params["buy_potential"])]),
        "customer_address": ("ca_address_sk INTEGER,ca_gmt_offset DECIMAL(15,2)", [(1, params["gmt_offset"])]),
        "catalog_returns": (
            "cr_order_number INTEGER,cr_item_sk INTEGER,cr_call_center_sk INTEGER,"
            "cr_returned_date_sk INTEGER,cr_returning_customer_sk INTEGER,cr_net_loss DECIMAL(15,2)",
            [(row + 1, 1, row // 2 + 1, 1, 1, value) for row, value in enumerate(_AMOUNTS)],
        ),
    }
    assert _check(monkeypatch, family, 91, params, spec, sql, float_money=True) == [
        ("CC1", "Center1", "Manager1", None),
        ("CC3", "Center3", "Manager3", 5),
        ("CC2", "Center2", "Manager2", 0),
    ]


@pytest.mark.parametrize("family", ["expression", "pandas"])
def test_q73_count_and_filters_are_unchanged(monkeypatch, family):
    params, sql = _draw(73)
    spec = {
        "date_dim": ("d_date_sk INTEGER,d_year INTEGER,d_dom INTEGER", [(1, params["year"], 1)]),
        "store": ("s_store_sk INTEGER,s_county VARCHAR", [(1, params["counties"][0])]),
        "household_demographics": (
            "hd_demo_sk INTEGER,hd_buy_potential VARCHAR,hd_vehicle_count INTEGER,hd_dep_count INTEGER",
            [(1, params["buy_potential_1"], 1, 2), (2, params["buy_potential_1"], 1, 0)],
        ),
        "customer": (
            "c_customer_sk INTEGER,c_last_name VARCHAR,c_first_name VARCHAR,c_salutation VARCHAR,c_preferred_cust_flag VARCHAR",
            [(key, f"Last{key}", "First", "Mr.", "Y") for key in (1, 2, 3)],
        ),
        "store_sales": (
            "ss_ticket_number INTEGER,ss_item_sk INTEGER,ss_sold_date_sk INTEGER,ss_store_sk INTEGER,"
            "ss_hdemo_sk INTEGER,ss_customer_sk INTEGER",
            [(10, 1, 1, 1, 1, 1), (20, 1, 1, 1, 1, 2), (30, 1, 1, 1, 2, 3)],
        ),
    }
    assert _check(monkeypatch, family, 73, params, spec, sql) == [
        ("Last1", "First", "Mr.", "Y", 10, 1),
        ("Last2", "First", "Mr.", "Y", 20, 1),
    ]


