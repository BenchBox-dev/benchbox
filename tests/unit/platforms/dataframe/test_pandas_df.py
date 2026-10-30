# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from benchbox.core.dataframe.query import DataFrameQuery, QueryCategory

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


try:
    import pandas as pd

    from benchbox.platforms.dataframe.pandas_df import (
        PANDAS_AVAILABLE,
        PandasDataFrameAdapter,
    )
except ImportError:
    PANDAS_AVAILABLE = False
    pd = None


@pytest.mark.skipif(not PANDAS_AVAILABLE, reason="Pandas not installed")
class TestPandasDataFrameAdapter:
    def test_initialization(self):

        adapter = PandasDataFrameAdapter()

        assert adapter.platform_name == "Pandas"
        assert adapter.family == "pandas"
        assert adapter.dtype_backend == "numpy_nullable"

    def test_initialization_with_options(self):

        adapter = PandasDataFrameAdapter(
            working_dir="/tmp/pandas",
            verbose=True,
            dtype_backend="pyarrow",
        )

        assert adapter.working_dir == Path("/tmp/pandas")
        assert adapter.verbose is True
        assert adapter.dtype_backend == "pyarrow"

    def test_initialization_with_copy_on_write(self):

        pandas_version = tuple(int(x) for x in pd.__version__.split(".")[:2])

        adapter = PandasDataFrameAdapter(copy_on_write=True)

        if pandas_version >= (3, 0):
            assert adapter.copy_on_write is True
        elif pandas_version >= (2, 0):
            assert adapter.copy_on_write is True
            assert pd.options.mode.copy_on_write is True
        else:
            assert adapter.copy_on_write is False

    def test_initialization_with_copy_on_write_disabled(self):

        pandas_version = tuple(int(x) for x in pd.__version__.split(".")[:2])

        adapter = PandasDataFrameAdapter(copy_on_write=False)

        if pandas_version >= (3, 0):
            assert adapter.copy_on_write is True
        elif pandas_version >= (2, 0):
            assert adapter.copy_on_write is False
            assert pd.options.mode.copy_on_write is False

    def test_platform_info(self):

        adapter = PandasDataFrameAdapter()

        info = adapter.get_platform_info()

        assert info["platform"] == "Pandas"
        assert info["family"] == "pandas"
        assert "version" in info
        assert "copy_on_write" in info

    def test_create_context(self):

        adapter = PandasDataFrameAdapter()
        ctx = adapter.create_context()

        assert ctx is not None
        assert ctx.platform == "Pandas"
        assert ctx.family == "pandas"


