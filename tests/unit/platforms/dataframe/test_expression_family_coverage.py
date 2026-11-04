from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from benchbox.core.dataframe.query import DataFrameQuery
from benchbox.platforms.dataframe.expression_family import ExpressionFamilyAdapter
from benchbox.platforms.dataframe.unified_frame import UnifiedExpr

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class CoverageExpressionAdapter(ExpressionFamilyAdapter[dict, dict, str]):
    table_mode = "native"
    config = None

    @property
    def platform_name(self) -> str:
        return getattr(self, "_platform_name", "Polars")

    def col(self, name: str) -> str:
        return f"col({name})"

    def lit(self, value: Any) -> str:
        return f"lit({value})"

    def date_sub(self, column: str, days: int) -> str:
        return f"date_sub({column},{days})"

    def date_add(self, column: str, days: int) -> str:
        return f"date_add({column},{days})"

    def cast_date(self, column: str) -> str:
        return f"cast_date({column})"

    def cast_string(self, column: str) -> str:
        return f"cast_string({column})"

    def read_csv(
        self,
        path: Path,
        *,
        delimiter: str = ",",
        has_header: bool = True,
        column_names: list[str] | None = None,
        null_marker: str | None = None,
    ) -> dict:
        return {"kind": "csv", "path": str(path), "delimiter": delimiter, "header": has_header, "cols": column_names}

    def read_parquet(self, path: Path) -> dict:
        return {"kind": "parquet", "path": str(path)}

    def collect(self, df: dict) -> dict:
        return df

    def get_row_count(self, df: dict) -> int:
        return int(df.get("rows", 1))

    def scalar(self, df: dict, column: str | None = None) -> Any:
        return df.get("value", 1)

    def scalar_to_df(self, data: dict[str, Any]) -> dict:
        return {"rows": 1, "first": tuple(data.values())}

    def window_rank(self, order_by, partition_by=None):
        return "window_rank"

    def window_row_number(self, order_by, partition_by=None):
        return "window_row_number"

    def window_dense_rank(self, order_by, partition_by=None):
        return "window_dense_rank"

    def window_sum(self, column, partition_by=None, order_by=None):
        return "window_sum"

    def window_avg(self, column, partition_by=None, order_by=None):
        return "window_avg"

    def window_count(self, column=None, partition_by=None, order_by=None):
        return "window_count"

    def window_min(self, column, partition_by=None):
        return "window_min"

    def window_max(self, column, partition_by=None):
        return "window_max"

    def union_all(self, *dataframes):
        return {"union": list(dataframes)}

    def rename_columns(self, df, mapping):
        return {"df": df, "mapping": mapping}


