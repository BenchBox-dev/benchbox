# Copyright 2026 Joe Harris / BenchBox Project

import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from benchbox.core.dataframe.maintenance_interface import (
    DELTA_LAKE_CAPABILITIES,
    ICEBERG_CAPABILITIES,
    PARQUET_CAPABILITIES,
    POLARS_CAPABILITIES,
    BaseDataFrameMaintenanceOperations,
    DataFrameMaintenanceCapabilities,
    MaintenanceOperationType,
    MaintenanceResult,
    TransactionIsolation,
    get_maintenance_operations_for_platform,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestDataFrameMaintenanceCapabilities:
    def test_default_capabilities(self):

        caps = DataFrameMaintenanceCapabilities(platform_name="test")

        assert caps.platform_name == "test"
        assert caps.supports_insert is True
        assert caps.supports_delete is False
        assert caps.supports_update is False
        assert caps.supports_merge is False
        assert caps.supports_transactions is False
        assert caps.transaction_isolation == TransactionIsolation.NONE

    def test_delta_lake_capabilities(self):

        caps = DELTA_LAKE_CAPABILITIES

        assert caps.platform_name == "delta-lake"
        assert caps.supports_insert is True
        assert caps.supports_delete is True
        assert caps.supports_update is True
        assert caps.supports_merge is True
        assert caps.supports_transactions is True
        assert caps.transaction_isolation == TransactionIsolation.SNAPSHOT
        assert caps.supports_row_level_delete is True
        assert caps.supports_time_travel is True

    def test_iceberg_capabilities(self):

        caps = ICEBERG_CAPABILITIES

        assert caps.platform_name == "iceberg"
        assert caps.supports_insert is True
        assert caps.supports_delete is True
        assert caps.supports_update is True
        assert caps.supports_merge is True
        assert caps.supports_transactions is True

    def test_parquet_capabilities_limited(self):
        caps = PARQUET_CAPABILITIES

        assert caps.platform_name == "parquet"
        assert caps.supports_insert is True
        assert caps.supports_delete is False
        assert caps.supports_update is False
        assert caps.supports_merge is False
        assert caps.supports_transactions is False
        assert caps.supports_partitioned_delete is True
        assert caps.supports_row_level_delete is False

    def test_polars_capabilities_full(self):

        caps = POLARS_CAPABILITIES

        assert caps.platform_name == "polars"
        assert caps.supports_insert is True
        assert caps.supports_delete is True
        assert caps.supports_update is True
        assert caps.supports_merge is True
        assert caps.supports_transactions is False
        assert caps.supports_time_travel is False

    def test_supports_operation_insert(self):

        caps = DataFrameMaintenanceCapabilities(platform_name="test", supports_insert=True)
        assert caps.supports_operation(MaintenanceOperationType.INSERT) is True
        assert caps.supports_operation(MaintenanceOperationType.BULK_INSERT) is True

    def test_supports_operation_delete(self):

        caps = DataFrameMaintenanceCapabilities(platform_name="test", supports_delete=True)
        assert caps.supports_operation(MaintenanceOperationType.DELETE) is True

        caps2 = DataFrameMaintenanceCapabilities(
            platform_name="test",
            supports_delete=False,
            supports_partitioned_delete=True,
        )
        assert caps2.supports_operation(MaintenanceOperationType.DELETE) is True
        assert caps2.supports_operation(MaintenanceOperationType.BULK_DELETE) is True

    def test_supports_operation_update(self):

        caps_no = DataFrameMaintenanceCapabilities(platform_name="test", supports_update=False)
        caps_yes = DataFrameMaintenanceCapabilities(platform_name="test", supports_update=True)

        assert caps_no.supports_operation(MaintenanceOperationType.UPDATE) is False
        assert caps_yes.supports_operation(MaintenanceOperationType.UPDATE) is True

    def test_supports_operation_merge(self):

        caps_no = DataFrameMaintenanceCapabilities(platform_name="test", supports_merge=False)
        caps_yes = DataFrameMaintenanceCapabilities(platform_name="test", supports_merge=True)

        assert caps_no.supports_operation(MaintenanceOperationType.MERGE) is False
        assert caps_yes.supports_operation(MaintenanceOperationType.MERGE) is True

    def test_validate_tpc_compliance_full(self):

        caps = DELTA_LAKE_CAPABILITIES
        is_compliant, issues = caps.validate_tpc_compliance()

        assert is_compliant is True
        assert issues == []

    def test_validate_tpc_compliance_partial(self):

        caps = PARQUET_CAPABILITIES
        is_compliant, issues = caps.validate_tpc_compliance()

        assert is_compliant is False
        assert len(issues) > 0
        assert any("UPDATE" in issue for issue in issues)

    def test_validate_tpc_compliance_insert_only(self):

        caps = DataFrameMaintenanceCapabilities(
            platform_name="test",
            supports_insert=True,
            supports_delete=False,
            supports_update=False,
        )
        is_compliant, issues = caps.validate_tpc_compliance()

        assert is_compliant is False
        assert any("RF2" in issue for issue in issues)
        assert any("DM3" in issue for issue in issues)


class TestMaintenanceResult:
    def test_successful_result(self):

        now = time.time()
        result = MaintenanceResult(
            operation_type=MaintenanceOperationType.INSERT,
            success=True,
            start_time=now,
            end_time=now + 1.5,
            duration=1.5,
            rows_affected=1000,
        )

        assert result.success is True
        assert result.rows_affected == 1000
        assert result.duration == 1.5
        assert result.error_message is None

    def test_failure_factory(self):

        result = MaintenanceResult.failure(
            MaintenanceOperationType.DELETE,
            "Table not found",
        )

        assert result.success is False
        assert result.rows_affected == 0
        assert result.error_message == "Table not found"
        assert result.operation_type == MaintenanceOperationType.DELETE

    def test_failure_with_start_time(self):

        start = time.time() - 2.0
        result = MaintenanceResult.failure(
            MaintenanceOperationType.UPDATE,
            "Constraint violation",
            start_time=start,
        )

        assert result.success is False
        assert result.start_time == start
        assert result.duration >= 2.0


class TestBaseDataFrameMaintenanceOperations:
    class MockMaintenanceOps(BaseDataFrameMaintenanceOperations):
        def __init__(self, caps: DataFrameMaintenanceCapabilities):
            super().__init__()
            self._caps = caps
            self.insert_called = False
            self.delete_called = False

        def _get_capabilities(self) -> DataFrameMaintenanceCapabilities:
            return self._caps

        def _do_insert(
            self,
            table_path: Path | str,
            dataframe: Any,
            partition_columns: list[str] | None,
            mode: str,
        ) -> int:
            self.insert_called = True
            return 100

        def _do_delete(
            self,
            table_path: Path | str,
            condition: str | Any,
        ) -> int:
            self.delete_called = True
            return 50

    def test_capabilities_cached(self):

        caps = DataFrameMaintenanceCapabilities(platform_name="test")
        ops = self.MockMaintenanceOps(caps)

        result1 = ops.get_capabilities()
        result2 = ops.get_capabilities()

        assert result1 is result2

    def test_insert_rows_success(self):

        caps = DataFrameMaintenanceCapabilities(platform_name="test", supports_insert=True)
        ops = self.MockMaintenanceOps(caps)

        result = ops.insert_rows("/path/to/table", MagicMock())

        assert result.success is True
        assert result.rows_affected == 100
        assert result.operation_type == MaintenanceOperationType.INSERT
        assert ops.insert_called is True

    def test_delete_rows_success(self):

        caps = DataFrameMaintenanceCapabilities(
            platform_name="test",
            supports_delete=True,
        )
        ops = self.MockMaintenanceOps(caps)

        result = ops.delete_rows("/path/to/table", "id > 100")

        assert result.success is True
        assert result.rows_affected == 50
        assert result.operation_type == MaintenanceOperationType.DELETE
        assert ops.delete_called is True

    def test_delete_not_supported(self):

        caps = DataFrameMaintenanceCapabilities(
            platform_name="test",
            supports_delete=False,
            supports_partitioned_delete=False,
        )
        ops = self.MockMaintenanceOps(caps)

        with pytest.raises(NotImplementedError) as exc_info:
            ops.delete_rows("/path/to/table", "id > 100")

        assert "does not support delete" in str(exc_info.value).lower()
        assert "Delta Lake" in str(exc_info.value)

    def test_update_not_implemented_by_default(self):

        caps = DataFrameMaintenanceCapabilities(
            platform_name="test",
            supports_update=True,
        )
        ops = self.MockMaintenanceOps(caps)

        with pytest.raises(NotImplementedError):
            ops.update_rows("/path/to/table", "id = 1", {"name": "new"})

    def test_merge_not_implemented_by_default(self):

        caps = DataFrameMaintenanceCapabilities(
            platform_name="test",
            supports_merge=True,
        )
        ops = self.MockMaintenanceOps(caps)

        with pytest.raises(NotImplementedError):
            ops.merge_rows("/path/to/table", MagicMock(), "id = source.id")

    def test_insert_exception_returns_failure(self):

        class FailingOps(self.MockMaintenanceOps):
            def _do_insert(self, *args, **kwargs) -> int:
                raise RuntimeError("Disk full")

        caps = DataFrameMaintenanceCapabilities(platform_name="test", supports_insert=True)
        ops = FailingOps(caps)

        result = ops.insert_rows("/path/to/table", MagicMock())

        assert result.success is False
        assert result.error_message == "Disk full"
        assert result.rows_affected == 0


class TestMaintenanceOperationType:
    def test_basic_operations(self):

        assert MaintenanceOperationType.INSERT.value == "insert"
        assert MaintenanceOperationType.DELETE.value == "delete"
        assert MaintenanceOperationType.UPDATE.value == "update"
        assert MaintenanceOperationType.MERGE.value == "merge"

    def test_bulk_operations(self):

        assert MaintenanceOperationType.BULK_INSERT.value == "bulk_insert"
        assert MaintenanceOperationType.BULK_DELETE.value == "bulk_delete"


class TestTransactionIsolation:
    def test_isolation_levels(self):

        assert TransactionIsolation.NONE.value == "none"
        assert TransactionIsolation.READ_COMMITTED.value == "read_committed"
        assert TransactionIsolation.SNAPSHOT.value == "snapshot"
        assert TransactionIsolation.SERIALIZABLE.value == "serializable"


class TestGetMaintenanceOperationsForPlatform:
    def test_unknown_platform_returns_none(self):

        result = get_maintenance_operations_for_platform("unknown-platform")
        assert result is None

    def test_polars_df_returns_implementation(self):
        result = get_maintenance_operations_for_platform("polars-df")
        if result is not None:
            caps = result.get_capabilities()
            assert caps.platform_name == "polars"

    def test_delta_lake_returns_implementation(self):
        result = get_maintenance_operations_for_platform("delta-lake")
        if result is not None:
            caps = result.get_capabilities()
            assert caps.platform_name == "delta-lake"
            assert caps.supports_insert is True
            assert caps.supports_delete is True
            assert caps.supports_update is True
            assert caps.supports_merge is True

    def test_iceberg_returns_implementation(self):
        result = get_maintenance_operations_for_platform("iceberg")
        if result is not None:
            caps = result.get_capabilities()
            assert caps.platform_name == "iceberg"
            assert caps.supports_insert is True
            assert caps.supports_delete is True
            assert caps.supports_update is True
            assert caps.supports_merge is True

    def test_case_insensitive(self):

        result1 = get_maintenance_operations_for_platform("Polars-DF")
        result2 = get_maintenance_operations_for_platform("POLARS-DF")
        assert type(result1) == type(result2)


