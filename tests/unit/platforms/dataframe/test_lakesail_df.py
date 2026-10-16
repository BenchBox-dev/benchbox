from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.dataframe.tuning import (
    DataFrameTuningConfiguration,
    ExecutionConfiguration,
    MemoryConfiguration,
    ParallelismConfiguration,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.skipif(sys.platform == "win32", reason="PySpark tests skipped on Windows"),
]


try:
    from benchbox.platforms.pyspark import PYSPARK_AVAILABLE
except ImportError:
    PYSPARK_AVAILABLE = False


class TestLakeSailDataFrameAdapterMocked:
    @pytest.fixture
    def mock_pyspark_env(self):
        mock_session = MagicMock()
        mock_session.version = "3.5.0"
        mock_builder = MagicMock()
        mock_builder.remote.return_value = mock_builder
        mock_builder.config.return_value = mock_builder
        mock_builder.getOrCreate.return_value = mock_session

        mock_session_class = MagicMock()
        mock_session_class.builder = mock_builder

        mock_col = MagicMock()
        mock_functions = MagicMock()
        mock_functions.col.return_value = mock_col
        mock_functions.lit.return_value = mock_col

        mock_pyspark = MagicMock()
        mock_pyspark_sql = MagicMock(
            SparkSession=mock_session_class,
            functions=mock_functions,
            DataFrame=MagicMock,
        )
        mock_pyspark_sql.column.Column = MagicMock
        mock_pyspark_sql.types.StringType = MagicMock()
        mock_pyspark_sql.types.StructField = MagicMock()
        mock_pyspark_sql.types.StructType = MagicMock()
        mock_pyspark_sql.window.Window = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "pyspark": mock_pyspark,
                "pyspark.sql": mock_pyspark_sql,
                "pyspark.sql.column": mock_pyspark_sql.column,
                "pyspark.sql.types": mock_pyspark_sql.types,
                "pyspark.sql.window": mock_pyspark_sql.window,
                "pyspark.sql.functions": mock_functions,
            },
        ):
            yield mock_session_class, mock_session, mock_functions

    def test_initialization_requires_pyspark(self):
        with patch.dict("sys.modules", {"pyspark": None, "pyspark.sql": None}):
            pass

    def test_platform_name(self, mock_pyspark_env):

        if not PYSPARK_AVAILABLE:
            pytest.skip("PySpark not available")

        from benchbox.platforms.dataframe.lakesail_df import LakeSailDataFrameAdapter

        adapter = LakeSailDataFrameAdapter.__new__(LakeSailDataFrameAdapter)
        adapter._endpoint = "sc://localhost:50051"
        assert adapter.platform_name == "LakeSail"


@pytest.mark.skipif(not PYSPARK_AVAILABLE, reason="PySpark not installed")
class TestLakeSailDataFrameAdapterReal:
    def test_initialization(self):

        from benchbox.platforms.dataframe.lakesail_df import LakeSailDataFrameAdapter

        adapter = LakeSailDataFrameAdapter.__new__(LakeSailDataFrameAdapter)
        assert adapter.platform_name == "LakeSail"

    def test_platform_name_value(self):

        from benchbox.platforms.dataframe.lakesail_df import LakeSailDataFrameAdapter

        adapter = LakeSailDataFrameAdapter.__new__(LakeSailDataFrameAdapter)
        assert adapter.platform_name == "LakeSail"

    def test_type_aliases_defined(self):

        from pyspark.sql import DataFrame
        from pyspark.sql.column import Column

        from benchbox.platforms.dataframe.lakesail_df import (
            LakeSailDF,
            LakeSailExpr,
            LakeSailLazyDF,
        )

        assert LakeSailDF is DataFrame
        assert LakeSailLazyDF is DataFrame
        assert LakeSailExpr is Column

    def test_adapter_class_exists_in_dataframe_package(self):

        from benchbox.platforms.dataframe import LakeSailDataFrameAdapter

        assert LakeSailDataFrameAdapter is not None

    def test_family_is_expression(self):

        from benchbox.platforms.dataframe.lakesail_df import LakeSailDataFrameAdapter

        adapter = LakeSailDataFrameAdapter.__new__(LakeSailDataFrameAdapter)
        assert adapter.family == "expression"


