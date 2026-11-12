from __future__ import annotations

from datetime import date

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

FAMILIES = ["polars", "datafusion", "pandas"]
WAREHOUSES = [1, 2, 1, None, None, None]
ORDERS = [1, 1, 2, 2, 3, 3]


def _sales(prefix, extra):
    sales = {
        f"{prefix}_order_number": ORDERS,
        f"{prefix}_warehouse_sk": WAREHOUSES,
        f"{prefix}_ship_date_sk": [1] * 6,
        f"{prefix}_ship_addr_sk": [1] * 6,
        f"{prefix}_ext_ship_cost": [1.0, 1.0, 10.0, 10.0, 100.0, 100.0],
        f"{prefix}_net_profit": [3.0, 3.0, 30.0, 30.0, 300.0, 300.0],
    }
    sales.update(extra)
    return sales


def _tables(returned):
    common = {
        "date_dim": {"d_date_sk": [1], "d_date": [date(2000, 3, 10)]},
        "customer_address": {"ca_address_sk": [1], "ca_state": ["TX"]},
    }
    return {
        **common,
        "web_sales": _sales("ws", {"ws_web_site_sk": [1] * 6}),
        "web_returns": {"wr_order_number": returned},
        "web_site": {"web_site_sk": [1], "web_company_name": ["pri"]},
        "catalog_sales": _sales("cs", {"cs_call_center_sk": [1] * 6}),
        "catalog_returns": {"cr_order_number": [999]},
        "call_center": {"cc_call_center_sk": [1], "cc_county": ["Walker County"]},
    }


def _context(family, tables):
    if family == "polars":
        pl = pytest.importorskip("polars")
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        ctx = PolarsDataFrameAdapter().create_context()
        for name, data in tables.items():
            ctx.register_table(name, pl.DataFrame(data).lazy())
    elif family == "datafusion":
        pa = pytest.importorskip("pyarrow")
        pytest.importorskip("datafusion")
        from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

        adapter = DataFusionDataFrameAdapter()
        ctx = adapter.create_context()
        for name, data in tables.items():
            columns = {
                key: pa.array(values, type=pa.float64() if None in values else None) for key, values in data.items()
            }
            adapter.session_ctx.register_record_batches(name, [pa.table(columns).to_batches()])
            ctx.register_table(name, adapter.session_ctx.sql(f"SELECT * FROM {name}"))
    else:
        import pandas as pd

        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        ctx = PandasDataFrameAdapter().create_context()
        for name, data in tables.items():
            ctx.register_table(name, pd.DataFrame(data))
    return ctx


def _run(family, number):
    from benchbox.core.equivalence.dataframe_surface import materialize_rows
    from benchbox.core.tpcds.dataframe_queries import queries
    from benchbox.core.tpcds.dataframe_queries.parameters import parameter_overrides

    parameters = {
        16: {"year": 2000, "month": 3, "state": "TX", "counties": ["Walker County"]},
        94: {"year": 2000, "month": 3, "states": ["TX"]},
        95: {"year": 2000, "month": 3, "states": ["TX"]},
    }
    impl_family = "pandas" if family == "pandas" else "expression"
    with parameter_overrides({number: parameters[number]}):
        result = getattr(queries, f"q{number}_{impl_family}_impl")(
            _context(family, _tables([1, 2, 3] if number == 95 else [999]))
        )
    if family == "datafusion":
        table = result.collect()
        return list(zip(*(table.column(i).to_pylist() for i in range(table.num_columns))))
    return materialize_rows(result)


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("number", [16, 94, 95])
def test_only_orders_with_two_distinct_warehouses_are_multi_warehouse(family, number):
    assert _run(family, number) == [(1, 2.0, 6.0)]
