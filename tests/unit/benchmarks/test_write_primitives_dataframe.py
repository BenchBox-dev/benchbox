# Copyright 2026 Joe Harris / BenchBox Project

import shutil
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


try:
    import polars as pl

    POLARS_AVAILABLE = True
except ImportError:
    POLARS_AVAILABLE = False
    pl = None

try:
    import pandas as pd

    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    pd = None

try:
    from benchbox.platforms.dataframe.pyspark_maintenance import (
        DELTA_SPARK_AVAILABLE,
        PYSPARK_AVAILABLE,
        PYSPARK_DELTA_CAPABILITIES,
        PYSPARK_PARQUET_CAPABILITIES,
        PySparkMaintenanceOperations,
        get_pyspark_maintenance_operations,
    )

    PYSPARK_MAINTENANCE_AVAILABLE = True
    PYSPARK_MAINTENANCE_IMPORT_ERROR = ""
except ImportError as exc:
    PYSPARK_MAINTENANCE_AVAILABLE = False
    PYSPARK_MAINTENANCE_IMPORT_ERROR = str(exc)
    DELTA_SPARK_AVAILABLE = False
    PYSPARK_AVAILABLE = False
    PYSPARK_DELTA_CAPABILITIES = None
    PYSPARK_PARQUET_CAPABILITIES = None
    PySparkMaintenanceOperations = None
    get_pyspark_maintenance_operations = None

pyspark_maintenance_skip = pytest.mark.skipif(
    not PYSPARK_MAINTENANCE_AVAILABLE,
    reason=f"PySpark maintenance module not available: {PYSPARK_MAINTENANCE_IMPORT_ERROR}",
)

from benchbox.core.write_primitives import (
    DataFrameWriteCapabilities,
    DataFrameWriteOperationsManager,
    DataFrameWriteResult,
    WriteOperationType,
    WritePrimitivesBenchmark,
    get_dataframe_write_manager,
)


class TestWritePrimitivesBenchmarkDataFrameSupport:
    def test_supports_dataframe_mode(self):

        benchmark = WritePrimitivesBenchmark()
        assert benchmark.supports_dataframe_mode() is True
        assert benchmark.skip_dataframe_data_loading() is True

    def test_get_dataframe_operations_polars(self):

        benchmark = WritePrimitivesBenchmark()
        manager = benchmark.get_dataframe_operations("polars-df")

        if POLARS_AVAILABLE:
            assert manager is not None
            assert isinstance(manager, DataFrameWriteOperationsManager)
        else:
            assert manager is not None

    def test_get_dataframe_operations_unknown_platform(self):

        benchmark = WritePrimitivesBenchmark()
        manager = benchmark.get_dataframe_operations("sqlite")
        assert manager is None

    def test_get_dataframe_capabilities(self):

        benchmark = WritePrimitivesBenchmark()
        caps = benchmark.get_dataframe_capabilities("polars-df")

        if POLARS_AVAILABLE:
            assert caps is not None
            assert isinstance(caps, DataFrameWriteCapabilities)
            assert caps.platform_name == "polars-df"

    def test_execute_dataframe_workload_delegates_to_sql_parity_path(self, monkeypatch):
        benchmark = WritePrimitivesBenchmark()
        parity_rows = [
            {"query_id": "insert_single_row", "status": "SUCCESS", "execution_time": 0.001, "rows_returned": 1}
        ]

        monkeypatch.setattr(
            benchmark,
            "_execute_dataframe_sql_parity_workload",
            lambda **_kwargs: parity_rows,
        )

        class DummyAdapter:
            platform_name = "polars-df"

        results = benchmark.execute_dataframe_workload(
            ctx=None,
            adapter=DummyAdapter(),
            benchmark_config=None,
            query_filter={"INSERT_SINGLE_ROW"},
            monitor=None,
            run_options=None,
        )

        assert len(results) == 4
        assert results[0]["run_type"] == "warmup"
        assert results[0]["iteration"] == 0
        assert results[1]["run_type"] == "measurement"
        assert results[1]["iteration"] == 1
        assert results[3]["iteration"] == 3
        assert all(r["query_id"] == "insert_single_row" for r in results)


class TestWriteOperationType:
    def test_all_operation_types(self):

        assert WriteOperationType.INSERT.value == "insert"
        assert WriteOperationType.UPDATE.value == "update"
        assert WriteOperationType.DELETE.value == "delete"
        assert WriteOperationType.MERGE.value == "merge"
        assert WriteOperationType.BULK_LOAD.value == "bulk_load"
        assert WriteOperationType.TRANSACTION.value == "transaction"


