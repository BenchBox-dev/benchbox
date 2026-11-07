from __future__ import annotations

import csv
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from benchbox.core.contracts.platform_capabilities import resolve_refresh_ingester

REFRESH_CHUNK_SIZE = 1000
_DECIMAL_PATTERN = re.compile(r"^DECIMAL\(\d+,\s*\d+\)$")


class RefreshDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class RefreshSourceTable:
    name: str
    file_stem: str
    columns: tuple[tuple[str, str, bool], ...]

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(name for name, _, _ in self.columns)

    @property
    def column_types(self) -> tuple[tuple[str, str], ...]:
        return tuple((name, sql_type) for name, sql_type, _ in self.columns)

    def create_sql(self) -> str:
        definitions = ", ".join(
            f"{name} {sql_type}{' NOT NULL' if not nullable else ''}" for name, sql_type, nullable in self.columns
        )
        return f"CREATE TABLE {self.name} ({definitions})"

    def drop_sql(self) -> str:
        return f"DROP TABLE IF EXISTS {self.name}"


def _column(spec: tuple[str, str] | tuple[str, str, bool]) -> tuple[str, str, bool]:
    name, sql_type, *nullable = spec
    return (str(name), str(sql_type), bool(nullable[0]) if nullable else True)


def _table(name: str, file_stem: str, *columns: tuple[str, str] | tuple[str, str, bool]) -> RefreshSourceTable:
    return RefreshSourceTable(name=name, file_stem=file_stem, columns=tuple(_column(column) for column in columns))


_INT = "INTEGER"
_ID = "CHAR(16)"
_DATE = "DATE"
_TIME = "CHAR(10)"
_AMOUNT = "DECIMAL(7,2)"

