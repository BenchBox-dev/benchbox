# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.dataframe.query import DataFrameQuery, QueryCategory
from benchbox.platforms.dataframe.expression_family import (
    ExpressionFamilyAdapter,
    ExpressionFamilyContext,
)
from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class MockExpressionAdapter(ExpressionFamilyAdapter[dict, dict, str]):
    @property
    def platform_name(self) -> str:
        return "MockExpression"

    def col(self, name: str) -> str:
        return f"col({name})"

    def lit(self, value: Any) -> str:
        return f"lit({value})"

    def date_sub(self, column: str, days: int) -> str:
        return f"date_sub({column}, {days})"

    def date_add(self, column: str, days: int) -> str:
        return f"date_add({column}, {days})"

    def cast_date(self, column: str) -> str:
        return f"cast_date({column})"

    def cast_string(self, column: str) -> str:
        return f"cast_string({column})"

    def _window_descriptor(
        self,
        op: str,
        *,
        column: str | None = None,
        order_by: list[tuple[str, bool]] | None = None,
        partition_by: list[str] | None = None,
    ) -> dict[str, Any]:
        descriptor: dict[str, Any] = {
            "op": op,
            "partition_by": partition_by or [],
        }
        if column is not None:
            descriptor["column"] = column
        if order_by is not None:
            descriptor["order_by"] = order_by
        return descriptor

    def window_rank(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._window_descriptor("window_rank", order_by=order_by, partition_by=partition_by)

    def window_row_number(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._window_descriptor("window_row_number", order_by=order_by, partition_by=partition_by)

    def window_dense_rank(
        self,
        order_by: list[tuple[str, bool]],
        partition_by: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._window_descriptor("window_dense_rank", order_by=order_by, partition_by=partition_by)

    def window_sum(
        self,
        column: str,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> dict[str, Any]:
        return self._window_descriptor("window_sum", column=column, partition_by=partition_by, order_by=order_by)

    def window_avg(
        self,
        column: str,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> dict[str, Any]:
        return self._window_descriptor("window_avg", column=column, partition_by=partition_by, order_by=order_by)

    def window_count(
        self,
        column: str | None = None,
        partition_by: list[str] | None = None,
        order_by: list[tuple[str, bool]] | None = None,
    ) -> dict[str, Any]:
        return self._window_descriptor("window_count", column=column, partition_by=partition_by, order_by=order_by)

    def window_min(
        self,
        column: str,
        partition_by: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._window_descriptor("window_min", column=column, partition_by=partition_by)

    def window_max(
        self,
        column: str,
        partition_by: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._window_descriptor("window_max", column=column, partition_by=partition_by)

    def union_all(self, *dataframes: Any) -> list[Any]:
        return list(dataframes)

    def rename_columns(self, df: Any, mapping: dict[str, str]) -> dict[str, Any]:
        return {"df": df, "mapping": mapping}

    def read_csv(
        self,
        path: Path,
        *,
        delimiter: str = ",",
        has_header: bool = True,
        column_names: list[str] | None = None,
        null_marker: str | None = None,
    ) -> dict:
        return {"type": "csv", "path": str(path), "delimiter": delimiter}

    def read_parquet(self, path: Path) -> dict:
        return {"type": "parquet", "path": str(path)}

    def collect(self, df: dict) -> dict:
        return df

    def get_row_count(self, df: dict) -> int:
        return df.get("rows", 10)

    def scalar(self, df: dict, column: str | None = None) -> Any:
        if "value" in df:
            return df["value"]
        return 42

    def _get_first_row(self, df: dict) -> tuple | None:
        return (1, "test", 100.0)

    def scalar_to_df(self, data: dict[str, Any]) -> dict:
        return {"type": "scalar_df", "data": data}


class TestExpressionFamilyContext:
    def test_context_creation(self):

        adapter = MockExpressionAdapter()
        ctx = adapter.create_context()

        assert isinstance(ctx, ExpressionFamilyContext)
        assert ctx.platform == "MockExpression"
        assert ctx.family == "expression"

    def test_context_col_delegates_to_adapter(self):
        adapter = MockExpressionAdapter()
        ctx = adapter.create_context()

        result = ctx.col("amount")
        assert result == "col(amount)"

    def test_context_lit_delegates_to_adapter(self):
        adapter = MockExpressionAdapter()
        ctx = adapter.create_context()

        result = ctx.lit(100)
        assert result == "lit(100)"

    def test_context_date_sub_delegates_to_adapter(self):
        adapter = MockExpressionAdapter()
        ctx = adapter.create_context()

        result = ctx.date_sub("col(date)", 7)
        assert result == "date_sub(col(date), 7)"

    def test_context_date_add_delegates_to_adapter(self):
        adapter = MockExpressionAdapter()
        ctx = adapter.create_context()

        result = ctx.date_add("col(date)", 30)
        assert result == "date_add(col(date), 30)"

    def test_context_cast_date_delegates_to_adapter(self):
        adapter = MockExpressionAdapter()
        ctx = adapter.create_context()

        result = ctx.cast_date("col(string_date)")
        assert result == "cast_date(col(string_date))"

    def test_context_cast_string_delegates_to_adapter(self):
        adapter = MockExpressionAdapter()
        ctx = adapter.create_context()

        result = ctx.cast_string("col(number)")
        assert result == "cast_string(col(number))"

    def test_context_table_registration(self):

        adapter = MockExpressionAdapter()
        ctx = adapter.create_context()

        test_df = {"data": [1, 2, 3]}
        ctx.register_table("orders", test_df)

        assert ctx.table_exists("orders")
        result = ctx.get_table("orders")
        assert isinstance(result, UnifiedLazyFrame)
        assert result.native == test_df

    def test_context_list_tables(self):

        adapter = MockExpressionAdapter()
        ctx = adapter.create_context()

        ctx.register_table("orders", {})
        ctx.register_table("customers", {})

        tables = ctx.list_tables()
        assert "orders" in tables
        assert "customers" in tables


class TestExpressionFamilyAdapter:
    def test_adapter_initialization(self):

        adapter = MockExpressionAdapter()

        assert adapter.platform_name == "MockExpression"
        assert adapter.family == "expression"
        assert adapter.verbose is False
        assert adapter.table_mode == "native"
        assert adapter.platform_config == {}

    def test_adapter_initialization_with_options(self):

        adapter = MockExpressionAdapter(
            working_dir="/tmp/test",
            verbose=True,
            very_verbose=True,
        )

        assert adapter.working_dir == Path("/tmp/test")
        assert adapter.verbose is True
        assert adapter.very_verbose is True

    def test_create_context(self):

        adapter = MockExpressionAdapter()
        ctx = adapter.create_context()

        assert isinstance(ctx, ExpressionFamilyContext)

    def test_get_context_creates_if_missing(self):

        adapter = MockExpressionAdapter()

        ctx = adapter.get_context()

        assert ctx is not None
        assert adapter._context == ctx

    def test_get_context_returns_existing(self):

        adapter = MockExpressionAdapter()

        ctx1 = adapter.create_context()
        ctx2 = adapter.get_context()

        assert ctx1 is ctx2

    def test_detect_format_parquet(self):

        adapter = MockExpressionAdapter()

        assert adapter._detect_format(Path("data.parquet")) == "parquet"
        assert adapter._detect_format(Path("/path/to/file.parquet")) == "parquet"

    def test_detect_format_tbl(self):

        adapter = MockExpressionAdapter()

        assert adapter._detect_format(Path("lineitem.tbl")) == "tbl"
        assert adapter._detect_format(Path("/tpch/orders.tbl")) == "tbl"

    def test_detect_format_csv(self):

        adapter = MockExpressionAdapter()

        assert adapter._detect_format(Path("data.csv")) == "csv"
        assert adapter._detect_format(Path("file.txt")) == "csv"

    def test_execute_query_success(self):

        adapter = MockExpressionAdapter()
        ctx = adapter.create_context()

        ctx.register_table("orders", {"rows": 100})

        def expression_impl(ctx):
            return ctx.get_table("orders")

        query = DataFrameQuery(
            query_id="Q1",
            query_name="Test Query",
            description="A test query",
            categories=[QueryCategory.SCAN],
            expression_impl=expression_impl,
        )

        result = adapter.execute_query(ctx, query)

        assert result["query_id"] == "Q1"
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 100
        assert "execution_time_seconds" in result

    def test_execute_query_timing_key_matches_schema_contract(self):
        adapter = MockExpressionAdapter()
        ctx = adapter.create_context()

        ctx.register_table("orders", {"rows": 100})

        def expression_impl(ctx):
            return ctx.get_table("orders")

        query = DataFrameQuery(
            query_id="Q1",
            query_name="Timing Contract Test",
            description="Verify timing key contract",
            expression_impl=expression_impl,
        )

        result = adapter.execute_query(ctx, query)

        assert "execution_time_seconds" in result
        assert "execution_time" not in result
        assert "execution_time_ms" not in result
        assert result["execution_time_seconds"] >= 0.0

    def test_execute_query_failure(self):

        adapter = MockExpressionAdapter()
        ctx = adapter.create_context()

        def failing_impl(ctx):
            raise ValueError("Test error")

        query = DataFrameQuery(
            query_id="Q2",
            query_name="Failing Query",
            description="A failing query",
            expression_impl=failing_impl,
        )

        result = adapter.execute_query(ctx, query)

        assert result["query_id"] == "Q2"
        assert result["status"] == "FAILED"
        assert "Test error" in result["error"]

    def test_execute_query_no_impl(self):

        adapter = MockExpressionAdapter()
        ctx = adapter.create_context()

        def pandas_impl(ctx):
            return ctx.get_table("orders")

        query = DataFrameQuery(
            query_id="Q3",
            query_name="Pandas Only",
            description="Pandas-only query",
            pandas_impl=pandas_impl,
        )

        result = adapter.execute_query(ctx, query)

        assert result["status"] == "FAILED"
        assert "no expression implementation" in result["error"]

    def test_load_table_with_parquet(self, tmp_path):

        adapter = MockExpressionAdapter()
        ctx = adapter.create_context()

        parquet_file = tmp_path / "orders.parquet"
        parquet_file.touch()

        row_count = adapter.load_table(ctx, "orders", [parquet_file])

        assert ctx.table_exists("orders")
        assert row_count == 10

    def test_load_table_no_files_raises(self):

        adapter = MockExpressionAdapter()
        ctx = adapter.create_context()

        with pytest.raises(ValueError, match="No files provided"):
            adapter.load_table(ctx, "orders", [])

    def test_load_tables_from_data_source_uses_default_loading_contract(self, tmp_path):
        adapter = MockExpressionAdapter()
        ctx = adapter.create_context()
        data_file = tmp_path / "orders.tbl"
        data_file.write_text("1|2|\n")

        with patch("benchbox.platforms.base.data_loading.DataSourceResolver") as mock_cls:
            mock_resolver = MagicMock()
            mock_cls.return_value = mock_resolver
            mock_resolver.resolve.return_value = SimpleNamespace(tables={"orders": [data_file]}, table_formats={})

            stats = adapter.load_tables_from_data_source(ctx, tmp_path)

        assert stats == {"orders": 10}
        assert mock_cls.call_args.kwargs == {
            "platform_name": "MockExpression",
            "table_mode": "native",
            "platform_config": {},
            "requested_format": None,
        }


class TestExpressionFamilyAdapterAbstract:
    def test_abstract_methods_required(self):

        class IncompleteAdapter(ExpressionFamilyAdapter):
            @property
            def platform_name(self) -> str:
                return "Incomplete"

        with pytest.raises(TypeError, match="abstract"):
            IncompleteAdapter()


class TestQueryIntegration:
    def test_query_with_table_access(self):

        adapter = MockExpressionAdapter()
        ctx = adapter.create_context()

        ctx.register_table("orders", {"rows": 50, "data": "test"})
        ctx.register_table("customers", {"rows": 10, "data": "cust"})

        def join_impl(ctx):
            orders = ctx.get_table("orders")
            _ = ctx.get_table("customers")
            return {"rows": orders.native["rows"], "joined": True}

        query = DataFrameQuery(
            query_id="JOIN1",
            query_name="Join Query",
            description="Join orders and customers",
            categories=[QueryCategory.JOIN],
            expression_impl=join_impl,
        )

        result = adapter.execute_query(ctx, query)

        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 50

    def test_query_with_expression_helpers(self):

        adapter = MockExpressionAdapter()
        ctx = adapter.create_context()

        ctx.register_table("orders", {"rows": 100})

        def filter_impl(ctx):
            amount_col = ctx.col("amount")
            threshold = ctx.lit(100)
            return {"rows": 75, "filter": f"{amount_col} > {threshold}"}

        query = DataFrameQuery(
            query_id="FILTER1",
            query_name="Filter Query",
            description="Filter by amount",
            categories=[QueryCategory.FILTER],
            expression_impl=filter_impl,
        )

        result = adapter.execute_query(ctx, query)

        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 75


