# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import datetime
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
def pl():
    return pytest.importorskip("polars")


@pytest.fixture
def ulf(pl):
    ULF = _get_unified_lazy_frame()
    adapter = _create_mock_adapter()
    df = pl.DataFrame(
        {
            "name": ["Alice", "Bob", "Charlie", "David", "Eve"],
            "city": ["New York", "Boston", "New York", "Chicago", "Boston"],
            "age": [25, 30, 35, 40, 45],
            "score": [88.5, 92.3, 75.0, 88.5, 95.1],
        }
    ).lazy()
    return ULF(df, adapter)


@pytest.fixture
def date_ulf(pl):
    ULF = _get_unified_lazy_frame()
    adapter = _create_mock_adapter()
    df = pl.DataFrame(
        {
            "event": ["A", "B", "C", "D"],
            "event_date": [
                datetime.date(2024, 1, 15),
                datetime.date(2024, 3, 20),
                datetime.date(2024, 6, 5),
                datetime.date(2024, 12, 31),
            ],
            "amount": [100, 200, 300, 400],
        }
    ).lazy()
    return ULF(df, adapter)


class TestStrToUppercase:
    def test_to_uppercase_converts_all_chars(self, pl, ulf):
        expr_factory = _get_unified_expr()
        expr = expr_factory(pl.col("name")).str.to_uppercase()
        result = ulf.with_columns(expr.alias("upper_name")).collect()
        assert result["upper_name"].to_list() == [
            "ALICE",
            "BOB",
            "CHARLIE",
            "DAVID",
            "EVE",
        ]

    def test_to_uppercase_mixed_case(self, pl, ulf):
        expr_factory = _get_unified_expr()
        expr = expr_factory(pl.col("city")).str.to_uppercase()
        result = ulf.with_columns(expr.alias("upper_city")).collect()
        assert result["upper_city"].to_list() == [
            "NEW YORK",
            "BOSTON",
            "NEW YORK",
            "CHICAGO",
            "BOSTON",
        ]


class TestStrToLowercase:
    def test_to_lowercase_converts_all_chars(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"text": ["HELLO", "World", "FoObAr"]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("text")).str.to_lowercase()
        result = frame.with_columns(expr.alias("lower")).collect()
        assert result["lower"].to_list() == ["hello", "world", "foobar"]


class TestStrLenChars:
    def test_len_chars_returns_character_count(self, pl, ulf):
        expr_factory = _get_unified_expr()
        expr = expr_factory(pl.col("name")).str.len_chars()
        result = ulf.with_columns(expr.alias("name_len")).collect()
        assert result["name_len"].to_list() == [5, 3, 7, 5, 3]

    def test_len_chars_with_spaces(self, pl, ulf):
        expr_factory = _get_unified_expr()
        expr = expr_factory(pl.col("city")).str.len_chars()
        result = ulf.with_columns(expr.alias("city_len")).collect()
        assert result["city_len"].to_list() == [8, 6, 8, 7, 6]


class TestStrSplit:
    def test_split_and_get_first_element(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"full_name": ["Alice Smith", "Bob Jones"]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("full_name")).str.split(" ").list.get(0)
        result = frame.with_columns(expr.alias("first_name")).collect()
        assert result["first_name"].to_list() == ["Alice", "Bob"]

    def test_split_and_get_last_element(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"full_name": ["Alice Smith", "Bob Jones"]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("full_name")).str.split(" ").list.get(1)
        result = frame.with_columns(expr.alias("last_name")).collect()
        assert result["last_name"].to_list() == ["Smith", "Jones"]


class TestStrSlice:
    def test_slice_with_offset_and_length(self, pl, ulf):
        expr_factory = _get_unified_expr()
        expr = expr_factory(pl.col("name")).str.slice(0, 3)
        result = ulf.with_columns(expr.alias("prefix")).collect()
        assert result["prefix"].to_list() == ["Ali", "Bob", "Cha", "Dav", "Eve"]

    def test_slice_with_offset_only(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"text": ["Hello"]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("text")).str.slice(2)
        result = frame.with_columns(expr.alias("sub")).collect()
        assert result["sub"].to_list() == ["llo"]


class TestDtHourMinute:
    def test_hour_extraction(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame(
            {
                "ts": [
                    datetime.datetime(2024, 1, 15, 10, 30),
                    datetime.datetime(2024, 1, 15, 23, 45),
                ]
            }
        ).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("ts")).dt.hour()
        result = frame.with_columns(expr.alias("hr")).collect()
        assert result["hr"].to_list() == [10, 23]

    def test_minute_extraction(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame(
            {
                "ts": [
                    datetime.datetime(2024, 1, 15, 10, 30),
                    datetime.datetime(2024, 1, 15, 23, 45),
                ]
            }
        ).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("ts")).dt.minute()
        result = frame.with_columns(expr.alias("mn")).collect()
        assert result["mn"].to_list() == [30, 45]


