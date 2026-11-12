from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from benchbox.platforms.dataframe import unified_frame as uf

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _DFExpr:
    __module__ = "datafusion.expr"

    def __init__(self, value):
        self.value = value

    def sort(self, ascending=True, nulls_first=True):
        return _DFSortExpr(("sort", self.value, ascending, nulls_first))

    def alias(self, name):
        return _DFExpr(("alias", self.value, name))

    def over(self, window):
        return _DFExpr(("over", self.value, window))

    def is_null(self):
        return _DFExpr(("is_null", self.value))

    def filter(self, condition):
        return _DFAggExpr(("filtered", self.value, getattr(condition, "value", condition)))

    def build(self):
        return _DFExpr(("built", self.value))

    def __mul__(self, other):
        return _DFExpr(("mul", self.value, getattr(other, "value", other)))

    def __add__(self, other):
        return _DFExpr(("add", self.value, getattr(other, "value", other)))

    def __sub__(self, other):
        return _DFExpr(("sub", self.value, getattr(other, "value", other)))

    def __truediv__(self, other):
        return _DFExpr(("div", self.value, getattr(other, "value", other)))

    def __str__(self):
        return str(self.value)


class _DFAggExpr(_DFExpr):
    def filter(self, condition):
        return _DFAggExpr(("agg_filter", self.value, getattr(condition, "value", condition)))

    def build(self):
        return _DFExpr(("agg_build", self.value))


class _DFCaseBuilder:
    def __init__(self, cond):
        self.cond = cond
        self.pairs = []

    def when(self, cond, value):
        self.pairs.append((cond, value))
        return self

    def end(self):
        return _DFExpr(("case_end", self.cond, len(self.pairs)))

    def otherwise(self, value):
        return _DFExpr(("case_otherwise", self.cond, len(self.pairs), getattr(value, "value", value)))


class _DFFunctions:
    def avg(self, x):
        return _DFAggExpr(("avg", getattr(x, "value", x)))

    def sum(self, x):
        return _DFAggExpr(("sum", getattr(x, "value", x)))

    def count(self, x=None):
        return _DFAggExpr(("count", getattr(x, "value", x)))

    def min(self, x):
        return _DFAggExpr(("min", getattr(x, "value", x)))

    def max(self, x):
        return _DFAggExpr(("max", getattr(x, "value", x)))

    def rank(self):
        return _DFExpr("rank")

    def dense_rank(self):
        return _DFExpr("dense_rank")

    def row_number(self):
        return _DFExpr("row_number")

    def coalesce(self, *exprs):
        return _DFExpr(("coalesce", tuple(getattr(e, "value", e) for e in exprs)))

    def case(self, cond):
        return _DFCaseBuilder(cond)

    def when(self, cond, value):
        return _DFCaseBuilder(cond).when(cond, value)


class _DFWindow:
    __module__ = "datafusion.expr"

    def __init__(self, partition_by=None, order_by=None, window_frame=None):
        self.partition_by = partition_by or []
        self.order_by = order_by or []
        self.window_frame = window_frame


class _DFWindowFrame:
    __module__ = "datafusion.expr"

    def __init__(self, frame_type, start_bound, end_bound):
        self.frame_type = frame_type
        self.start_bound = start_bound
        self.end_bound = end_bound


class _DFSortExpr(_DFExpr):
    __module__ = "datafusion.expr"


class _DField:
    def __init__(self, name):
        self.name = name


