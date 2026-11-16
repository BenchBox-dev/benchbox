from __future__ import annotations

import inspect

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

pytest.importorskip("datafusion")
pl = pytest.importorskip("polars")

from benchbox.platforms.dataframe.unified_frame import UnifiedExpr, _DataFusionDeferredFilter

AGGREGATES = {
    "sum": (),
    "mean": (),
    "avg": (),
    "count": (),
    "min": (),
    "max": (),
    "first": (),
    "last": (),
    "std": (),
    "var": (),
    "quantile": (0.5,),
    "n_unique": (),
    "approx_n_unique": (),
}

EXACT = ["sum", "mean", "avg", "count", "min", "max", "std", "var", "n_unique", "approx_n_unique"]


DATA = {
    "k": [1, 1, 1, 2, 2, 2, 3, 3, 3, 3],
    "v": [5, 6, 7, 8, 9, None, 1, 1, 2, 10],
    "x": [10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
}


def _frames():
    import pyarrow as pa

    from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

    table = pa.table(DATA)
    adapter = DataFusionDataFrameAdapter(target_partitions=1)
    ctx = adapter.create_context()
    adapter.session_ctx.register_record_batches("t", [table.to_batches()])
    ctx.register_table("t", adapter.session_ctx.table("t"))
    return ctx.get_table("t"), ctx


def _df_value(df_ctx, frame, build):
    result = frame.agg(build(df_ctx).alias("r")).collect()
    return result.column("r").to_pylist()[0]


@pytest.mark.parametrize("name", EXACT)
def test_filtered_aggregate_matches_polars(name):
    df_frame, df_ctx = _frames()
    args = AGGREGATES[name]
    expr = getattr(df_ctx.col("v").filter(df_ctx.col("x") > 30), name)(*args)
    result = df_frame.group_by("k").agg(expr.alias("r")).collect()
    actual = dict(zip(result.column("k").to_pylist(), result.column("r").to_pylist()))
    pl_expr = pl.col("v").filter(pl.col("x") > 30)
    if name in {"n_unique", "approx_n_unique"}:
        pl_expr = pl_expr.drop_nulls().n_unique()
    else:
        pl_expr = getattr(pl_expr, "mean" if name == "avg" else name)()
    expected = dict(pl.DataFrame(DATA).group_by("k").agg(pl_expr.alias("r")).iter_rows())
    assert actual.keys() == expected.keys()
    for key, value in expected.items():
        if value is None or (name == "sum" and value == 0):
            assert actual[key] in (None, 0)
        else:
            assert actual[key] == pytest.approx(value)


def test_filtered_n_unique_differs_from_unfiltered_and_skips_nulls():
    df_frame, df_ctx = _frames()
    filtered = _df_value(df_ctx, df_frame, lambda c: c.col("v").filter(c.col("x") > 50).n_unique())
    unfiltered = _df_value(df_ctx, df_frame, lambda c: c.col("v").n_unique())
    assert filtered == 3
    assert unfiltered == 8


def test_filtered_first_last_quantile_respect_filter():
    df_frame, df_ctx = _frames()
    assert _df_value(df_ctx, df_frame, lambda c: c.col("x").filter(c.col("x") > 50).min()) == 60
    assert _df_value(df_ctx, df_frame, lambda c: c.col("x").filter(c.col("x") > 50).first()) in {60, 70, 80, 90, 100}
    assert _df_value(df_ctx, df_frame, lambda c: c.col("x").filter(c.col("x") > 50).last()) in {60, 70, 80, 90, 100}
    result = df_frame.group_by("k").agg(df_ctx.col("x").filter(df_ctx.col("x") > 50).quantile(0.5).alias("r")).collect()
    medians = dict(zip(result.column("k").to_pylist(), result.column("r").to_pylist()))
    assert medians[1] is None
    assert medians[2] == 60
    assert 70 <= medians[3] <= 100


def test_grouped_filtered_n_unique_matches_polars():
    df_frame, df_ctx = _frames()
    result = df_frame.group_by("k").agg(df_ctx.col("v").filter(df_ctx.col("x") > 30).n_unique().alias("r")).collect()
    actual = dict(zip(result.column("k").to_pylist(), result.column("r").to_pylist()))
    data = pl.DataFrame(
        {
            "k": [1, 1, 1, 2, 2, 2, 3, 3, 3, 3],
            "v": [5, 6, 7, 8, 9, None, 1, 1, 2, 10],
            "x": [10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
        }
    )
    expected = dict(
        data.group_by("k").agg(pl.col("v").filter(pl.col("x") > 30).drop_nulls().n_unique().alias("r")).iter_rows()
    )
    assert actual == expected


def _public_aggregate_names():
    names = set()
    for name, member in vars(UnifiedExpr).items():
        if name.startswith("_") or not callable(member):
            continue
        parameters = [p for p in inspect.signature(member).parameters.values() if p.name != "self"]
        if any(p.default is inspect.Parameter.empty for p in parameters) and name != "quantile":
            continue
        names.add(name)
    return names


def test_every_unified_expr_aggregate_is_filter_aware_or_rejected():
    df_frame, df_ctx = _frames()
    cond = df_ctx.col("x") > 30
    filtered = df_ctx.col("v").filter(cond)
    assert isinstance(filtered, _DataFusionDeferredFilter)
    for name in vars(UnifiedExpr):
        if name.startswith("_") and not (name.startswith("__") and name.endswith("__")):
            continue
        if name in {
            "native",
            "filter",
            "__init__",
            "__repr__",
            "__dict__",
            "__weakref__",
            "__module__",
            "__doc__",
            "__hash__",
        }:
            continue
        resolved = getattr(type(filtered), name)
        if name in _DataFusionDeferredFilter._FILTERED_AGGREGATES:
            assert resolved is not getattr(UnifiedExpr, name), name
        else:
            assert resolved is not vars(UnifiedExpr)[name], name


def test_declared_aggregates_cover_unified_expr_terminals():
    terminals = {
        "sum",
        "mean",
        "avg",
        "count",
        "min",
        "max",
        "first",
        "last",
        "std",
        "var",
        "quantile",
        "n_unique",
        "approx_n_unique",
    }
    assert terminals == _DataFusionDeferredFilter._FILTERED_AGGREGATES
    assert terminals <= set(vars(UnifiedExpr))
    aggregate_like = {
        name
        for name, member in vars(UnifiedExpr).items()
        if not name.startswith("_") and callable(member) and "_apply_aggregation" in inspect.getsource(member)
    }
    assert aggregate_like <= terminals


def test_non_aggregate_operations_on_filtered_expression_raise():
    _, df_ctx = _frames()
    filtered = df_ctx.col("v").filter(df_ctx.col("x") > 30)
    with pytest.raises(NotImplementedError, match="silently ignore the filter"):
        filtered.cast_float64()
    with pytest.raises(NotImplementedError):
        filtered + 1
    with pytest.raises(NotImplementedError):
        filtered.over("k")
    with pytest.raises(NotImplementedError):
        _ = filtered.str


def test_filtered_expression_cannot_leak_as_operand_argument_or_native():
    df_frame, df_ctx = _frames()
    filtered = df_ctx.col("v").filter(df_ctx.col("x") > 30)
    with pytest.raises(NotImplementedError, match="silently ignore the filter"):
        _ = filtered.native
    with pytest.raises(NotImplementedError, match="silently ignore the filter"):
        _ = df_ctx.col("x") * filtered
    with pytest.raises(NotImplementedError, match="silently ignore the filter"):
        _ = df_ctx.lit(100.0) * filtered
    with pytest.raises(NotImplementedError, match="silently ignore the filter"):
        df_frame.select(filtered)
    with pytest.raises(NotImplementedError, match="silently ignore the filter"):
        filtered.filter(df_ctx.col("x") > 50)
    assert "filtered" in repr(filtered)