@pytest.mark.skipif(not PANDAS_AVAILABLE, reason="Pandas not installed")
class TestPandasDataLoading:
    def test_read_csv_basic(self, tmp_path):

        adapter = PandasDataFrameAdapter()

        csv_path = tmp_path / "test.csv"
        csv_path.write_text("id,name,amount\n1,Alice,100\n2,Bob,200\n")

        df = adapter.read_csv(csv_path)

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2
        assert list(df.columns) == ["id", "name", "amount"]

    def test_read_csv_with_delimiter(self, tmp_path):

        adapter = PandasDataFrameAdapter()

        csv_path = tmp_path / "test.csv"
        csv_path.write_text("id|name|amount\n1|Alice|100\n2|Bob|200\n")

        df = adapter.read_csv(csv_path, delimiter="|")

        assert len(df) == 2

    def test_read_csv_with_column_names(self, tmp_path):

        adapter = PandasDataFrameAdapter()

        csv_path = tmp_path / "test.csv"
        csv_path.write_text("1,Alice,100\n2,Bob,200\n")

        df = adapter.read_csv(
            csv_path,
            header=None,
            names=["id", "name", "amount"],
        )

        assert list(df.columns) == ["id", "name", "amount"]

    def test_read_csv_with_column_names_parses_dates(self, tmp_path):
        adapter = PandasDataFrameAdapter()

        csv_path = tmp_path / "orders.tbl"
        csv_path.write_text("1|1998-01-02|2450815|2013-07-01 12:34:56|\n")

        df = adapter.read_csv(
            csv_path,
            delimiter="|",
            header=None,
            names=["o_orderkey", "o_orderdate", "d_date_sk", "EventTime"],
            null_marker="",
        )

        assert df["o_orderdate"].iloc[0] == date(1998, 1, 2)
        assert df["o_orderdate"].dt.year.iloc[0] == 1998
        assert not pd.api.types.is_datetime64_any_dtype(df["d_date_sk"])
        assert pd.api.types.is_datetime64_any_dtype(df["EventTime"])

    def test_read_csv_date_columns_keep_missing_values_as_null(self, tmp_path):
        adapter = PandasDataFrameAdapter()
        csv_path = tmp_path / "items.dat"
        csv_path.write_text("1|1998-01-02|\n2||2001-05-06\n")

        df = adapter.read_csv(
            csv_path,
            delimiter="|",
            header=None,
            names=["i_id", "i_rec_start_date", "i_rec_end_date"],
            null_marker="",
        )

        assert df["i_rec_start_date"].tolist() == [date(1998, 1, 2), pd.NA]
        assert df["i_rec_end_date"].tolist() == [pd.NA, date(2001, 5, 6)]
        assert str(df["i_rec_start_date"].dtype) == "date32[day][pyarrow]"

    def test_read_csv_date_columns_fall_back_for_non_iso_text(self, tmp_path):
        adapter = PandasDataFrameAdapter()
        csv_path = tmp_path / "events.dat"
        csv_path.write_text("1|Jan 2 1998\n2|1999-03-04\n")

        df = adapter.read_csv(csv_path, delimiter="|", header=None, names=["id", "event_date"], null_marker="")

        assert df["event_date"].tolist() == [date(1998, 1, 2), date(1999, 3, 4)]

    def test_read_csv_type_aware_date_parsing_by_declared_type(self, tmp_path):
        adapter = PandasDataFrameAdapter()
        csv_path = tmp_path / "hits.csv"
        csv_path.write_text("1|2013-07-01 00:01:34\n")

        df = adapter.read_csv(
            csv_path,
            delimiter="|",
            header=None,
            names=["id", "ClientEventTime"],
            null_marker=None,
            column_types=["INTEGER", "TIMESTAMP"],
        )
        assert pd.api.types.is_datetime64_any_dtype(df["ClientEventTime"])

    def test_read_csv_declared_text_column_not_inferred_numeric(self, tmp_path):
        adapter = PandasDataFrameAdapter()
        csv_path = tmp_path / "info.csv"
        csv_path.write_text("1|8.0\n2|10.0\n")

        df = adapter.read_csv(
            csv_path,
            delimiter="|",
            header=None,
            names=["id", "info"],
            null_marker="",
            column_types=["INTEGER", "TEXT"],
        )
        assert not pd.api.types.is_numeric_dtype(df["info"])
        assert df["info"].iloc[0] == "8.0"

    def test_read_csv_empty_string_handling_follows_null_marker(self, tmp_path):
        adapter = PandasDataFrameAdapter()
        csv_path = tmp_path / "t.csv"
        csv_path.write_text("1|\n2|hello\n")

        kept = adapter.read_csv(
            csv_path,
            delimiter="|",
            header=None,
            names=["id", "note"],
            null_marker=None,
            column_types=["INTEGER", "TEXT"],
        )
        assert kept["note"].iloc[0] == ""

        nulled = adapter.read_csv(
            csv_path,
            delimiter="|",
            header=None,
            names=["id", "note"],
            null_marker="",
            column_types=["INTEGER", "TEXT"],
        )
        assert pd.isna(nulled["note"].iloc[0])

    def test_load_headered_csv_with_column_names_parses_dates(self, tmp_path):
        adapter = PandasDataFrameAdapter()
        ctx = adapter.create_context()

        csv_path = tmp_path / "flights.csv"
        csv_path.write_text("flight_id,flight_date,EventTime\n1,2018-01-02,2018-01-02 12:34:56\n")

        adapter.load_table(
            ctx,
            "flights",
            [csv_path],
            column_names=["flight_id", "flight_date", "EventTime"],
            format_hint="csv",
        )

        df = ctx.get_table("flights")._df
        assert df["flight_date"].iloc[0] == date(2018, 1, 2)
        assert df["flight_date"].dt.year.iloc[0] == 2018
        assert pd.api.types.is_datetime64_any_dtype(df["EventTime"])

    def test_read_csv_all_digit_string_column_stays_text(self, tmp_path):
        adapter = PandasDataFrameAdapter()

        csv_path = tmp_path / "codes.tbl"
        csv_path.write_text("1|007|x\n2|10|y\n3||z\n")

        df = adapter.read_csv(
            csv_path,
            delimiter="|",
            header=None,
            names=["id", "code", "tag"],
            null_marker=None,
            column_types=["INTEGER", "VARCHAR", "VARCHAR"],
        )

        assert list(df["code"]) == ["007", "10", ""]
        assert df["code"].dtype == object

    def test_read_parquet(self, tmp_path):

        adapter = PandasDataFrameAdapter()

        parquet_path = tmp_path / "test.parquet"
        test_df = pd.DataFrame(
            {
                "id": [1, 2, 3],
                "name": ["A", "B", "C"],
                "amount": [100.0, 200.0, 300.0],
            }
        )
        test_df.to_parquet(parquet_path)

        df = adapter.read_parquet(parquet_path)

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 3

    def test_concat_dataframes(self):

        adapter = PandasDataFrameAdapter()

        df1 = pd.DataFrame({"a": [1, 2]})
        df2 = pd.DataFrame({"a": [3, 4]})

        combined = adapter.concat([df1, df2])

        assert len(combined) == 4

    def test_concat_single_dataframe(self):

        adapter = PandasDataFrameAdapter()

        df = pd.DataFrame({"a": [1, 2, 3]})
        result = adapter.concat([df])

        assert result is df

    def test_concat_shards_matches_concat_and_consumes_inputs(self):
        import pyarrow as pa

        adapter = PandasDataFrameAdapter()
        date_dtype = pd.ArrowDtype(pa.date32())

        def shard(offset: int) -> pd.DataFrame:
            return pd.DataFrame(
                {
                    "id": [offset, offset + 1],
                    "d": pd.array([pd.Timestamp("2024-01-01").date()] * 2, dtype=date_dtype),
                    "s": pd.Series(["a", None], dtype=object),
                    "x": [1.5, float("nan")],
                }
            )

        expected = pd.concat([shard(0), shard(2), shard(4)], ignore_index=True)
        shards = [shard(0), shard(2), shard(4)]
        combined = adapter.concat_shards(shards)

        assert shards == []
        assert list(combined.columns) == list(expected.columns)
        assert combined.dtypes.to_dict() == expected.dtypes.to_dict()
        pd.testing.assert_frame_equal(combined, expected)

    def test_concat_shards_mismatched_columns_falls_back(self):
        adapter = PandasDataFrameAdapter()

        combined = adapter.concat_shards([pd.DataFrame({"a": [1]}), pd.DataFrame({"b": [2]})])

        assert len(combined) == 2

    def test_concat_shards_peak_memory_stays_near_final_size(self):
        import tracemalloc

        import numpy as np

        adapter = PandasDataFrameAdapter()
        shards = [pd.DataFrame(np.random.default_rng(i).random((100_000, 8))) for i in range(12)]
        shards = [df.rename(columns=str) for df in shards]
        final_bytes = sum(int(df.memory_usage(index=False).sum()) for df in shards)

        tracemalloc.start()
        baseline = tracemalloc.get_traced_memory()[0]
        combined = adapter.concat_shards(shards)
        peak = tracemalloc.get_traced_memory()[1] - baseline
        tracemalloc.stop()

        assert len(combined) == 1_200_000
        assert peak < final_bytes * 1.25

    def test_get_row_count(self):

        adapter = PandasDataFrameAdapter()

        df = pd.DataFrame({"a": [1, 2, 3, 4, 5]})
        count = adapter.get_row_count(df)

        assert count == 5

    def test_get_first_row(self):

        adapter = PandasDataFrameAdapter()

        df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        first = adapter._get_first_row(df)

        assert first == (1, "x")

    def test_get_first_row_empty(self):

        adapter = PandasDataFrameAdapter()

        df = pd.DataFrame({"a": [], "b": []})
        first = adapter._get_first_row(df)

        assert first is None