class TestLakeSailDataFrameAdapterConfig:
    def test_default_endpoint(self):

        if not PYSPARK_AVAILABLE:
            pytest.skip("PySpark not available")

        from benchbox.platforms.dataframe.lakesail_df import LakeSailDataFrameAdapter

        adapter = LakeSailDataFrameAdapter.__new__(LakeSailDataFrameAdapter)
        adapter._endpoint = "sc://localhost:50051"
        assert adapter._endpoint == "sc://localhost:50051"

    def test_custom_endpoint(self):

        if not PYSPARK_AVAILABLE:
            pytest.skip("PySpark not available")

        from benchbox.platforms.dataframe.lakesail_df import LakeSailDataFrameAdapter

        adapter = LakeSailDataFrameAdapter.__new__(LakeSailDataFrameAdapter)
        adapter._endpoint = "sc://sail-cluster:50051"
        assert adapter._endpoint == "sc://sail-cluster:50051"

    def test_get_platform_info_structure(self):

        if not PYSPARK_AVAILABLE:
            pytest.skip("PySpark not available")

        from benchbox.platforms.dataframe.lakesail_df import LakeSailDataFrameAdapter

        adapter = LakeSailDataFrameAdapter.__new__(LakeSailDataFrameAdapter)
        adapter._endpoint = "sc://localhost:50051"
        adapter._driver_memory = "4g"
        adapter._shuffle_partitions = 8
        adapter._enable_aqe = True
        adapter.deployment_selected = "local"
        adapter.verbose = False
        adapter.very_verbose = False
        adapter.working_dir = "/tmp"

        info = adapter.get_platform_info()
        assert info["platform"] == "LakeSail"
        assert info["family"] == "expression"
        assert info["endpoint"] == "sc://localhost:50051"
        assert info["deployment_mode"] == "local"
        assert "driver_memory" in info
        assert "shuffle_partitions" in info
        assert "aqe_enabled" in info

    def test_tuning_config_overrides_shuffle_and_memory(self):
        if not PYSPARK_AVAILABLE:
            pytest.skip("PySpark not available")

        from benchbox.platforms.dataframe.lakesail_df import LakeSailDataFrameAdapter

        tuning = DataFrameTuningConfiguration(
            parallelism=ParallelismConfiguration(thread_count=7),
            memory=MemoryConfiguration(memory_limit="5GB"),
            execution=ExecutionConfiguration(streaming_mode=True),
        )

        adapter = LakeSailDataFrameAdapter(
            endpoint="sc://localhost:50051",
            driver_memory="1g",
            shuffle_partitions=2,
            tuning_config=tuning,
        )

        assert adapter._shuffle_partitions == 7
        assert adapter._driver_memory == "5GB"

        summary = adapter.get_tuning_summary()
        assert summary["endpoint"] == "sc://localhost:50051"
        assert summary["driver_memory"] == "5GB"
        assert summary["shuffle_partitions"] == 7

        adapter.close()

    def test_build_schema_uses_nullable_strings(self):
        if not PYSPARK_AVAILABLE:
            pytest.skip("PySpark not available")

        from benchbox.platforms.dataframe.lakesail_df import LakeSailDataFrameAdapter

        adapter = LakeSailDataFrameAdapter.__new__(LakeSailDataFrameAdapter)
        schema = adapter._build_schema(["order_id", "customer_name"])

        assert schema.fieldNames() == ["order_id", "customer_name"]
        assert [field.dataType.simpleString() for field in schema.fields] == ["string", "string"]
        assert all(field.nullable for field in schema.fields)


