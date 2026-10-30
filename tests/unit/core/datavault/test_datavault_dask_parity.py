from __future__ import annotations

from datetime import date

import pytest

from tests.fixtures.dataframe_parity import FAMILIES, build_context, result_rows

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def _dates(*values):
    import pandas as pd
    import pyarrow as pa

    return pd.array(list(values), dtype=pd.ArrowDtype(pa.date32()))


def _open(n):
    return [None] * n


def _frame(**columns):
    import pandas as pd

    return pd.DataFrame(columns)


@pytest.mark.parametrize("family", FAMILIES)
def test_q4_order_priority_counts(family):
    from benchbox.core.datavault.dataframe_queries.queries import q4_pandas_impl

    tables = {
        "hub_order": _frame(hk_order=["o1", "o2", "o3"]),
        "sat_order": _frame(
            hk_order=["o1", "o2", "o3"],
            o_orderpriority=["1-URGENT", "1-URGENT", "2-HIGH"],
            o_orderdate=_dates(date(1993, 7, 10), date(1993, 8, 1), date(1993, 9, 1)),
            load_end_dts=_open(3),
        ),
        "link_lineitem": _frame(hk_lineitem_link=["l1", "l2", "l3", "l4"], hk_order=["o1", "o2", "o3", "o3"]),
        "sat_lineitem": _frame(
            hk_lineitem_link=["l1", "l2", "l3", "l4"],
            l_commitdate=_dates(date(1993, 7, 1), date(1993, 7, 1), date(1993, 7, 5), date(1993, 7, 1)),
            l_receiptdate=_dates(date(1993, 7, 2), date(1993, 7, 2), date(1993, 7, 2), date(1993, 7, 2)),
            load_end_dts=_open(4),
        ),
    }

    assert result_rows(q4_pandas_impl(build_context(family, tables))) == [("1-URGENT", 2), ("2-HIGH", 1)]


@pytest.mark.parametrize("family", FAMILIES)
def test_q13_customer_distribution(family):
    from benchbox.core.datavault.dataframe_queries.queries import q13_pandas_impl

    tables = {
        "hub_customer": _frame(hk_customer=["c1", "c2", "c3"], c_custkey=[1, 2, 3]),
        "link_order_customer": _frame(hk_order=["o1", "o2", "o3"], hk_customer=["c1", "c1", "c2"]),
        "sat_order": _frame(
            hk_order=["o1", "o2", "o3"],
            o_comment=["fine", "special requests", "fine"],
            load_end_dts=_open(3),
        ),
    }

    assert result_rows(q13_pandas_impl(build_context(family, tables))) == [(1, 2), (0, 1)]


@pytest.mark.parametrize("family", FAMILIES)
def test_q14_promo_revenue_percentage(family):
    from benchbox.core.datavault.dataframe_queries.queries import q14_pandas_impl

    tables = {
        "link_lineitem": _frame(hk_lineitem_link=["l1", "l2"], hk_part=["p1", "p2"]),
        "sat_lineitem": _frame(
            hk_lineitem_link=["l1", "l2"],
            l_shipdate=_dates(date(1995, 9, 5), date(1995, 9, 6)),
            l_extendedprice=[100.0, 100.0],
            l_discount=[0.1, 0.0],
            load_end_dts=_open(2),
        ),
        "sat_part": _frame(hk_part=["p1", "p2"], p_type=["PROMO BRUSHED", "STANDARD"], load_end_dts=_open(2)),
    }

    rows = result_rows(q14_pandas_impl(build_context(family, tables)))

    assert rows[0][0] == pytest.approx(100.0 * 90.0 / 190.0)


@pytest.mark.parametrize("family", FAMILIES)
def test_q21_suppliers_who_kept_orders_waiting(family):
    from benchbox.core.datavault.dataframe_queries.queries import q21_pandas_impl

    tables = {
        "sat_supplier": _frame(hk_supplier=["s1", "s2"], s_name=["S1", "S2"], load_end_dts=_open(2)),
        "link_lineitem": _frame(hk_lineitem_link=["l1", "l2"], hk_order=["o1", "o1"], hk_supplier=["s1", "s2"]),
        "sat_lineitem": _frame(
            hk_lineitem_link=["l1", "l2"],
            l_commitdate=_dates(date(1995, 1, 1), date(1995, 1, 5)),
            l_receiptdate=_dates(date(1995, 1, 3), date(1995, 1, 2)),
            load_end_dts=_open(2),
        ),
        "sat_order": _frame(hk_order=["o1"], o_orderstatus=["F"], load_end_dts=_open(1)),
        "link_supplier_nation": _frame(hk_supplier=["s1", "s2"], hk_nation=["n1", "n1"]),
        "sat_nation": _frame(hk_nation=["n1"], n_name=["SAUDI ARABIA"], load_end_dts=_open(1)),
    }

    assert result_rows(q21_pandas_impl(build_context(family, tables))) == [("S1", 1)]