class _FakeDataFusionDF:
    __module__ = "datafusion.dataframe"

    def __init__(self, columns, batches=None):
        self._columns = list(columns)
        self._batches = list(batches or [])
        self.ops = []

    @property
    def columns(self):
        return list(self._columns)

    def schema(self):
        return [_DField(c) for c in self._columns]

    def with_column(self, name, expr):
        if name not in self._columns:
            self._columns.append(name)
        self.ops.append(("with_column", name, getattr(expr, "value", expr)))
        return self

    def with_columns(self, *exprs):
        self.ops.append(("with_columns", len(exprs)))
        return self

    def with_column_renamed(self, old, new):
        if old in self._columns:
            self._columns[self._columns.index(old)] = new
        self.ops.append(("rename", old, new))
        return self

    def join(self, other, left_on=None, right_on=None, how="inner"):
        merged = list(dict.fromkeys(self._columns + list(other.columns)))
        out = _FakeDataFusionDF(merged, batches=self._batches or other._batches)
        out.ops = self.ops + [("join", tuple(left_on or []), tuple(right_on or []), how)] + other.ops
        return out

    def drop(self, *names):
        for name in names:
            if name in self._columns:
                self._columns.remove(name)
        self.ops.append(("drop", names))
        return self

    def select(self, *exprs):
        self.ops.append(("select", len(exprs)))
        return self

    def aggregate(self, group_cols, exprs):
        agg_cols = [getattr(e, "value", f"agg_{i}") for i, e in enumerate(exprs)]
        out_cols = [str(c) for c in group_cols] + [str(c) for c in agg_cols]
        out = _FakeDataFusionDF(out_cols, batches=self._batches)
        out.ops = self.ops + [("aggregate", len(group_cols), len(exprs))]
        return out

    def select_columns(self, *cols):
        out = _FakeDataFusionDF(list(cols), batches=self._batches)
        out.ops = self.ops + [("select_columns", cols)]
        return out

    def distinct(self):
        self.ops.append(("distinct",))
        return self

    def sort(self, *exprs):
        self.ops.append(("sort", len(exprs)))
        return self

    def filter(self, cond):
        self.ops.append(("filter", getattr(cond, "value", cond)))
        return self

    def limit(self, n):
        self.ops.append(("limit", n))
        return self

    def collect(self):
        return list(self._batches)


class _DefaultStrOps:
    def __init__(self, expr):
        self.expr = expr

    def starts_with(self, prefix):
        return _DefaultNativeExpr(("starts_with", self.expr.value, prefix))

    def ends_with(self, suffix):
        return _DefaultNativeExpr(("ends_with", self.expr.value, suffix))

    def contains(self, pattern):
        return _DefaultNativeExpr(("contains", self.expr.value, pattern))

    def slice(self, offset, length):
        return _DefaultNativeExpr(("slice", self.expr.value, offset, length))

    def to_uppercase(self):
        return _DefaultNativeExpr(("upper", self.expr.value))

    def to_lowercase(self):
        return _DefaultNativeExpr(("lower", self.expr.value))


class _DefaultDtOps:
    def __init__(self, expr):
        self.expr = expr

    def year(self):
        return _DefaultNativeExpr(("year", self.expr.value))

    def month(self):
        return _DefaultNativeExpr(("month", self.expr.value))

    def day(self):
        return _DefaultNativeExpr(("day", self.expr.value))

    def hour(self):
        return _DefaultNativeExpr(("hour", self.expr.value))

    def minute(self):
        return _DefaultNativeExpr(("minute", self.expr.value))

    def weekday(self):
        return _DefaultNativeExpr(("weekday", self.expr.value))

    def truncate(self, every):
        return _DefaultNativeExpr(("truncate", self.expr.value, every))

    def total_seconds(self):
        return _DefaultNativeExpr(("total_seconds", self.expr.value))


