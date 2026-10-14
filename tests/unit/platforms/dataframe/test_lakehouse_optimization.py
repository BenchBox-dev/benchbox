# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

try:
    import pyarrow as pa
    from deltalake import DeltaTable, write_deltalake

    from benchbox.platforms.dataframe.delta_lake_maintenance import DeltaLakeMaintenanceOperations

    DELTA_AVAILABLE = True
except ImportError:
    pa = None
    DeltaTable = None
    write_deltalake = None
    DeltaLakeMaintenanceOperations = None
    DELTA_AVAILABLE = False

try:
    import pyiceberg

    from benchbox.platforms.dataframe.iceberg_maintenance import IcebergMaintenanceOperations

    ICEBERG_AVAILABLE = True
except ImportError:
    IcebergMaintenanceOperations = None
    ICEBERG_AVAILABLE = False


def _arrow_batch(rows: list[dict[str, Any]]) -> Any:
    schema = pa.schema([("id", pa.int64()), ("v", pa.string())])
    return pa.Table.from_pylist(rows, schema=schema)


@pytest.mark.skipif(not DELTA_AVAILABLE, reason="deltalake not installed")
class TestDeltaOptimization:
    def _two_file_table(self, tmp_path: Path) -> str:
        path = str(tmp_path / "delta_tbl")
        write_deltalake(path, _arrow_batch([{"id": 1, "v": "a"}]))
        write_deltalake(path, _arrow_batch([{"id": 2, "v": "b"}]), mode="append")
        assert len(DeltaTable(path).file_uris()) == 2
        return path

    def test_compact_binpacks_files(self, tmp_path: Path) -> None:
        ops = DeltaLakeMaintenanceOperations()
        result = ops.optimize_table(self._two_file_table(tmp_path), strategy="compact")

        assert result.success is True
        assert result.operation_type.value == "optimize"
        assert result.metrics["strategy"] == "compact"
        assert result.metrics["numFilesRemoved"] == 2
        assert result.metrics["numFilesAdded"] == 1
        assert result.rows_affected == 3

    def test_z_order_requires_columns(self, tmp_path: Path) -> None:
        ops = DeltaLakeMaintenanceOperations()
        result = ops.optimize_table(self._two_file_table(tmp_path), strategy="z_order")

        assert result.success is False
        assert "ordering columns" in (result.error_message or "")

    def test_z_order_compacts_with_columns(self, tmp_path: Path) -> None:
        ops = DeltaLakeMaintenanceOperations()
        result = ops.optimize_table(self._two_file_table(tmp_path), strategy="z_order", columns=["id"])

        assert result.success is True
        assert result.metrics["strategy"] == "z_order"
        assert result.metrics["numFilesRemoved"] == 2
        assert result.metrics["numFilesAdded"] == 1

    def test_unknown_strategy_raises(self, tmp_path: Path) -> None:
        ops = DeltaLakeMaintenanceOperations()
        with pytest.raises(NotImplementedError, match="Unknown Delta optimize strategy"):
            ops.optimize_table(self._two_file_table(tmp_path), strategy="bogus")

    def test_vacuum_dry_run_preserves_files(self, tmp_path: Path) -> None:
        ops = DeltaLakeMaintenanceOperations()
        path = self._two_file_table(tmp_path)

        result = ops.vacuum_table(path, dry_run=True)

        assert result.success is True
        assert result.metrics["dry_run"] is True
        assert len(DeltaTable(path).file_uris()) == 2

    def test_vacuum_reclaims_with_zero_retention(self, tmp_path: Path) -> None:
        ops = DeltaLakeMaintenanceOperations()
        path = self._two_file_table(tmp_path)
        ops.optimize_table(path, strategy="compact")

        result = ops.vacuum_table(path, retention_hours=0, dry_run=False, enforce_retention=False)

        assert result.success is True
        assert result.rows_affected == 2
        assert len(result.metrics["deleted_paths"]) == 2

    def test_vacuum_enforces_retention_floor(self, tmp_path: Path) -> None:
        ops = DeltaLakeMaintenanceOperations()
        result = ops.vacuum_table(
            self._two_file_table(tmp_path), retention_hours=0, dry_run=False, enforce_retention=True
        )

        assert result.success is False


