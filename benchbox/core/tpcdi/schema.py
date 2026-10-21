import logging
from pathlib import Path
from typing import Any, Optional, cast

import yaml

from benchbox.utils.dialect_utils import SCHEMA_DDL_SCOPE, translate_sql_query

from .schema_extensions import EXTENSION_TABLES, get_extended_table_order, get_foreign_key_constraints

logger = logging.getLogger(__name__)


def _load_schema_specs() -> dict[str, Any]:
    with (Path(__file__).with_name("schema_specs.yaml")).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


_SCHEMA_SPECS = _load_schema_specs()
_CORE_TABLES = {entry["id"]: entry for entry in _SCHEMA_SPECS["core_tables"]}
globals().update({symbol: entry["schema"] for symbol, entry in _CORE_TABLES.items()})

TABLES = {entry["key"]: globals()[symbol] for symbol, entry in _CORE_TABLES.items()} | EXTENSION_TABLES
CORE_TABLE_ORDER = list(_SCHEMA_SPECS["core_table_order"])

_SPARK_FAMILY_DIALECTS = {"spark", "lakesail", "pyspark", "velox", "databricks"}


def _column_type_for_dialect(column: dict[str, Any], dialect: str) -> str:
    column_type = cast(str, column["type"])
    if column_type == "TIME" and dialect.lower() in _SPARK_FAMILY_DIALECTS:
        return "STRING"
    if column_type == "TIME" and dialect.lower() == "clickhouse":
        return "String"
    return column_type


def get_create_table_sql(
    table_name: str,
    dialect: str = "standard",
    enable_primary_keys: bool = True,
    enable_foreign_keys: bool = True,
) -> str:
    if table_name not in TABLES:
        raise ValueError(f"Unknown table: {table_name}")

    table = TABLES[table_name]
    columns = []

    for col in cast(list, table["columns"]):
        col_def = f"{cast(str, col['name'])} {_column_type_for_dialect(col, dialect)}"
        if col.get("primary_key") and enable_primary_keys:
            col_def += " PRIMARY KEY"
        columns.append(col_def)

    sql = f"CREATE TABLE IF NOT EXISTS {table['name']} (\n"
    sql += ",\n".join(f"  {col}" for col in columns)
    sql += "\n);"
    return sql


def get_all_create_table_sql(
    dialect: str = "standard",
    enable_primary_keys: bool = True,
    enable_foreign_keys: bool = True,
) -> str:
    full_table_order = CORE_TABLE_ORDER + get_extended_table_order()
    return "\n\n".join(
        get_create_table_sql(table_name, dialect, enable_primary_keys, enable_foreign_keys)
        for table_name in full_table_order
    )


class TPCDISchemaManager:
    def __init__(self, include_extensions: bool = True):
        self.include_extensions = include_extensions
        self.tables = TABLES
        self.core_table_order = CORE_TABLE_ORDER.copy()
        self.table_order = (
            self.core_table_order + get_extended_table_order() if include_extensions else self.core_table_order
        )

    def create_schema(self, connection: Any, dialect: str = "duckdb") -> None:
        logger.info(f"Creating TPC-DI schema for {dialect} dialect")
        ddl_statements = self.get_create_table_ddl(dialect)

        for statement in ddl_statements:
            try:
                if hasattr(connection, "execute"):
                    connection.execute(statement)
                elif hasattr(connection, "exec_driver_sql"):
                    connection.exec_driver_sql(statement)
                else:
                    connection.query(statement)
            except Exception as e:
                logger.error(f"Failed to execute DDL: {statement}")
                logger.error(f"Error: {e}")
                raise

        logger.info(f"Successfully created {len(ddl_statements)} tables")

    def get_create_table_ddl(self, dialect: str = "standard") -> list[str]:
        statements = []

        for table_name in self.table_order:
            sql = get_create_table_sql(table_name, "standard")

            if dialect != "standard":
                statements.append(
                    translate_sql_query(sql, dialect, source_dialect="postgres", identify=False, scope=SCHEMA_DDL_SCOPE)
                )
            else:
                statements.append(sql)

        return statements

    def translate_schema(self, from_dialect: str, to_dialect: str) -> list[str]:
        translated = []

        for sql in self.get_create_table_ddl(from_dialect):
            translated.append(
                translate_sql_query(
                    sql, to_dialect, source_dialect=from_dialect, identify=False, scope=SCHEMA_DDL_SCOPE
                )
            )

        return translated

    def get_table_schema(self, table_name: str) -> dict[str, Any]:
        if table_name not in self.tables:
            raise ValueError(f"Unknown table: {table_name}")
        return self.tables[table_name]

    def get_column_names(self, table_name: str) -> list[str]:
        schema = self.get_table_schema(table_name)
        return [col["name"] for col in schema["columns"]]

    def get_primary_key(self, table_name: str) -> Optional[str]:
        schema = self.get_table_schema(table_name)
        for col in schema["columns"]:
            if col.get("primary_key"):
                return col["name"]
        return None

    def create_foreign_key_constraints(self, connection: Any, dialect: str = "duckdb") -> None:
        if not self.include_extensions:
            logger.info("Skipping foreign key constraints - extensions not included")
            return

        logger.info("Creating foreign key constraints...")
        constraint_count = 0

        for table_name, fk_list in get_foreign_key_constraints().items():
            if table_name not in self.table_order:
                continue

            for fk in fk_list:
                constraint_sql = (
                    f"ALTER TABLE {table_name} "
                    f"ADD CONSTRAINT {fk['constraint_name']} "
                    f"FOREIGN KEY ({fk['column']}) "
                    f"REFERENCES {fk['references_table']}({fk['references_column']})"
                )

                try:
                    if hasattr(connection, "execute"):
                        connection.execute(constraint_sql)
                    elif hasattr(connection, "exec_driver_sql"):
                        connection.exec_driver_sql(constraint_sql)
                    else:
                        connection.query(constraint_sql)
                    constraint_count += 1
                except Exception as e:
                    logger.warning(f"Failed to create constraint {fk['constraint_name']}: {e}")

        logger.info(f"Successfully created {constraint_count} foreign key constraints")

    def get_table_count(self) -> int:
        return len(self.table_order)

    def get_core_tables(self) -> list[str]:
        return self.core_table_order.copy()

    def get_extended_tables(self) -> list[str]:
        if self.include_extensions:
            return get_extended_table_order()
        return []

    def drop_schema(self, connection: Any, if_exists: bool = True) -> None:
        for table_name in reversed(self.table_order):
            if_exists_clause = "IF EXISTS " if if_exists else ""
            drop_sql = f"DROP TABLE {if_exists_clause}{table_name}"

            try:
                if hasattr(connection, "execute"):
                    connection.execute(drop_sql)
                elif hasattr(connection, "exec_driver_sql"):
                    connection.exec_driver_sql(drop_sql)
                else:
                    connection.query(drop_sql)
            except Exception as e:
                if not if_exists:
                    logger.error(f"Failed to drop table {table_name}: {e}")
                    raise