class TestDataFrameWriteCapabilities:
    def test_supports_operation_bulk_load(self):

        caps = DataFrameWriteCapabilities(
            platform_name="test",
            supports_bulk_load=True,
        )
        assert caps.supports_operation(WriteOperationType.BULK_LOAD) is True

        caps_no = DataFrameWriteCapabilities(
            platform_name="test",
            supports_bulk_load=False,
        )
        assert caps_no.supports_operation(WriteOperationType.BULK_LOAD) is False

    def test_supports_operation_without_maintenance_caps(self):

        caps = DataFrameWriteCapabilities(
            platform_name="test",
            maintenance_caps=None,
        )
        assert caps.supports_operation(WriteOperationType.INSERT) is False
        assert caps.supports_operation(WriteOperationType.UPDATE) is False
        assert caps.supports_operation(WriteOperationType.DELETE) is False
        assert caps.supports_operation(WriteOperationType.MERGE) is False

    def test_get_unsupported_operations(self):

        caps = DataFrameWriteCapabilities(
            platform_name="test",
            maintenance_caps=None,
            supports_bulk_load=True,
        )
        unsupported = caps.get_unsupported_operations()

        assert WriteOperationType.INSERT in unsupported
        assert WriteOperationType.UPDATE in unsupported
        assert WriteOperationType.BULK_LOAD not in unsupported


class TestDataFrameWriteResult:
    def test_failure_factory(self):

        result = DataFrameWriteResult.failure(
            WriteOperationType.INSERT,
            "Table not found",
        )

        assert result.success is False
        assert result.rows_affected == 0
        assert result.error_message == "Table not found"
        assert result.operation_type == WriteOperationType.INSERT
        assert result.validation_passed is False

    def test_failure_with_start_time(self):

        import time

        start = time.time() - 2.0
        result = DataFrameWriteResult.failure(
            WriteOperationType.UPDATE,
            "Constraint violation",
            start_time=start,
        )

        assert result.success is False
        assert result.start_time == start
        assert result.duration_ms >= 2000


class TestGetDataFrameWriteManager:
    def test_polars_manager(self):

        manager = get_dataframe_write_manager("polars-df")
        if POLARS_AVAILABLE:
            assert manager is not None
            assert "polars" in manager.platform_name
        else:
            assert manager is not None

    def test_pandas_manager(self):

        manager = get_dataframe_write_manager("pandas-df")
        if PANDAS_AVAILABLE:
            assert manager is not None
            assert "pandas" in manager.platform_name

    def test_unknown_platform_returns_none(self):

        manager = get_dataframe_write_manager("duckdb")
        assert manager is None

        manager = get_dataframe_write_manager("sqlite")
        assert manager is None

    def test_case_insensitive(self):

        manager1 = get_dataframe_write_manager("Polars-DF")
        manager2 = get_dataframe_write_manager("POLARS-DF")
        assert (manager1 is None) == (manager2 is None)