@pytest.mark.skipif(not PANDAS_AVAILABLE, reason="Pandas not installed")
class TestPandasHelperMethods:
    def test_to_datetime(self):

        adapter = PandasDataFrameAdapter()

        series = pd.Series(["2024-01-01", "2024-02-01"])
        result = adapter.to_datetime(series)

        assert pd.api.types.is_datetime64_any_dtype(result)

    def test_timedelta_days(self):

        adapter = PandasDataFrameAdapter()

        td = adapter.timedelta_days(7)

        assert td == pd.Timedelta(days=7)

    def test_merge(self):

        adapter = PandasDataFrameAdapter()

        left = pd.DataFrame({"id": [1, 2, 3], "name": ["A", "B", "C"]})
        right = pd.DataFrame({"id": [1, 2], "value": [100, 200]})

        merged = adapter.merge(left, right, on="id")

        assert len(merged) == 2
        assert "name" in merged.columns
        assert "value" in merged.columns

    def test_merge_left_join(self):

        adapter = PandasDataFrameAdapter()

        left = pd.DataFrame({"id": [1, 2, 3], "name": ["A", "B", "C"]})
        right = pd.DataFrame({"id": [1, 2], "value": [100, 200]})

        merged = adapter.merge(left, right, on="id", how="left")

        assert len(merged) == 3

    def test_groupby_agg(self):

        adapter = PandasDataFrameAdapter()

        df = pd.DataFrame(
            {
                "category": ["A", "B", "A", "B"],
                "amount": [100, 200, 150, 250],
            }
        )

        result = adapter.groupby_agg(df, "category", {"amount": "sum"})

        assert len(result) == 2
        assert "category" in result.columns
        assert "amount" in result.columns

    def test_filter_rows_greater_than(self):

        adapter = PandasDataFrameAdapter()

        df = pd.DataFrame({"value": [1, 5, 10, 15, 20]})
        filtered = adapter.filter_rows(df, "value", ">", 10)

        assert len(filtered) == 2

    def test_filter_rows_less_than(self):

        adapter = PandasDataFrameAdapter()

        df = pd.DataFrame({"value": [1, 5, 10, 15, 20]})
        filtered = adapter.filter_rows(df, "value", "<", 10)

        assert len(filtered) == 2

    def test_filter_rows_equal(self):

        adapter = PandasDataFrameAdapter()

        df = pd.DataFrame({"value": [1, 5, 10, 15, 20]})
        filtered = adapter.filter_rows(df, "value", "==", 10)

        assert len(filtered) == 1

    def test_filter_rows_invalid_operator(self):

        adapter = PandasDataFrameAdapter()

        df = pd.DataFrame({"value": [1, 2, 3]})

        with pytest.raises(ValueError, match="Unknown operator"):
            adapter.filter_rows(df, "value", "invalid", 10)

    def test_sort_values(self):

        adapter = PandasDataFrameAdapter()

        df = pd.DataFrame({"value": [3, 1, 2]})
        sorted_df = adapter.sort_values(df, "value")

        assert list(sorted_df["value"]) == [1, 2, 3]

    def test_sort_values_descending(self):

        adapter = PandasDataFrameAdapter()

        df = pd.DataFrame({"value": [1, 3, 2]})
        sorted_df = adapter.sort_values(df, "value", ascending=False)

        assert list(sorted_df["value"]) == [3, 2, 1]

    def test_select_columns(self):

        adapter = PandasDataFrameAdapter()

        df = pd.DataFrame({"a": [1], "b": [2], "c": [3]})
        selected = adapter.select_columns(df, ["a", "c"])

        assert list(selected.columns) == ["a", "c"]

    def test_with_column(self):

        adapter = PandasDataFrameAdapter()

        df = pd.DataFrame({"a": [1, 2, 3]})
        result = adapter.with_column(df, "b", [10, 20, 30])

        assert "b" in result.columns
        assert list(result["b"]) == [10, 20, 30]


