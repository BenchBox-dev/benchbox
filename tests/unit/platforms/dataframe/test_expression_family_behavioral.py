# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def adapter() -> PolarsDataFrameAdapter:
    return PolarsDataFrameAdapter()


@pytest.fixture
def ctx(adapter):
    return adapter.create_context()


class TestArithmeticExpressions:
    def test_col_add_lit(self, ctx, adapter):
        adapter.create_context()
        df = pl.LazyFrame({"price": [100, 200, 300]})
        ctx.register_table("items", df)

        table = ctx.get_table("items")
        result = table.select((ctx.col("price") + ctx.lit(10)).alias("total"))
        collected = adapter.collect(result.native)

        assert list(collected["total"]) == [110, 210, 310]

    def test_col_sub_lit(self, ctx, adapter):
        df = pl.LazyFrame({"price": [50, 100]})
        ctx.register_table("items", df)

        table = ctx.get_table("items")
        result = table.select((ctx.col("price") - ctx.lit(5)).alias("discounted"))
        collected = adapter.collect(result.native)

        assert list(collected["discounted"]) == [45, 95]

    def test_col_mul_lit(self, ctx, adapter):
        df = pl.LazyFrame({"qty": [3, 7]})
        ctx.register_table("items", df)

        table = ctx.get_table("items")
        result = table.select((ctx.col("qty") * ctx.lit(2)).alias("doubled"))
        collected = adapter.collect(result.native)

        assert list(collected["doubled"]) == [6, 14]

    def test_col_div_lit(self, ctx, adapter):
        df = pl.LazyFrame({"total": [10.0, 20.0]})
        ctx.register_table("items", df)

        table = ctx.get_table("items")
        result = table.select((ctx.col("total") / ctx.lit(2)).alias("half"))
        collected = adapter.collect(result.native)

        assert list(collected["half"]) == [5.0, 10.0]

    def test_col_add_col(self, ctx, adapter):
        df = pl.LazyFrame({"a": [1, 2, 3], "b": [10, 20, 30]})
        ctx.register_table("data", df)

        table = ctx.get_table("data")
        result = table.select((ctx.col("a") + ctx.col("b")).alias("total"))
        collected = adapter.collect(result.native)

        assert list(collected["total"]) == [11, 22, 33]

    def test_complex_arithmetic_expression(self, ctx, adapter):
        df = pl.LazyFrame({"price": [10, 20], "qty": [3, 4]})
        ctx.register_table("orders", df)

        table = ctx.get_table("orders")
        expr = (ctx.col("price") * ctx.col("qty")) - ctx.lit(5)
        result = table.select(expr.alias("net"))
        collected = adapter.collect(result.native)

        assert list(collected["net"]) == [25, 75]


class TestStringExpressions:
    def test_str_contains(self, ctx, adapter):
        df = pl.LazyFrame({"name": ["foobar", "bazfoo", "hello"]})
        ctx.register_table("items", df)

        table = ctx.get_table("items")
        result = table.filter(ctx.col("name").str.contains("foo"))
        collected = adapter.collect(result.native)

        assert list(collected["name"]) == ["foobar", "bazfoo"]

    def test_str_starts_with(self, ctx, adapter):
        df = pl.LazyFrame({"name": ["abcdef", "abc", "xyzabc"]})
        ctx.register_table("items", df)

        table = ctx.get_table("items")
        result = table.filter(ctx.col("name").str.starts_with("abc"))
        collected = adapter.collect(result.native)

        assert list(collected["name"]) == ["abcdef", "abc"]

    def test_str_len_chars(self, ctx, adapter):
        df = pl.LazyFrame({"word": ["hi", "hello", ""]})
        ctx.register_table("items", df)

        table = ctx.get_table("items")
        result = table.select(ctx.col("word").str.len_chars().alias("length"))
        collected = adapter.collect(result.native)

        assert list(collected["length"]) == [2, 5, 0]

    def test_str_ends_with(self, ctx, adapter):
        df = pl.LazyFrame({"name": ["abcxyz", "xyz", "abc"]})
        ctx.register_table("items", df)

        table = ctx.get_table("items")
        result = table.filter(ctx.col("name").str.ends_with("xyz"))
        collected = adapter.collect(result.native)

        assert list(collected["name"]) == ["abcxyz", "xyz"]

    def test_str_to_uppercase(self, ctx, adapter):
        df = pl.LazyFrame({"name": ["hello", "World"]})
        ctx.register_table("items", df)

        table = ctx.get_table("items")
        result = table.select(ctx.col("name").str.to_uppercase().alias("upper"))
        collected = adapter.collect(result.native)

        assert list(collected["upper"]) == ["HELLO", "WORLD"]