@pytest.mark.skipif(not ICEBERG_AVAILABLE, reason="pyiceberg not installed")
class TestIcebergVacuum:
    def _two_snapshot_table(self, tmp_path: Path) -> tuple[Any, str]:
        ops = IcebergMaintenanceOperations(working_dir=str(tmp_path / "ice"))
        identifier = "default.probe"
        table = ops._get_or_create_table(identifier, _arrow_batch([{"id": 1, "v": "a"}]).schema)
        table.append(_arrow_batch([{"id": 1, "v": "a"}]))
        table.append(_arrow_batch([{"id": 2, "v": "b"}]))
        assert len(ops.catalog.load_table(identifier).snapshots()) == 2
        return ops, identifier

    def test_vacuum_dry_run_reports_without_expiring(self, tmp_path: Path) -> None:
        ops, identifier = self._two_snapshot_table(tmp_path)

        result = ops.vacuum_table(identifier, retention_hours=0, dry_run=True)

        assert result.success is True
        assert result.operation_type.value == "vacuum"
        assert result.rows_affected == 1
        assert result.metrics["reclaimable_files"] >= 1
        assert result.metrics["reclaimable_bytes"] > 0
        assert len(ops.catalog.load_table(identifier).snapshots()) == 2

    def test_vacuum_expires_old_snapshots(self, tmp_path: Path) -> None:
        ops, identifier = self._two_snapshot_table(tmp_path)

        result = ops.vacuum_table(identifier, retention_hours=0, dry_run=False)

        assert result.success is True
        assert result.rows_affected == 1
        assert result.metrics["file_cleanup"] == "full"
        assert result.metrics["reclaimed_files"] >= 1
        assert result.metrics["reclaimed_bytes"] > 0
        remaining = ops.catalog.load_table(identifier).snapshots()
        assert len(remaining) == 1
        assert remaining[0].snapshot_id not in result.metrics["expired_snapshot_ids"]
        assert ops.catalog.load_table(identifier).scan().to_arrow().num_rows == 2

    def test_vacuum_default_preserves_fresh_history(self, tmp_path: Path) -> None:
        ops, identifier = self._two_snapshot_table(tmp_path)

        result = ops.vacuum_table(identifier, dry_run=False)

        assert result.success is True
        assert result.rows_affected == 0
        assert len(ops.catalog.load_table(identifier).snapshots()) == 2

    def test_vacuum_default_uses_table_properties(self, tmp_path: Path) -> None:
        ops, identifier = self._two_snapshot_table(tmp_path)
        table = ops.catalog.load_table(identifier)
        transaction = table.transaction()
        transaction.set_properties({"history.expire.max-snapshot-age-ms": "0"})
        transaction.commit_transaction()

        result = ops.vacuum_table(identifier, dry_run=False)

        assert result.success is True
        assert result.rows_affected == 1
        assert len(ops.catalog.load_table(identifier).snapshots()) == 1

    def test_vacuum_respects_min_snapshots_to_keep(self, tmp_path: Path) -> None:
        ops, identifier = self._two_snapshot_table(tmp_path)
        table = ops.catalog.load_table(identifier)
        table.append(_arrow_batch([{"id": 3, "v": "c"}]))
        assert len(ops.catalog.load_table(identifier).snapshots()) == 3
        transaction = ops.catalog.load_table(identifier).transaction()
        transaction.set_properties(
            {"history.expire.max-snapshot-age-ms": "0", "history.expire.min-snapshots-to-keep": "2"}
        )
        transaction.commit_transaction()

        result = ops.vacuum_table(identifier, dry_run=False)

        assert result.success is True
        assert result.rows_affected == 1
        assert len(ops.catalog.load_table(identifier).snapshots()) == 2

    def test_vacuum_skips_tagged_snapshots(self, tmp_path: Path) -> None:
        ops, identifier = self._two_snapshot_table(tmp_path)
        table = ops.catalog.load_table(identifier)
        old_id = [
            snap.snapshot_id for snap in table.snapshots() if snap.snapshot_id != table.current_snapshot().snapshot_id
        ][0]
        table.manage_snapshots().create_tag(old_id, "protected").commit()

        result = ops.vacuum_table(identifier, retention_hours=0, dry_run=False)

        assert result.success is True
        assert result.rows_affected == 0
        assert len(ops.catalog.load_table(identifier).snapshots()) == 2

    def test_optimize_raises_not_implemented(self, tmp_path: Path) -> None:
        ops, identifier = self._two_snapshot_table(tmp_path)

        with pytest.raises(NotImplementedError, match="no binpack rewrite"):
            ops.optimize_table(identifier)


