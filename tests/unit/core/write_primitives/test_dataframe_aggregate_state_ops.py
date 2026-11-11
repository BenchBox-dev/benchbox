from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from benchbox.core.write_primitives.dataframe_operations import (
    PANDAS_WRITE_CAPABILITIES,
    POLARS_WRITE_CAPABILITIES,
    PYSPARK_WRITE_CAPABILITIES,
    DataFrameWriteCapabilities,
    DataFrameWriteOperationsManager,
    WriteOperationType,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class TestAggregateStateEnum:
    def test_persist_value(self):
        assert WriteOperationType.AGGREGATE_PERSIST.value == "aggregate_persist"

    def test_merge_value(self):
        assert WriteOperationType.AGGREGATE_MERGE.value == "aggregate_merge"

    def test_persist_and_merge_are_distinct(self):
        assert WriteOperationType.AGGREGATE_PERSIST != WriteOperationType.AGGREGATE_MERGE


class TestAggregateStateCapabilities:
    def test_default_caps_have_aggregate_disabled(self):
        caps = DataFrameWriteCapabilities(platform_name="test")
        assert caps.supports_aggregate_persist is False
        assert caps.supports_aggregate_merge is False

    def test_pyspark_preset_enables_both(self):
        assert PYSPARK_WRITE_CAPABILITIES.supports_aggregate_persist is True
        assert PYSPARK_WRITE_CAPABILITIES.supports_aggregate_merge is True

    def test_polars_preset_keeps_both_disabled(self):
        assert POLARS_WRITE_CAPABILITIES.supports_aggregate_persist is False
        assert POLARS_WRITE_CAPABILITIES.supports_aggregate_merge is False

    def test_pandas_preset_keeps_both_disabled(self):
        assert PANDAS_WRITE_CAPABILITIES.supports_aggregate_persist is False
        assert PANDAS_WRITE_CAPABILITIES.supports_aggregate_merge is False


class TestSupportsOperationAggregateStateRouting:
    def test_persist_consults_dedicated_flag_not_maintenance_caps(self):
        caps = DataFrameWriteCapabilities(
            platform_name="test",
            maintenance_caps=None,
            supports_aggregate_persist=True,
        )
        assert caps.supports_operation(WriteOperationType.AGGREGATE_PERSIST) is True

    def test_merge_consults_dedicated_flag_not_maintenance_caps(self):
        caps = DataFrameWriteCapabilities(
            platform_name="test",
            maintenance_caps=None,
            supports_aggregate_merge=True,
        )
        assert caps.supports_operation(WriteOperationType.AGGREGATE_MERGE) is True

    def test_persist_unsupported_when_flag_false(self):
        caps = DataFrameWriteCapabilities(platform_name="test")
        assert caps.supports_operation(WriteOperationType.AGGREGATE_PERSIST) is False

    def test_merge_unsupported_when_flag_false(self):
        caps = DataFrameWriteCapabilities(platform_name="test")
        assert caps.supports_operation(WriteOperationType.AGGREGATE_MERGE) is False

    def test_unsupported_operations_includes_aggregate_when_off(self):
        caps = DataFrameWriteCapabilities(platform_name="test", supports_bulk_load=True)
        unsupported = caps.get_unsupported_operations()
        assert WriteOperationType.AGGREGATE_PERSIST in unsupported
        assert WriteOperationType.AGGREGATE_MERGE in unsupported

    def test_unsupported_operations_excludes_aggregate_when_on(self):
        caps = DataFrameWriteCapabilities(
            platform_name="test",
            supports_aggregate_persist=True,
            supports_aggregate_merge=True,
        )
        unsupported = caps.get_unsupported_operations()
        assert WriteOperationType.AGGREGATE_PERSIST not in unsupported
        assert WriteOperationType.AGGREGATE_MERGE not in unsupported


def _make_manager_with_caps(caps: DataFrameWriteCapabilities) -> DataFrameWriteOperationsManager:
    manager = DataFrameWriteOperationsManager.__new__(DataFrameWriteOperationsManager)
    manager.platform_name = caps.platform_name
    manager.spark_session = None
    manager.logger = MagicMock()
    manager._maintenance_ops = None
    manager._capabilities = caps
    return manager


class TestExecuteAggregatePersist:
    def test_returns_failure_when_unsupported(self, tmp_path: Path):
        manager = _make_manager_with_caps(DataFrameWriteCapabilities(platform_name="test"))
        result = manager.execute_aggregate_persist(tmp_path / "state", lambda: None)
        assert result.success is False
        assert result.operation_type == WriteOperationType.AGGREGATE_PERSIST
        assert "aggregate_persist" in result.error_message
        assert "sketch APIs" in result.error_message

    def test_invokes_builder_and_returns_success(self, tmp_path: Path, monkeypatch):
        caps = DataFrameWriteCapabilities(platform_name="test", supports_aggregate_persist=True)
        manager = _make_manager_with_caps(caps)

        sentinel_df = MagicMock(name="state_df")
        builder = MagicMock(return_value=sentinel_df)

        monkeypatch.setattr(
            manager,
            "_persist_dataframe_to_parquet",
            lambda df, path, compression: (10, 4096, 1),
        )

        target = tmp_path / "state"
        result = manager.execute_aggregate_persist(target, builder, compression="zstd")

        builder.assert_called_once_with()
        assert result.success is True
        assert result.operation_type == WriteOperationType.AGGREGATE_PERSIST
        assert result.rows_affected == 10
        assert result.bytes_written == 4096
        assert result.file_count == 1
        assert result.compression == "zstd"
        assert target.exists()

    def test_builder_exception_returns_failure(self, tmp_path: Path):
        caps = DataFrameWriteCapabilities(platform_name="test", supports_aggregate_persist=True)
        manager = _make_manager_with_caps(caps)

        def boom():
            raise RuntimeError("builder blew up")

        result = manager.execute_aggregate_persist(tmp_path / "state", boom)
        assert result.success is False
        assert "builder blew up" in result.error_message
        assert result.operation_type == WriteOperationType.AGGREGATE_PERSIST

    def test_persist_failure_cleans_up_empty_target_dir(self, tmp_path: Path, monkeypatch):
        caps = DataFrameWriteCapabilities(platform_name="test", supports_aggregate_persist=True)
        manager = _make_manager_with_caps(caps)
        builder = MagicMock(return_value=MagicMock(name="state_df"))

        def boom_persist(df, path, compression):
            raise RuntimeError("write blew up")

        monkeypatch.setattr(manager, "_persist_dataframe_to_parquet", boom_persist)

        target = tmp_path / "state"
        result = manager.execute_aggregate_persist(target, builder)
        assert result.success is False
        assert not target.exists()

    def test_persist_failure_preserves_pre_existing_target_dir(self, tmp_path: Path, monkeypatch):
        caps = DataFrameWriteCapabilities(platform_name="test", supports_aggregate_persist=True)
        manager = _make_manager_with_caps(caps)
        builder = MagicMock(return_value=MagicMock(name="state_df"))
        monkeypatch.setattr(
            manager,
            "_persist_dataframe_to_parquet",
            lambda df, path, compression: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        target = tmp_path / "state"
        target.mkdir()
        sentinel = target / "marker.txt"
        sentinel.write_text("preexisting")
        result = manager.execute_aggregate_persist(target, builder)
        assert result.success is False
        assert target.exists()
        assert sentinel.read_text() == "preexisting"


class TestExecuteAggregateMerge:
    def test_returns_failure_when_unsupported(self, tmp_path: Path):
        manager = _make_manager_with_caps(DataFrameWriteCapabilities(platform_name="test"))
        result = manager.execute_aggregate_merge(tmp_path / "state", lambda _path: None)
        assert result.success is False
        assert result.operation_type == WriteOperationType.AGGREGATE_MERGE
        assert "aggregate_merge" in result.error_message

    def test_invokes_extractor_and_records_value_in_metrics(self, tmp_path: Path):
        caps = DataFrameWriteCapabilities(platform_name="test", supports_aggregate_merge=True)
        manager = _make_manager_with_caps(caps)

        source = tmp_path / "state"
        merge_extract = MagicMock(return_value=14836.5)
        result = manager.execute_aggregate_merge(source, merge_extract)

        merge_extract.assert_called_once_with(source)
        assert result.success is True
        assert result.operation_type == WriteOperationType.AGGREGATE_MERGE
        assert result.rows_affected == 1
        assert result.metrics["aggregate_value"] == 14836.5

    def test_extractor_exception_returns_failure(self, tmp_path: Path):
        caps = DataFrameWriteCapabilities(platform_name="test", supports_aggregate_merge=True)
        manager = _make_manager_with_caps(caps)

        def boom(_path):
            raise ValueError("merge failed")

        result = manager.execute_aggregate_merge(tmp_path / "state", boom)
        assert result.success is False
        assert "merge failed" in result.error_message


class TestUnsupportedAggregateStateMessage:
    def test_persist_message_mentions_sketch_apis(self):
        manager = _make_manager_with_caps(DataFrameWriteCapabilities(platform_name="pandas-df"))
        msg = manager.get_unsupported_message(WriteOperationType.AGGREGATE_PERSIST)
        assert "pandas-df" in msg
        assert "aggregate_persist" in msg
        assert "sketch" in msg.lower()

    def test_merge_message_mentions_sketch_apis(self):
        manager = _make_manager_with_caps(DataFrameWriteCapabilities(platform_name="polars-df"))
        msg = manager.get_unsupported_message(WriteOperationType.AGGREGATE_MERGE)
        assert "polars-df" in msg
        assert "aggregate_merge" in msg
        assert "sketch" in msg.lower()


class TestAggregatePersistStorageValidation:
    def test_persist_round_trip_reports_matching_on_disk_size(self, tmp_path: Path):
        pytest.importorskip("polars")
        import polars as pl

        caps = DataFrameWriteCapabilities(platform_name="polars", supports_aggregate_persist=True)
        manager = _make_manager_with_caps(caps)
        builder = lambda: pl.DataFrame({"group": ["a", "b"], "sketch": [b"\x01\x02", b"\x03\x04"]})

        target = tmp_path / "state"
        result = manager.execute_aggregate_persist(target, builder, compression="zstd")

        on_disk_bytes = sum(f.stat().st_size for f in target.rglob("*.parquet"))
        on_disk_files = len(list(target.rglob("*.parquet")))
        assert result.success is True
        assert result.bytes_written == on_disk_bytes > 0
        assert result.file_count == on_disk_files == 1
        assert result.validation_passed is True
        (entry,) = result.validation_results
        assert entry["check"] == "storage_size"
        assert entry["passed"] is True
        assert entry["reason"] is None

    def test_mismatched_reported_size_fails_validation_not_persist(self, tmp_path: Path, monkeypatch):
        caps = DataFrameWriteCapabilities(platform_name="test", supports_aggregate_persist=True)
        manager = _make_manager_with_caps(caps)
        monkeypatch.setattr(
            manager,
            "_persist_dataframe_to_parquet",
            lambda df, path, compression: (10, 4096, 1),
        )

        target = tmp_path / "state"
        result = manager.execute_aggregate_persist(target, MagicMock(), compression="zstd")

        assert result.success is True
        assert result.validation_passed is False
        (entry,) = result.validation_results
        assert entry["check"] == "storage_size"
        assert entry["passed"] is False
        assert entry["expected_bytes"] == 0
        assert entry["actual_bytes"] == 4096
        assert entry["reason"] is not None

    def test_empty_target_dir_passes_for_zero_rows(self, tmp_path: Path):
        manager = _make_manager_with_caps(
            DataFrameWriteCapabilities(platform_name="test", supports_aggregate_persist=True)
        )
        target = tmp_path / "state"
        target.mkdir()
        entry = manager.validate_persisted_storage_size(target, rows_affected=0, bytes_written=0, file_count=0)
        assert entry["check"] == "storage_size"
        assert entry["passed"] is True


class TestAggregateStateOpStorageValidationPropagation:
    def test_validation_failure_reports_validation_failed_without_merge(self, tmp_path: Path, monkeypatch) -> None:
        from types import SimpleNamespace
        from unittest.mock import Mock

        from benchbox.core.write_primitives import dataframe_operations as df_ops
        from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark

        benchmark = object.__new__(WritePrimitivesBenchmark)
        benchmark.output_dir = str(tmp_path)
        spec = SimpleNamespace(
            supported_platforms=("pyspark",),
            sketch_type="hll",
            source_table="orders",
            target_subdir="_aggregate_state",
            group_cols=["g"],
            value_col="v",
            sketch_alias="sketch",
        )
        benchmark.operations_manager = SimpleNamespace(
            get_operation=lambda op_id: SimpleNamespace(aggregate_state=spec)
        )
        monkeypatch.setattr(benchmark, "_resolve_aggregate_source_path", lambda *args, **kwargs: tmp_path / "source")

        persist_result = SimpleNamespace(
            success=True,
            validation_passed=False,
            validation_results=[
                {
                    "check": "storage_size",
                    "passed": False,
                    "reason": "reported bytes_written=4096 != on-disk bytes=0",
                }
            ],
            rows_affected=10,
            error_message=None,
        )

        def fake_persist(target_path, state_builder):
            target_path.mkdir(parents=True, exist_ok=True)
            (target_path / "evidence.parquet").write_bytes(b"drift")
            return persist_result

        merge_mock = Mock()
        monkeypatch.setattr(
            df_ops,
            "get_dataframe_write_manager",
            lambda *args, **kwargs: SimpleNamespace(
                execute_aggregate_persist=fake_persist, execute_aggregate_merge=merge_mock
            ),
        )
        monkeypatch.setattr(df_ops, "make_pyspark_hll_persist_builder", lambda **kwargs: object())

        adapter = SimpleNamespace(platform_name="pyspark-df", spark=object())
        result = benchmark._execute_aggregate_state_op("agg_drift", adapter=adapter)

        assert result["status"] == "VALIDATION_FAILED"
        assert "4096" in result["error"]
        assert result["rows_returned"] == 10
        merge_mock.assert_not_called()
        assert (tmp_path / "_aggregate_state" / "evidence.parquet").exists()


