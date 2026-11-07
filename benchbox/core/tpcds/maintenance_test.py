import logging
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from benchbox.core.contracts.platform_capabilities import resolve_primary_key_probe
from benchbox.core.results.maintenance_method import MAINTENANCE_METHOD
from benchbox.core.tpcds.maintenance_functions import MAINTENANCE_FUNCTIONS, MaintenanceFunction
from benchbox.core.tpcds.refresh_data import (
    SOURCE_TABLES_BY_NAME,
    RefreshDataError,
    RefreshSet,
    create_staging_tables,
    drop_staging_tables,
    generate_refresh_sets,
    load_refresh_file,
)
from benchbox.utils.clock import elapsed_seconds, mono_time


@dataclass
class TPCDSMaintenanceTestConfig:
    scale_factor: float = 1.0
    refresh_runs: int = 1
    first_refresh_set: int = 1
    validate_integrity: bool = True
    verbose: bool = False
    output_dir: Optional[Path] = None
    seed: Optional[int] = None


@dataclass
class TPCDSMaintenanceOperation:
    operation_type: str
    table_name: str
    start_time: float
    end_time: float
    duration: float
    rows_affected: int
    success: bool
    error: Optional[str] = None
    function: str = ""
    function_id: int = 0
    method: int = 0
    refresh_set: int = 0
    staged_rows: dict[str, int] = field(default_factory=dict)
    rows_by_table: dict[str, int] = field(default_factory=dict)
    statements_executed: int = 0


@dataclass
class TPCDSMaintenanceTestResult:
    config: TPCDSMaintenanceTestConfig
    start_time: str
    end_time: str
    total_time: float
    insert_operations: int
    delete_operations: int
    total_operations: int
    successful_operations: int
    failed_operations: int
    operations: list[TPCDSMaintenanceOperation] = field(default_factory=list)
    overall_throughput: float = 0.0
    success: bool = True
    errors: list[str] = field(default_factory=list)
    maintenance_method: str = MAINTENANCE_METHOD


class MaintenanceKeyConflictError(RuntimeError):
    pass


def maintenance_unsupported_reason(adapter: Any) -> Optional[str]:
    if getattr(adapter, "supports_refresh_functions", False) is True:
        return None
    platform_name = getattr(adapter, "platform_name", type(adapter).__name__)
    return f"TPC-DS maintenance functions are not supported by platform '{platform_name}'"