class TestExpressionFamilyCoverage:
    def test_context_when_paths_pyspark_and_datafusion(self):
        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()

        adapter._platform_name = "PySpark"
        when_expr = ctx.when(True)
        assert when_expr is not None

        adapter._platform_name = "DataFusion"
        when_expr = ctx.when(True)
        assert when_expr is not None

    def test_context_helpers_unified_expr_and_scalar(self):
        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()

        wrapped = ctx.lit("x")
        assert isinstance(wrapped, UnifiedExpr)
        same = ctx.lit(wrapped)
        assert same is wrapped

        scalar = ctx.scalar({"value": 99})
        assert scalar == 99

        scalar_df = ctx.scalar_to_df({"a": 1, "b": 2})
        assert scalar_df.native["rows"] == 1

    def test_load_tables_from_data_source_and_csv_parquet_paths(self, tmp_path, monkeypatch):
        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()

        f1 = tmp_path / "orders.tbl"
        f2 = tmp_path / "lineitem.parquet"
        f1.write_text("1|2|\n")
        f2.write_text("x")

        class FakeSource:
            tables = {
                "ORDERS": [f1],
                "LINEITEM": [f2],
                "MISSING": [tmp_path / "missing.tbl"],
            }

        class FakeResolver:
            def resolve(self, benchmark, data_dir):
                return FakeSource()

        monkeypatch.setattr("benchbox.platforms.base.data_loading.DataSourceResolver", lambda **_: FakeResolver())

        schema_info = {
            "orders": {"columns": [{"name": "o_orderkey"}, {"name": "o_custkey"}]},
            "lineitem": {"columns": [{"name": "l_orderkey"}]},
        }

        stats = adapter.load_tables_from_data_source(ctx, tmp_path, schema_info=schema_info)

        assert stats["orders"] == 1
        assert stats["lineitem"] == 1
        assert "missing" not in stats

    def test_plan_capture_failure_records_real_cause_and_warns_once(self, monkeypatch, caplog):

        import logging

        import benchbox.platforms.dataframe.expression_family as ef

        def _boom(_df, _platform):
            raise RuntimeError("capture exploded")

        monkeypatch.setattr(ef, "capture_query_plan", _boom)

        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()

        q_a = DataFrameQuery(query_id="QA", query_name="a", description="a", expression_impl=lambda c: {"rows": 1})
        q_b = DataFrameQuery(query_id="QB", query_name="b", description="b", expression_impl=lambda c: {"rows": 1})

        with caplog.at_level(logging.WARNING, logger="benchbox.platforms.dataframe.expression_family"):
            result_a, _ = adapter.execute_query_profiled(ctx, q_a, track_memory=False, capture_plan=True)
            result_b, _ = adapter.execute_query_profiled(ctx, q_b, track_memory=False, capture_plan=True)

        assert result_a["status"] == "SUCCESS"

        assert result_a["plan_capture_error"] == "capture exploded"
        assert result_b["plan_capture_error"] == "capture exploded"

        assert [e["query_id"] for e in adapter.plan_capture_errors] == ["QA", "QB"]
        assert all(e["error"] == "capture exploded" for e in adapter.plan_capture_errors)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "capture exploded" in r.getMessage()]
        assert len(warnings) == 1

    def test_execute_query_profiled_success_and_failure(self):
        adapter = CoverageExpressionAdapter(verbose=True)
        ctx = adapter.create_context()
        ctx.register_table("orders", {"rows": 3, "first": (1,)})

        ok_query = DataFrameQuery(
            query_id="Q1", query_name="ok", description="ok", expression_impl=lambda c: {"rows": 3}
        )
        result, profile = adapter.execute_query_profiled(ctx, ok_query, track_memory=False, capture_plan=False)
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 3
        assert profile.query_id == "Q1"

        bad_query = DataFrameQuery(
            query_id="Q2",
            query_name="bad",
            description="bad",
            expression_impl=lambda c: (_ for _ in ()).throw(ValueError("boom")),
        )
        result, profile = adapter.execute_query_profiled(ctx, bad_query, track_memory=False, capture_plan=False)
        assert result["status"] == "FAILED"
        assert "boom" in result["error"]
        assert profile.query_id == "Q2"

    def test_concat_and_coalesce_polars_path(self):
        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()

        out = ctx.concat([{"rows": 1}, {"rows": 2}])
        assert out.native["rows"] == 1

        expr = ctx.coalesce(ctx.col("a"), "b", 1)
        assert isinstance(expr, UnifiedExpr)


class TestContextWindowDelegates:
    def test_window_delegates_all(self):
        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()

        assert ctx.window_rank([("a", True)]) == "window_rank"
        assert ctx.window_row_number([("a", True)]) == "window_row_number"
        assert ctx.window_dense_rank([("a", True)]) == "window_dense_rank"
        assert ctx.window_sum("val") == "window_sum"
        assert ctx.window_avg("val") == "window_avg"
        assert ctx.window_count() == "window_count"
        assert ctx.window_min("val") == "window_min"
        assert ctx.window_max("val") == "window_max"

    def test_window_with_partition_by(self):
        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()

        assert ctx.window_sum("val", partition_by=["grp"]) == "window_sum"
        assert ctx.window_avg("val", partition_by=["grp"]) == "window_avg"
        assert ctx.window_count("val", partition_by=["grp"]) == "window_count"
        assert ctx.window_min("val", partition_by=["grp"]) == "window_min"
        assert ctx.window_max("val", partition_by=["grp"]) == "window_max"

    def test_union_all_and_rename(self):
        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()

        result = ctx.union_all({"rows": 1}, {"rows": 2})
        assert result["union"] == [{"rows": 1}, {"rows": 2}]

        renamed = ctx.rename_columns({"rows": 1}, {"a": "b"})
        assert renamed["mapping"] == {"a": "b"}


