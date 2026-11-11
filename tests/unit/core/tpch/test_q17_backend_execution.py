from __future__ import annotations

import pytest

from benchbox.core.tpch.dataframe_queries import get_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.mark.parametrize("platform", ["polars", "datafusion"])
@pytest.mark.parametrize("case", ["empty", "all_null", "nonempty"])
def test_q17_adapter_matches_sql_null_and_revenue(platform, case):
    import duckdb
    import pyarrow as pa

    part = pa.table({"p_partkey": [1], "p_brand": ["Brand#23"], "p_container": ["MED BOX"]})
    quantities = [10.0, 10.0] if case == "empty" else [1.0, 19.0]
    prices = [None, None] if case == "all_null" else [70.0, 140.0]
    lineitem = pa.table(
        {
            "l_partkey": [1, 1],
            "l_quantity": quantities,
            "l_extendedprice": pa.array(prices, type=pa.float64()),
        }
    )
    if platform == "polars":
        import polars as pl

        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        adapter = PolarsDataFrameAdapter()
        ctx = adapter.create_context()
        ctx.register_table("part", pl.from_arrow(part).lazy())
        ctx.register_table("lineitem", pl.from_arrow(lineitem).lazy())
    else:
        from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

        adapter = DataFusionDataFrameAdapter()
        ctx = adapter.create_context()
        for name, table in [("part", part), ("lineitem", lineitem)]:
            adapter.register_table(name, table)
            ctx.register_table(name, adapter.session_ctx.table(name))

    with duckdb.connect() as oracle:
        oracle.register("part", part)
        oracle.register("lineitem", lineitem)
        expected = oracle.execute(
            "SELECT SUM(l_extendedprice) / 7.0 AS avg_yearly "
            "FROM lineitem, part WHERE p_partkey = l_partkey "
            "AND p_brand = 'Brand#23' AND p_container = 'MED BOX' "
            "AND l_quantity < (SELECT 0.2 * AVG(l_quantity) FROM lineitem WHERE l_partkey = p_partkey)"
        ).fetchone()

    result = adapter.execute_query(ctx, get_query("Q17"))
    assert result["status"] == "SUCCESS", result.get("error")
    assert result["rows_returned"] == 1
    assert result["first_row"] == expected
    assert expected == ((10.0,) if case == "nonempty" else (None,))