@pytest.mark.skipif(not POLARS_AVAILABLE, reason="Polars not installed")
class TestPolarsWriteOperations:
    @pytest.fixture
    def temp_table_dir(self, tmp_path: Path) -> Path:
        table_dir = tmp_path / "test_table"
        table_dir.mkdir()
        return table_dir

    @pytest.fixture
    def sample_df(self):
        return pl.DataFrame(
            {
                "id": [1, 2, 3, 4, 5],
                "name": ["Alice", "Bob", "Charlie", "Diana", "Eve"],
                "amount": [100.0, 200.0, 150.0, 300.0, 250.0],
                "region": ["US", "EU", "US", "EU", "APAC"],
            }
        )

    @pytest.fixture
    def existing_table(self, temp_table_dir: Path, sample_df):
        sample_df.write_parquet(temp_table_dir / "part-00000.parquet")
        return temp_table_dir

    @pytest.fixture
    def manager(self):
        return get_dataframe_write_manager("polars-df")

    def test_polars_capabilities(self, manager):

        caps = manager.get_capabilities()

        assert caps.platform_name == "polars-df"
        assert caps.supports_operation(WriteOperationType.INSERT) is True
        assert caps.supports_operation(WriteOperationType.UPDATE) is True
        assert caps.supports_operation(WriteOperationType.DELETE) is True
        assert caps.supports_operation(WriteOperationType.MERGE) is True
        assert caps.supports_operation(WriteOperationType.BULK_LOAD) is True
        assert caps.supports_operation(WriteOperationType.TRANSACTION) is False

    def test_insert_new_rows(self, manager, temp_table_dir: Path, sample_df):

        result = manager.execute_insert(temp_table_dir, sample_df, mode="append")

        assert result.success is True
        assert result.rows_affected == 5
        assert result.operation_type == WriteOperationType.INSERT

        written = pl.read_parquet(temp_table_dir / "part-00000.parquet")
        assert written.height == 5

    def test_insert_append_mode(self, manager, existing_table: Path, sample_df):

        new_df = pl.DataFrame(
            {"id": [6, 7], "name": ["Frank", "Grace"], "amount": [400.0, 500.0], "region": ["US", "EU"]}
        )

        result = manager.execute_insert(existing_table, new_df, mode="append")

        assert result.success is True
        assert result.rows_affected == 2

        all_data = pl.read_parquet(existing_table / "*.parquet")
        assert all_data.height == 7

    def test_update_rows(self, manager, existing_table: Path):

        result = manager.execute_update(
            existing_table,
            "region = 'EU'",
            {"amount": "0"},
        )

        assert result.success is True
        assert result.rows_affected == 2

        df = pl.read_parquet(existing_table / "*.parquet")
        eu_rows = df.filter(pl.col("region") == "EU")
        assert eu_rows["amount"].to_list() == [0, 0]

    def test_delete_rows(self, manager, existing_table: Path):

        result = manager.execute_delete(existing_table, "amount > 200")

        assert result.success is True
        assert result.rows_affected == 2

        remaining = pl.read_parquet(existing_table / "*.parquet")
        assert remaining.height == 3

    def test_merge_rows(self, manager, existing_table: Path):
        source_df = pl.DataFrame(
            {
                "id": [1, 100],
                "name": ["Alice Updated", "NewPerson"],
                "amount": [111.0, 100.0],
                "region": ["US", "EU"],
            }
        )

        result = manager.execute_merge(
            existing_table,
            source_df,
            merge_condition="id",
            when_matched={"name": "source.name"},
            when_not_matched={"id": "source.id", "name": "source.name"},
        )

        assert result.success is True
        assert result.rows_affected == 2

        df = pl.read_parquet(existing_table / "*.parquet")
        assert df.height == 6

        alice = df.filter(pl.col("id") == 1)
        assert alice["name"][0] == "Alice Updated"


@pytest.mark.skipif(not POLARS_AVAILABLE, reason="Polars not installed")
class TestPolarsBulkLoad:
    @pytest.fixture
    def source_csv(self, tmp_path: Path) -> Path:
        csv_path = tmp_path / "source.csv"
        df = pl.DataFrame(
            {
                "id": [1, 2, 3],
                "value": ["a", "b", "c"],
            }
        )
        df.write_csv(csv_path)
        return csv_path

    @pytest.fixture
    def source_parquet(self, tmp_path: Path) -> Path:
        parquet_path = tmp_path / "source.parquet"
        df = pl.DataFrame(
            {
                "id": [1, 2, 3, 4, 5],
                "name": ["Alice", "Bob", "Charlie", "Diana", "Eve"],
                "amount": [100.0, 200.0, 150.0, 300.0, 250.0],
            }
        )
        df.write_parquet(parquet_path)
        return parquet_path

    @pytest.fixture
    def manager(self):
        return get_dataframe_write_manager("polars-df")

    def test_bulk_load_csv(self, manager, source_csv: Path, tmp_path: Path):

        target_path = tmp_path / "target_table"

        result = manager.execute_bulk_load(
            source_path=source_csv,
            target_path=target_path,
            source_format="csv",
            target_format="parquet",
            compression="zstd",
        )

        assert result.success is True
        assert result.rows_affected == 3
        assert result.compression == "zstd"
        assert result.file_count == 1
        assert result.bytes_written is not None
        assert result.bytes_written > 0

        loaded = pl.read_parquet(target_path / "*.parquet")
        assert loaded.height == 3

    def test_bulk_load_parquet(self, manager, source_parquet: Path, tmp_path: Path):

        target_path = tmp_path / "target_table"

        result = manager.execute_bulk_load(
            source_path=source_parquet,
            target_path=target_path,
            source_format="parquet",
            target_format="parquet",
            compression="snappy",
        )

        assert result.success is True
        assert result.rows_affected == 5
        assert result.compression == "snappy"

    def test_bulk_load_with_sorting(self, manager, source_parquet: Path, tmp_path: Path):

        target_path = tmp_path / "target_table"

        result = manager.execute_bulk_load(
            source_path=source_parquet,
            target_path=target_path,
            source_format="parquet",
            sort_columns=["amount"],
        )

        assert result.success is True

        loaded = pl.read_parquet(target_path / "*.parquet")
        amounts = loaded["amount"].to_list()
        assert amounts == sorted(amounts)

    def test_bulk_load_with_partitioning(self, manager, tmp_path: Path):

        source_path = tmp_path / "source.parquet"
        df = pl.DataFrame(
            {
                "id": [1, 2, 3, 4],
                "region": ["US", "US", "EU", "EU"],
                "value": [10, 20, 30, 40],
            }
        )
        df.write_parquet(source_path)

        target_path = tmp_path / "target_table"

        result = manager.execute_bulk_load(
            source_path=source_path,
            target_path=target_path,
            source_format="parquet",
            partition_columns=["region"],
        )

        assert result.success is True
        assert result.rows_affected == 4
        assert result.file_count == 2

        assert (target_path / "region=US").exists()
        assert (target_path / "region=EU").exists()


