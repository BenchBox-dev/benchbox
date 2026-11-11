# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pytest

from benchbox.core.dataframe.context import (
    DataFrameContext,
    DataFrameContextImpl,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class StubContext(DataFrameContextImpl[dict]):
    def col(self, name: str) -> str:
        return name

    def lit(self, value: Any) -> Any:
        return value

    def date_sub(self, column: Any, days: int) -> dict:
        return {"op": "date_sub", "column": column, "days": days}

    def date_add(self, column: Any, days: int) -> dict:
        return {"op": "date_add", "column": column, "days": days}

    def cast_date(self, column: Any) -> dict:
        return {"op": "cast_date", "column": column}

    def cast_string(self, column: Any) -> dict:
        return {"op": "cast_string", "column": column}

    def window_rank(self, order_by, partition_by=None):
        return {"op": "rank", "order_by": order_by, "partition_by": partition_by or []}

    def window_row_number(self, order_by, partition_by=None):
        return {"op": "row_number", "order_by": order_by, "partition_by": partition_by or []}

    def window_dense_rank(self, order_by, partition_by=None):
        return {"op": "dense_rank", "order_by": order_by, "partition_by": partition_by or []}

    def window_sum(self, column, partition_by=None, order_by=None):
        return {"op": "window_sum", "column": column, "partition_by": partition_by or [], "order_by": order_by}

    def window_avg(self, column, partition_by=None, order_by=None):
        return {"op": "window_avg", "column": column, "partition_by": partition_by or [], "order_by": order_by}

    def window_count(self, column=None, partition_by=None, order_by=None):
        return {"op": "window_count", "column": column, "partition_by": partition_by or [], "order_by": order_by}

    def window_min(self, column, partition_by=None):
        return {"op": "window_min", "column": column, "partition_by": partition_by or []}

    def window_max(self, column, partition_by=None):
        return {"op": "window_max", "column": column, "partition_by": partition_by or []}

    def union_all(self, *dataframes):
        return list(dataframes)

    def rename_columns(self, df, mapping):
        return {"df": df, "mapping": mapping}

    def scalar(self, df, column=None):
        if isinstance(df, dict):
            if column and column in df:
                vals = df[column]
                return vals[0] if isinstance(vals, list) and vals else None
            for key, vals in df.items():
                return vals[0] if isinstance(vals, list) and vals else None
        return None

    def scalar_to_df(self, data):
        return {k: [v] for k, v in data.items()}


class TestElementMethod:
    def test_element_raises_not_implemented(self):
        ctx = StubContext(platform="pandas", family="pandas")
        with pytest.raises(NotImplementedError, match="not supported on pandas"):
            ctx.element()


class TestTableRegistrationEdgeCases:
    def test_register_empty_dict_as_table(self):
        ctx = StubContext(platform="test", family="pandas")
        ctx.register_table("empty_table", {})
        assert ctx.table_exists("empty_table") is True
        assert ctx.get_table("empty_table") == {}

    def test_register_table_with_mixed_case(self):
        ctx = StubContext(platform="test", family="pandas")
        ctx.register_table("MyTable", {"col": [1]})

        assert ctx.table_exists("mytable") is True
        assert ctx.table_exists("MYTABLE") is True
        assert ctx.table_exists("MyTable") is True

    def test_unregister_case_insensitive(self):
        ctx = StubContext(platform="test", family="pandas")
        ctx.register_table("orders", {"data": [1]})

        result = ctx.unregister_table("ORDERS")
        assert result is True
        assert ctx.table_exists("orders") is False

    def test_get_table_error_with_no_tables(self):
        ctx = StubContext(platform="test", family="pandas")
        with pytest.raises(KeyError, match="none"):
            ctx.get_table("anything")

    def test_clear_then_list_returns_empty(self):
        ctx = StubContext(platform="test", family="pandas")
        ctx.register_table("alpha", {})
        ctx.register_table("beta", {})
        ctx.clear_tables()

        assert ctx.list_tables() == []


class TestToDateEdgeCases:
    def test_to_date_leap_year(self):
        ctx = StubContext(platform="test", family="pandas")
        result = ctx.to_date("2024-02-29")
        assert result == date(2024, 2, 29)

    def test_to_date_invalid_format_raises(self):
        ctx = StubContext(platform="test", family="pandas")
        with pytest.raises(ValueError):
            ctx.to_date("not-a-date")

    def test_to_date_datetime_extracts_date_part(self):
        ctx = StubContext(platform="test", family="pandas")
        dt_input = datetime(2026, 12, 25, 23, 59, 59)
        result = ctx.to_date(dt_input)
        assert result == date(2026, 12, 25)

    def test_to_date_with_bool_raises(self):
        ctx = StubContext(platform="test", family="pandas")
        with pytest.raises(TypeError, match="Cannot convert"):
            ctx.to_date(True)

    def test_to_date_with_none_raises(self):
        ctx = StubContext(platform="test", family="pandas")
        with pytest.raises(TypeError, match="Cannot convert"):
            ctx.to_date(None)


class TestDaysEdgeCases:
    def test_days_zero(self):
        ctx = StubContext(platform="test", family="pandas")
        result = ctx.days(0)
        assert result == timedelta(days=0)

    def test_days_large_value(self):
        ctx = StubContext(platform="test", family="pandas")
        result = ctx.days(365)
        assert result.days == 365


class TestWindowFunctions:
    def test_window_rank_with_partition(self):
        ctx = StubContext(platform="test", family="expression")
        result = ctx.window_rank(
            order_by=[("sales", False)],
            partition_by=["category"],
        )
        assert result["op"] == "rank"
        assert result["order_by"] == [("sales", False)]
        assert result["partition_by"] == ["category"]

    def test_window_row_number_without_partition(self):
        ctx = StubContext(platform="test", family="expression")
        result = ctx.window_row_number(order_by=[("created_at", True)])
        assert result["op"] == "row_number"
        assert result["partition_by"] == []

    def test_window_dense_rank(self):
        ctx = StubContext(platform="test", family="expression")
        result = ctx.window_dense_rank(
            order_by=[("score", False)],
            partition_by=["department"],
        )
        assert result["op"] == "dense_rank"

    def test_window_sum_with_order_by(self):
        ctx = StubContext(platform="test", family="expression")
        result = ctx.window_sum(
            column="revenue",
            partition_by=["year"],
            order_by=[("month", True)],
        )
        assert result["op"] == "window_sum"
        assert result["column"] == "revenue"
        assert result["order_by"] == [("month", True)]

    def test_window_avg_without_order_by(self):
        ctx = StubContext(platform="test", family="expression")
        result = ctx.window_avg(column="price", partition_by=["category"])
        assert result["op"] == "window_avg"
        assert result["order_by"] is None

    def test_window_count_star(self):
        ctx = StubContext(platform="test", family="expression")
        result = ctx.window_count(column=None, partition_by=["dept"])
        assert result["op"] == "window_count"
        assert result["column"] is None

    def test_window_count_column(self):
        ctx = StubContext(platform="test", family="expression")
        result = ctx.window_count(column="order_id", partition_by=["customer_id"])
        assert result["column"] == "order_id"

    def test_window_min(self):
        ctx = StubContext(platform="test", family="expression")
        result = ctx.window_min(column="price", partition_by=["category"])
        assert result["op"] == "window_min"
        assert result["column"] == "price"

    def test_window_max(self):
        ctx = StubContext(platform="test", family="expression")
        result = ctx.window_max(column="quantity")
        assert result["op"] == "window_max"
        assert result["partition_by"] == []


class TestUnionAndRename:
    def test_union_all_two_frames(self):
        ctx = StubContext(platform="test", family="pandas")
        frame_a = {"col": [1, 2]}
        frame_b = {"col": [3, 4]}

        result = ctx.union_all(frame_a, frame_b)
        assert len(result) == 2
        assert result[0] is frame_a
        assert result[1] is frame_b

    def test_union_all_single_frame(self):
        ctx = StubContext(platform="test", family="pandas")
        frame = {"col": [1]}

        result = ctx.union_all(frame)
        assert len(result) == 1

    def test_union_all_multiple_frames(self):
        ctx = StubContext(platform="test", family="pandas")
        frames = [{"col": [i]} for i in range(5)]

        result = ctx.union_all(*frames)
        assert len(result) == 5

    def test_rename_columns(self):
        ctx = StubContext(platform="test", family="pandas")
        frame = {"old_name": [1, 2]}
        mapping = {"old_name": "new_name"}

        result = ctx.rename_columns(frame, mapping)
        assert result["mapping"] == {"old_name": "new_name"}
        assert result["df"] is frame


class TestScalarMethods:
    def test_scalar_extracts_first_value(self):
        ctx = StubContext(platform="test", family="pandas")
        frame = {"total": [42]}
        assert ctx.scalar(frame) == 42

    def test_scalar_with_column_name(self):
        ctx = StubContext(platform="test", family="pandas")
        frame = {"alpha": [10], "beta": [20]}
        assert ctx.scalar(frame, column="beta") == 20

    def test_scalar_from_non_dict_returns_none(self):
        ctx = StubContext(platform="test", family="pandas")
        assert ctx.scalar("not a dict") is None

    def test_scalar_to_df_single_column(self):
        ctx = StubContext(platform="test", family="pandas")
        result = ctx.scalar_to_df({"total": 100})
        assert result == {"total": [100]}

    def test_scalar_to_df_multiple_columns(self):
        ctx = StubContext(platform="test", family="pandas")
        result = ctx.scalar_to_df({"count": 5, "avg_price": 19.99})
        assert result == {"count": [5], "avg_price": [19.99]}


class TestProtocolCompliance:
    def test_stub_satisfies_protocol(self):
        ctx = StubContext(platform="test", family="expression")
        assert isinstance(ctx, DataFrameContext)

    def test_protocol_requires_all_methods(self):

        class IncompleteContext:
            def get_table(self, name):
                return None

        required_methods = [
            "get_table",
            "list_tables",
            "table_exists",
            "col",
            "lit",
            "date_sub",
            "date_add",
            "cast_date",
            "cast_string",
            "family",
        ]
        ctx = StubContext(platform="test", family="pandas")
        for method in required_methods:
            assert hasattr(ctx, method), f"Missing required method: {method}"


class TestFamilyProperty:
    def test_pandas_family_string(self):
        ctx = StubContext(platform="pandas", family="pandas")
        assert ctx.family == "pandas"

    def test_expression_family_string(self):
        ctx = StubContext(platform="polars", family="expression")
        assert ctx.family == "expression"

    def test_platform_property(self):
        ctx = StubContext(platform="dask", family="pandas")
        assert ctx.platform == "dask"


