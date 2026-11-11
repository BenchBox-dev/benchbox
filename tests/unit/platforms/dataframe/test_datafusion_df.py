# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.core.dataframe.query import DataFrameQuery, QueryCategory

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


try:
    import datafusion
    import pyarrow as pa

    from benchbox.platforms.dataframe.datafusion_df import (
        DATAFUSION_DF_AVAILABLE,
        DataFusionDataFrameAdapter,
    )
except ImportError:
    DATAFUSION_DF_AVAILABLE = False
    datafusion = None
    pa = None


@pytest.mark.skipif(not DATAFUSION_DF_AVAILABLE, reason="DataFusion not installed")
class TestDataFusionDataFrameAdapter:
    def test_initialization(self):

        adapter = DataFusionDataFrameAdapter()

        assert adapter.platform_name == "DataFusion"
        assert adapter.family == "expression"
        info = adapter.get_platform_info()
        assert info["repartition_joins"] is True
        assert info["parquet_pushdown"] is True
        assert info["batch_size"] == 8192

    def test_initialization_with_options(self):

        adapter = DataFusionDataFrameAdapter(
            working_dir="/tmp/datafusion",
            verbose=True,
            target_partitions=4,
            repartition_joins=False,
            parquet_pushdown=False,
            batch_size=4096,
        )

        assert adapter.working_dir == Path("/tmp/datafusion")
        assert adapter.verbose is True
        info = adapter.get_platform_info()
        assert info["target_partitions"] == 4
        assert info["repartition_joins"] is False
        assert info["parquet_pushdown"] is False
        assert info["batch_size"] == 4096

    def test_platform_info(self):

        adapter = DataFusionDataFrameAdapter()

        info = adapter.get_platform_info()

        assert info["platform"] == "DataFusion"
        assert info["family"] == "expression"
        assert "version" in info
        assert "target_partitions" in info

    def test_create_context(self):

        adapter = DataFusionDataFrameAdapter()
        ctx = adapter.create_context()

        assert ctx is not None
        assert ctx.platform == "DataFusion"
        assert ctx.family == "expression"


@pytest.mark.skipif(not DATAFUSION_DF_AVAILABLE, reason="DataFusion not installed")
class TestDataFusionExpressionMethods:
    def test_col(self):
        adapter = DataFusionDataFrameAdapter()

        expr = adapter.col("amount")

        assert expr is not None
        assert hasattr(expr, "alias")

    def test_lit_integer(self):
        adapter = DataFusionDataFrameAdapter()

        expr = adapter.lit(100)

        assert expr is not None

    def test_lit_string(self):
        adapter = DataFusionDataFrameAdapter()

        expr = adapter.lit("test")

        assert expr is not None

    def test_lit_float(self):
        adapter = DataFusionDataFrameAdapter()

        expr = adapter.lit(3.14)

        assert expr is not None

    def test_cast_date(self):
        adapter = DataFusionDataFrameAdapter()

        expr = adapter.cast_date(adapter.col("date_str"))

        assert expr is not None

    def test_cast_string(self):
        adapter = DataFusionDataFrameAdapter()

        expr = adapter.cast_string(adapter.col("number"))

        assert expr is not None

    def test_date_sub(self):
        adapter = DataFusionDataFrameAdapter()

        expr = adapter.date_sub(adapter.col("date"), 7)

        assert expr is not None

    def test_date_add(self):
        adapter = DataFusionDataFrameAdapter()

        expr = adapter.date_add(adapter.col("date"), 30)

        assert expr is not None


