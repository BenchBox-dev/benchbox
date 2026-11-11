from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pytest

from benchbox.core.dataframe.query import DataFrameQuery
from benchbox.core.tpch.dataframe_queries import get_query, list_query_ids, set_parameter_overrides

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@dataclass
class PandasContextStub:
    tables: dict[str, pd.DataFrame]

    def get_table(self, name: str) -> pd.DataFrame:
        return self.tables[name].copy()


def _build_tables() -> dict[str, pd.DataFrame]:
    region = pd.DataFrame(
        {
            "r_regionkey": [1, 2, 3],
            "r_name": ["EUROPE", "ASIA", "AMERICA"],
        }
    )

    nation = pd.DataFrame(
        {
            "n_nationkey": [10, 20, 30, 40, 50],
            "n_name": ["GERMANY", "FRANCE", "SAUDI ARABIA", "BRAZIL", "CANADA"],
            "n_regionkey": [1, 1, 2, 3, 3],
        }
    )

    supplier = pd.DataFrame(
        {
            "s_suppkey": [1000, 1001, 1002],
            "s_name": ["Supp-A", "Supp-B", "Supp-C"],
            "s_address": ["Addr A", "Addr B", "Addr C"],
            "s_nationkey": [10, 20, 30],
            "s_phone": ["13-000", "31-111", "17-222"],
            "s_acctbal": [100.0, 200.0, 300.0],
            "s_comment": ["Reliable", "Customer and no Complaints", "Fast"],
        }
    )

    customer = pd.DataFrame(
        {
            "c_custkey": [1, 2, 3, 4],
            "c_name": ["Cust-A", "Cust-B", "Cust-C", "Cust-D"],
            "c_address": ["A", "B", "C", "D"],
            "c_nationkey": [20, 10, 40, 50],
            "c_phone": ["13-100", "31-200", "23-300", "29-400"],
            "c_acctbal": [1000.0, 200.0, 1500.0, 50.0],
            "c_mktsegment": ["BUILDING", "BUILDING", "AUTOMOBILE", "BUILDING"],
            "c_comment": ["x", "y", "z", "w"],
        }
    )

    orders = pd.DataFrame(
        {
            "o_orderkey": [10, 11, 12, 13],
            "o_custkey": [1, 2, 1, 3],
            "o_orderstatus": ["F", "O", "F", "F"],
            "o_totalprice": [1000.0, 1500.0, 800.0, 700.0],
            "o_orderdate": pd.to_datetime(["1994-01-10", "1995-03-10", "1996-01-10", "1995-09-10"]),
            "o_orderpriority": ["1-URGENT", "2-HIGH", "3-MEDIUM", "5-LOW"],
            "o_clerk": ["C1", "C2", "C3", "C4"],
            "o_shippriority": [0, 0, 0, 0],
            "o_comment": ["normal", "special requests", "normal", "normal"],
        }
    )

    part = pd.DataFrame(
        {
            "p_partkey": [100, 101, 102],
            "p_name": ["forest green part", "promo steel", "blue widget"],
            "p_mfgr": ["M1", "M2", "M3"],
            "p_brand": ["Brand#12", "Brand#23", "Brand#34"],
            "p_type": ["PROMO POLISHED STEEL", "ECONOMY ANODIZED STEEL", "MEDIUM POLISHED TIN"],
            "p_size": [3, 15, 9],
            "p_container": ["SM CASE", "MED BOX", "LG PACK"],
        }
    )

    partsupp = pd.DataFrame(
        {
            "ps_partkey": [100, 101, 102],
            "ps_suppkey": [1000, 1001, 1002],
            "ps_availqty": [500, 300, 200],
            "ps_supplycost": [10.0, 20.0, 30.0],
        }
    )

    lineitem = pd.DataFrame(
        {
            "l_orderkey": [10, 10, 11, 12, 13],
            "l_partkey": [100, 101, 101, 102, 100],
            "l_suppkey": [1000, 1001, 1001, 1002, 1000],
            "l_linenumber": [1, 2, 1, 1, 1],
            "l_quantity": [5.0, 15.0, 3.0, 8.0, 20.0],
            "l_extendedprice": [100.0, 200.0, 50.0, 120.0, 500.0],
            "l_discount": [0.06, 0.04, 0.05, 0.07, 0.03],
            "l_tax": [0.02, 0.03, 0.01, 0.02, 0.05],
            "l_returnflag": ["R", "N", "R", "N", "R"],
            "l_linestatus": ["F", "O", "F", "O", "F"],
            "l_shipdate": pd.to_datetime(["1994-02-01", "1995-04-01", "1994-03-01", "1996-02-01", "1995-10-01"]),
            "l_commitdate": pd.to_datetime(["1994-01-30", "1995-03-28", "1994-02-28", "1996-01-30", "1995-09-25"]),
            "l_receiptdate": pd.to_datetime(["1994-02-10", "1995-04-10", "1994-03-05", "1996-02-10", "1995-10-10"]),
            "l_shipmode": ["MAIL", "AIR", "SHIP", "AIR REG", "MAIL"],
            "l_shipinstruct": [
                "DELIVER IN PERSON",
                "DELIVER IN PERSON",
                "NONE",
                "DELIVER IN PERSON",
                "DELIVER IN PERSON",
            ],
        }
    )

    return {
        "region": region,
        "nation": nation,
        "supplier": supplier,
        "customer": customer,
        "orders": orders,
        "part": part,
        "partsupp": partsupp,
        "lineitem": lineitem,
    }


