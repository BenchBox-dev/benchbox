from __future__ import annotations

import pytest

from benchbox.core.tpcds.dataframe_queries.parameter_adapters import ADAPTERS

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]

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


def _run(family, number, parameters, tables):
    from benchbox.core.equivalence.dataframe_surface import materialize_rows
    from benchbox.core.tpcds.dataframe_queries import queries
    from benchbox.core.tpcds.dataframe_queries.parameters import parameter_overrides

    impl = getattr(queries, f"q{number}_{family}_impl")
    with parameter_overrides({number: parameters}):
        return materialize_rows(impl(_context(family, tables)))


class TestAdapters:
    def test_q9_takes_the_five_row_count_thresholds_and_both_summed_columns(self):
        logged = {
            "RC.01": "393",
            "RC.02": "109",
            "RC.03": "322",
            "RC.04": "256",
            "RC.05": "140",
            "AGGCTHEN.01": "ss_ext_list_price",
            "AGGCELSE.01": "ss_net_paid",
        }
        assert ADAPTERS[9](logged) == {
            "quantity_ranges": [(1, 20), (21, 40), (41, 60), (61, 80), (81, 100)],
            "thresholds": [393, 109, 322, 256, 140],
            "agg_then": "ss_ext_list_price",
            "agg_else": "ss_net_paid",
        }

    def test_q46_takes_the_five_city_slots_in_template_order(self):
        logged = {
            "YEAR.01": "1999",
            "DEPCNT.01": "6",
            "VEHCNT.01": "2",
            **{
                f"CITY_{letter}.01": city for letter, city in zip("ABCDE", ["Midway", "Fairview", "Fairview", "X", "Y"])
            },
        }
        assert ADAPTERS[46](logged) == {
            "year": 1999,
            "year_offsets": [0, 1, 2],
            "dow": [6, 0],
            "cities": ["Midway", "Fairview", "Fairview", "X", "Y"],
            "dep_count": 6,
            "vehicle_count": 2,
        }

    def test_q68_takes_two_cities_and_the_household_counts(self):
        logged = {"YEAR.01": "2000", "DEPCNT.01": "2", "VEHCNT.01": "-1", "CITY_A.01": "Midway", "CITY_B.01": "Oak"}
        assert ADAPTERS[68](logged) == {
            "year": 2000,
            "year_offsets": [0, 1, 2],
            "cities": ["Midway", "Oak"],
            "dep_count": 2,
            "vehicle_count": -1,
        }


@pytest.mark.parametrize("family", FAMILIES)
def test_q9_uses_the_drawn_thresholds_and_columns_and_reports_an_empty_bucket_as_none(family):
    tables = {
        "store_sales": {
            "ss_quantity": [5, 10, 25],
            "ss_ext_discount_amt": [1.0, 3.0, 100.0],
            "ss_ext_tax": [10.0, 30.0, 1000.0],
            "ss_net_paid": [2.0, 4.0, 200.0],
            "ss_net_profit": [-1.0, -3.0, -50.0],
        },
        "reason": {"r_reason_sk": [1]},
    }
    drawn = {
        "thresholds": [1, 5, 0, 0, 0],
        "agg_then": "ss_ext_tax",
        "agg_else": "ss_net_paid",
    }
    assert _run(family, 9, drawn, tables) == [(20.0, 200.0, None, None, None)]
    assert _run(family, 9, {"thresholds": [1, 5, 0, 0, 0]}, tables) == [(2.0, -50.0, None, None, None)]


def _household_tables(year):
    return {
        "store_sales": {
            "ss_ticket_number": [1, 2],
            "ss_customer_sk": [1, 1],
            "ss_addr_sk": [1, 1],
            "ss_sold_date_sk": [1, 1],
            "ss_store_sk": [1, 1],
            "ss_hdemo_sk": [1, 2],
            "ss_coupon_amt": [5.0, 7.0],
            "ss_net_profit": [1.0, 2.0],
            "ss_ext_sales_price": [10.0, 20.0],
            "ss_ext_list_price": [11.0, 21.0],
            "ss_ext_tax": [0.5, 0.75],
        },
        "date_dim": {"d_date_sk": [1], "d_year": [year], "d_dow": [6], "d_dom": [1]},
        "store": {"s_store_sk": [1], "s_city": ["Midway"]},
        "household_demographics": {"hd_demo_sk": [1, 2], "hd_dep_count": [2, 5], "hd_vehicle_count": [0, 0]},
        "customer_address": {"ca_address_sk": [1, 2], "ca_city": ["Midway", "Oak"]},
        "customer": {
            "c_customer_sk": [1],
            "c_last_name": ["Smith"],
            "c_first_name": ["Ann"],
            "c_current_addr_sk": [2],
        },
    }


@pytest.mark.parametrize("family", FAMILIES)
def test_q46_filters_on_the_drawn_year_households_and_cities(family):
    drawn = {"year": 1998, "cities": ["Midway", "Midway"], "dep_count": 2, "vehicle_count": 1}
    assert _run(family, 46, drawn, _household_tables(1998)) == [("Smith", "Ann", "Oak", "Midway", 1, 5.0, 1.0)]
    assert _run(family, 46, {}, _household_tables(1998)) == []
    default_households = _run(family, 46, {"year": 1998}, _household_tables(1998))
    assert default_households == [("Smith", "Ann", "Oak", "Midway", 2, 7.0, 2.0)]


@pytest.mark.parametrize("family", FAMILIES)
def test_q68_filters_on_the_drawn_year_households_and_cities(family):
    drawn = {"year": 1998, "cities": ["Midway", "Midway"], "dep_count": 2, "vehicle_count": 1}
    assert _run(family, 68, drawn, _household_tables(1998)) == [("Smith", "Ann", "Oak", "Midway", 1, 10.0, 0.5, 11.0)]
    assert _run(family, 68, {}, _household_tables(1998)) == []
    default_households = _run(family, 68, {"year": 1998}, _household_tables(1998))
    assert default_households == [("Smith", "Ann", "Oak", "Midway", 2, 20.0, 0.75, 21.0)]