@pytest.mark.skipif(not DATAFUSION_DF_AVAILABLE, reason="DataFusion not installed")
class TestDataFusionDataLoading:
    def test_read_csv_basic(self, tmp_path):

        adapter = DataFusionDataFrameAdapter()

        csv_path = tmp_path / "test.csv"
        csv_path.write_text("id,name,amount\n1,Alice,100\n2,Bob,200\n")

        df = adapter.read_csv(csv_path)

        result = adapter.collect(df)
        assert isinstance(result, pa.Table)
        assert result.num_rows == 2
        assert result.num_columns == 3

    def test_read_csv_with_delimiter(self, tmp_path):

        adapter = DataFusionDataFrameAdapter()

        csv_path = tmp_path / "test.csv"
        csv_path.write_text("id|name|amount\n1|Alice|100\n2|Bob|200\n")

        df = adapter.read_csv(csv_path, delimiter="|")

        result = adapter.collect(df)
        assert result.num_rows == 2

    def test_read_csv_headerless_with_schema_string_columns_does_not_raise(self, tmp_path):
        adapter = DataFusionDataFrameAdapter()

        csv_path = tmp_path / "hits.csv"
        csv_path.write_text("1,Alice\n2,\n")

        df = adapter.read_csv(
            csv_path,
            has_header=False,
            null_marker=None,
            string_columns=["url"],
        )

        result = adapter.collect(df)
        assert result.num_rows == 2

    def test_read_csv_coalesces_present_string_columns_to_empty(self, tmp_path):
        adapter = DataFusionDataFrameAdapter()

        csv_path = tmp_path / "with_header.csv"
        csv_path.write_text("id,name\n1,Alice\n2,\n")

        df = adapter.read_csv(
            csv_path,
            has_header=True,
            null_marker=None,
            string_columns=["name"],
        )

        result = adapter.collect(df)
        names = result.column("name").to_pylist()
        assert "" in names
        assert None not in names

    def test_read_csv_headerless_with_column_names_coalesces_declared_string_columns(self, tmp_path):
        adapter = DataFusionDataFrameAdapter()

        csv_path = tmp_path / "hits.csv"
        csv_path.write_text("1,Alice\n2,\n")

        df = adapter.read_csv(
            csv_path,
            has_header=False,
            column_names=["id", "search_phrase"],
            null_marker=None,
            string_columns=["search_phrase"],
        )

        result = adapter.collect(df)
        assert result.column_names == ["id", "search_phrase"]
        phrases = result.column("search_phrase").to_pylist()
        assert phrases == ["Alice", ""]
        assert None not in phrases

    def test_read_tbl_with_column_names(self, tmp_path):
        adapter = DataFusionDataFrameAdapter()

        tbl_path = tmp_path / "test.tbl"
        tbl_path.write_text("1|Alice|\n2|Bob|\n")

        df = adapter.read_csv(
            tbl_path,
            delimiter="|",
            has_header=False,
            column_names=["id", "name"],
        )

        result = adapter.collect(df)
        assert result.num_rows == 2
        assert result.column_names == ["id", "name"]

    def test_read_parquet(self, tmp_path):

        adapter = DataFusionDataFrameAdapter()

        parquet_path = tmp_path / "test.parquet"
        test_table = pa.table(
            {
                "id": [1, 2, 3],
                "name": ["A", "B", "C"],
                "amount": [100.0, 200.0, 300.0],
            }
        )
        import pyarrow.parquet as pq

        pq.write_table(test_table, parquet_path)

        df = adapter.read_parquet(parquet_path)

        result = adapter.collect(df)
        assert isinstance(result, pa.Table)
        assert result.num_rows == 3

    def test_collect_dataframe(self):

        adapter = DataFusionDataFrameAdapter()

        adapter.register_table("test", pa.table({"a": [1, 2, 3]}))
        df = adapter.session_ctx.table("test")

        result = adapter.collect(df)

        assert isinstance(result, pa.Table)
        assert result.num_rows == 3

    def test_collect_empty_dataframe(self):

        adapter = DataFusionDataFrameAdapter()

        adapter.register_table("test", pa.table({"a": [1, 2, 3]}))
        df = adapter.sql("SELECT * FROM test WHERE a > 10")

        result = adapter.collect(df)

        assert isinstance(result, pa.Table)
        assert result.num_rows == 0
        assert result.num_columns == 0

    def test_get_row_count_dataframe(self):

        adapter = DataFusionDataFrameAdapter()

        adapter.register_table("test", pa.table({"a": [1, 2, 3, 4, 5]}))
        df = adapter.session_ctx.table("test")

        count = adapter.get_row_count(df)

        assert count == 5

    def test_get_row_count_table(self):

        adapter = DataFusionDataFrameAdapter()

        table = pa.table({"a": [1, 2, 3]})
        count = adapter.get_row_count(table)

        assert count == 3


