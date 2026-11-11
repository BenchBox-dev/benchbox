from __future__ import annotations

import logging
import sys

import pandas as pd
import pytest

from benchbox.core.tpcds.dataframe_queries.rollup_helper import (
    compute_grouping_function,
    expand_rollup_expression,
    expand_rollup_pandas,
    lochierarchy_expression,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

try:
    import pyarrow as pa

    from benchbox.platforms.dataframe.datafusion_df import DATAFUSION_DF_AVAILABLE, DataFusionDataFrameAdapter

    HAS_DATAFUSION = DATAFUSION_DF_AVAILABLE
except ImportError:
    HAS_DATAFUSION = False


class _LitExpr:
    def __init__(self, value):
        self.value = value

    def alias(self, name: str):
        return ("alias", self.value, name)


class _AggMeta:
    def __init__(self, name: str):
        self._name = name

    def output_name(self) -> str:
        return self._name


class _AggExpr:
    def __init__(self, name: str):
        self.meta = _AggMeta(name)


class _FakeFrame:
    def __init__(self, cols=None):
        self.cols = cols or []

    def group_by(self, *cols):
        return _FakeFrame(list(cols))

    def agg(self, *exprs):
        return _FakeFrame(self.cols.copy())

    def select(self, *cols):
        return _FakeFrame(list(cols))

    def with_columns(self, expr):
        return self


class _FakeColExpr:
    def __init__(self, name):
        self.name = name

    def count(self):
        return _FakeCountExpr(self.name)


class _FakeCountExpr:
    def __init__(self, name):
        self.name = name

    def alias(self, name: str):
        return ("count-alias", self.name, name)


class _FakeExprCtx:
    def lit(self, value):
        return _LitExpr(value)

    def col(self, name):
        return _FakeColExpr(name)

    def when(self, condition):
        return _FakeWhen(condition)

    def concat(self, frames):
        return frames


class _FakeWhen:
    def __init__(self, condition):
        self.condition = condition

    def then(self, value):
        return _FakeThen(value)


class _FakeThen:
    def __init__(self, value):
        self.value = value

    def otherwise(self, value):
        return _FakeOtherwise(value)


class _FakeOtherwise:
    def __init__(self, value):
        self.value = value

    def alias(self, name: str):
        return ("when-alias", name)


class _PandasAdapter:
    def groupby_agg(self, df, group_cols, agg_spec, as_index=False, dropna=True):
        return df.groupby(group_cols, as_index=False).agg(**agg_spec)


class _PandasCtx:
    def __init__(self):
        self._adapter = _PandasAdapter()

    def concat(self, frames):
        return pd.concat(frames, ignore_index=True)


def test_expand_rollup_expression_returns_all_levels():
    df = _FakeFrame()
    ctx = _FakeExprCtx()
    agg_exprs = [_AggExpr("sum_sales")]

    out = expand_rollup_expression(df, group_cols=["a", "b"], agg_exprs=agg_exprs, ctx=ctx)

    assert len(out) == 3


def test_expand_rollup_pandas_includes_grouping_id_and_null_levels():
    df = pd.DataFrame({"a": ["x", "x"], "b": ["y", "z"], "val": [1, 2]})
    ctx = _PandasCtx()
    agg = {"sum_val": ("val", "sum")}

    out = expand_rollup_pandas(df, group_cols=["a", "b"], agg_dict=agg, ctx=ctx)

    assert "grouping_id" in out.columns
    assert out["grouping_id"].max() >= 0


def test_compute_grouping_function_and_lochierarchy_fallback(monkeypatch):
    grouping_expr = compute_grouping_function(df=None, grouping_id_col="grouping_id", column_position=1)
    assert grouping_expr is not None

    monkeypatch.setitem(sys.modules, "polars", None)
    counter = lochierarchy_expression(grouping_id_col="grouping_id", num_cols=3, ctx=None)
    assert callable(counter)
    assert counter(7) == 3


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_expand_rollup_expression_aligns_levels_on_datafusion():
    adapter = DataFusionDataFrameAdapter()
    ctx = adapter.create_context()
    table = pa.table(
        {"g1": ["x", "x", "y"], "g2": ["p", "q", "p"], "v": [1, 2, 3], "w": [4, 5, 6]},
    )
    ctx.register_table("t", adapter.session_ctx.create_dataframe([table.to_batches()]))
    col = ctx.col

    result = expand_rollup_expression(
        ctx.get_table("t"),
        group_cols=["g1", "g2"],
        agg_exprs=[col("v").sum().alias("sv"), col("w").sum().alias("sw")],
        ctx=ctx,
    ).collect()

    assert result.column_names == ["g1", "g2", "sv", "sw", "grouping_id"]
    rows = sorted(
        ((r["g1"], r["g2"], r["sv"], r["sw"], r["grouping_id"]) for r in result.to_pylist()),
        key=lambda r: (r[4], r[0] or "", r[1] or ""),
    )
    assert rows == [
        ("x", "p", 1, 4, 0),
        ("x", "q", 2, 5, 0),
        ("y", "p", 3, 6, 0),
        ("x", None, 3, 9, 1),
        ("y", None, 3, 6, 1),
        (None, None, 6, 15, 3),
    ]


def _selects_names(cols) -> bool:
    flat = [c for item in cols for c in (item if isinstance(item, list) else [item])]
    return any(isinstance(c, str) for c in flat)


def test_expand_rollup_expression_surfaces_reorder_failure():

    class _FailingSelectFrame(_FakeFrame):
        def group_by(self, *_cols):
            return self

        def agg(self, *_exprs):
            return self

        def with_columns(self, _expr):
            return self

        def select(self, *cols):
            if _selects_names(cols):
                raise RuntimeError("No field named sv")
            return _FakeFrame(list(cols))

    with pytest.raises(RuntimeError, match="No field named sv"):
        expand_rollup_expression(
            _FailingSelectFrame(),
            group_cols=["a"],
            agg_exprs=[_AggExpr("sv")],
            ctx=_FakeExprCtx(),
        )


def test_expand_rollup_expression_skips_reorder_with_warning_for_unknown_expr_type(caplog):

    class _OpaqueExpr:
        pass

    class _NoSelectFrame(_FakeFrame):
        def select(self, *cols):
            if _selects_names(cols):
                raise AssertionError("reorder must be skipped when output names are unknown")
            return _FakeFrame(list(cols))

    with caplog.at_level(logging.WARNING):
        out = expand_rollup_expression(
            _NoSelectFrame(),
            group_cols=["a"],
            agg_exprs=[_OpaqueExpr()],
            ctx=_FakeExprCtx(),
        )

    assert len(out) == 2
    assert "cannot read the output name" in caplog.text


