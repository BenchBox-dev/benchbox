from __future__ import annotations

from datetime import date

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ON_TIME = (date(1995, 1, 1), date(1995, 1, 5))
LATE = (date(1995, 1, 5), date(1995, 1, 1))


def _tables(nation_name):
    lines = [
        (1, 1, LATE),
        (2, 1, LATE),
        (2, 2, ON_TIME),
        (3, 1, LATE),
        (3, 2, LATE),
        (4, 1, ON_TIME),
        (4, 2, LATE),
        (4, 3, ON_TIME),
    ]
    return {
        "nation": {"n_nationkey": [0], "n_name": [nation_name]},
        "supplier": {
            "s_suppkey": [1, 2, 3],
            "s_name": ["Supplier#1", "Supplier#2", "Supplier#3"],
            "s_nationkey": [0, 0, 0],
        },
        "orders": {"o_orderkey": [1, 2, 3, 4], "o_orderstatus": ["F"] * 4},
        "lineitem": {
            "l_orderkey": [order for order, _, _ in lines],
            "l_suppkey": [supp for _, supp, _ in lines],
            "l_receiptdate": [dates[0] for _, _, dates in lines],
            "l_commitdate": [dates[1] for _, _, dates in lines],
        },
    }


def _context(engine, tables):
    if engine == "polars":
        pl = pytest.importorskip("polars")
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        ctx = PolarsDataFrameAdapter().create_context()
        for name, data in tables.items():
            ctx.register_table(name, pl.DataFrame(data).lazy())
        return ctx
    pa = pytest.importorskip("pyarrow")
    pytest.importorskip("datafusion")
    from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

    adapter = DataFusionDataFrameAdapter()
    ctx = adapter.create_context()
    for name, data in tables.items():
        adapter.session_ctx.register_record_batches(name, [pa.table(data).to_batches()])
        ctx.register_table(name, adapter.session_ctx.table(name))
    return ctx


def _rows(engine, result):
    if engine == "polars":
        return [tuple(row) for row in result.collect().rows()]
    table = result.collect()
    return list(zip(*(table.column(i).to_pylist() for i in range(table.num_columns))))


@pytest.mark.parametrize("engine", ["polars", "datafusion"])
def test_q21_counts_late_supplier_only_on_multi_supplier_orders_with_one_late_supplier(engine):
    from benchbox.core.tpch.dataframe_queries import get_tpch_parameters, q21_expression_impl

    tables = _tables(get_tpch_parameters(21)["nation_name"])

    rows = _rows(engine, q21_expression_impl(_context(engine, tables)))

    assert rows == [("Supplier#1", 1), ("Supplier#2", 1)]
