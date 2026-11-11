# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest

from benchbox.core.transaction_primitives.dataframe_operations import (
    DataFrameTransactionOperationsManager,
    TransactionOperationType,
)
from benchbox.platforms.dataframe.iceberg_maintenance import get_iceberg_maintenance_operations

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_integration,
    pytest.mark.skipif(
        get_iceberg_maintenance_operations() is None,
        reason="PyIceberg maintenance operations unavailable (need pyiceberg and pyarrow)",
    ),
]


@pytest.fixture
def ops(tmp_path: Path):
    return get_iceberg_maintenance_operations(working_dir=str(tmp_path / "warehouse"))


@pytest.fixture
def manager():
    return DataFrameTransactionOperationsManager("iceberg")


@pytest.fixture
def manager_env(tmp_path: Path, monkeypatch):

    monkeypatch.chdir(tmp_path)
    from benchbox.platforms.dataframe.iceberg_maintenance import get_iceberg_maintenance_operations

    manager = DataFrameTransactionOperationsManager("iceberg")

    seeder = get_iceberg_maintenance_operations(working_dir=str(tmp_path / "iceberg_warehouse"))
    return manager, seeder


def _location(ops, table: str) -> str:
    return ops.catalog.load_table(ops._normalize_table_identifier(table)).location()


def _table(ops, name: str = "default.t") -> str:
    return name


def _scan_count(ops, table: str) -> int:
    iceberg_table = ops.catalog.load_table(ops._normalize_table_identifier(table))
    return iceberg_table.scan().to_arrow().num_rows


def _scan_rows(ops, table: str) -> dict[int, str]:
    iceberg_table = ops.catalog.load_table(ops._normalize_table_identifier(table))
    arrow = iceberg_table.scan().to_arrow()
    return dict(zip(arrow.column("id").to_pylist(), arrow.column("v").to_pylist()))


def _seed(ops, table: str, rows: list[tuple[int, str]]) -> None:
    ids, values = zip(*rows)
    result = ops.insert_rows(table, pa.table({"id": list(ids), "v": list(values)}), mode="overwrite")
    assert result.success is True


class TestIcebergTableLayout:
    def test_insert_creates_metadata_layout(self, ops):
        table = _table(ops, "default.layout")
        _seed(ops, table, [(1, "a")])

        metadata_dir = Path(_location(ops, table)) / "metadata"
        assert metadata_dir.is_dir()
        assert list(metadata_dir.glob("*.metadata.json")), "expected Iceberg metadata files"

    def test_manager_recognizes_iceberg_table(self, ops, manager):
        table = _table(ops, "default.recognized")
        _seed(ops, table, [(1, "a")])

        is_valid, _ = manager.validate_table_format(_location(ops, table))
        assert is_valid is True

    def test_manager_supports_iceberg_transactions(self, manager):
        assert manager.supports_transactions() is True
        for operation in (
            TransactionOperationType.ATOMIC_INSERT,
            TransactionOperationType.ATOMIC_UPDATE,
            TransactionOperationType.ATOMIC_DELETE,
            TransactionOperationType.ATOMIC_MERGE,
        ):
            assert manager.supports_operation(operation) is True


class TestIcebergWrites:
    def test_append_and_overwrite_counts(self, ops):
        table = _table(ops, "default.writes")
        _seed(ops, table, [(1, "a"), (2, "b")])
        assert _scan_count(ops, table) == 2

        result = ops.insert_rows(table, pa.table({"id": [3], "v": ["c"]}), mode="append")
        assert result.success is True and result.rows_affected == 1
        assert _scan_count(ops, table) == 3

        _seed(ops, table, [(9, "z")])
        assert _scan_rows(ops, table) == {9: "z"}

    def test_manager_insert_bumps_rows(self, manager_env):
        manager, seeder = manager_env
        table = "default.mgr_insert"
        _seed(seeder, table, [(1, "a")])
        location = _location(seeder, table)

        result = manager.execute_atomic_insert(
            table_path=location,
            dataframe=pa.table({"id": [2, 3], "v": ["b", "c"]}),
        )

        assert result.success is True
        assert result.rows_affected == 2
        assert _scan_rows(seeder, table) == {1: "a", 2: "b", 3: "c"}


class TestIcebergRowOperations:
    def test_delete(self, ops):
        table = _table(ops, "default.del")
        _seed(ops, table, [(1, "a"), (2, "b"), (3, "c")])

        result = ops.delete_rows(table, "id = 2")

        assert result.success is True
        assert _scan_rows(ops, table) == {1: "a", 3: "c"}

    def test_update(self, ops):
        table = _table(ops, "default.upd")
        _seed(ops, table, [(1, "a"), (2, "b")])

        result = ops.update_rows(table, "id = 1", {"v": "z"})

        assert result.success is True
        assert _scan_rows(ops, table) == {1: "z", 2: "b"}

    def test_manager_update_and_delete(self, manager_env):
        manager, seeder = manager_env
        table = "default.mgr_ud"
        _seed(seeder, table, [(1, "a"), (2, "b")])
        location = _location(seeder, table)

        update = manager.execute_atomic_update(table_path=location, condition="id = 1", updates={"v": "z"})
        assert update.success is True
        delete = manager.execute_atomic_delete(table_path=location, condition="id = 2")
        assert delete.success is True
        assert _scan_rows(seeder, table) == {1: "z"}

    def test_merge_upserts(self, ops):
        table = _table(ops, "default.mrg")
        _seed(ops, table, [(1, "a"), (2, "b")])

        result = ops.merge_rows(
            table,
            pa.table({"id": [2, 3], "v": ["B", "C"]}),
            "target.id = source.id",
            when_matched={"v": "source.v"},
            when_not_matched={"id": "source.id", "v": "source.v"},
        )

        assert result.success is True
        assert result.rows_affected == 2
        assert isinstance(result.rows_affected, int)
        assert _scan_rows(ops, table) == {1: "a", 2: "B", 3: "C"}

    def test_manager_merge(self, manager_env):
        manager, seeder = manager_env
        table = "default.mgr_mrg"
        _seed(seeder, table, [(1, "a")])
        location = _location(seeder, table)

        result = manager.execute_atomic_merge(
            table_path=location,
            source_dataframe=pa.table({"id": [1, 2], "v": ["A", "B"]}),
            merge_condition="target.id = source.id",
            when_matched={"v": "source.v"},
            when_not_matched={"id": "source.id", "v": "source.v"},
        )

        assert result.success is True
        assert _scan_rows(seeder, table) == {1: "A", 2: "B"}


class TestIcebergSnapshots:
    def test_snapshot_history_grows(self, ops):
        table = _table(ops, "default.snaps")
        _seed(ops, table, [(1, "a")])
        ops.insert_rows(table, pa.table({"id": [2], "v": ["b"]}), mode="append")

        iceberg_table = ops.catalog.load_table("default.snaps")
        snapshots = list(iceberg_table.snapshots())

        assert len(snapshots) == 2
        assert snapshots[0].snapshot_id != snapshots[1].snapshot_id


