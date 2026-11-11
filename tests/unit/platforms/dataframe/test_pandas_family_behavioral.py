# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import pandas as pd
import pytest

from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def adapter() -> PandasDataFrameAdapter:
    return PandasDataFrameAdapter()


@pytest.fixture
def ctx(adapter):
    return adapter.create_context()


class TestDataFrameCreation:
    def test_scalar_to_df_single_column(self, adapter):
        result = adapter.scalar_to_df({"total": 42})

        assert len(result) == 1
        assert list(result.columns) == ["total"]
        assert result["total"].iloc[0] == 42

    def test_scalar_to_df_multiple_columns(self, adapter):
        result = adapter.scalar_to_df({"count": 10, "avg": 3.5, "label": "test"})

        assert len(result) == 1
        assert list(result.columns) == ["count", "avg", "label"]
        assert result["count"].iloc[0] == 10
        assert result["avg"].iloc[0] == pytest.approx(3.5)
        assert result["label"].iloc[0] == "test"

    def test_scalar_to_df_via_context(self, ctx):
        from benchbox.platforms.dataframe.unified_pandas_frame import UnifiedPandasFrame

        result = ctx.scalar_to_df({"answer": 42})

        assert isinstance(result, UnifiedPandasFrame)
        assert len(result.native) == 1
        assert result.native["answer"].iloc[0] == 42


class TestTypeMappingAndDtypes:
    def test_integer_column_dtype(self, adapter, ctx):
        df = pd.DataFrame({"val": [1, 2, 3]})
        ctx.register_table("nums", df)

        retrieved = ctx.get_table("nums").native
        assert pd.api.types.is_integer_dtype(retrieved["val"])

    def test_float_column_dtype(self, adapter, ctx):
        df = pd.DataFrame({"val": [1.5, 2.5, 3.5]})
        ctx.register_table("floats", df)

        retrieved = ctx.get_table("floats").native
        assert pd.api.types.is_float_dtype(retrieved["val"])

    def test_string_column_dtype(self, adapter, ctx):
        df = pd.DataFrame({"name": ["alice", "bob"]})
        ctx.register_table("names", df)

        retrieved = ctx.get_table("names").native
        assert pd.api.types.is_string_dtype(retrieved["name"].dtype)

    def test_datetime_column_dtype(self, adapter, ctx):
        df = pd.DataFrame({"ts": pd.to_datetime(["2024-01-01", "2024-06-15"])})
        ctx.register_table("dates", df)

        retrieved = ctx.get_table("dates").native
        assert pd.api.types.is_datetime64_any_dtype(retrieved["ts"])


class TestMergeOperations:
    def test_inner_merge_on_key(self, adapter):
        left = pd.DataFrame({"key": [1, 2, 3], "val_l": ["a", "b", "c"]})
        right = pd.DataFrame({"key": [2, 3, 4], "val_r": ["x", "y", "z"]})

        result = adapter.merge(left, right, on="key", how="inner")

        assert len(result) == 2
        assert set(result["key"]) == {2, 3}
        assert list(result.columns) == ["key", "val_l", "val_r"]

    def test_left_merge_preserves_left_rows(self, adapter):
        left = pd.DataFrame({"key": [1, 2, 3], "val_l": ["a", "b", "c"]})
        right = pd.DataFrame({"key": [2, 3], "val_r": ["x", "y"]})

        result = adapter.merge(left, right, on="key", how="left")

        assert len(result) == 3
        assert set(result["key"]) == {1, 2, 3}
        row_1 = result[result["key"] == 1]
        assert pd.isna(row_1["val_r"].iloc[0])

    def test_merge_with_left_on_right_on(self, adapter):
        left = pd.DataFrame({"order_id": [10, 20], "amount": [100, 200]})
        right = pd.DataFrame({"oid": [10, 20], "status": ["shipped", "pending"]})

        result = adapter.merge(left, right, left_on="order_id", right_on="oid", how="inner")

        assert len(result) == 2
        assert "order_id" in result.columns
        assert "status" in result.columns

    def test_merge_invalid_input_raises(self, adapter):
        with pytest.raises(TypeError, match="merge requires DataFrame-like"):
            adapter.merge("not_a_df", "also_not_a_df", on="key")


class TestGroupByOperations:
    def test_groupby_agg_named_style(self, adapter):
        df = pd.DataFrame({"category": ["A", "A", "B", "B"], "value": [10, 20, 30, 40]})

        result = adapter.groupby_agg(
            df,
            "category",
            {"total": ("value", "sum"), "average": ("value", "mean")},
            as_index=False,
        )

        assert set(result.columns) == {"category", "total", "average"}
        assert len(result) == 2

        row_a = result[result["category"] == "A"]
        assert row_a["total"].iloc[0] == 30
        assert row_a["average"].iloc[0] == pytest.approx(15.0)

    def test_groupby_agg_direct_style(self, adapter):
        df = pd.DataFrame({"category": ["X", "X", "Y"], "value": [5, 15, 100]})

        result = adapter.groupby_agg(df, "category", {"value": "sum"}, as_index=False)

        assert "value" in result.columns
        row_x = result[result["category"] == "X"]
        assert row_x["value"].iloc[0] == 20

    def test_groupby_size_single_column(self, adapter):
        df = pd.DataFrame({"grp": ["A", "B", "A", "B", "A"]})

        result = adapter.groupby_size(df, "grp", name="cnt")

        assert set(result.columns) == {"grp", "cnt"}
        row_a = result[result["grp"] == "A"]
        row_b = result[result["grp"] == "B"]
        assert row_a["cnt"].iloc[0] == 3
        assert row_b["cnt"].iloc[0] == 2

    def test_groupby_size_multiple_columns(self, adapter):
        df = pd.DataFrame(
            {
                "region": ["East", "East", "West", "West"],
                "product": ["A", "A", "A", "B"],
            }
        )

        result = adapter.groupby_size(df, ["region", "product"], name="count")

        assert "region" in result.columns
        assert "product" in result.columns
        assert "count" in result.columns
        assert len(result) == 3