class TestContextStandaloneAggFunctions:
    def test_count_no_column(self):
        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()

        expr = ctx.count()
        assert isinstance(expr, UnifiedExpr)

    def test_count_with_column(self):
        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()
        expr = ctx.count("val")
        assert isinstance(expr, UnifiedExpr)

    def test_len(self):
        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()
        expr = ctx.len()
        assert isinstance(expr, UnifiedExpr)

    def test_sum(self):
        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()
        expr = ctx.sum("amount")
        assert isinstance(expr, UnifiedExpr)

    def test_mean(self):
        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()
        expr = ctx.mean("amount")
        assert isinstance(expr, UnifiedExpr)

    def test_std(self):
        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()
        expr = ctx.std("amount")
        assert isinstance(expr, UnifiedExpr)

    def test_max_(self):
        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()
        expr = ctx.max_("amount")
        assert isinstance(expr, UnifiedExpr)

    def test_coalesce_string_columns_polars_path(self):
        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()

        expr = ctx.coalesce(ctx.col("a"), "b", ctx.lit(0))
        assert isinstance(expr, UnifiedExpr)

    def test_create_dataframe_polars_path(self):
        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()
        from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame

        result = ctx.create_dataframe({"x": [1, 2, 3], "y": [4, 5, 6]})
        assert isinstance(result, UnifiedLazyFrame)


class TestContextElementAndStructAndMap:
    def test_element_delegates_to_adapter(self):

        class ElementAdapter(CoverageExpressionAdapter):
            def element(self):
                return "element_expr"

        adapter = ElementAdapter()
        ctx = adapter.create_context()
        result = ctx.element()
        assert isinstance(result, UnifiedExpr)

    def test_struct_polars_path(self):
        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()

        result = ctx.struct("col_a", ctx.col("col_b"))
        assert isinstance(result, UnifiedExpr)

    def test_map_from_arrays_polars_raises(self):
        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()
        with pytest.raises(NotImplementedError, match="Polars"):
            ctx.map_from_arrays("col_a", "col_b")

    def test_map_from_arrays_unified_expr_input(self):
        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()
        with pytest.raises(NotImplementedError, match="Polars"):
            ctx.map_from_arrays(ctx.col("col_a"), ctx.col("col_b"))


class TestAdapterHelperMethods:
    def test_build_ctas_sort_sql_returns_none(self):
        adapter = CoverageExpressionAdapter()
        result = adapter._build_ctas_sort_sql("orders", ["col_a"])
        assert result is None

    def test_log_very_verbose_no_op_when_off(self):
        adapter = CoverageExpressionAdapter(very_verbose=False)

        adapter._log_very_verbose("should not log")

    def test_log_very_verbose_when_on(self):
        adapter = CoverageExpressionAdapter(very_verbose=True)

        adapter._log_very_verbose("verbose message")

    def test_concat_dataframes_single_returns_same(self):
        adapter = CoverageExpressionAdapter()
        df = {"rows": 5}
        result = adapter._concat_dataframes([df])
        assert result is df

    def test_concat_dataframes_multiple_warns_returns_first(self):
        adapter = CoverageExpressionAdapter()
        df1 = {"rows": 5}
        df2 = {"rows": 10}
        result = adapter._concat_dataframes([df1, df2])
        assert result is df1

    def test_load_parquet_files_multi(self, tmp_path):
        adapter = CoverageExpressionAdapter()
        f1 = tmp_path / "a.parquet"
        f2 = tmp_path / "b.parquet"
        f1.touch()
        f2.touch()
        result = adapter._load_parquet_files([f1, f2])

        assert result is not None

    def test_load_csv_files_multi(self, tmp_path):
        adapter = CoverageExpressionAdapter()
        f1 = tmp_path / "a.tbl"
        f2 = tmp_path / "b.tbl"
        f1.write_text("1|2|\n")
        f2.write_text("3|4|\n")
        result = adapter._load_csv_files([f1, f2], delimiter="|", has_header=False, column_names=None)
        assert result is not None

    def test_execute_query_with_collect_branch(self):

        class CollectableDF:
            def collect(self):
                return self

        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()

        def impl(c):
            return CollectableDF()

        from benchbox.core.dataframe.query import DataFrameQuery

        q = DataFrameQuery(query_id="Q_COLLECT", query_name="q", description="d", expression_impl=impl)
        result = adapter.execute_query(ctx, q)

        assert result["query_id"] == "Q_COLLECT"

    def test_element_not_implemented_on_base_adapter(self):
        adapter = CoverageExpressionAdapter()
        with pytest.raises(NotImplementedError, match="element"):
            adapter.element()

    def test_window_lag_not_implemented(self):
        adapter = CoverageExpressionAdapter()
        with pytest.raises(NotImplementedError, match="window_lag"):
            adapter.window_lag("col_a")

    def test_window_lead_not_implemented(self):
        adapter = CoverageExpressionAdapter()
        with pytest.raises(NotImplementedError, match="window_lead"):
            adapter.window_lead("col_a")

    def test_window_ntile_not_implemented(self):
        adapter = CoverageExpressionAdapter()
        with pytest.raises(NotImplementedError, match="window_ntile"):
            adapter.window_ntile(4, [("a", True)])

    def test_window_percent_rank_not_implemented(self):
        adapter = CoverageExpressionAdapter()
        with pytest.raises(NotImplementedError, match="window_percent_rank"):
            adapter.window_percent_rank([("a", True)])

    def test_window_cume_dist_not_implemented(self):
        adapter = CoverageExpressionAdapter()
        with pytest.raises(NotImplementedError, match="window_cume_dist"):
            adapter.window_cume_dist([("a", True)])


