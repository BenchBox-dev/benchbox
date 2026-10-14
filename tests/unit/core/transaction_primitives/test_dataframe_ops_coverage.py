from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.dataframe.maintenance_interface import TransactionIsolation
from benchbox.core.transaction_primitives.dataframe_operations import (
    DELTA_LAKE_TRANSACTION_CAPABILITIES,
    DataFrameTransactionCapabilities,
    DataFrameTransactionOperationsManager,
    TransactionOperationType,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class FakeMaintenanceOps:
    def __init__(self, capabilities):
        self._capabilities = capabilities

    def get_capabilities(self):
        return self._capabilities

    def insert_rows(self, **kwargs):
        return SimpleNamespace(success=True, duration=0.05, rows_affected=3, error_message=None)

    def update_rows(self, **kwargs):
        return SimpleNamespace(success=True, duration=0.01, rows_affected=2, error_message=None)

    def delete_rows(self, **kwargs):
        return SimpleNamespace(success=True, duration=0.01, rows_affected=5, error_message=None)

    def merge_rows(self, **kwargs):
        return SimpleNamespace(success=True, duration=0.02, rows_affected=4, error_message=None)


def test_build_capabilities_from_maintenance(monkeypatch):
    caps = SimpleNamespace(
        supports_transactions=True, supports_time_travel=True, transaction_isolation=TransactionIsolation.SNAPSHOT
    )
    monkeypatch.setattr(
        "benchbox.core.transaction_primitives.dataframe_operations.get_maintenance_operations_for_platform",
        lambda _name, _engine: FakeMaintenanceOps(caps),
    )

    manager = DataFrameTransactionOperationsManager("custom-df")

    built = manager.get_capabilities()
    assert built.supports_transactions is True
    assert built.supports_rollback is True
    assert built.supports_time_travel is True


def test_validate_table_format_detects_iceberg(tmp_path):
    table = tmp_path / "iceberg_tbl"
    metadata = table / "metadata"
    metadata.mkdir(parents=True)
    (metadata / "version-hint.text").write_text("1")

    manager = DataFrameTransactionOperationsManager("iceberg")
    is_valid, message = manager.validate_table_format(table)

    assert is_valid is True
    assert message == ""


def test_execute_atomic_insert_success(monkeypatch, tmp_path):
    manager = DataFrameTransactionOperationsManager("delta-lake")
    manager._maintenance_ops = FakeMaintenanceOps(SimpleNamespace())

    table = tmp_path / "delta_table"
    (table / "_delta_log").mkdir(parents=True)

    versions = iter([1, 2])
    monkeypatch.setattr(manager, "get_table_version", lambda _path: next(versions))

    result = manager.execute_atomic_insert(table_path=table, dataframe=object())

    assert result.success is True
    assert result.rows_affected == 3
    assert result.version_before == 1
    assert result.version_after == 2
    assert result.operation_type == TransactionOperationType.ATOMIC_INSERT


def test_execute_atomic_update_fails_without_maintenance_ops(tmp_path):
    manager = DataFrameTransactionOperationsManager("delta-lake")
    manager._maintenance_ops = None

    table = tmp_path / "delta_table"
    (table / "_delta_log").mkdir(parents=True)

    result = manager.execute_atomic_update(table_path=table, condition="id=1", updates={"x": 2})

    assert result.success is False
    assert "Maintenance operations not available" in (result.error_message or "")


def test_execute_time_travel_requires_selector(tmp_path):
    manager = DataFrameTransactionOperationsManager("delta-lake")
    result = manager.execute_time_travel_query(table_path=tmp_path, version=None, timestamp=None)
    assert result.success is False
    assert "Either version or timestamp" in (result.error_message or "")


def test_execute_version_compare_not_implemented_branch(monkeypatch, tmp_path):
    manager = DataFrameTransactionOperationsManager("delta-lake")
    manager._capabilities = DataFrameTransactionCapabilities(
        platform_name="delta-lake",
        supports_transactions=True,
        supports_rollback=True,
        supports_time_travel=True,
        supports_concurrent_writes=True,
        transaction_isolation=TransactionIsolation.SNAPSHOT,
        table_format="other",
    )

    table = tmp_path / "table"
    (table / "_delta_log").mkdir(parents=True)

    result = manager.execute_version_compare(table_path=table, version1=1, version2=2)

    assert result.success is False
    assert "not implemented for table format" in (result.error_message or "")


def test_all_transaction_operation_types_enumerable():
    from benchbox.core.transaction_primitives.dataframe_operations import TransactionOperationType

    ops = list(TransactionOperationType)
    assert len(ops) == 12

    assert TransactionOperationType.ATOMIC_INSERT in ops
    assert TransactionOperationType.ROLLBACK_TO_VERSION in ops
    assert TransactionOperationType.SNAPSHOT_ISOLATION in ops


def _full_caps(**overrides):
    defaults = {
        "platform_name": "test",
        "supports_transactions": True,
        "supports_rollback": True,
        "supports_time_travel": True,
        "supports_concurrent_writes": True,
        "transaction_isolation": TransactionIsolation.SNAPSHOT,
        "table_format": "delta",
    }
    defaults.update(overrides)
    return DataFrameTransactionCapabilities(**defaults)


def test_supports_operation_atomic_ops_require_transactions():
    from benchbox.core.transaction_primitives.dataframe_operations import TransactionOperationType

    caps_yes = _full_caps(supports_transactions=True)
    caps_no = _full_caps(supports_transactions=False)

    for op in [
        TransactionOperationType.ATOMIC_INSERT,
        TransactionOperationType.ATOMIC_UPDATE,
        TransactionOperationType.ATOMIC_DELETE,
        TransactionOperationType.ATOMIC_MERGE,
        TransactionOperationType.READ_YOUR_WRITES,
    ]:
        assert caps_yes.supports_operation(op) is True
        assert caps_no.supports_operation(op) is False


def test_supports_operation_rollback_requires_rollback():
    from benchbox.core.transaction_primitives.dataframe_operations import TransactionOperationType

    caps = _full_caps(supports_rollback=False)
    assert caps.supports_operation(TransactionOperationType.ROLLBACK_TO_VERSION) is False
    assert caps.supports_operation(TransactionOperationType.ROLLBACK_TO_TIMESTAMP) is False


def test_supports_operation_time_travel_requires_time_travel():
    from benchbox.core.transaction_primitives.dataframe_operations import TransactionOperationType

    caps = _full_caps(supports_time_travel=False)
    assert caps.supports_operation(TransactionOperationType.TIME_TRAVEL_QUERY) is False
    assert caps.supports_operation(TransactionOperationType.VERSION_COMPARE) is False


def test_supports_operation_snapshot_isolation_requires_snapshot():
    from benchbox.core.transaction_primitives.dataframe_operations import TransactionOperationType

    caps_snap = _full_caps(transaction_isolation=TransactionIsolation.SNAPSHOT)
    caps_none = _full_caps(transaction_isolation=TransactionIsolation.NONE)

    assert caps_snap.supports_operation(TransactionOperationType.SNAPSHOT_ISOLATION) is True
    assert caps_none.supports_operation(TransactionOperationType.SNAPSHOT_ISOLATION) is False


def test_get_unsupported_operations_returns_complement():
    from benchbox.core.transaction_primitives.dataframe_operations import TransactionOperationType

    full_caps = _full_caps()
    unsupported = full_caps.get_unsupported_operations()
    assert unsupported == []

    no_caps = DataFrameTransactionCapabilities(platform_name="none")
    unsupported_all = no_caps.get_unsupported_operations()
    all_ops = set(TransactionOperationType)
    assert set(unsupported_all) == all_ops


def test_transaction_result_failure_factory():
    from benchbox.core.transaction_primitives.dataframe_operations import (
        DataFrameTransactionResult,
        TransactionOperationType,
    )

    result = DataFrameTransactionResult.failure(
        TransactionOperationType.ATOMIC_INSERT,
        "disk full",
    )

    assert result.success is False
    assert result.error_message == "disk full"
    assert result.validation_passed is False
    assert result.rows_affected == 0
    assert result.operation_type == TransactionOperationType.ATOMIC_INSERT
    assert result.duration_ms == 0.0


def test_transaction_result_failure_with_start_time():
    import time as _time

    from benchbox.core.transaction_primitives.dataframe_operations import (
        DataFrameTransactionResult,
        TransactionOperationType,
    )

    start = _time.time() - 1.0
    result = DataFrameTransactionResult.failure(
        TransactionOperationType.CONCURRENT_WRITE,
        "conflict",
        start_time=start,
    )

    assert result.duration_ms > 0
    assert result.start_time == start


def test_build_capabilities_pyspark_without_delta(monkeypatch):
    monkeypatch.setattr(
        "benchbox.core.transaction_primitives.dataframe_operations.get_maintenance_operations_for_platform",
        lambda _, _engine: None,
    )
    manager = DataFrameTransactionOperationsManager("pyspark-df")
    assert manager.get_capabilities().supports_transactions is False


def test_build_capabilities_iceberg(monkeypatch):
    monkeypatch.setattr(
        "benchbox.core.transaction_primitives.dataframe_operations.get_maintenance_operations_for_platform",
        lambda _, _engine: None,
    )
    manager = DataFrameTransactionOperationsManager("iceberg")
    assert manager.get_capabilities().table_format == "iceberg"


def test_build_capabilities_polars(monkeypatch):
    monkeypatch.setattr(
        "benchbox.core.transaction_primitives.dataframe_operations.get_maintenance_operations_for_platform",
        lambda _, _engine: None,
    )
    manager = DataFrameTransactionOperationsManager("polars-df")
    assert manager.get_capabilities().supports_transactions is False


def test_build_capabilities_pandas(monkeypatch):
    monkeypatch.setattr(
        "benchbox.core.transaction_primitives.dataframe_operations.get_maintenance_operations_for_platform",
        lambda _, _engine: None,
    )
    manager = DataFrameTransactionOperationsManager("pandas-df")
    assert manager.get_capabilities().supports_transactions is False


def test_build_capabilities_unknown_no_maintenance(monkeypatch):
    monkeypatch.setattr(
        "benchbox.core.transaction_primitives.dataframe_operations.get_maintenance_operations_for_platform",
        lambda _, _engine: None,
    )
    manager = DataFrameTransactionOperationsManager("some-unknown-df")
    assert manager.get_capabilities().supports_transactions is False


def test_manager_supports_transactions(monkeypatch):
    caps = SimpleNamespace(
        supports_transactions=True, supports_time_travel=True, transaction_isolation=TransactionIsolation.SNAPSHOT
    )
    monkeypatch.setattr(
        "benchbox.core.transaction_primitives.dataframe_operations.get_maintenance_operations_for_platform",
        lambda _, _engine: FakeMaintenanceOps(caps),
    )
    manager = DataFrameTransactionOperationsManager("custom-df")
    assert manager.supports_transactions() is True


def test_manager_get_unsupported_message(monkeypatch):
    monkeypatch.setattr(
        "benchbox.core.transaction_primitives.dataframe_operations.get_maintenance_operations_for_platform",
        lambda _, _engine: None,
    )
    manager = DataFrameTransactionOperationsManager("polars-df")
    msg = manager.get_unsupported_message()
    assert "polars-df" in msg
    assert "Transaction Primitives" in msg


def test_validate_path_safe_rejects_traversal():
    manager = DataFrameTransactionOperationsManager("delta-lake")
    resolved, err = manager._validate_path_safe("/data/../etc/passwd")
    assert resolved is None
    assert "traversal" in err.lower()


def test_validate_path_safe_accepts_normal_path():
    manager = DataFrameTransactionOperationsManager("delta-lake")
    resolved, err = manager._validate_path_safe("/data/my_table")
    assert resolved is not None
    assert err == ""


def _delta_manager(monkeypatch=None) -> DataFrameTransactionOperationsManager:
    manager = DataFrameTransactionOperationsManager("delta-lake")
    manager._capabilities = DELTA_LAKE_TRANSACTION_CAPABILITIES
    caps = SimpleNamespace(
        supports_transactions=True,
        supports_time_travel=True,
        transaction_isolation=TransactionIsolation.SNAPSHOT,
    )
    manager._maintenance_ops = FakeMaintenanceOps(caps)
    return manager


def test_get_maintenance_ops_pyspark_with_spark_session(monkeypatch):
    mock_get = MagicMock(return_value=None)
    monkeypatch.setattr(
        "benchbox.core.transaction_primitives.dataframe_operations.get_maintenance_operations_for_platform",
        mock_get,
    )
    mock_pyspark_module = MagicMock()
    monkeypatch.setitem(sys.modules, "benchbox.platforms.dataframe.pyspark_maintenance", mock_pyspark_module)
    mock_pyspark_module.get_pyspark_maintenance_operations.return_value = MagicMock()

    manager = DataFrameTransactionOperationsManager("pyspark-df", spark_session=MagicMock())

    assert manager is not None


def test_get_maintenance_ops_pyspark_no_spark_session():
    manager = DataFrameTransactionOperationsManager("pyspark-df", spark_session=None)
    assert manager._maintenance_ops is None


def test_validate_table_format_delta_lake(tmp_path):
    table = tmp_path / "delta_tbl"
    (table / "_delta_log").mkdir(parents=True)
    manager = DataFrameTransactionOperationsManager("delta-lake")
    is_valid, msg = manager.validate_table_format(table)
    assert is_valid is True
    assert msg == ""


def test_validate_table_format_existing_non_transactional(tmp_path):
    table = tmp_path / "plain_dir"
    table.mkdir()
    manager = DataFrameTransactionOperationsManager("delta-lake")
    is_valid, msg = manager.validate_table_format(table)
    assert is_valid is False
    assert "not a Delta Lake or Iceberg table" in msg


def test_validate_table_format_nonexistent_path():
    manager = DataFrameTransactionOperationsManager("delta-lake")
    is_valid, msg = manager.validate_table_format("/tmp/nonexistent_benchbox_xyz123")
    assert is_valid is False
    assert "does not exist" in msg


def test_get_table_version_delta_rs(tmp_path):
    manager = DataFrameTransactionOperationsManager("delta-lake")
    manager._capabilities = DELTA_LAKE_TRANSACTION_CAPABILITIES

    mock_deltalake = MagicMock()
    mock_dt = MagicMock()
    mock_dt.version.return_value = 7
    mock_deltalake.DeltaTable.return_value = mock_dt

    with patch.dict(sys.modules, {"deltalake": mock_deltalake}):
        version = manager.get_table_version(tmp_path / "delta_tbl")

    assert version == 7


def test_get_table_version_exception_returns_none(tmp_path):
    manager = DataFrameTransactionOperationsManager("delta-lake")
    manager._capabilities = DELTA_LAKE_TRANSACTION_CAPABILITIES

    mock_deltalake = MagicMock()
    mock_deltalake.DeltaTable.side_effect = RuntimeError("no such table")

    with patch.dict(sys.modules, {"deltalake": mock_deltalake}):
        version = manager.get_table_version(tmp_path / "missing_tbl")

    assert version is None


def test_get_table_version_iceberg_returns_none(tmp_path):
    manager = DataFrameTransactionOperationsManager("iceberg")
    version = manager.get_table_version(tmp_path)
    assert version is None


def test_execute_atomic_update_success(monkeypatch, tmp_path):
    manager = _delta_manager()
    table = tmp_path / "delta_table"
    (table / "_delta_log").mkdir(parents=True)

    versions = iter([3, 4])
    monkeypatch.setattr(manager, "get_table_version", lambda _p: next(versions))

    result = manager.execute_atomic_update(table_path=table, condition="id=1", updates={"x": 99})

    assert result.success is True
    assert result.rows_affected == 2
    assert result.version_before == 3
    assert result.version_after == 4


def test_execute_atomic_update_exception_returns_failure(monkeypatch, tmp_path):
    manager = _delta_manager()
    table = tmp_path / "delta_table"
    (table / "_delta_log").mkdir(parents=True)

    monkeypatch.setattr(manager, "get_table_version", lambda _p: 1)
    manager._maintenance_ops.update_rows = MagicMock(side_effect=RuntimeError("lock timeout"))

    result = manager.execute_atomic_update(table_path=table, condition="id=1", updates={"x": 99})

    assert result.success is False
    assert "lock timeout" in (result.error_message or "")


def test_execute_atomic_delete_success(monkeypatch, tmp_path):
    manager = _delta_manager()
    table = tmp_path / "delta_table"
    (table / "_delta_log").mkdir(parents=True)

    versions = iter([2, 3])
    monkeypatch.setattr(manager, "get_table_version", lambda _p: next(versions))

    result = manager.execute_atomic_delete(table_path=table, condition="id=1")

    assert result.success is True
    assert result.rows_affected == 5


def test_execute_atomic_delete_no_maintenance_ops(tmp_path):
    manager = _delta_manager()
    manager._maintenance_ops = None
    table = tmp_path / "delta_table"
    (table / "_delta_log").mkdir(parents=True)

    result = manager.execute_atomic_delete(table_path=table, condition="id=1")
    assert result.success is False


def test_execute_atomic_merge_success(monkeypatch, tmp_path):
    manager = _delta_manager()
    table = tmp_path / "delta_table"
    (table / "_delta_log").mkdir(parents=True)

    versions = iter([5, 6])
    monkeypatch.setattr(manager, "get_table_version", lambda _p: next(versions))

    result = manager.execute_atomic_merge(
        table_path=table,
        source_dataframe=MagicMock(),
        merge_condition="src.id = tgt.id",
        when_matched={"x": "src.x"},
        when_not_matched={"x": "src.x"},
    )

    assert result.success is True
    assert result.rows_affected == 4


def test_execute_atomic_merge_exception_returns_failure(monkeypatch, tmp_path):
    manager = _delta_manager()
    table = tmp_path / "delta_table"
    (table / "_delta_log").mkdir(parents=True)

    monkeypatch.setattr(manager, "get_table_version", lambda _p: 1)
    manager._maintenance_ops.merge_rows = MagicMock(side_effect=Exception("merge conflict"))

    result = manager.execute_atomic_merge(
        table_path=table,
        source_dataframe=MagicMock(),
        merge_condition="src.id = tgt.id",
    )
    assert result.success is False


def test_execute_rollback_to_version_success(monkeypatch, tmp_path):
    manager = _delta_manager()
    table = tmp_path / "delta_table"
    (table / "_delta_log").mkdir(parents=True)

    monkeypatch.setattr(manager, "get_table_version", lambda _p: 5)

    mock_deltalake = MagicMock()
    mock_dt = MagicMock()
    mock_deltalake.DeltaTable.return_value = mock_dt

    with patch.dict(sys.modules, {"deltalake": mock_deltalake}):
        result = manager.execute_rollback_to_version(table_path=table, version=3)

    assert result.success is True
    mock_dt.restore.assert_called_once_with(3)


def test_execute_rollback_to_version_exception_returns_failure(monkeypatch, tmp_path):
    manager = _delta_manager()
    table = tmp_path / "delta_table"
    (table / "_delta_log").mkdir(parents=True)

    monkeypatch.setattr(manager, "get_table_version", lambda _p: 5)

    mock_deltalake = MagicMock()
    mock_deltalake.DeltaTable.side_effect = RuntimeError("version not found")

    with patch.dict(sys.modules, {"deltalake": mock_deltalake}):
        result = manager.execute_rollback_to_version(table_path=table, version=99)

    assert result.success is False


def test_execute_rollback_to_timestamp_success(monkeypatch, tmp_path):
    manager = _delta_manager()
    table = tmp_path / "delta_table"
    (table / "_delta_log").mkdir(parents=True)

    monkeypatch.setattr(manager, "get_table_version", lambda _p: 5)

    mock_deltalake = MagicMock()
    mock_dt = MagicMock()
    mock_deltalake.DeltaTable.return_value = mock_dt

    with patch.dict(sys.modules, {"deltalake": mock_deltalake}):
        result = manager.execute_rollback_to_timestamp(table_path=table, timestamp="2024-01-15T10:00:00Z")

    assert result.success is True


def test_execute_time_travel_query_by_version(monkeypatch, tmp_path):
    manager = _delta_manager()
    table = tmp_path / "delta_table"
    (table / "_delta_log").mkdir(parents=True)

    monkeypatch.setattr(manager, "get_table_version", lambda _p: 10)

    mock_arrow = MagicMock()
    mock_arrow.num_rows = 42

    mock_deltalake = MagicMock()
    mock_dt = MagicMock()
    mock_dt.to_pyarrow_table.return_value = mock_arrow
    mock_deltalake.DeltaTable.return_value = mock_dt

    with patch.dict(sys.modules, {"deltalake": mock_deltalake}):
        result = manager.execute_time_travel_query(table_path=table, version=3, timestamp=None)

    assert result.success is True
    assert result.rows_affected == 42


def test_execute_time_travel_query_by_timestamp(monkeypatch, tmp_path):
    manager = _delta_manager()
    table = tmp_path / "delta_table"
    (table / "_delta_log").mkdir(parents=True)

    monkeypatch.setattr(manager, "get_table_version", lambda _p: 10)

    mock_arrow = MagicMock()
    mock_arrow.num_rows = 99

    mock_deltalake = MagicMock()
    mock_dt = MagicMock()
    mock_dt.to_pyarrow_table.return_value = mock_arrow
    mock_deltalake.DeltaTable.return_value = mock_dt

    with patch.dict(sys.modules, {"deltalake": mock_deltalake}):
        result = manager.execute_time_travel_query(table_path=table, version=None, timestamp="2024-01-01T00:00:00Z")

    assert result.success is True
    assert result.rows_affected == 99


def test_execute_version_compare_success(monkeypatch, tmp_path):
    manager = _delta_manager()
    table = tmp_path / "delta_table"
    (table / "_delta_log").mkdir(parents=True)

    monkeypatch.setattr(manager, "get_table_version", lambda _p: 5)

    mock_arrow1 = MagicMock()
    mock_arrow1.num_rows = 100
    mock_arrow1.schema.names = ["id", "name"]

    mock_arrow2 = MagicMock()
    mock_arrow2.num_rows = 105
    mock_arrow2.schema.names = ["id", "name", "new_col"]

    mock_dt1 = MagicMock()
    mock_dt1.to_pyarrow_table.return_value = mock_arrow1
    mock_dt2 = MagicMock()
    mock_dt2.to_pyarrow_table.return_value = mock_arrow2

    mock_deltalake = MagicMock()
    mock_deltalake.DeltaTable.side_effect = [mock_dt1, mock_dt2]

    with patch.dict(sys.modules, {"deltalake": mock_deltalake}):
        result = manager.execute_version_compare(table_path=table, version1=3, version2=5)

    assert result.success is True
    assert result.metrics["row_difference"] == 5
    assert "new_col" in result.metrics["columns_added"]


def test_execute_version_compare_exception_returns_failure(monkeypatch, tmp_path):
    manager = _delta_manager()
    table = tmp_path / "delta_table"
    (table / "_delta_log").mkdir(parents=True)

    monkeypatch.setattr(manager, "get_table_version", lambda _p: 5)

    mock_deltalake = MagicMock()
    mock_deltalake.DeltaTable.side_effect = RuntimeError("version not found")

    with patch.dict(sys.modules, {"deltalake": mock_deltalake}):
        result = manager.execute_version_compare(table_path=table, version1=1, version2=2)

    assert result.success is False


def test_transaction_manager_passes_the_execution_engine_to_polars_maintenance():
    pytest.importorskip("polars")
    from benchbox.core.transaction_primitives.dataframe_operations import get_dataframe_transaction_manager

    manager = get_dataframe_transaction_manager("polars-df", execution_engine="streaming")

    assert manager is not None
    assert manager._maintenance_ops.execution_engine == "streaming"
