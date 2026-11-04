import importlib
from pathlib import Path
from typing import Any, cast

import yaml

from benchbox.core.tpch.schema import CUSTOMER, LINEITEM, NATION, ORDERS, PART, PARTSUPP, REGION, SUPPLIER
from benchbox.core.transactional.staging_types import (
    staging_nullable_type,
    staging_table_suffix,
    translate_staging_column_type,
)


def _load_schema_specs() -> dict[str, Any]:
    with (Path(__file__).with_name("schema_specs.yaml")).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


_SCHEMA_SPECS = _load_schema_specs()
_TABLE_DEFS = {entry["id"]: entry for entry in _SCHEMA_SPECS["tables"]}
globals().update({symbol: entry["schema"] for symbol, entry in _TABLE_DEFS.items()})

TABLES = {
    "region": REGION,
    "nation": NATION,
    "customer": CUSTOMER,
    "supplier": SUPPLIER,
    "part": PART,
    "partsupp": PARTSUPP,
    "orders": ORDERS,
    "lineitem": LINEITEM,
    **{entry["key"]: globals()[symbol] for symbol, entry in _TABLE_DEFS.items()},
}

STAGING_TABLES = {name: TABLES[name] for name in _SCHEMA_SPECS["staging_tables"]}
_CREATE_ORDER = _SCHEMA_SPECS["create_order"]


_BINARY_TYPE_BY_DIALECT: dict[str, str | None] = {
    "standard": "BINARY",
    "duckdb": "BLOB",
    "databricks": "BINARY",
    "snowflake": "BINARY",
    "bigquery": "BYTES",
    "clickhouse": "String",
    "postgres": "BYTEA",
    "redshift": "HLLSKETCH",
    "sqlite": None,
    "datafusion": None,
}


def translate_column_type(type_str: str, dialect: str) -> str | None:
    canonical = type_str.strip().upper()
    if canonical == "BINARY":
        return _BINARY_TYPE_BY_DIALECT.get(dialect.lower(), "BINARY")
    return translate_staging_column_type(type_str, dialect)


def _supports_primary_keys(dialect: str) -> bool:
    importlib.import_module("benchbox.sql_compat.rules.schema_emit.pk_capability")
    from benchbox.sql_compat.actions import CompatAction
    from benchbox.sql_compat.context import CompatibilityContext, Phase
    from benchbox.sql_compat.registry import REGISTRY

    ctx = CompatibilityContext(
        platform=dialect.lower(),
        platform_version=None,
        benchmark="write_primitives",
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

    table = STAGING_TABLES[table_name]
    supports_primary_keys = _supports_primary_keys(dialect)
    columns: list[str] = []

    for col in table["columns"]:
        column_type = translate_column_type(col["type"], dialect)
        if column_type is None:
            raise ValueError(
                f"Column '{col['name']}' uses logical type '{col['type']}' which has no "
                f"mapping for dialect '{dialect}'. Skip this table or pick a dialect with support."
            )
        column_type = staging_nullable_type(
            column_type, dialect, bool(col.get("nullable", False)) and not col.get("primary_key")
        )
        col_def = f"{col['name']} {column_type}"
        if col.get("primary_key") and supports_primary_keys:
            col_def += " PRIMARY KEY"
        if not col.get("nullable", False) and not col.get("primary_key"):
            col_def += " NOT NULL"
        columns.append(col_def)

    if supports_primary_keys and "primary_key" in table and isinstance(table["primary_key"], list):
        columns.append(f"PRIMARY KEY ({', '.join(table['primary_key'])})")

    if_not_exists_clause = " IF NOT EXISTS" if if_not_exists else ""
    sql = f"CREATE TABLE{if_not_exists_clause} {table['name']} (\n"
    sql += ",\n".join(f"  {col}" for col in columns)
    sql += "\n)"
    sql += staging_table_suffix(dialect, table)
    sql += ";"
    return sql


def get_all_staging_tables_sql(dialect: str = "standard") -> str:
    return "\n\n".join(get_create_table_sql(table_name, dialect) for table_name in _CREATE_ORDER)


def get_table_schema(table_name: str) -> dict[str, Any]:
    if table_name not in TABLES:
        raise ValueError(f"Unknown table: {table_name}")
    return cast(dict[str, Any], TABLES[table_name])


__all__ = [
    "TABLES",
    "STAGING_TABLES",
    *_TABLE_DEFS,
    "get_create_table_sql",
    "get_all_staging_tables_sql",
    "get_table_schema",
    "translate_column_type",
]