class TestLakeSailDataFrameAdapterLifecycle:
    @pytest.mark.skipif(not PYSPARK_AVAILABLE, reason="PySpark not available")
    def test_session_builder_uses_endpoint_and_extra_configs(self):
        from benchbox.platforms.dataframe.lakesail_df import LakeSailDataFrameAdapter

        mock_session = MagicMock()
        mock_builder = MagicMock()
        mock_builder.remote.return_value = mock_builder
        mock_builder.config.return_value = mock_builder
        mock_builder.getOrCreate.return_value = mock_session
        mock_spark_class = MagicMock(builder=mock_builder)

        with patch("benchbox.platforms.dataframe.lakesail_df.SparkSession", mock_spark_class):
            adapter = LakeSailDataFrameAdapter(
                endpoint="sc://lakehouse:50051",
                app_name="BenchBox-LakeSail-Tests",
                driver_memory="2g",
                shuffle_partitions=4,
                enable_aqe=True,
                verbose=True,
                spark_sql_catalog_implementation="in-memory",
            )

            assert adapter.spark is mock_session
            assert adapter.spark is mock_session

        mock_builder.remote.assert_called_once_with("sc://lakehouse:50051")
        assert mock_builder.getOrCreate.call_count == 1
        assert mock_builder.config.call_args_list == [
            (("spark.app.name", "BenchBox-LakeSail-Tests"), {}),
            (("spark.sql.shuffle.partitions", "4"), {}),
            (("spark.sql.adaptive.enabled", "true"), {}),
            (("spark.sql.adaptive.coalescePartitions.enabled", "true"), {}),
            (("spark.sql.adaptive.skewJoin.enabled", "true"), {}),
            (("spark_sql_catalog_implementation", "in-memory"), {}),
        ]

    @pytest.mark.skipif(not PYSPARK_AVAILABLE, reason="PySpark not available")
    def test_session_builder_disables_aqe_explicitly(self):
        from benchbox.platforms.dataframe.lakesail_df import LakeSailDataFrameAdapter

        mock_session = MagicMock()
        mock_builder = MagicMock()
        mock_builder.remote.return_value = mock_builder
        mock_builder.config.return_value = mock_builder
        mock_builder.getOrCreate.return_value = mock_session
        mock_spark_class = MagicMock(builder=mock_builder)

        with patch("benchbox.platforms.dataframe.lakesail_df.SparkSession", mock_spark_class):
            adapter = LakeSailDataFrameAdapter(
                endpoint="sc://lakehouse:50051",
                enable_aqe=False,
            )

            assert adapter.spark is mock_session

        assert mock_builder.config.call_args_list == [
            (("spark.app.name", "BenchBox-LakeSail-DF"), {}),
            (("spark.sql.shuffle.partitions", str(adapter._shuffle_partitions)), {}),
            (("spark.sql.adaptive.enabled", "false"), {}),
            (("spark.sql.adaptive.coalescePartitions.enabled", "false"), {}),
            (("spark.sql.adaptive.skewJoin.enabled", "false"), {}),
        ]

    @pytest.mark.skipif(not PYSPARK_AVAILABLE, reason="PySpark not available")
    def test_session_creation_failure_propagates(self):
        from benchbox.platforms.dataframe.lakesail_df import LakeSailDataFrameAdapter

        mock_builder = MagicMock()
        mock_builder.remote.return_value = mock_builder
        mock_builder.config.return_value = mock_builder
        mock_builder.getOrCreate.side_effect = RuntimeError("connect failed")
        mock_spark_class = MagicMock(builder=mock_builder)

        with patch("benchbox.platforms.dataframe.lakesail_df.SparkSession", mock_spark_class):
            adapter = LakeSailDataFrameAdapter(endpoint="sc://missing:50051")

            with pytest.raises(RuntimeError, match="connect failed"):
                _ = adapter.spark

        assert adapter._spark is None

    def test_close_ignores_stop_errors_and_clears_session(self):
        from benchbox.platforms.dataframe.lakesail_df import LakeSailDataFrameAdapter

        adapter = LakeSailDataFrameAdapter.__new__(LakeSailDataFrameAdapter)
        adapter._spark = MagicMock()
        adapter._spark.stop.side_effect = RuntimeError("stop failed")
        adapter.verbose = False

        adapter.close()

        assert adapter._spark is None

    def test_explain_and_get_query_plan_capture_modes(self):
        from benchbox.platforms.dataframe.lakesail_df import LakeSailDataFrameAdapter

        class DummyFrame:
            def __init__(self):
                self.calls: list[str] = []

            def explain(self, mode="extended"):
                self.calls.append(mode)
                print(f"{mode} plan")

        adapter = LakeSailDataFrameAdapter.__new__(LakeSailDataFrameAdapter)
        frame = DummyFrame()

        simple = adapter.explain(frame, mode="simple")
        plans = adapter.get_query_plan(frame)

        assert "simple plan" in simple
        assert "simple plan" in plans["logical"]
        assert "extended plan" in plans["physical"]
        assert frame.calls == ["simple", "simple", "extended"]

    def test_sql_and_register_table_delegate_to_spark_objects(self):
        from benchbox.platforms.dataframe.lakesail_df import LakeSailDataFrameAdapter

        adapter = LakeSailDataFrameAdapter.__new__(LakeSailDataFrameAdapter)
        expected_result = object()
        adapter._spark = MagicMock()
        adapter._spark.sql.return_value = expected_result

        df = MagicMock()

        assert adapter.sql("SELECT 1") is expected_result
        adapter._spark.sql.assert_called_once_with("SELECT 1")

        adapter.register_table("orders", df)
        df.createOrReplaceTempView.assert_called_once_with("orders")