class TestDtWeekday:
    def test_weekday_returns_iso_weekday(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame(
            {
                "d": [
                    datetime.date(2024, 1, 15),
                    datetime.date(2024, 1, 20),
                    datetime.date(2024, 1, 21),
                ]
            }
        ).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("d")).dt.weekday()
        result = frame.with_columns(expr.alias("wd")).collect()
        weekdays = result["wd"].to_list()
        assert weekdays[0] == 0
        assert weekdays[1] == 5
        assert weekdays[2] == 6


class TestDtTruncate:
    def test_truncate_to_day(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame(
            {
                "ts": [
                    datetime.datetime(2024, 3, 15, 10, 30, 45),
                    datetime.datetime(2024, 3, 15, 23, 59, 59),
                ]
            }
        ).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("ts")).dt.truncate("1d")
        result = frame.with_columns(expr.alias("day")).collect()
        days = result["day"].to_list()
        assert days[0] == days[1]

    def test_truncate_to_hour(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame(
            {
                "ts": [
                    datetime.datetime(2024, 3, 15, 10, 30, 45),
                    datetime.datetime(2024, 3, 15, 10, 0, 0),
                ]
            }
        ).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("ts")).dt.truncate("1h")
        result = frame.with_columns(expr.alias("hr")).collect()
        hours = result["hr"].to_list()
        assert hours[0] == hours[1]


class TestDtDuration:
    def test_total_seconds_from_duration(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame(
            {
                "start": [datetime.datetime(2024, 1, 1, 0, 0, 0)],
                "end": [datetime.datetime(2024, 1, 1, 1, 30, 0)],
            }
        ).lazy()
        frame = ULF(df, adapter)
        diff = expr_factory(pl.col("end") - pl.col("start"))
        seconds = diff.dt.total_seconds()
        result = frame.with_columns(seconds.alias("secs")).collect()
        assert result["secs"].to_list() == [5400]

    def test_total_days_from_duration(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame(
            {
                "start": [datetime.date(2024, 1, 1)],
                "end": [datetime.date(2024, 1, 11)],
            }
        ).lazy()
        frame = ULF(df, adapter)
        diff = expr_factory(pl.col("end") - pl.col("start"))
        days = diff.dt.total_days()
        result = frame.with_columns(days.alias("d")).collect()
        assert result["d"].to_list() == [10]


class TestWhenThenOtherwise:
    def test_single_when_then_otherwise(self, pl, ulf):
        from benchbox.platforms.dataframe.unified_frame import UnifiedExpr

        cond = UnifiedExpr(pl.col("age") < 35)
        result_expr = pl.when(cond.native).then(pl.lit("young")).otherwise(pl.lit("mature"))
        result = ulf.with_columns(UnifiedExpr(result_expr).alias("category")).collect()
        assert result["category"].to_list() == [
            "young",
            "young",
            "mature",
            "mature",
            "mature",
        ]

    def test_multi_branch_when_then(self, pl, ulf):
        from benchbox.platforms.dataframe.unified_frame import (
            UnifiedExpr,
            UnifiedWhen,
        )

        when_builder = UnifiedWhen(pl.when(pl.col("age") < 30), platform="Polars")
        when_then = when_builder.then(pl.lit("young"))
        when2 = when_then.when(UnifiedExpr(pl.col("age") < 40))
        when_then2 = when2.then(pl.lit("middle"))
        final = when_then2.otherwise(pl.lit("senior"))

        result = ulf.with_columns(final.alias("bracket")).collect()
        assert result["bracket"].to_list() == [
            "young",
            "middle",
            "middle",
            "senior",
            "senior",
        ]

    def test_when_then_with_unified_expr_value(self, pl, ulf):
        from benchbox.platforms.dataframe.unified_frame import (
            UnifiedExpr,
            UnifiedWhen,
        )

        when_builder = UnifiedWhen(pl.when(pl.col("age") >= 35), platform="Polars")
        doubled_age = UnifiedExpr(pl.col("age") * 2)
        when_then = when_builder.then(doubled_age)
        final = when_then.otherwise(UnifiedExpr(pl.col("age")))
        result = ulf.with_columns(final.alias("adjusted")).collect()
        assert result["adjusted"].to_list() == [25, 30, 70, 80, 90]


class TestVstack:
    def test_vstack_combines_rows(self, pl):
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df1 = pl.DataFrame({"x": [1, 2], "y": ["a", "b"]}).lazy()
        df2 = pl.DataFrame({"x": [3, 4], "y": ["c", "d"]}).lazy()
        f1 = ULF(df1, adapter)
        f2 = ULF(df2, adapter)
        result = f1.vstack(f2).collect()
        assert result["x"].to_list() == [1, 2, 3, 4]
        assert result["y"].to_list() == ["a", "b", "c", "d"]

    def test_vstack_preserves_column_count(self, pl):
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df1 = pl.DataFrame({"a": [1], "b": [2], "c": [3]}).lazy()
        df2 = pl.DataFrame({"a": [4], "b": [5], "c": [6]}).lazy()
        f1 = ULF(df1, adapter)
        f2 = ULF(df2, adapter)
        result = f1.vstack(f2).collect()
        assert len(result.columns) == 3
        assert len(result) == 2


class TestDistinct:
    def test_distinct_removes_duplicates(self, pl):
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"x": [1, 1, 2, 2, 3], "y": ["a", "a", "b", "b", "c"]}).lazy()
        frame = ULF(df, adapter)
        result = frame.distinct().collect()
        assert len(result) == 3
        assert sorted(result["x"].to_list()) == [1, 2, 3]


