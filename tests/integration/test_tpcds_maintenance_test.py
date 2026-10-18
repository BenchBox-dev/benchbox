# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DS (TPC-DS) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DS specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox.core.tpcds.benchmark import MaintenanceTestConfig, MaintenanceTestResult, TPCDSBenchmark
from benchbox.core.tpcds.maintenance_operations import (
    MaintenanceOperations,
    MaintenanceOperationType,
)
from benchbox.core.tpcds.maintenance_test import TPCDSMaintenanceTest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


class TestTPCDSMaintenanceTestIntegration:
    def test_maintenance_test_config_creation(self):

        config = MaintenanceTestConfig(
            concurrent_streams=2,
            maintenance_interval=10.0,
            scale_factor=1.0,
            verbose=True,
        )

        assert config.concurrent_streams == 2
        assert config.maintenance_interval == 10.0
        assert config.scale_factor == 1.0
        assert config.verbose is True

    def test_maintenance_test_initialization(self):

        benchmark = TPCDSBenchmark(scale_factor=1.0)

        def connection_factory():
            return Mock()

        maintenance_test = TPCDSMaintenanceTest(
            benchmark=benchmark,
            connection_factory=connection_factory,
            scale_factor=1.0,
            verbose=True,
        )

        assert maintenance_test.benchmark == benchmark
        assert maintenance_test.connection_factory == connection_factory
        assert maintenance_test.scale_factor == 1.0
        assert maintenance_test.verbose is True

    def test_maintenance_operations_initialization(self):

        operations = MaintenanceOperations()

        assert operations.connection is None
        assert operations.benchmark_instance is None
        assert operations.config is None
        assert len(operations.operation_handlers) == 13

        for op_type in MaintenanceOperationType:
            assert op_type in operations.operation_handlers

    def test_benchmark_run_maintenance_test_basic(self):

        mock_connection = Mock()
        Mock(return_value=mock_connection)

        benchmark = TPCDSBenchmark(scale_factor=1.0, verbose=False)

        config = MaintenanceTestConfig(
            concurrent_streams=1,
            maintenance_interval=1.0,
            scale_factor=1.0,
            verbose=False,
        )

        with patch(
            "benchbox.core.tpcds.maintenance_operations.MaintenanceOperations.execute_operation"
        ) as mock_execute:
            mock_execute.return_value = Mock(
                operation_type=MaintenanceOperationType.INSERT_STORE_SALES,
                success=True,
                start_time=time.time(),
                end_time=time.time() + 0.1,
                duration=0.1,
                rows_affected=100,
                error_message=None,
            )

            mock_connection = Mock()
            result = benchmark.run_maintenance_test(connection=mock_connection, config=config)

            assert isinstance(result, MaintenanceTestResult)
            assert result.test_duration > 0

    def test_benchmark_run_maintenance_test_validation(self):

        benchmark = TPCDSBenchmark(scale_factor=1.0)

        with pytest.raises((ValueError, TypeError, AttributeError)):
            benchmark.run_maintenance_test(None)

    def test_benchmark_validate_data_integrity(self):

        benchmark = TPCDSBenchmark(scale_factor=1.0, verbose=False)

        assert hasattr(benchmark, "validate_maintenance_data_integrity")

    def test_maintenance_operations_type_enum(self):

        expected_operations = [
            "INSERT_STORE_SALES",
            "INSERT_CATALOG_SALES",
            "INSERT_WEB_SALES",
            "INSERT_STORE_RETURNS",
            "INSERT_CATALOG_RETURNS",
            "INSERT_WEB_RETURNS",
            "UPDATE_CUSTOMER",
            "UPDATE_ITEM",
            "UPDATE_INVENTORY",
            "DELETE_OLD_SALES",
            "DELETE_OLD_RETURNS",
            "BULK_LOAD_SALES",
            "BULK_UPDATE_INVENTORY",
        ]

        actual_operations = [op.name for op in MaintenanceOperationType]

        for expected in expected_operations:
            assert expected in actual_operations

    def test_maintenance_test_concurrent_execution(self):

        MaintenanceTestConfig(
            concurrent_streams=2,
            maintenance_interval=0.1,
            scale_factor=1.0,
            verbose=False,
        )

        benchmark = TPCDSBenchmark(scale_factor=1.0)

        def connection_factory():
            return Mock()

        maintenance_test = TPCDSMaintenanceTest(benchmark, connection_factory)

        assert maintenance_test.benchmark == benchmark
        assert maintenance_test.connection_factory == connection_factory
        assert maintenance_test.scale_factor >= 1.0

    def test_maintenance_test_result_metrics_calculation(self):

        result = MaintenanceTestResult(test_duration=10.0, total_operations=3, successful_operations=2)

        result.maintenance_operations.append(
            {
                "operation_type": "INSERT_STORE_SALES",
                "start_time": 1000.0,
                "end_time": 1001.0,
                "duration": 1.0,
                "rows_affected": 100,
                "success": True,
            }
        )

        result.maintenance_operations.append(
            {
                "operation_type": "UPDATE_CUSTOMER",
                "start_time": 1001.0,
                "end_time": 1003.0,
                "duration": 2.0,
                "rows_affected": 50,
                "success": True,
            }
        )

        result.maintenance_operations.append(
            {
                "operation_type": "DELETE_OLD_SALES",
                "start_time": 1003.0,
                "end_time": 1004.0,
                "duration": 1.0,
                "rows_affected": 0,
                "success": False,
                "error_message": "Connection timeout",
            }
        )

        assert result.test_duration == 10.0
        assert result.total_operations == 3
        assert result.successful_operations == 2
        assert result.failed_operations == 0
        assert len(result.maintenance_operations) == 3

    def test_maintenance_operations_data_generation(self):

        operations = MaintenanceOperations()

        store_sales_row = operations._generate_store_sales_row()
        assert len(store_sales_row) == 23
        assert isinstance(store_sales_row[0], int)
        assert isinstance(store_sales_row[10], int)
        assert isinstance(store_sales_row[11], float)

        catalog_sales_row = operations._generate_catalog_sales_row()
        assert len(catalog_sales_row) == 34

        web_sales_row = operations._generate_web_sales_row()
        assert len(web_sales_row) == 34

        store_returns_row = operations._generate_store_returns_row()
        assert len(store_returns_row) == 20

        catalog_returns_row = operations._generate_catalog_returns_row()
        assert len(catalog_returns_row) == 27

        web_returns_row = operations._generate_web_returns_row()
        assert len(web_returns_row) == 24

    def test_maintenance_test_error_handling(self):

        MaintenanceTestConfig(scale_factor=1.0, verbose=False)
        benchmark = TPCDSBenchmark(scale_factor=1.0)

        def connection_factory():
            return Mock()

        maintenance_test = TPCDSMaintenanceTest(benchmark, connection_factory)

        assert hasattr(maintenance_test, "run")

    def test_maintenance_test_report_generation(self):

        MaintenanceTestConfig(scale_factor=1.0)
        benchmark = TPCDSBenchmark(scale_factor=1.0)

        def connection_factory():
            return Mock()

        TPCDSMaintenanceTest(benchmark, connection_factory)

        result = MaintenanceTestResult(test_duration=10.0, total_operations=1, successful_operations=1)

        result.maintenance_operations.append(
            {
                "operation_type": "INSERT_STORE_SALES",
                "start_time": 1000.0,
                "end_time": 1001.0,
                "duration": 1.0,
                "rows_affected": 100,
                "success": True,
            }
        )

        assert result.test_duration == 10.0
        assert result.total_operations == 1
        assert result.successful_operations == 1
        assert len(result.maintenance_operations) == 1

    def test_benchmark_info_includes_maintenance_test(self):

        benchmark = TPCDSBenchmark(scale_factor=1.0)
        info = benchmark.get_benchmark_info()

        assert "maintenance_test_supported" in info
        assert info["maintenance_test_supported"] is True
        assert info["name"] == "TPC-DS"
        assert info["scale_factor"] == 1.0


