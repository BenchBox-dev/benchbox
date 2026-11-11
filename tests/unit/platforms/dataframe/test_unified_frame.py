# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.utilities.optional_engines import require_pyspark

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


class TestUnifiedExprStringConcat:
    @pytest.fixture
    def polars_context(self):
        polars = pytest.importorskip("polars")

        unified_lazy_frame_cls = _get_unified_lazy_frame()
        mock_adapter = _create_mock_adapter()

        df = polars.DataFrame({"name": ["Alice", "Bob"], "id": [1, 2]}).lazy()
        return {
            "polars": polars,
            "df": unified_lazy_frame_cls(df, mock_adapter),
        }

    def test_polars_string_concat_with_plus(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        prefix = unified_expr_cls(pl.lit("Hello, "), _is_string_literal=True)
        name_col = unified_expr_cls(pl.col("name"))

        result_expr = prefix + name_col

        result = df.with_columns(result_expr.alias("greeting")).collect()
        assert result["greeting"].to_list() == ["Hello, Alice", "Hello, Bob"]

    def test_polars_concat_str_method(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        name_col = unified_expr_cls(pl.col("name"))

        result_expr = name_col.concat_str(" - ", pl.lit("suffix"))

        result = df.with_columns(result_expr.alias("combined")).collect()
        assert result["combined"].to_list() == ["Alice - suffix", "Bob - suffix"]


class TestUnifiedExprBitwiseAnd:
    @pytest.fixture
    def polars_context(self):
        polars = pytest.importorskip("polars")

        unified_lazy_frame_cls = _get_unified_lazy_frame()
        mock_adapter = _create_mock_adapter()

        df = polars.DataFrame({"grouping_id": [0, 1, 2, 3, 7]}).lazy()
        return {
            "polars": polars,
            "df": unified_lazy_frame_cls(df, mock_adapter),
        }

    def test_polars_bitwise_and_with_int(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        gid_col = unified_expr_cls(pl.col("grouping_id"))
        bit0_expr = gid_col & 1

        result = df.with_columns(bit0_expr.alias("bit0")).collect()
        assert result["bit0"].to_list() == [0, 1, 0, 1, 1]

    def test_polars_bitwise_and_with_mask(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        gid_col = unified_expr_cls(pl.col("grouping_id"))
        bit1_expr = gid_col & 2

        result = df.with_columns(bit1_expr.alias("bit1")).collect()
        assert result["bit1"].to_list() == [0, 0, 2, 2, 2]


class TestUnifiedExprMathMethods:
    @pytest.fixture
    def polars_context(self):
        polars = pytest.importorskip("polars")

        unified_lazy_frame_cls = _get_unified_lazy_frame()
        mock_adapter = _create_mock_adapter()

        df = polars.DataFrame({"value": [1.234, -5.678, 9.999, -0.1]}).lazy()
        return {
            "polars": polars,
            "df": unified_lazy_frame_cls(df, mock_adapter),
        }

    def test_round_method(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        value_col = unified_expr_cls(pl.col("value"))
        rounded = value_col.round(2)

        result = df.with_columns(rounded.alias("rounded")).collect()
        assert result["rounded"].to_list() == [1.23, -5.68, 10.0, -0.1]

    def test_abs_method(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        value_col = unified_expr_cls(pl.col("value"))
        absolute = value_col.abs()

        result = df.with_columns(absolute.alias("abs_value")).collect()
        expected = [1.234, 5.678, 9.999, 0.1]
        actual = result["abs_value"].to_list()
        for exp, act in zip(expected, actual, strict=True):
            assert abs(exp - act) < 0.001


class TestUnifiedLazyFrameJoins:
    @pytest.fixture
    def polars_dfs(self):
        polars = pytest.importorskip("polars")

        unified_lazy_frame_cls = _get_unified_lazy_frame()
        mock_adapter = _create_mock_adapter()

        left = polars.DataFrame(
            {
                "id": [1, 2, 3],
                "name": ["Alice", "Bob", "Carol"],
            }
        ).lazy()

        right = polars.DataFrame(
            {
                "id": [2, 3, 4],
                "value": [100, 200, 300],
            }
        ).lazy()

        return {
            "polars": polars,
            "mock_adapter": mock_adapter,
            "left": unified_lazy_frame_cls(left, mock_adapter),
            "right": unified_lazy_frame_cls(right, mock_adapter),
        }

    def test_inner_join_on_column(self, polars_dfs):

        left = polars_dfs["left"]
        right = polars_dfs["right"]

        result = left.join(right, on="id", how="inner").collect()

        assert len(result) == 2
        assert set(result["id"].to_list()) == {2, 3}

    def test_left_join_with_suffix(self, polars_dfs):

        pl = polars_dfs["polars"]
        left = polars_dfs["left"]
        mock_adapter = polars_dfs["mock_adapter"]

        unified_lazy_frame_cls = _get_unified_lazy_frame()

        right = pl.DataFrame(
            {
                "id": [2, 3],
                "name": ["Bob2", "Carol2"],
            }
        ).lazy()

        right_ulf = unified_lazy_frame_cls(right, mock_adapter)

        result = left.join(right_ulf, on="id", how="left", suffix="_r").collect()

        assert "name" in result.columns
        assert "name_r" in result.columns

    def test_cross_join(self, polars_dfs):

        pl = polars_dfs["polars"]
        mock_adapter = polars_dfs["mock_adapter"]

        unified_lazy_frame_cls = _get_unified_lazy_frame()

        left = unified_lazy_frame_cls(pl.DataFrame({"a": [1, 2]}).lazy(), mock_adapter)
        right = unified_lazy_frame_cls(pl.DataFrame({"b": ["x", "y", "z"]}).lazy(), mock_adapter)

        result = left.join(right, how="cross").collect()

        assert len(result) == 6


class TestUnifiedLazyFrameWithColumns:
    @pytest.fixture
    def polars_df(self):
        polars = pytest.importorskip("polars")

        unified_lazy_frame_cls = _get_unified_lazy_frame()
        mock_adapter = _create_mock_adapter()

        df = polars.DataFrame({"x": [1, 2, 3], "y": [10, 20, 30]}).lazy()
        return {
            "polars": polars,
            "df": unified_lazy_frame_cls(df, mock_adapter),
        }

    def test_with_columns_adds_column(self, polars_df):

        pl = polars_df["polars"]
        df = polars_df["df"]
        unified_expr_cls = _get_unified_expr()

        z_expr = unified_expr_cls((pl.col("x") + pl.col("y")).alias("z"))
        result = df.with_columns(z_expr).collect()

        assert "z" in result.columns
        assert result["z"].to_list() == [11, 22, 33]

    def test_with_columns_replaces_existing(self, polars_df):

        pl = polars_df["polars"]
        df = polars_df["df"]
        unified_expr_cls = _get_unified_expr()

        x_doubled = unified_expr_cls((pl.col("x") * 2).alias("x"))
        result = df.with_columns(x_doubled).collect()

        assert result["x"].to_list() == [2, 4, 6]
        assert len(result.columns) == 2


class TestUnifiedStrExpr:
    @pytest.fixture
    def polars_context(self):
        polars = pytest.importorskip("polars")

        unified_lazy_frame_cls = _get_unified_lazy_frame()
        mock_adapter = _create_mock_adapter()

        df = polars.DataFrame({"text": ["Hello World", "hello", "WORLD", "  spaces  "]}).lazy()
        return {
            "polars": polars,
            "df": unified_lazy_frame_cls(df, mock_adapter),
        }

    def test_starts_with(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        text_col = unified_expr_cls(pl.col("text"))
        result_expr = text_col.str.starts_with("Hello")

        result = df.with_columns(result_expr.alias("starts")).collect()
        assert result["starts"].to_list() == [True, False, False, False]

    def test_ends_with(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        text_col = unified_expr_cls(pl.col("text"))
        result_expr = text_col.str.ends_with("World")

        result = df.with_columns(result_expr.alias("ends")).collect()
        assert result["ends"].to_list() == [True, False, False, False]

    def test_contains(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        text_col = unified_expr_cls(pl.col("text"))
        result_expr = text_col.str.contains("o")

        result = df.with_columns(result_expr.alias("has_o")).collect()
        assert result["has_o"].to_list() == [True, True, False, False]

    def test_contains_accepts_unified_literal(self, polars_context):
        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        text_col = unified_expr_cls(pl.col("text"))
        pattern = unified_expr_cls(pl.lit("World"))
        result_expr = text_col.str.contains(pattern)

        result = df.with_columns(result_expr.alias("has_world")).collect()
        assert result["has_world"].to_list() == [True, False, False, False]

    def test_string_literal_unwrap_returns_scalar_value(self):
        from benchbox.platforms.dataframe.unified_frame import _unwrap_unified_expr

        unified_expr_cls = _get_unified_expr()
        native_expr = object()

        wrapped = unified_expr_cls(native_expr, _is_string_literal=True, _literal_value="google")
        assert _unwrap_unified_expr(wrapped) == "google"

        column_expr = unified_expr_cls(native_expr)
        assert _unwrap_unified_expr(column_expr) is native_expr

    def test_slice(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        text_col = unified_expr_cls(pl.col("text"))
        result_expr = text_col.str.slice(0, 5)

        result = df.with_columns(result_expr.alias("sliced")).collect()
        assert result["sliced"].to_list() == ["Hello", "hello", "WORLD", "  spa"]


class TestUnifiedDtExpr:
    @pytest.fixture
    def polars_context(self):
        polars = pytest.importorskip("polars")
        from datetime import date

        unified_lazy_frame_cls = _get_unified_lazy_frame()
        mock_adapter = _create_mock_adapter()

        df = polars.DataFrame(
            {
                "date": [
                    date(2023, 1, 15),
                    date(2024, 6, 20),
                    date(2025, 12, 31),
                ]
            }
        ).lazy()
        return {
            "polars": polars,
            "df": unified_lazy_frame_cls(df, mock_adapter),
        }

    def test_year(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        date_col = unified_expr_cls(pl.col("date"))
        result_expr = date_col.dt.year()

        result = df.with_columns(result_expr.alias("year")).collect()
        assert result["year"].to_list() == [2023, 2024, 2025]

    def test_month(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        date_col = unified_expr_cls(pl.col("date"))
        result_expr = date_col.dt.month()

        result = df.with_columns(result_expr.alias("month")).collect()
        assert result["month"].to_list() == [1, 6, 12]

    def test_day(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        date_col = unified_expr_cls(pl.col("date"))
        result_expr = date_col.dt.day()

        result = df.with_columns(result_expr.alias("day")).collect()
        assert result["day"].to_list() == [15, 20, 31]


class TestUnifiedExprArithmetic:
    @pytest.fixture
    def polars_context(self):
        polars = pytest.importorskip("polars")

        unified_lazy_frame_cls = _get_unified_lazy_frame()
        mock_adapter = _create_mock_adapter()

        df = polars.DataFrame({"a": [10, 20, 30], "b": [2, 4, 5]}).lazy()
        return {
            "polars": polars,
            "df": unified_lazy_frame_cls(df, mock_adapter),
        }

    def test_subtraction(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        a = unified_expr_cls(pl.col("a"))
        b = unified_expr_cls(pl.col("b"))
        result = df.with_columns((a - b).alias("diff")).collect()
        assert result["diff"].to_list() == [8, 16, 25]

    def test_multiplication(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        a = unified_expr_cls(pl.col("a"))
        b = unified_expr_cls(pl.col("b"))
        result = df.with_columns((a * b).alias("prod")).collect()
        assert result["prod"].to_list() == [20, 80, 150]

    def test_division(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        a = unified_expr_cls(pl.col("a"))
        b = unified_expr_cls(pl.col("b"))
        result = df.with_columns((a / b).alias("quot")).collect()
        assert result["quot"].to_list() == [5.0, 5.0, 6.0]

    def test_reverse_subtraction(self, polars_context):
        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        a = unified_expr_cls(pl.col("a"))
        result = df.with_columns((100 - a).alias("rsub")).collect()
        assert result["rsub"].to_list() == [90, 80, 70]

    def test_reverse_multiplication(self, polars_context):
        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        a = unified_expr_cls(pl.col("a"))
        result = df.with_columns((3 * a).alias("rmul")).collect()
        assert result["rmul"].to_list() == [30, 60, 90]


class TestUnifiedExprComparison:
    @pytest.fixture
    def polars_context(self):
        polars = pytest.importorskip("polars")

        unified_lazy_frame_cls = _get_unified_lazy_frame()
        mock_adapter = _create_mock_adapter()

        df = polars.DataFrame({"x": [10, 20, 30]}).lazy()
        return {
            "polars": polars,
            "df": unified_lazy_frame_cls(df, mock_adapter),
        }

    def test_equal(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        x = unified_expr_cls(pl.col("x"))
        result = df.with_columns((x == 20).alias("eq")).collect()
        assert result["eq"].to_list() == [False, True, False]

    def test_not_equal(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        x = unified_expr_cls(pl.col("x"))
        result = df.with_columns((x != 20).alias("ne")).collect()
        assert result["ne"].to_list() == [True, False, True]

    def test_less_than(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        x = unified_expr_cls(pl.col("x"))
        result = df.with_columns((x < 20).alias("lt")).collect()
        assert result["lt"].to_list() == [True, False, False]

    def test_less_than_or_equal(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        x = unified_expr_cls(pl.col("x"))
        result = df.with_columns((x <= 20).alias("le")).collect()
        assert result["le"].to_list() == [True, True, False]

    def test_greater_than(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        x = unified_expr_cls(pl.col("x"))
        result = df.with_columns((x > 20).alias("gt")).collect()
        assert result["gt"].to_list() == [False, False, True]

    def test_greater_than_or_equal(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        x = unified_expr_cls(pl.col("x"))
        result = df.with_columns((x >= 20).alias("ge")).collect()
        assert result["ge"].to_list() == [False, True, True]


class TestUnifiedExprAggregations:
    @pytest.fixture
    def polars_context(self):
        polars = pytest.importorskip("polars")

        unified_lazy_frame_cls = _get_unified_lazy_frame()
        mock_adapter = _create_mock_adapter()

        df = polars.DataFrame({"group": ["A", "A", "B", "B"], "value": [10, 20, 30, 40]}).lazy()
        return {
            "polars": polars,
            "df": unified_lazy_frame_cls(df, mock_adapter),
        }

    def test_sum(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        value = unified_expr_cls(pl.col("value"))
        result = df.group_by("group").agg(value.sum().alias("total")).sort("group").collect()
        assert result["total"].to_list() == [30, 70]

    def test_mean(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        value = unified_expr_cls(pl.col("value"))
        result = df.group_by("group").agg(value.mean().alias("avg")).sort("group").collect()
        assert result["avg"].to_list() == [15.0, 35.0]

    def test_avg_alias(self, polars_context):
        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        value = unified_expr_cls(pl.col("value"))
        result = df.group_by("group").agg(value.avg().alias("avg")).sort("group").collect()
        assert result["avg"].to_list() == [15.0, 35.0]

    def test_count(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        value = unified_expr_cls(pl.col("value"))
        result = df.group_by("group").agg(value.count().alias("cnt")).sort("group").collect()
        assert result["cnt"].to_list() == [2, 2]

    def test_min(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        value = unified_expr_cls(pl.col("value"))
        result = df.group_by("group").agg(value.min().alias("minimum")).sort("group").collect()
        assert result["minimum"].to_list() == [10, 30]

    def test_max(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        value = unified_expr_cls(pl.col("value"))
        result = df.group_by("group").agg(value.max().alias("maximum")).sort("group").collect()
        assert result["maximum"].to_list() == [20, 40]

    def test_first(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        value = unified_expr_cls(pl.col("value"))
        result = df.group_by("group").agg(value.first().alias("first")).sort("group").collect()
        assert result["first"].to_list() == [10, 30]

    def test_last(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        value = unified_expr_cls(pl.col("value"))
        result = df.group_by("group").agg(value.last().alias("last")).sort("group").collect()
        assert result["last"].to_list() == [20, 40]


class TestUnifiedExprNullHandling:
    @pytest.fixture
    def polars_context(self):
        polars = pytest.importorskip("polars")

        unified_lazy_frame_cls = _get_unified_lazy_frame()
        mock_adapter = _create_mock_adapter()

        df = polars.DataFrame({"x": [1, None, 3, None, 5]}).lazy()
        return {
            "polars": polars,
            "df": unified_lazy_frame_cls(df, mock_adapter),
        }

    def test_is_null(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        x = unified_expr_cls(pl.col("x"))
        result = df.with_columns(x.is_null().alias("isnull")).collect()
        assert result["isnull"].to_list() == [False, True, False, True, False]

    def test_is_not_null(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        x = unified_expr_cls(pl.col("x"))
        result = df.with_columns(x.is_not_null().alias("isnotnull")).collect()
        assert result["isnotnull"].to_list() == [True, False, True, False, True]

    def test_fill_null(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        x = unified_expr_cls(pl.col("x"))
        result = df.with_columns(x.fill_null(0).alias("filled")).collect()
        assert result["filled"].to_list() == [1, 0, 3, 0, 5]


class TestUnifiedExprMembership:
    @pytest.fixture
    def polars_context(self):
        polars = pytest.importorskip("polars")

        unified_lazy_frame_cls = _get_unified_lazy_frame()
        mock_adapter = _create_mock_adapter()

        df = polars.DataFrame({"status": ["A", "B", "C", "D", "A"]}).lazy()
        return {
            "polars": polars,
            "df": unified_lazy_frame_cls(df, mock_adapter),
        }

    def test_is_in(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        status = unified_expr_cls(pl.col("status"))
        result = df.with_columns(status.is_in(["A", "B"]).alias("in_list")).collect()
        assert result["in_list"].to_list() == [True, True, False, False, True]

    def test_is_between(self, polars_context):

        polars = pytest.importorskip("polars")

        unified_lazy_frame_cls = _get_unified_lazy_frame()
        mock_adapter = _create_mock_adapter()

        df = polars.DataFrame({"value": [1, 5, 10, 15, 20]}).lazy()
        ulf = unified_lazy_frame_cls(df, mock_adapter)

        unified_expr_cls = _get_unified_expr()
        value = unified_expr_cls(polars.col("value"))
        result = ulf.with_columns(value.is_between(5, 15).alias("between")).collect()
        assert result["between"].to_list() == [False, True, True, True, False]


class TestUnifiedExprCasting:
    @pytest.fixture
    def polars_context(self):
        polars = pytest.importorskip("polars")

        unified_lazy_frame_cls = _get_unified_lazy_frame()
        mock_adapter = _create_mock_adapter()

        df = polars.DataFrame({"num": [1, 2, 3], "flt": [1.5, 2.5, 3.5]}).lazy()
        return {
            "polars": polars,
            "df": unified_lazy_frame_cls(df, mock_adapter),
        }

    def test_cast_float64(self, polars_context):
        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        num = unified_expr_cls(pl.col("num"))
        result = df.with_columns(num.cast_float64().alias("as_float")).collect()
        assert result["as_float"].to_list() == [1.0, 2.0, 3.0]

    def test_cast_int32(self, polars_context):
        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        flt = unified_expr_cls(pl.col("flt"))
        result = df.with_columns(flt.cast_int32().alias("as_int")).collect()
        assert result["as_int"].to_list() == [1, 2, 3]

    def test_cast_int64(self, polars_context):
        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        flt = unified_expr_cls(pl.col("flt"))
        result = df.with_columns(flt.cast_int64().alias("as_int64")).collect()
        assert result["as_int64"].to_list() == [1, 2, 3]

    def test_cast_string(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        num = unified_expr_cls(pl.col("num"))
        result = df.with_columns(num.cast_string().alias("as_str")).collect()
        assert result["as_str"].to_list() == ["1", "2", "3"]


class TestUnifiedLazyFrameOperations:
    @pytest.fixture
    def polars_df(self):
        polars = pytest.importorskip("polars")

        unified_lazy_frame_cls = _get_unified_lazy_frame()
        mock_adapter = _create_mock_adapter()

        df = polars.DataFrame(
            {
                "id": [1, 2, 3, 4, 5],
                "category": ["A", "B", "A", "B", "A"],
                "value": [10, 20, 30, 40, 50],
            }
        ).lazy()
        return {
            "polars": polars,
            "df": unified_lazy_frame_cls(df, mock_adapter),
        }

    def test_filter(self, polars_df):

        pl = polars_df["polars"]
        df = polars_df["df"]
        unified_expr_cls = _get_unified_expr()

        condition = unified_expr_cls(pl.col("category") == "A")
        result = df.filter(condition).collect()
        assert len(result) == 3
        assert result["id"].to_list() == [1, 3, 5]

    def test_select(self, polars_df):

        pl = polars_df["polars"]
        df = polars_df["df"]
        unified_expr_cls = _get_unified_expr()

        id_col = unified_expr_cls(pl.col("id"))
        value_col = unified_expr_cls(pl.col("value"))
        result = df.select(id_col, value_col).collect()
        assert result.columns == ["id", "value"]
        assert len(result) == 5

    def test_sort(self, polars_df):

        df = polars_df["df"]

        result = df.sort("value", descending=True).collect()
        assert result["value"].to_list() == [50, 40, 30, 20, 10]

    def test_limit(self, polars_df):

        df = polars_df["df"]

        result = df.limit(3).collect()
        assert len(result) == 3
        assert result["id"].to_list() == [1, 2, 3]

    def test_head(self, polars_df):
        df = polars_df["df"]

        result = df.head(2).collect()
        assert len(result) == 2

    def test_unique(self, polars_df):

        df = polars_df["df"]

        result = df.select("category").unique().sort("category").collect()
        assert result["category"].to_list() == ["A", "B"]

    def test_columns_property(self, polars_df):

        df = polars_df["df"]
        assert df.columns == ["id", "category", "value"]

    def test_native_property(self, polars_df):

        polars = pytest.importorskip("polars")
        df = polars_df["df"]
        native = df.native
        assert isinstance(native, polars.LazyFrame)

    def test_rename(self, polars_df):

        df = polars_df["df"]

        result = df.rename({"id": "identifier", "value": "amount"}).collect()
        assert "identifier" in result.columns
        assert "amount" in result.columns
        assert "id" not in result.columns

    def test_group_by_with_list_agg(self, polars_df):

        pl = polars_df["polars"]
        df = polars_df["df"]
        unified_expr_cls = _get_unified_expr()

        value_col = unified_expr_cls(pl.col("value"))
        result = df.group_by("category").agg([value_col.sum().alias("total")]).sort("category").collect()
        assert result["total"].to_list() == [90, 60]


class TestUnifiedExprRepr:
    def test_repr(self):

        polars = pytest.importorskip("polars")
        unified_expr_cls = _get_unified_expr()

        expr = unified_expr_cls(polars.col("test"))
        repr_str = repr(expr)
        assert "UnifiedExpr" in repr_str


class TestUnifiedExprLogical:
    @pytest.fixture
    def polars_context(self):
        polars = pytest.importorskip("polars")

        unified_lazy_frame_cls = _get_unified_lazy_frame()
        mock_adapter = _create_mock_adapter()

        df = polars.DataFrame({"a": [True, True, False, False], "b": [True, False, True, False]}).lazy()
        return {
            "polars": polars,
            "df": unified_lazy_frame_cls(df, mock_adapter),
        }

    def test_or_operator(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        a = unified_expr_cls(pl.col("a"))
        b = unified_expr_cls(pl.col("b"))
        result = df.with_columns((a | b).alias("or_result")).collect()
        assert result["or_result"].to_list() == [True, True, True, False]

    def test_invert_operator(self, polars_context):

        pl = polars_context["polars"]
        df = polars_context["df"]
        unified_expr_cls = _get_unified_expr()

        a = unified_expr_cls(pl.col("a"))
        result = df.with_columns((~a).alias("not_result")).collect()
        assert result["not_result"].to_list() == [False, False, True, True]


class TestUnifiedExprNUnique:
    def test_n_unique(self):

        polars = pytest.importorskip("polars")

        unified_lazy_frame_cls = _get_unified_lazy_frame()
        mock_adapter = _create_mock_adapter()
        unified_expr_cls = _get_unified_expr()

        df = polars.DataFrame({"group": ["A", "A", "B"], "val": [1, 1, 2]}).lazy()
        ulf = unified_lazy_frame_cls(df, mock_adapter)

        val_col = unified_expr_cls(polars.col("val"))
        result = ulf.group_by("group").agg(val_col.n_unique().alias("distinct")).sort("group").collect()
        assert result["distinct"].to_list() == [1, 1]


class TestFrameAggFacade:
    @pytest.fixture
    def frame(self):
        polars = pytest.importorskip("polars")

        unified_lazy_frame_cls = _get_unified_lazy_frame()
        mock_adapter = _create_mock_adapter()
        unified_expr_cls = _get_unified_expr()

        df = polars.DataFrame({"x": [1.0, 2.0, 3.0], "y": [10.0, 20.0, 30.0]}).lazy()
        return {
            "polars": polars,
            "df": unified_lazy_frame_cls(df, mock_adapter),
            "expr": unified_expr_cls,
        }

    def test_global_sum(self, frame):
        pl = frame["polars"]
        result = frame["df"].agg(frame["expr"](pl.col("x").sum().alias("s"))).collect()
        assert result.to_dicts() == [{"s": 6.0}]

    def test_plain_aggregates_with_select_arithmetic(self, frame):
        pl = frame["polars"]
        expr_cls = frame["expr"]
        result = (
            frame["df"]
            .agg(expr_cls(pl.col("x").sum().alias("s")), expr_cls(pl.col("y").sum().alias("t")))
            .select((expr_cls(pl.col("s")) * 100.0 / expr_cls(pl.col("t"))).alias("r"))
            .collect()
        )
        assert result.to_dicts() == [{"r": 10.0}]

    def test_list_form(self, frame):
        pl = frame["polars"]
        expr_cls = frame["expr"]
        result = frame["df"].agg([expr_cls(pl.col("x").max().alias("m"))]).collect()
        assert result.to_dicts() == [{"m": 3.0}]

    def test_empty_frame_yields_single_row(self, frame):
        pl = frame["polars"]

        unified_lazy_frame_cls = _get_unified_lazy_frame()
        mock_adapter = _create_mock_adapter()
        empty = unified_lazy_frame_cls(pl.DataFrame({"x": []}, schema={"x": pl.Float64}).lazy(), mock_adapter)
        result = empty.agg(frame["expr"](pl.col("x").count().alias("n"))).collect()
        assert result.to_dicts() == [{"n": 0}]


class TestFrameAggFacadeDataFusion:
    @pytest.fixture
    def dframe(self):
        pytest.importorskip("datafusion")
        pa = pytest.importorskip("pyarrow")

        from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

        adapter = DataFusionDataFrameAdapter()
        ctx = adapter.create_context()
        table = pa.table({"x": [10.0, 20.0, 30.0, 40.0], "d": [0.1, 0.2, 0.0, 0.5], "g": ["a", "a", "b", "b"]})
        adapter.session_ctx.register_record_batches("t", [table.to_batches()])
        ctx.register_table("t", adapter.session_ctx.sql("SELECT * FROM t"))
        return ctx

    def test_plain_global_sums(self, dframe):
        col = dframe.col
        result = dframe.get_table("t").agg(col("x").sum().alias("s"), col("d").sum().alias("t")).collect()
        as_dict = result.to_pydict()
        assert as_dict["s"] == [100.0]
        assert as_dict["t"] == pytest.approx([0.8])

    def test_precomputed_product_sums_correctly(self, dframe):
        col, lit = dframe.col, dframe.lit
        result = (
            dframe.get_table("t")
            .with_columns((col("x") * (lit(1) - col("d"))).alias("revenue"))
            .agg(col("revenue").sum().alias("revenue"))
            .collect()
        )
        assert result.to_pydict()["revenue"] == pytest.approx([75.0])

    def test_ratio_over_plain_sums(self, dframe):
        col, lit = dframe.col, dframe.lit
        result = (
            dframe.get_table("t")
            .agg(col("x").sum().alias("s"), col("d").sum().alias("t"))
            .select((col("s") * lit(100.0) / col("t")).alias("r"))
            .collect()
        )
        assert result.to_pydict()["r"] == pytest.approx([10000.0 / 0.8])

    def test_grouped_precomputed_sums(self, dframe):
        col, lit = dframe.col, dframe.lit
        result = (
            dframe.get_table("t")
            .with_columns((col("x") * (lit(1) - col("d"))).alias("revenue"))
            .group_by("g")
            .agg(col("revenue").sum().alias("revenue"))
            .sort("g")
            .collect()
        )
        as_dict = result.to_pydict()
        assert as_dict["g"] == ["a", "b"]
        assert as_dict["revenue"] == pytest.approx([10 * 0.9 + 20 * 0.8, 30 * 1.0 + 40 * 0.5])

    def test_empty_set_selects_null_row(self, dframe):
        col, lit = dframe.col, dframe.lit
        result = (
            dframe.get_table("t")
            .filter(col("x") > lit(1000.0))
            .agg(col("x").sum().alias("total"), col("x").count().alias("n"))
            .select(
                dframe.when(col("n") > lit(0)).then(col("total") / lit(7.0)).otherwise(lit(None)).alias("avg_yearly")
            )
            .collect()
        )
        as_dict = result.to_pydict()
        assert as_dict["avg_yearly"] == [None]


@pytest.mark.slow
class TestFrameAggIdiomsPySpark:
    @pytest.fixture(scope="class")
    def sframe(self, pyspark_test_environment):
        require_pyspark()

        from benchbox.platforms.dataframe.pyspark_df import PySparkDataFrameAdapter

        adapter = PySparkDataFrameAdapter(master="local[2]", driver_memory="1g")
        ctx = adapter.create_context()
        ctx.register_table("t", adapter.spark.createDataFrame([(10.0,), (20.0,)], ["x"]))
        yield ctx
        adapter.close()

    def _avg_yearly(self, sframe, pred):
        col, lit = sframe.col, sframe.lit
        return (
            sframe.get_table("t")
            .filter(pred(col, lit))
            .agg(col("x").sum().alias("total"), col("x").count().alias("n"))
            .select(
                sframe.when(col("n") > lit(0)).then(col("total") / lit(7.0)).otherwise(lit(None)).alias("avg_yearly")
            )
            .collect_column_as_list("avg_yearly")
        )

    def test_empty_set_selects_null_row(self, sframe):
        assert self._avg_yearly(sframe, lambda col, lit: col("x") > lit(1000.0)) == [None]

    def test_nonempty_set_selects_value(self, sframe):
        assert self._avg_yearly(sframe, lambda col, lit: col("x") > lit(0.0)) == pytest.approx([30.0 / 7.0])


