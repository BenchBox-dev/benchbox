from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import benchbox.platforms.dataframe.pyspark_maintenance as pyspark_maintenance

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_factory_returns_none_when_pyspark_unavailable(monkeypatch):
    monkeypatch.setattr(pyspark_maintenance, "PYSPARK_AVAILABLE", False)

    assert pyspark_maintenance.get_pyspark_maintenance_operations(spark_session=object()) is None


def test_factory_returns_none_without_session(monkeypatch):
    monkeypatch.setattr(pyspark_maintenance, "PYSPARK_AVAILABLE", True)

    assert pyspark_maintenance.get_pyspark_maintenance_operations(spark_session=None) is None


def test_factory_constructs_operations_when_available(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(pyspark_maintenance, "PYSPARK_AVAILABLE", True)
    constructor = MagicMock(return_value=sentinel)
    monkeypatch.setattr(pyspark_maintenance, "PySparkMaintenanceOperations", constructor)

    result = pyspark_maintenance.get_pyspark_maintenance_operations(
        spark_session="spark",
        working_dir="/tmp/work",
        prefer_delta=False,
    )

    assert result is sentinel
    constructor.assert_called_once_with(spark_session="spark", working_dir="/tmp/work", prefer_delta=False)


def test_module_exports_expected_symbols():
    exported = set(pyspark_maintenance.__all__)

    assert "PySparkMaintenanceOperations" in exported
    assert "get_pyspark_maintenance_operations" in exported
    assert "PYSPARK_AVAILABLE" in exported
    assert "DELTA_SPARK_AVAILABLE" in exported


def test_init_raises_import_error_when_pyspark_missing(monkeypatch):
    monkeypatch.setattr(pyspark_maintenance, "PYSPARK_AVAILABLE", False)

    with pytest.raises(ImportError, match="PySpark is not installed"):
        pyspark_maintenance.PySparkMaintenanceOperations(spark_session=object())


def test_get_capabilities_and_delta_detection(monkeypatch, tmp_path):
    monkeypatch.setattr(pyspark_maintenance, "PYSPARK_AVAILABLE", True)
    monkeypatch.setattr(pyspark_maintenance, "DELTA_SPARK_AVAILABLE", True)

    ops = pyspark_maintenance.PySparkMaintenanceOperations(spark_session=MagicMock(), prefer_delta=True)
    assert ops._get_capabilities().platform_name == "pyspark-delta"

    delta_dir = tmp_path / "tbl" / "_delta_log"
    delta_dir.mkdir(parents=True)
    assert ops.is_delta_table(tmp_path / "tbl") is True

    ops.prefer_delta = False
    assert ops._get_capabilities().platform_name == "pyspark-parquet"


def test_execute_bulk_load_covers_csv_zero_rows_branch(monkeypatch):
    monkeypatch.setattr(pyspark_maintenance, "PYSPARK_AVAILABLE", True)
    monkeypatch.setattr(pyspark_maintenance, "DELTA_SPARK_AVAILABLE", False)

    mock_spark = MagicMock()
    reader = MagicMock()
    mock_df = MagicMock()
    mock_df.count.return_value = 0
    mock_spark.read.format.return_value = reader
    reader.option.return_value = reader
    reader.load.return_value = mock_df

    ops = pyspark_maintenance.PySparkMaintenanceOperations(spark_session=mock_spark, prefer_delta=False)
    rows = ops.execute_bulk_load("src.csv", "dst", source_format="csv")

    assert rows == 0
    assert reader.option.call_count >= 2


def test_execute_bulk_load_covers_sort_partition_compression_and_delta(monkeypatch):
    monkeypatch.setattr(pyspark_maintenance, "PYSPARK_AVAILABLE", True)
    monkeypatch.setattr(pyspark_maintenance, "DELTA_SPARK_AVAILABLE", True)

    mock_spark = MagicMock()
    reader = MagicMock()
    mock_df = MagicMock()
    ordered_df = MagicMock()
    writer = MagicMock()
    writer.mode.return_value = writer
    writer.partitionBy.return_value = writer
    writer.option.return_value = writer
    writer.format.return_value = writer

    mock_df.count.return_value = 3
    mock_df.orderBy.return_value = ordered_df
    ordered_df.write = writer
    mock_spark.read.format.return_value = reader
    reader.load.return_value = mock_df

    ops = pyspark_maintenance.PySparkMaintenanceOperations(spark_session=mock_spark, prefer_delta=True)
    rows = ops.execute_bulk_load(
        source_path="src.parquet",
        target_path="dst",
        source_format="parquet",
        target_format="delta",
        compression="zstd",
        partition_columns=["p1"],
        sort_columns=["s1"],
    )

    assert rows == 3
    mock_df.orderBy.assert_called_once_with("s1")
    writer.partitionBy.assert_called_once_with("p1")
    writer.option.assert_called_once_with("compression", "zstd")
    writer.format.assert_called_once_with("delta")


def test_convert_to_spark_df_covers_pandas_polars_list_and_error_paths(monkeypatch):
    pytest.importorskip("pyspark")
    monkeypatch.setattr(pyspark_maintenance, "PYSPARK_AVAILABLE", True)

    spark = MagicMock()
    ops = pyspark_maintenance.PySparkMaintenanceOperations(spark_session=spark, prefer_delta=False)

    pandas_like = SimpleNamespace(columns=["a"], to_dict=lambda *a, **k: {})
    polars_like = SimpleNamespace(to_pandas=lambda: {"a": [1]})

    assert ops._convert_to_spark_df(pandas_like) == spark.createDataFrame.return_value
    assert ops._convert_to_spark_df(polars_like) == spark.createDataFrame.return_value
    assert ops._convert_to_spark_df([{"a": 1}]) == spark.createDataFrame.return_value

    with pytest.raises(TypeError, match="Unsupported DataFrame type"):
        ops._convert_to_spark_df(42)


def test_to_spark_column_covers_prefixes_and_literals(monkeypatch):
    pytest.importorskip("pyspark")
    monkeypatch.setattr(pyspark_maintenance, "PYSPARK_AVAILABLE", True)
    import pyspark.sql.functions as spark_functions

    ops = pyspark_maintenance.PySparkMaintenanceOperations(spark_session=MagicMock(), prefer_delta=False)
    monkeypatch.setattr(spark_functions, "lit", lambda value: ("lit", value))
    monkeypatch.setattr(spark_functions, "col", lambda value: ("col", value))
    monkeypatch.setattr(spark_functions, "expr", lambda value: ("expr", value))

    literal_col = ops._to_spark_column(10)
    col_ref = ops._to_spark_column("col:source.id")
    expr_col = ops._to_spark_column("expr:amount * 2")
    lit_col = ops._to_spark_column("lit:done")
    merge_col = ops._to_spark_column("source.name")
    default_expr = ops._to_spark_column("upper(name)")

    assert literal_col == ("lit", 10)
    assert col_ref == ("col", "source.id")
    assert expr_col == ("expr", "amount * 2")
    assert lit_col == ("lit", "done")
    assert merge_col == ("col", "source.name")
    assert default_expr == ("expr", "upper(name)")


def test_do_insert_covers_zero_rows_and_partitioned_delta_paths(monkeypatch):
    monkeypatch.setattr(pyspark_maintenance, "PYSPARK_AVAILABLE", True)
    monkeypatch.setattr(pyspark_maintenance, "DELTA_SPARK_AVAILABLE", True)

    spark = MagicMock()
    ops = pyspark_maintenance.PySparkMaintenanceOperations(spark_session=spark, prefer_delta=True)

    zero_df = MagicMock()
    zero_df.count.return_value = 0
    monkeypatch.setattr(ops, "_convert_to_spark_df", lambda _df: zero_df)
    assert ops._do_insert("tbl", object(), partition_columns=None, mode="append") == 0

    data_df = MagicMock()
    data_df.count.return_value = 2
    writer = MagicMock()
    writer.mode.return_value = writer
    writer.partitionBy.return_value = writer
    writer.format.return_value = writer
    data_df.write = writer
    monkeypatch.setattr(ops, "_convert_to_spark_df", lambda _df: data_df)
    monkeypatch.setattr(ops, "is_delta_table", lambda _path: True)

    assert ops._do_insert("tbl", object(), partition_columns=["p"], mode="overwrite") == 2
    writer.partitionBy.assert_called_once_with("p")
    writer.format.assert_called_once_with("delta")


def test_row_level_operations_raise_when_delta_support_is_unavailable(monkeypatch):
    monkeypatch.setattr(pyspark_maintenance, "PYSPARK_AVAILABLE", True)
    monkeypatch.setattr(pyspark_maintenance, "DELTA_SPARK_AVAILABLE", False)
    ops = pyspark_maintenance.PySparkMaintenanceOperations(spark_session=MagicMock(), prefer_delta=True)

    with pytest.raises(NotImplementedError, match="DELETE requires Delta Lake"):
        ops._do_delete("tbl", "id > 1")
    with pytest.raises(NotImplementedError, match="UPDATE requires Delta Lake"):
        ops._do_update("tbl", "id > 1", {"x": "1"})
    with pytest.raises(NotImplementedError, match="MERGE requires Delta Lake"):
        ops._do_merge("tbl", [{"id": 1}], "target.id = source.id", None, None)


def test_arrow_table_goes_direct_to_spark_on_spark4(monkeypatch):
    pa = pytest.importorskip("pyarrow")
    monkeypatch.setattr(pyspark_maintenance, "_supports_direct_arrow_ingest", lambda: True)
    spark = MagicMock()
    table = pa.table({"a": [1, None]})

    result = pyspark_maintenance.create_spark_dataframe_from_arrow(spark, table)

    spark.createDataFrame.assert_called_once_with(table)
    assert result is spark.createDataFrame.return_value
    spark.conf.set.assert_not_called()


def test_arrow_table_pre_spark4_enables_arrow_conversion_and_restores(monkeypatch):
    pa = pytest.importorskip("pyarrow")
    pytest.importorskip("pandas")
    monkeypatch.setattr(pyspark_maintenance, "_supports_direct_arrow_ingest", lambda: False)
    spark = MagicMock()
    spark.conf.get.return_value = "false"
    seen = []
    spark.createDataFrame.side_effect = lambda df: seen.append(list(spark.conf.set.call_args_list)) or "df"

    result = pyspark_maintenance.create_spark_dataframe_from_arrow(spark, pa.table({"a": [1, 2]}))

    key = "spark.sql.execution.arrow.pyspark.enabled"
    assert result == "df"
    assert seen[0][-1].args == (key, "true")
    assert spark.conf.set.call_args_list[-1].args == (key, "false")


def test_pandas_conversion_unsets_arrow_key_when_previously_unset():
    spark = MagicMock()
    spark.conf.get.return_value = None

    pyspark_maintenance.create_spark_dataframe_from_pandas(spark, object())

    spark.conf.unset.assert_called_once_with("spark.sql.execution.arrow.pyspark.enabled")


def test_convert_arrow_table_uses_arrow_path(monkeypatch):
    pa = pytest.importorskip("pyarrow")
    pytest.importorskip("pyspark")
    monkeypatch.setattr(pyspark_maintenance, "PYSPARK_AVAILABLE", True)
    monkeypatch.setattr(pyspark_maintenance, "_supports_direct_arrow_ingest", lambda: True)
    spark = MagicMock()
    ops = pyspark_maintenance.PySparkMaintenanceOperations(spark_session=spark, prefer_delta=False)
    table = pa.table({"a": [1]})

    ops._convert_to_spark_df(table)

    spark.createDataFrame.assert_called_once_with(table)