class TestDateExpressions:
    def test_dt_year(self, ctx, adapter):
        df = pl.LazyFrame({"order_date": [date(2024, 1, 15), date(2025, 6, 30), date(2023, 12, 1)]})
        ctx.register_table("orders", df)

        table = ctx.get_table("orders")
        result = table.select(ctx.col("order_date").dt.year().alias("yr"))
        collected = adapter.collect(result.native)

        assert list(collected["yr"]) == [2024, 2025, 2023]

    def test_dt_month(self, ctx, adapter):
        df = pl.LazyFrame({"order_date": [date(2024, 1, 15), date(2024, 6, 30), date(2024, 12, 1)]})
        ctx.register_table("orders", df)

        table = ctx.get_table("orders")
        result = table.select(ctx.col("order_date").dt.month().alias("mo"))
        collected = adapter.collect(result.native)

        assert list(collected["mo"]) == [1, 6, 12]

    def test_dt_day(self, ctx, adapter):
        df = pl.LazyFrame({"order_date": [date(2024, 1, 15), date(2024, 6, 30), date(2024, 12, 1)]})
        ctx.register_table("orders", df)

        table = ctx.get_table("orders")
        result = table.select(ctx.col("order_date").dt.day().alias("dd"))
        collected = adapter.collect(result.native)

        assert list(collected["dd"]) == [15, 30, 1]


class TestAggregationExpressions:
    def test_sum_aggregation(self, ctx, adapter):
        df = pl.LazyFrame({"value": [10, 20, 30]})
        ctx.register_table("data", df)

        table = ctx.get_table("data")
        result = table.select(ctx.col("value").sum().alias("total"))
        collected = adapter.collect(result.native)

        assert collected["total"].item() == 60

    def test_count_aggregation(self, ctx, adapter):
        df = pl.LazyFrame({"value": [10, 20, 30, 40]})
        ctx.register_table("data", df)

        table = ctx.get_table("data")
        result = table.select(ctx.col("value").count().alias("cnt"))
        collected = adapter.collect(result.native)

        assert collected["cnt"].item() == 4

    def test_mean_aggregation(self, ctx, adapter):
        df = pl.LazyFrame({"value": [10.0, 20.0, 30.0]})
        ctx.register_table("data", df)

        table = ctx.get_table("data")
        result = table.select(ctx.col("value").mean().alias("avg_val"))
        collected = adapter.collect(result.native)

        assert collected["avg_val"].item() == pytest.approx(20.0)

    def test_min_aggregation(self, ctx, adapter):
        df = pl.LazyFrame({"value": [30, 10, 20]})
        ctx.register_table("data", df)

        table = ctx.get_table("data")
        result = table.select(ctx.col("value").min().alias("min_val"))
        collected = adapter.collect(result.native)

        assert collected["min_val"].item() == 10

    def test_max_aggregation(self, ctx, adapter):
        df = pl.LazyFrame({"value": [30, 10, 20]})
        ctx.register_table("data", df)

        table = ctx.get_table("data")
        result = table.select(ctx.col("value").max().alias("max_val"))
        collected = adapter.collect(result.native)

        assert collected["max_val"].item() == 30

    def test_count_distinct(self, ctx, adapter):
        df = pl.LazyFrame({"category": ["A", "B", "A", "C", "B"]})
        ctx.register_table("data", df)

        table = ctx.get_table("data")
        result = table.select(ctx.col("category").n_unique().alias("nuniq"))
        collected = adapter.collect(result.native)

        assert collected["nuniq"].item() == 3

    def test_grouped_sum(self, ctx, adapter):
        df = pl.LazyFrame({"grp": ["X", "Y", "X", "Y"], "val": [10, 20, 30, 40]})
        ctx.register_table("data", df)

        table = ctx.get_table("data")
        result = table.group_by("grp").agg(ctx.col("val").sum().alias("total"))
        collected = adapter.collect(result.native).sort("grp")

        assert list(collected["grp"]) == ["X", "Y"]
        assert list(collected["total"]) == [40, 60]

    def test_grouped_count(self, ctx, adapter):
        df = pl.LazyFrame({"grp": ["A", "A", "B"]})
        ctx.register_table("data", df)

        table = ctx.get_table("data")
        result = table.group_by("grp").agg(ctx.col("grp").count().alias("cnt"))
        collected = adapter.collect(result.native).sort("grp")

        assert list(collected["grp"]) == ["A", "B"]
        assert list(collected["cnt"]) == [2, 1]


