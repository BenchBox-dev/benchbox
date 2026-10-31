from __future__ import annotations

import pytest

from tests.fixtures.dataframe_parity import FAMILIES, build_context, result_rows

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _tables(nation_name: str):
    import pandas as pd

    keys = [1000 + 7 * i for i in range(12)]
    lines = []
    for index, key in enumerate(keys):
        lines.append((key, 1, "2024-01-05", "2024-01-01"))
        lines.append((key, 2, "2024-01-01", "2024-01-03"))
        if index % 3 == 0:
            lines.append((key, 3, "2024-01-09", "2024-01-02"))
    lineitem = pd.DataFrame(lines, columns=["l_orderkey", "l_suppkey", "l_receiptdate", "l_commitdate"])
    lineitem["l_receiptdate"] = pd.to_datetime(lineitem["l_receiptdate"])
    lineitem["l_commitdate"] = pd.to_datetime(lineitem["l_commitdate"])
    return {
        "nation": pd.DataFrame({"n_nationkey": [0, 1], "n_name": [nation_name, "OTHER"]}),
        "supplier": pd.DataFrame(
            {"s_suppkey": [1, 2, 3], "s_name": ["Supplier#1", "Supplier#2", "Supplier#3"], "s_nationkey": [0, 0, 1]}
        ),
        "orders": pd.DataFrame({"o_orderkey": keys, "o_orderstatus": ["F"] * len(keys)}),
        "lineitem": lineitem,
    }


@pytest.mark.parametrize("family", FAMILIES)
def test_q21_selects_orders_by_key_not_by_position(family):
    from benchbox.core.tpch.dataframe_queries import get_tpch_parameters, q21_pandas_impl

    nation_name = get_tpch_parameters(21)["nation_name"]
    ctx = build_context(family, _tables(nation_name), npartitions=3)

    rows = result_rows(q21_pandas_impl(ctx))

    assert rows == [("Supplier#1", 8)]