@pytest.mark.skipif(not PANDAS_AVAILABLE, reason="Pandas not installed")
class TestPandasQueryExecution:
    def test_simple_select_query(self):

        adapter = PandasDataFrameAdapter()
        ctx = adapter.create_context()

        test_df = pd.DataFrame(
            {
                "id": [1, 2, 3],
                "name": ["Alice", "Bob", "Charlie"],
                "amount": [100, 200, 300],
            }
        )
        ctx.register_table("customers", test_df)

        def select_impl(ctx):
            customers = ctx.get_table("customers")
            return customers[["id", "name"]]

        query = DataFrameQuery(
            query_id="SELECT1",
            query_name="Select ID and Name",
            description="Select id and name columns",
            pandas_impl=select_impl,
        )

        result = adapter.execute_query(ctx, query)

        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 3

    def test_filter_query(self):

        adapter = PandasDataFrameAdapter()
        ctx = adapter.create_context()

        test_df = pd.DataFrame(
            {
                "id": [1, 2, 3, 4, 5],
                "amount": [50, 150, 75, 200, 100],
            }
        )
        ctx.register_table("orders", test_df)

        def filter_impl(ctx):
            orders = ctx.get_table("orders")
            return orders[orders["amount"] > 100]

        query = DataFrameQuery(
            query_id="FILTER1",
            query_name="Filter by Amount",
            description="Filter orders over 100",
            categories=[QueryCategory.FILTER],
            pandas_impl=filter_impl,
        )

        result = adapter.execute_query(ctx, query)

        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 2

    def test_groupby_query(self):

        adapter = PandasDataFrameAdapter()
        ctx = adapter.create_context()

        test_df = pd.DataFrame(
            {
                "category": ["A", "B", "A", "B", "A"],
                "amount": [100, 200, 150, 250, 50],
            }
        )
        ctx.register_table("sales", test_df)

        def groupby_impl(ctx):
            sales = ctx.get_table("sales")
            return sales.groupby("category", as_index=False).agg({"amount": "sum"})

        query = DataFrameQuery(
            query_id="GROUP1",
            query_name="Group by Category",
            description="Sum amount by category",
            categories=[QueryCategory.GROUP_BY, QueryCategory.AGGREGATE],
            pandas_impl=groupby_impl,
        )

        result = adapter.execute_query(ctx, query)

        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 2

    def test_join_query(self):

        adapter = PandasDataFrameAdapter()
        ctx = adapter.create_context()

        orders_df = pd.DataFrame(
            {
                "order_id": [1, 2, 3],
                "customer_id": [101, 102, 101],
                "amount": [100, 200, 150],
            }
        )
        customers_df = pd.DataFrame(
            {
                "customer_id": [101, 102],
                "name": ["Alice", "Bob"],
            }
        )

        ctx.register_table("orders", orders_df)
        ctx.register_table("customers", customers_df)

        def join_impl(ctx):
            orders = ctx.get_table("orders")
            customers = ctx.get_table("customers")
            return orders.merge(customers, on="customer_id", how="left")

        query = DataFrameQuery(
            query_id="JOIN1",
            query_name="Join Orders and Customers",
            description="Left join orders with customers",
            categories=[QueryCategory.JOIN],
            pandas_impl=join_impl,
        )

        result = adapter.execute_query(ctx, query)

        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 3

    def test_query_with_context_helpers(self):

        adapter = PandasDataFrameAdapter()
        ctx = adapter.create_context()

        test_df = pd.DataFrame(
            {
                "value": [10, 20, 30],
            }
        )
        ctx.register_table("data", test_df)

        def helper_impl(ctx):
            data = ctx.get_table("data")
            col_name = ctx.col("value")
            threshold = ctx.lit(15)
            return data[data[col_name] > threshold]

        query = DataFrameQuery(
            query_id="HELPER1",
            query_name="Query with Helpers",
            description="Use context helpers",
            pandas_impl=helper_impl,
        )

        result = adapter.execute_query(ctx, query)

        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 2


