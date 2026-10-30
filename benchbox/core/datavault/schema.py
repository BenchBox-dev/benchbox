from enum import Enum
from pathlib import Path
from typing import Any, NamedTuple, Optional

import yaml

from benchbox.core.schema_primitives import BaseSchemaTable
from benchbox.utils.questdb_subquery_schemas import register_benchmark_schema


class DataType(Enum):
    INTEGER = "INTEGER"
    DECIMAL = "DECIMAL(15,2)"
    VARCHAR = "VARCHAR"
    CHAR = "CHAR"
    DATE = "DATE"
    TIMESTAMP = "TIMESTAMP"
    HASHKEY = "VARCHAR(64)"


class Column(NamedTuple):
    name: str
    data_type: DataType
    size: Optional[int] = None
    nullable: bool = False
    primary_key: bool = False
    foreign_key: Optional[tuple[str, str]] = None

    def get_sql_type(self) -> str:
        if self.data_type == DataType.HASHKEY:
            return "VARCHAR(64)"
        if self.data_type in (DataType.VARCHAR, DataType.CHAR) and self.size is not None:
            return f"{self.data_type.value}({self.size})"
        return self.data_type.value


class Table(BaseSchemaTable):
    def __init__(self, name: str, columns: list[Column], table_type: str = "unknown") -> None:
        super().__init__(name, columns)
        self.table_type = table_type


def _column(spec: list[Any]) -> Column:
    name, data_type, size, nullable, primary_key, foreign_key = spec
    return Column(
        name,
        DataType[data_type],
        size=size,
        nullable=nullable,
        primary_key=primary_key,
        foreign_key=tuple(foreign_key) if foreign_key is not None else None,
    )


def _table(spec: dict[str, Any]) -> Table:
    return Table(spec["name"], [_column(column) for column in spec["columns"]], table_type=spec["table_type"])


def _load_schema_specs() -> dict[str, Any]:
    with (Path(__file__).with_name("schema_specs.yaml")).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


_SCHEMA_SPECS = _load_schema_specs()
_TABLES_BY_ID = {entry["id"]: _table(entry) for entry in _SCHEMA_SPECS["tables"]}
globals().update(_TABLES_BY_ID)

TABLES_BY_NAME = {table.name: table for table in _TABLES_BY_ID.values()}
HUBS = [TABLES_BY_NAME[name] for name in _SCHEMA_SPECS["groups"]["hubs"]]
LINKS = [TABLES_BY_NAME[name] for name in _SCHEMA_SPECS["groups"]["links"]]
SATELLITES = [TABLES_BY_NAME[name] for name in _SCHEMA_SPECS["groups"]["satellites"]]
TABLES = HUBS + LINKS + SATELLITES
register_benchmark_schema(TABLES)
LOADING_ORDER = list(_SCHEMA_SPECS["loading_order"])


def get_table(name: str) -> Table:
    name_lower = name.lower()
    if name_lower not in TABLES_BY_NAME:
        raise ValueError(f"Invalid table name: {name}. Valid tables: {list(TABLES_BY_NAME.keys())}")
    return TABLES_BY_NAME[name_lower]


def get_create_all_tables_sql(enable_primary_keys: bool = True, enable_foreign_keys: bool = True) -> str:
    import logging

    logger = logging.getLogger(__name__)
    table_sqls = []
    logger.debug(
        f"Generating SQL for {len(TABLES)} Data Vault tables "
        f"(primary_keys={enable_primary_keys}, foreign_keys={enable_foreign_keys})"
    )

    for i, table_name in enumerate(LOADING_ORDER, 1):
        table = TABLES_BY_NAME[table_name]
        try:
            logger.debug(f"  [{i}/{len(TABLES)}] Generating SQL for table: {table.name}")
            sql = table.get_create_table_sql(
                enable_primary_keys=enable_primary_keys,
                enable_foreign_keys=enable_foreign_keys,
            )
            table_sqls.append(sql)
            logger.debug(f"  [{i}/{len(TABLES)}] Generated {len(sql)} characters for {table.name}")
        except Exception as e:
            logger.error(f"  [{i}/{len(TABLES)}] Failed to generate SQL for table {table.name}: {e}")
            raise RuntimeError(f"Schema generation failed for table {table.name}: {e}") from e

    result = "\n\n".join(table_sqls)
    logger.debug(f"Schema generation complete: {len(result)} total characters, {len(table_sqls)} tables")
    return result


def get_table_loading_order() -> list[str]:
    return LOADING_ORDER.copy()
