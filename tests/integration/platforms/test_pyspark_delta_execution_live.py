# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.core.transaction_primitives.dataframe_operations import (
    DataFrameTransactionOperationsManager,
    TransactionOperationType,
)

from .delta_live_helpers import delta_live_skip_reason, make_delta_spark_session

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_integration,
    pytest.mark.live_delta,
    pytest.mark.skipif(
        delta_live_skip_reason() is not None,
        reason=delta_live_skip_reason() or "PySpark + Delta Lake runtime unavailable",
    ),
]


@pytest.fixture(scope="module")
def spark(tmp_path_factory, pyspark_test_environment):

    warehouse = tmp_path_factory.mktemp("delta_execution_warehouse")
    session = make_delta_spark_session(warehouse, app_name="benchbox-delta-execution")
    yield session
    session.stop()


@pytest.fixture
def manager(spark):
    return DataFrameTransactionOperationsManager("pyspark-df", spark_session=spark)


@pytest.fixture
def table_dir(tmp_path: Path) -> Path:
    target = tmp_path / "tables"
    target.mkdir()
    return target


def _seed(spark, path: str, rows: list[tuple[int, str]]) -> None:
    spark.createDataFrame(rows, ["id", "v"]).write.format("delta").mode("overwrite").save(path)


def _read_all(spark, path: str) -> dict[int, str]:
    return {row["id"]: row["v"] for row in spark.read.format("delta").load(path).collect()}


class TestManagerCapabilities:
    def test_supports_transactions_on_delta(self, manager):
        assert manager.supports_transactions() is True
        assert manager.get_capabilities().table_format == "delta"

    def test_atomic_operations_supported(self, manager):
        for operation in (
            TransactionOperationType.ATOMIC_INSERT,
            TransactionOperationType.ATOMIC_UPDATE,
            TransactionOperationType.ATOMIC_DELETE,
            TransactionOperationType.ATOMIC_MERGE,
            TransactionOperationType.ROLLBACK_TO_VERSION,
            TransactionOperationType.TIME_TRAVEL_QUERY,
        ):
            assert manager.supports_operation(operation) is True


class TestFormatValidation:
    def test_delta_table_valid(self, spark, manager, table_dir: Path):
        path = str(table_dir / "valid")
        _seed(spark, path, [(1, "a")])
        is_valid, _ = manager.validate_table_format(path)
        assert is_valid is True

    def test_plain_parquet_rejected(self, spark, manager, table_dir: Path):
        path = str(table_dir / "plain")
        spark.createDataFrame([(1, "a")], ["id", "v"]).write.format("parquet").mode("overwrite").save(path)
        is_valid, message = manager.validate_table_format(path)
        assert is_valid is False
        assert "Delta Lake" in message

    def test_missing_path_rejected(self, manager, table_dir: Path):
        is_valid, _ = manager.validate_table_format(str(table_dir / "absent"))
        assert is_valid is False

    def test_traversal_rejected(self, manager):
        is_valid, _ = manager.validate_table_format("/tmp/../etc/passwd")
        assert is_valid is False


class TestAtomicWrites:
    def test_insert_appends_and_bumps_version(self, spark, manager, table_dir: Path):
        path = str(table_dir / "insert")
        _seed(spark, path, [(1, "a"), (2, "b")])
        version_before = manager.get_table_version(path)

        new_rows = spark.createDataFrame([(3, "c"), (4, "d")], ["id", "v"])
        result = manager.execute_atomic_insert(table_path=path, dataframe=new_rows)

        assert result.success is True
        assert result.version_before == version_before == 0
        assert result.version_after == 1
        assert result.rows_affected == 2
        assert _read_all(spark, path) == {1: "a", 2: "b", 3: "c", 4: "d"}

    def test_update_changes_matching_rows(self, spark, manager, table_dir: Path):
        path = str(table_dir / "update")
        _seed(spark, path, [(1, "a"), (2, "b")])

        result = manager.execute_atomic_update(table_path=path, condition="id = 1", updates={"v": "lit:z"})

        assert result.success is True
        assert result.version_after == result.version_before + 1
        assert _read_all(spark, path) == {1: "z", 2: "b"}

    def test_delete_removes_matching_rows(self, spark, manager, table_dir: Path):
        path = str(table_dir / "delete")
        _seed(spark, path, [(1, "a"), (2, "b"), (3, "c")])

        result = manager.execute_atomic_delete(table_path=path, condition="id = 2")

        assert result.success is True
        assert _read_all(spark, path) == {1: "a", 3: "c"}

    def test_merge_upserts(self, spark, manager, table_dir: Path):
        path = str(table_dir / "merge")
        _seed(spark, path, [(1, "a"), (2, "b")])
        source = spark.createDataFrame([(2, "B"), (3, "C")], ["id", "v"])

        result = manager.execute_atomic_merge(
            table_path=path,
            source_dataframe=source,
            merge_condition="target.id = source.id",
            when_matched={"v": "source.v"},
            when_not_matched={"id": "source.id", "v": "source.v"},
        )

        assert result.success is True
        assert _read_all(spark, path) == {1: "a", 2: "B", 3: "C"}


class TestHistory:
    def test_rollback_restores_previous_data(self, spark, manager, table_dir: Path):
        path = str(table_dir / "rollback")
        _seed(spark, path, [(1, "a")])
        manager.execute_atomic_insert(
            table_path=path,
            dataframe=spark.createDataFrame([(2, "b")], ["id", "v"]),
        )
        assert manager.get_table_version(path) == 1

        result = manager.execute_rollback_to_version(table_path=path, version=0)

        assert result.success is True
        assert _read_all(spark, path) == {1: "a"}

    def test_time_travel_reads_old_version(self, spark, manager, table_dir: Path):
        path = str(table_dir / "timetravel")
        _seed(spark, path, [(1, "a")])
        manager.execute_atomic_delete(table_path=path, condition="id = 1")
        assert spark.read.format("delta").load(path).count() == 0

        result = manager.execute_time_travel_query(table_path=path, version=0)

        assert result.success is True
        assert result.metrics["row_count"] == 1

    def test_version_increments_across_writes(self, spark, manager, table_dir: Path):
        path = str(table_dir / "versions")
        _seed(spark, path, [(1, "a")])
        assert manager.get_table_version(path) == 0
        manager.execute_atomic_delete(table_path=path, condition="id = 1")
        assert manager.get_table_version(path) == 1


