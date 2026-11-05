import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from benchbox.core.tpcds.maintenance_operations import (
    DEFAULT_MAINTENANCE_SEED,
    MaintenanceOperations,
    MaintenanceOperationType,
)
from benchbox.utils.clock import elapsed_seconds, mono_time

_MAINTENANCE_OP_MAP: dict[tuple[str, str], MaintenanceOperationType] = {
    ("INSERT", "STORE_SALES"): MaintenanceOperationType.INSERT_STORE_SALES,
    ("INSERT", "CATALOG_SALES"): MaintenanceOperationType.INSERT_CATALOG_SALES,
    ("INSERT", "WEB_SALES"): MaintenanceOperationType.INSERT_WEB_SALES,
    ("INSERT", "STORE_RETURNS"): MaintenanceOperationType.INSERT_STORE_RETURNS,
    ("INSERT", "CATALOG_RETURNS"): MaintenanceOperationType.INSERT_CATALOG_RETURNS,
    ("INSERT", "WEB_RETURNS"): MaintenanceOperationType.INSERT_WEB_RETURNS,
    ("UPDATE", "CUSTOMER"): MaintenanceOperationType.UPDATE_CUSTOMER,
    ("UPDATE", "ITEM"): MaintenanceOperationType.UPDATE_ITEM,
    ("UPDATE", "INVENTORY"): MaintenanceOperationType.UPDATE_INVENTORY,
    ("DELETE", "SALES"): MaintenanceOperationType.DELETE_OLD_SALES,
    ("DELETE", "RETURNS"): MaintenanceOperationType.DELETE_OLD_RETURNS,
}


@dataclass
class TPCDSMaintenanceTestConfig:
    scale_factor: float = 1.0
    maintenance_operations: int = 4
    operation_interval: float = 60.0
    concurrent_with_queries: bool = True
    validate_integrity: bool = True
    verbose: bool = False
    output_dir: Optional[Path] = None
    seed: int = DEFAULT_MAINTENANCE_SEED


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