@pytest.mark.skipif(not PANDAS_AVAILABLE, reason="Pandas not installed")
class TestPandasWriteOperations:
    @pytest.fixture
    def manager(self):
        return get_dataframe_write_manager("pandas-df")

    def test_pandas_capabilities(self, manager):

        caps = manager.get_capabilities()

        assert caps.platform_name == "pandas-df"
        assert caps.supports_operation(WriteOperationType.BULK_LOAD) is True
        assert caps.supports_operation(WriteOperationType.UPDATE) is False
        assert caps.supports_operation(WriteOperationType.DELETE) is False
        assert caps.supports_operation(WriteOperationType.MERGE) is False

    def test_unsupported_operation_message(self, manager):

        result = manager.execute_update(
            "/some/path",
            "id = 1",
            {"name": "'Updated'"},
        )

        assert result.success is False
        assert "polars-df" in result.error_message.lower() or "pyspark-df" in result.error_message.lower()

    def test_bulk_load_parquet(self, manager, tmp_path: Path):

        source_path = tmp_path / "source.parquet"
        df = pd.DataFrame(
            {
                "id": [1, 2, 3],
                "name": ["A", "B", "C"],
            }
        )
        df.to_parquet(source_path, index=False)

        target_path = tmp_path / "target_table"

        result = manager.execute_bulk_load(
            source_path=source_path,
            target_path=target_path,
            source_format="parquet",
        )

        assert result.success is True
        assert result.rows_affected == 3


@pyspark_maintenance_skip
class TestPySparkCapabilities:
    def test_delta_capabilities_full_acid(self):

        caps = PYSPARK_DELTA_CAPABILITIES

        assert caps.platform_name == "pyspark-delta"
        assert caps.supports_insert is True
        assert caps.supports_delete is True
        assert caps.supports_update is True
        assert caps.supports_merge is True
        assert caps.supports_transactions is True
        assert caps.supports_row_level_delete is True
        assert caps.supports_time_travel is True

    def test_parquet_capabilities_limited(self):

        caps = PYSPARK_PARQUET_CAPABILITIES

        assert caps.platform_name == "pyspark-parquet"
        assert caps.supports_insert is True
        assert caps.supports_delete is False
        assert caps.supports_update is False
        assert caps.supports_merge is False
        assert caps.supports_transactions is False
        assert caps.supports_row_level_delete is False


@pyspark_maintenance_skip
class TestPySparkMaintenanceInit:
    def test_requires_spark_session(self):

        with pytest.raises(ValueError, match="spark_session is required"):
            PySparkMaintenanceOperations(spark_session=None)

    def test_factory_returns_none_without_session(self):

        result = get_pyspark_maintenance_operations(spark_session=None)
        assert result is None


