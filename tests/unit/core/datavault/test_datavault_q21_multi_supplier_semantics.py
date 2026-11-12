from __future__ import annotations

from datetime import date

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ON_TIME = (date(1995, 1, 1), date(1995, 1, 5))
LATE = (date(1995, 1, 5), date(1995, 1, 1))


def _tables():
    lines = [
        ("o1", "s1", LATE),
        ("o2", "s1", LATE),
        ("o2", "s2", ON_TIME),
        ("o3", "s1", LATE),
        ("o3", "s2", LATE),
        ("o4", "s1", ON_TIME),
        ("o4", "s2", LATE),
        ("o4", "s3", ON_TIME),
    ]
    links = [f"l{i}" for i in range(len(lines))]
    return {
        "sat_supplier": {
            "hk_supplier": ["s1", "s2", "s3"],
            "s_name": ["Supplier#1", "Supplier#2", "Supplier#3"],
            "load_end_dts": [None] * 3,
        },
        "link_lineitem": {
            "hk_lineitem_link": links,
            "hk_order": [order for order, _, _ in lines],
            "hk_supplier": [supp for _, supp, _ in lines],
        },
        "sat_lineitem": {
            "hk_lineitem_link": links,
            "l_receiptdate": [dates[0] for _, _, dates in lines],
            "l_commitdate": [dates[1] for _, _, dates in lines],
            "load_end_dts": [None] * len(lines),
        },
        "sat_order": {"hk_order": ["o1", "o2", "o3", "o4"], "o_orderstatus": ["F"] * 4, "load_end_dts": [None] * 4},
        "link_supplier_nation": {"hk_supplier": ["s1", "s2", "s3"], "hk_nation": ["n1"] * 3},
        "sat_nation": {"hk_nation": ["n1"], "n_name": ["SAUDI ARABIA"], "load_end_dts": [None]},
    }


def _context(engine):
    import pyarrow as pa

    if engine == "polars":
        pl = pytest.importorskip("polars")
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        ctx = PolarsDataFrameAdapter().create_context()
        for name, data in _tables().items():
            ctx.register_table(name, pl.DataFrame(data).lazy())
        return ctx
    pytest.importorskip("datafusion")
    from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

    adapter = DataFusionDataFrameAdapter()
    ctx = adapter.create_context()
    for name, data in _tables().items():
        schema = None
        if name in {"sat_supplier", "sat_lineitem", "sat_order", "sat_nation"}:
            schema = pa.schema(
                [(key, pa.null() if key == "load_end_dts" else pa.array(values).type) for key, values in data.items()]
            )
        adapter.session_ctx.register_record_batches(name, [pa.table(data, schema=schema).to_batches()])
        ctx.register_table(name, adapter.session_ctx.table(name))
    return ctx


def _rows(engine, result):
    if engine == "polars":
        return [tuple(row) for row in result.collect().rows()]
    table = result.collect()
    return list(zip(*(table.column(i).to_pylist() for i in range(table.num_columns))))


@pytest.mark.parametrize("engine", ["polars", "datafusion"])
def test_q21_counts_late_supplier_only_on_multi_supplier_orders_with_one_late_supplier(engine):
    from benchbox.core.datavault.dataframe_queries.queries import q21_expression_impl

    rows = _rows(engine, q21_expression_impl(_context(engine)))

    assert rows == [("Supplier#1", 1), ("Supplier#2", 1)]
