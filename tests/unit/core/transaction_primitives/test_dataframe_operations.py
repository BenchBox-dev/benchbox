# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import pytest

from benchbox.core.dataframe.maintenance_interface import TransactionIsolation
from benchbox.core.transaction_primitives.dataframe_operations import (
    DELTA_LAKE_TRANSACTION_CAPABILITIES,
    ICEBERG_TRANSACTION_CAPABILITIES,
    PANDAS_TRANSACTION_CAPABILITIES,
    POLARS_TRANSACTION_CAPABILITIES,
    PYSPARK_DELTA_TRANSACTION_CAPABILITIES,
    DataFrameTransactionCapabilities,
    DataFrameTransactionOperationsManager,
    DataFrameTransactionResult,
    TransactionOperationType,
    get_dataframe_transaction_manager,
    validate_transaction_primitives_platform,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _SparkWriteBuilder:
    def __init__(self) -> None:
        self.format_name: str | None = None
        self.mode_name: str | None = None
        self.saved_path: str | None = None

    def format(self, format_name: str) -> _SparkWriteBuilder:
        self.format_name = format_name
        return self

    def mode(self, mode_name: str) -> _SparkWriteBuilder:
        self.mode_name = mode_name
        return self

    def save(self, path: str) -> None:
        self.saved_path = path


class _SparkTransactionFrame:
    def __init__(self, rows: int = 3) -> None:
        self.rows = rows
        self.count_calls = 0
        self.write = _SparkWriteBuilder()

    def count(self) -> int:
        self.count_calls += 1
        return self.rows


class TestTransactionOperationType:
    def test_atomic_operations_exist(self):

        assert TransactionOperationType.ATOMIC_INSERT is not None
        assert TransactionOperationType.ATOMIC_UPDATE is not None
        assert TransactionOperationType.ATOMIC_DELETE is not None
        assert TransactionOperationType.ATOMIC_MERGE is not None

    def test_rollback_operations_exist(self):

        assert TransactionOperationType.ROLLBACK_TO_VERSION is not None
        assert TransactionOperationType.ROLLBACK_TO_TIMESTAMP is not None

    def test_time_travel_operations_exist(self):

        assert TransactionOperationType.TIME_TRAVEL_QUERY is not None
        assert TransactionOperationType.VERSION_COMPARE is not None

    def test_concurrency_operations_exist(self):

        assert TransactionOperationType.CONCURRENT_WRITE is not None
        assert TransactionOperationType.CONFLICT_RESOLUTION is not None

    def test_isolation_operations_exist(self):

        assert TransactionOperationType.SNAPSHOT_ISOLATION is not None
        assert TransactionOperationType.READ_YOUR_WRITES is not None

    def test_operation_values_are_strings(self):

        for op in TransactionOperationType:
            assert isinstance(op.value, str)
            assert op.value == op.value.lower()


class TestDataFrameTransactionCapabilities:
    def test_default_no_transaction_support(self):

        caps = DataFrameTransactionCapabilities(platform_name="test")
        assert caps.supports_transactions is False
        assert caps.supports_rollback is False
        assert caps.supports_time_travel is False
        assert caps.supports_concurrent_writes is False
        assert caps.transaction_isolation == TransactionIsolation.NONE
        assert caps.table_format == "none"

    def test_full_transaction_support(self):

        caps = DataFrameTransactionCapabilities(
            platform_name="test-acid",
            supports_transactions=True,
            supports_rollback=True,
            supports_time_travel=True,
            supports_concurrent_writes=True,
            transaction_isolation=TransactionIsolation.SNAPSHOT,
            table_format="delta",
        )
        assert caps.supports_transactions is True
        assert caps.supports_rollback is True
        assert caps.supports_time_travel is True
        assert caps.transaction_isolation == TransactionIsolation.SNAPSHOT

    def test_delta_lake_capabilities(self):

        caps = DELTA_LAKE_TRANSACTION_CAPABILITIES
        assert caps.platform_name == "delta-lake"
        assert caps.supports_transactions is True
        assert caps.supports_rollback is True
        assert caps.supports_time_travel is True
        assert caps.supports_concurrent_writes is True
        assert caps.transaction_isolation == TransactionIsolation.SNAPSHOT
        assert caps.table_format == "delta"

    def test_pyspark_delta_capabilities(self):

        caps = PYSPARK_DELTA_TRANSACTION_CAPABILITIES
        assert caps.platform_name == "pyspark-delta"
        assert caps.supports_transactions is True
        assert caps.supports_rollback is True
        assert caps.table_format == "delta"

    def test_iceberg_capabilities(self):

        caps = ICEBERG_TRANSACTION_CAPABILITIES
        assert caps.platform_name == "iceberg"
        assert caps.supports_transactions is True
        assert caps.supports_rollback is True
        assert caps.table_format == "iceberg"

    def test_polars_capabilities(self):
        caps = POLARS_TRANSACTION_CAPABILITIES
        assert caps.platform_name == "polars-df"
        assert caps.supports_transactions is False
        assert caps.supports_rollback is False
        assert caps.table_format == "parquet"
        assert "No transaction support" in caps.notes

    def test_pandas_capabilities(self):
        caps = PANDAS_TRANSACTION_CAPABILITIES
        assert caps.platform_name == "pandas-df"
        assert caps.supports_transactions is False
        assert caps.supports_rollback is False
        assert caps.table_format == "parquet"

    def test_supports_operation_atomic_writes(self):

        caps = DELTA_LAKE_TRANSACTION_CAPABILITIES
        assert caps.supports_operation(TransactionOperationType.ATOMIC_INSERT) is True
        assert caps.supports_operation(TransactionOperationType.ATOMIC_UPDATE) is True
        assert caps.supports_operation(TransactionOperationType.ATOMIC_DELETE) is True
        assert caps.supports_operation(TransactionOperationType.ATOMIC_MERGE) is True

    def test_supports_operation_rollback(self):

        delta_caps = DELTA_LAKE_TRANSACTION_CAPABILITIES
        assert delta_caps.supports_operation(TransactionOperationType.ROLLBACK_TO_VERSION) is True
        assert delta_caps.supports_operation(TransactionOperationType.ROLLBACK_TO_TIMESTAMP) is True

        polars_caps = POLARS_TRANSACTION_CAPABILITIES
        assert polars_caps.supports_operation(TransactionOperationType.ROLLBACK_TO_VERSION) is False
        assert polars_caps.supports_operation(TransactionOperationType.ROLLBACK_TO_TIMESTAMP) is False

    def test_supports_operation_time_travel(self):

        delta_caps = DELTA_LAKE_TRANSACTION_CAPABILITIES
        assert delta_caps.supports_operation(TransactionOperationType.TIME_TRAVEL_QUERY) is True
        assert delta_caps.supports_operation(TransactionOperationType.VERSION_COMPARE) is True

        polars_caps = POLARS_TRANSACTION_CAPABILITIES
        assert polars_caps.supports_operation(TransactionOperationType.TIME_TRAVEL_QUERY) is False

    def test_supports_operation_isolation(self):

        delta_caps = DELTA_LAKE_TRANSACTION_CAPABILITIES
        assert delta_caps.supports_operation(TransactionOperationType.SNAPSHOT_ISOLATION) is True
        assert delta_caps.supports_operation(TransactionOperationType.READ_YOUR_WRITES) is True

        polars_caps = POLARS_TRANSACTION_CAPABILITIES
        assert polars_caps.supports_operation(TransactionOperationType.SNAPSHOT_ISOLATION) is False

    def test_get_unsupported_operations(self):

        delta_caps = DELTA_LAKE_TRANSACTION_CAPABILITIES
        unsupported = delta_caps.get_unsupported_operations()
        assert len(unsupported) == 0

        polars_caps = POLARS_TRANSACTION_CAPABILITIES
        unsupported = polars_caps.get_unsupported_operations()
        assert len(unsupported) == len(list(TransactionOperationType))


class TestDataFrameTransactionResult:
    def test_success_result(self):

        result = DataFrameTransactionResult(
            operation_type=TransactionOperationType.ATOMIC_INSERT,
            success=True,
            start_time=1000.0,
            end_time=1001.5,
            duration_ms=1500.0,
            rows_affected=100,
            version_before=1,
            version_after=2,
        )
        assert result.success is True
        assert result.rows_affected == 100
        assert result.version_before == 1
        assert result.version_after == 2
        assert result.error_message is None

    def test_failure_result_factory(self):

        result = DataFrameTransactionResult.failure(
            TransactionOperationType.ATOMIC_UPDATE,
            "Table not found",
            start_time=1000.0,
        )
        assert result.success is False
        assert result.error_message == "Table not found"
        assert result.rows_affected == 0
        assert result.validation_passed is False

    def test_failure_without_start_time(self):

        result = DataFrameTransactionResult.failure(
            TransactionOperationType.ROLLBACK_TO_VERSION,
            "Version not available",
        )
        assert result.success is False
        assert result.duration_ms == 0.0

    def test_result_with_metrics(self):

        result = DataFrameTransactionResult(
            operation_type=TransactionOperationType.ATOMIC_INSERT,
            success=True,
            start_time=1000.0,
            end_time=1001.0,
            duration_ms=1000.0,
            rows_affected=50,
            metrics={"write_duration_ms": 950.0, "version_check_overhead_ms": 50.0},
        )
        assert result.metrics["write_duration_ms"] == 950.0
        assert result.metrics["version_check_overhead_ms"] == 50.0


class TestValidateTransactionPrimitivesPlatform:
    def test_pyspark_is_valid(self):

        is_valid, error_msg = validate_transaction_primitives_platform("pyspark-df")
        assert is_valid is True
        assert error_msg == ""

    def test_delta_lake_is_valid(self):

        is_valid, error_msg = validate_transaction_primitives_platform("delta-lake")
        assert is_valid is True
        assert error_msg == ""

    def test_iceberg_is_valid(self):

        is_valid, error_msg = validate_transaction_primitives_platform("iceberg")
        assert is_valid is True
        assert error_msg == ""

    def test_polars_is_invalid(self):

        is_valid, error_msg = validate_transaction_primitives_platform("polars-df")
        assert is_valid is False
        assert "does not support DataFrame transactions" in error_msg
        assert "pyspark-df" in error_msg

    def test_pandas_is_invalid(self):

        is_valid, error_msg = validate_transaction_primitives_platform("pandas-df")
        assert is_valid is False
        assert "does not support" in error_msg

    def test_duckdb_is_invalid(self):

        is_valid, error_msg = validate_transaction_primitives_platform("duckdb")
        assert is_valid is False
        assert "does not support" in error_msg

    def test_sqlite_is_invalid(self):

        is_valid, error_msg = validate_transaction_primitives_platform("sqlite")
        assert is_valid is False

    def test_unknown_platform_allowed(self):
        is_valid, error_msg = validate_transaction_primitives_platform("custom-platform")
        assert is_valid is True


class TestDataFrameTransactionOperationsManager:
    def test_polars_manager_no_transaction_support(self):

        manager = DataFrameTransactionOperationsManager("polars-df")
        assert manager.supports_transactions() is False

        caps = manager.get_capabilities()
        assert caps.supports_transactions is False

    def test_pandas_manager_no_transaction_support(self):

        manager = DataFrameTransactionOperationsManager("pandas-df")
        assert manager.supports_transactions() is False

    def test_unsupported_message_helpful(self):

        manager = DataFrameTransactionOperationsManager("polars-df")
        msg = manager.get_unsupported_message()

        assert "polars-df" in msg
        assert "pyspark-df" in msg.lower()
        assert "delta" in msg.lower()

    def test_manager_platform_name_normalized(self):

        manager = DataFrameTransactionOperationsManager("POLARS-DF")
        assert manager.platform_name == "polars-df"

    def test_manager_supports_operation_check(self):

        manager = DataFrameTransactionOperationsManager("polars-df")

        assert manager.supports_operation(TransactionOperationType.ATOMIC_INSERT) is False
        assert manager.supports_operation(TransactionOperationType.ROLLBACK_TO_VERSION) is False


class TestGetDataFrameTransactionManager:
    def test_returns_manager_for_dataframe_platform(self):

        manager = get_dataframe_transaction_manager("polars-df")
        assert manager is not None
        assert isinstance(manager, DataFrameTransactionOperationsManager)

    def test_returns_manager_for_delta_lake(self):

        manager = get_dataframe_transaction_manager("delta-lake")
        assert manager is not None

    def test_returns_none_for_sql_platform(self):

        manager = get_dataframe_transaction_manager("duckdb")
        assert manager is None

        manager = get_dataframe_transaction_manager("postgresql")
        assert manager is None

    def test_returns_none_for_unknown_platform(self):

        manager = get_dataframe_transaction_manager("unknown-platform-xyz")
        assert manager is None


class TestAtomicOperationsFailForNonAcidPlatforms:
    def test_atomic_insert_fails_on_polars(self, tmp_path):

        manager = DataFrameTransactionOperationsManager("polars-df")
        result = manager.execute_atomic_insert(
            table_path=tmp_path / "test_table",
            dataframe=None,
        )
        assert result.success is False
        assert "ACID transaction support" in result.error_message

    def test_atomic_update_fails_on_polars(self, tmp_path):

        manager = DataFrameTransactionOperationsManager("polars-df")
        result = manager.execute_atomic_update(
            table_path=tmp_path / "test_table",
            condition="id > 0",
            updates={"status": "'updated'"},
        )
        assert result.success is False
        assert "ACID transaction support" in result.error_message

    def test_rollback_fails_on_polars(self, tmp_path):

        manager = DataFrameTransactionOperationsManager("polars-df")
        result = manager.execute_rollback_to_version(
            table_path=tmp_path / "test_table",
            version=1,
        )
        assert result.success is False
        assert "Rollback not supported" in result.error_message

    def test_time_travel_fails_on_polars(self, tmp_path):

        manager = DataFrameTransactionOperationsManager("polars-df")
        result = manager.execute_time_travel_query(
            table_path=tmp_path / "test_table",
            version=1,
        )
        assert result.success is False
        assert "Time travel not supported" in result.error_message

    def test_version_compare_fails_on_polars(self, tmp_path):

        manager = DataFrameTransactionOperationsManager("polars-df")
        result = manager.execute_version_compare(
            table_path=tmp_path / "test_table",
            version1=1,
            version2=2,
        )
        assert result.success is False
        assert "Version compare not supported" in result.error_message


class TestTableFormatValidation:
    def test_validate_nonexistent_table(self, tmp_path):

        manager = DataFrameTransactionOperationsManager("delta-lake")
        is_valid, error_msg = manager.validate_table_format(tmp_path / "nonexistent")
        assert is_valid is False
        assert "does not exist" in error_msg

    def test_validate_plain_parquet_directory(self, tmp_path):
        table_dir = tmp_path / "parquet_table"
        table_dir.mkdir()
        (table_dir / "part-00000.parquet").touch()

        manager = DataFrameTransactionOperationsManager("delta-lake")
        is_valid, error_msg = manager.validate_table_format(table_dir)
        assert is_valid is False
        assert "not a Delta Lake" in error_msg
        assert "df.write.format('delta')" in error_msg

    def test_validate_delta_table_directory(self, tmp_path):

        table_dir = tmp_path / "delta_table"
        table_dir.mkdir()
        delta_log = table_dir / "_delta_log"
        delta_log.mkdir()
        (delta_log / "00000000000000000000.json").touch()

        manager = DataFrameTransactionOperationsManager("delta-lake")
        is_valid, error_msg = manager.validate_table_format(table_dir)
        assert is_valid is True
        assert error_msg == ""

    def test_validate_path_traversal_rejected(self, tmp_path):

        manager = DataFrameTransactionOperationsManager("delta-lake")

        is_valid, error_msg = manager.validate_table_format(tmp_path / ".." / "etc" / "passwd")
        assert is_valid is False
        assert "Path traversal" in error_msg

    def test_validate_path_traversal_in_string(self):

        manager = DataFrameTransactionOperationsManager("delta-lake")

        is_valid, error_msg = manager.validate_table_format("/data/../../../etc/passwd")
        assert is_valid is False
        assert "Path traversal" in error_msg


class TestBenchmarkIntegration:
    def test_benchmark_supports_dataframe_mode_pyspark(self):

        from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark

        benchmark = TransactionPrimitivesBenchmark(scale_factor=0.01)
        assert benchmark.supports_dataframe_mode("pyspark-df") is True

    def test_benchmark_supports_dataframe_mode_delta(self):

        from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark

        benchmark = TransactionPrimitivesBenchmark(scale_factor=0.01)
        assert benchmark.supports_dataframe_mode("delta-lake") is True

    def test_benchmark_rejects_dataframe_mode_polars(self):

        from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark

        benchmark = TransactionPrimitivesBenchmark(scale_factor=0.01)
        assert benchmark.supports_dataframe_mode("polars-df") is False

    def test_benchmark_rejects_dataframe_mode_pandas(self):

        from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark

        benchmark = TransactionPrimitivesBenchmark(scale_factor=0.01)
        assert benchmark.supports_dataframe_mode("pandas-df") is False

    def test_benchmark_get_dataframe_operations_raises_for_polars(self):

        from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark

        benchmark = TransactionPrimitivesBenchmark(scale_factor=0.01)
        with pytest.raises(ValueError, match="does not support"):
            benchmark.get_dataframe_operations("polars-df")

    def test_benchmark_validate_dataframe_configuration_pyspark_no_session(self):

        from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark

        benchmark = TransactionPrimitivesBenchmark(scale_factor=0.01)
        is_valid, error_msg = benchmark.validate_dataframe_configuration("pyspark-df")
        assert is_valid is False
        assert "SparkSession" in error_msg


class TestNewOperationTypes:
    def test_all_12_operation_types_present(self):
        assert len(TransactionOperationType) == 12

    def test_concurrent_write_in_enum(self):
        assert TransactionOperationType.CONCURRENT_WRITE.value == "concurrent_write"

    def test_conflict_resolution_in_enum(self):
        assert TransactionOperationType.CONFLICT_RESOLUTION.value == "conflict_resolution"

    def test_snapshot_isolation_in_enum(self):
        assert TransactionOperationType.SNAPSHOT_ISOLATION.value == "snapshot_isolation"

    def test_read_your_writes_in_enum(self):
        assert TransactionOperationType.READ_YOUR_WRITES.value == "read_your_writes"

    def test_execute_concurrent_write_rejects_unsupported_platform(self):
        manager = DataFrameTransactionOperationsManager("polars-df", POLARS_TRANSACTION_CAPABILITIES)
        result = manager.execute_concurrent_write("/tmp/fake_table", [])
        assert result.success is False
        assert "concurrent_write" in result.error_message.lower() or "not supported" in result.error_message.lower()

    def test_execute_conflict_resolution_rejects_unsupported_platform(self):
        manager = DataFrameTransactionOperationsManager("pandas-df", PANDAS_TRANSACTION_CAPABILITIES)
        result = manager.execute_conflict_resolution("/tmp/fake_table", None)
        assert result.success is False

    def test_execute_snapshot_isolation_rejects_unsupported_platform(self):
        manager = DataFrameTransactionOperationsManager("polars-df", POLARS_TRANSACTION_CAPABILITIES)
        result = manager.execute_snapshot_isolation("/tmp/fake_table")
        assert result.success is False

    def test_execute_read_your_writes_rejects_unsupported_platform(self):
        manager = DataFrameTransactionOperationsManager("polars-df", POLARS_TRANSACTION_CAPABILITIES)
        result = manager.execute_read_your_writes("/tmp/fake_table", None)
        assert result.success is False

    def test_delta_capabilities_support_all_new_operations(self):
        assert DELTA_LAKE_TRANSACTION_CAPABILITIES.supports_operation(TransactionOperationType.CONCURRENT_WRITE)
        assert DELTA_LAKE_TRANSACTION_CAPABILITIES.supports_operation(TransactionOperationType.CONFLICT_RESOLUTION)
        assert DELTA_LAKE_TRANSACTION_CAPABILITIES.supports_operation(TransactionOperationType.SNAPSHOT_ISOLATION)
        assert DELTA_LAKE_TRANSACTION_CAPABILITIES.supports_operation(TransactionOperationType.READ_YOUR_WRITES)


class TestTransactionWriteCounting:
    def test_concurrent_write_does_not_count_spark_input(self):
        manager = DataFrameTransactionOperationsManager("delta-lake", spark_session=object())
        manager.get_table_version = lambda _path: 1
        frame = _SparkTransactionFrame(rows=5)

        result = manager.execute_concurrent_write("/tmp/table", [frame])

        assert result.success is True
        assert result.rows_affected == 1
        assert frame.count_calls == 0
        assert frame.write.format_name == "delta"
        assert frame.write.mode_name == "append"
        assert frame.write.saved_path == "/tmp/table"

    def test_conflict_resolution_does_not_count_spark_input(self):
        manager = DataFrameTransactionOperationsManager("delta-lake", spark_session=object())
        manager.get_table_version = lambda _path: 1
        frame = _SparkTransactionFrame(rows=5)

        result = manager.execute_conflict_resolution("/tmp/table", frame, resolution_strategy="overwrite")

        assert result.success is True
        assert result.rows_affected == 1
        assert frame.count_calls == 0
        assert frame.write.format_name == "delta"
        assert frame.write.mode_name == "overwrite"
        assert frame.write.saved_path == "/tmp/table"

    def test_read_your_writes_counts_spark_input_for_validation(self):
        manager = DataFrameTransactionOperationsManager("delta-lake", spark_session=object())
        manager.get_table_version = lambda _path: 1
        manager._read_transaction_table_count = lambda _path: 7
        frame = _SparkTransactionFrame(rows=5)

        result = manager.execute_read_your_writes("/tmp/table", frame)

        assert result.success is True
        assert result.rows_affected == 5
        assert result.metrics["rows_written"] == 5
        assert result.metrics["reads_own_writes"] is True
        assert frame.count_calls == 1


class TestBenchmarkRegistryDataframeFlag:
    def test_transaction_primitives_supports_dataframe(self):
        from benchbox.core.benchmark_registry import BENCHMARK_METADATA

        assert BENCHMARK_METADATA["transaction_primitives"]["supports_dataframe"] is True
