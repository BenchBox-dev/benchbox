# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DS (TPC-DS) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DS specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from benchbox.utils.clock import elapsed_seconds, mono_time

DEFAULT_MAINTENANCE_SEED = 20260101


class MaintenanceOperationType(Enum):
    INSERT_STORE_SALES = "INSERT_STORE_SALES"
    INSERT_CATALOG_SALES = "INSERT_CATALOG_SALES"
    INSERT_WEB_SALES = "INSERT_WEB_SALES"

    INSERT_STORE_RETURNS = "INSERT_STORE_RETURNS"
    INSERT_CATALOG_RETURNS = "INSERT_CATALOG_RETURNS"
    INSERT_WEB_RETURNS = "INSERT_WEB_RETURNS"

    UPDATE_CUSTOMER = "UPDATE_CUSTOMER"
    UPDATE_ITEM = "UPDATE_ITEM"
    UPDATE_INVENTORY = "UPDATE_INVENTORY"

    DELETE_OLD_SALES = "DELETE_OLD_SALES"
    DELETE_OLD_RETURNS = "DELETE_OLD_RETURNS"

    BULK_LOAD_SALES = "BULK_LOAD_SALES"
    BULK_UPDATE_INVENTORY = "BULK_UPDATE_INVENTORY"


@dataclass
class MaintenanceResult:
    operation_type: MaintenanceOperationType
    success: bool
    start_time: float
    end_time: float
    duration: float
    rows_affected: int
    error_message: Optional[str] = None
    transaction_id: Optional[str] = None


class MaintenanceError(Exception):
    pass


class ForeignKeyViolationError(MaintenanceError):
    pass


class DataGenerationError(MaintenanceError):
    pass


class ConnectionError(MaintenanceError):
    pass