def _timestamp_overrides() -> dict[int, dict[str, object]]:
    return {
        1: {"cutoff_date": pd.Timestamp("1998-09-02")},
        3: {"order_date": pd.Timestamp("1995-03-15")},
        4: {"start_date": pd.Timestamp("1993-07-01"), "end_date": pd.Timestamp("1993-10-01")},
        5: {"start_date": pd.Timestamp("1994-01-01"), "end_date": pd.Timestamp("1995-01-01")},
        6: {"start_date": pd.Timestamp("1994-01-01"), "end_date": pd.Timestamp("1995-01-01")},
        7: {"start_date": pd.Timestamp("1995-01-01"), "end_date": pd.Timestamp("1996-12-31")},
        8: {"start_date": pd.Timestamp("1995-01-01"), "end_date": pd.Timestamp("1996-12-31")},
        10: {"start_date": pd.Timestamp("1993-10-01"), "end_date": pd.Timestamp("1994-01-01")},
        12: {"start_date": pd.Timestamp("1994-01-01"), "end_date": pd.Timestamp("1995-01-01")},
        14: {"start_date": pd.Timestamp("1995-09-01"), "end_date": pd.Timestamp("1995-10-01")},
        15: {"start_date": pd.Timestamp("1996-01-01"), "end_date": pd.Timestamp("1996-04-01")},
        20: {"start_date": pd.Timestamp("1994-01-01"), "end_date": pd.Timestamp("1995-01-01")},
    }


@pytest.mark.parametrize("query_id", list_query_ids())
def test_all_pandas_query_implementations_execute_without_error(query_id: str) -> None:
    set_parameter_overrides(_timestamp_overrides())
    query = get_query(query_id)
    assert query.pandas_impl is not None

    ctx = PandasContextStub(_build_tables())

    result = query.pandas_impl(ctx)

    assert result is not None
    assert hasattr(result, "shape")
    set_parameter_overrides(None)


def test_parameter_override_round_trip() -> None:
    overrides = _timestamp_overrides()
    overrides[6] = {**overrides[6], "quantity_limit": 999}
    set_parameter_overrides(overrides)
    query = get_query("Q6")
    ctx = PandasContextStub(_build_tables())

    result = query.pandas_impl(ctx)

    assert not result.empty
    set_parameter_overrides(None)