class TestScalarExtraction:
    def test_scalar_from_single_value_df(self, adapter):
        df = pd.DataFrame({"result": [42]})

        value = adapter.scalar(df)

        assert value == 42

    def test_scalar_from_named_column(self, adapter):
        df = pd.DataFrame({"name": ["alice"], "age": [30]})

        value = adapter.scalar(df, column="name")

        assert value == "alice"

    def test_scalar_empty_df_raises(self, adapter):
        df = pd.DataFrame({"val": []})

        with pytest.raises(ValueError, match="empty DataFrame"):
            adapter.scalar(df)

    def test_scalar_multi_row_raises(self, adapter):
        df = pd.DataFrame({"val": [1, 2]})

        with pytest.raises(ValueError, match="Expected exactly one row"):
            adapter.scalar(df)

    def test_scalar_via_context(self, ctx, adapter):
        df = pd.DataFrame({"total": [99]})
        ctx.register_table("results", df)

        table = ctx.get_table("results")
        value = ctx.scalar(table.native, column="total")

        assert value == 99


class TestStringColumnAccess:
    def test_col_returns_string(self, ctx):
        result = ctx.col("order_total")

        assert result == "order_total"
        assert isinstance(result, str)

    def test_lit_returns_value_unchanged(self, ctx):
        assert ctx.lit(42) == 42
        assert ctx.lit("hello") == "hello"
        assert ctx.lit(3.14) == pytest.approx(3.14)

    def test_column_access_pattern_with_df(self, ctx, adapter):
        df = pd.DataFrame({"price": [10, 20, 30]})
        ctx.register_table("items", df)

        table = ctx.get_table("items")
        col_name = ctx.col("price")
        values = table.native[col_name]

        assert list(values) == [10, 20, 30]


class TestContextHelpers:
    def test_rename_columns(self, adapter):
        df = pd.DataFrame({"old_a": [1], "old_b": [2]})

        result = adapter.rename_columns(df, {"old_a": "new_a", "old_b": "new_b"})

        assert list(result.columns) == ["new_a", "new_b"]
        assert result["new_a"].iloc[0] == 1

    def test_union_all_single(self, adapter):
        df = pd.DataFrame({"val": [1, 2]})

        result = adapter.union_all(df)

        assert len(result) == 2
        assert list(result["val"]) == [1, 2]

    def test_union_all_multiple(self, adapter):
        df1 = pd.DataFrame({"val": [1, 2]})
        df2 = pd.DataFrame({"val": [3, 4]})

        result = adapter.union_all(df1, df2)

        assert len(result) == 4
        assert list(result["val"]) == [1, 2, 3, 4]

    def test_union_all_empty_raises(self, adapter):
        with pytest.raises(ValueError, match="At least one DataFrame"):
            adapter.union_all()

    def test_context_filter_gt(self, ctx, adapter):
        df = pd.DataFrame({"price": [5, 10, 15, 20]})
        ctx.register_table("products", df)

        result = ctx.filter_gt(df, "price", 10)

        assert len(result.native) == 2
        assert list(result.native["price"]) == [15, 20]

    def test_context_to_set_from_series(self, ctx):
        series = pd.Series([1, 2, 2, 3, 3, 3])

        result = ctx.to_set(series)

        assert isinstance(result, set)
        assert result == {1, 2, 3}

    def test_context_to_set_from_dataframe(self, ctx):
        df = pd.DataFrame({"ids": [10, 20, 10, 30]})

        result = ctx.to_set(df)

        assert result == {10, 20, 30}


class TestDateOperationDescriptors:
    def test_date_sub_descriptor(self, adapter):
        result = adapter.date_sub("ship_date", 30)

        assert result == {"op": "date_sub", "column": "ship_date", "days": 30}

    def test_date_add_descriptor(self, adapter):
        result = adapter.date_add("order_date", 7)

        assert result == {"op": "date_add", "column": "order_date", "days": 7}

    def test_cast_date_descriptor(self, adapter):
        result = adapter.cast_date("date_string")

        assert result == {"op": "cast_date", "column": "date_string"}

    def test_cast_string_descriptor(self, adapter):
        result = adapter.cast_string("numeric_col")

        assert result == {"op": "cast_string", "column": "numeric_col"}


class TestWindowFunctionDescriptors:
    def test_window_rank_descriptor(self, adapter):
        result = adapter.window_rank(order_by=[("price", False)], partition_by=["category"])

        assert result["op"] == "window_rank"
        assert result["order_by"] == [("price", False)]
        assert result["partition_by"] == ["category"]

    def test_window_row_number_descriptor(self, adapter):
        result = adapter.window_row_number(order_by=[("date", True)])

        assert result["op"] == "window_row_number"
        assert result["order_by"] == [("date", True)]
        assert result["partition_by"] == []

    def test_window_sum_descriptor(self, adapter):
        result = adapter.window_sum("amount", partition_by=["region"], order_by=[("date", True)])

        assert result["op"] == "window_sum"
        assert result["column"] == "amount"
        assert result["partition_by"] == ["region"]