@pytest.mark.skipif(not DATAFUSION_DF_AVAILABLE, reason="DataFusion not installed")
class TestDataFusionWindowFunctions:
    def test_window_row_number(self):

        adapter = DataFusionDataFrameAdapter()

        expr = adapter.window_row_number(
            order_by=[("value", True)],
            partition_by=["category"],
        )

        assert expr is not None
        adapter.register_table("test", pa.table({"category": ["A", "B"], "value": [1, 2]}))
        df = adapter.session_ctx.table("test")
        result = df.select(adapter.col("category"), adapter.col("value"), expr.alias("rn"))
        collected = adapter.collect(result)
        assert "rn" in collected.column_names

    def test_window_rank(self):

        adapter = DataFusionDataFrameAdapter()

        expr = adapter.window_rank(
            order_by=[("value", True)],
            partition_by=["category"],
        )

        assert expr is not None
        adapter.register_table("test", pa.table({"category": ["A", "A"], "value": [1, 1]}))
        df = adapter.session_ctx.table("test")
        result = df.select(adapter.col("category"), expr.alias("rnk"))
        collected = adapter.collect(result)
        assert "rnk" in collected.column_names

    def test_window_dense_rank(self):

        adapter = DataFusionDataFrameAdapter()

        expr = adapter.window_dense_rank(
            order_by=[("value", True)],
            partition_by=["category"],
        )

        assert expr is not None
        adapter.register_table("test", pa.table({"category": ["A", "A"], "value": [1, 2]}))
        df = adapter.session_ctx.table("test")
        result = df.select(adapter.col("category"), expr.alias("drnk"))
        collected = adapter.collect(result)
        assert "drnk" in collected.column_names

    def test_window_sum(self):

        adapter = DataFusionDataFrameAdapter()

        expr = adapter.window_sum(
            column="value",
            partition_by=["category"],
        )

        assert expr is not None
        adapter.register_table("test", pa.table({"category": ["A", "A"], "value": [10, 20]}))
        df = adapter.session_ctx.table("test")
        result = df.select(adapter.col("category"), expr.alias("total"))
        collected = adapter.collect(result)
        assert "total" in collected.column_names

    def test_window_avg(self):

        adapter = DataFusionDataFrameAdapter()

        expr = adapter.window_avg(
            column="value",
            partition_by=["category"],
        )

        assert expr is not None
        adapter.register_table("test", pa.table({"category": ["A", "A"], "value": [10.0, 20.0]}))
        df = adapter.session_ctx.table("test")
        result = df.select(adapter.col("category"), expr.alias("avg_val"))
        collected = adapter.collect(result)
        assert "avg_val" in collected.column_names


@pytest.mark.skipif(not DATAFUSION_DF_AVAILABLE, reason="DataFusion not installed")
class TestDataFusionDataFrameOperations:
    def test_union_all(self):

        adapter = DataFusionDataFrameAdapter()

        adapter.register_table("t1", pa.table({"a": [1, 2]}))
        adapter.register_table("t2", pa.table({"a": [3, 4]}))

        df1 = adapter.session_ctx.table("t1")
        df2 = adapter.session_ctx.table("t2")

        combined = adapter.union_all(df1, df2)
        result = adapter.collect(combined)

        assert result.num_rows == 4

    def test_rename_columns(self):

        adapter = DataFusionDataFrameAdapter()

        adapter.register_table("test", pa.table({"old_name": [1, 2, 3]}))
        df = adapter.session_ctx.table("test")

        renamed = adapter.rename_columns(df, {"old_name": "new_name"})
        result = adapter.collect(renamed)

        assert "new_name" in result.column_names
        assert "old_name" not in result.column_names


@pytest.mark.skipif(not DATAFUSION_DF_AVAILABLE, reason="DataFusion not installed")
class TestDataFusionQueryExecution:
    def test_simple_select_query(self):

        adapter = DataFusionDataFrameAdapter()
        ctx = adapter.create_context()

        test_table = pa.table(
            {
                "id": [1, 2, 3],
                "name": ["Alice", "Bob", "Charlie"],
                "amount": [100, 200, 300],
            }
        )
        adapter.register_table("customers", test_table)

        ctx.register_table("customers", adapter.session_ctx.table("customers"))

        def select_impl(ctx):
            customers = ctx.get_table("customers")
            return customers.select(ctx.col("id"), ctx.col("name"))

        query = DataFrameQuery(
            query_id="SELECT1",
            query_name="Select ID and Name",
            description="Select id and name columns",
            expression_impl=select_impl,
        )

        result = adapter.execute_query(ctx, query)

        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 3

    def test_filter_query(self):

        adapter = DataFusionDataFrameAdapter()
        ctx = adapter.create_context()

        test_table = pa.table(
            {
                "id": [1, 2, 3, 4, 5],
                "amount": [50, 150, 75, 200, 100],
            }
        )
        adapter.register_table("orders", test_table)
        ctx.register_table("orders", adapter.session_ctx.table("orders"))

        def filter_impl(ctx):
            orders = ctx.get_table("orders")
            return orders.filter(ctx.col("amount") > ctx.lit(100))

        query = DataFrameQuery(
            query_id="FILTER1",
            query_name="Filter by Amount",
            description="Filter orders over 100",
            categories=[QueryCategory.FILTER],
            expression_impl=filter_impl,
        )

        result = adapter.execute_query(ctx, query)

        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 2


