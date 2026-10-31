from __future__ import annotations

from datetime import date

import pytest

from tests.fixtures.dataframe_parity import FAMILIES, build_context, result_rows

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def _dates(values):
    import pandas as pd
    import pyarrow as pa

    return pd.array(values, dtype=pd.ArrowDtype(pa.date32()))


def _orders_lineitems(count: int):
    import pandas as pd

    orders = pd.DataFrame(
        {
            "o_orderkey": list(range(1, count + 1)),
            "o_custkey": [1 + (i % 4) for i in range(count)],
            "o_orderdate": _dates([date(1993, 7, 1 + (i % 28)) for i in range(count)]),
            "o_orderpriority": [f"{1 + i % 3}-PRIORITY" for i in range(count)],
            "o_totalprice": [1000.0 + 7 * i for i in range(count)],
            "o_orderstatus": ["F" if i % 2 == 0 else "O" for i in range(count)],
        }
    )
    lineitem = pd.DataFrame(
        {
            "l_orderkey": [k for k in range(1, count + 1) for _ in range(3)],
            "l_suppkey": [1 + (k + j) % 3 for k in range(1, count + 1) for j in range(3)],
            "l_quantity": [120.0 + j for _ in range(1, count + 1) for j in range(3)],
            "l_commitdate": _dates([date(1993, 8, 1)] * (3 * count)),
            "l_receiptdate": _dates([date(1993, 8, 2 if j == 0 else 1) for _ in range(1, count + 1) for j in range(3)]),
        }
    )
    return orders, lineitem


def _tables():
    import pandas as pd

    orders, lineitem = _orders_lineitems(12)
    customer = pd.DataFrame(
        {"c_custkey": [1, 2, 3, 4], "c_name": ["c1", "c2", "c3", "c4"], "c_nationkey": [1, 5, 14, 20]}
    )
    supplier = pd.DataFrame({"s_suppkey": [1, 2, 3], "s_name": ["s1", "s2", "s3"], "s_nationkey": [7, 7, 8]})
    nation = pd.DataFrame({"n_nationkey": [7, 8], "n_name": ["SAUDI ARABIA", "FRANCE"]})
    return {"customer": customer, "orders": orders, "lineitem": lineitem, "supplier": supplier, "nation": nation}


def _ids(query_prefix: str, variants: int) -> list[str]:
    return [f"{query_prefix}v{v}" for v in range(1, variants + 1)]


@pytest.mark.parametrize("query_id", _ids("Q4", 10) + _ids("Q18", 10) + _ids("Q21", 10))
def test_variant_rows_match_pandas_on_dask(query_id, monkeypatch):
    import benchbox.core.dataframe
    from benchbox.core.tpch import dataframe_queries as tpch_queries
    from benchbox.core.tpchavoc.dataframe_queries import get_query

    original = tpch_queries.get_tpch_parameters

    def parameters(query_number):
        values = dict(original(query_number))
        if query_number == 18:
            values["quantity_threshold"] = 360
        if query_number == 21:
            values["nation_name"] = "SAUDI ARABIA"
        return values

    for module_name in ("q04", "q18", "q21"):
        module = __import__(f"benchbox.core.tpchavoc.dataframe_queries.{module_name}", fromlist=["get_tpch_parameters"])
        monkeypatch.setattr(module, "get_tpch_parameters", parameters)
    monkeypatch.setattr(tpch_queries, "get_tpch_parameters", parameters)

    impl = get_query(query_id).pandas_impl
    tables = _tables()
    expected = result_rows(impl(build_context(FAMILIES[0], tables)))
    actual = result_rows(impl(build_context(FAMILIES[1], tables, npartitions=3)))

    assert actual == expected
    if query_id.startswith("Q21"):
        assert expected