@pyspark_maintenance_skip
class TestPySparkInsertWithMock:
    def test_insert_uses_dataframe_write_api(self):
        if not PYSPARK_AVAILABLE:
            pytest.skip("PySpark not installed")

        from unittest.mock import MagicMock, patch

        mock_spark = MagicMock()
        mock_df = MagicMock()
        mock_df.count.return_value = 5

        mock_writer = MagicMock()
        mock_df.write = mock_writer
        mock_writer.mode.return_value = mock_writer
        mock_writer.partitionBy.return_value = mock_writer
        mock_writer.format.return_value = mock_writer

        ops = PySparkMaintenanceOperations(spark_session=mock_spark, prefer_delta=False)

        with patch.object(ops, "_convert_to_spark_df", return_value=mock_df):
            with patch.object(ops, "is_delta_table", return_value=False):
                result = ops._do_insert(
                    table_path="/tmp/test_table",
                    dataframe=mock_df,
                    partition_columns=None,
                    mode="append",
                )

        mock_writer.mode.assert_called_once_with("append")
        mock_writer.parquet.assert_called_once_with("/tmp/test_table")

        mock_spark.sql.assert_not_called()

        assert result == 5


@pyspark_maintenance_skip
class TestPySparkBulkLoadWithMock:
    def test_bulk_load_uses_read_write_pattern(self):
        if not PYSPARK_AVAILABLE:
            pytest.skip("PySpark not installed")

        from unittest.mock import MagicMock

        mock_spark = MagicMock()

        mock_reader = MagicMock()
        mock_spark.read = mock_reader
        mock_reader.format.return_value = mock_reader
        mock_reader.option.return_value = mock_reader

        mock_df = MagicMock()
        mock_df.count.return_value = 100
        mock_reader.load.return_value = mock_df

        mock_writer = MagicMock()
        mock_df.write = mock_writer
        mock_writer.mode.return_value = mock_writer
        mock_writer.partitionBy.return_value = mock_writer
        mock_writer.option.return_value = mock_writer
        mock_writer.format.return_value = mock_writer

        ops = PySparkMaintenanceOperations(spark_session=mock_spark)

        result = ops.execute_bulk_load(
            source_path="/tmp/source.parquet",
            target_path="/tmp/target",
            source_format="parquet",
            target_format="parquet",
            compression="zstd",
            sort_columns=None,
        )

        mock_reader.format.assert_called_with("parquet")
        mock_reader.load.assert_called_once_with("/tmp/source.parquet")

        mock_writer.mode.assert_called_once_with("overwrite")
        mock_writer.option.assert_called_with("compression", "zstd")

        mock_spark.sql.assert_not_called()

        assert result == 100


