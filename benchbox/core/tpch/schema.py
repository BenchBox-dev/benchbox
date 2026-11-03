from enum import Enum
from pathlib import Path
from typing import Any, NamedTuple, Optional

import yaml

from benchbox.core.schema_primitives import BaseSchemaTable, get_fk_ordered_table_names
from benchbox.core.tuning import BenchmarkTunings, TableTuning, TuningColumn
from benchbox.utils.questdb_subquery_schemas import register_benchmark_schema


class DataType(Enum):
    INTEGER = "INTEGER"
    DECIMAL = "DECIMAL(15,2)"
    VARCHAR = "VARCHAR"
    CHAR = "CHAR"
    DATE = "DATE"


class Column(NamedTuple):
    name: str
    data_type: DataType
    size: Optional[int] = None
    nullable: bool = False
    primary_key: bool = False
    foreign_key: Optional[tuple[str, str]] = None

    def get_sql_type(self) -> str:
        if self.data_type in (DataType.VARCHAR, DataType.CHAR) and self.size is not None:
            return f"{self.data_type.value}({self.size})"
        return self.data_type.value


class Table(BaseSchemaTable):
    def __init__(self, name: str, columns: list[Column]) -> None:
        super().__init__(name, columns)


def _load_schema_specs() -> dict[str, Any]:
    with (Path(__file__).with_name("schema_specs.yaml")).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _make_column(spec: dict[str, Any]) -> Column:
    foreign_key = spec.get("foreign_key")
    return Column(
        spec["name"],
        DataType[spec["data_type"]],
        size=spec.get("size"),
        nullable=spec.get("nullable", False),
        primary_key=spec.get("primary_key", False),
        foreign_key=tuple(foreign_key) if foreign_key else None,
    )


def _make_table(spec: dict[str, Any]) -> Table:
    return Table(spec["name"], [_make_column(column) for column in spec["columns"]])


_SCHEMA_SPECS = _load_schema_specs()
_TABLE_DEFS = {entry["id"]: _make_table(entry) for entry in _SCHEMA_SPECS["tables"]}

REGION = _TABLE_DEFS["REGION"]
NATION = _TABLE_DEFS["NATION"]
SUPPLIER = _TABLE_DEFS["SUPPLIER"]
PART = _TABLE_DEFS["PART"]
PARTSUPP = _TABLE_DEFS["PARTSUPP"]
CUSTOMER = _TABLE_DEFS["CUSTOMER"]
ORDERS = _TABLE_DEFS["ORDERS"]
LINEITEM = _TABLE_DEFS["LINEITEM"]


TABLES = [
    REGION,
    NATION,
    SUPPLIER,
    PART,
    PARTSUPP,
    CUSTOMER,
    ORDERS,
    LINEITEM,
]

TABLES_BY_NAME = {table.name: table for table in TABLES}

register_benchmark_schema(TABLES)


def get_table(name: str) -> Table:
    name_lower = name.lower()
    if name_lower not in TABLES_BY_NAME:
        raise ValueError(f"Invalid table name: {name}")
    return TABLES_BY_NAME[name_lower]


def get_table_loading_order() -> list[str]:
    return get_fk_ordered_table_names(TABLES)


def get_create_all_tables_sql(enable_primary_keys: bool = True, enable_foreign_keys: bool = True) -> str:
    import logging

    logger = logging.getLogger(__name__)

    table_sqls = []
    logger.debug(
        f"Generating SQL for {len(TABLES)} TPC-H tables "
        f"(primary_keys={enable_primary_keys}, foreign_keys={enable_foreign_keys})"
    )

    for i, table in enumerate(TABLES, 1):
        try:
            logger.debug(f"  [{i}/{len(TABLES)}] Generating SQL for table: {table.name}")
            sql = table.get_create_table_sql(
                enable_primary_keys=enable_primary_keys,
                enable_foreign_keys=enable_foreign_keys,
            )
            table_sqls.append(sql)
            logger.debug(f"  [{i}/{len(TABLES)}] ✓ Generated {len(sql)} characters for {table.name}")
        except Exception as e:
            logger.error(f"  [{i}/{len(TABLES)}] ✗ Failed to generate SQL for table {table.name}: {e}")
            raise RuntimeError(f"Schema generation failed for table {table.name}: {e}") from e

    result = "\n\n".join(table_sqls)
    logger.debug(f"Schema generation complete: {len(result)} total characters, {len(table_sqls)} tables")
    return result


def get_tunings() -> BenchmarkTunings:
    tunings = BenchmarkTunings("tpch")

    lineitem_tuning = TableTuning(
        table_name="lineitem",
        partitioning=[TuningColumn("l_shipdate", "DATE", 1)],
        clustering=[TuningColumn("l_orderkey", "INTEGER", 1)],
        sorting=[
            TuningColumn("l_linenumber", "INTEGER", 1),
            TuningColumn("l_partkey", "INTEGER", 2),
        ],
    )
    tunings.add_table_tuning(lineitem_tuning)

    orders_tuning = TableTuning(
        table_name="orders",
        partitioning=[TuningColumn("o_orderdate", "DATE", 1)],
        clustering=[TuningColumn("o_custkey", "INTEGER", 1)],
        sorting=[TuningColumn("o_totalprice", "DECIMAL", 1)],
    )
    tunings.add_table_tuning(orders_tuning)

    partsupp_tuning = TableTuning(
        table_name="partsupp",
        distribution=[TuningColumn("ps_partkey", "INTEGER", 1)],
        sorting=[
            TuningColumn("ps_suppkey", "INTEGER", 1),
            TuningColumn("ps_availqty", "INTEGER", 2),
        ],
    )
    tunings.add_table_tuning(partsupp_tuning)

    customer_tuning = TableTuning(
        table_name="customer",
        distribution=[TuningColumn("c_custkey", "INTEGER", 1)],
        sorting=[TuningColumn("c_mktsegment", "CHAR", 1)],
    )
    tunings.add_table_tuning(customer_tuning)

    supplier_tuning = TableTuning(
        table_name="supplier",
        distribution=[TuningColumn("s_suppkey", "INTEGER", 1)],
        sorting=[TuningColumn("s_nationkey", "INTEGER", 1)],
    )
    tunings.add_table_tuning(supplier_tuning)

    part_tuning = TableTuning(
        table_name="part",
        distribution=[TuningColumn("p_partkey", "INTEGER", 1)],
        sorting=[
            TuningColumn("p_type", "VARCHAR", 1),
            TuningColumn("p_size", "INTEGER", 2),
        ],
    )
    tunings.add_table_tuning(part_tuning)

    nation_tuning = TableTuning(table_name="nation", sorting=[TuningColumn("n_nationkey", "INTEGER", 1)])
    tunings.add_table_tuning(nation_tuning)

    region_tuning = TableTuning(table_name="region", sorting=[TuningColumn("r_regionkey", "INTEGER", 1)])
    tunings.add_table_tuning(region_tuning)

    return tunings
