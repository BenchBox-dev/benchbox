from __future__ import annotations

from benchbox.core.tuning import BenchmarkTunings, TableTuning, TuningColumn
from benchbox.utils.questdb_subquery_schemas import register_benchmark_schema

from .models import Table
from .tables import _TABLES

_TABLE_ORDER = (
    "DATE_DIM",
    "TIME_DIM",
    "ITEM",
    "CUSTOMER_DEMOGRAPHICS",
    "INCOME_BAND",
    "HOUSEHOLD_DEMOGRAPHICS",
    "CUSTOMER_ADDRESS",
    "STORE",
    "WAREHOUSE",
    "WEB_SITE",
    "WEB_PAGE",
    "PROMOTION",
    "REASON",
    "CALL_CENTER",
    "CATALOG_PAGE",
    "SHIP_MODE",
    "CUSTOMER",
    "STORE_SALES",
    "WEB_SALES",
    "CATALOG_SALES",
    "INVENTORY",
    "STORE_RETURNS",
    "WEB_RETURNS",
    "CATALOG_RETURNS",
    "DBGEN_VERSION",
)

TABLES = [_TABLES[name] for name in _TABLE_ORDER]

TABLES_BY_NAME = {table.name: table for table in TABLES}

register_benchmark_schema(TABLES)


def get_table(name: str) -> Table:
    name_upper = name.upper()
    if name_upper not in TABLES_BY_NAME:
        raise ValueError(f"Invalid table name: {name}")
    return TABLES_BY_NAME[name_upper]


def get_create_all_tables_sql(enable_primary_keys: bool = True, enable_foreign_keys: bool = True) -> str:
    return "\n\n".join(
        table.get_create_table_sql(
            enable_primary_keys=enable_primary_keys,
            enable_foreign_keys=enable_foreign_keys,
        )
        for table in TABLES
    )


def get_tunings() -> BenchmarkTunings:
    tunings = BenchmarkTunings("tpcds")

    store_sales_tuning = TableTuning(
        table_name="STORE_SALES",
        partitioning=[TuningColumn("SS_SOLD_DATE_SK", "INTEGER", 1)],
        clustering=[
            TuningColumn("SS_STORE_SK", "INTEGER", 1),
            TuningColumn("SS_ITEM_SK", "INTEGER", 2),
        ],
        sorting=[
            TuningColumn("SS_TICKET_NUMBER", "INTEGER", 1),
            TuningColumn("SS_QUANTITY", "INTEGER", 2),
        ],
    )
    tunings.add_table_tuning(store_sales_tuning)

    catalog_sales_tuning = TableTuning(
        table_name="CATALOG_SALES",
        partitioning=[TuningColumn("CS_SOLD_DATE_SK", "INTEGER", 1)],
        clustering=[
            TuningColumn("CS_ITEM_SK", "INTEGER", 1),
            TuningColumn("CS_BILL_CUSTOMER_SK", "INTEGER", 2),
        ],
        sorting=[
            TuningColumn("CS_ORDER_NUMBER", "INTEGER", 1),
            TuningColumn("CS_QUANTITY", "INTEGER", 2),
        ],
    )
    tunings.add_table_tuning(catalog_sales_tuning)

    web_sales_tuning = TableTuning(
        table_name="WEB_SALES",
        partitioning=[TuningColumn("WS_SOLD_DATE_SK", "INTEGER", 1)],
        clustering=[
            TuningColumn("WS_ITEM_SK", "INTEGER", 1),
            TuningColumn("WS_BILL_CUSTOMER_SK", "INTEGER", 2),
        ],
        sorting=[
            TuningColumn("WS_ORDER_NUMBER", "INTEGER", 1),
            TuningColumn("WS_QUANTITY", "INTEGER", 2),
        ],
    )
    tunings.add_table_tuning(web_sales_tuning)

    store_returns_tuning = TableTuning(
        table_name="STORE_RETURNS",
        partitioning=[TuningColumn("SR_RETURNED_DATE_SK", "INTEGER", 1)],
        clustering=[
            TuningColumn("SR_STORE_SK", "INTEGER", 1),
            TuningColumn("SR_ITEM_SK", "INTEGER", 2),
        ],
        sorting=[TuningColumn("SR_TICKET_NUMBER", "INTEGER", 1)],
    )
    tunings.add_table_tuning(store_returns_tuning)

    web_returns_tuning = TableTuning(
        table_name="WEB_RETURNS",
        partitioning=[TuningColumn("WR_RETURNED_DATE_SK", "INTEGER", 1)],
        clustering=[TuningColumn("WR_ITEM_SK", "INTEGER", 1)],
        sorting=[TuningColumn("WR_ORDER_NUMBER", "INTEGER", 1)],
    )
    tunings.add_table_tuning(web_returns_tuning)

    catalog_returns_tuning = TableTuning(
        table_name="CATALOG_RETURNS",
        partitioning=[TuningColumn("CR_RETURNED_DATE_SK", "INTEGER", 1)],
        clustering=[TuningColumn("CR_ITEM_SK", "INTEGER", 1)],
        sorting=[TuningColumn("CR_ORDER_NUMBER", "INTEGER", 1)],
    )
    tunings.add_table_tuning(catalog_returns_tuning)

    inventory_tuning = TableTuning(
        table_name="INVENTORY",
        partitioning=[TuningColumn("INV_DATE_SK", "INTEGER", 1)],
        clustering=[
            TuningColumn("INV_ITEM_SK", "INTEGER", 1),
            TuningColumn("INV_WAREHOUSE_SK", "INTEGER", 2),
        ],
        sorting=[TuningColumn("INV_QUANTITY_ON_HAND", "INTEGER", 1)],
    )
    tunings.add_table_tuning(inventory_tuning)

    date_dim_tuning = TableTuning(table_name="DATE_DIM", sorting=[TuningColumn("D_DATE_SK", "INTEGER", 1)])
    tunings.add_table_tuning(date_dim_tuning)

    item_tuning = TableTuning(
        table_name="ITEM",
        distribution=[TuningColumn("I_ITEM_SK", "INTEGER", 1)],
        sorting=[TuningColumn("I_ITEM_ID", "CHAR", 1)],
    )
    tunings.add_table_tuning(item_tuning)

    customer_tuning = TableTuning(
        table_name="CUSTOMER",
        distribution=[TuningColumn("C_CUSTOMER_SK", "INTEGER", 1)],
        sorting=[TuningColumn("C_CUSTOMER_ID", "CHAR", 1)],
    )
    tunings.add_table_tuning(customer_tuning)

    store_tuning = TableTuning(table_name="STORE", sorting=[TuningColumn("S_STORE_SK", "INTEGER", 1)])
    tunings.add_table_tuning(store_tuning)

    return tunings


__all__ = [
    "TABLES",
    "TABLES_BY_NAME",
    "get_table",
    "get_create_all_tables_sql",
    "get_tunings",
]