SOURCE_TABLES: tuple[RefreshSourceTable, ...] = (
    _table(
        "s_purchase",
        "s_purchase",
        ("purc_purchase_id", _INT, False),
        ("purc_store_id", _ID),
        ("purc_customer_id", _ID),
        ("purc_purchase_date", _DATE),
        ("purc_purchase_time", _INT),
        ("purc_register_id", _INT),
        ("purc_clerk_id", _INT),
        ("purc_comment", "CHAR(100)"),
    ),
    _table(
        "s_purchase_lineitem",
        "s_purchase_lineitem",
        ("plin_purchase_id", _INT, False),
        ("plin_line_number", _INT, False),
        ("plin_item_id", _ID),
        ("plin_promotion_id", _ID),
        ("plin_quantity", _INT),
        ("plin_sale_price", _AMOUNT),
        ("plin_coupon_amt", _AMOUNT),
        ("plin_comment", "VARCHAR(100)"),
    ),
    _table(
        "s_catalog_order",
        "s_catalog_order",
        ("cord_order_id", _INT, False),
        ("cord_bill_customer_id", _ID),
        ("cord_ship_customer_id", _ID),
        ("cord_order_date", _DATE),
        ("cord_order_time", _INT),
        ("cord_ship_mode_id", _ID),
        ("cord_call_center_id", _ID),
        ("cord_order_comments", "VARCHAR(100)"),
    ),
    _table(
        "s_catalog_order_lineitem",
        "s_catalog_order_lineitem",
        ("clin_order_id", _INT, False),
        ("clin_line_number", _INT, False),
        ("clin_item_id", _ID),
        ("clin_promotion_id", _ID),
        ("clin_quantity", _INT),
        ("clin_sales_price", _AMOUNT),
        ("clin_coupon_amt", _AMOUNT),
        ("clin_warehouse_id", _ID),
        ("clin_ship_date", _DATE),
        ("clin_catalog_number", _INT),
        ("clin_catalog_page_number", _INT),
        ("clin_ship_cost", _AMOUNT),
    ),
    _table(
        "s_web_order",
        "s_web_order",
        ("word_order_id", _INT, False),
        ("word_bill_customer_id", _ID),
        ("word_ship_customer_id", _ID),
        ("word_order_date", _DATE),
        ("word_order_time", _INT),
        ("word_ship_mode_id", _ID),
        ("word_web_site_id", _ID),
        ("word_order_comments", "CHAR(100)"),
    ),
    _table(
        "s_web_order_lineitem",
        "s_web_order_lineitem",
        ("wlin_order_id", _INT, False),
        ("wlin_line_number", _INT, False),
        ("wlin_item_id", _ID),
        ("wlin_promotion_id", _ID),
        ("wlin_quantity", _INT),
        ("wlin_sales_price", _AMOUNT),
        ("wlin_coupon_amt", _AMOUNT),
        ("wlin_warehouse_id", _ID),
        ("wlin_ship_date", _DATE),
        ("wlin_ship_cost", _AMOUNT),
        ("wlin_web_page_id", _ID),
    ),
    _table(
        "s_store_returns",
        "s_store_returns",
        ("sret_store_id", _ID),
        ("sret_purchase_id", _ID, False),
        ("sret_line_number", _INT, False),
        ("sret_item_id", _ID, False),
        ("sret_customer_id", _ID),
        ("sret_return_date", _DATE),
        ("sret_return_time", _TIME),
        ("sret_ticket_number", "CHAR(20)"),
        ("sret_return_qty", _INT),
        ("sret_return_amt", _AMOUNT),
        ("sret_return_tax", _AMOUNT),
        ("sret_return_fee", _AMOUNT),
        ("sret_return_ship_cost", _AMOUNT),
        ("sret_refunded_cash", _AMOUNT),
        ("sret_reversed_charge", _AMOUNT),
        ("sret_store_credit", _AMOUNT),
        ("sret_reason_id", _ID),
    ),
    _table(
        "s_catalog_returns",
        "s_catalog_returns",
        ("cret_call_center_id", _ID),
        ("cret_order_id", _INT, False),
        ("cret_line_number", _INT, False),
        ("cret_item_id", _ID, False),
        ("cret_return_customer_id", _ID),
        ("cret_refund_customer_id", _ID),
        ("cret_return_date", _DATE),
        ("cret_return_time", _TIME),
        ("cret_return_qty", _INT),
        ("cret_return_amt", _AMOUNT),
        ("cret_return_tax", _AMOUNT),
        ("cret_return_fee", _AMOUNT),
        ("cret_return_ship_cost", _AMOUNT),
        ("cret_refunded_cash", _AMOUNT),
        ("cret_reversed_charge", _AMOUNT),
        ("cret_merchant_credit", _AMOUNT),
        ("cret_reason_id", _ID),
        ("cret_shipmode_id", _ID),
        ("cret_catalog_page_id", _ID),
        ("cret_warehouse_id", _ID),
    ),
    _table(
        "s_web_returns",
        "s_web_returns",
        ("wret_web_page_id", _ID),
        ("wret_order_id", _INT, False),
        ("wret_line_number", _INT, False),
        ("wret_item_id", _ID, False),
        ("wret_return_customer_id", _ID),
        ("wret_refund_customer_id", _ID),
        ("wret_return_date", _DATE),
        ("wret_return_time", _TIME),
        ("wret_return_qty", _INT),
        ("wret_return_amt", _AMOUNT),
        ("wret_return_tax", _AMOUNT),
        ("wret_return_fee", _AMOUNT),
        ("wret_return_ship_cost", _AMOUNT),
        ("wret_refunded_cash", _AMOUNT),
        ("wret_reversed_charge", _AMOUNT),
        ("wret_account_credit", _AMOUNT),
        ("wret_reason_id", _ID),
    ),
    _table(
        "s_inventory",
        "s_inventory",
        ("invn_warehouse_id", _ID, False),
        ("invn_item_id", _ID, False),
        ("invn_date", _DATE, False),
        ("invn_qty_on_hand", _INT),
    ),
    _table(
        "s_delete",
        "delete",
        ("delete_date1", _DATE, False),
        ("delete_date2", _DATE, False),
    ),
    _table(
        "s_inventory_delete",
        "inventory_delete",
        ("inv_delete_date1", _DATE, False),
        ("inv_delete_date2", _DATE, False),
    ),
)

SOURCE_TABLES_BY_NAME: Mapping[str, RefreshSourceTable] = {table.name: table for table in SOURCE_TABLES}


@dataclass(frozen=True)
class RefreshSet:
    set_id: int
    directory: Path
    files: Mapping[str, Path]
    row_counts: Mapping[str, int]

    def path_for(self, table_name: str) -> Path:
        return self.files[table_name]


def refresh_file_name(table: RefreshSourceTable, set_id: int) -> str:
    return f"{table.file_stem}_{set_id}.dat"


