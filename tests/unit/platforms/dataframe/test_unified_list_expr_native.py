# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

pl = pytest.importorskip("polars", reason="Polars not installed")

from benchbox.platforms.dataframe.unified_frame import UnifiedExpr, UnifiedLazyFrame, UnifiedListExpr


def _polars_frame():
    df = pl.DataFrame({"arr": [[10, 20, 30], [40, 50]], "idx": [1, 0]}).lazy()
    return UnifiedLazyFrame(df, adapter=SimpleNamespace(platform_name="Polars"))


def test_list_expr_exposes_native():
    native = pl.col("arr").list.sort()
    assert UnifiedExpr(pl.col("arr")).list.sort().native is not None
    assert UnifiedListExpr(native, is_polars=True).native is native


def test_bare_list_expr_in_with_columns_and_select():
    frame = _polars_frame()
    bare = UnifiedExpr(pl.col("arr")).list.sort()
    assert not isinstance(bare, UnifiedExpr)

    out = frame.with_columns(bare).collect()
    assert out["arr"].to_list() == [[10, 20, 30], [40, 50]]

    out = frame.select(bare).collect()
    assert out["arr"].to_list() == [[10, 20, 30], [40, 50]]


def test_get_accepts_expression_index_polars():
    frame = _polars_frame()
    out = frame.with_columns(UnifiedExpr(pl.col("arr")).list.get(UnifiedExpr(pl.col("idx"))).alias("picked")).collect()
    assert out["picked"].to_list() == [20, 40]


try:
    import datafusion
    import pyarrow as pa

    HAS_DATAFUSION = True
except ImportError:
    HAS_DATAFUSION = False


def _datafusion_frame():
    ctx = datafusion.SessionContext()
    table = pa.table({"arr": [[10, 20, 30], [40, 50]], "idx": [1, 0]})
    ctx.register_record_batches("t", [table.to_batches()])
    return UnifiedLazyFrame(ctx.sql("SELECT * FROM t"), adapter=SimpleNamespace(platform_name="DataFusion"))


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_get_accepts_expression_index_datafusion_with_offset():
    frame = _datafusion_frame()
    out = (
        frame.with_columns(
            UnifiedExpr(datafusion.col("arr")).list.get(UnifiedExpr(datafusion.col("idx"))).alias("picked")
        )
        .collect()
        .to_pydict()
    )

    assert out["picked"] == [20, 40]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_bare_list_expr_in_select_datafusion():
    frame = _datafusion_frame()
    bare = UnifiedExpr(datafusion.col("arr")).list.sort()
    out = frame.select(bare.alias("sorted")).collect().to_pydict()
    assert out["sorted"] == [[10, 20, 30], [40, 50]]


