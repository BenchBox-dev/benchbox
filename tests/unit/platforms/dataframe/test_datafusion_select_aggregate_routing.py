from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

pytest.importorskip("datafusion")
pl = pytest.importorskip("polars")

DATA = {
    "k": [1, 1, 1, 2, 2, 2, 3, 3, 3, 3],
    "v": [5, 6, 7, 8, 9, None, 1, 1, 2, 10],
    "x": [10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
}


def _frames():
    import pyarrow as pa

    from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

    adapter = DataFusionDataFrameAdapter(target_partitions=1)
    ctx = adapter.create_context()
    adapter.session_ctx.register_record_batches("t", [pa.table(DATA).to_batches()])
    ctx.register_table("t", adapter.session_ctx.table("t"))
    return ctx, ctx.get_table("t")


def _terminal(name, expr):
    return {"var": expr.var, "std": expr.std, "quantile": lambda: expr.quantile(0.5)}[name]()


@pytest.mark.parametrize("filtered", [False, True])
@pytest.mark.parametrize("aliased", [False, True])
@pytest.mark.parametrize("name", ["var", "std", "quantile"])
def test_ungrouped_select_aggregates_match_polars(name, filtered, aliased):
    ctx, frame = _frames()
    expr = ctx.col("v")
    pl_col = pl.col("v")
    if filtered:
        expr = expr.filter(ctx.col("x") > 20)
        pl_col = pl_col.filter(pl.col("x") > 20)
    out = _terminal(name, expr)
    if aliased:
        out = out.alias("r")

    got = frame.select(out).collect().column(0).to_pylist()[0]

    pl_expr = {
        "var": pl_col.var(),
        "std": pl_col.std(),
        "quantile": pl_col.quantile(0.5, interpolation="nearest"),
    }[name]
    expected = pl.DataFrame(DATA).select(pl_expr).item()
    assert got == pytest.approx(expected, rel=1e-9)


def test_plain_projection_is_not_aggregated():
    ctx, frame = _frames()
    plain = [ctx.col("x").native, (ctx.col("x") + 1).native, ctx.col("x").abs().native]
    assert not frame._has_aggregate_expr(plain)
    result = frame.select(ctx.col("x"), (ctx.col("x") + 1).alias("y")).collect()
    assert result.num_rows == len(DATA["x"])


def test_wrapped_aggregate_detected():
    ctx, frame = _frames()
    assert frame._has_aggregate_expr([ctx.col("x").sum().abs().native])
    assert frame._has_aggregate_expr([(ctx.col("x").sum() / ctx.col("x").count()).native])