@pytest.mark.skipif(not ICEBERG_AVAILABLE, reason="pyiceberg not installed")
class TestIcebergLocalPaths:
    @pytest.mark.parametrize(
        "location",
        [
            "C:\\data\\warehouse/default.db/probe",
            "D:/data/warehouse/default.db/probe",
            "/srv/warehouse/default.db/probe",
            "relative/warehouse/default.db/probe",
        ],
    )
    def test_plain_locations_map_to_the_same_path(self, location: str) -> None:
        assert IcebergMaintenanceOperations._local_path(location) == Path(location)

    @pytest.mark.parametrize(
        "location",
        [
            "s3://bucket/warehouse/probe",
            "gs://bucket/probe",
            "abfss://c@a.dfs.core.windows.net/probe",
            "file://host/probe",
        ],
    )
    def test_remote_locations_have_no_local_path(self, location: str) -> None:
        assert IcebergMaintenanceOperations._local_path(location) is None
        assert IcebergMaintenanceOperations._normalize_file_ref(location) == location

    def test_file_uri_maps_to_its_path(self, tmp_path: Path) -> None:
        target = tmp_path / "with space" / "probe"

        assert IcebergMaintenanceOperations._local_path(target.as_uri()) == target

    def test_a_drive_letter_location_is_not_treated_as_remote(self) -> None:
        assert IcebergMaintenanceOperations._local_table_dir("C:\\no\\such\\dir") is None
        assert IcebergMaintenanceOperations._local_path("C:\\no\\such\\dir") is not None

    @pytest.mark.parametrize("spelling", ["plain", "file-uri"])
    @pytest.mark.parametrize("dry_run", [True, False])
    def test_cleanup_keeps_referenced_files_for_every_location_spelling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spelling: str, dry_run: bool
    ) -> None:
        directory = tmp_path / "warehouse" / "default.db" / "probe"
        (directory / "data").mkdir(parents=True)
        (directory / "metadata").mkdir()
        metadata = directory / "metadata" / "v2.metadata.json"
        kept_plain = directory / "data" / "kept-plain.parquet"
        kept_uri = directory / "data" / "kept-uri.parquet"
        orphan = directory / "data" / "orphan.parquet"
        for path in (metadata, kept_plain, kept_uri, orphan):
            path.write_bytes(b"x" * 8)

        def spell(path: Path) -> str:
            return str(path) if spelling == "plain" else path.as_uri()

        ops = IcebergMaintenanceOperations.__new__(IcebergMaintenanceOperations)
        ops.logger = MagicMock()
        table = MagicMock()
        table.location.return_value = spell(directory)
        table.metadata_location = spell(metadata)
        monkeypatch.setattr(
            IcebergMaintenanceOperations,
            "_snapshot_file_refs",
            lambda self, _table, _ids: {
                IcebergMaintenanceOperations._normalize_file_ref(str(kept_plain)),
                IcebergMaintenanceOperations._normalize_file_ref(kept_uri.as_uri()),
            },
        )

        if dry_run:
            files, total_bytes = ops._reclaimable_files(table, {1})
            assert (files, total_bytes) == (1, 8)
        else:
            result = ops._reclaim_orphan_files(table, {1})
            assert result == {"file_cleanup": "full", "reclaimed_files": 1, "reclaimed_bytes": 8}

        assert kept_plain.exists()
        assert kept_uri.exists()
        assert metadata.exists()
        assert orphan.exists() is dry_run