class _DefaultNativeExpr:
    def __init__(self, value):
        self.value = value
        self.str = _DefaultStrOps(self)
        self.dt = _DefaultDtOps(self)

    def __add__(self, other):
        return _DefaultNativeExpr(("add", self.value, getattr(other, "value", other)))

    def __sub__(self, other):
        return _DefaultNativeExpr(("sub", self.value, getattr(other, "value", other)))

    def __mul__(self, other):
        return _DefaultNativeExpr(("mul", self.value, getattr(other, "value", other)))

    def __truediv__(self, other):
        return _DefaultNativeExpr(("div", self.value, getattr(other, "value", other)))

    def __rtruediv__(self, other):
        return _DefaultNativeExpr(("rdiv", getattr(other, "value", other), self.value))

    def __eq__(self, other):
        return _DefaultNativeExpr(("eq", self.value, getattr(other, "value", other)))

    def __ne__(self, other):
        return _DefaultNativeExpr(("ne", self.value, getattr(other, "value", other)))

    def __lt__(self, other):
        return _DefaultNativeExpr(("lt", self.value, getattr(other, "value", other)))

    def __le__(self, other):
        return _DefaultNativeExpr(("le", self.value, getattr(other, "value", other)))

    def __gt__(self, other):
        return _DefaultNativeExpr(("gt", self.value, getattr(other, "value", other)))

    def __ge__(self, other):
        return _DefaultNativeExpr(("ge", self.value, getattr(other, "value", other)))

    def __and__(self, other):
        return _DefaultNativeExpr(("and", self.value, getattr(other, "value", other)))

    def __or__(self, other):
        return _DefaultNativeExpr(("or", self.value, getattr(other, "value", other)))

    def __invert__(self):
        return _DefaultNativeExpr(("not", self.value))

    def sum(self):
        return _DefaultNativeExpr(("sum", self.value))

    def mean(self):
        return _DefaultNativeExpr(("mean", self.value))

    def count(self):
        return _DefaultNativeExpr(("count", self.value))

    def min(self):
        return _DefaultNativeExpr(("min", self.value))

    def max(self):
        return _DefaultNativeExpr(("max", self.value))

    def first(self):
        return _DefaultNativeExpr(("first", self.value))

    def last(self):
        return _DefaultNativeExpr(("last", self.value))

    def std(self):
        return _DefaultNativeExpr(("std", self.value))

    def quantile(self, q, interpolation="nearest"):
        return _DefaultNativeExpr(("quantile", self.value, q, interpolation))

    def alias(self, name):
        return _DefaultNativeExpr(("alias", self.value, name))

    def cast(self, dtype):
        return _DefaultNativeExpr(("cast", self.value, str(dtype)))

    def round(self, decimals):
        return _DefaultNativeExpr(("round", self.value, decimals))

    def floor(self):
        return _DefaultNativeExpr(("floor", self.value))

    def abs(self):
        return _DefaultNativeExpr(("abs", self.value))

    def is_in(self, values):
        return _DefaultNativeExpr(("is_in", self.value, tuple(values)))

    def drop_nulls(self):
        return _DefaultNativeExpr(("drop_nulls", self.value))

    def n_unique(self):
        return _DefaultNativeExpr(("n_unique", self.value))

    def filter(self, cond):
        return _DefaultNativeExpr(("filter", self.value, getattr(cond, "value", cond)))

    def is_null(self):
        return _DefaultNativeExpr(("is_null", self.value))

    def is_not_null(self):
        return _DefaultNativeExpr(("is_not_null", self.value))

    def fill_null(self, value):
        return _DefaultNativeExpr(("fill_null", self.value, getattr(value, "value", value)))

    def is_between(self, low, high):
        return _DefaultNativeExpr(("between", self.value, getattr(low, "value", low), getattr(high, "value", high)))

    def rank(self, method="min", descending=False):
        return _DefaultNativeExpr(("rank", self.value, method, descending))

    def over(self, partition_cols):
        return _DefaultNativeExpr(("over", self.value, tuple(partition_cols)))

    def cum_sum(self):
        return _DefaultNativeExpr(("cum_sum", self.value))

    def cum_max(self):
        return _DefaultNativeExpr(("cum_max", self.value))

    def cum_min(self):
        return _DefaultNativeExpr(("cum_min", self.value))

    def to_uppercase(self):
        return _DefaultNativeExpr(("upper", self.value))

    def to_lowercase(self):
        return _DefaultNativeExpr(("lower", self.value))


def _install_rich_fake_datafusion(monkeypatch):
    fake_f = _DFFunctions()
    fake_df = SimpleNamespace(
        col=lambda name: _DFExpr(("col", name)),
        lit=lambda v: _DFExpr(("lit", v)),
        functions=fake_f,
    )
    fake_expr_mod = SimpleNamespace(Window=_DFWindow, WindowFrame=_DFWindowFrame, SortExpr=_DFSortExpr)
    monkeypatch.setitem(sys.modules, "datafusion", fake_df)
    monkeypatch.setitem(sys.modules, "datafusion.expr", fake_expr_mod)


def test_wrap_expr_and_backend_detector_helpers():
    wrapped = uf.wrap_expr("abc")
    assert isinstance(wrapped, uf.UnifiedExpr)
    assert uf.wrap_expr(wrapped) is wrapped

    PySparkType = type("PySparkType", (), {"__module__": "pyspark.sql.dataframe"})
    PolarsType = type("PolarsType", (), {"__module__": "polars.lazyframe.frame"})
    DataFusionType = type("DataFusionType", (), {"__module__": "datafusion.dataframe"})

    assert uf._is_pyspark_df(PySparkType())
    assert uf._is_polars_df(PolarsType())
    assert uf._is_datafusion_df(DataFusionType())


