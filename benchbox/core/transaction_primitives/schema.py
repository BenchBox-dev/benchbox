import importlib
from pathlib import Path
from typing import Any, cast

import yaml

from benchbox.core.tpch.schema import (
    CUSTOMER,
    LINEITEM,
    NATION,
    ORDERS,
    PART,
    PARTSUPP,
    REGION,
    SUPPLIER,
)
from benchbox.core.transactional.staging_types import translate_staging_column_type


def _load_schema_specs() -> dict[str, Any]:
    with (Path(__file__).with_name("schema_specs.yaml")).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


_SCHEMA_SPECS = _load_schema_specs()
_STAGING_DEFS = {entry["id"]: entry for entry in _SCHEMA_SPECS["staging_tables"]}
globals().update({symbol: entry["schema"] for symbol, entry in _STAGING_DEFS.items()})
TXN_ORDERS = cast(dict[str, Any], globals()["TXN_ORDERS"])
TXN_LINEITEM = cast(dict[str, Any], globals()["TXN_LINEITEM"])
TXN_CUSTOMER = cast(dict[str, Any], globals()["TXN_CUSTOMER"])
_STAGING_TABLE_ORDER = list(_SCHEMA_SPECS["staging_table_order"])

TABLES = {
    "region": REGION,
    "nation": NATION,
    "customer": CUSTOMER,
    "supplier": SUPPLIER,
    "part": PART,
    "partsupp": PARTSUPP,
    "orders": ORDERS,
    "lineitem": LINEITEM,
    "txn_orders": TXN_ORDERS,
    "txn_lineitem": TXN_LINEITEM,
    "txn_customer": TXN_CUSTOMER,
}

STAGING_TABLES = {entry["key"]: globals()[symbol] for symbol, entry in _STAGING_DEFS.items()}


def _requires_catalog_managed_staging(dialect: str) -> bool:
    importlib.import_module("benchbox.sql_compat.rules.ddl_optimize.databricks_ddl_rewrites")
    from benchbox.sql_compat.actions import CompatAction
    from benchbox.sql_compat.context import CompatibilityContext, Phase
    from benchbox.sql_compat.registry import REGISTRY

    ctx = CompatibilityContext(
        platform=dialect.lower(),
        platform_version=None,
        benchmark="transaction_primitives",
        query_id=None,
        phase=Phase.DDL_OPTIMIZE,
        mode="sql",
        dialect=dialect,
    )
    registry_decision = REGISTRY.resolve(ctx)

    if registry_decision is not None:
        return (
            registry_decision.action == CompatAction.REWRITE_DDL
            and registry_decision.rule_id
            == "ddl_optimize.databricks.transaction_primitives.txn_staging_catalog_managed"
        )
    return False


def _supports_primary_keys(dialect: str) -> bool:
    importlib.import_module("benchbox.sql_compat.rules.schema_emit.pk_capability_txn")
    from benchbox.sql_compat.actions import CompatAction
    from benchbox.sql_compat.context import CompatibilityContext, Phase
    from benchbox.sql_compat.registry import REGISTRY

    ctx = CompatibilityContext(
        platform=dialect.lower(),
        platform_version=None,
        benchmark="transaction_primitives",
        query_id=None,
        phase=Phase.SCHEMA_EMIT,
        mode="sql",
        dialect=dialect,
    )
    registry_decision = REGISTRY.resolve(ctx)

    if registry_decision is not None:
        return registry_decision.action == CompatAction.NATIVE
    return True


def get_create_table_sql(table_name: str, dialect: str = "standard", if_not_exists: bool = False) -> str:
    if table_name not in STAGING_TABLES:
        raise ValueError(f"Unknown staging table: {table_name}. Use STAGING_TABLES only.")

    supports_pk = _supports_primary_keys(dialect)
    table = STAGING_TABLES[table_name]
    columns: list[str] = []

    for col in table["columns"]:
        col_def = f"{col['name']} {translate_staging_column_type(col['type'], dialect)}"
        if supports_pk and col.get("primary_key"):
            col_def += " PRIMARY KEY"
        if not col.get("nullable", False) and not col.get("primary_key"):
            col_def += " NOT NULL"
        columns.append(col_def)

    if supports_pk and "primary_key" in table and isinstance(table["primary_key"], list):
        pk_cols = ", ".join(table["primary_key"])
        columns.append(f"PRIMARY KEY ({pk_cols})")

    if_not_exists_clause = " IF NOT EXISTS" if if_not_exists else ""
    sql = f"CREATE TABLE{if_not_exists_clause} {table['name']} (\n"
    sql += ",\n".join(f"  {col}" for col in columns)
    sql += "\n)"
    if _requires_catalog_managed_staging(dialect):
        sql += " USING DELTA TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported')"
    sql += ";"

    return sql


def get_all_staging_tables_sql(dialect: str = "standard") -> str:
    sql_statements: list[str] = []
    for table_name in _STAGING_TABLE_ORDER:
        sql_statements.append(get_create_table_sql(table_name, dialect))

    return "\n\n".join(sql_statements)


def get_table_schema(table_name: str) -> dict[str, Any]:
    if table_name not in TABLES:
        raise ValueError(f"Unknown table: {table_name}")
    return cast(dict[str, Any], TABLES[table_name])


__all__ = [
    "TABLES",
    "STAGING_TABLES",
    "TXN_ORDERS",
    "TXN_LINEITEM",
    "TXN_CUSTOMER",
    "get_create_table_sql",
    "get_all_staging_tables_sql",
    "get_table_schema",
]
