# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import benchbox.platforms.dataframe.pyspark_df as _mod
from benchbox.core.dataframe.tuning import (
    DataFrameTuningConfiguration,
    ExecutionConfiguration,
    MemoryConfiguration,
    ParallelismConfiguration,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _make_F():
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


def _make_Window():
    w = MagicMock()
    w.partitionBy.return_value = w
    w.orderBy.return_value = w
    w.rowsBetween.return_value = w
    w.unboundedPreceding = -sys.maxsize
    w.currentRow = 0
    return w


def _new_adapter(monkeypatch):

    mock_F = _make_F()
    mock_Window = _make_Window()
    mock_session = MagicMock()

    monkeypatch.setattr(_mod, "PYSPARK_AVAILABLE", True)
    monkeypatch.setattr(_mod, "F", mock_F)
    monkeypatch.setattr(_mod, "Window", mock_Window)

    import benchbox.platforms.pyspark as _ps

    monkeypatch.setattr(_ps.SparkSessionManager, "get_or_create", lambda **kw: mock_session)
    monkeypatch.setattr(_ps.SparkSessionManager, "release", lambda: None)

    adapter = _mod.PySparkDataFrameAdapter(master="local[2]", shuffle_partitions=4)
    adapter._spark = mock_session
    adapter._session_claimed = True
    return adapter, mock_session, mock_F, mock_Window


class TestPySparkCoverageMocked:
    def test_init_and_platform_name(self, monkeypatch):
        adapter, _, _, _ = _new_adapter(monkeypatch)
        assert adapter.platform_name == "PySpark"
        assert adapter._master == "local[2]"
        assert adapter._shuffle_partitions == 4

    def test_apply_tuning_parallelism_and_memory(self, monkeypatch):
        monkeypatch.setattr(_mod, "PYSPARK_AVAILABLE", True)
        import benchbox.platforms.pyspark as _ps

        monkeypatch.setattr(_ps.SparkSessionManager, "get_or_create", lambda **kw: MagicMock())
        monkeypatch.setattr(_ps.SparkSessionManager, "release", lambda: None)
        tuning = DataFrameTuningConfiguration(
            parallelism=ParallelismConfiguration(thread_count=6),
            memory=MemoryConfiguration(memory_limit="8GB"),
            execution=ExecutionConfiguration(streaming_mode=True),
        )
        adapter = _mod.PySparkDataFrameAdapter(tuning_config=tuning)
        assert adapter._master == "local[6]"
        assert adapter._shuffle_partitions == 6
        assert adapter._driver_memory == "8GB"

    def test_get_or_create_session(self, monkeypatch):
        mock_session = MagicMock()
        mock_session.sparkContext.master = "local[2]"
        monkeypatch.setattr(_mod, "PYSPARK_AVAILABLE", True)
        monkeypatch.setattr(_mod, "F", _make_F())
        monkeypatch.setattr(_mod, "Window", _make_Window())
        import benchbox.platforms.pyspark as _ps

        monkeypatch.setattr(_ps.SparkSessionManager, "get_or_create", lambda **kw: mock_session)
        monkeypatch.setattr(_ps.SparkSessionManager, "release", lambda: None)

        adapter = _mod.PySparkDataFrameAdapter(verbose=True)
        adapter._spark = None
        adapter._session_claimed = False
        assert adapter._get_or_create_session() is mock_session
        assert adapter._session_claimed is True

        assert adapter._get_or_create_session() is mock_session

    def test_close_and_context_manager(self, monkeypatch):
        adapter, _, _, _ = _new_adapter(monkeypatch)
        import benchbox.platforms.pyspark as _ps

        released = []
        monkeypatch.setattr(_ps.SparkSessionManager, "release", lambda: released.append(True))
        adapter.close()
        assert released
        assert adapter._spark is None
        adapter.close()

    def test_context_manager_protocol(self, monkeypatch):
        adapter, _, _, _ = _new_adapter(monkeypatch)
        import benchbox.platforms.pyspark as _ps

        monkeypatch.setattr(_ps.SparkSessionManager, "release", lambda: None)
        with adapter as a:
            assert a is adapter
        assert adapter._spark is None

    def test_expression_methods(self, monkeypatch):
        adapter, _, _, _ = _new_adapter(monkeypatch)
        assert adapter.col("x") is not None
        assert adapter.lit(1) is not None
        assert adapter.date_sub(MagicMock(), 7) is not None
        assert adapter.date_add(MagicMock(), 3) is not None
        col_mock = MagicMock()
        col_mock.cast.return_value = "d"
        assert adapter.cast_date(col_mock) == "d"
        col_mock.cast.return_value = "s"
        assert adapter.cast_string(col_mock) == "s"

    def test_aggregation_methods(self, monkeypatch):
        adapter, _, _, _ = _new_adapter(monkeypatch)
        assert adapter.sum("price") is not None
        assert adapter.mean("price") is not None
        assert adapter.count("price") is not None
        assert adapter.count(None) is not None
        assert adapter.min("price") is not None
        assert adapter.max("price") is not None
        assert adapter.when(MagicMock()) is not None
        assert adapter.concat_str("a", "b") is not None
        assert adapter.concat_str("a", "b", separator="-") is not None

    def test_read_csv_branches(self, monkeypatch):
        adapter, mock_session, _, _ = _new_adapter(monkeypatch)
        mock_reader = MagicMock()
        mock_session.read.option.return_value = mock_reader
        mock_reader.option.return_value = mock_reader
        mock_reader.csv.return_value = MagicMock()
        mock_reader.schema.return_value = mock_reader
        assert adapter.read_csv(Path("/data/file.csv")) is not None
        assert adapter.read_csv(Path("/data/file.csv"), column_names=["a", "b"]) is not None

    def test_read_parquet(self, monkeypatch):
        adapter, mock_session, _, _ = _new_adapter(monkeypatch)
        mock_session.read.parquet.return_value = MagicMock()
        assert adapter.read_parquet(Path("/data/file.parquet")) is not None

    def test_collect_and_row_count(self, monkeypatch):
        adapter, _, _, _ = _new_adapter(monkeypatch)
        df = MagicMock()
        df.count.return_value = 10
        assert adapter.collect(df) is df
        assert adapter.get_row_count(df) == 10

    def test_scalar_paths(self, monkeypatch):
        adapter, _, _, _ = _new_adapter(monkeypatch)
        row = MagicMock()
        row.__getitem__ = lambda self, idx: 99 if idx == 0 else "x"
        df = MagicMock()
        df.limit.return_value.collect.return_value = [row]
        assert adapter.scalar(df) == 99

        row2 = MagicMock()
        row2.__getitem__ = lambda self, k: "val"
        df2 = MagicMock()
        df2.limit.return_value.collect.return_value = [row2]
        assert adapter.scalar(df2, column="c") == "val"

        df_empty = MagicMock()
        df_empty.limit.return_value.collect.return_value = []
        with pytest.raises(ValueError, match="empty"):
            adapter.scalar(df_empty)

        df_multi = MagicMock()
        df_multi.limit.return_value.collect.return_value = [row, row]
        with pytest.raises(ValueError, match="exactly one row"):
            adapter.scalar(df_multi)

    def test_scalar_to_df(self, monkeypatch):
        adapter, mock_session, _, _ = _new_adapter(monkeypatch)
        mock_session.createDataFrame.return_value = MagicMock()
        with patch.dict("sys.modules", {"pyspark.sql": MagicMock(Row=lambda **kw: kw)}):
            result = adapter.scalar_to_df({"x": 1})
            assert result is not None

    def test_build_window_spec(self, monkeypatch):
        adapter, _, _, _ = _new_adapter(monkeypatch)
        assert adapter._build_window_spec([("c", True), ("d", False)]) is not None
        assert adapter._build_window_spec([("c", True)], partition_by=["dept"]) is not None
        assert adapter._build_window_spec([]) is not None

    def test_build_aggregate_window_spec(self, monkeypatch):
        adapter, _, _, _ = _new_adapter(monkeypatch)
        assert adapter._build_aggregate_window_spec(partition_by=["dept"]) is not None
        assert adapter._build_aggregate_window_spec(order_by=[("date", True)]) is not None

    def test_window_functions(self, monkeypatch):
        adapter, _, _, _ = _new_adapter(monkeypatch)
        assert adapter.window_rank([("c", True)]) is not None
        assert adapter.window_row_number([("c", False)]) is not None
        assert adapter.window_dense_rank([("c", True)]) is not None
        assert adapter.window_sum("amt") is not None
        assert adapter.window_avg("price", order_by=[("d", True)]) is not None
        assert adapter.window_count("col") is not None
        assert adapter.window_count(None) is not None
        assert adapter.window_min("price") is not None
        assert adapter.window_max("qty") is not None

    def test_union_all_paths(self, monkeypatch):
        adapter, _, _, _ = _new_adapter(monkeypatch)
        df1, df2, df3 = MagicMock(), MagicMock(), MagicMock()
        df1.union.return_value = MagicMock()
        with pytest.raises(ValueError, match="At least one"):
            adapter.union_all()
        assert adapter.union_all(df1) is df1
        assert adapter.union_all(df1, df2, df3) is not None

    def test_rename_and_concat(self, monkeypatch):
        adapter, _, _, _ = _new_adapter(monkeypatch)
        df = MagicMock()
        df.columns = ["old"]
        df.withColumnRenamed.return_value = df
        adapter.rename_columns(df, {"old": "new"})
        df.withColumnRenamed.assert_called_with("old", "new")

        df1, df2 = MagicMock(), MagicMock()
        df1.union.return_value = MagicMock()
        assert adapter._concat_dataframes([df1]) is df1
        assert adapter._concat_dataframes([df1, df2]) is not None

    def test_get_first_row(self, monkeypatch):
        adapter, _, _, _ = _new_adapter(monkeypatch)
        df = MagicMock()
        df.take.return_value = [("a", "b")]
        assert adapter._get_first_row(df) == ("a", "b")
        df.take.return_value = []
        assert adapter._get_first_row(df) is None

    def test_platform_info_and_tuning_summary(self, monkeypatch):
        adapter, _, _, _ = _new_adapter(monkeypatch)
        info = adapter.get_platform_info()
        assert info["platform"] == "PySpark"
        assert "master" in info
        summary = adapter.get_tuning_summary()
        assert "master" in summary

    def test_explain_and_query_plan(self, monkeypatch):
        adapter, _, _, _ = _new_adapter(monkeypatch)
        df = MagicMock()
        df.explain.side_effect = lambda mode="extended": print(f"{mode}_plan")
        assert "simple_plan" in adapter.explain(df, "simple")
        plans = adapter.get_query_plan(df)
        assert "logical" in plans and "physical" in plans

    def test_sql_register_pandas(self, monkeypatch):
        adapter, mock_session, _, _ = _new_adapter(monkeypatch)
        mock_session.sql.return_value = MagicMock()
        adapter.sql("SELECT 1")
        mock_session.sql.assert_called_with("SELECT 1")
        df = MagicMock()
        adapter.register_table("t", df)
        df.createOrReplaceTempView.assert_called_with("t")
        df.toPandas.return_value = MagicMock()
        adapter.to_pandas(df)
        df.toPandas.assert_called_once()

    def test_to_polars_via_pandas(self, monkeypatch):
        adapter, _, _, _ = _new_adapter(monkeypatch)
        df = MagicMock()
        df.toPandas.return_value = MagicMock()
        del df.toArrow
        with patch("polars.from_pandas", return_value=MagicMock()) as mock_fp:
            adapter.to_polars(df)
            mock_fp.assert_called_once()