class TestNullHandling:
    def test_is_null(self, ctx, adapter):
        df = pl.LazyFrame({"val": [1, None, 3, None]})
        ctx.register_table("data", df)

        table = ctx.get_table("data")
        result = table.filter(ctx.col("val").is_null())
        collected = adapter.collect(result.native)

        assert len(collected) == 2

    def test_is_not_null(self, ctx, adapter):
        df = pl.LazyFrame({"val": [1, None, 3, None]})
        ctx.register_table("data", df)

        table = ctx.get_table("data")
        result = table.filter(ctx.col("val").is_not_null())
        collected = adapter.collect(result.native)

        assert len(collected) == 2
        assert list(collected["val"]) == [1, 3]

    def test_fill_null_with_literal(self, ctx, adapter):
        df = pl.LazyFrame({"val": [1, None, 3, None]})
        ctx.register_table("data", df)

        table = ctx.get_table("data")
        filled_expr = ctx.col("val").fill_null(ctx.lit(0))
        result = table.select(filled_expr.alias("filled"))
        collected = adapter.collect(result.native)

        assert list(collected["filled"]) == [1, 0, 3, 0]

    def test_fill_null_preserves_non_null(self, ctx, adapter):
        df = pl.LazyFrame({"val": [10, 20, 30]})
        ctx.register_table("data", df)

        table = ctx.get_table("data")
        result = table.select(ctx.col("val").fill_null(ctx.lit(999)).alias("filled"))
        collected = adapter.collect(result.native)

        assert list(collected["filled"]) == [10, 20, 30]

    def test_null_count_after_fill(self, ctx, adapter):
        df = pl.LazyFrame({"val": [None, None, 5]})
        ctx.register_table("data", df)

        table = ctx.get_table("data")
        filled = table.select(ctx.col("val").fill_null(ctx.lit(0)).alias("val"))
        null_count_result = filled.filter(ctx.col("val").is_null())
        collected = adapter.collect(null_count_result.native)

        assert len(collected) == 0


class TestContextIntegration:
    def test_lit_returns_unified_expr(self, ctx):
        from benchbox.platforms.dataframe.unified_frame import UnifiedExpr

        result = ctx.lit(42)
        assert isinstance(result, UnifiedExpr)

    def test_col_returns_unified_expr(self, ctx):
        from benchbox.platforms.dataframe.unified_frame import UnifiedExpr

        result = ctx.col("name")
        assert isinstance(result, UnifiedExpr)

    def test_get_table_returns_unified_lazy_frame(self, ctx, adapter):
        from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame

        df = pl.LazyFrame({"val": [1]})
        ctx.register_table("tiny", df)

        result = ctx.get_table("tiny")
        assert isinstance(result, UnifiedLazyFrame)

    def test_multiple_tables_independent(self, ctx, adapter):
        df_a = pl.LazyFrame({"col_a": [1, 2]})
        df_b = pl.LazyFrame({"col_b": [10, 20, 30]})
        ctx.register_table("alpha", df_a)
        ctx.register_table("beta", df_b)

        alpha = ctx.get_table("alpha")
        beta = ctx.get_table("beta")

        assert adapter.get_row_count(adapter.collect(alpha.native)) == 2
        assert adapter.get_row_count(adapter.collect(beta.native)) == 3