class TestLimitHead:
    def test_limit_returns_n_rows(self, pl, ulf):
        result = ulf.limit(3).collect()
        assert len(result) == 3

    def test_limit_preserves_order(self, pl, ulf):
        result = ulf.limit(2).collect()
        assert result["name"].to_list() == ["Alice", "Bob"]

    def test_head_default_10(self, pl, ulf):
        result = ulf.head().collect()
        assert len(result) == 5

    def test_head_with_arg(self, pl, ulf):
        result = ulf.head(2).collect()
        assert len(result) == 2
        assert result["name"].to_list() == ["Alice", "Bob"]


class TestHavingClause:
    def test_having_filters_groups(self, pl):
        ULF = _get_unified_lazy_frame()
        expr_factory = _get_unified_expr()
        adapter = _create_mock_adapter()
        df = pl.DataFrame(
            {
                "dept": ["A", "A", "B", "B", "B", "C"],
                "salary": [50, 60, 40, 45, 55, 100],
            }
        ).lazy()
        frame = ULF(df, adapter)

        agg_result = frame.group_by("dept").agg(
            expr_factory(pl.col("salary").sum()).alias("total"),
            expr_factory(pl.col("salary").count()).alias("cnt"),
        )
        filtered = agg_result.filter(expr_factory(pl.col("cnt") >= 2))
        result = filtered.collect()
        depts = sorted(result["dept"].to_list())
        assert depts == ["A", "B"]

    def test_having_on_aggregated_value(self, pl):
        ULF = _get_unified_lazy_frame()
        expr_factory = _get_unified_expr()
        adapter = _create_mock_adapter()
        df = pl.DataFrame(
            {
                "dept": ["A", "A", "B", "B", "B"],
                "salary": [50, 60, 40, 45, 55],
            }
        ).lazy()
        frame = ULF(df, adapter)

        agg_result = frame.group_by("dept").agg(
            expr_factory(pl.col("salary").sum()).alias("total"),
        )
        filtered = agg_result.filter(expr_factory(pl.col("total") > 115))
        result = filtered.collect()
        assert result["dept"].to_list() == ["B"]
        assert result["total"].to_list() == [140]


class TestTypeCasting:
    def test_cast_int_to_float(self, pl, ulf):
        expr_factory = _get_unified_expr()
        expr = expr_factory(pl.col("age")).cast_float64()
        result = ulf.with_columns(expr.alias("age_f")).collect()
        assert result["age_f"].dtype == pl.Float64
        assert result["age_f"].to_list() == [25.0, 30.0, 35.0, 40.0, 45.0]

    def test_cast_float_to_string(self, pl, ulf):
        expr_factory = _get_unified_expr()
        expr = expr_factory(pl.col("score")).cast_string()
        result = ulf.with_columns(expr.alias("score_s")).collect()
        assert result["score_s"].dtype == pl.Utf8
        assert result["score_s"].to_list()[0] == "88.5"

    def test_cast_float_alias(self, pl, ulf):
        expr_factory = _get_unified_expr()
        expr = expr_factory(pl.col("age")).cast_float()
        result = ulf.with_columns(expr.alias("f")).collect()
        assert result["f"].dtype == pl.Float64

    def test_cast_int_alias(self, pl, ulf):
        expr_factory = _get_unified_expr()
        expr = expr_factory(pl.col("score")).cast_int()
        result = ulf.with_columns(expr.alias("i")).collect()
        assert result["i"].dtype == pl.Int32


