from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

GROUPS = ["a", "a", "a", "a", "b", "b"]
VALUES = [1, 1, 2, None, None, None]


def _polars_frame():
    pl = pytest.importorskip("polars")
    from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame

    adapter = MagicMock()
    adapter.platform_name = "Polars"
    return pl, UnifiedLazyFrame(pl.DataFrame({"grp": GROUPS, "value": VALUES}).lazy(), adapter)


def _datafusion_table():
    pytest.importorskip("datafusion")
    pa = pytest.importorskip("pyarrow")
    from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

    adapter = DataFusionDataFrameAdapter()
    ctx = adapter.create_context()
    table = pa.table({"grp": GROUPS, "value": pa.array(VALUES, type=pa.int64())})
    adapter.session_ctx.register_record_batches("facts", [table.to_batches()])
    ctx.register_table("facts", adapter.session_ctx.table("facts"))
    return ctx


def test_polars_select_counts_distinct_non_null():
    from benchbox.platforms.dataframe.unified_frame import UnifiedExpr

    pl, frame = _polars_frame()
    result = frame.select(UnifiedExpr(pl.col("value")).n_unique().alias("n")).collect()
    assert result["n"].to_list() == [2]


def test_polars_group_by_counts_distinct_non_null_and_zero_for_all_null_group():
    from benchbox.platforms.dataframe.unified_frame import UnifiedExpr

    pl, frame = _polars_frame()
    result = frame.group_by("grp").agg(UnifiedExpr(pl.col("value")).n_unique().alias("n")).sort("grp").collect()
    assert result["n"].to_list() == [2, 0]


def test_polars_over_window_counts_distinct_non_null():
    from benchbox.platforms.dataframe.unified_frame import UnifiedExpr

    pl, frame = _polars_frame()
    expr = UnifiedExpr(pl.col("value")).n_unique().over("grp").alias("n")
    result = frame.with_columns(expr).collect()
    assert result["n"].to_list() == [2, 2, 2, 2, 0, 0]


def test_datafusion_select_counts_distinct_non_null():
    ctx = _datafusion_table()
    result = ctx.get_table("facts").select(ctx.col("value").n_unique().alias("n")).collect()
    assert result.column("n").to_pylist() == [2]


def test_datafusion_group_by_counts_distinct_non_null_and_zero_for_all_null_group():
    ctx = _datafusion_table()
    result = ctx.get_table("facts").group_by("grp").agg(ctx.col("value").n_unique().alias("n")).sort("grp").collect()
    assert dict(zip(result.column("grp").to_pylist(), result.column("n").to_pylist())) == {"a": 2, "b": 0}