class MaintenanceOperations:
    def __init__(self) -> None:
        self.logger = logging.getLogger(__name__)
        self.connection = None
        self.benchmark_instance = None
        self.config = None
        self.seed = DEFAULT_MAINTENANCE_SEED
        self.random_gen = random.Random(self.seed)
        self._transaction_counter = 0

        self.dimension_ranges = {}

        self.operation_handlers = {
            MaintenanceOperationType.INSERT_STORE_SALES: self._insert_store_sales,
            MaintenanceOperationType.INSERT_CATALOG_SALES: self._insert_catalog_sales,
            MaintenanceOperationType.INSERT_WEB_SALES: self._insert_web_sales,
            MaintenanceOperationType.INSERT_STORE_RETURNS: self._insert_store_returns,
            MaintenanceOperationType.INSERT_CATALOG_RETURNS: self._insert_catalog_returns,
            MaintenanceOperationType.INSERT_WEB_RETURNS: self._insert_web_returns,
            MaintenanceOperationType.UPDATE_CUSTOMER: self._update_customer,
            MaintenanceOperationType.UPDATE_ITEM: self._update_item,
            MaintenanceOperationType.UPDATE_INVENTORY: self._update_inventory,
            MaintenanceOperationType.DELETE_OLD_SALES: self._delete_old_sales,
            MaintenanceOperationType.DELETE_OLD_RETURNS: self._delete_old_returns,
            MaintenanceOperationType.BULK_LOAD_SALES: self._bulk_load_sales,
            MaintenanceOperationType.BULK_UPDATE_INVENTORY: self._bulk_update_inventory,
        }

    def set_seed(self, seed: int) -> None:
        self.seed = seed
        self.random_gen.seed(seed)
        self._transaction_counter = 0

    def initialize(self, connection: Any, benchmark_instance: Any, config: Any) -> None:
        self.connection = connection
        self.benchmark_instance = benchmark_instance
        self.config = config

        self._initialize_dimension_ranges(connection)

    def _initialize_dimension_ranges(self, connection: Any) -> None:
        dimension_queries = {
            "date_dim": "SELECT MIN(D_DATE_SK), MAX(D_DATE_SK) FROM DATE_DIM",
            "time_dim": "SELECT MIN(T_TIME_SK), MAX(T_TIME_SK) FROM TIME_DIM",
            "item": "SELECT MIN(I_ITEM_SK), MAX(I_ITEM_SK) FROM ITEM",
            "customer": "SELECT MIN(C_CUSTOMER_SK), MAX(C_CUSTOMER_SK) FROM CUSTOMER",
            "customer_demographics": "SELECT MIN(CD_DEMO_SK), MAX(CD_DEMO_SK) FROM CUSTOMER_DEMOGRAPHICS",
            "household_demographics": "SELECT MIN(HD_DEMO_SK), MAX(HD_DEMO_SK) FROM HOUSEHOLD_DEMOGRAPHICS",
            "customer_address": "SELECT MIN(CA_ADDRESS_SK), MAX(CA_ADDRESS_SK) FROM CUSTOMER_ADDRESS",
            "store": "SELECT MIN(S_STORE_SK), MAX(S_STORE_SK) FROM STORE",
            "promotion": "SELECT MIN(P_PROMO_SK), MAX(P_PROMO_SK) FROM PROMOTION",
            "call_center": "SELECT MIN(CC_CALL_CENTER_SK), MAX(CC_CALL_CENTER_SK) FROM CALL_CENTER",
            "catalog_page": "SELECT MIN(CP_CATALOG_PAGE_SK), MAX(CP_CATALOG_PAGE_SK) FROM CATALOG_PAGE",
            "ship_mode": "SELECT MIN(SM_SHIP_MODE_SK), MAX(SM_SHIP_MODE_SK) FROM SHIP_MODE",
            "warehouse": "SELECT MIN(W_WAREHOUSE_SK), MAX(W_WAREHOUSE_SK) FROM WAREHOUSE",
            "web_site": "SELECT MIN(WEB_SITE_SK), MAX(WEB_SITE_SK) FROM WEB_SITE",
            "web_page": "SELECT MIN(WP_WEB_PAGE_SK), MAX(WP_WEB_PAGE_SK) FROM WEB_PAGE",
        }

        for dim_name, query in dimension_queries.items():
            try:
                cursor = connection.execute(query)
                result = cursor.fetchone()
                if result and result[0] is not None and result[1] is not None:
                    self.dimension_ranges[dim_name] = (int(result[0]), int(result[1]))
                    self.logger.info(f"Dimension {dim_name}: range {self.dimension_ranges[dim_name]}")
                else:
                    self.logger.warning(f"Dimension {dim_name} is empty, using default range")
                    self.dimension_ranges[dim_name] = (1, 1)
            except Exception as e:
                self.logger.error(f"Failed to query dimension {dim_name}: {e}")
                self.dimension_ranges[dim_name] = (1, 1)

    def _get_random_key(self, dimension: str, allow_null: bool = False, null_probability: float = 0.0) -> Optional[int]:
        if allow_null and self.random_gen.random() < null_probability:
            return None

        if dimension not in self.dimension_ranges:
            self.logger.warning(f"Dimension {dimension} not initialized, returning 1")
            return 1

        min_key, max_key = self.dimension_ranges[dimension]
        return self.random_gen.randint(min_key, max_key)

    def _get_parameter_placeholder(self, connection: Any) -> str:
        connection_type = type(connection).__name__.lower()

        if "sqlite" in connection_type or "duckdb" in connection_type:
            return "?"
        elif "psycopg" in connection_type or "postgres" in connection_type or "mysql" in connection_type:
            return "%s"
        else:
            return "?"

    def _execute_batched_insert(
        self, connection: Any, table_name: str, columns: str, rows_to_insert: list[tuple], num_columns: int
    ) -> int:
        if not rows_to_insert:
            return 0

        placeholder = self._get_parameter_placeholder(connection)
        batch_size = 100
        max_retries = 3

        total_inserted = 0

        for batch_start in range(0, len(rows_to_insert), batch_size):
            batch_rows = rows_to_insert[batch_start : batch_start + batch_size]

            row_placeholders = ", ".join([placeholder] * num_columns)
            values_placeholders = ", ".join([f"({row_placeholders})" for _ in batch_rows])

            params = []
            for row in batch_rows:
                params.extend(row)

            insert_sql = f"INSERT INTO {table_name} ({columns}) VALUES {values_placeholders}"

            retry_count = 0
            while retry_count <= max_retries:
                try:
                    connection.execute(insert_sql, tuple(params))
                    total_inserted += len(batch_rows)
                    break
                except Exception as e:
                    error_msg = str(e).lower()
                    is_fk_error = any(
                        keyword in error_msg
                        for keyword in [
                            "foreign key",
                            "constraint",
                            "violates",
                            "fk_",
                            "integrity constraint",
                        ]
                    )

                    if is_fk_error and retry_count < max_retries:
                        retry_count += 1
                        self.logger.warning(
                            f"FK violation on {table_name} batch {batch_start}, retry {retry_count}/{max_retries}: {e}"
                        )
                        self._initialize_dimension_ranges(connection)
                        continue
                    else:
                        self.logger.error(f"Insert failed on {table_name} batch {batch_start}: {e}")
                        raise

        return total_inserted

    def _next_transaction_id(self) -> str:
        self._transaction_counter += 1
        return f"txn_{self.seed}_{self._transaction_counter}"

    def execute_operation(
        self,
        connection: Any,
        operation_type: MaintenanceOperationType,
        estimated_rows: int,
    ) -> MaintenanceResult:
        start_time = mono_time()

        try:
            if operation_type not in self.operation_handlers:
                raise MaintenanceError(f"Unsupported operation type: {operation_type}")

            if connection is None:
                raise ConnectionError("Database connection is None")

            handler = self.operation_handlers[operation_type]
            rows_affected = handler(connection, estimated_rows)

            end_time = mono_time()

            return MaintenanceResult(
                operation_type=operation_type,
                success=True,
                start_time=start_time,
                end_time=end_time,
                duration=elapsed_seconds(start_time, end_time),
                rows_affected=rows_affected,
                transaction_id=self._next_transaction_id(),
            )

        except ForeignKeyViolationError as e:
            end_time = mono_time()
            error_msg = f"Foreign key constraint violation in {operation_type}: {e}"
            self.logger.error(error_msg)

            return MaintenanceResult(
                operation_type=operation_type,
                success=False,
                start_time=start_time,
                end_time=end_time,
                duration=elapsed_seconds(start_time, end_time),
                rows_affected=0,
                error_message=error_msg,
            )

        except DataGenerationError as e:
            end_time = mono_time()
            error_msg = f"Data generation failed for {operation_type}: {e}"
            self.logger.error(error_msg)

            return MaintenanceResult(
                operation_type=operation_type,
                success=False,
                start_time=start_time,
                end_time=end_time,
                duration=elapsed_seconds(start_time, end_time),
                rows_affected=0,
                error_message=error_msg,
            )

        except ConnectionError as e:
            end_time = mono_time()
            error_msg = f"Database connection error for {operation_type}: {e}"
            self.logger.error(error_msg)

            return MaintenanceResult(
                operation_type=operation_type,
                success=False,
                start_time=start_time,
                end_time=end_time,
                duration=elapsed_seconds(start_time, end_time),
                rows_affected=0,
                error_message=error_msg,
            )

        except MaintenanceError as e:
            end_time = mono_time()
            error_msg = f"Maintenance operation {operation_type} failed: {e}"
            self.logger.error(error_msg)

            return MaintenanceResult(
                operation_type=operation_type,
                success=False,
                start_time=start_time,
                end_time=end_time,
                duration=elapsed_seconds(start_time, end_time),
                rows_affected=0,
                error_message=error_msg,
            )

        except Exception as e:
            end_time = mono_time()
            error_msg = f"Unexpected error in {operation_type}: {type(e).__name__}: {e}"
            self.logger.error(error_msg)

            return MaintenanceResult(
                operation_type=operation_type,
                success=False,
                start_time=start_time,
                end_time=end_time,
                duration=elapsed_seconds(start_time, end_time),
                rows_affected=0,
                error_message=error_msg,
            )

    def _insert_store_sales(self, connection: Any, estimated_rows: int) -> int:
        self.logger.info(f"Inserting {estimated_rows} rows into STORE_SALES")

        rows_to_insert = []
        for _ in range(estimated_rows):
            row = self._generate_store_sales_row()
            rows_to_insert.append(row)

        columns = """SS_SOLD_DATE_SK, SS_SOLD_TIME_SK, SS_ITEM_SK, SS_CUSTOMER_SK,
            SS_CDEMO_SK, SS_HDEMO_SK, SS_ADDR_SK, SS_STORE_SK, SS_PROMO_SK,
            SS_TICKET_NUMBER, SS_QUANTITY, SS_WHOLESALE_COST, SS_LIST_PRICE,
            SS_SALES_PRICE, SS_EXT_DISCOUNT_AMT, SS_EXT_SALES_PRICE,
            SS_EXT_WHOLESALE_COST, SS_EXT_LIST_PRICE, SS_EXT_TAX,
            SS_COUPON_AMT, SS_NET_PAID, SS_NET_PAID_INC_TAX, SS_NET_PROFIT"""

        return self._execute_batched_insert(connection, "STORE_SALES", columns, rows_to_insert, 23)

    def _insert_catalog_sales(self, connection: Any, estimated_rows: int) -> int:
        self.logger.info(f"Inserting {estimated_rows} rows into CATALOG_SALES")

        rows_to_insert = []
        for _ in range(estimated_rows):
            row = self._generate_catalog_sales_row()
            rows_to_insert.append(row)

        columns = """CS_SOLD_DATE_SK, CS_SOLD_TIME_SK, CS_SHIP_DATE_SK, CS_BILL_CUSTOMER_SK,
            CS_BILL_CDEMO_SK, CS_BILL_HDEMO_SK, CS_BILL_ADDR_SK, CS_SHIP_CUSTOMER_SK,
            CS_SHIP_CDEMO_SK, CS_SHIP_HDEMO_SK, CS_SHIP_ADDR_SK, CS_CALL_CENTER_SK,
            CS_CATALOG_PAGE_SK, CS_SHIP_MODE_SK, CS_WAREHOUSE_SK, CS_ITEM_SK,
            CS_PROMO_SK, CS_ORDER_NUMBER, CS_QUANTITY, CS_WHOLESALE_COST,
            CS_LIST_PRICE, CS_SALES_PRICE, CS_EXT_DISCOUNT_AMT, CS_EXT_SALES_PRICE,
            CS_EXT_WHOLESALE_COST, CS_EXT_LIST_PRICE, CS_EXT_TAX, CS_COUPON_AMT,
            CS_EXT_SHIP_COST, CS_NET_PAID, CS_NET_PAID_INC_TAX, CS_NET_PAID_INC_SHIP,
            CS_NET_PAID_INC_SHIP_TAX, CS_NET_PROFIT"""

        return self._execute_batched_insert(connection, "CATALOG_SALES", columns, rows_to_insert, 34)

    def _insert_web_sales(self, connection: Any, estimated_rows: int) -> int:
        self.logger.info(f"Inserting {estimated_rows} rows into WEB_SALES")

        rows_to_insert = []
        for _ in range(estimated_rows):
            row = self._generate_web_sales_row()
            rows_to_insert.append(row)

        columns = """WS_SOLD_DATE_SK, WS_SOLD_TIME_SK, WS_SHIP_DATE_SK, WS_ITEM_SK,
            WS_BILL_CUSTOMER_SK, WS_BILL_CDEMO_SK, WS_BILL_HDEMO_SK, WS_BILL_ADDR_SK,
            WS_SHIP_CUSTOMER_SK, WS_SHIP_CDEMO_SK, WS_SHIP_HDEMO_SK, WS_SHIP_ADDR_SK,
            WS_WEB_PAGE_SK, WS_WEB_SITE_SK, WS_SHIP_MODE_SK, WS_WAREHOUSE_SK,
            WS_PROMO_SK, WS_ORDER_NUMBER, WS_QUANTITY, WS_WHOLESALE_COST,
            WS_LIST_PRICE, WS_SALES_PRICE, WS_EXT_DISCOUNT_AMT, WS_EXT_SALES_PRICE,
            WS_EXT_WHOLESALE_COST, WS_EXT_LIST_PRICE, WS_EXT_TAX, WS_COUPON_AMT,
            WS_EXT_SHIP_COST, WS_NET_PAID, WS_NET_PAID_INC_TAX, WS_NET_PAID_INC_SHIP,
            WS_NET_PAID_INC_SHIP_TAX, WS_NET_PROFIT"""

        return self._execute_batched_insert(connection, "WEB_SALES", columns, rows_to_insert, 34)

    def _insert_returns_generic(
        self,
        connection: Any,
        estimated_rows: int,
        *,
        sales_table: str,
        returns_table: str,
        sales_select_columns: str,
        sales_key_columns: tuple[str, str],
        sales_quantity_column: str,
        returns_columns: str,
        returns_key_columns: tuple[str, str],
        num_returns_columns: int,
        row_generator: Callable[[tuple], tuple],
    ) -> int:
        self.logger.info(f"Inserting {estimated_rows} rows into {returns_table}")

        placeholder = self._get_parameter_placeholder(connection)

        sales_item, sales_number = sales_key_columns
        returns_item, returns_number = returns_key_columns
        order_offset = self.random_gen.randrange(1000003)
        query_sql = f"""
        SELECT {sales_select_columns}
        FROM {sales_table}
        WHERE {sales_quantity_column} IS NOT NULL
          AND NOT EXISTS (
            SELECT 1
            FROM {returns_table}
            WHERE {returns_table}.{returns_item} = {sales_table}.{sales_item}
              AND {returns_table}.{returns_number} = {sales_table}.{sales_number}
          )
        ORDER BY (({sales_item} * 7919 + {sales_number} * 104729 + {order_offset}) % 1000003), {sales_item}, {sales_number}
        LIMIT {placeholder}
        """

        try:
            cursor = connection.execute(query_sql, (estimated_rows,))
            sales_records = cursor.fetchall()
        except Exception as e:
            self.logger.error(f"Failed to query {sales_table.lower()} for returns: {e}")
            raise

        if not sales_records:
            self.logger.warning(f"No {sales_table.lower()} records without a return found to generate returns from")
            return 0

        if len(sales_records) < estimated_rows:
            self.logger.warning(
                f"Only {len(sales_records)} {sales_table.lower()} records without a return available "
                f"for {estimated_rows} requested {returns_table.lower()} rows"
            )

        rows_to_insert = []
        for sale in sales_records:
            row = row_generator(sale)
            rows_to_insert.append(row)

        return self._execute_batched_insert(
            connection, returns_table, returns_columns, rows_to_insert, num_returns_columns
        )

    def _insert_store_returns(self, connection: Any, estimated_rows: int) -> int:
        return self._insert_returns_generic(
            connection,
            estimated_rows,
            sales_table="STORE_SALES",
            returns_table="STORE_RETURNS",
            sales_select_columns="""SS_TICKET_NUMBER, SS_ITEM_SK, SS_CUSTOMER_SK, SS_CDEMO_SK,
               SS_HDEMO_SK, SS_ADDR_SK, SS_STORE_SK, SS_QUANTITY""",
            sales_key_columns=("SS_ITEM_SK", "SS_TICKET_NUMBER"),
            sales_quantity_column="SS_QUANTITY",
            returns_columns="""SR_RETURNED_DATE_SK, SR_RETURN_TIME_SK, SR_ITEM_SK, SR_CUSTOMER_SK,
            SR_CDEMO_SK, SR_HDEMO_SK, SR_ADDR_SK, SR_STORE_SK, SR_REASON_SK,
            SR_TICKET_NUMBER, SR_RETURN_QUANTITY, SR_RETURN_AMT, SR_RETURN_TAX,
            SR_RETURN_AMT_INC_TAX, SR_FEE, SR_RETURN_SHIP_COST, SR_REFUNDED_CASH,
            SR_REVERSED_CHARGE, SR_STORE_CREDIT, SR_NET_LOSS""",
            returns_key_columns=("SR_ITEM_SK", "SR_TICKET_NUMBER"),
            num_returns_columns=20,
            row_generator=self._generate_store_returns_from_sale,
        )

    def _insert_catalog_returns(self, connection: Any, estimated_rows: int) -> int:
        return self._insert_returns_generic(
            connection,
            estimated_rows,
            sales_table="CATALOG_SALES",
            returns_table="CATALOG_RETURNS",
            sales_select_columns="""CS_ORDER_NUMBER, CS_ITEM_SK, CS_BILL_CUSTOMER_SK, CS_BILL_CDEMO_SK,
               CS_BILL_HDEMO_SK, CS_BILL_ADDR_SK, CS_CALL_CENTER_SK, CS_CATALOG_PAGE_SK,
               CS_SHIP_MODE_SK, CS_WAREHOUSE_SK, CS_QUANTITY""",
            sales_key_columns=("CS_ITEM_SK", "CS_ORDER_NUMBER"),
            sales_quantity_column="CS_QUANTITY",
            returns_columns="""CR_RETURNED_DATE_SK, CR_RETURNED_TIME_SK, CR_ITEM_SK, CR_REFUNDED_CUSTOMER_SK,
            CR_REFUNDED_CDEMO_SK, CR_REFUNDED_HDEMO_SK, CR_REFUNDED_ADDR_SK,
            CR_RETURNING_CUSTOMER_SK, CR_RETURNING_CDEMO_SK, CR_RETURNING_HDEMO_SK,
            CR_RETURNING_ADDR_SK, CR_CALL_CENTER_SK, CR_CATALOG_PAGE_SK,
            CR_SHIP_MODE_SK, CR_WAREHOUSE_SK, CR_REASON_SK, CR_ORDER_NUMBER,
            CR_RETURN_QUANTITY, CR_RETURN_AMOUNT, CR_RETURN_TAX, CR_RETURN_AMT_INC_TAX,
            CR_FEE, CR_RETURN_SHIP_COST, CR_REFUNDED_CASH, CR_REVERSED_CHARGE,
            CR_STORE_CREDIT, CR_NET_LOSS""",
            returns_key_columns=("CR_ITEM_SK", "CR_ORDER_NUMBER"),
            num_returns_columns=27,
            row_generator=self._generate_catalog_returns_from_sale,
        )

    def _insert_web_returns(self, connection: Any, estimated_rows: int) -> int:
        return self._insert_returns_generic(
            connection,
            estimated_rows,
            sales_table="WEB_SALES",
            returns_table="WEB_RETURNS",
            sales_select_columns="""WS_ORDER_NUMBER, WS_ITEM_SK, WS_BILL_CUSTOMER_SK, WS_BILL_CDEMO_SK,
               WS_BILL_HDEMO_SK, WS_BILL_ADDR_SK, WS_WEB_PAGE_SK, WS_QUANTITY""",
            sales_key_columns=("WS_ITEM_SK", "WS_ORDER_NUMBER"),
            sales_quantity_column="WS_QUANTITY",
            returns_columns="""WR_RETURNED_DATE_SK, WR_RETURNED_TIME_SK, WR_ITEM_SK, WR_REFUNDED_CUSTOMER_SK,
            WR_REFUNDED_CDEMO_SK, WR_REFUNDED_HDEMO_SK, WR_REFUNDED_ADDR_SK,
            WR_RETURNING_CUSTOMER_SK, WR_RETURNING_CDEMO_SK, WR_RETURNING_HDEMO_SK,
            WR_RETURNING_ADDR_SK, WR_WEB_PAGE_SK, WR_REASON_SK, WR_ORDER_NUMBER,
            WR_RETURN_QUANTITY, WR_RETURN_AMT, WR_RETURN_TAX, WR_RETURN_AMT_INC_TAX,
            WR_FEE, WR_RETURN_SHIP_COST, WR_REFUNDED_CASH, WR_REVERSED_CHARGE,
            WR_ACCOUNT_CREDIT, WR_NET_LOSS""",
            returns_key_columns=("WR_ITEM_SK", "WR_ORDER_NUMBER"),
            num_returns_columns=24,
            row_generator=self._generate_web_returns_from_sale,
        )

    def _update_customer(self, connection: Any, estimated_rows: int) -> int:
        self.logger.info(f"Updating {estimated_rows} rows in CUSTOMER table")

        updates = [
            "UPDATE CUSTOMER SET C_CURRENT_ADDR_SK = ? WHERE C_CUSTOMER_SK = ?",
            "UPDATE CUSTOMER SET C_CURRENT_CDEMO_SK = ? WHERE C_CUSTOMER_SK = ?",
            "UPDATE CUSTOMER SET C_CURRENT_HDEMO_SK = ? WHERE C_CUSTOMER_SK = ?",
            "UPDATE CUSTOMER SET C_PREFERRED_CUST_FLAG = ? WHERE C_CUSTOMER_SK = ?",
            "UPDATE CUSTOMER SET C_EMAIL_ADDRESS = ? WHERE C_CUSTOMER_SK = ?",
        ]

        rows_updated = 0
        for _i in range(estimated_rows):
            customer_sk = self.random_gen.randint(1, 100000)

            update_sql = self.random_gen.choice(updates)

            if "C_CURRENT_ADDR_SK" in update_sql:
                new_value = self.random_gen.randint(1, 50000)
            elif "C_CURRENT_CDEMO_SK" in update_sql:
                new_value = self.random_gen.randint(1, 1920800)
            elif "C_CURRENT_HDEMO_SK" in update_sql:
                new_value = self.random_gen.randint(1, 7200)
            elif "C_PREFERRED_CUST_FLAG" in update_sql:
                new_value = self.random_gen.choice(["Y", "N"])
            elif "C_EMAIL_ADDRESS" in update_sql:
                new_value = f"customer{customer_sk}@example.com"
            else:
                continue

            connection.execute(update_sql, (new_value, customer_sk))
            rows_updated += 1

        return rows_updated

    def _update_item(self, connection: Any, estimated_rows: int) -> int:
        self.logger.info(f"Updating {estimated_rows} rows in ITEM table")

        updates = [
            "UPDATE ITEM SET I_CURRENT_PRICE = ? WHERE I_ITEM_SK = ?",
            "UPDATE ITEM SET I_WHOLESALE_COST = ? WHERE I_ITEM_SK = ?",
            "UPDATE ITEM SET I_ITEM_DESC = ? WHERE I_ITEM_SK = ?",
            "UPDATE ITEM SET I_MANAGER_ID = ? WHERE I_ITEM_SK = ?",
        ]

        rows_updated = 0
        for _i in range(estimated_rows):
            item_sk = self.random_gen.randint(1, 18000)

            update_sql = self.random_gen.choice(updates)

            if "I_CURRENT_PRICE" in update_sql:
                new_value = round(self.random_gen.uniform(1.0, 1000.0), 2)
            elif "I_WHOLESALE_COST" in update_sql:
                new_value = round(self.random_gen.uniform(0.5, 500.0), 2)
            elif "I_ITEM_DESC" in update_sql:
                new_value = f"Updated item description {item_sk}"
            elif "I_MANAGER_ID" in update_sql:
                new_value = self.random_gen.randint(1, 100)
            else:
                continue

            connection.execute(update_sql, (new_value, item_sk))
            rows_updated += 1

        return rows_updated

    def _update_inventory(self, connection: Any, estimated_rows: int) -> int:
        self.logger.info(f"Updating {estimated_rows} rows in INVENTORY table")

        update_sql = "UPDATE INVENTORY SET INV_QUANTITY_ON_HAND = ? WHERE INV_DATE_SK = ? AND INV_ITEM_SK = ? AND INV_WAREHOUSE_SK = ?"

        rows_updated = 0
        for _i in range(estimated_rows):
            date_sk = self.random_gen.randint(2450815, 2453005)
            item_sk = self.random_gen.randint(1, 18000)
            warehouse_sk = self.random_gen.randint(1, 5)

            new_quantity = self.random_gen.randint(0, 1000)

            connection.execute(update_sql, (new_quantity, date_sk, item_sk, warehouse_sk))
            rows_updated += 1

        return rows_updated

    def _get_delete_rowid_column(self, connection: Any) -> Optional[str]:
        dialect = f"{type(connection).__module__}.{type(connection).__name__}".lower()

        if "sqlite" in dialect or "duckdb" in dialect:
            return "rowid"
        elif "psycopg" in dialect or "postgres" in dialect:
            return "ctid"
        return None

    @staticmethod
    def _count_table_rows(connection: Any, table_name: str) -> int:
        cursor = connection.execute(f"SELECT COUNT(*) FROM {table_name}")
        row = cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    @staticmethod
    def _reported_deleted_rows(cursor: Any) -> int:
        rowcount = getattr(cursor, "rowcount", -1)
        if type(rowcount) is int and rowcount >= 0:
            return rowcount
        return -1

    def _execute_limited_delete(
        self,
        connection: Any,
        table_name: str,
        date_column: str,
        cutoff_date_sk: int,
        limit: int,
    ) -> int:
        limit = max(0, int(limit))
        placeholder = self._get_parameter_placeholder(connection)
        rowid_column = self._get_delete_rowid_column(connection)

        if rowid_column is not None:
            delete_sql = (
                f"DELETE FROM {table_name} WHERE {rowid_column} IN "
                f"(SELECT {rowid_column} FROM {table_name} "
                f"WHERE {date_column} < {placeholder} LIMIT {placeholder})"
            )
            params: tuple = (cutoff_date_sk, limit)
        else:
            delete_sql = f"DELETE FROM {table_name} WHERE {date_column} < {placeholder} LIMIT {limit}"
            params = (cutoff_date_sk,)

        try:
            total_before = self._count_table_rows(connection, table_name)
            cursor = connection.execute(delete_sql, params)
            deleted = self._reported_deleted_rows(cursor)
            if deleted < 0:
                deleted = max(0, total_before - self._count_table_rows(connection, table_name))
        except Exception as e:
            raise MaintenanceError(f"Delete from {table_name} failed: {e}") from e
        return deleted

    def _delete_from_tables(
        self, connection: Any, specs: list[tuple[str, str]], cutoff_date_sk: int, per_table_limit: int
    ) -> int:
        total_deleted = 0
        for table_name, date_column in specs:
            total_deleted += self._execute_limited_delete(
                connection, table_name, date_column, cutoff_date_sk, per_table_limit
            )
        return total_deleted

    def _delete_old_sales(self, connection: Any, estimated_rows: int) -> int:
        self.logger.info(f"Deleting approximately {estimated_rows} rows from sales tables")

        cutoff_date_sk = 2450815

        return self._delete_from_tables(
            connection,
            [
                ("STORE_SALES", "SS_SOLD_DATE_SK"),
                ("CATALOG_SALES", "CS_SOLD_DATE_SK"),
                ("WEB_SALES", "WS_SOLD_DATE_SK"),
            ],
            cutoff_date_sk,
            estimated_rows // 3,
        )

    def _delete_old_returns(self, connection: Any, estimated_rows: int) -> int:
        self.logger.info(f"Deleting approximately {estimated_rows} rows from returns tables")

        cutoff_date_sk = 2450815

        return self._delete_from_tables(
            connection,
            [
                ("STORE_RETURNS", "SR_RETURNED_DATE_SK"),
                ("CATALOG_RETURNS", "CR_RETURNED_DATE_SK"),
                ("WEB_RETURNS", "WR_RETURNED_DATE_SK"),
            ],
            cutoff_date_sk,
            estimated_rows // 3,
        )

    def _bulk_load_sales(self, connection: Any, estimated_rows: int) -> int:
        self.logger.info(f"Bulk loading {estimated_rows} rows into sales tables")

        total_inserted = 0

        batch_size = estimated_rows // 3

        total_inserted += self._insert_store_sales(connection, batch_size)
        total_inserted += self._insert_catalog_sales(connection, batch_size)
        total_inserted += self._insert_web_sales(connection, batch_size)

        return total_inserted

    def _bulk_update_inventory(self, connection: Any, estimated_rows: int) -> int:
        self.logger.info(f"Bulk updating {estimated_rows} rows in INVENTORY table")

        update_sql = """
        UPDATE INVENTORY
        SET INV_QUANTITY_ON_HAND = INV_QUANTITY_ON_HAND - ?
        WHERE INV_DATE_SK = ? AND INV_ITEM_SK = ? AND INV_WAREHOUSE_SK = ?
        AND INV_QUANTITY_ON_HAND > ?
        """

        rows_updated = 0
        for _i in range(estimated_rows):
            date_sk = self.random_gen.randint(2452640, 2453005)
            item_sk = self.random_gen.randint(1, 18000)
            warehouse_sk = self.random_gen.randint(1, 5)

            quantity_adjustment = self.random_gen.randint(1, 50)

            connection.execute(
                update_sql,
                (
                    quantity_adjustment,
                    date_sk,
                    item_sk,
                    warehouse_sk,
                    quantity_adjustment,
                ),
            )
            rows_updated += 1

        return rows_updated

    def _generate_store_sales_row(self) -> tuple:
        return (
            self._get_random_key("date_dim"),
            self._get_random_key("time_dim"),
            self._get_random_key("item"),
            self._get_random_key("customer"),
            self._get_random_key("customer_demographics"),
            self._get_random_key("household_demographics"),
            self._get_random_key("customer_address"),
            self._get_random_key("store"),
            self._get_random_key("promotion", allow_null=True, null_probability=0.7),
            self.random_gen.randint(1, 99999999),
            self.random_gen.randint(1, 100),
            round(self.random_gen.uniform(1.0, 100.0), 2),
            round(self.random_gen.uniform(1.0, 200.0), 2),
            round(self.random_gen.uniform(1.0, 200.0), 2),
            round(self.random_gen.uniform(0.0, 50.0), 2),
            round(self.random_gen.uniform(1.0, 1000.0), 2),
            round(self.random_gen.uniform(1.0, 500.0), 2),
            round(self.random_gen.uniform(1.0, 1000.0), 2),
            round(self.random_gen.uniform(0.0, 100.0), 2),
            round(self.random_gen.uniform(0.0, 50.0), 2),
            round(self.random_gen.uniform(1.0, 1000.0), 2),
            round(self.random_gen.uniform(1.0, 1100.0), 2),
            round(self.random_gen.uniform(-50.0, 500.0), 2),
        )

    def _generate_catalog_sales_row(self) -> tuple:
        return (
            self._get_random_key("date_dim"),
            self._get_random_key("time_dim"),
            self._get_random_key("date_dim"),
            self._get_random_key("customer"),
            self._get_random_key("customer_demographics"),
            self._get_random_key("household_demographics"),
            self._get_random_key("customer_address"),
            self._get_random_key("customer"),
            self._get_random_key("customer_demographics"),
            self._get_random_key("household_demographics"),
            self._get_random_key("customer_address"),
            self._get_random_key("call_center"),
            self._get_random_key("catalog_page"),
            self._get_random_key("ship_mode"),
            self._get_random_key("warehouse"),
            self._get_random_key("item"),
            self._get_random_key("promotion", allow_null=True, null_probability=0.7),
            self.random_gen.randint(1, 99999999),
            self.random_gen.randint(1, 100),
            round(self.random_gen.uniform(1.0, 100.0), 2),
            round(self.random_gen.uniform(1.0, 200.0), 2),
            round(self.random_gen.uniform(1.0, 200.0), 2),
            round(self.random_gen.uniform(0.0, 50.0), 2),
            round(self.random_gen.uniform(1.0, 1000.0), 2),
            round(self.random_gen.uniform(1.0, 500.0), 2),
            round(self.random_gen.uniform(1.0, 1000.0), 2),
            round(self.random_gen.uniform(0.0, 100.0), 2),
            round(self.random_gen.uniform(0.0, 50.0), 2),
            round(self.random_gen.uniform(1.0, 100.0), 2),
            round(self.random_gen.uniform(1.0, 1000.0), 2),
            round(self.random_gen.uniform(1.0, 1100.0), 2),
            round(self.random_gen.uniform(1.0, 1200.0), 2),
            round(self.random_gen.uniform(1.0, 1300.0), 2),
            round(self.random_gen.uniform(-50.0, 500.0), 2),
        )

    def _generate_web_sales_row(self) -> tuple:
        return (
            self._get_random_key("date_dim"),
            self._get_random_key("time_dim"),
            self._get_random_key("date_dim"),
            self._get_random_key("item"),
            self._get_random_key("customer"),
            self._get_random_key("customer_demographics"),
            self._get_random_key("household_demographics"),
            self._get_random_key("customer_address"),
            self._get_random_key("customer"),
            self._get_random_key("customer_demographics"),
            self._get_random_key("household_demographics"),
            self._get_random_key("customer_address"),
            self._get_random_key("web_page"),
            self._get_random_key("web_site"),
            self._get_random_key("ship_mode"),
            self._get_random_key("warehouse"),
            self._get_random_key("promotion", allow_null=True, null_probability=0.7),
            self.random_gen.randint(1, 99999999),
            self.random_gen.randint(1, 100),
            round(self.random_gen.uniform(1.0, 100.0), 2),
            round(self.random_gen.uniform(1.0, 200.0), 2),
            round(self.random_gen.uniform(1.0, 200.0), 2),
            round(self.random_gen.uniform(0.0, 50.0), 2),
            round(self.random_gen.uniform(1.0, 1000.0), 2),
            round(self.random_gen.uniform(1.0, 500.0), 2),
            round(self.random_gen.uniform(1.0, 1000.0), 2),
            round(self.random_gen.uniform(0.0, 100.0), 2),
            round(self.random_gen.uniform(0.0, 50.0), 2),
            round(self.random_gen.uniform(1.0, 100.0), 2),
            round(self.random_gen.uniform(1.0, 1000.0), 2),
            round(self.random_gen.uniform(1.0, 1100.0), 2),
            round(self.random_gen.uniform(1.0, 1200.0), 2),
            round(self.random_gen.uniform(1.0, 1300.0), 2),
            round(self.random_gen.uniform(-50.0, 500.0), 2),
        )

    def _generate_store_returns_from_sale(self, sale_record: tuple) -> tuple:
        ticket_num, item_sk, cust_sk, cdemo_sk, hdemo_sk, addr_sk, store_sk, quantity = sale_record

        return_qty = self.random_gen.randint(1, min(100, int(quantity)))

        return (
            self._get_random_key("date_dim"),
            self._get_random_key("time_dim"),
            item_sk,
            cust_sk,
            cdemo_sk,
            hdemo_sk,
            addr_sk,
            store_sk,
            self.random_gen.randint(1, 35)
            if "dimension_ranges" in dir(self) and "reason" in self.dimension_ranges
            else 1,
            ticket_num,
            return_qty,
            round(self.random_gen.uniform(1.0, 1000.0), 2),
            round(self.random_gen.uniform(0.0, 100.0), 2),
            round(self.random_gen.uniform(1.0, 1100.0), 2),
            round(self.random_gen.uniform(0.0, 50.0), 2),
            round(self.random_gen.uniform(0.0, 100.0), 2),
            round(self.random_gen.uniform(1.0, 1000.0), 2),
            round(self.random_gen.uniform(0.0, 500.0), 2),
            round(self.random_gen.uniform(0.0, 500.0), 2),
            round(self.random_gen.uniform(-100.0, 100.0), 2),
        )

    def _generate_store_returns_row(self) -> tuple:
        return (
            self._get_random_key("date_dim"),
            self._get_random_key("time_dim"),
            self._get_random_key("item"),
            self._get_random_key("customer"),
            self._get_random_key("customer_demographics"),
            self._get_random_key("household_demographics"),
            self._get_random_key("customer_address"),
            self._get_random_key("store"),
            self.random_gen.randint(1, 35),
            self.random_gen.randint(1, 99999999),
            self.random_gen.randint(1, 100),
            round(self.random_gen.uniform(1.0, 1000.0), 2),
            round(self.random_gen.uniform(0.0, 100.0), 2),
            round(self.random_gen.uniform(1.0, 1100.0), 2),
            round(self.random_gen.uniform(0.0, 50.0), 2),
            round(self.random_gen.uniform(0.0, 50.0), 2),
            round(self.random_gen.uniform(0.0, 500.0), 2),
            round(self.random_gen.uniform(0.0, 500.0), 2),
            round(self.random_gen.uniform(0.0, 500.0), 2),
            round(self.random_gen.uniform(0.0, 500.0), 2),
        )

    def _generate_returns_from_sale(
        self,
        sale_record: tuple,
        *,
        field_mapping: tuple[int, int, int, int, int, int, int],
        channel_fields: tuple[int, ...],
    ) -> tuple:
        (
            order_num_idx,
            item_idx,
            bill_customer_idx,
            bill_cdemo_idx,
            bill_hdemo_idx,
            bill_addr_idx,
            quantity_idx,
        ) = field_mapping

        order_num = sale_record[order_num_idx]
        item_sk = sale_record[item_idx]
        bill_cust_sk = sale_record[bill_customer_idx]
        bill_cdemo_sk = sale_record[bill_cdemo_idx]
        bill_hdemo_sk = sale_record[bill_hdemo_idx]
        bill_addr_sk = sale_record[bill_addr_idx]
        quantity = sale_record[quantity_idx]
        return_qty = self.random_gen.randint(1, min(100, int(quantity)))
        channel_values = tuple(sale_record[index] for index in channel_fields)

        return (
            self._get_random_key("date_dim"),
            self._get_random_key("time_dim"),
            item_sk,
            bill_cust_sk,
            bill_cdemo_sk,
            bill_hdemo_sk,
            bill_addr_sk,
            bill_cust_sk,
            bill_cdemo_sk,
            bill_hdemo_sk,
            bill_addr_sk,
            *channel_values,
            self.random_gen.randint(1, 35),
            order_num,
            return_qty,
            round(self.random_gen.uniform(1.0, 1000.0), 2),
            round(self.random_gen.uniform(0.0, 100.0), 2),
            round(self.random_gen.uniform(1.0, 1100.0), 2),
            round(self.random_gen.uniform(0.0, 50.0), 2),
            round(self.random_gen.uniform(0.0, 50.0), 2),
            round(self.random_gen.uniform(0.0, 500.0), 2),
            round(self.random_gen.uniform(0.0, 500.0), 2),
            round(self.random_gen.uniform(0.0, 500.0), 2),
            round(self.random_gen.uniform(0.0, 500.0), 2),
        )

    def _generate_catalog_returns_from_sale(self, sale_record: tuple) -> tuple:
        return self._generate_returns_from_sale(
            sale_record,
            field_mapping=(0, 1, 2, 3, 4, 5, 10),
            channel_fields=(6, 7, 8, 9),
        )

    def _generate_web_returns_from_sale(self, sale_record: tuple) -> tuple:
        return self._generate_returns_from_sale(
            sale_record,
            field_mapping=(0, 1, 2, 3, 4, 5, 7),
            channel_fields=(6,),
        )

    def _generate_catalog_returns_row(self) -> tuple:
        return (
            self._get_random_key("date_dim"),
            self._get_random_key("time_dim"),
            self._get_random_key("item"),
            self._get_random_key("customer"),
            self._get_random_key("customer_demographics"),
            self._get_random_key("household_demographics"),
            self._get_random_key("customer_address"),
            self._get_random_key("customer"),
            self._get_random_key("customer_demographics"),
            self._get_random_key("household_demographics"),
            self._get_random_key("customer_address"),
            self._get_random_key("call_center"),
            self._get_random_key("catalog_page"),
            self._get_random_key("ship_mode"),
            self._get_random_key("warehouse"),
            self.random_gen.randint(1, 35),
            self.random_gen.randint(1, 99999999),
            self.random_gen.randint(1, 100),
            round(self.random_gen.uniform(1.0, 1000.0), 2),
            round(self.random_gen.uniform(0.0, 100.0), 2),
            round(self.random_gen.uniform(1.0, 1100.0), 2),
            round(self.random_gen.uniform(0.0, 50.0), 2),
            round(self.random_gen.uniform(0.0, 50.0), 2),
            round(self.random_gen.uniform(0.0, 500.0), 2),
            round(self.random_gen.uniform(0.0, 500.0), 2),
            round(self.random_gen.uniform(0.0, 500.0), 2),
            round(self.random_gen.uniform(0.0, 500.0), 2),
        )

    def _generate_web_returns_row(self) -> tuple:
        return (
            self.random_gen.randint(2452640, 2453005),
            self.random_gen.randint(28800, 72000),
            self.random_gen.randint(1, 18000),
            self.random_gen.randint(1, 100000),
            self.random_gen.randint(1, 1920800),
            self.random_gen.randint(1, 7200),
            self.random_gen.randint(1, 50000),
            self.random_gen.randint(1, 100000),
            self.random_gen.randint(1, 1920800),
            self.random_gen.randint(1, 7200),
            self.random_gen.randint(1, 50000),
            self.random_gen.randint(1, 60),
            self.random_gen.randint(1, 35),
            self.random_gen.randint(1, 99999999),
            self.random_gen.randint(1, 100),
            round(self.random_gen.uniform(1.0, 1000.0), 2),
            round(self.random_gen.uniform(0.0, 100.0), 2),
            round(self.random_gen.uniform(1.0, 1100.0), 2),
            round(self.random_gen.uniform(0.0, 50.0), 2),
            round(self.random_gen.uniform(0.0, 50.0), 2),
            round(self.random_gen.uniform(0.0, 500.0), 2),
            round(self.random_gen.uniform(0.0, 500.0), 2),
            round(self.random_gen.uniform(0.0, 500.0), 2),
            round(self.random_gen.uniform(0.0, 500.0), 2),
        )
