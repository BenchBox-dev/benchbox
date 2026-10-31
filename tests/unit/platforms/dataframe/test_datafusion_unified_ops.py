from __future__ import annotations

from datetime import date, datetime

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def _context(name, table):
    from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

    adapter = DataFusionDataFrameAdapter(target_partitions=2)
    ctx = adapter.create_context()
    adapter.session_ctx.register_record_batches(name, [table.to_batches()])
    ctx.register_table(name, adapter.session_ctx.table(name))
    return ctx


def test_truncate_to_second_keeps_whole_seconds():
    pytest.importorskip("datafusion")
    import pyarrow as pa

    table = pa.table({"t": pa.array([datetime(2024, 1, 1, 10, 30, 15, 750000)], pa.timestamp("us"))})
    ctx = _context("ev", table)

    result = ctx.get_table("ev").select(ctx.col("t").dt.truncate("1s").alias("s")).collect()

    assert result.column("s").to_pylist() == [datetime(2024, 1, 1, 10, 30, 15)]


def test_total_days_handles_date_and_timestamp_differences():
    pytest.importorskip("datafusion")
    import pyarrow as pa

    table = pa.table(
        {
            "d1": pa.array([date(1995, 1, 11)], pa.date32()),
            "d0": pa.array([date(1995, 1, 1)], pa.date32()),
            "t1": pa.array([datetime(1995, 1, 11, 12)], pa.timestamp("us")),
            "t0": pa.array([datetime(1995, 1, 1)], pa.timestamp("us")),
        }
    )
    ctx = _context("diffs", table)
    col = ctx.col

    result = (
        ctx.get_table("diffs")
        .with_columns(
            (col("d1") - col("d0")).dt.total_days().alias("date_days"),
            (col("t1") - col("t0")).dt.total_days().alias("timestamp_days"),
        )
        .collect()
    )

    assert result.column("date_days").to_pylist() == [10]
    assert result.column("timestamp_days").to_pylist() == [10]


def test_select_runs_approximate_distinct_count_as_an_aggregate():
    pytest.importorskip("datafusion")
    import pyarrow as pa

    ctx = _context("keys", pa.table({"k": list(range(100)) * 3}))

    result = ctx.get_table("keys").select(ctx.col("k").approx_n_unique().alias("n")).collect()

    assert result.num_rows == 1
    assert 90 <= result.column("n").to_pylist()[0] <= 110


def test_map_from_arrays_supports_extract_keys_and_values():
    pytest.importorskip("datafusion")
    import pyarrow as pa

    ctx = _context("pairs", pa.table({"g": [1, 1, 2], "k": ["a", "b", "c"], "v": [1.5, 2.5, 3.5]}))
    col = ctx.col

    result = (
        ctx.get_table("pairs")
        .group_by("g")
        .agg(col("k").sort_by("k").alias("ks"), col("v").sort_by("k").alias("vs"))
        .with_columns(ctx.map_from_arrays("ks", "vs").alias("m"))
        .select(
            "g",
            col("m").map.get(ctx.lit("b")).alias("b_value"),
            col("m").map.keys().alias("keys"),
            col("m").map.values().alias("values"),
        )
        .sort("g")
        .collect()
    )

    assert result.column("b_value").to_pylist() == [2.5, None]
    assert result.column("keys").to_pylist() == [["a", "b"], ["c"]]
    assert result.column("values").to_pylist() == [[1.5, 2.5], [3.5]]