@pytest.mark.skipif(not DATAFUSION_DF_AVAILABLE, reason="DataFusion not installed")
class TestDataFusionTableLoading:
    def test_load_table_parquet(self, tmp_path):

        adapter = DataFusionDataFrameAdapter()
        ctx = adapter.create_context()

        parquet_path = tmp_path / "orders.parquet"
        test_table = pa.table(
            {
                "id": [1, 2, 3],
                "amount": [100, 200, 300],
            }
        )
        import pyarrow.parquet as pq

        pq.write_table(test_table, parquet_path)

        row_count = adapter.load_table(ctx, "orders", [parquet_path])

        assert ctx.table_exists("orders")
        assert row_count == 3

    def test_load_table_csv(self, tmp_path):

        adapter = DataFusionDataFrameAdapter()
        ctx = adapter.create_context()

        csv_path = tmp_path / "customers.csv"
        csv_path.write_text("id,name\n1,Alice\n2,Bob\n")

        row_count = adapter.load_table(ctx, "customers", [csv_path])

        assert ctx.table_exists("customers")
        assert row_count == 2

    def test_load_multiple_tables(self, tmp_path):

        adapter = DataFusionDataFrameAdapter()
        ctx = adapter.create_context()

        import pyarrow.parquet as pq

        for name, rows in [("orders", 5), ("customers", 3), ("products", 10)]:
            path = tmp_path / f"{name}.parquet"
            test_table = pa.table({"id": list(range(rows))})
            pq.write_table(test_table, path)
            adapter.load_table(ctx, name, [path])

        tables = ctx.list_tables()

        assert len(tables) == 3
        assert all(t in tables for t in ["orders", "customers", "products"])


