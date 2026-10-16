# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ H (TPC-H) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-H specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import logging
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from benchbox.core.tpch.refresh import (
    TPCHRefreshUpdateSet,
    apply_rf1,
    apply_rf2,
    check_rf1_preconditions,
    check_rf2_applied,
    check_rf2_preconditions,
    generate_refresh_update_sets,
)
from benchbox.core.tpch.schema import LINEITEM, ORDERS
from benchbox.utils.clock import elapsed_seconds, mono_time


@dataclass
class TPCHMaintenanceTestConfig:
    scale_factor: float = 1.0
    maintenance_pairs: int = 1
    rf1_interval: float = 30.0
    rf2_interval: float = 30.0
    concurrent_with_queries: bool = True
    validate_integrity: bool = True
    verbose: bool = False
    output_dir: Optional[Path] = None
    first_set: int = 1


@dataclass
class TPCHMaintenanceOperation:
    operation_type: str
    start_time: float
    end_time: float
    duration: float
    rows_affected: int
    success: bool
    error: Optional[str] = None
    update_set: Optional[int] = None


@dataclass
class TPCHMaintenanceTestResult:
    config: TPCHMaintenanceTestConfig
    start_time: str
    end_time: str
    total_time: float
    rf1_operations: int
    rf2_operations: int
    total_operations: int
    successful_operations: int
    failed_operations: int
    operations: list[TPCHMaintenanceOperation] = field(default_factory=list)
    overall_throughput: float = 0.0
    success: bool = True
    errors: list[str] = field(default_factory=list)

    @property
    def scale_factor(self) -> float:
        return self.config.scale_factor