class TestHudiProcedures:
    def _ops(self) -> tuple[Any, MagicMock]:
        from benchbox.platforms.dataframe.hudi_maintenance import HudiMaintenanceOperations

        spark = MagicMock()
        return HudiMaintenanceOperations(spark_session=spark), spark

    def test_compact_issues_run_compaction(self) -> None:
        ops, spark = self._ops()

        result = ops.optimize_table("mydb.tbl", strategy="compact")

        assert result.success is True
        assert spark.sql.call_count == 1
        assert spark.sql.call_args[0][0] == "CALL run_compaction(op => 'run', table => 'mydb.tbl')"

    def test_cluster_issues_run_clustering(self) -> None:
        ops, spark = self._ops()

        result = ops.optimize_table("mydb.tbl", strategy="cluster")

        assert result.success is True
        assert spark.sql.call_args[0][0] == "CALL run_clustering(table => 'mydb.tbl')"

    def test_path_target_uses_path_argument(self) -> None:
        ops, spark = self._ops()

        ops.optimize_table("/tmp/hoodie/tbl", strategy="compact")

        assert spark.sql.call_args[0][0] == "CALL run_compaction(op => 'run', path => '/tmp/hoodie/tbl')"

    def test_unknown_strategy_raises(self) -> None:
        ops, _ = self._ops()

        with pytest.raises(NotImplementedError, match="Unknown Hudi optimize strategy"):
            ops.optimize_table("mydb.tbl", strategy="bogus")

    def test_vacuum_issues_run_clean(self) -> None:
        ops, spark = self._ops()

        result = ops.vacuum_table("mydb.tbl", dry_run=False)

        assert result.success is True
        assert spark.sql.call_args[0][0] == "CALL run_clean(table => 'mydb.tbl')"

    def test_vacuum_retention_maps_to_hours_policy(self) -> None:
        ops, spark = self._ops()

        ops.vacuum_table("mydb.tbl", retention_hours=72, dry_run=False)

        assert spark.sql.call_args[0][0] == (
            "CALL run_clean(table => 'mydb.tbl', clean_policy => 'KEEP_LATEST_BY_HOURS', hours_retained => 72)"
        )

    def test_vacuum_dry_run_raises(self) -> None:
        ops, spark = self._ops()

        with pytest.raises(NotImplementedError, match="no dry-run mode"):
            ops.vacuum_table("mydb.tbl", dry_run=True)
        spark.sql.assert_not_called()

    def test_vacuum_path_form_rejected(self) -> None:
        ops, spark = self._ops()

        with pytest.raises(ValueError, match="path form is unverified"):
            ops.vacuum_table("/tmp/hoodie/tbl", dry_run=False)
        spark.sql.assert_not_called()

    def test_run_procedure_parses_deleted_counts(self) -> None:
        ops, spark = self._ops()

        row = MagicMock()
        row.asDict.return_value = {"deleted_files": 7, "other": "x"}
        frame = MagicMock()
        frame.collect.return_value = [row]
        spark.sql.return_value = frame

        result = ops.vacuum_table("mydb.tbl", dry_run=False)

        assert result.success is True
        assert result.rows_affected == 7
        assert result.metrics["result_rows"] == [{"deleted_files": 7, "other": "x"}]

    def test_durations_use_monotonic_clock(self) -> None:
        ops, _ = self._ops()

        result = ops.optimize_table("mydb.tbl", strategy="compact")

        assert result.success is True
        assert result.duration >= 0.0
        assert result.duration == pytest.approx(result.end_time - result.start_time)

    def test_optimize_ignored_args_warn(self, caplog: pytest.LogCaptureFixture) -> None:
        ops, _ = self._ops()

        with caplog.at_level("WARNING"):
            ops.optimize_table("mydb.tbl", strategy="compact", columns=["a"], partition_filter="x=1")

        assert any("ignores columns" in record.message for record in caplog.records)
        assert any("ignores partition_filter" in record.message for record in caplog.records)

    def test_vacuum_ignored_retention_warn(self, caplog: pytest.LogCaptureFixture) -> None:
        ops, _ = self._ops()

        with caplog.at_level("WARNING"):
            ops.vacuum_table("mydb.tbl", dry_run=False, enforce_retention=False)

        assert any("ignores enforce_retention" in record.message for record in caplog.records)


class TestOptimizationContracts:
    def test_capability_flags(self) -> None:
        from benchbox.core.dataframe.maintenance_interface import (
            DELTA_LAKE_CAPABILITIES,
            HUDI_CAPABILITIES,
            ICEBERG_CAPABILITIES,
            POLARS_CAPABILITIES,
            MaintenanceOperationType,
        )

        assert DELTA_LAKE_CAPABILITIES.supports_operation(MaintenanceOperationType.OPTIMIZE) is True
        assert DELTA_LAKE_CAPABILITIES.supports_operation(MaintenanceOperationType.VACUUM) is True
        assert ICEBERG_CAPABILITIES.supports_operation(MaintenanceOperationType.OPTIMIZE) is False
        assert ICEBERG_CAPABILITIES.supports_operation(MaintenanceOperationType.VACUUM) is True
        assert HUDI_CAPABILITIES.supports_operation(MaintenanceOperationType.OPTIMIZE) is True
        assert HUDI_CAPABILITIES.supports_operation(MaintenanceOperationType.VACUUM) is True
        assert POLARS_CAPABILITIES.supports_operation(MaintenanceOperationType.OPTIMIZE) is False
        assert POLARS_CAPABILITIES.supports_operation(MaintenanceOperationType.VACUUM) is False

    def test_protocol_membership(self) -> None:
        from benchbox.core.dataframe.maintenance_interface import TableFormatOptimizationOperations
        from benchbox.platforms.dataframe.delta_lake_maintenance import DeltaLakeMaintenanceOperations
        from benchbox.platforms.dataframe.hudi_maintenance import HudiMaintenanceOperations
        from benchbox.platforms.dataframe.iceberg_maintenance import IcebergMaintenanceOperations
        from benchbox.platforms.dataframe.polars_maintenance import PolarsMaintenanceOperations

        assert isinstance(DeltaLakeMaintenanceOperations(), TableFormatOptimizationOperations)
        assert isinstance(
            IcebergMaintenanceOperations(working_dir="/tmp/benchbox-protocol-probe"),
            TableFormatOptimizationOperations,
        )
        assert isinstance(
            HudiMaintenanceOperations(spark_session=MagicMock()),
            TableFormatOptimizationOperations,
        )
        assert not isinstance(PolarsMaintenanceOperations(), TableFormatOptimizationOperations)