@pytest.mark.skipif(not DATAFUSION_DF_AVAILABLE, reason="DataFusion not installed")
class TestDataFusionSpecificFeatures:
    def test_sql_execution(self):

        adapter = DataFusionDataFrameAdapter()

        test_table = pa.table({"id": [1, 2, 3], "value": [10, 20, 30]})
        adapter.register_table("test", test_table)

        df = adapter.sql("SELECT * FROM test WHERE value > 15")
        result = adapter.collect(df)

        assert result.num_rows == 2

    def test_register_table(self):

        adapter = DataFusionDataFrameAdapter()

        test_table = pa.table({"x": [1, 2, 3]})
        adapter.register_table("my_table", test_table)

        df = adapter.session_ctx.table("my_table")
        result = adapter.collect(df)

        assert result.num_rows == 3

    def test_register_table_from_lazy_dataframe(self):

        adapter = DataFusionDataFrameAdapter()

        adapter.register_table("source", pa.table({"x": [1, 2, 3]}))
        lazy_df = adapter.sql("SELECT x FROM source WHERE x >= 2")

        adapter.register_table("copied", lazy_df)
        result = adapter.collect(adapter.sql("SELECT * FROM copied"))

        assert result.to_pydict() == {"x": [2, 3]}

    def test_register_parquet_table(self, tmp_path):

        adapter = DataFusionDataFrameAdapter()

        parquet_path = tmp_path / "events.parquet"
        import pyarrow.parquet as pq

        pq.write_table(pa.table({"event_id": [1, 2], "amount": [10, 20]}), parquet_path)

        adapter.register_parquet_table("events", parquet_path)
        result = adapter.collect(adapter.sql("SELECT event_id FROM events WHERE amount >= 20"))

        assert result.to_pydict() == {"event_id": [2]}

    def test_to_pandas(self):

        adapter = DataFusionDataFrameAdapter()

        adapter.register_table("test", pa.table({"a": [1, 2, 3]}))
        df = adapter.session_ctx.table("test")

        result = adapter.collect(df)
        pandas_df = adapter.to_pandas(result)

        assert len(pandas_df) == 3
        assert "a" in pandas_df.columns

    def test_to_pandas_from_lazy_dataframe(self):

        adapter = DataFusionDataFrameAdapter()

        adapter.register_table("test", pa.table({"a": [1, 2, 3]}))
        pandas_df = adapter.to_pandas(adapter.sql("SELECT a FROM test WHERE a >= 2"))

        assert pandas_df.to_dict(orient="list") == {"a": [2, 3]}

    def test_to_polars_from_lazy_dataframe(self):

        adapter = DataFusionDataFrameAdapter()

        adapter.register_table("test", pa.table({"a": [1, 2, 3]}))
        polars_df = adapter.to_polars(adapter.sql("SELECT a FROM test WHERE a >= 2"))

        assert polars_df.to_dict(as_series=False) == {"a": [2, 3]}

    def test_lit_returns_existing_expression(self):
        adapter = DataFusionDataFrameAdapter()

        expr = adapter.col("value")

        assert adapter.lit(expr) is expr

    def test_get_logical_plan_contains_filter_and_scan(self):

        adapter = DataFusionDataFrameAdapter()

        adapter.register_table("test", pa.table({"x": [1, 2, 3], "y": [10, 20, 30]}))
        plan = adapter.get_logical_plan(adapter.sql("SELECT x FROM test WHERE y > 15"))

        assert "Filter" in plan
        assert "TableScan" in plan

    def test_parse_memory_limit_variants(self):

        adapter = DataFusionDataFrameAdapter()

        assert adapter._parse_memory_limit("1.5GB") == 1610612736
        assert adapter._parse_memory_limit("512M") == 536870912
        assert adapter._parse_memory_limit("64K") == 65536
        assert adapter._parse_memory_limit("128") == 128

    def test_get_first_row_from_lazy_dataframe(self):

        adapter = DataFusionDataFrameAdapter()

        adapter.register_table("test", pa.table({"id": [1, 2], "name": ["A", "B"]}))
        first_row = adapter._get_first_row(adapter.sql("SELECT * FROM test ORDER BY id"))

        assert first_row == (1, "A")


@pytest.mark.skipif(not DATAFUSION_DF_AVAILABLE, reason="DataFusion not installed")
class TestDataFusionScalarExtraction:
    def test_scalar_from_pyarrow_table(self):

        adapter = DataFusionDataFrameAdapter()

        table = pa.table({"value": [42]})
        result = adapter.scalar(table)

        assert result == 42

    def test_scalar_with_column_name(self):

        adapter = DataFusionDataFrameAdapter()

        table = pa.table({"a": [1], "b": [2], "c": [3]})
        result = adapter.scalar(table, column="b")

        assert result == 2

    def test_scalar_from_datafusion_dataframe(self):
        adapter = DataFusionDataFrameAdapter()

        table = pa.table({"value": [100]})
        adapter.session_ctx.register_record_batches("test_table", [table.to_batches()])
        df = adapter.session_ctx.sql("SELECT value FROM test_table")

        result = adapter.scalar(df)

        assert result == 100

    def test_scalar_first_column_multicolumn(self):

        adapter = DataFusionDataFrameAdapter()

        table = pa.table({"first": [10], "second": [20]})
        result = adapter.scalar(table)

        assert result == 10

    def test_scalar_empty_table_raises(self):

        adapter = DataFusionDataFrameAdapter()

        table = pa.table({"value": pa.array([], type=pa.int64())})

        with pytest.raises(ValueError, match="empty DataFrame"):
            adapter.scalar(table)

    def test_scalar_float_value(self):

        adapter = DataFusionDataFrameAdapter()

        table = pa.table({"value": [3.14159]})
        result = adapter.scalar(table)

        assert result == pytest.approx(3.14159)

    def test_scalar_string_value(self):

        adapter = DataFusionDataFrameAdapter()

        table = pa.table({"value": ["hello"]})
        result = adapter.scalar(table)

        assert result == "hello"

    def test_scalar_via_context(self):
        adapter = DataFusionDataFrameAdapter()
        ctx = adapter.create_context()

        table = pa.table({"total": [999]})
        result = ctx.scalar(table)

        assert result == 999

    def test_scalar_multiple_rows_raises(self):

        adapter = DataFusionDataFrameAdapter()

        table = pa.table({"value": [1, 2, 3]})

        with pytest.raises(ValueError, match="exactly one row"):
            adapter.scalar(table)

    def test_scalar_two_rows_raises(self):
        adapter = DataFusionDataFrameAdapter()

        table = pa.table({"a": [1, 2], "b": [3, 4]})

        with pytest.raises(ValueError, match="exactly one row"):
            adapter.scalar(table)