class TestFloor:
    def test_floor_rounds_down(self, pl, ulf):
        expr_factory = _get_unified_expr()
        expr = expr_factory(pl.col("score")).floor()
        result = ulf.with_columns(expr.alias("fl")).collect()
        assert result["fl"].to_list() == [88.0, 92.0, 75.0, 88.0, 95.0]


class TestStatisticalAggregations:
    def test_std_on_column(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"v": [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("v")).std()
        result = frame.select(expr.alias("s")).collect()
        std_val = result["s"].to_list()[0]
        assert std_val == pytest.approx(2.138089935299395)

    def test_var_on_column(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"v": [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("v")).var()
        result = frame.select(expr.alias("variance")).collect()
        var_val = result["variance"].to_list()[0]
        assert var_val == pytest.approx(4.571428571428571)

    def test_quantile_median(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"v": [1.0, 2.0, 3.0, 4.0, 5.0]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("v")).quantile(0.5)
        result = frame.select(expr.alias("median")).collect()
        median_val = result["median"].to_list()[0]
        assert median_val == 3.0


class TestDescExpr:
    def test_desc_sort_order(self, pl, ulf):
        expr_factory = _get_unified_expr()
        desc_expr = expr_factory(pl.col("age")).desc()
        result = ulf.sort(desc_expr).collect()
        assert result["age"].to_list() == [45, 40, 35, 30, 25]


class TestMelt:
    def test_melt_unpivots_columns(self, pl):
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame(
            {
                "id": [1, 2],
                "q1": [10.0, 20.0],
                "q2": [30.0, 40.0],
            }
        ).lazy()
        frame = ULF(df, adapter)
        result = frame.melt(
            id_vars=["id"],
            value_vars=["q1", "q2"],
            variable_name="quarter",
            value_name="revenue",
        ).collect()
        assert len(result) == 4
        assert sorted(result["quarter"].unique().to_list()) == ["q1", "q2"]
        assert sorted(result["revenue"].to_list()) == [10.0, 20.0, 30.0, 40.0]


class TestExplode:
    def test_explode_list_column(self, pl):
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame(
            {
                "id": [1, 2],
                "items": [["a", "b"], ["c", "d", "e"]],
            }
        ).lazy()
        frame = ULF(df, adapter)
        result = frame.explode("items").collect()
        assert len(result) == 5
        assert result["items"].to_list() == ["a", "b", "c", "d", "e"]
        assert result["id"].to_list() == [1, 1, 2, 2, 2]


class TestScalar:
    def test_scalar_extracts_single_value(self, pl):
        ULF = _get_unified_lazy_frame()
        expr_factory = _get_unified_expr()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"x": [10, 20, 30]}).lazy()
        frame = ULF(df, adapter)
        result = frame.select(expr_factory(pl.col("x").sum()).alias("total"))
        val = result.scalar()
        assert val == 60

    def test_scalar_with_row_col_indices(self, pl):
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"a": [10, 20], "b": [30, 40]}).lazy()
        frame = ULF(df, adapter)
        assert frame.scalar(0, 0) == 10
        assert frame.scalar(0, 1) == 30
        assert frame.scalar(1, 0) == 20
        assert frame.scalar(1, 1) == 40


class TestRename:
    def test_rename_single_column(self, pl, ulf):
        result = ulf.rename({"name": "person"}).collect()
        assert "person" in result.columns
        assert "name" not in result.columns
        assert result["person"].to_list() == [
            "Alice",
            "Bob",
            "Charlie",
            "David",
            "Eve",
        ]

    def test_rename_multiple_columns(self, pl, ulf):
        result = ulf.rename({"name": "person", "age": "years"}).collect()
        assert "person" in result.columns
        assert "years" in result.columns
        assert "name" not in result.columns
        assert "age" not in result.columns