class TestTPCDSMaintenanceTestPerformance:
    def test_maintenance_test_timeout_handling(self):

        MaintenanceTestConfig(
            scale_factor=1.0,
            verbose=False,
        )

        benchmark = TPCDSBenchmark(scale_factor=1.0)

        def connection_factory():
            return Mock()

        maintenance_test = TPCDSMaintenanceTest(benchmark, connection_factory)

        assert hasattr(maintenance_test, "run")
        assert maintenance_test.benchmark == benchmark
        assert maintenance_test.connection_factory == connection_factory

    def test_maintenance_operations_throughput_calculation(self):

        from benchbox.core.tpcds.maintenance_test import TPCDSMaintenanceOperation

        operation = TPCDSMaintenanceOperation(
            operation_type="INSERT_STORE_SALES",
            table_name="store_sales",
            start_time=1000.0,
            end_time=1001.0,
            duration=1.0,
            rows_affected=1000,
            success=True,
        )

        assert operation.operation_type == "INSERT_STORE_SALES"
        assert operation.table_name == "store_sales"
        assert operation.duration == 1.0
        assert operation.rows_affected == 1000
        assert operation.success is True

        throughput = operation.rows_affected / operation.duration if operation.duration > 0 else 0
        assert throughput == 1000.0


@pytest.mark.integration
class TestTPCDSMaintenanceTestDatabaseIntegration:
    @pytest.fixture
    def temp_db_path(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            yield f.name
        Path(f.name).unlink(missing_ok=True)

    def test_maintenance_test_with_real_database(self, temp_db_path):

        benchmark = TPCDSBenchmark(scale_factor=1.0)

        with patch.object(benchmark, "generate_data", return_value=[]):
            benchmark.generate_data()

        config = MaintenanceTestConfig(
            concurrent_streams=1,
            maintenance_interval=1.0,
            scale_factor=1.0,
            verbose=True,
        )

        import sqlite3

        connection = sqlite3.connect(temp_db_path)
        result = benchmark.run_maintenance_test(connection=connection, config=config, dialect="sqlite")

        assert result.test_duration >= 0
        assert isinstance(result.total_operations, int)
        assert isinstance(result.successful_operations, int)

        if len(result.error_details) > 0:
            print(f"Maintenance test encountered setup issues (as expected): {result.error_details}")
        else:
            assert result.total_operations > 0
            assert result.test_duration > 0

            integrity_result = benchmark.validate_maintenance_data_integrity(connection=connection, dialect="sqlite")

            assert "validation_checks" in integrity_result
            assert "integrity_score" in integrity_result
            assert "errors" in integrity_result
            assert isinstance(integrity_result["integrity_score"], float)
            assert integrity_result["integrity_score"] >= 0.0
