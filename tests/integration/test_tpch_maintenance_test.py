# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ H (TPC-H) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-H specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from benchbox.core.tpch.maintenance_test import (
    TPCHMaintenanceOperation,
    TPCHMaintenanceTest,
    TPCHMaintenanceTestConfig,
)
from benchbox.core.tpch.refresh import generate_refresh_update_sets
from benchbox.core.tpch.schema import LINEITEM, ORDERS
from tests.integration._tpch_refresh_utils import _delete_keys, _file_row_count, _load_sf001_base, _table_count

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]


class TestMaintenanceTest:
    def setup_method(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.scale_factor = 0.1
        self.mock_connection = object()

    def teardown_method(self):
        import shutil

        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_maintenance_test_initialization(self):

        def connection_factory():
            return self.mock_connection

        maintenance_test = TPCHMaintenanceTest(
            connection_factory=connection_factory,
            scale_factor=self.scale_factor,
            output_dir=self.temp_dir,
            verbose=True,
        )

        assert maintenance_test.scale_factor == self.scale_factor
        assert maintenance_test.output_dir == self.temp_dir
        assert maintenance_test.verbose is True
        assert maintenance_test.connection_factory is not None


class TestMaintenanceOperation:
    def test_operation_creation(self):

        operation = TPCHMaintenanceOperation(
            operation_type="RF1",
            start_time=time.time(),
            end_time=time.time() + 1,
            duration=1.0,
            rows_affected=100,
            success=True,
        )

        assert operation.operation_type == "RF1"
        assert operation.duration == 1.0
        assert operation.rows_affected == 100
        assert operation.success is True
        assert operation.error is None

    def test_operation_with_error(self):

        operation = TPCHMaintenanceOperation(
            operation_type="RF2",
            start_time=time.time(),
            end_time=time.time() + 1,
            duration=1.0,
            rows_affected=0,
            success=False,
            error="Test error",
        )

        assert operation.success is False
        assert operation.error == "Test error"


class TestMaintenanceTestConfig:
    def test_config_defaults(self):

        config = TPCHMaintenanceTestConfig()

        assert config.scale_factor == 1.0
        assert config.maintenance_pairs == 1
        assert config.rf1_interval == 30.0
        assert config.rf2_interval == 30.0
        assert config.concurrent_with_queries is True
        assert config.validate_integrity is True
        assert config.verbose is False

    def test_config_custom_values(self):

        config = TPCHMaintenanceTestConfig(
            scale_factor=10.0,
            maintenance_pairs=5,
            rf1_interval=60.0,
            rf2_interval=120.0,
            concurrent_with_queries=False,
            validate_integrity=False,
            verbose=True,
        )

        assert config.scale_factor == 10.0
        assert config.maintenance_pairs == 5
        assert config.rf1_interval == 60.0
        assert config.rf2_interval == 120.0
        assert config.concurrent_with_queries is False
        assert config.validate_integrity is False
        assert config.verbose is True


@pytest.mark.parametrize("engine", ["duckdb", "sqlite"])
def test_maintenance_pairs_against_in_memory_database(tmp_path: Path, engine: str) -> None:
    if engine == "duckdb":
        import duckdb

        connection = duckdb.connect(":memory:")
    else:
        connection = sqlite3.connect(":memory:")

    class NonclosingConnection:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def close(self):
            pass

    try:
        base_counts = _load_sf001_base(connection)
        update_sets = generate_refresh_update_sets(0.01, 3, 2, tmp_path / "expected")
        expected_rows = []
        for update_set in update_sets.values():
            rf1_rows = _file_row_count(update_set.orders_path) + _file_row_count(update_set.lineitem_path)
            keys = _delete_keys(update_set)
            key_list = ", ".join(str(key) for key in keys)
            rf2_rows = len(keys) + _table_count(connection, LINEITEM.name, f"L_ORDERKEY IN ({key_list})")
            expected_rows.extend([rf1_rows, rf2_rows])

        maintenance = TPCHMaintenanceTest(lambda: NonclosingConnection(connection), scale_factor=0.01, first_set=3)
        result = maintenance.run_maintenance_test(
            maintenance_pairs=2,
            concurrent_with_queries=False,
            rf1_interval=0.0,
            rf2_interval=0.0,
        )

        assert result.success, result.errors
        assert result.config.scale_factor == 0.01
        assert result.config.concurrent_with_queries is False
        assert result.total_time > 0
        assert result.start_time
        assert result.end_time
        assert result.overall_throughput > 0
        assert result.total_operations == 4
        assert result.successful_operations == 4
        assert [operation.operation_type for operation in result.operations] == ["RF1", "RF2", "RF1", "RF2"]
        assert [operation.update_set for operation in result.operations] == [3, 3, 4, 4]
        assert [operation.rows_affected for operation in result.operations] == expected_rows
        assert _table_count(connection, ORDERS.name) == base_counts[ORDERS.name] + sum(
            _file_row_count(update_set.orders_path) - len(_delete_keys(update_set))
            for update_set in update_sets.values()
        )

        repeated = maintenance.run_maintenance_test(maintenance_pairs=2, rf1_interval=0.0, rf2_interval=0.0)
        assert not repeated.success
        assert repeated.config.concurrent_with_queries is True
        assert "already applied" in repeated.errors[0]
    finally:
        connection.close()


def test_maintenance_reports_connection_failure() -> None:
    def failing_connection_factory():
        raise RuntimeError("Connection failed")

    maintenance = TPCHMaintenanceTest(failing_connection_factory, scale_factor=0.01)
    result = maintenance.run_maintenance_test(maintenance_pairs=1, rf1_interval=0.0, rf2_interval=0.0)

    assert not result.success
    assert result.failed_operations == 1
    assert result.total_operations == 1
    assert "Connection failed" in result.errors[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