@dataclass
class TPCDSMaintenanceTestResult:
    config: TPCDSMaintenanceTestConfig
    start_time: str
    end_time: str
    total_time: float
    insert_operations: int
    update_operations: int
    delete_operations: int
    total_operations: int
    successful_operations: int
    failed_operations: int
    operations: list[TPCDSMaintenanceOperation] = field(default_factory=list)
    overall_throughput: float = 0.0
    success: bool = True
    errors: list[str] = field(default_factory=list)


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
        self.captured_items: list[tuple[str, str]] = []

        self.maintenance_ops = MaintenanceOperations()

    def run(self, config: Optional[TPCDSMaintenanceTestConfig] = None) -> dict[str, Any]:
        if config is None:
            config = TPCDSMaintenanceTestConfig(
                scale_factor=self.scale_factor,
                verbose=self.verbose,
                output_dir=self.output_dir,
            )

        start_time = mono_time()
        start_time_str = datetime.now().isoformat()

        result: dict[str, Any] = {
            "config": config,
            "seed": config.seed,
            "start_time": start_time_str,
            "end_time": "",
            "total_time": 0.0,
            "insert_operations": 0,
            "update_operations": 0,
            "delete_operations": 0,
            "total_operations": 0,
            "successful_operations": 0,
            "failed_operations": 0,
            "operations": [],
            "overall_throughput": 0.0,
            "success": True,
            "errors": [],
        }

        try:
            if config.verbose:
                self.logger.info("Starting TPC-DS Maintenance Test")
                self.logger.info(f"Maintenance operations: {config.maintenance_operations}")
                self.logger.info(f"Scale factor: {config.scale_factor}")

            self.maintenance_ops.set_seed(config.seed)
            self._run_maintenance_loop(config, result)
            self._finalize_maintenance_result(config, result, start_time)

            return result

        except Exception as e:
            result["total_time"] = elapsed_seconds(start_time)
            result["end_time"] = datetime.now().isoformat()
            result["success"] = False
            result["errors"].append(f"Maintenance Test execution failed: {e}")

            if self.verbose:
                self.logger.error(f"Maintenance Test failed: {e}")

            return result

    def _run_maintenance_loop(self, config: TPCDSMaintenanceTestConfig, result: dict[str, Any]) -> None:
        _OP_TYPE_KEYS = {"INSERT": "insert_operations", "UPDATE": "update_operations", "DELETE": "delete_operations"}
        maintenance_tables = [
            "catalog_sales",
            "catalog_returns",
            "web_sales",
            "web_returns",
            "store_sales",
            "store_returns",
            "inventory",
        ]

        for operation_id in range(config.maintenance_operations):
            if config.verbose:
                self.logger.info(f"Executing maintenance operation {operation_id + 1}")

            operation_type = ["INSERT", "UPDATE", "DELETE"][operation_id % 3]
            table_name = maintenance_tables[operation_id % len(maintenance_tables)]

            operation_result = self._execute_maintenance_operation(operation_type, table_name, operation_id)

            result["operations"].append(operation_result)
            result["total_operations"] += 1
            result[_OP_TYPE_KEYS[operation_type]] += 1

            if operation_result.success:
                result["successful_operations"] += 1
            else:
                result["failed_operations"] += 1
                result["errors"].append(
                    f"{operation_type} operation {operation_id + 1} on {table_name} failed: {operation_result.error}"
                )

            if config.operation_interval > 0 and operation_id < config.maintenance_operations - 1:
                time.sleep(config.operation_interval)

    def _finalize_maintenance_result(
        self, config: TPCDSMaintenanceTestConfig, result: dict[str, Any], start_time: float
    ) -> None:
        total_time = elapsed_seconds(start_time)
        result["total_time"] = total_time
        result["end_time"] = datetime.now().isoformat()

        if total_time > 0:
            result["overall_throughput"] = result["successful_operations"] / total_time

        if result["total_operations"] > 0:
            success_rate = result["successful_operations"] / result["total_operations"]
            result["success"] = success_rate >= 0.9
        else:
            success_rate = 0.0
            result["success"] = False

        if config.verbose:
            self.logger.info(f"Maintenance Test completed in {total_time:.3f}s")
            self.logger.info(f"Successful operations: {result['successful_operations']}/{result['total_operations']}")
            self.logger.info(f"Success rate: {success_rate:.2%}")
            self.logger.info(f"Overall throughput: {result['overall_throughput']:.2f} ops/sec")

    def _map_to_maintenance_operation_type(self, operation_type: str, table_name: str) -> MaintenanceOperationType:
        op_upper = operation_type.upper()
        table_upper = table_name.upper()
        for (op_key, table_substr), op_type in _MAINTENANCE_OP_MAP.items():
            if op_upper == op_key and table_substr in table_upper:
                return op_type
        return MaintenanceOperationType.BULK_LOAD_SALES

    def _execute_maintenance_operation(
        self, operation_type: str, table_name: str, operation_id: int
    ) -> TPCDSMaintenanceOperation:
        operation = TPCDSMaintenanceOperation(
            operation_type=operation_type,
            table_name=table_name,
            start_time=0.0,
            end_time=0.0,
            duration=0.0,
            rows_affected=0,
            success=False,
        )

        connection = None
        try:
            if self.verbose:
                self.logger.info(f"Executing {operation_type} operation on {table_name}")

            connection = self.connection_factory()
            self.maintenance_ops.initialize(connection, self.benchmark, None)
            maint_op_type = self._map_to_maintenance_operation_type(operation_type, table_name)
            estimated_rows = 10

            start_time = mono_time()
            operation.start_time = start_time
            try:
                result = self.maintenance_ops.execute_operation(connection, maint_op_type, estimated_rows)
            finally:
                end_t = mono_time()
                operation.end_time = end_t
                operation.duration = elapsed_seconds(start_time, end_t)

            operation.rows_affected = result.rows_affected
            operation.success = result.success

            if result.error_message:
                operation.error = result.error_message

            if self.verbose:
                self.logger.info(
                    f"{operation_type} operation on {table_name} completed: {result.rows_affected} rows affected"
                )

        except Exception as e:
            operation.error = str(e)
            if self.verbose:
                self.logger.error(f"{operation_type} operation on {table_name} failed: {e}")

        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception as e:
                    if operation.error is None:
                        operation.error = f"Connection close failed: {e}"
                        operation.success = False

        return operation

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
            else:
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
            elif self.verbose:
                self.logger.info(f"✓ No orphaned {returns_table} records")
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

    def get_maintenance_statistics(self) -> dict[str, Any]:
        return {
            "scale_factor": self.scale_factor,
            "estimated_daily_inserts": int(self.scale_factor * 10000),
            "estimated_daily_updates": int(self.scale_factor * 5000),
            "estimated_daily_deletes": int(self.scale_factor * 2000),
            "maintenance_tables": [
                "catalog_sales",
                "catalog_returns",
                "web_sales",
                "web_returns",
                "store_sales",
                "store_returns",
                "inventory",
                "customer",
                "customer_address",
            ],
        }

    def get_maintenance_operations_sql(
        self,
        placeholder: str = "?",
    ) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}

        insert_rows = max(10, int(self.scale_factor * 100))
        update_rows = max(5, int(self.scale_factor * 50))
        delete_rows = max(2, int(self.scale_factor * 20))

        self._init_maintenance_ops_for_dryrun()

        result["DM1_INSERT"] = self._generate_insert_sql_via_maintenance_ops(placeholder, insert_rows)

        result["DM2_INSERT_RETURNS"] = self._generate_returns_insert_sql_via_maintenance_ops(
            placeholder, insert_rows // 10
        )

        result["DM3_UPDATE"] = self._generate_update_sql_via_maintenance_ops(placeholder, update_rows)

        result["DM4_DELETE"] = self._generate_delete_sql_via_maintenance_ops(placeholder, delete_rows)

        return result

    def _init_maintenance_ops_for_dryrun(self) -> None:
        sf = self.scale_factor

        self.maintenance_ops.dimension_ranges = {
            "date_dim": (2450815, 2453005),
            "time_dim": (0, 86399),
            "item": (1, max(1, int(18000 * sf))),
            "customer": (1, max(1, int(100000 * sf))),
            "customer_demographics": (1, 1920800),
            "household_demographics": (1, 7200),
            "customer_address": (1, max(1, int(50000 * sf))),
            "store": (1, max(1, int(12 * sf))),
            "promotion": (1, max(1, int(300 * sf))),
            "call_center": (1, max(1, int(6 * sf))),
            "catalog_page": (1, max(1, int(11718 * sf))),
            "ship_mode": (1, 20),
            "warehouse": (1, max(1, int(5 * sf))),
            "web_site": (1, max(1, int(30 * sf))),
            "web_page": (1, max(1, int(60 * sf))),
        }

        self.maintenance_ops.random_gen.seed(42)

    def _generate_insert_sql_via_maintenance_ops(self, placeholder: str, num_rows: int) -> list[str]:
        statements = []
        batch_size = min(100, num_rows)

        store_sales_rows = [self.maintenance_ops._generate_store_sales_row() for _ in range(batch_size)]
        columns = """SS_SOLD_DATE_SK, SS_SOLD_TIME_SK, SS_ITEM_SK, SS_CUSTOMER_SK,
            SS_CDEMO_SK, SS_HDEMO_SK, SS_ADDR_SK, SS_STORE_SK, SS_PROMO_SK,
            SS_TICKET_NUMBER, SS_QUANTITY, SS_WHOLESALE_COST, SS_LIST_PRICE,
            SS_SALES_PRICE, SS_EXT_DISCOUNT_AMT, SS_EXT_SALES_PRICE,
            SS_EXT_WHOLESALE_COST, SS_EXT_LIST_PRICE, SS_EXT_TAX,
            SS_COUPON_AMT, SS_NET_PAID, SS_NET_PAID_INC_TAX, SS_NET_PROFIT"""

        row_placeholders = ", ".join([placeholder] * 23)
        values_placeholders = ", ".join([f"({row_placeholders})" for _ in store_sales_rows])
        sample_row = store_sales_rows[0] if store_sales_rows else ()

        statements.append(
            f"-- TPC-DS Maintenance: Insert {num_rows} new store sales records\n"
            f"-- Sample row values: {sample_row[:5]}...\n"
            f"INSERT INTO STORE_SALES ({columns}) VALUES {values_placeholders}"
        )

        catalog_sales_rows = [self.maintenance_ops._generate_catalog_sales_row() for _ in range(batch_size)]
        columns = """CS_SOLD_DATE_SK, CS_SOLD_TIME_SK, CS_SHIP_DATE_SK, CS_BILL_CUSTOMER_SK,
            CS_BILL_CDEMO_SK, CS_BILL_HDEMO_SK, CS_BILL_ADDR_SK, CS_SHIP_CUSTOMER_SK,
            CS_SHIP_CDEMO_SK, CS_SHIP_HDEMO_SK, CS_SHIP_ADDR_SK, CS_CALL_CENTER_SK,
            CS_CATALOG_PAGE_SK, CS_SHIP_MODE_SK, CS_WAREHOUSE_SK, CS_ITEM_SK,
            CS_PROMO_SK, CS_ORDER_NUMBER, CS_QUANTITY, CS_WHOLESALE_COST,
            CS_LIST_PRICE, CS_SALES_PRICE, CS_EXT_DISCOUNT_AMT, CS_EXT_SALES_PRICE,
            CS_EXT_WHOLESALE_COST, CS_EXT_LIST_PRICE, CS_EXT_TAX, CS_COUPON_AMT,
            CS_EXT_SHIP_COST, CS_NET_PAID, CS_NET_PAID_INC_TAX, CS_NET_PAID_INC_SHIP,
            CS_NET_PAID_INC_SHIP_TAX, CS_NET_PROFIT"""

        row_placeholders = ", ".join([placeholder] * 34)
        values_placeholders = ", ".join([f"({row_placeholders})" for _ in catalog_sales_rows])
        sample_row = catalog_sales_rows[0] if catalog_sales_rows else ()

        statements.append(
            f"-- TPC-DS Maintenance: Insert {num_rows} new catalog sales records\n"
            f"-- Sample row values: {sample_row[:5]}...\n"
            f"INSERT INTO CATALOG_SALES ({columns}) VALUES {values_placeholders}"
        )

        web_sales_rows = [self.maintenance_ops._generate_web_sales_row() for _ in range(batch_size)]
        columns = """WS_SOLD_DATE_SK, WS_SOLD_TIME_SK, WS_SHIP_DATE_SK, WS_ITEM_SK,
            WS_BILL_CUSTOMER_SK, WS_BILL_CDEMO_SK, WS_BILL_HDEMO_SK, WS_BILL_ADDR_SK,
            WS_SHIP_CUSTOMER_SK, WS_SHIP_CDEMO_SK, WS_SHIP_HDEMO_SK, WS_SHIP_ADDR_SK,
            WS_WEB_PAGE_SK, WS_WEB_SITE_SK, WS_SHIP_MODE_SK, WS_WAREHOUSE_SK,
            WS_PROMO_SK, WS_ORDER_NUMBER, WS_QUANTITY, WS_WHOLESALE_COST,
            WS_LIST_PRICE, WS_SALES_PRICE, WS_EXT_DISCOUNT_AMT, WS_EXT_SALES_PRICE,
            WS_EXT_WHOLESALE_COST, WS_EXT_LIST_PRICE, WS_EXT_TAX, WS_COUPON_AMT,
            WS_EXT_SHIP_COST, WS_NET_PAID, WS_NET_PAID_INC_TAX, WS_NET_PAID_INC_SHIP,
            WS_NET_PAID_INC_SHIP_TAX, WS_NET_PROFIT"""

        row_placeholders = ", ".join([placeholder] * 34)
        values_placeholders = ", ".join([f"({row_placeholders})" for _ in web_sales_rows])
        sample_row = web_sales_rows[0] if web_sales_rows else ()

        statements.append(
            f"-- TPC-DS Maintenance: Insert {num_rows} new web sales records\n"
            f"-- Sample row values: {sample_row[:5]}...\n"
            f"INSERT INTO WEB_SALES ({columns}) VALUES {values_placeholders}"
        )

        return statements

    def _generate_returns_insert_sql_via_maintenance_ops(self, placeholder: str, num_rows: int) -> list[str]:
        statements = []

        columns = """SR_RETURNED_DATE_SK, SR_RETURN_TIME_SK, SR_ITEM_SK, SR_CUSTOMER_SK,
            SR_CDEMO_SK, SR_HDEMO_SK, SR_ADDR_SK, SR_STORE_SK, SR_REASON_SK,
            SR_TICKET_NUMBER, SR_RETURN_QUANTITY, SR_RETURN_AMT, SR_RETURN_TAX,
            SR_RETURN_AMT_INC_TAX, SR_FEE, SR_RETURN_SHIP_COST, SR_REFUNDED_CASH,
            SR_REVERSED_CHARGE, SR_STORE_CREDIT, SR_NET_LOSS"""

        row_placeholders = ", ".join([placeholder] * 20)
        statements.append(
            f"-- TPC-DS Maintenance: Insert {num_rows} store returns\n"
            f"-- Step 1: Query STORE_SALES to get valid (ticket_number, item_sk) pairs\n"
            f"-- Step 2: Generate returns referencing those sales\n"
            f"INSERT INTO STORE_RETURNS ({columns}) VALUES ({row_placeholders})"
        )

        columns = """CR_RETURNED_DATE_SK, CR_RETURNED_TIME_SK, CR_ITEM_SK, CR_REFUNDED_CUSTOMER_SK,
            CR_REFUNDED_CDEMO_SK, CR_REFUNDED_HDEMO_SK, CR_REFUNDED_ADDR_SK,
            CR_RETURNING_CUSTOMER_SK, CR_RETURNING_CDEMO_SK, CR_RETURNING_HDEMO_SK,
            CR_RETURNING_ADDR_SK, CR_CALL_CENTER_SK, CR_CATALOG_PAGE_SK, CR_SHIP_MODE_SK,
            CR_WAREHOUSE_SK, CR_REASON_SK, CR_ORDER_NUMBER, CR_RETURN_QUANTITY,
            CR_RETURN_AMOUNT, CR_RETURN_TAX, CR_RETURN_AMT_INC_TAX, CR_FEE,
            CR_RETURN_SHIP_COST, CR_REFUNDED_CASH, CR_REVERSED_CHARGE, CR_STORE_CREDIT,
            CR_NET_LOSS"""

        row_placeholders = ", ".join([placeholder] * 27)
        statements.append(
            f"-- TPC-DS Maintenance: Insert {num_rows} catalog returns\n"
            f"-- Step 1: Query CATALOG_SALES to get valid (order_number, item_sk) pairs\n"
            f"-- Step 2: Generate returns referencing those sales\n"
            f"INSERT INTO CATALOG_RETURNS ({columns}) VALUES ({row_placeholders})"
        )

        columns = """WR_RETURNED_DATE_SK, WR_RETURNED_TIME_SK, WR_ITEM_SK, WR_REFUNDED_CUSTOMER_SK,
            WR_REFUNDED_CDEMO_SK, WR_REFUNDED_HDEMO_SK, WR_REFUNDED_ADDR_SK,
            WR_RETURNING_CUSTOMER_SK, WR_RETURNING_CDEMO_SK, WR_RETURNING_HDEMO_SK,
            WR_RETURNING_ADDR_SK, WR_WEB_PAGE_SK, WR_REASON_SK, WR_ORDER_NUMBER,
            WR_RETURN_QUANTITY, WR_RETURN_AMT, WR_RETURN_TAX, WR_RETURN_AMT_INC_TAX,
            WR_FEE, WR_RETURN_SHIP_COST, WR_REFUNDED_CASH, WR_REVERSED_CHARGE,
            WR_ACCOUNT_CREDIT, WR_NET_LOSS"""

        row_placeholders = ", ".join([placeholder] * 24)
        statements.append(
            f"-- TPC-DS Maintenance: Insert {num_rows} web returns\n"
            f"-- Step 1: Query WEB_SALES to get valid (order_number, item_sk) pairs\n"
            f"-- Step 2: Generate returns referencing those sales\n"
            f"INSERT INTO WEB_RETURNS ({columns}) VALUES ({row_placeholders})"
        )

        return statements

    def _generate_update_sql_via_maintenance_ops(self, placeholder: str, num_rows: int) -> list[str]:
        statements = []

        statements.append(
            f"-- TPC-DS Maintenance: Update {num_rows} customer records\n"
            f"-- Same logic as MaintenanceOperations._update_customer()\n"
            f"UPDATE CUSTOMER SET C_CURRENT_ADDR_SK = {placeholder} WHERE C_CUSTOMER_SK = {placeholder}"
        )

        statements.append(
            f"-- TPC-DS Maintenance: Update {num_rows} item prices\n"
            f"-- Same logic as MaintenanceOperations._update_item()\n"
            f"UPDATE ITEM SET I_CURRENT_PRICE = {placeholder} WHERE I_ITEM_SK = {placeholder}"
        )

        statements.append(
            f"-- TPC-DS Maintenance: Update {num_rows} inventory levels\n"
            f"-- Same logic as MaintenanceOperations._update_inventory()\n"
            f"UPDATE INVENTORY SET INV_QUANTITY_ON_HAND = {placeholder} "
            f"WHERE INV_DATE_SK = {placeholder} AND INV_ITEM_SK = {placeholder} AND INV_WAREHOUSE_SK = {placeholder}"
        )

        return statements

    def _generate_delete_sql_via_maintenance_ops(self, placeholder: str, num_rows: int) -> list[str]:
        statements = []

        cutoff_date_sk = 2450815

        statements.append(
            f"-- TPC-DS Maintenance: Delete {num_rows} old store sales records\n"
            f"-- Same logic as MaintenanceOperations._delete_old_sales()\n"
            f"DELETE FROM STORE_SALES WHERE rowid IN (SELECT rowid FROM STORE_SALES "
            f"WHERE SS_SOLD_DATE_SK < {cutoff_date_sk} LIMIT {placeholder})"
        )

        statements.append(
            f"-- TPC-DS Maintenance: Delete {num_rows} old catalog sales records\n"
            f"DELETE FROM CATALOG_SALES WHERE rowid IN (SELECT rowid FROM CATALOG_SALES "
            f"WHERE CS_SOLD_DATE_SK < {cutoff_date_sk} LIMIT {placeholder})"
        )

        statements.append(
            f"-- TPC-DS Maintenance: Delete {num_rows} old web sales records\n"
            f"DELETE FROM WEB_SALES WHERE rowid IN (SELECT rowid FROM WEB_SALES "
            f"WHERE WS_SOLD_DATE_SK < {cutoff_date_sk} LIMIT {placeholder})"
        )

        statements.append(
            f"-- TPC-DS Maintenance: Delete {num_rows // 2} old store returns\n"
            f"-- Same logic as MaintenanceOperations._delete_old_returns()\n"
            f"DELETE FROM STORE_RETURNS WHERE rowid IN (SELECT rowid FROM STORE_RETURNS "
            f"WHERE SR_RETURNED_DATE_SK < {cutoff_date_sk} LIMIT {placeholder})"
        )

        return statements