class TestWrapHelpers:
    def test_wrap_expr_wraps_native(self, pl):
        from benchbox.platforms.dataframe.unified_frame import UnifiedExpr, wrap_expr

        native = pl.col("x")
        wrapped = wrap_expr(native)
        assert isinstance(wrapped, UnifiedExpr)
        assert wrapped.native is native

    def test_wrap_expr_passthrough_already_wrapped(self, pl):
        from benchbox.platforms.dataframe.unified_frame import UnifiedExpr, wrap_expr

        expr = UnifiedExpr(pl.col("x"))
        result = wrap_expr(expr)
        assert result is expr

    def test_wrap_dataframe_wraps_native(self, pl):
        from benchbox.platforms.dataframe.unified_frame import (
            UnifiedLazyFrame,
            wrap_dataframe,
        )

        adapter = _create_mock_adapter()
        df = pl.DataFrame({"a": [1]}).lazy()
        wrapped = wrap_dataframe(df, adapter)
        assert isinstance(wrapped, UnifiedLazyFrame)
        assert wrapped.native is df


class TestTypeDetectionHelpers:
    def test_is_polars_expr_true(self, pl):
        from benchbox.platforms.dataframe.unified_frame import _is_polars_expr

        expr = pl.col("x")
        assert _is_polars_expr(expr) is True

    def test_is_polars_expr_false_for_int(self):
        from benchbox.platforms.dataframe.unified_frame import _is_polars_expr

        assert _is_polars_expr(42) is False

    def test_is_pyspark_column_false_for_int(self):
        from benchbox.platforms.dataframe.unified_frame import _is_pyspark_column

        assert _is_pyspark_column(42) is False

    def test_is_polars_df_true(self, pl):
        from benchbox.platforms.dataframe.unified_frame import _is_polars_df

        df = pl.DataFrame({"a": [1]}).lazy()
        assert _is_polars_df(df) is True

    def test_is_polars_df_false_for_dict(self):
        from benchbox.platforms.dataframe.unified_frame import _is_polars_df

        assert _is_polars_df({"a": 1}) is False

    def test_is_pyspark_df_false_for_dict(self):
        from benchbox.platforms.dataframe.unified_frame import _is_pyspark_df

        assert _is_pyspark_df({"a": 1}) is False

    def test_is_datafusion_df_false_for_dict(self):
        from benchbox.platforms.dataframe.unified_frame import _is_datafusion_df

        assert _is_datafusion_df({"a": 1}) is False


class TestCumulativeOps:
    def test_cum_sum(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"v": [1, 2, 3, 4, 5]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("v")).cum_sum()
        result = frame.with_columns(expr.alias("cs")).collect()
        assert result["cs"].to_list() == [1, 3, 6, 10, 15]

    def test_cum_max(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"v": [3, 1, 4, 1, 5]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("v")).cum_max()
        result = frame.with_columns(expr.alias("cm")).collect()
        assert result["cm"].to_list() == [3, 3, 4, 4, 5]

    def test_cum_min(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"v": [3, 1, 4, 1, 5]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("v")).cum_min()
        result = frame.with_columns(expr.alias("cm")).collect()
        assert result["cm"].to_list() == [3, 1, 1, 1, 1]


