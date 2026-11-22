import logging
from pathlib import Path
from typing import Any, cast

import yaml

from benchbox.sql_compat.rules.schema_emit.tpcdi_ddl import FLAG_BOOLEAN_PLATFORMS, TIME_AS_STRING_TYPES
from benchbox.utils.dialect_utils import translate_ddl

logger = logging.getLogger(__name__)

_FLAG_TYPE = "BOOLEAN"
_FLAG_INTEGER_TYPE = "SMALLINT"
_TIME_TYPE = "TIME"


def _load_schema_specs() -> dict[str, Any]:
    with (Path(__file__).with_name("schema_specs.yaml")).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


_SCHEMA_SPECS = _load_schema_specs()
_CORE_TABLES = {entry["id"]: entry for entry in _SCHEMA_SPECS["core_tables"]}
_STAGING_TABLES = {entry["id"]: entry for entry in _SCHEMA_SPECS["staging_tables"]}
globals().update({symbol: entry["schema"] for symbol, entry in (_CORE_TABLES | _STAGING_TABLES).items()})

CORE_TABLES = {entry["key"]: entry["schema"] for entry in _CORE_TABLES.values()}
STAGING_TABLES = {entry["key"]: entry["schema"] for entry in _STAGING_TABLES.values()}
TABLES = CORE_TABLES | STAGING_TABLES
CORE_TABLE_ORDER = list(CORE_TABLES)
STAGING_TABLE_ORDER = list(STAGING_TABLES)
TABLE_ORDER = CORE_TABLE_ORDER + STAGING_TABLE_ORDER


def _flag_type_map(platform: str) -> dict[str, str]:
    return {} if platform in FLAG_BOOLEAN_PLATFORMS else {_FLAG_TYPE: _FLAG_INTEGER_TYPE}


def _time_type_map(platform: str) -> dict[str, str]:
    return {_TIME_TYPE: TIME_AS_STRING_TYPES[platform]} if platform in TIME_AS_STRING_TYPES else {}


def type_map_for_dialect(dialect: str) -> dict[str, str]:
    platform = dialect.lower()
    return _flag_type_map(platform) | _time_type_map(platform)


def _create_table_sql(table_name: str, type_map: dict[str, str]) -> str:
    if table_name not in TABLES:
        raise ValueError(f"Unknown table: {table_name}")

    table = TABLES[table_name]
    columns = []

    for col in cast(list, table["columns"]):
        column_type = cast(str, col["type"])
        col_def = f"{cast(str, col['name'])} {type_map.get(column_type, column_type)}"
        if col.get("not_null"):
            col_def += " NOT NULL"
        columns.append(col_def)

    sql = f"CREATE TABLE IF NOT EXISTS {table['name']} (\n"
    sql += ",\n".join(f"  {col}" for col in columns)
    sql += "\n);"
    return sql


def get_create_table_sql(table_name: str, dialect: str = "standard") -> str:
    return _create_table_sql(table_name, type_map_for_dialect(dialect))


def get_all_create_table_sql(dialect: str = "standard") -> str:
    return "\n\n".join(get_create_table_sql(table_name, dialect) for table_name in TABLE_ORDER)


def _translate_create_table(table_name: str, dialect: str) -> str:
    platform = dialect.lower()
    portable = translate_ddl(
        _create_table_sql(table_name, {}),
        "postgres",
        "postgres",
        identify=False,
        type_map=_flag_type_map(platform),
    )[0]
    return translate_ddl(
        portable,
        dialect,
        "postgres",
        identify=False,
        type_map=_time_type_map(platform),
    )[0]


class TPCDISchemaManager:
    def __init__(self) -> None:
        self.tables = TABLES
        self.table_order = TABLE_ORDER.copy()

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
            if dialect != "standard":
                statements.append(_translate_create_table(table_name, dialect))
            else:
                statements.append(get_create_table_sql(table_name, dialect))

        return statements

    def get_table_schema(self, table_name: str) -> dict[str, Any]:
        if table_name not in self.tables:
            raise ValueError(f"Unknown table: {table_name}")
        return self.tables[table_name]

    def get_column_names(self, table_name: str) -> list[str]:
        schema = self.get_table_schema(table_name)
        return [col["name"] for col in schema["columns"]]

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