class TestContextWhenPolarsPath:
    def test_when_polars_default_path(self):

        from benchbox.platforms.dataframe.unified_frame import UnifiedWhen

        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()

        result = ctx.when(True)
        assert isinstance(result, UnifiedWhen)

    def test_when_with_unified_expr_condition(self):

        from benchbox.platforms.dataframe.unified_frame import UnifiedExpr, UnifiedWhen

        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()

        cond_expr = UnifiedExpr(True)
        result = ctx.when(cond_expr)
        assert isinstance(result, UnifiedWhen)


class TestContextWindowLagLeadNtileDelegates:
    def _make_adapter_with_window_support(self):
        class WindowAdapter(CoverageExpressionAdapter):
            def window_lag(self, column, offset=1, partition_by=None, order_by=None):
                return f"lag({column},{offset})"

            def window_lead(self, column, offset=1, partition_by=None, order_by=None):
                return f"lead({column},{offset})"

            def window_ntile(self, n, order_by, partition_by=None):
                return f"ntile({n})"

            def window_percent_rank(self, order_by, partition_by=None):
                return "percent_rank"

            def window_cume_dist(self, order_by, partition_by=None):
                return "cume_dist"

        return WindowAdapter()

    def test_context_window_lag_delegates(self):
        adapter = self._make_adapter_with_window_support()
        ctx = adapter.create_context()
        result = ctx.window_lag("val", offset=2)
        assert result == "lag(val,2)"

    def test_context_window_lead_delegates(self):
        adapter = self._make_adapter_with_window_support()
        ctx = adapter.create_context()
        result = ctx.window_lead("val", offset=1)
        assert result == "lead(val,1)"

    def test_context_window_ntile_delegates(self):
        adapter = self._make_adapter_with_window_support()
        ctx = adapter.create_context()
        result = ctx.window_ntile(4, [("a", True)])
        assert result == "ntile(4)"

    def test_context_window_percent_rank_delegates(self):
        adapter = self._make_adapter_with_window_support()
        ctx = adapter.create_context()
        result = ctx.window_percent_rank([("a", True)])
        assert result == "percent_rank"

    def test_context_window_cume_dist_delegates(self):
        adapter = self._make_adapter_with_window_support()
        ctx = adapter.create_context()
        result = ctx.window_cume_dist([("a", True)])
        assert result == "cume_dist"