class TestExprUnique:
    def test_unique_collects_distinct_values(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame(
            {
                "grp": ["A", "A", "A", "B", "B"],
                "val": [1, 2, 1, 3, 3],
            }
        ).lazy()
        frame = ULF(df, adapter)
        result = frame.group_by("grp").agg(expr_factory(pl.col("val")).unique().alias("uniques")).sort("grp").collect()
        uniques_a = sorted(result["uniques"].to_list()[0])
        uniques_b = sorted(result["uniques"].to_list()[1])
        assert uniques_a == [1, 2]
        assert uniques_b == [3]


class TestExprSort:
    def test_sort_values_in_agg(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame(
            {
                "grp": ["A", "A", "A"],
                "val": [3, 1, 2],
            }
        ).lazy()
        frame = ULF(df, adapter)
        result = frame.group_by("grp").agg(expr_factory(pl.col("val")).sort().alias("sorted_vals")).collect()
        assert result["sorted_vals"].to_list()[0] == [1, 2, 3]


class TestIsIn:
    def test_is_in_filters_matching_values(self, pl, ulf):
        expr_factory = _get_unified_expr()
        cond = expr_factory(pl.col("city")).is_in(["Boston", "Chicago"])
        result = ulf.filter(cond).collect()
        cities = sorted(result["city"].to_list())
        assert cities == ["Boston", "Boston", "Chicago"]


class TestIsBetween:
    def test_is_between_inclusive(self, pl, ulf):
        expr_factory = _get_unified_expr()
        cond = expr_factory(pl.col("age")).is_between(30, 40)
        result = ulf.filter(cond).collect()
        ages = result["age"].to_list()
        assert all(30 <= a <= 40 for a in ages)
        assert sorted(ages) == [30, 35, 40]


class TestNullHandling:
    def test_fill_null_replaces_nulls(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"x": [1, None, 3, None, 5]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("x")).fill_null(0)
        result = frame.with_columns(expr.alias("filled")).collect()
        assert result["filled"].to_list() == [1, 0, 3, 0, 5]

    def test_is_null_detection(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"x": [1, None, 3]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("x")).is_null()
        result = frame.with_columns(expr.alias("nulls")).collect()
        assert result["nulls"].to_list() == [False, True, False]

    def test_is_not_null_detection(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"x": [1, None, 3]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("x")).is_not_null()
        result = frame.with_columns(expr.alias("ok")).collect()
        assert result["ok"].to_list() == [True, False, True]


class TestNUnique:
    def test_n_unique_counts_distinct(self, pl, ulf):
        expr_factory = _get_unified_expr()
        expr = expr_factory(pl.col("city")).n_unique()
        result = ulf.select(expr.alias("cnt")).collect()
        assert result["cnt"].to_list() == [3]


class TestFirstLast:
    def test_first_returns_first_value(self, pl, ulf):
        expr_factory = _get_unified_expr()
        expr = expr_factory(pl.col("name")).first()
        result = ulf.select(expr.alias("f")).collect()
        assert result["f"].to_list() == ["Alice"]

    def test_last_returns_last_value(self, pl, ulf):
        expr_factory = _get_unified_expr()
        expr = expr_factory(pl.col("name")).last()
        result = ulf.select(expr.alias("l")).collect()
        assert result["l"].to_list() == ["Eve"]


class TestUnifiedExprRepr:
    def test_repr_includes_class_name(self, pl):
        expr_factory = _get_unified_expr()
        expr = expr_factory(pl.col("x"))
        r = repr(expr)
        assert r.startswith("UnifiedExpr(")

    def test_native_property(self, pl):
        expr_factory = _get_unified_expr()
        native = pl.col("x")
        expr = expr_factory(native)
        assert expr.native is native


class TestColumnsProperty:
    def test_columns_returns_list(self, pl, ulf):
        cols = ulf.columns
        assert isinstance(cols, list)
        assert sorted(cols) == ["age", "city", "name", "score"]


class TestGroupByListArg:
    def test_group_by_with_list(self, pl, ulf):
        expr_factory = _get_unified_expr()
        result = ulf.group_by(["city"]).agg(expr_factory(pl.col("age").mean()).alias("avg_age")).sort("city").collect()
        cities = result["city"].to_list()
        assert sorted(cities) == ["Boston", "Chicago", "New York"]


class TestAggListArg:
    def test_agg_with_list(self, pl, ulf):
        expr_factory = _get_unified_expr()
        exprs = [
            expr_factory(pl.col("age").sum()).alias("total_age"),
            expr_factory(pl.col("age").count()).alias("cnt"),
        ]
        result = ulf.group_by("city").agg(exprs).sort("city").collect()
        assert "total_age" in result.columns
        assert "cnt" in result.columns


class TestSelectListArg:
    def test_select_with_list(self, pl, ulf):
        result = ulf.select(["name", "age"]).collect()
        assert sorted(result.columns) == ["age", "name"]
        assert len(result) == 5


class TestWithColumnsListArg:
    def test_with_columns_list(self, pl, ulf):
        expr_factory = _get_unified_expr()
        exprs = [
            expr_factory(pl.col("age") * 2).alias("double_age"),
            expr_factory(pl.col("score") + 1).alias("score_plus"),
        ]
        result = ulf.with_columns(exprs).collect()
        assert result["double_age"].to_list() == [50, 60, 70, 80, 90]
        assert result["score_plus"].to_list()[0] == 89.5


class TestArithmeticOps:
    def test_modulo_via_native(self, pl, ulf):
        expr_factory = _get_unified_expr()
        expr = expr_factory(pl.col("age") % 10)
        result = ulf.with_columns(expr.alias("mod10")).collect()
        assert result["mod10"].to_list() == [5, 0, 5, 0, 5]

    def test_or_operator(self, pl, ulf):
        expr_factory = _get_unified_expr()
        cond = expr_factory(pl.col("age") < 30) | expr_factory(pl.col("age") > 40)
        result = ulf.filter(cond).collect()
        ages = sorted(result["age"].to_list())
        assert ages == [25, 45]

    def test_invert_operator(self, pl, ulf):
        expr_factory = _get_unified_expr()
        cond = ~expr_factory(pl.col("age") < 35)
        result = ulf.filter(cond).collect()
        ages = sorted(result["age"].to_list())
        assert ages == [35, 40, 45]

    def test_ne_operator(self, pl, ulf):
        expr_factory = _get_unified_expr()
        cond = expr_factory(pl.col("city")) != "Boston"
        result = ulf.filter(cond).collect()
        assert "Boston" not in result["city"].to_list()


class TestUnifiedListExpr:
    def test_list_len(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"items": [[1, 2, 3], [4, 5], [6]]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("items")).list.len()
        result = frame.with_columns(expr.alias("n")).collect()
        assert result["n"].to_list() == [3, 2, 1]

    def test_list_sum(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"items": [[1, 2, 3], [4, 5], [6]]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("items")).list.sum()
        result = frame.with_columns(expr.alias("s")).collect()
        assert result["s"].to_list() == [6, 9, 6]

    def test_list_min(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"items": [[3, 1, 2], [5, 4], [6]]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("items")).list.min()
        result = frame.with_columns(expr.alias("mn")).collect()
        assert result["mn"].to_list() == [1, 4, 6]

    def test_list_max(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"items": [[3, 1, 2], [5, 4], [6]]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("items")).list.max()
        result = frame.with_columns(expr.alias("mx")).collect()
        assert result["mx"].to_list() == [3, 5, 6]

    def test_list_contains(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"items": [[1, 2, 3], [4, 5], [6, 1]]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("items")).list.contains(1)
        result = frame.with_columns(expr.alias("has1")).collect()
        assert result["has1"].to_list() == [True, False, True]

    def test_list_unique(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"items": [[1, 2, 1, 3], [4, 4, 5]]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("items")).list.unique()
        result = frame.with_columns(expr.alias("u")).collect()
        assert sorted(result["u"].to_list()[0]) == [1, 2, 3]
        assert sorted(result["u"].to_list()[1]) == [4, 5]

    def test_list_sort(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"items": [[3, 1, 2], [5, 4]]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("items")).list.sort()
        result = frame.with_columns(expr.alias("sorted")).collect()
        assert result["sorted"].to_list()[0] == [1, 2, 3]
        assert result["sorted"].to_list()[1] == [4, 5]

    def test_list_sort_descending(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"items": [[3, 1, 2]]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("items")).list.sort(descending=True)
        result = frame.with_columns(expr.alias("sorted")).collect()
        assert result["sorted"].to_list()[0] == [3, 2, 1]

    def test_list_slice(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"items": [[10, 20, 30, 40, 50]]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("items")).list.slice(1, 3)
        result = frame.with_columns(expr.alias("sliced")).collect()
        assert result["sliced"].to_list()[0] == [20, 30, 40]

    def test_list_get(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"items": [[10, 20, 30]]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("items")).list.get(1)
        result = frame.with_columns(expr.alias("second")).collect()
        assert result["second"].to_list() == [20]

    def test_list_eval(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"items": [[1, 2, 3]]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("items")).list.eval(pl.element() * 2)
        result = frame.with_columns(expr.alias("doubled")).collect()
        assert result["doubled"].to_list()[0] == [2, 4, 6]

    def test_list_alias(self, pl):
        from benchbox.platforms.dataframe.unified_frame import UnifiedListExpr

        list_expr = UnifiedListExpr(pl.col("items").implode(), is_polars=True)
        aliased = list_expr.alias("result")
        from benchbox.platforms.dataframe.unified_frame import UnifiedExpr

        assert isinstance(aliased, UnifiedExpr)

    def test_list_list_property_returns_self(self, pl):
        from benchbox.platforms.dataframe.unified_frame import UnifiedListExpr

        list_expr = UnifiedListExpr(pl.col("items"), is_polars=True)
        assert list_expr.list is list_expr


class TestUnifiedListExprCallable:
    def test_list_aggregation(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame(
            {
                "grp": ["A", "A", "B", "B"],
                "val": [1, 2, 3, 4],
            }
        ).lazy()
        frame = ULF(df, adapter)
        result = frame.group_by("grp").agg(expr_factory(pl.col("val")).list().alias("vals")).sort("grp").collect()
        assert sorted(result["vals"].to_list()[0]) == [1, 2]
        assert sorted(result["vals"].to_list()[1]) == [3, 4]


class TestRank:
    def test_rank_default(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"v": [30, 10, 20]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("v")).rank(method="ordinal")
        result = frame.with_columns(expr.alias("rnk")).collect()
        ranks = result["rnk"].to_list()
        assert ranks == [3, 1, 2]

    def test_rank_descending(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"v": [30, 10, 20]}).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("v")).rank(method="ordinal", descending=True)
        result = frame.with_columns(expr.alias("rnk")).collect()
        ranks = result["rnk"].to_list()
        assert ranks == [1, 3, 2]


class TestOver:
    def test_sum_over_partition(self, pl, ulf):
        expr_factory = _get_unified_expr()
        expr = expr_factory(pl.col("age")).sum().over("city")
        result = ulf.with_columns(expr.alias("city_total")).collect()
        ny_idx = [i for i, c in enumerate(result["city"].to_list()) if c == "New York"]
        for i in ny_idx:
            assert result["city_total"].to_list()[i] == 60

    def test_over_with_list_partition(self, pl, ulf):
        expr_factory = _get_unified_expr()
        expr = expr_factory(pl.col("score")).mean().over(["city"])
        result = ulf.with_columns(expr.alias("avg_score")).collect()
        boston_idx = [i for i, c in enumerate(result["city"].to_list()) if c == "Boston"]
        for i in boston_idx:
            assert abs(result["avg_score"].to_list()[i] - 93.7) < 0.1


class TestConditionalAggregation:
    def test_filter_sum(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame(
            {
                "type": ["A", "B", "A", "B", "A"],
                "amount": [10, 20, 30, 40, 50],
            }
        ).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("amount")).filter(expr_factory(pl.col("type") == "A")).sum()
        result = frame.select(expr.alias("a_total")).collect()
        assert result["a_total"].to_list() == [90]

    def test_filter_count(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame(
            {
                "type": ["A", "B", "A", "B", "A"],
                "amount": [10, 20, 30, 40, 50],
            }
        ).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("amount")).filter(expr_factory(pl.col("type") == "B")).count()
        result = frame.select(expr.alias("b_cnt")).collect()
        assert result["b_cnt"].to_list() == [2]


class TestStructExpr:
    def test_struct_field_access(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame(
            {
                "data": [
                    {"name": "Alice", "age": 25},
                    {"name": "Bob", "age": 30},
                ]
            }
        ).lazy()
        frame = ULF(df, adapter)
        expr = expr_factory(pl.col("data")).struct.field("name")
        result = frame.with_columns(expr.alias("person")).collect()
        assert result["person"].to_list() == ["Alice", "Bob"]


class TestSortBy:
    def test_sort_by_column(self, pl):
        expr_factory = _get_unified_expr()
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame(
            {
                "grp": ["A", "A", "A"],
                "rank": [3, 1, 2],
                "name": ["C", "A", "B"],
            }
        ).lazy()
        frame = ULF(df, adapter)
        result = frame.group_by("grp").agg(expr_factory(pl.col("name")).sort_by("rank").alias("sorted_names")).collect()
        assert result["sorted_names"].to_list()[0] == ["A", "B", "C"]


class TestFrameLevelAggregation:
    def test_frame_sum_all_columns(self, pl):
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]}).lazy()
        frame = ULF(df, adapter)
        result = frame.sum().collect()
        assert result["a"].to_list() == [6]
        assert result["b"].to_list() == [15]

    def test_frame_mean_all_columns(self, pl):
        ULF = _get_unified_lazy_frame()
        adapter = _create_mock_adapter()
        df = pl.DataFrame({"a": [1.0, 2.0, 3.0], "b": [4.0, 5.0, 6.0]}).lazy()
        frame = ULF(df, adapter)
        result = frame.mean().collect()
        assert result["a"].to_list() == [2.0]
        assert result["b"].to_list() == [5.0]


class TestCastDate:
    @pytest.mark.parametrize(
        "values",
        [
            ["2024-01-02", None],
            [datetime.date(2024, 1, 2), None],
            [datetime.datetime(2024, 1, 2, 5, 30), None],
        ],
        ids=["string", "date", "datetime"],
    )
    def test_cast_date_converts_supported_dtypes(self, pl, values):
        expr_factory = _get_unified_expr()
        frame = _get_unified_lazy_frame()(pl.DataFrame({"value": values}).lazy(), _create_mock_adapter())

        result = frame.select(expr_factory(pl.col("value")).cast_date().alias("value")).collect()

        assert result["value"].to_list() == [datetime.date(2024, 1, 2), None]

    def test_cast_date_rejects_invalid_string(self, pl):
        expr_factory = _get_unified_expr()
        frame = _get_unified_lazy_frame()(pl.DataFrame({"value": ["not a date"]}).lazy(), _create_mock_adapter())

        with pytest.raises(pl.exceptions.ComputeError):
            frame.select(expr_factory(pl.col("value")).cast_date().alias("value")).collect()


