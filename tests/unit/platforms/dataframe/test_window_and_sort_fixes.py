# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

pl = pytest.importorskip("polars", reason="Polars not installed")

from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter
from benchbox.platforms.dataframe.unified_frame import UnifiedExpr, UnifiedLazyFrame

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_window_lag_sorts_before_shifting():
    adapter = PolarsDataFrameAdapter()
    df = pl.DataFrame({"g": [1, 1, 1], "o": [3, 1, 2], "v": [30, 10, 20]})
    expr = adapter.window_lag("v", 1, partition_by=["g"], order_by=[("o", True)])
    out = df.with_columns(expr.alias("lag")).sort(["g", "o"])

    assert out["lag"].to_list() == [None, 10, 20]


def test_window_lead_sorts_before_shifting():
    adapter = PolarsDataFrameAdapter()
    df = pl.DataFrame({"g": [1, 1, 1], "o": [3, 1, 2], "v": [30, 10, 20]})
    expr = adapter.window_lead("v", 1, partition_by=["g"], order_by=[("o", True)])
    out = df.with_columns(expr.alias("lead")).sort(["g", "o"])
    assert out["lead"].to_list() == [20, 30, None]


def test_window_ntile_even_distribution():
    adapter = PolarsDataFrameAdapter()

    df = pl.DataFrame({"o": [10, 20, 30, 40, 50]})
    expr = adapter.window_ntile(3, order_by=[("o", True)])
    out = df.with_columns(expr.alias("nt")).sort("o")
    assert out["nt"].to_list() == [1, 1, 2, 2, 3]


def test_window_ntile_partition_smaller_than_n():
    adapter = PolarsDataFrameAdapter()

    df = pl.DataFrame({"o": [10, 20]})
    expr = adapter.window_ntile(4, order_by=[("o", True)])
    out = df.with_columns(expr.alias("nt")).sort("o")
    assert out["nt"].to_list() == [1, 2]


def test_unified_sort_desc_marker_is_descending():
    adapter = PolarsDataFrameAdapter()
    lf = UnifiedLazyFrame(pl.DataFrame({"a": [1, 1, 2], "b": [1, 3, 2]}).lazy(), adapter)
    out = lf.sort(UnifiedExpr(pl.col("a")), UnifiedExpr(pl.col("b")).desc()).native.collect()

    assert out.select("a", "b").rows() == [(1, 3), (1, 1), (2, 2)]


def test_window_lag_honors_composite_order_by():
    adapter = PolarsDataFrameAdapter()

    df = pl.DataFrame({"g": [1, 1, 1], "o1": [1, 2, 1], "o2": [2, 1, 1], "v": [20, 30, 10]})
    expr = adapter.window_lag("v", 1, partition_by=["g"], order_by=[("o1", True), ("o2", True)])
    out = df.with_columns(expr.alias("lag")).sort(["o1", "o2"])
    assert out["lag"].to_list() == [None, 10, 20]


def test_window_lead_honors_composite_order_by():
    adapter = PolarsDataFrameAdapter()
    df = pl.DataFrame({"g": [1, 1, 1], "o1": [1, 2, 1], "o2": [2, 1, 1], "v": [20, 30, 10]})
    expr = adapter.window_lead("v", 1, partition_by=["g"], order_by=[("o1", True), ("o2", True)])
    out = df.with_columns(expr.alias("lead")).sort(["o1", "o2"])
    assert out["lead"].to_list() == [20, 30, None]


def test_window_ntile_honors_composite_order_by():
    adapter = PolarsDataFrameAdapter()

    df = pl.DataFrame({"o1": [1, 1, 2, 2], "o2": [2, 1, 2, 1]})
    expr = adapter.window_ntile(2, order_by=[("o1", True), ("o2", True)])
    out = df.with_columns(expr.alias("nt")).sort(["o1", "o2"])
    assert out["nt"].to_list() == [1, 1, 2, 2]


def test_window_helpers_reject_mixed_order_directions():
    adapter = PolarsDataFrameAdapter()
    with pytest.raises(ValueError, match="uniform ORDER BY direction"):
        adapter.window_lag("v", 1, partition_by=["g"], order_by=[("o1", True), ("o2", False)])
    with pytest.raises(ValueError, match="uniform ORDER BY direction"):
        adapter.window_ntile(2, order_by=[("o1", True), ("o2", False)])