def test_unified_expr_default_branches_cover_core_methods():
    expr = uf.UnifiedExpr(_DefaultNativeExpr("base"))
    other = uf.UnifiedExpr(_DefaultNativeExpr("other"))

    assert isinstance(expr + other, uf.UnifiedExpr)
    assert isinstance(expr - 1, uf.UnifiedExpr)
    assert isinstance(expr * 2, uf.UnifiedExpr)
    assert isinstance(expr / 3, uf.UnifiedExpr)
    assert isinstance(3 / expr, uf.UnifiedExpr)
    assert isinstance(expr == other, uf.UnifiedExpr)
    assert isinstance(expr != other, uf.UnifiedExpr)
    assert isinstance(expr < other, uf.UnifiedExpr)
    assert isinstance(expr <= other, uf.UnifiedExpr)
    assert isinstance(expr > other, uf.UnifiedExpr)
    assert isinstance(expr >= other, uf.UnifiedExpr)
    assert isinstance(expr & other, uf.UnifiedExpr)
    assert isinstance(expr | other, uf.UnifiedExpr)
    assert isinstance(~expr, uf.UnifiedExpr)

    assert isinstance(expr.sum(), uf.UnifiedExpr)
    assert isinstance(expr.mean(), uf.UnifiedExpr)
    assert isinstance(expr.avg(), uf.UnifiedExpr)
    assert isinstance(expr.count(), uf.UnifiedExpr)
    assert isinstance(expr.min(), uf.UnifiedExpr)
    assert isinstance(expr.max(), uf.UnifiedExpr)
    assert isinstance(expr.first(), uf.UnifiedExpr)
    assert isinstance(expr.last(), uf.UnifiedExpr)
    assert isinstance(expr.std(), uf.UnifiedExpr)
    assert isinstance(expr.quantile(0.9), uf.UnifiedExpr)
    assert isinstance(expr.alias("x"), uf.UnifiedExpr)
    assert isinstance(expr.cast("int"), uf.UnifiedExpr)
    assert isinstance(expr.round(2), uf.UnifiedExpr)
    assert isinstance(expr.floor(), uf.UnifiedExpr)
    assert isinstance(expr.abs(), uf.UnifiedExpr)
    assert isinstance(expr.is_in([1, 2]), uf.UnifiedExpr)
    assert isinstance(expr.n_unique(), uf.UnifiedExpr)
    assert isinstance(expr.filter(expr > 1), uf.UnifiedExpr)
    assert isinstance(expr.is_null(), uf.UnifiedExpr)
    assert isinstance(expr.is_not_null(), uf.UnifiedExpr)
    assert isinstance(expr.fill_null(0), uf.UnifiedExpr)
    assert isinstance(expr.is_between(1, 10), uf.UnifiedExpr)
    assert isinstance(expr.rank("dense", descending=True), uf.UnifiedExpr)
    assert isinstance(expr.over(["k1", "k2"]), uf.UnifiedExpr)
    assert isinstance(expr.cum_sum(), uf.UnifiedExpr)
    assert isinstance(expr.cum_max(), uf.UnifiedExpr)
    assert isinstance(expr.cum_min(), uf.UnifiedExpr)

    assert isinstance(expr.str.starts_with("a"), uf.UnifiedExpr)
    assert isinstance(expr.str.ends_with("z"), uf.UnifiedExpr)
    assert isinstance(expr.str.contains("x"), uf.UnifiedExpr)
    assert isinstance(expr.str.slice(1, 2), uf.UnifiedExpr)
    assert isinstance(expr.str.to_uppercase(), uf.UnifiedExpr)
    assert isinstance(expr.str.to_lowercase(), uf.UnifiedExpr)

    assert isinstance(expr.dt.year(), uf.UnifiedExpr)
    assert isinstance(expr.dt.month(), uf.UnifiedExpr)
    assert isinstance(expr.dt.day(), uf.UnifiedExpr)
    assert isinstance(expr.dt.hour(), uf.UnifiedExpr)
    assert isinstance(expr.dt.minute(), uf.UnifiedExpr)
    assert isinstance(expr.dt.weekday(), uf.UnifiedExpr)
    assert isinstance(expr.dt.truncate("1d"), uf.UnifiedExpr)
    assert isinstance(expr.dt.total_seconds(), uf.UnifiedExpr)