class TestContextStandaloneAggsPolarsWithMockedPlatform:
    def _make_mock_adapter(self, platform: str):

        class MockPlatformAdapter(CoverageExpressionAdapter):
            @property
            def platform_name(self):
                return platform

        return MockPlatformAdapter()

    def test_count_no_col_mock_pyspark(self, monkeypatch):
        import sys
        from types import ModuleType

        mock_f = MagicMock()
        mock_f.count.return_value = "spark_count"
        mock_f.lit.return_value = "spark_lit_1"
        pyspark_mod = ModuleType("pyspark")
        pyspark_sql_mod = ModuleType("pyspark.sql")
        pyspark_func_mod = ModuleType("pyspark.sql.functions")
        pyspark_func_mod.count = mock_f.count
        pyspark_func_mod.lit = mock_f.lit
        monkeypatch.setitem(sys.modules, "pyspark", pyspark_mod)
        monkeypatch.setitem(sys.modules, "pyspark.sql", pyspark_sql_mod)
        monkeypatch.setitem(sys.modules, "pyspark.sql.functions", pyspark_func_mod)

        adapter = self._make_mock_adapter("PySpark")
        ctx = adapter.create_context()
        expr = ctx.count()
        from benchbox.platforms.dataframe.unified_frame import UnifiedExpr

        assert isinstance(expr, UnifiedExpr)

    def test_count_with_col_mock_pyspark(self, monkeypatch):
        import sys
        from types import ModuleType

        mock_f = MagicMock()
        mock_f.count.return_value = "spark_count_col"
        pyspark_mod = ModuleType("pyspark")
        pyspark_sql_mod = ModuleType("pyspark.sql")
        pyspark_func_mod = ModuleType("pyspark.sql.functions")
        pyspark_func_mod.count = mock_f.count
        pyspark_func_mod.lit = MagicMock(return_value="lit")
        monkeypatch.setitem(sys.modules, "pyspark", pyspark_mod)
        monkeypatch.setitem(sys.modules, "pyspark.sql", pyspark_sql_mod)
        monkeypatch.setitem(sys.modules, "pyspark.sql.functions", pyspark_func_mod)

        adapter = self._make_mock_adapter("PySpark")
        ctx = adapter.create_context()
        expr = ctx.count("amount")
        from benchbox.platforms.dataframe.unified_frame import UnifiedExpr

        assert isinstance(expr, UnifiedExpr)


class TestExecuteQueryProfiledWithMemory:
    def test_profiled_with_memory_tracking(self):
        adapter = CoverageExpressionAdapter(verbose=True)
        ctx = adapter.create_context()
        ctx.register_table("orders", {"rows": 5, "first": (1,)})

        from benchbox.core.dataframe.query import DataFrameQuery

        q = DataFrameQuery(
            query_id="MEM1",
            query_name="mem_test",
            description="memory tracking",
            expression_impl=lambda c: {"rows": 5},
        )

        result, profile = adapter.execute_query_profiled(ctx, q, track_memory=True, capture_plan=False)
        assert result["status"] == "SUCCESS"
        assert profile.query_id == "MEM1"

    def test_profiled_no_impl_raises(self):

        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()

        from benchbox.core.dataframe.query import DataFrameQuery

        q = DataFrameQuery(
            query_id="NOIMPL",
            query_name="no_impl",
            description="no expr impl",
            pandas_impl=lambda c: None,
        )
        result, profile = adapter.execute_query_profiled(ctx, q, track_memory=False, capture_plan=False)
        assert result["status"] == "FAILED"
        assert "no expression implementation" in result["error"]

    def test_profiled_failure_stops_memory_tracker(self):

        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()

        from benchbox.core.dataframe.query import DataFrameQuery

        q = DataFrameQuery(
            query_id="ERRTRACK",
            query_name="error_memory",
            description="error with memory tracking",
            expression_impl=lambda c: (_ for _ in ()).throw(RuntimeError("fail")),
        )
        result, profile = adapter.execute_query_profiled(ctx, q, track_memory=True, capture_plan=False)
        assert result["status"] == "FAILED"
        assert "fail" in result["error"]

    def test_profiled_with_capture_plan_enabled(self):

        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()
        ctx.register_table("data", {"rows": 2})

        from benchbox.core.dataframe.query import DataFrameQuery

        q = DataFrameQuery(
            query_id="PLAN1",
            query_name="plan_capture",
            description="plan",
            expression_impl=lambda c: {"rows": 2},
        )
        result, profile = adapter.execute_query_profiled(ctx, q, track_memory=False, capture_plan=True)
        assert result["status"] == "SUCCESS"

    def test_profiled_with_unified_lazy_frame_result(self):

        adapter = CoverageExpressionAdapter()
        ctx = adapter.create_context()
        ctx.register_table("data", {"rows": 3})

        from benchbox.core.dataframe.query import DataFrameQuery
        from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame

        def impl(c):
            return UnifiedLazyFrame({"rows": 3}, adapter)

        q = DataFrameQuery(
            query_id="ULF1",
            query_name="ulf_unwrap",
            description="ulf",
            expression_impl=impl,
        )
        result, profile = adapter.execute_query_profiled(ctx, q, track_memory=False, capture_plan=False)
        assert result["status"] == "SUCCESS"