@pytest.mark.skipif(not PANDAS_AVAILABLE, reason="Pandas not installed")
class TestPandasTableLoading:
    def test_load_table_parquet(self, tmp_path):

        adapter = PandasDataFrameAdapter()
        ctx = adapter.create_context()

        parquet_path = tmp_path / "orders.parquet"
        pd.DataFrame(
            {
                "id": [1, 2, 3],
                "amount": [100, 200, 300],
            }
        ).to_parquet(parquet_path)

        row_count = adapter.load_table(ctx, "orders", [parquet_path])

        assert ctx.table_exists("orders")
        assert row_count == 3

    def test_load_table_csv(self, tmp_path):

        adapter = PandasDataFrameAdapter()
        ctx = adapter.create_context()

        csv_path = tmp_path / "customers.csv"
        csv_path.write_text("id,name\n1,Alice\n2,Bob\n")

        row_count = adapter.load_table(ctx, "customers", [csv_path])

        assert ctx.table_exists("customers")
        assert row_count == 2

    def test_load_multiple_tables(self, tmp_path):

        adapter = PandasDataFrameAdapter()
        ctx = adapter.create_context()

        for name, rows in [("orders", 5), ("customers", 3), ("products", 10)]:
            path = tmp_path / f"{name}.parquet"
            pd.DataFrame({"id": list(range(rows))}).to_parquet(path)
            adapter.load_table(ctx, name, [path])

        tables = ctx.list_tables()

        assert len(tables) == 3
        assert all(t in tables for t in ["orders", "customers", "products"])


