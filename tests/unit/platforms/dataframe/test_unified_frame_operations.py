# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import operator
from unittest.mock import MagicMock

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _get_unified_expr():
    from benchbox.platforms.dataframe.unified_frame import UnifiedExpr

    return UnifiedExpr


def _get_unified_lazy_frame():
    from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame

    return UnifiedLazyFrame


def _create_mock_adapter():
    mock_adapter = MagicMock()
    mock_adapter.platform_name = "Polars"
    return mock_adapter


@pytest.fixture
def polars_frame():
    pl = pytest.importorskip("polars")
    ULF = _get_unified_lazy_frame()
    adapter = _create_mock_adapter()

    df = pl.DataFrame(
        {
            "category": ["A", "A", "B", "B", "C"],
            "value": [10, 20, 30, 40, 50],
            "score": [1.5, 2.5, 3.5, 4.5, 5.5],
            "label": ["x", "y", "x", "y", "x"],
        }
    ).lazy()
    return {"pl": pl, "df": ULF(df, adapter)}


class TestDropColumns:
    def test_drop_single_column(self, polars_frame):
        df = polars_frame["df"]
        result = df.drop("score")
        collected = result.collect()
        assert "score" not in collected.columns
        assert sorted(collected.columns) == ["category", "label", "value"]

    def test_drop_multiple_columns(self, polars_frame):
        df = polars_frame["df"]
        result = df.drop("score", "label")
        collected = result.collect()
        assert sorted(collected.columns) == ["category", "value"]

    def test_drop_preserves_row_count(self, polars_frame):
        df = polars_frame["df"]
        result = df.drop("score", "label")
        collected = result.collect()
        assert len(collected) == 5

    def test_drop_preserves_remaining_data(self, polars_frame):
        df = polars_frame["df"]
        result = df.drop("score", "label")
        collected = result.collect()
        assert collected["value"].to_list() == [10, 20, 30, 40, 50]
        assert collected["category"].to_list() == ["A", "A", "B", "B", "C"]

    def test_drop_returns_unified_lazy_frame(self, polars_frame):
        ULF = _get_unified_lazy_frame()
        df = polars_frame["df"]
        result = df.drop("score")
        assert isinstance(result, ULF)


class TestCollectColumnAsList:
    def test_collect_string_column(self, polars_frame):
        df = polars_frame["df"]
        result = df.collect_column_as_list("category")
        assert result == ["A", "A", "B", "B", "C"]

    def test_collect_int_column(self, polars_frame):
        df = polars_frame["df"]
        result = df.collect_column_as_list("value")
        assert result == [10, 20, 30, 40, 50]

    def test_collect_float_column(self, polars_frame):
        df = polars_frame["df"]
        result = df.collect_column_as_list("score")
        assert result == [1.5, 2.5, 3.5, 4.5, 5.5]

    def test_collect_column_returns_python_list(self, polars_frame):
        df = polars_frame["df"]
        result = df.collect_column_as_list("value")
        assert type(result) is list


