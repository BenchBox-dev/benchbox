from __future__ import annotations

from datetime import date, timedelta

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def _context(tables):
    from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

    adapter = DataFusionDataFrameAdapter(target_partitions=2)
    ctx = adapter.create_context()
    for name, table in tables.items():
        adapter.session_ctx.register_record_batches(name, [table.to_batches()])
        ctx.register_table(name, adapter.session_ctx.table(name))
    return ctx


def test_window_moving_frame_sums_orders_in_the_trailing_thirty_days():
    pytest.importorskip("datafusion")
    import pyarrow as pa

    from benchbox.core.read_primitives.dataframe_queries import window_moving_frame_expression_impl

    offsets = [0, 0, 10, 25, 31, 40, 70]
    orders = pa.table(
        {
            "o_orderkey": list(range(1, 8)),
            "o_orderdate": pa.array([date(1995, 1, 1) + timedelta(days=d) for d in offsets], pa.date32()),
            "o_totalprice": [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0],
        }
    )
    ctx = _context({"orders": orders})

    result = window_moving_frame_expression_impl(ctx).collect()

    assert result.column("monthly_running_total").to_pylist() == [3.0, 3.0, 7.0, 15.0, 28.0, 60.0, 96.0]
    assert result.column("moving_avg_6_orders").to_pylist()[:3] == [1.0, 1.5, pytest.approx(7 / 3)]


def test_olap_rollup_analysis_adds_subtotal_rows_with_null_dimensions():
    pytest.importorskip("datafusion")
    import pyarrow as pa

    from benchbox.core.read_primitives.dataframe_queries import olap_rollup_analysis_expression_impl

    tables = {
        "orders": pa.table(
            {
                "o_custkey": [1, 2, 3],
                "o_orderdate": pa.array([date(1995, 2, 1)] * 3, pa.date32()),
                "o_totalprice": [10.0, 20.0, 40.0],
            }
        ),
        "customer": pa.table(
            {"c_custkey": [1, 2, 3], "c_nationkey": [0, 0, 1], "c_mktsegment": ["AUTO", "AUTO", "HOME"]}
        ),
        "nation": pa.table({"n_nationkey": [0, 1], "n_name": ["ALGERIA", "BRAZIL"], "n_regionkey": [0, 1]}),
        "region": pa.table({"r_regionkey": [0, 1], "r_name": ["AFRICA", "AMERICA"]}),
    }
    ctx = _context(tables)

    result = olap_rollup_analysis_expression_impl(ctx).collect().to_pylist()

    totals = {(row["region"], row["nation"], row["market_segment"]): row["total_revenue"] for row in result}
    assert totals[(None, None, None)] == 70.0
    assert totals[("AFRICA", None, None)] == 30.0
    assert totals[("AFRICA", "ALGERIA", "AUTO")] == 30.0
    assert result[-1]["region"] is None