def test_datafusion_deferred_rank_filter_and_when_paths(monkeypatch):
    _install_rich_fake_datafusion(monkeypatch)

    ranked = uf._DataFusionDeferredRank(_DFExpr("metric"), method="dense", descending=True).over(["grp"])
    assert isinstance(ranked, uf.UnifiedExpr)

    deferred = uf._DataFusionDeferredFilter(_DFExpr("value"), _DFExpr("cond"))
    assert isinstance(deferred.sum(), uf.UnifiedExpr)
    assert isinstance(deferred.count(), uf.UnifiedExpr)
    assert isinstance(deferred.mean(), uf.UnifiedExpr)
    assert isinstance(deferred.avg(), uf.UnifiedExpr)
    assert isinstance(deferred.min(), uf.UnifiedExpr)
    assert isinstance(deferred.max(), uf.UnifiedExpr)

    single = uf.UnifiedWhen(_DFExpr("c1"), platform="DataFusion").then(10).otherwise(0)
    assert isinstance(single, uf.UnifiedExpr)

    chained = uf.UnifiedWhen(_DFExpr("c1"), platform="DataFusion").then(10).when(_DFExpr("c2")).then(20).otherwise(0)
    assert isinstance(chained, uf.UnifiedExpr)


def test_unified_lazyframe_datafusion_paths(monkeypatch):
    _install_rich_fake_datafusion(monkeypatch)

    left = _FakeDataFusionDF(["id", "k", "value"])
    right = _FakeDataFusionDF(["id", "k", "value", "other"])
    frame = uf.UnifiedLazyFrame(left, adapter=SimpleNamespace())

    assert frame.columns == ["id", "k", "value"]

    cross = frame.join(right, how="cross")
    assert isinstance(cross, uf.UnifiedLazyFrame)

    regular = frame.join(right, on="id", how="inner", suffix="_r")
    assert isinstance(regular, uf.UnifiedLazyFrame)

    expr_join = frame.join(
        right,
        left_on=[uf.UnifiedExpr(_DFExpr("left_expr")), "id"],
        right_on=[uf.UnifiedExpr(_DFExpr("right_expr")), "id"],
        how="inner",
    )
    assert isinstance(expr_join, uf.UnifiedLazyFrame)

    grouped = frame.group_by(["id", "k"])
    agg = grouped.agg(uf.UnifiedExpr(_DFExpr("sum(value)")))
    assert isinstance(agg, uf.UnifiedLazyFrame)

    selected = frame.select("id", uf.UnifiedExpr(_DFExpr("SUM(value)")))
    assert isinstance(selected, uf.UnifiedLazyFrame)
    assert frame._has_aggregate_expr([_DFExpr("sum(x)")]) is True

    with_cols = frame.with_columns(uf.UnifiedExpr(_DFExpr("new_col")))
    assert isinstance(with_cols, uf.UnifiedLazyFrame)

    assert isinstance(frame.sum(), uf.UnifiedLazyFrame)
    assert isinstance(frame.mean(), uf.UnifiedLazyFrame)
    assert isinstance(frame.unique("id"), uf.UnifiedLazyFrame)
    assert isinstance(frame.unique(["id", "k"]), uf.UnifiedLazyFrame)
    assert isinstance(frame.distinct(), uf.UnifiedLazyFrame)
    assert isinstance(frame.sort([("id", "desc"), ("k", "asc")], nulls_last=True), uf.UnifiedLazyFrame)
    assert isinstance(frame.limit(2), uf.UnifiedLazyFrame)
    assert isinstance(frame.head(1), uf.UnifiedLazyFrame)
    assert isinstance(frame.rename({"id": "id_new"}), uf.UnifiedLazyFrame)
    assert isinstance(frame.drop("id_new"), uf.UnifiedLazyFrame)


def test_unified_lazyframe_datafusion_collect_helpers(monkeypatch):
    _install_rich_fake_datafusion(monkeypatch)
    pa = pytest.importorskip("pyarrow")

    batch = pa.record_batch([pa.array([1, 2]), pa.array(["a", "b"])], names=["x", "y"])
    with_rows = _FakeDataFusionDF(["x", "y"], batches=[batch])
    no_rows = _FakeDataFusionDF(["x", "y"], batches=[])

    frame_rows = uf.UnifiedLazyFrame(with_rows, adapter=SimpleNamespace())
    frame_empty = uf.UnifiedLazyFrame(no_rows, adapter=SimpleNamespace())

    collected = frame_rows.collect()
    assert hasattr(collected, "to_pandas")
    assert frame_rows.collect_column_as_list("x") == [1, 2]
    assert frame_empty.collect_column_as_list("x") == []
    assert frame_rows.scalar(1, 0) == 2
    assert frame_empty.scalar() is None