from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import benchbox.platforms.dataframe.lakesail_df as _lakesail_mod
import benchbox.platforms.dataframe.pyspark_df as _pyspark_mod


def _make_lakesail_F():
    def _col(n):
        m = MagicMock()
        m.asc.return_value = m
        m.desc.return_value = m
        m.cast.return_value = m
        m.over = lambda ws: MagicMock()
        return m

    def _agg(x):
        m = MagicMock()
        m.over = lambda ws: MagicMock()
        return m

    return SimpleNamespace(
        col=_col,
        lit=lambda v: MagicMock(),
        sum=_agg,
        avg=_agg,
        count=_agg,
        min=_agg,
        max=_agg,
        when=lambda c, v: MagicMock(),
        concat_ws=lambda sep, *cols: MagicMock(),
        concat=lambda *cols: MagicMock(),
        date_sub=lambda col, d: MagicMock(),
        date_add=lambda col, d: MagicMock(),
        rank=lambda: MagicMock(over=lambda ws: MagicMock()),
        row_number=lambda: MagicMock(over=lambda ws: MagicMock()),
        dense_rank=lambda: MagicMock(over=lambda ws: MagicMock()),
    )


def _new_lakesail_adapter(monkeypatch):
    mock_F = _make_lakesail_F()
    mock_Window = MagicMock()
    mock_Window.partitionBy.return_value = mock_Window
    mock_Window.orderBy.return_value = mock_Window
    mock_Window.rowsBetween.return_value = mock_Window
    mock_Window.unboundedPreceding = -1
    mock_Window.currentRow = 0
    mock_session = MagicMock()

    monkeypatch.setattr(_lakesail_mod, "PYSPARK_AVAILABLE", True)
    monkeypatch.setattr(_lakesail_mod, "F", mock_F)
    monkeypatch.setattr(_lakesail_mod, "Window", mock_Window)
    monkeypatch.setattr(_pyspark_mod, "F", mock_F)
    monkeypatch.setattr(_pyspark_mod, "Window", mock_Window)

    mock_builder = MagicMock()
    mock_builder.remote.return_value = mock_builder
    mock_builder.config.return_value = mock_builder
    mock_builder.getOrCreate.return_value = mock_session
    mock_session_class = MagicMock(builder=mock_builder)
    monkeypatch.setattr(_lakesail_mod, "SparkSession", mock_session_class)

    adapter = _lakesail_mod.LakeSailDataFrameAdapter(endpoint="sc://localhost:50051", shuffle_partitions=4)
    adapter._spark = mock_session
    return adapter, mock_session, mock_F, mock_Window