class TestWindowFunctionsOver:
    def test_sum_over_partition(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        UnifExpr = _get_unified_expr()
        adapter = _create_mock_adapter()

        df = pl.DataFrame({"grp": ["A", "A", "B", "B"], "val": [1, 2, 10, 20]}).lazy()
        udf = ULF(df, adapter)

        expr = UnifExpr(pl.col("val")).sum().over("grp")
        result = udf.with_columns(expr.alias("grp_sum")).collect()

        assert result["grp_sum"].to_list() == [3, 3, 30, 30]

    def test_mean_over_partition(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        UnifExpr = _get_unified_expr()
        adapter = _create_mock_adapter()

        df = pl.DataFrame({"grp": ["A", "A", "B", "B"], "val": [2.0, 4.0, 10.0, 30.0]}).lazy()
        udf = ULF(df, adapter)

        expr = UnifExpr(pl.col("val")).mean().over("grp")
        result = udf.with_columns(expr.alias("grp_mean")).collect()

        assert result["grp_mean"].to_list() == [3.0, 3.0, 20.0, 20.0]

    def test_count_over_partition(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        UnifExpr = _get_unified_expr()
        adapter = _create_mock_adapter()

        df = pl.DataFrame({"grp": ["X", "X", "X", "Y", "Y"], "val": [1, 2, 3, 4, 5]}).lazy()
        udf = ULF(df, adapter)

        expr = UnifExpr(pl.col("val")).count().over("grp")
        result = udf.with_columns(expr.alias("grp_count")).collect()

        assert result["grp_count"].to_list() == [3, 3, 3, 2, 2]

    def test_over_with_list_partition(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        UnifExpr = _get_unified_expr()
        adapter = _create_mock_adapter()

        df = pl.DataFrame(
            {
                "grp1": ["A", "A", "A", "B"],
                "grp2": ["x", "x", "y", "x"],
                "val": [1, 2, 3, 4],
            }
        ).lazy()
        udf = ULF(df, adapter)

        expr = UnifExpr(pl.col("val")).sum().over(["grp1", "grp2"])
        result = udf.with_columns(expr.alias("grp_sum")).collect()

        assert result["grp_sum"].to_list() == [3, 3, 3, 4]


class TestSortBy:
    def test_sort_by_ascending(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        UnifExpr = _get_unified_expr()
        adapter = _create_mock_adapter()

        df = pl.DataFrame(
            {
                "grp": ["A", "A", "A", "B", "B"],
                "order_col": [3, 1, 2, 2, 1],
                "val": ["c", "a", "b", "y", "x"],
            }
        ).lazy()
        udf = ULF(df, adapter)

        result = (
            udf.group_by("grp")
            .agg(UnifExpr(pl.col("val")).sort_by("order_col", descending=False).first().alias("first_val"))
            .sort("grp")
            .collect()
        )

        assert result["first_val"].to_list() == ["a", "x"]

    def test_sort_by_descending(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        UnifExpr = _get_unified_expr()
        adapter = _create_mock_adapter()

        df = pl.DataFrame(
            {
                "grp": ["A", "A", "A"],
                "order_col": [1, 2, 3],
                "val": ["first", "second", "third"],
            }
        ).lazy()
        udf = ULF(df, adapter)

        result = (
            udf.group_by("grp")
            .agg(UnifExpr(pl.col("val")).sort_by("order_col", descending=True).first().alias("first_val"))
            .collect()
        )

        assert result["first_val"].to_list() == ["third"]


class TestCastGeneric:
    def test_cast_with_polars_float64(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        UnifExpr = _get_unified_expr()
        adapter = _create_mock_adapter()

        df = pl.DataFrame({"val": [1, 2, 3]}).lazy()
        udf = ULF(df, adapter)

        expr = UnifExpr(pl.col("val")).cast(pl.Float64)
        result = udf.with_columns(expr.alias("val_f")).collect()

        assert result["val_f"].to_list() == [1.0, 2.0, 3.0]
        assert result["val_f"].dtype == pl.Float64

    def test_cast_with_polars_utf8(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        UnifExpr = _get_unified_expr()
        adapter = _create_mock_adapter()

        df = pl.DataFrame({"val": [10, 20, 30]}).lazy()
        udf = ULF(df, adapter)

        expr = UnifExpr(pl.col("val")).cast(pl.Utf8)
        result = udf.with_columns(expr.alias("val_s")).collect()

        assert result["val_s"].to_list() == ["10", "20", "30"]

    def test_cast_with_polars_int32(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        UnifExpr = _get_unified_expr()
        adapter = _create_mock_adapter()

        df = pl.DataFrame({"val": [100, 200, 300]}).lazy()
        udf = ULF(df, adapter)

        expr = UnifExpr(pl.col("val")).cast(pl.Int32)
        result = udf.with_columns(expr.alias("val_i32")).collect()

        assert result["val_i32"].to_list() == [100, 200, 300]
        assert result["val_i32"].dtype == pl.Int32


class TestCastConvenience:
    def test_cast_float_alias(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        UnifExpr = _get_unified_expr()
        adapter = _create_mock_adapter()

        df = pl.DataFrame({"val": [1, 2, 3]}).lazy()
        udf = ULF(df, adapter)

        expr = UnifExpr(pl.col("val")).cast_float()
        result = udf.with_columns(expr.alias("val_f")).collect()

        assert result["val_f"].to_list() == [1.0, 2.0, 3.0]
        assert result["val_f"].dtype == pl.Float64

    def test_cast_int_alias(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        UnifExpr = _get_unified_expr()
        adapter = _create_mock_adapter()

        df = pl.DataFrame({"val": [1.9, 2.1, 3.7]}).lazy()
        udf = ULF(df, adapter)

        expr = UnifExpr(pl.col("val")).cast_int()
        result = udf.with_columns(expr.alias("val_i")).collect()

        assert result["val_i"].to_list() == [1, 2, 3]
        assert result["val_i"].dtype == pl.Int32

    def test_cast_int64(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        UnifExpr = _get_unified_expr()
        adapter = _create_mock_adapter()

        df = pl.DataFrame({"val": [1.0, 2.0, 3.0]}).lazy()
        udf = ULF(df, adapter)

        expr = UnifExpr(pl.col("val")).cast_int64()
        result = udf.with_columns(expr.alias("val_i64")).collect()

        assert result["val_i64"].to_list() == [1, 2, 3]
        assert result["val_i64"].dtype == pl.Int64

    def test_cast_float64(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        UnifExpr = _get_unified_expr()
        adapter = _create_mock_adapter()

        df = pl.DataFrame({"val": [7, 8, 9]}).lazy()
        udf = ULF(df, adapter)

        expr = UnifExpr(pl.col("val")).cast_float64()
        result = udf.with_columns(expr.alias("val_f64")).collect()

        assert result["val_f64"].to_list() == [7.0, 8.0, 9.0]
        assert result["val_f64"].dtype == pl.Float64

    def test_cast_int32(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        UnifExpr = _get_unified_expr()
        adapter = _create_mock_adapter()

        df = pl.DataFrame({"val": [100, 200, 300]}).lazy()
        udf = ULF(df, adapter)

        expr = UnifExpr(pl.col("val")).cast_int32()
        result = udf.with_columns(expr.alias("val_i32")).collect()

        assert result["val_i32"].to_list() == [100, 200, 300]
        assert result["val_i32"].dtype == pl.Int32


class TestFilteredAggregation:
    def test_filter_sum(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        UnifExpr = _get_unified_expr()
        adapter = _create_mock_adapter()

        df = pl.DataFrame(
            {
                "category": ["A", "A", "B", "B"],
                "revenue": [100, 200, 300, 400],
            }
        ).lazy()
        udf = ULF(df, adapter)

        cond = UnifExpr(pl.col("category")) == "A"
        expr = UnifExpr(pl.col("revenue")).filter(cond).sum().alias("a_revenue")
        result = udf.select(expr).collect()

        assert result["a_revenue"].to_list() == [300]

    def test_filter_count(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        UnifExpr = _get_unified_expr()
        adapter = _create_mock_adapter()

        df = pl.DataFrame(
            {
                "status": ["ok", "fail", "ok", "ok", "fail"],
                "val": [1, 2, 3, 4, 5],
            }
        ).lazy()
        udf = ULF(df, adapter)

        cond = UnifExpr(pl.col("status")) == "ok"
        expr = UnifExpr(pl.col("val")).filter(cond).count().alias("ok_count")
        result = udf.select(expr).collect()

        assert result["ok_count"].to_list() == [3]

    def test_filter_mean(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        UnifExpr = _get_unified_expr()
        adapter = _create_mock_adapter()

        df = pl.DataFrame(
            {
                "flag": [True, False, True, False],
                "val": [10.0, 100.0, 30.0, 200.0],
            }
        ).lazy()
        udf = ULF(df, adapter)

        cond = operator.eq(UnifExpr(pl.col("flag")), True)
        expr = UnifExpr(pl.col("val")).filter(cond).mean().alias("flagged_mean")
        result = udf.select(expr).collect()

        assert result["flagged_mean"].to_list() == [20.0]


class TestFrameLevelAggregations:
    def test_frame_sum(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()

        df = pl.DataFrame({"a": [1, 2, 3], "b": [10, 20, 30]}).lazy()
        udf = ULF(df, adapter)

        result = udf.sum().collect()

        assert result["a"].to_list() == [6]
        assert result["b"].to_list() == [60]

    def test_frame_mean(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()

        df = pl.DataFrame({"a": [2.0, 4.0, 6.0], "b": [10.0, 20.0, 30.0]}).lazy()
        udf = ULF(df, adapter)

        result = udf.mean().collect()

        assert result["a"].to_list() == [4.0]
        assert result["b"].to_list() == [20.0]

    def test_frame_sum_returns_unified_lazy_frame(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()

        df = pl.DataFrame({"a": [1, 2]}).lazy()
        udf = ULF(df, adapter)

        result = udf.sum()
        assert isinstance(result, ULF)

    def test_frame_mean_returns_unified_lazy_frame(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()

        df = pl.DataFrame({"a": [1.0, 2.0]}).lazy()
        udf = ULF(df, adapter)

        result = udf.mean()
        assert isinstance(result, ULF)


class TestMultiColumnSort:
    def test_sort_two_columns_default_ascending(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()

        df = pl.DataFrame(
            {
                "grp": ["B", "A", "A", "B"],
                "val": [2, 1, 2, 1],
            }
        ).lazy()
        udf = ULF(df, adapter)

        result = udf.sort(["grp", "val"]).collect()

        assert result["grp"].to_list() == ["A", "A", "B", "B"]
        assert result["val"].to_list() == [1, 2, 1, 2]

    def test_sort_mixed_descending_flags(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()

        df = pl.DataFrame(
            {
                "grp": ["A", "A", "B", "B"],
                "val": [1, 2, 3, 4],
            }
        ).lazy()
        udf = ULF(df, adapter)

        result = udf.sort(["grp", "val"], descending=[False, True]).collect()

        assert result["grp"].to_list() == ["A", "A", "B", "B"]
        assert result["val"].to_list() == [2, 1, 4, 3]

    def test_sort_tuple_syntax(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()

        df = pl.DataFrame(
            {
                "x": ["A", "B", "A", "B"],
                "y": [4, 3, 2, 1],
            }
        ).lazy()
        udf = ULF(df, adapter)

        result = udf.sort([("x", "asc"), ("y", "desc")]).collect()

        assert result["x"].to_list() == ["A", "A", "B", "B"]
        assert result["y"].to_list() == [4, 2, 3, 1]

    def test_sort_positional_columns(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()

        df = pl.DataFrame(
            {
                "a": ["B", "A", "A", "B"],
                "b": [2, 1, 2, 1],
            }
        ).lazy()
        udf = ULF(df, adapter)

        result = udf.sort("a", "b").collect()

        assert result["a"].to_list() == ["A", "A", "B", "B"]
        assert result["b"].to_list() == [1, 2, 1, 2]

    def test_sort_all_descending(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()

        df = pl.DataFrame(
            {
                "a": ["A", "B", "A", "B"],
                "b": [1, 2, 3, 4],
            }
        ).lazy()
        udf = ULF(df, adapter)

        result = udf.sort(["a", "b"], descending=True).collect()

        assert result["a"].to_list() == ["B", "B", "A", "A"]
        assert result["b"].to_list() == [4, 2, 3, 1]


class TestUniqueWithSubset:
    def test_unique_no_subset(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()

        df = pl.DataFrame(
            {
                "a": [1, 1, 2, 2],
                "b": [10, 10, 20, 30],
            }
        ).lazy()
        udf = ULF(df, adapter)

        result = udf.unique().sort("a", "b").collect()

        assert len(result) == 3
        assert result["a"].to_list() == [1, 2, 2]
        assert result["b"].to_list() == [10, 20, 30]

    def test_unique_with_single_subset_column(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()

        df = pl.DataFrame(
            {
                "a": [1, 1, 2, 2, 3],
                "b": [10, 20, 30, 40, 50],
            }
        ).lazy()
        udf = ULF(df, adapter)

        result = udf.unique(subset="a").sort("a").collect()

        assert result["a"].to_list() == [1, 2, 3]
        assert len(result) == 3

    def test_unique_with_list_subset(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()

        df = pl.DataFrame(
            {
                "a": [1, 1, 1, 2],
                "b": ["x", "x", "y", "x"],
                "c": [100, 200, 300, 400],
            }
        ).lazy()
        udf = ULF(df, adapter)

        result = udf.unique(subset=["a", "b"]).sort(["a", "b"]).collect()

        assert len(result) == 3
        assert result["a"].to_list() == [1, 1, 2]
        assert result["b"].to_list() == ["x", "y", "x"]

    def test_unique_returns_unified_lazy_frame(self):
        pl = pytest.importorskip("polars")
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()

        df = pl.DataFrame({"a": [1, 1, 2]}).lazy()
        udf = ULF(df, adapter)

        result = udf.unique(subset="a")
        assert isinstance(result, ULF)