def _count_lines(path: Path) -> int:
    with path.open("rb") as source:
        return sum(1 for _ in source)


def generate_refresh_sets(
    scale_factor: float,
    first_set: int,
    count: int,
    output_dir: Path,
    seed: int | None = None,
) -> dict[int, RefreshSet]:
    for label, value in (("first_set", first_set), ("count", count)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{label} must be an integer, got {type(value).__name__}")
        if value < 1:
            raise ValueError(f"{label} must be positive, got {value}")

    from benchbox.core.tpcds.generator import TPCDSDataGenerator

    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise ValueError(f"dsdgen refresh output directory must be empty: {output_dir}")

    generator = TPCDSDataGenerator(scale_factor=scale_factor, output_dir=output_dir, quiet=True)
    refresh_sets: dict[int, RefreshSet] = {}
    for set_id in range(first_set, first_set + count):
        generator.generate_refresh_set(set_id, output_dir, seed)
        files: dict[str, Path] = {}
        row_counts: dict[str, int] = {}
        for table in SOURCE_TABLES:
            path = output_dir / refresh_file_name(table, set_id)
            if not path.is_file():
                raise RefreshDataError(f"dsdgen did not produce {path.name} for refresh set {set_id}")
            files[table.name] = path
            row_counts[table.name] = _count_lines(path)
        refresh_sets[set_id] = RefreshSet(set_id=set_id, directory=output_dir, files=files, row_counts=row_counts)
    return refresh_sets


def create_staging_tables(connection: Any, tables: Sequence[RefreshSourceTable]) -> None:
    for table in tables:
        connection.execute(table.drop_sql())
        connection.execute(table.create_sql())


def drop_staging_tables(connection: Any, tables: Sequence[RefreshSourceTable]) -> None:
    for table in tables:
        connection.execute(table.drop_sql())


def load_refresh_file(connection: Any, table: RefreshSourceTable, path: Path, expected_rows: int) -> int:
    ingester = resolve_refresh_ingester(getattr(connection, "platform_adapter", None))
    if ingester is None:
        loaded = _insert_rows(connection, table, path)
    else:
        loaded = ingester.ingest_refresh_file(connection, table.name, table.column_types, path, null_marker="")
    if loaded != expected_rows:
        raise RefreshDataError(
            f"loading {path.name} into {table.name} reported {loaded} rows; the refresh file has {expected_rows}"
        )
    return loaded


def _rows(path: Path, table: RefreshSourceTable) -> Iterator[list[str]]:
    width = len(table.columns)
    with path.open(encoding="utf-8", newline="") as source:
        for line_number, fields in enumerate(csv.reader(source, delimiter="|", quoting=csv.QUOTE_NONE), start=1):
            if len(fields) != width:
                raise RefreshDataError(f"{path.name}:{line_number} has {len(fields)} fields; expected {width}")
            yield fields


def _insert_rows(connection: Any, table: RefreshSourceTable, path: Path) -> int:
    column_list = ", ".join(table.column_names)
    loaded = 0
    chunk: list[str] = []
    for fields in _rows(path, table):
        chunk.append(
            "("
            + ", ".join(_sql_literal(value, column[1]) for value, column in zip(fields, table.columns, strict=True))
            + ")"
        )
        if len(chunk) == REFRESH_CHUNK_SIZE:
            connection.execute(f"INSERT INTO {table.name} ({column_list}) VALUES {', '.join(chunk)}")
            loaded += len(chunk)
            chunk = []
    if chunk:
        connection.execute(f"INSERT INTO {table.name} ({column_list}) VALUES {', '.join(chunk)}")
        loaded += len(chunk)
    return loaded


def _sql_literal(value: str, sql_type: str) -> str:
    if value == "":
        return "NULL"
    if sql_type == "INTEGER":
        try:
            return str(int(value))
        except ValueError:
            raise RefreshDataError(f"invalid TPC-DS integer value {value!r}") from None
    if _DECIMAL_PATTERN.match(sql_type):
        try:
            decimal_value = Decimal(value)
        except InvalidOperation:
            raise RefreshDataError(f"invalid TPC-DS decimal value {value!r}") from None
        if not decimal_value.is_finite():
            raise RefreshDataError(f"invalid TPC-DS decimal value {value!r}")
        return format(decimal_value, "f")
    return "'" + value.replace("'", "''") + "'"