class TPCHMaintenanceTest:
    def __init__(
        self,
        connection_factory: Callable[[], Any],
        scale_factor: float = 1.0,
        output_dir: Optional[Path] = None,
        verbose: bool = False,
        first_set: int = 1,
    ) -> None:
        if isinstance(first_set, bool) or not isinstance(first_set, int):
            raise TypeError(f"first_set must be an integer, got {type(first_set).__name__}")
        if first_set < 1:
            raise ValueError(f"first_set must be positive, got {first_set}")
        self.connection_factory = connection_factory
        self.scale_factor = scale_factor
        self.output_dir = output_dir or Path.cwd() / "maintenance_test"
        self.verbose = verbose
        self.first_set = first_set

        self.logger = logging.getLogger(__name__)
        if verbose:
            self.logger.setLevel(logging.INFO)

    def run_maintenance_test(
        self,
        maintenance_pairs: int = 1,
        concurrent_with_queries: bool = True,
        _query_stream_duration: Optional[float] = None,
        rf1_interval: float = 30.0,
        rf2_interval: float = 30.0,
        validate_integrity: bool = True,
    ) -> TPCHMaintenanceTestResult:
        config = TPCHMaintenanceTestConfig(
            scale_factor=self.scale_factor,
            maintenance_pairs=maintenance_pairs,
            first_set=self.first_set,
            rf1_interval=rf1_interval,
            rf2_interval=rf2_interval,
            concurrent_with_queries=concurrent_with_queries,
            validate_integrity=validate_integrity,
            verbose=self.verbose,
            output_dir=self.output_dir,
        )
        result = TPCHMaintenanceTestResult(
            config=config,
            start_time="",
            end_time="",
            total_time=0.0,
            rf1_operations=0,
            rf2_operations=0,
            total_operations=0,
            successful_operations=0,
            failed_operations=0,
        )

        try:
            if maintenance_pairs < 0:
                raise ValueError(f"maintenance_pairs must not be negative, got {maintenance_pairs}")
            if self.verbose:
                self.logger.info("Starting TPC-H Maintenance Test")
                self.logger.info(f"Maintenance pairs: {maintenance_pairs}")
                self.logger.info(f"Scale factor: {self.scale_factor}")

            if maintenance_pairs == 0:
                return self._finish_maintenance_test(result, mono_time())

            with tempfile.TemporaryDirectory(prefix="benchbox-tpch-refresh-") as temporary:
                update_sets = generate_refresh_update_sets(
                    self.scale_factor,
                    self.first_set,
                    maintenance_pairs,
                    Path(temporary),
                )
                return self._run_maintenance_pairs(
                    result,
                    update_sets,
                    maintenance_pairs,
                    rf1_interval,
                    rf2_interval,
                )
        except Exception as exc:
            result.start_time = result.start_time or datetime.now().isoformat()
            result.end_time = datetime.now().isoformat()
            result.success = False
            result.errors.append(f"Maintenance Test execution failed: {exc}")
            if self.verbose:
                self.logger.error(f"Maintenance Test failed: {exc}")
            return result

    def _run_maintenance_pairs(
        self,
        result: TPCHMaintenanceTestResult,
        update_sets: dict[int, TPCHRefreshUpdateSet],
        maintenance_pairs: int,
        rf1_interval: float,
        rf2_interval: float,
    ) -> TPCHMaintenanceTestResult:
        start_time = mono_time()
        result.start_time = datetime.now().isoformat()

        for pair_id in range(maintenance_pairs):
            update_set = update_sets[self.first_set + pair_id]
            if self.verbose:
                self.logger.info(f"Executing maintenance pair {pair_id + 1} using update set {update_set.set_id}")

            rf1_result = self._execute_rf1(pair_id, update_set)
            result.operations.append(rf1_result)
            result.total_operations += 1
            result.rf1_operations += 1
            if not rf1_result.success:
                result.failed_operations += 1
                result.errors.append(f"RF1 pair {pair_id + 1} failed: {rf1_result.error}")
                break
            result.successful_operations += 1

            if rf1_interval > 0:
                time.sleep(rf1_interval)

            rf2_result = self._execute_rf2(pair_id, update_set)
            result.operations.append(rf2_result)
            result.total_operations += 1
            result.rf2_operations += 1
            if not rf2_result.success:
                result.failed_operations += 1
                result.errors.append(f"RF2 pair {pair_id + 1} failed: {rf2_result.error}")
                break
            result.successful_operations += 1

            if rf2_interval > 0 and pair_id < maintenance_pairs - 1:
                time.sleep(rf2_interval)

        return self._finish_maintenance_test(result, start_time)

    def _finish_maintenance_test(
        self,
        result: TPCHMaintenanceTestResult,
        start_time: float,
    ) -> TPCHMaintenanceTestResult:
        result.total_time = elapsed_seconds(start_time)
        result.end_time = datetime.now().isoformat()
        if not result.start_time:
            result.start_time = result.end_time
        if result.total_time > 0:
            result.overall_throughput = result.successful_operations / result.total_time
        result.success = result.failed_operations == 0
        if self.verbose:
            self.logger.info(f"Maintenance Test completed in {result.total_time:.3f}s")
            self.logger.info(f"Successful operations: {result.successful_operations}/{result.total_operations}")
            self.logger.info(f"Overall throughput: {result.overall_throughput:.2f} ops/sec")
        return result

    def get_maintenance_operations_sql(
        self,
        pair_id: int = 0,
        placeholder: str = "?",
    ) -> dict[str, list[str]]:
        set_id = self.first_set + pair_id
        order_columns = ", ".join(column.name for column in ORDERS.columns)
        lineitem_columns = ", ".join(column.name for column in LINEITEM.columns)
        order_values = ", ".join(placeholder for _ in ORDERS.columns)
        lineitem_values = ", ".join(placeholder for _ in LINEITEM.columns)
        rf1_statements = [
            f"-- DBGen refresh set {set_id}",
            f"INSERT INTO ORDERS ({order_columns}) VALUES ({order_values})",
            f"INSERT INTO LINEITEM ({lineitem_columns}) VALUES ({lineitem_values})",
        ]
        rf2_statements = [
            f"-- Delete keys from DBGen refresh set {set_id}",
            f"DELETE FROM LINEITEM WHERE L_ORDERKEY IN ({placeholder})",
            f"DELETE FROM ORDERS WHERE O_ORDERKEY IN ({placeholder})",
        ]
        return {"RF1": rf1_statements, "RF2": rf2_statements}

    def _execute_rf1(self, pair_id: int, update_set: TPCHRefreshUpdateSet) -> TPCHMaintenanceOperation:
        operation = TPCHMaintenanceOperation(
            operation_type="RF1",
            start_time=0.0,
            end_time=0.0,
            duration=0.0,
            rows_affected=0,
            success=False,
            update_set=update_set.set_id,
        )
        connection = None
        try:
            if self.verbose:
                self.logger.info(f"Executing RF1 for pair {pair_id + 1} using update set {update_set.set_id}")
            connection = self.connection_factory()
            check_rf1_preconditions(connection, update_set)
            operation.start_time = mono_time()
            operation.rows_affected = apply_rf1(connection, update_set)
            operation.end_time = mono_time()
            operation.duration = elapsed_seconds(operation.start_time)
            operation.success = True
        except Exception as exc:
            operation.error = str(exc)
            if self.verbose:
                self.logger.error(f"RF1 pair {pair_id + 1} failed: {exc}")
        finally:
            if not operation.end_time:
                operation.end_time = mono_time()
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
        return operation

    def _execute_rf2(self, pair_id: int, update_set: TPCHRefreshUpdateSet) -> TPCHMaintenanceOperation:
        operation = TPCHMaintenanceOperation(
            operation_type="RF2",
            start_time=0.0,
            end_time=0.0,
            duration=0.0,
            rows_affected=0,
            success=False,
            update_set=update_set.set_id,
        )
        connection = None
        try:
            if self.verbose:
                self.logger.info(f"Executing RF2 for pair {pair_id + 1} using update set {update_set.set_id}")
            connection = self.connection_factory()
            deleted_lineitems = check_rf2_preconditions(connection, update_set)
            operation.start_time = mono_time()
            operation.rows_affected = apply_rf2(connection, update_set, deleted_lineitems)
            operation.end_time = mono_time()
            operation.duration = elapsed_seconds(operation.start_time)
            check_rf2_applied(connection, update_set)
            operation.success = True
        except Exception as exc:
            operation.error = str(exc)
            if self.verbose:
                self.logger.error(f"RF2 pair {pair_id + 1} failed: {exc}")
        finally:
            if not operation.end_time:
                operation.end_time = mono_time()
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
        return operation

    def _execute_count_query(self, connection: Any, sql: str) -> int:
        cursor = connection.execute(sql)
        result = cursor.fetchone()
        return result[0] if result else 0

    def _run_integrity_check(
        self,
        connection: Any,
        *,
        sql: str,
        exception_message: str,
        violation_message: str,
        success_message: str | None,
        critical_violation: bool,
    ) -> bool:
        try:
            count = self._execute_count_query(connection, sql)
        except Exception as e:
            if self.verbose:
                self.logger.warning(f"{exception_message}: {e}")
            return False

        if count > 0:
            if self.verbose:
                log_fn = self.logger.error if critical_violation else self.logger.info
                log_fn(violation_message.format(count=count))
            return critical_violation

        if success_message and self.verbose:
            self.logger.info(success_message)
        return False

    def validate_data_integrity(self) -> bool:
        orphaned_lineitems_sql = """
            SELECT COUNT(*) as orphan_count
            FROM LINEITEM l
            WHERE NOT EXISTS (
                SELECT 1 FROM ORDERS o WHERE o.O_ORDERKEY = l.L_ORDERKEY
            )
        """
        date_violations_sql = """
            SELECT COUNT(*) as violation_count
            FROM LINEITEM
            WHERE L_SHIPDATE > L_COMMITDATE
               OR L_COMMITDATE > L_RECEIPTDATE
               OR L_SHIPDATE > L_RECEIPTDATE
        """
        orderless_lineitems_sql = """
            SELECT COUNT(*) as orderless_count
            FROM ORDERS o
            WHERE NOT EXISTS (
                SELECT 1 FROM LINEITEM l WHERE l.L_ORDERKEY = o.O_ORDERKEY
            )
        """
        checks = [
            {
                "sql": orphaned_lineitems_sql,
                "exception_message": "Could not check orphaned lineitems",
                "violation_message": "Integrity violation: {count} orphaned LINEITEM records found",
                "success_message": "✓ No orphaned LINEITEM records",
                "critical_violation": True,
            },
            {
                "sql": date_violations_sql,
                "exception_message": "Could not check date constraints",
                "violation_message": "Integrity violation: {count} LINEITEM date constraint violations",
                "success_message": "✓ All LINEITEM date constraints valid",
                "critical_violation": True,
            },
            {
                "sql": orderless_lineitems_sql,
                "exception_message": "Could not check orders without lineitems",
                "violation_message": "Note: {count} ORDERS without LINEITEM records (may be expected)",
                "success_message": None,
                "critical_violation": False,
            },
        ]

        try:
            connection = self.connection_factory()
            violations_found = False

            if self.verbose:
                self.logger.info("Starting TPC-H data integrity validation")

            for check in checks:
                has_critical_violation = self._run_integrity_check(
                    connection,
                    sql=check["sql"],
                    exception_message=check["exception_message"],
                    violation_message=check["violation_message"],
                    success_message=check["success_message"],
                    critical_violation=check["critical_violation"],
                )
                violations_found = violations_found or has_critical_violation

            connection.close()

            if violations_found:
                if self.verbose:
                    self.logger.error("Data integrity validation FAILED - violations found")
                return False
            else:
                if self.verbose:
                    self.logger.info("Data integrity validation PASSED")
                return True

        except Exception as e:
            if self.verbose:
                self.logger.error(f"Data integrity validation failed with exception: {e}")
            return False
