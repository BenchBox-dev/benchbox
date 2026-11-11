from __future__ import annotations

from datetime import date

import pytest

from benchbox.core.tpcds.dataframe_queries import queries
from benchbox.core.tpcds.dataframe_queries.parameters import parameter_overrides

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]


def _context(backend, tables):
    if backend == "datafusion":
        pytest.importorskip("datafusion")
        import pyarrow as pa

        from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

        adapter = DataFusionDataFrameAdapter(target_partitions=4)
        ctx = adapter.create_context()
        for name, table in tables.items():
            adapter.session_ctx.register_record_batches(name, [pa.table(table).to_batches()])
            ctx.register_table(name, adapter.session_ctx.table(name))
    elif backend == "polars":
        pl = pytest.importorskip("polars")
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        ctx = PolarsDataFrameAdapter().create_context()
        for name, table in tables.items():
            ctx.register_table(name, pl.from_arrow(table).lazy())
    else:
        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        ctx = PandasDataFrameAdapter().create_context()
        for name, table in tables.items():
            ctx.register_table(name, table.to_pandas())
    return ctx


def _rows(frame, backend):
    if backend == "datafusion":
        table = frame.collect()
        return list(zip(*(column.to_pylist() for column in table.columns)))
    from benchbox.core.equivalence.dataframe_surface import materialize_rows

    return materialize_rows(frame)


def _tables():
    import pyarrow as pa

    return {
        "web_sales": pa.table(
            {
                "ws_item_sk": [1, 1, 1, 1, 1, 2, 2, 3, 4, None, 1],
                "ws_sold_date_sk": [3, 1, 1, 2, 4, 2, 1, 1, 4, 1, 5],
                "ws_sales_price": [2.0, 5.0, 3.0, None, -3.0, 4.0, None, 20.0, 6.0, 100.0, 1000.0],
            }
        ),
        "store_sales": pa.table(
            {
                "ss_item_sk": [1, 1, 2, 2, 4, 5, None, 1],
                "ss_sold_date_sk": [1, 2, 1, 3, 3, 1, 1, 5],
                "ss_sales_price": [3.0, 4.0, 2.0, 5.0, 2.0, 7.0, 100.0, 1000.0],
            }
        ),
        "date_dim": pa.table(
            {
                "d_date_sk": [1, 2, 3, 4, 5],
                "d_date": [date(2000, 1, day) for day in range(1, 6)],
                "d_month_seq": [1212, 1212, 1212, 1212, 1224],
            }
        ),
    }


@pytest.fixture(scope="module")
def tpcds_benchmark(tmp_path_factory):
    from benchbox.tpcds import TPCDS

    return TPCDS(scale_factor=0.01, output_dir=tmp_path_factory.mktemp("q51-sql"))


@pytest.mark.parametrize("backend", ["datafusion", "polars", "pandas"])
def test_q51_matches_generated_sql_with_null_days_and_unmatched_channels(backend, tpcds_benchmark):
    import duckdb

    tables = _tables()
    implementation = tpcds_benchmark._impl
    raw = implementation.query_manager.dsqgen.generate_with_parameters(51, {"DMS.01": "1212"}, scale_factor=0.01)
    sql = implementation.translate_query_text(raw, "netezza", "duckdb")
    with duckdb.connect() as connection:
        for name, table in tables.items():
            connection.register(name, table)
        expected = connection.execute(sql).fetchall()
    assert len(expected) == 6
    ctx = _context(backend, tables)
    with parameter_overrides({51: {"dms": 1212}}):
        function = queries.q51_pandas_impl if backend == "pandas" else queries.q51_expression_impl
        actual = _rows(function(ctx), backend)
    assert actual == expected


@pytest.mark.parametrize("backend", ["datafusion", "polars"])
def test_q51_channel_merge_retains_unmatched_null_dates(backend):
    import duckdb
    import pyarrow as pa

    tables = {
        "web": pa.table(
            {"item_sk": [1, 1, 2], "d_date": [None, date(2000, 1, 1), date(2000, 1, 2)], "cume_sales": [9.0, 4.0, None]}
        ),
        "store": pa.table(
            {"item_sk": [1, 1, 3], "d_date": [None, date(2000, 1, 1), date(2000, 1, 2)], "cume_sales": [2.0, 1.0, 8.0]}
        ),
    }
    with duckdb.connect() as connection:
        for name, table in tables.items():
            connection.register(name, table)
        expected = connection.execute(
            "SELECT coalesce(w.item_sk, s.item_sk), coalesce(w.d_date, s.d_date), w.cume_sales, s.cume_sales "
            "FROM web w FULL OUTER JOIN store s ON w.item_sk=s.item_sk AND w.d_date=s.d_date"
        ).fetchall()
    ctx = _context(backend, tables)
    actual = _rows(queries._q51_merge_channels(ctx, ctx.get_table("web"), ctx.get_table("store")), backend)
    assert len(actual) == 5
    assert sorted(actual, key=repr) == sorted(expected, key=repr)


@pytest.mark.parametrize("backend", ["datafusion", "polars"])
@pytest.mark.parametrize("operation,divisor", [("sum", 1.0), ("max", 1.0), ("sum", 2.0)])
def test_cumulative_windows_use_explicit_order_and_current_row_frame(backend, operation, divisor):
    import duckdb
    import pyarrow as pa

    tables = {"t": pa.table({"item": [1, 2, 1, 2, 1], "day": [3, 2, 1, 1, 2], "value": [2.0, 4.0, 8.0, 3.0, -5.0]})}
    with duckdb.connect() as connection:
        connection.register("t", tables["t"])
        expected = connection.execute(
            f"SELECT item, day, {operation}(value / {divisor}) OVER (PARTITION BY item ORDER BY day "
            "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running FROM t ORDER BY item, day"
        ).fetchall()
    ctx = _context(backend, tables)
    expression = getattr(ctx.col("value") / ctx.lit(divisor), f"cum_{operation}")().over("item", order_by="day")
    frame = (
        ctx.get_table("t")
        .with_columns(expression.alias("running"))
        .select("item", "day", "running")
        .sort(["item", "day"])
    )
    assert _rows(frame, backend) == expected


@pytest.mark.parametrize("backend", ["datafusion", "polars"])
def test_cumulative_sum_counts_each_peer_row(backend):
    import pyarrow as pa

    ctx = _context(backend, {"t": pa.table({"item": [1, 1, 1], "day": [1, 1, 2], "value": [1.0, 1.0, 5.0]})})
    expression = ctx.col("value").cum_sum().over("item", order_by="day")
    result = ctx.get_table("t").with_columns(expression.alias("running")).select("running")
    assert sorted(row[0] for row in _rows(result, backend)) == [1.0, 2.0, 7.0]


@pytest.mark.parametrize("operation", ["sum", "max"])
def test_datafusion_cumulative_windows_reject_missing_order(operation):
    pytest.importorskip("datafusion")
    from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

    ctx = DataFusionDataFrameAdapter().create_context()
    with pytest.raises(ValueError, match="explicit order_by"):
        getattr(ctx.col("value"), f"cum_{operation}")().over("item")