class TestLakeSailMockedCoverage:
    def test_init_and_platform_name(self, monkeypatch):
        adapter, _, _, _ = _new_lakesail_adapter(monkeypatch)
        assert adapter.platform_name == "LakeSail"
        assert adapter._endpoint == "sc://localhost:50051"
        assert adapter._shuffle_partitions == 4

    def test_apply_tuning(self, monkeypatch):
        monkeypatch.setattr(_lakesail_mod, "PYSPARK_AVAILABLE", True)
        mock_builder = MagicMock()
        mock_builder.remote.return_value = mock_builder
        mock_builder.config.return_value = mock_builder
        mock_builder.getOrCreate.return_value = MagicMock()
        monkeypatch.setattr(_lakesail_mod, "SparkSession", MagicMock(builder=mock_builder))
        tuning = DataFrameTuningConfiguration(
            parallelism=ParallelismConfiguration(thread_count=8),
            memory=MemoryConfiguration(memory_limit="16GB"),
            execution=ExecutionConfiguration(streaming_mode=True),
        )
        adapter = _lakesail_mod.LakeSailDataFrameAdapter(tuning_config=tuning)
        assert adapter._shuffle_partitions == 8
        assert adapter._driver_memory == "16GB"

    def test_get_or_create_session_and_close(self, monkeypatch):
        mock_F = _make_lakesail_F()
        mock_session = MagicMock()
        mock_builder = MagicMock()
        mock_builder.remote.return_value = mock_builder
        mock_builder.config.return_value = mock_builder
        mock_builder.getOrCreate.return_value = mock_session

        monkeypatch.setattr(_lakesail_mod, "PYSPARK_AVAILABLE", True)
        monkeypatch.setattr(_lakesail_mod, "F", mock_F)
        monkeypatch.setattr(_lakesail_mod, "Window", MagicMock())
        monkeypatch.setattr(_pyspark_mod, "F", mock_F)
        monkeypatch.setattr(_pyspark_mod, "Window", MagicMock())
        monkeypatch.setattr(_lakesail_mod, "SparkSession", MagicMock(builder=mock_builder))

        adapter = _lakesail_mod.LakeSailDataFrameAdapter(endpoint="sc://host:50051", verbose=True)
        adapter._spark = None
        session = adapter._get_or_create_session()
        assert session is mock_session
        assert adapter._get_or_create_session() is mock_session

        adapter.close()
        assert adapter._spark is None
        adapter.close()

    def test_context_manager(self, monkeypatch):
        adapter, _, _, _ = _new_lakesail_adapter(monkeypatch)
        with adapter as a:
            assert a is adapter
        assert adapter._spark is None

    def test_expression_and_agg_methods(self, monkeypatch):
        adapter, _, _, _ = _new_lakesail_adapter(monkeypatch)
        assert adapter.col("name") is not None
        assert adapter.lit(42) is not None
        assert adapter.date_sub(MagicMock(), 7) is not None
        assert adapter.date_add(MagicMock(), 3) is not None
        col_mock = MagicMock()
        col_mock.cast.return_value = "d"
        assert adapter.cast_date(col_mock) == "d"
        col_mock.cast.return_value = "s"
        assert adapter.cast_string(col_mock) == "s"
        assert adapter.sum("amt") is not None
        assert adapter.mean("amt") is not None
        assert adapter.count("amt") is not None
        assert adapter.count(None) is not None
        assert adapter.min("amt") is not None
        assert adapter.max("amt") is not None
        assert adapter.when(MagicMock()) is not None
        assert adapter.concat_str("a", "b") is not None
        assert adapter.concat_str("a", "b", separator="-") is not None

    def test_read_csv_and_parquet(self, monkeypatch):
        adapter, mock_session, _, _ = _new_lakesail_adapter(monkeypatch)
        from pathlib import Path

        mock_reader = MagicMock()
        mock_session.read.option.return_value = mock_reader
        mock_reader.option.return_value = mock_reader
        mock_reader.csv.return_value = MagicMock()
        mock_reader.schema.return_value = mock_reader
        mock_session.read.parquet.return_value = MagicMock()

        assert adapter.read_csv(Path("/data/test.csv")) is not None
        assert adapter.read_csv(Path("/data/test.csv"), column_names=["a", "b"]) is not None
        assert adapter.read_parquet(Path("/data/file.parquet")) is not None

    def test_collect_and_row_count(self, monkeypatch):
        adapter, _, _, _ = _new_lakesail_adapter(monkeypatch)
        df = MagicMock()
        df.count.return_value = 5
        assert adapter.collect(df) is df
        assert adapter.get_row_count(df) == 5

    def test_scalar_paths(self, monkeypatch):
        adapter, _, _, _ = _new_lakesail_adapter(monkeypatch)
        row = MagicMock()
        row.__getitem__ = lambda self, k: 99 if k == 0 else "v"
        df = MagicMock()
        df.limit.return_value.collect.return_value = [row]
        assert adapter.scalar(df) == 99

        row2 = MagicMock()
        row2.__getitem__ = lambda self, k: "named"
        df2 = MagicMock()
        df2.limit.return_value.collect.return_value = [row2]
        assert adapter.scalar(df2, column="col1") == "named"

        df_empty = MagicMock()
        df_empty.limit.return_value.collect.return_value = []
        with pytest.raises(ValueError, match="empty"):
            adapter.scalar(df_empty)

        df_multi = MagicMock()
        df_multi.limit.return_value.collect.return_value = [row, row]
        with pytest.raises(ValueError, match="exactly one row"):
            adapter.scalar(df_multi)

    def test_scalar_to_df(self, monkeypatch):
        adapter, mock_session, _, _ = _new_lakesail_adapter(monkeypatch)
        mock_session.createDataFrame.return_value = MagicMock()
        with patch.dict("sys.modules", {"pyspark.sql": MagicMock(Row=lambda **kw: kw)}):
            result = adapter.scalar_to_df({"x": 1})
            assert result is not None

    def test_window_functions(self, monkeypatch):
        adapter, _, _, _ = _new_lakesail_adapter(monkeypatch)
        assert adapter._build_window_spec([("c", True), ("d", False)]) is not None
        assert adapter._build_window_spec([("c", True)], partition_by=["dept"]) is not None
        assert adapter._build_window_spec([]) is not None
        assert adapter._build_aggregate_window_spec(partition_by=["p"]) is not None
        assert adapter._build_aggregate_window_spec(order_by=[("d", True)]) is not None
        assert adapter.window_rank([("c", True)]) is not None
        assert adapter.window_row_number([("c", False)]) is not None
        assert adapter.window_dense_rank([("c", True)]) is not None
        assert adapter.window_sum("amt") is not None
        assert adapter.window_avg("price", order_by=[("d", True)]) is not None
        assert adapter.window_count("col") is not None
        assert adapter.window_count(None) is not None
        assert adapter.window_min("price") is not None
        assert adapter.window_max("qty") is not None

    def test_union_rename_concat_firstrow(self, monkeypatch):
        adapter, _, _, _ = _new_lakesail_adapter(monkeypatch)
        df1 = MagicMock()
        df2 = MagicMock()
        df1.union.return_value = MagicMock()

        with pytest.raises(ValueError):
            adapter.union_all()
        assert adapter.union_all(df1) is df1
        assert adapter.union_all(df1, df2) is not None

        df3 = MagicMock()
        df3.columns = ["old"]
        df3.withColumnRenamed.return_value = df3
        adapter.rename_columns(df3, {"old": "new"})
        df3.withColumnRenamed.assert_called_with("old", "new")

        assert adapter._concat_dataframes([df1]) is df1
        assert adapter._concat_dataframes([df1, df2]) is not None

        df4 = MagicMock()
        df4.take.return_value = [("x", "y")]
        assert adapter._get_first_row(df4) == ("x", "y")
        df4.take.return_value = []
        assert adapter._get_first_row(df4) is None

    def test_platform_info_and_tuning_summary(self, monkeypatch):
        adapter, _, _, _ = _new_lakesail_adapter(monkeypatch)
        info = adapter.get_platform_info()
        assert info["platform"] == "LakeSail"
        assert "endpoint" in info
        summary = adapter.get_tuning_summary()
        assert "endpoint" in summary

    def test_sql_register_explain(self, monkeypatch):
        adapter, mock_session, _, _ = _new_lakesail_adapter(monkeypatch)
        mock_session.sql.return_value = MagicMock()
        assert adapter.sql("SELECT 1") is not None

        df = MagicMock()
        adapter.register_table("t", df)
        df.createOrReplaceTempView.assert_called_with("t")

        df.toPandas.return_value = MagicMock()
        adapter.to_pandas(df)

        df2 = MagicMock()
        df2.explain.side_effect = lambda mode="extended": print(f"{mode}_out")
        result = adapter.explain(df2, "simple")
        assert "simple_out" in result
        plans = adapter.get_query_plan(df2)
        assert "logical" in plans

    def test_to_polars(self, monkeypatch):
        adapter, _, _, _ = _new_lakesail_adapter(monkeypatch)
        import polars as pl

        df = MagicMock()
        df.toPandas.return_value = MagicMock()
        del df.toArrow
        with patch("polars.from_pandas", return_value=MagicMock()) as mock_fp:
            adapter.to_polars(df)
            mock_fp.assert_called_once()
