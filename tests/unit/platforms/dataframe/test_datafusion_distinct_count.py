from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

GROUPS = 20_000
ROWS_PER_GROUP = 5


def _context(table):
    from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

    adapter = DataFusionDataFrameAdapter(target_partitions=4)
    ctx = adapter.create_context()
    adapter.session_ctx.register_record_batches("facts", [table.to_batches()])
    ctx.register_table("facts", adapter.session_ctx.table("facts"))
    return ctx


def _sample_table():
    import pyarrow as pa

    keys = [index // ROWS_PER_GROUP for index in range(GROUPS * ROWS_PER_GROUP)]
    values = [f"v{index % 3}" if index % 7 else None for index in range(GROUPS * ROWS_PER_GROUP)]
    keys[0] = None
    keys[1] = None
    return pa.table({"grp": keys, "value": values})


def _plan_text(frame) -> str:
    return frame.native.optimized_logical_plan().display_indent()


def test_grouped_distinct_count_matches_duckdb_with_null_keys_and_values():
    pytest.importorskip("datafusion")
    import duckdb

    table = _sample_table()
    ctx = _context(table)
    frame = ctx.get_table("facts").group_by("grp").agg(ctx.col("value").n_unique().alias("n_values"))
    result = frame.sort("grp").collect()
    actual = dict(zip(result.column("grp").to_pylist(), result.column("n_values").to_pylist()))
    with duckdb.connect() as connection:
        connection.register("facts", table)
        expected = dict(connection.execute("SELECT grp, count(DISTINCT value) FROM facts GROUP BY grp").fetchall())
    assert len(actual) == GROUPS + 1
    assert actual == expected


def test_grouped_distinct_count_plans_as_two_stage_aggregate_without_distinct_accumulator():
    pytest.importorskip("datafusion")

    ctx = _context(_sample_table())
    frame = ctx.get_table("facts").group_by("grp").agg(ctx.col("value").n_unique().alias("n_values"))
    plan = _plan_text(frame)
    assert "DISTINCT" not in plan
    assert plan.count("Aggregate:") == 2


def test_global_and_multi_aggregate_distinct_counts_keep_native_accumulator():
    pytest.importorskip("datafusion")

    ctx = _context(_sample_table())
    mixed = (
        ctx.get_table("facts")
        .group_by("grp")
        .agg(ctx.col("value").n_unique().alias("n_values"), ctx.col("value").count().alias("n_rows"))
    )
    assert "DISTINCT" in _plan_text(mixed)
    assert mixed.collect().num_rows == GROUPS + 1
    total = ctx.get_table("facts").select(ctx.col("value").n_unique().alias("n_values")).collect()
    assert total.column("n_values").to_pylist() == [3]