@pyspark_maintenance_skip
class TestPySparkDeltaOperationsWithMock:
    def test_delete_uses_delta_table_api(self):
        if not PYSPARK_AVAILABLE:
            pytest.skip("PySpark not installed")

        if not DELTA_SPARK_AVAILABLE:
            pytest.skip("delta-spark not installed")

        from unittest.mock import MagicMock, patch

        mock_spark = MagicMock()
        mock_delta_table = MagicMock()

        mock_df_before = MagicMock()
        mock_df_before.count.return_value = 100
        mock_df_after = MagicMock()
        mock_df_after.count.return_value = 90
        mock_delta_table.toDF.side_effect = [mock_df_before, mock_df_after]

        ops = PySparkMaintenanceOperations(spark_session=mock_spark, prefer_delta=True)

        with patch("benchbox.platforms.dataframe.pyspark_maintenance.DeltaTable") as MockDeltaTable:
            MockDeltaTable.forPath.return_value = mock_delta_table

            with patch.object(ops, "is_delta_table", return_value=True):
                result = ops._do_delete(
                    table_path="/tmp/delta_table",
                    condition="id > 100",
                )

        MockDeltaTable.forPath.assert_called_once_with(mock_spark, "/tmp/delta_table")

        mock_delta_table.delete.assert_called_once_with(condition="id > 100")

        mock_spark.sql.assert_not_called()

        assert result == 10

    def test_update_uses_delta_table_api(self):
        if not PYSPARK_AVAILABLE:
            pytest.skip("PySpark not installed")

        if not DELTA_SPARK_AVAILABLE:
            pytest.skip("delta-spark not installed")

        from unittest.mock import MagicMock, patch

        mock_spark = MagicMock()
        mock_delta_table = MagicMock()

        mock_df = MagicMock()
        mock_filtered = MagicMock()
        mock_filtered.count.return_value = 5
        mock_df.filter.return_value = mock_filtered
        mock_delta_table.toDF.return_value = mock_df

        ops = PySparkMaintenanceOperations(spark_session=mock_spark, prefer_delta=True)

        with patch("benchbox.platforms.dataframe.pyspark_maintenance.DeltaTable") as MockDeltaTable:
            MockDeltaTable.forPath.return_value = mock_delta_table

            with patch.object(ops, "is_delta_table", return_value=True):
                with patch.object(ops, "_to_spark_column", side_effect=lambda value: value):
                    result = ops._do_update(
                        table_path="/tmp/delta_table",
                        condition="status = 'pending'",
                        updates={"status": "'completed'"},
                    )

        MockDeltaTable.forPath.assert_called_once_with(mock_spark, "/tmp/delta_table")

        mock_delta_table.update.assert_called_once()

        mock_spark.sql.assert_not_called()

        assert result == 5

    def test_merge_uses_delta_table_api(self):
        if not PYSPARK_AVAILABLE:
            pytest.skip("PySpark not installed")

        if not DELTA_SPARK_AVAILABLE:
            pytest.skip("delta-spark not installed")

        from unittest.mock import MagicMock, patch

        mock_spark = MagicMock()
        mock_delta_table = MagicMock()

        mock_merge_builder = MagicMock()
        mock_delta_table.alias.return_value = mock_delta_table
        mock_delta_table.merge.return_value = mock_merge_builder
        mock_merge_builder.whenMatchedUpdate.return_value = mock_merge_builder
        mock_merge_builder.whenNotMatchedInsert.return_value = mock_merge_builder

        mock_source_df = MagicMock()
        mock_source_df.count.return_value = 10
        mock_source_df.alias.return_value = mock_source_df

        ops = PySparkMaintenanceOperations(spark_session=mock_spark, prefer_delta=True)

        with patch("benchbox.platforms.dataframe.pyspark_maintenance.DeltaTable") as MockDeltaTable:
            MockDeltaTable.forPath.return_value = mock_delta_table

            with patch.object(ops, "is_delta_table", return_value=True):
                with patch.object(ops, "_convert_to_spark_df", return_value=mock_source_df):
                    with patch.object(ops, "_to_spark_column", side_effect=lambda value: value):
                        result = ops._do_merge(
                            table_path="/tmp/delta_table",
                            source_dataframe=mock_source_df,
                            merge_condition="target.id = source.id",
                            when_matched={"name": "source.name"},
                            when_not_matched={"id": "source.id"},
                        )

        MockDeltaTable.forPath.assert_called_once_with(mock_spark, "/tmp/delta_table")

        mock_delta_table.alias.assert_called_with("target")
        mock_delta_table.merge.assert_called_once()
        mock_merge_builder.execute.assert_called_once()

        mock_spark.sql.assert_not_called()

        assert result == 10


@pyspark_maintenance_skip
class TestPySparkParquetLimitations:
    def test_delete_raises_for_parquet(self):

        if not PYSPARK_AVAILABLE:
            pytest.skip("PySpark not installed")

        from unittest.mock import MagicMock, patch

        mock_spark = MagicMock()
        ops = PySparkMaintenanceOperations(spark_session=mock_spark, prefer_delta=True)

        with patch.object(ops, "is_delta_table", return_value=False):
            with pytest.raises(NotImplementedError, match="DELETE requires Delta Lake"):
                ops._do_delete("/tmp/parquet_table", "id > 100")

    def test_update_raises_for_parquet(self):

        if not PYSPARK_AVAILABLE:
            pytest.skip("PySpark not installed")

        from unittest.mock import MagicMock, patch

        mock_spark = MagicMock()
        ops = PySparkMaintenanceOperations(spark_session=mock_spark, prefer_delta=True)

        with patch.object(ops, "is_delta_table", return_value=False):
            with pytest.raises(NotImplementedError, match="UPDATE requires Delta Lake"):
                ops._do_update("/tmp/parquet_table", "status = 'x'", {"status": "'y'"})

    def test_merge_raises_for_parquet(self):

        if not PYSPARK_AVAILABLE:
            pytest.skip("PySpark not installed")

        from unittest.mock import MagicMock, patch

        mock_spark = MagicMock()
        ops = PySparkMaintenanceOperations(spark_session=mock_spark, prefer_delta=True)

        with patch.object(ops, "is_delta_table", return_value=False):
            with pytest.raises(NotImplementedError, match="MERGE requires Delta Lake"):
                ops._do_merge("/tmp/parquet_table", MagicMock(), "id = id", None, None)


