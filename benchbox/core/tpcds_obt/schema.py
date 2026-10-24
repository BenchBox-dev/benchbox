from __future__ import annotations

import datetime
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from benchbox.core.tpcds.schema.models import DataType, Table
from benchbox.core.tpcds.schema.registry import TABLES_BY_NAME

OBT_TABLE_NAME = "tpcds_sales_returns_obt"
DEFAULT_MODE = "full"
ALLOWED_MODES = {DEFAULT_MODE, "minimal"}
CHANNELS = ("store", "web", "catalog")
INCOME_BAND_TABLE = "income_band"
INCOME_BAND_KEY = "hd_income_band_sk"
INCOME_BAND_PRIMARY_KEY = "ib_income_band_sk"
INCOME_BAND_COLUMNS_BY_NAME = ("ib_lower_bound", "ib_upper_bound")
_SOURCE_REFERENCE = re.compile(r"^(\w+)\.(\w+)$")


@dataclass(frozen=True)
class OBTColumn:
    name: str
    data_type: DataType
    size: int | None = None
    nullable: bool = True
    primary_key: bool = False
    source_table: str | None = None
    source_column: str | None = None
    role: str | None = None
    description: str | None = None

    def sql_type(self) -> str:
        if self.data_type in (DataType.VARCHAR, DataType.CHAR) and self.size is not None:
            return f"{self.data_type.value}({self.size})"
        return self.data_type.value


@dataclass(frozen=True)
class ChannelSpec:
    name: str
    sales_table: str
    sales_alias: str
    returns_table: str
    returns_alias: str
    returns_join: str

    @property
    def aliases(self) -> dict[str, str]:
        return {self.sales_alias: self.sales_table, self.returns_alias: self.returns_table}


@dataclass(frozen=True)
class FactSpec:
    column: OBTColumn
    sources: dict[str, str]


@dataclass(frozen=True)
class DimensionRole:
    name: str
    table: Table
    prefix: str
    description: str | None = None
    keys: dict[str, str] | None = None
    via_role: str | None = None
    via_column: str | None = None
    minimal: bool = False
    income_band: bool = False

    @property
    def primary_key(self) -> str:
        return next(column.name for column in self.table.columns if column.primary_key)


@dataclass(frozen=True)
class OBTCoverage:
    sold_date_from: datetime.date
    sold_date_to: datetime.date
    every_item_sold: bool


@dataclass(frozen=True)
class OBTTable:
    name: str
    columns: tuple[OBTColumn, ...]

    def get_primary_key(self) -> list[str]:
        return [col.name for col in self.columns if col.primary_key]

    def get_create_table_sql(self) -> str:
        column_defs: list[str] = []
        pk_columns = self.get_primary_key()

        for col in self.columns:
            col_def = f"{col.name} {col.sql_type()}"
            if not col.nullable:
                col_def += " NOT NULL"
            column_defs.append(col_def)

        if pk_columns:
            column_defs.append(f"PRIMARY KEY ({', '.join(pk_columns)})")

        sql = f"CREATE TABLE {self.name} (\n    "
        sql += ",\n    ".join(column_defs)
        sql += "\n);"
        return sql


@cache
def _load_model() -> dict[str, Any]:
    with (Path(__file__).with_name("model.yaml")).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _fact_from_spec(spec: dict[str, Any]) -> FactSpec:
    column = OBTColumn(
        name=spec["name"],
        data_type=DataType[spec["data_type"]],
        size=spec.get("size"),
        nullable=spec.get("nullable", True),
        role="fact",
        description=spec.get("description"),
    )
    return FactSpec(column=column, sources=dict(spec["sources"]))


def _role_from_spec(spec: dict[str, Any]) -> DimensionRole:
    via = spec.get("via") or {}
    return DimensionRole(
        name=spec["name"],
        table=TABLES_BY_NAME[spec["table"]],
        prefix=spec["prefix"],
        description=spec.get("description"),
        keys=spec.get("keys"),
        via_role=via.get("role"),
        via_column=via.get("column"),
        minimal=spec.get("minimal", False),
        income_band=spec.get("income_band", False),
    )


@cache
def get_channels() -> dict[str, ChannelSpec]:
    return {
        name: ChannelSpec(
            name=name,
            sales_table=spec["sales"]["table"],
            sales_alias=spec["sales"]["alias"],
            returns_table=spec["returns"]["table"],
            returns_alias=spec["returns"]["alias"],
            returns_join=spec["returns"]["on"],
        )
        for name, spec in _load_model()["channels"].items()
    }


@cache
def get_fact_specs() -> tuple[FactSpec, ...]:
    return tuple(_fact_from_spec(spec) for spec in _load_model()["facts"])