@pytest.mark.skipif(not DATAFUSION_DF_AVAILABLE, reason="DataFusion not installed")
class TestDataFusionUnifiedFrameMethods:
    def _collect_expr(self, adapter, table_name, expr):
        df = adapter.session_ctx.table(table_name)
        result_df = df.select(expr.native.alias("result"))
        return adapter.collect(result_df)

    def test_quantile_aggregation(self):
        from benchbox.platforms.dataframe.unified_frame import UnifiedExpr

        adapter = DataFusionDataFrameAdapter()
        adapter.register_table("nums", pa.table({"val": [10.0, 20.0, 30.0, 40.0, 50.0]}))

        col_expr = adapter.col("val")
        unified = UnifiedExpr(col_expr)
        q_expr = unified.quantile(0.5)

        df = adapter.session_ctx.table("nums")
        result_df = df.aggregate([], [q_expr.native.alias("median")])
        result = adapter.collect(result_df)

        assert result.num_rows == 1
        median_val = result.column("median")[0].as_py()
        assert 20.0 <= median_val <= 40.0

    def test_floor(self):
        from benchbox.platforms.dataframe.unified_frame import UnifiedExpr

        adapter = DataFusionDataFrameAdapter()
        adapter.register_table("nums", pa.table({"val": [1.7, 2.3, -0.5, 3.9]}))

        col_expr = adapter.col("val")
        unified = UnifiedExpr(col_expr)
        floor_expr = unified.floor()

        result = self._collect_expr(adapter, "nums", floor_expr)

        values = [v.as_py() for v in result.column("result")]
        assert values == [1.0, 2.0, -1.0, 3.0]

    def test_cast_int(self):
        from benchbox.platforms.dataframe.unified_frame import UnifiedExpr

        adapter = DataFusionDataFrameAdapter()
        adapter.register_table("nums", pa.table({"val": [1.7, 2.3, 3.9]}))

        col_expr = adapter.col("val")
        unified = UnifiedExpr(col_expr)
        int_expr = unified.cast_int()

        result = self._collect_expr(adapter, "nums", int_expr)

        values = [v.as_py() for v in result.column("result")]
        assert values == [1, 2, 3]
        assert result.schema.field("result").type == pa.int32()

    def test_hour(self):
        from datetime import datetime

        from datafusion import col as df_col

        from benchbox.platforms.dataframe.unified_frame import UnifiedDtExpr

        adapter = DataFusionDataFrameAdapter()
        timestamps = [datetime(2024, 6, 15, 14, 30, 0), datetime(2024, 6, 15, 9, 15, 0)]
        adapter.register_table("ts", pa.table({"dt": pa.array(timestamps, type=pa.timestamp("us"))}))

        dt_expr = UnifiedDtExpr(df_col("dt"), is_pyspark=False, is_datafusion=True)
        hour_expr = dt_expr.hour()

        result = self._collect_expr(adapter, "ts", hour_expr)

        values = [v.as_py() for v in result.column("result")]
        assert values == [14, 9]

    def test_minute(self):
        from datetime import datetime

        from datafusion import col as df_col

        from benchbox.platforms.dataframe.unified_frame import UnifiedDtExpr

        adapter = DataFusionDataFrameAdapter()
        timestamps = [datetime(2024, 6, 15, 14, 30, 0), datetime(2024, 6, 15, 9, 45, 0)]
        adapter.register_table("ts", pa.table({"dt": pa.array(timestamps, type=pa.timestamp("us"))}))

        dt_expr = UnifiedDtExpr(df_col("dt"), is_pyspark=False, is_datafusion=True)
        min_expr = dt_expr.minute()

        result = self._collect_expr(adapter, "ts", min_expr)

        values = [v.as_py() for v in result.column("result")]
        assert values == [30, 45]

    def test_weekday(self):
        from datetime import datetime

        from datafusion import col as df_col

        from benchbox.platforms.dataframe.unified_frame import UnifiedDtExpr

        adapter = DataFusionDataFrameAdapter()
        timestamps = [datetime(2024, 6, 10, 12, 0, 0), datetime(2024, 6, 15, 12, 0, 0), datetime(2024, 6, 16, 12, 0, 0)]
        adapter.register_table("ts", pa.table({"dt": pa.array(timestamps, type=pa.timestamp("us"))}))

        dt_expr = UnifiedDtExpr(df_col("dt"), is_pyspark=False, is_datafusion=True)
        wd_expr = dt_expr.weekday()

        result = self._collect_expr(adapter, "ts", wd_expr)

        values = [v.as_py() for v in result.column("result")]
        assert values == [0, 5, 6]

    def test_truncate(self):
        from datetime import datetime

        from datafusion import col as df_col

        from benchbox.platforms.dataframe.unified_frame import UnifiedDtExpr

        adapter = DataFusionDataFrameAdapter()
        timestamps = [datetime(2024, 6, 15, 14, 35, 42)]
        adapter.register_table("ts", pa.table({"dt": pa.array(timestamps, type=pa.timestamp("us"))}))

        dt_expr = UnifiedDtExpr(df_col("dt"), is_pyspark=False, is_datafusion=True)
        trunc_expr = dt_expr.truncate("1h")

        result = self._collect_expr(adapter, "ts", trunc_expr)

        val = result.column("result")[0].as_py()
        assert val.hour == 14
        assert val.minute == 0
        assert val.second == 0

    def test_total_seconds(self):
        from datetime import datetime

        from datafusion import col as df_col

        from benchbox.platforms.dataframe.unified_frame import UnifiedDtExpr

        adapter = DataFusionDataFrameAdapter()
        timestamps = [datetime(2024, 1, 1, 0, 1, 0)]
        adapter.register_table("ts", pa.table({"dt": pa.array(timestamps, type=pa.timestamp("s"))}))

        dt_expr = UnifiedDtExpr(df_col("dt"), is_pyspark=False, is_datafusion=True)
        secs_expr = dt_expr.total_seconds()

        result = self._collect_expr(adapter, "ts", secs_expr)

        val = result.column("result")[0].as_py()
        assert isinstance(val, (int, float))
        assert val > 0

    def test_sort_tuple_syntax(self):
        adapter = DataFusionDataFrameAdapter()
        ctx = adapter.create_context()

        test_table = pa.table({"id": [3, 1, 2], "name": ["C", "A", "B"]})
        adapter.register_table("data", test_table)
        ctx.register_table("data", adapter.session_ctx.table("data"))

        uf = ctx.get_table("data")
        sorted_uf = uf.sort([("id", "asc")])

        result = adapter.collect(sorted_uf.native)

        ids = [v.as_py() for v in result.column("id")]
        assert ids == [1, 2, 3]

    def test_sort_tuple_descending(self):
        adapter = DataFusionDataFrameAdapter()
        ctx = adapter.create_context()

        test_table = pa.table({"id": [3, 1, 2], "name": ["C", "A", "B"]})
        adapter.register_table("data", test_table)
        ctx.register_table("data", adapter.session_ctx.table("data"))

        uf = ctx.get_table("data")
        sorted_uf = uf.sort([("id", "desc")])

        result = adapter.collect(sorted_uf.native)

        ids = [v.as_py() for v in result.column("id")]
        assert ids == [3, 2, 1]

    def test_list_get_with_expression_index(self):
        from benchbox.platforms.dataframe.unified_frame import UnifiedExpr

        adapter = DataFusionDataFrameAdapter()
        adapter.register_table("lists", pa.table({"tags": [["a", "b", "c"], ["x", "y"]]}))

        unified = UnifiedExpr(adapter.col("tags"))
        last = unified.list.get(unified.list.len() - 1)

        result = self._collect_expr(adapter, "lists", last)
        assert result.column("result").to_pylist() == ["c", "y"]


class TestDataFusionNotAvailable:
    def test_datafusion_available_flag(self):

        from benchbox.platforms.dataframe.datafusion_df import DATAFUSION_DF_AVAILABLE

        assert isinstance(DATAFUSION_DF_AVAILABLE, bool)