class TPCDSMaintenanceTest:
    def __init__(
        self,
        benchmark: Any,
        connection_factory: Callable[[], Any],
        scale_factor: float = 1.0,
        output_dir: Optional[Path] = None,
        verbose: bool = False,
        dialect: Optional[str] = None,
    ) -> None:
        self.benchmark = benchmark
        self.connection_factory = connection_factory
        self.scale_factor = scale_factor
        self.output_dir = output_dir or Path.cwd() / "tpcds_maintenance_test"
        self.verbose = verbose
        self.target_dialect = dialect

        self.logger = logging.getLogger(__name__)
        if verbose:
            self.logger.setLevel(logging.INFO)

    def run(self, config: Optional[TPCDSMaintenanceTestConfig] = None) -> dict[str, Any]:
        if config is None:
            config = TPCDSMaintenanceTestConfig(
                scale_factor=self.scale_factor,
                verbose=self.verbose,
                output_dir=self.output_dir,
            )

        result: dict[str, Any] = {
            "config": config,
            "maintenance_method": MAINTENANCE_METHOD,
            "seed": config.seed,
            "refresh_sets": [],
            "start_time": datetime.now().isoformat(),
            "end_time": "",
            "total_time": 0.0,
            "insert_operations": 0,
            "delete_operations": 0,
            "total_operations": 0,
            "successful_operations": 0,
            "failed_operations": 0,
            "operations": [],
            "overall_throughput": 0.0,
            "success": True,
            "errors": [],
            "integrity_valid": None,
        }

        try:
            self._require_positive_int("refresh_runs", config.refresh_runs)
            self._require_positive_int("first_refresh_set", config.first_refresh_set)
            if config.verbose:
                self.logger.info("Starting TPC-DS Maintenance Test")
                self.logger.info(f"Refresh runs: {config.refresh_runs}, scale factor: {config.scale_factor}")

            self._refuse_enforced_primary_keys()
            with tempfile.TemporaryDirectory(prefix="benchbox-tpcds-refresh-") as temporary:
                refresh_sets = generate_refresh_sets(
                    config.scale_factor,
                    config.first_refresh_set,
                    config.refresh_runs,
                    Path(temporary),
                    config.seed,
                )
                self._run_refresh_runs(result, refresh_sets)
        except Exception as exc:
            result["success"] = False
            result["errors"].append(f"Maintenance Test execution failed: {exc}")
            result["end_time"] = datetime.now().isoformat()
            if config.verbose:
                self.logger.error(f"Maintenance Test failed: {exc}")
            return result

        self._finalize(config, result)
        if config.validate_integrity and result["success"]:
            result["integrity_valid"] = self.validate_data_integrity()
        return result

    def _refuse_enforced_primary_keys(self) -> None:
        connection = self.connection_factory()
        adapter = getattr(connection, "platform_adapter", None)
        try:
            probe = resolve_primary_key_probe(adapter)
            if probe is None:
                return
            targets = sorted({table for function in MAINTENANCE_FUNCTIONS for table in function.target_tables})
            enforced = probe.enforced_primary_key_tables(connection, targets)
        finally:
            if adapter is not None:
                connection.close()
        if enforced:
            raise MaintenanceKeyConflictError(
                f"TPC-DS maintenance refused: primary keys are enforced on {', '.join(enforced)}. "
                "The dsdgen refresh sets number orders and tickets from 1 in every set, so their keys overlap the "
                "base data and an enforced primary key rejects the insert as a duplicate. "
                "Load the data without enforced primary keys (run with --tuning notuning, or with primary keys "
                "disabled in the tuning configuration) and run the maintenance test again."
            )

    @staticmethod
    def _require_positive_int(label: str, value: Any) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{label} must be an integer, got {type(value).__name__}")
        if value < 1:
            raise ValueError(f"{label} must be positive, got {value}")

    def _run_refresh_runs(
        self,
        result: dict[str, Any],
        refresh_sets: dict[int, RefreshSet],
    ) -> None:
        for set_id in sorted(refresh_sets):
            result["refresh_sets"].append(set_id)
            for function in MAINTENANCE_FUNCTIONS:
                operation = self._execute_function(function, refresh_sets[set_id])
                result["operations"].append(operation)
                result["total_operations"] += 1
                result["insert_operations" if function.operation_type == "INSERT" else "delete_operations"] += 1
                if operation.success:
                    result["successful_operations"] += 1
                    continue
                result["failed_operations"] += 1
                result["errors"].append(f"{function.name} on refresh set {set_id} failed: {operation.error}")
                return

    def _finalize(self, config: TPCDSMaintenanceTestConfig, result: dict[str, Any]) -> None:
        operations: list[TPCDSMaintenanceOperation] = result["operations"]
        started = [op.start_time for op in operations if op.start_time]
        ended = [op.end_time for op in operations if op.end_time]
        if started and ended:
            result["total_time"] = max(ended) - min(started)
        result["end_time"] = datetime.now().isoformat()
        if result["total_time"] > 0:
            result["overall_throughput"] = result["successful_operations"] / result["total_time"]
        result["success"] = result["total_operations"] > 0 and result["failed_operations"] == 0
        if config.verbose:
            self.logger.info(f"Maintenance Test completed in {result['total_time']:.3f}s")
            self.logger.info(f"Successful operations: {result['successful_operations']}/{result['total_operations']}")

    def get_maintenance_operations_sql(self) -> dict[str, list[str]]:
        operations: dict[str, list[str]] = {}
        for function in MAINTENANCE_FUNCTIONS:
            statements = [f"-- {function.name}: method {function.method}"]
            statements.extend(SOURCE_TABLES_BY_NAME[name].create_sql() for name in function.staging_tables)
            statements.extend(function.statements)
            operations[function.name] = statements
        return operations

    def _execute_function(self, function: MaintenanceFunction, refresh_set: RefreshSet) -> TPCDSMaintenanceOperation:
        operation = TPCDSMaintenanceOperation(
            operation_type=function.operation_type,
            table_name=", ".join(function.target_tables),
            start_time=0.0,
            end_time=0.0,
            duration=0.0,
            rows_affected=0,
            success=False,
            function=function.name,
            function_id=function.function_id,
            method=function.method,
            refresh_set=refresh_set.set_id,
            staged_rows={name: refresh_set.row_counts[name] for name in function.staging_tables},
        )
        staging = [SOURCE_TABLES_BY_NAME[name] for name in function.staging_tables]
        connection = None
        staging_created = False
        in_transaction = False
        try:
            if self.verbose:
                self.logger.info(f"Executing {function.name} on refresh set {refresh_set.set_id}")
            connection = self.connection_factory()
            before = {table: self._count_rows(connection, table) for table in function.target_tables}

            operation.start_time = mono_time()
            try:
                staging_created = True
                create_staging_tables(connection, staging)
                self._begin(connection)
                in_transaction = True
                try:
                    for table in staging:
                        load_refresh_file(
                            connection, table, refresh_set.path_for(table.name), refresh_set.row_counts[table.name]
                        )
                    for statement in function.statements:
                        connection.execute(statement)
                        operation.statements_executed += 1
                    self._commit(connection)
                except BaseException:
                    self._rollback(connection)
                    raise
            finally:
                operation.end_time = mono_time()
                operation.duration = elapsed_seconds(operation.start_time, operation.end_time)

            after = {table: self._count_rows(connection, table) for table in function.target_tables}
            operation.rows_by_table = {table: after[table] - before[table] for table in function.target_tables}
            operation.rows_affected = sum(abs(delta) for delta in operation.rows_by_table.values())
            self._verify_rows(function, operation)
            operation.success = True
        except Exception as exc:
            operation.error = str(exc)
            if connection is not None and not in_transaction:
                self._rollback(connection)
            if self.verbose:
                self.logger.error(f"{function.name} failed: {exc}")
        finally:
            if connection is not None:
                if staging_created:
                    try:
                        drop_staging_tables(connection, staging)
                        self._commit(connection)
                    except Exception as exc:
                        if operation.error is None:
                            operation.error = f"Staging cleanup failed: {exc}"
                            operation.success = False
                try:
                    connection.close()
                except Exception as exc:
                    if operation.error is None:
                        operation.error = f"Connection close failed: {exc}"
                        operation.success = False
        return operation

    @staticmethod
    def _verify_rows(function: MaintenanceFunction, operation: TPCDSMaintenanceOperation) -> None:
        if function.expected_rows_table is None:
            return
        expected = operation.staged_rows[function.expected_rows_table]
        inserted = sum(operation.rows_by_table.values())
        if inserted != expected:
            raise RefreshDataError(
                f"{function.name} inserted {inserted} rows; the source schema table {function.expected_rows_table} "
                f"has {expected}"
            )

    @staticmethod
    def _count_rows(connection: Any, table: str) -> int:
        row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        if row is None or row[0] is None:
            raise RefreshDataError(f"could not count rows in {table}")
        return int(row[0])

    @staticmethod
    def _begin(connection: Any) -> None:
        connection.execute("BEGIN")

    @staticmethod
    def _commit(connection: Any) -> None:
        commit = getattr(connection, "commit", None)
        if not callable(commit):
            raise TypeError("TPC-DS maintenance functions require transactional commit() support")
        commit()

    @staticmethod
    def _rollback(connection: Any) -> None:
        rollback = getattr(connection, "rollback", None)
        if callable(rollback):
            try:
                rollback()
            except Exception:
                pass

    def validate_data_integrity(self) -> bool:
        try:
            connection = self.connection_factory()
            violations_found = False

            if self.verbose:
                self.logger.info("Starting TPC-DS data integrity validation")

            violations_found |= self._check_orphaned_returns(
                connection,
                "catalog_returns",
                "catalog_sales",
                "cs_item_sk",
                "cr_item_sk",
                "cs_order_number",
                "cr_order_number",
            )
            violations_found |= self._check_orphaned_returns(
                connection, "web_returns", "web_sales", "ws_item_sk", "wr_item_sk", "ws_order_number", "wr_order_number"
            )
            violations_found |= self._check_orphaned_returns(
                connection,
                "store_returns",
                "store_sales",
                "ss_item_sk",
                "sr_item_sk",
                "ss_ticket_number",
                "sr_ticket_number",
            )
            self._check_return_date_logic(connection)

            connection.close()

            if violations_found:
                if self.verbose:
                    self.logger.error("Data integrity validation FAILED - violations found")
                return False
            if self.verbose:
                self.logger.info("Data integrity validation PASSED")
            return True

        except Exception as e:
            if self.verbose:
                self.logger.error(f"Data integrity validation failed with exception: {e}")
            return False

    def _check_orphaned_returns(
        self,
        connection: Any,
        returns_table: str,
        sales_table: str,
        sales_item_col: str,
        returns_item_col: str,
        sales_order_col: str,
        returns_order_col: str,
    ) -> bool:
        sql = f"""
            SELECT COUNT(*) as orphan_count
            FROM {returns_table} r
            WHERE NOT EXISTS (
                SELECT 1 FROM {sales_table} s
                WHERE s.{sales_item_col} = r.{returns_item_col}
                  AND s.{sales_order_col} = r.{returns_order_col}
            )
        """
        try:
            cursor = connection.execute(sql)
            result = cursor.fetchone()
            orphan_count = result[0] if result else 0

            if orphan_count > 0:
                if self.verbose:
                    self.logger.error(f"Integrity violation: {orphan_count} orphaned {returns_table} records")
                return True
            if self.verbose:
                self.logger.info(f"No orphaned {returns_table} records")
        except Exception as e:
            if self.verbose:
                self.logger.warning(f"Could not check {returns_table} integrity: {e}")
        return False

    def _check_return_date_logic(self, connection: Any) -> None:
        date_logic_sql = """
            SELECT COUNT(*) as violation_count
            FROM catalog_returns cr
            JOIN catalog_sales cs
              ON cs.cs_item_sk = cr.cr_item_sk
             AND cs.cs_order_number = cr.cr_order_number
            WHERE cr.cr_returned_date_sk < cs.cs_sold_date_sk
        """
        try:
            cursor = connection.execute(date_logic_sql)
            result = cursor.fetchone()
            date_violation_count = result[0] if result else 0

            if date_violation_count > 0 and self.verbose:
                self.logger.info(f"Note: {date_violation_count} catalog_returns with return date before sale date")
        except Exception as e:
            if self.verbose:
                self.logger.warning(f"Could not check date logic: {e}")