@pytest.mark.skipif(not PANDAS_AVAILABLE, reason="Pandas not installed")
class TestPandasScalarExtraction:
    def test_scalar_single_value_dataframe(self):

        adapter = PandasDataFrameAdapter()

        df = pd.DataFrame({"value": [42]})
        result = adapter.scalar(df)

        assert result == 42

    def test_scalar_with_column_name(self):

        adapter = PandasDataFrameAdapter()

        df = pd.DataFrame({"a": [1], "b": [2], "c": [3]})
        result = adapter.scalar(df, column="b")

        assert result == 2

    def test_scalar_first_column_multicolumn_df(self):

        adapter = PandasDataFrameAdapter()

        df = pd.DataFrame({"first": [10], "second": [20]})
        result = adapter.scalar(df)

        assert result == 10

    def test_scalar_empty_dataframe_raises(self):

        adapter = PandasDataFrameAdapter()

        df = pd.DataFrame({"value": []})

        with pytest.raises(ValueError, match="empty DataFrame"):
            adapter.scalar(df)

    def test_scalar_float_value(self):

        adapter = PandasDataFrameAdapter()

        df = pd.DataFrame({"value": [3.14159]})
        result = adapter.scalar(df)

        assert result == pytest.approx(3.14159)

    def test_scalar_string_value(self):

        adapter = PandasDataFrameAdapter()

        df = pd.DataFrame({"value": ["hello"]})
        result = adapter.scalar(df)

        assert result == "hello"

    def test_scalar_via_context(self):
        adapter = PandasDataFrameAdapter()
        ctx = adapter.create_context()

        df = pd.DataFrame({"total": [999]})
        result = ctx.scalar(df)

        assert result == 999

    def test_scalar_multiple_rows_raises(self):

        adapter = PandasDataFrameAdapter()

        df = pd.DataFrame({"value": [1, 2, 3]})

        with pytest.raises(ValueError, match="exactly one row"):
            adapter.scalar(df)

    def test_scalar_two_rows_raises(self):
        adapter = PandasDataFrameAdapter()

        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})

        with pytest.raises(ValueError, match="exactly one row"):
            adapter.scalar(df)


class TestPandasNotAvailable:
    def test_pandas_available_flag(self):

        from benchbox.platforms.dataframe.pandas_df import PANDAS_AVAILABLE

        assert isinstance(PANDAS_AVAILABLE, bool)


class TestTypeAwareDateColumnInference:
    def test_numeric_date_named_column_is_not_a_date(self):
        from benchbox.platforms.dataframe.pandas_df import _pandas_parse_date_columns

        names = ["lo_orderkey", "lo_orderdate", "lo_commitdate", "lo_revenue"]
        types = ["INTEGER", "INTEGER", "INTEGER", "INTEGER"]
        date_cols, datetime_cols = _pandas_parse_date_columns(names, types)
        assert date_cols == []
        assert datetime_cols == []

    def test_string_date_named_column_is_still_a_date(self):
        from benchbox.platforms.dataframe.pandas_df import _pandas_parse_date_columns

        names = ["d_datekey", "d_date"]
        types = ["INTEGER", "VARCHAR(18)"]
        date_cols, _ = _pandas_parse_date_columns(names, types)
        assert date_cols == ["d_date"]

    def test_without_types_falls_back_to_name_heuristic(self):
        from benchbox.platforms.dataframe.pandas_df import _pandas_parse_date_columns

        names = ["o_orderdate", "o_custkey"]
        date_cols, _ = _pandas_parse_date_columns(names, None)
        assert date_cols == ["o_orderdate"]

    def test_mismatched_types_length_is_ignored(self):
        from benchbox.platforms.dataframe.pandas_df import _pandas_parse_date_columns

        names = ["o_orderdate", "o_custkey"]
        date_cols, _ = _pandas_parse_date_columns(names, ["INTEGER"])
        assert date_cols == ["o_orderdate"]

    def test_is_numeric_sql_type(self):
        from benchbox.platforms.dataframe.pandas_df import _is_numeric_sql_type

        assert _is_numeric_sql_type("INTEGER")
        assert _is_numeric_sql_type("integer")
        assert _is_numeric_sql_type("BIGINT")
        assert _is_numeric_sql_type("DECIMAL(15,2)")
        assert not _is_numeric_sql_type("VARCHAR(18)")
        assert not _is_numeric_sql_type("DATE")
        assert not _is_numeric_sql_type(None)

    def test_read_csv_does_not_date_parse_integer_datekeys(self, tmp_path):
        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        csv = tmp_path / "lineorder.tbl"
        csv.write_text("1|19920101|100\n2|19980815|200\n")
        adapter = PandasDataFrameAdapter()
        df = adapter.read_csv(
            csv,
            delimiter="|",
            header=None,
            names=["lo_orderkey", "lo_orderdate", "lo_revenue"],
            column_types=["INTEGER", "INTEGER", "INTEGER"],
        )
        assert df["lo_orderdate"].tolist() == [19920101, 19980815]