@cache
def get_dimension_roles(mode: str = DEFAULT_MODE) -> tuple[DimensionRole, ...]:
    mode_lower = mode.lower()
    if mode_lower not in ALLOWED_MODES:
        raise ValueError(f"Unsupported mode '{mode}'. Allowed modes: {sorted(ALLOWED_MODES)}")
    roles = tuple(_role_from_spec(spec) for spec in _load_model()["roles"])
    if mode_lower == "minimal":
        roles = tuple(role for role in roles if role.minimal)
    names = {role.name for role in roles}
    for role in roles:
        if (role.keys is None) == (role.via_role is None):
            raise ValueError(f"Role {role.name} must declare exactly one of 'keys' and 'via'")
        if role.via_role is not None and role.via_role not in names:
            raise ValueError(f"Role {role.name} joins through {role.via_role}, which is not in mode {mode_lower}")
    return roles


@cache
def get_coverage() -> OBTCoverage:
    spec = _load_model()["coverage"]
    date_from, date_to = spec["sold_date_range"]
    return OBTCoverage(sold_date_from=date_from, sold_date_to=date_to, every_item_sold=bool(spec["every_item_sold"]))


def source_tables(channels: Iterable[str], mode: str = DEFAULT_MODE) -> set[str]:
    selected = list(channels)
    channel_specs = get_channels()
    tables: set[str] = {INCOME_BAND_TABLE}
    for channel in selected:
        spec = channel_specs[channel]
        tables.update({spec.sales_table, spec.returns_table})
    for role in get_dimension_roles(mode):
        if role.via_role is not None or any(channel in (role.keys or {}) for channel in selected):
            tables.add(role.table.name)
    return tables


def _prefixed_columns(role: DimensionRole) -> list[OBTColumn]:
    return [
        OBTColumn(
            name=f"{role.prefix}{column.name.lower()}",
            data_type=column.data_type,
            size=column.size,
            nullable=True,
            source_table=role.table.name,
            source_column=column.name,
            role=role.name,
            description=role.description,
        )
        for column in role.table.columns
    ]


def _income_band_columns(role: DimensionRole) -> list[OBTColumn]:
    band = TABLES_BY_NAME[INCOME_BAND_TABLE]
    return [
        OBTColumn(
            name=f"{role.prefix}{name}",
            data_type=band_column.data_type,
            size=band_column.size,
            nullable=True,
            source_table=INCOME_BAND_TABLE,
            source_column=name,
            role=role.name,
            description=f"{role.description} income band {name.removeprefix('ib_').replace('_', ' ')}",
        )
        for name in INCOME_BAND_COLUMNS_BY_NAME
        for band_column in band.columns
        if band_column.name == name
    ]


def _fact_lineage(spec: FactSpec) -> OBTColumn:
    tables: list[str] = []
    columns: list[str] = []
    for channel in CHANNELS:
        source = spec.sources.get(channel)
        match = _SOURCE_REFERENCE.match(source) if source else None
        if match is None:
            continue
        alias, column = match.groups()
        tables.append(get_channels()[channel].aliases[alias])
        columns.append(column)
    return OBTColumn(
        name=spec.column.name,
        data_type=spec.column.data_type,
        size=spec.column.size,
        nullable=spec.column.nullable,
        source_table="|".join(tables) or None,
        source_column="|".join(columns) or None,
        role="fact",
        description=spec.column.description,
    )


@cache
def get_obt_columns(mode: str = DEFAULT_MODE) -> tuple[OBTColumn, ...]:
    roles = get_dimension_roles(mode)
    columns: list[OBTColumn] = [_fact_lineage(spec) for spec in get_fact_specs()]
    for role in roles:
        columns.extend(_prefixed_columns(role))
    for role in roles:
        if role.income_band:
            columns.extend(_income_band_columns(role))

    duplicates = sorted(name for name, count in Counter(col.name for col in columns).items() if count > 1)
    if duplicates:
        raise ValueError(f"Duplicate column names detected in OBT schema: {duplicates}")

    return tuple(columns)


@cache
def get_obt_table(mode: str = DEFAULT_MODE) -> OBTTable:
    return OBTTable(name=OBT_TABLE_NAME, columns=get_obt_columns(mode))


def get_column_lineage(mode: str = DEFAULT_MODE) -> dict[str, dict[str, str | None]]:
    return {
        col.name: {
            "source_table": col.source_table,
            "source_column": col.source_column,
            "role": col.role,
            "description": col.description,
        }
        for col in get_obt_columns(mode)
    }


TPCDS_OBT_TABLE = get_obt_table()

__all__ = [
    "ALLOWED_MODES",
    "CHANNELS",
    "INCOME_BAND_KEY",
    "INCOME_BAND_PRIMARY_KEY",
    "INCOME_BAND_TABLE",
    "DEFAULT_MODE",
    "ChannelSpec",
    "DimensionRole",
    "FactSpec",
    "OBTColumn",
    "OBTCoverage",
    "OBT_TABLE_NAME",
    "TPCDS_OBT_TABLE",
    "get_channels",
    "get_column_lineage",
    "get_coverage",
    "get_dimension_roles",
    "get_fact_specs",
    "get_obt_columns",
    "get_obt_table",
    "source_tables",
]
