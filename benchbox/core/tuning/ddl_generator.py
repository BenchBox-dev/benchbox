# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import (
        PlatformOptimizationConfiguration,
        TableTuning,
    )


class ColumnNullability(Enum):
    NULLABLE = "nullable"
    NOT_NULL = "not_null"
    DEFAULT = "default"


@dataclass
class ColumnDefinition:
    name: str
    data_type: str
    nullable: ColumnNullability = ColumnNullability.DEFAULT
    default_value: str | None = None
    primary_key: bool = False
    comment: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "data_type": self.data_type,
            "nullable": self.nullable.value,
        }
        if self.default_value is not None:
            result["default_value"] = self.default_value
        if self.primary_key:
            result["primary_key"] = True
        if self.comment is not None:
            result["comment"] = self.comment
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ColumnDefinition:
        nullable = ColumnNullability.DEFAULT
        if "nullable" in data:
            nullable = ColumnNullability(data["nullable"])

        return cls(
            name=data["name"],
            data_type=data["data_type"],
            nullable=nullable,
            default_value=data.get("default_value"),
            primary_key=data.get("primary_key", False),
            comment=data.get("comment"),
        )


_DISTRIBUTE_AFTER_PARTITION_PLATFORMS = frozenset({"starrocks", "doris"})


@dataclass
class TuningClauses:
    partition_by: str | None = None

    cluster_by: str | None = None

    distribute_by: str | None = None

    sort_by: str | None = None

    primary_key: str | None = None

    order_by: str | None = None

    table_properties: dict[str, str] = field(default_factory=dict)

    table_options: dict[str, Any] = field(default_factory=dict)

    additional_clauses: list[str] = field(default_factory=list)

    post_create_statements: list[str] = field(default_factory=list)

    platform: str | None = None

    def is_empty(self) -> bool:
        return (
            self.partition_by is None
            and self.cluster_by is None
            and self.distribute_by is None
            and self.sort_by is None
            and self.primary_key is None
            and self.order_by is None
            and not self.table_properties
            and not self.table_options
            and not self.additional_clauses
            and not self.post_create_statements
        )

    def get_inline_clauses(self) -> list[str]:
        clauses = []

        if self.primary_key:
            clauses.append(self.primary_key)

        if self.platform == "doris":
            if self.sort_by:
                clauses.append(self.sort_by)
            if self.partition_by:
                clauses.append(self.partition_by)
            if self.distribute_by:
                clauses.append(self.distribute_by)
        elif self.platform in _DISTRIBUTE_AFTER_PARTITION_PLATFORMS:
            if self.partition_by:
                clauses.append(self.partition_by)
            if self.distribute_by:
                clauses.append(self.distribute_by)
        else:
            if self.distribute_by:
                clauses.append(self.distribute_by)

            if self.partition_by:
                clauses.append(self.partition_by)

        if self.cluster_by:
            clauses.append(self.cluster_by)

        if self.sort_by and self.platform != "doris":
            clauses.append(self.sort_by)

        if self.order_by:
            clauses.append(self.order_by)

        clauses.extend(self.additional_clauses)

        return clauses

    def get_table_properties_clause(self) -> str | None:
        if not self.table_properties:
            return None

        props = ", ".join(f"'{k}' = '{v}'" for k, v in sorted(self.table_properties.items()))
        return f"TBLPROPERTIES ({props})"

    def get_table_options_clause(self) -> str | None:
        if not self.table_options:
            return None

        def format_value(v: Any) -> str:
            if isinstance(v, bool):
                return "true" if v else "false"
            if isinstance(v, str):
                return f"'{v}'"
            return str(v)

        opts = ", ".join(f"{k} = {format_value(v)}" for k, v in sorted(self.table_options.items()))
        return f"OPTIONS ({opts})"

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}

        if self.partition_by:
            result["partition_by"] = self.partition_by
        if self.cluster_by:
            result["cluster_by"] = self.cluster_by
        if self.distribute_by:
            result["distribute_by"] = self.distribute_by
        if self.sort_by:
            result["sort_by"] = self.sort_by
        if self.primary_key:
            result["primary_key"] = self.primary_key
        if self.order_by:
            result["order_by"] = self.order_by
        if self.table_properties:
            result["table_properties"] = self.table_properties
        if self.table_options:
            result["table_options"] = self.table_options
        if self.additional_clauses:
            result["additional_clauses"] = self.additional_clauses
        if self.post_create_statements:
            result["post_create_statements"] = self.post_create_statements
        if self.platform and not self.is_empty():
            result["platform"] = self.platform

        return result

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TuningClauses:
        return cls(
            partition_by=data.get("partition_by"),
            cluster_by=data.get("cluster_by"),
            distribute_by=data.get("distribute_by"),
            sort_by=data.get("sort_by"),
            primary_key=data.get("primary_key"),
            order_by=data.get("order_by"),
            table_properties=data.get("table_properties", {}),
            table_options=data.get("table_options", {}),
            additional_clauses=data.get("additional_clauses", []),
            post_create_statements=data.get("post_create_statements", []),
            platform=data.get("platform"),
        )

    def merge(self, other: TuningClauses) -> TuningClauses:
        return TuningClauses(
            partition_by=other.partition_by or self.partition_by,
            cluster_by=other.cluster_by or self.cluster_by,
            distribute_by=other.distribute_by or self.distribute_by,
            sort_by=other.sort_by or self.sort_by,
            primary_key=other.primary_key or self.primary_key,
            order_by=other.order_by or self.order_by,
            table_properties={**self.table_properties, **other.table_properties},
            table_options={**self.table_options, **other.table_options},
            additional_clauses=[*self.additional_clauses, *other.additional_clauses],
            post_create_statements=[*self.post_create_statements, *other.post_create_statements],
            platform=other.platform or self.platform,
        )


@runtime_checkable
class DDLGenerator(Protocol):
    @property
    def platform_name(self) -> str: ...

    def generate_tuning_clauses(
        self,
        table_tuning: TableTuning | None,
        platform_opts: PlatformOptimizationConfiguration | None = None,
    ) -> TuningClauses: ...

    def generate_create_table_ddl(
        self,
        table_name: str,
        columns: list[ColumnDefinition],
        tuning: TuningClauses | None = None,
        if_not_exists: bool = False,
        schema: str | None = None,
    ) -> str: ...

    def get_post_load_statements(
        self,
        table_name: str,
        tuning: TuningClauses,
        schema: str | None = None,
    ) -> list[str]: ...

    def supports_tuning_type(self, tuning_type: str) -> bool: ...


class BaseDDLGenerator(ABC):
    IDENTIFIER_QUOTE: str = '"'
    SUPPORTS_IF_NOT_EXISTS: bool = True
    STATEMENT_TERMINATOR: str = ";"

    SUPPORTED_TUNING_TYPES: frozenset[str] = frozenset()

    @property
    @abstractmethod
    def platform_name(self) -> str: ...

    def quote_identifier(self, identifier: str) -> str:
        if identifier.isidentifier() and identifier.lower() == identifier:
            return identifier
        return f"{self.IDENTIFIER_QUOTE}{identifier}{self.IDENTIFIER_QUOTE}"

    def format_qualified_name(self, table_name: str, schema: str | None = None) -> str:
        if schema:
            return f"{self.quote_identifier(schema)}.{self.quote_identifier(table_name)}"
        return self.quote_identifier(table_name)

    def generate_column_list(
        self,
        columns: list[ColumnDefinition],
        include_constraints: bool = True,
    ) -> str:
        col_defs = []
        for col in columns:
            col_def = self._format_column_definition(col, include_constraints)
            col_defs.append(col_def)

        return ",\n    ".join(col_defs)

    def _format_column_definition(
        self,
        column: ColumnDefinition,
        include_constraints: bool = True,
    ) -> str:
        parts = [self.quote_identifier(column.name), column.data_type]

        if include_constraints:
            if column.nullable == ColumnNullability.NOT_NULL:
                parts.append("NOT NULL")
            elif column.nullable == ColumnNullability.NULLABLE:
                parts.append("NULL")

            if column.default_value is not None:
                parts.append(f"DEFAULT {column.default_value}")

            if column.primary_key:
                parts.append("PRIMARY KEY")

        return " ".join(parts)

    @abstractmethod
    def generate_tuning_clauses(
        self,
        table_tuning: TableTuning | None,
        platform_opts: PlatformOptimizationConfiguration | None = None,
    ) -> TuningClauses: ...

    def generate_create_table_ddl(
        self,
        table_name: str,
        columns: list[ColumnDefinition],
        tuning: TuningClauses | None = None,
        if_not_exists: bool = False,
        schema: str | None = None,
    ) -> str:
        parts = ["CREATE TABLE"]

        if if_not_exists and self.SUPPORTS_IF_NOT_EXISTS:
            parts.append("IF NOT EXISTS")

        parts.append(self.format_qualified_name(table_name, schema))

        statement = " ".join(parts)

        col_list = self.generate_column_list(columns)
        statement = f"{statement} (\n    {col_list}\n)"

        if tuning and not tuning.is_empty():
            logger.debug(
                "Applying tuning to table %s: %s",
                table_name,
                tuning.to_dict(),
            )
            inline_clauses = tuning.get_inline_clauses()
            if inline_clauses:
                statement = f"{statement}\n{chr(10).join(inline_clauses)}"

            props_clause = tuning.get_table_properties_clause()
            if props_clause:
                statement = f"{statement}\n{props_clause}"

            opts_clause = tuning.get_table_options_clause()
            if opts_clause:
                statement = f"{statement}\n{opts_clause}"

        statement = f"{statement}{self.STATEMENT_TERMINATOR}"

        return statement

    def get_post_load_statements(
        self,
        table_name: str,
        tuning: TuningClauses,
        schema: str | None = None,
    ) -> list[str]:
        if not tuning or not tuning.post_create_statements:
            return []

        qualified_name = self.format_qualified_name(table_name, schema)
        return [stmt.format(table_name=qualified_name) for stmt in tuning.post_create_statements]

    def supports_tuning_type(self, tuning_type: str) -> bool:
        return tuning_type.lower() in self.SUPPORTED_TUNING_TYPES


class NoOpDDLGenerator(BaseDDLGenerator):
    SUPPORTED_TUNING_TYPES: frozenset[str] = frozenset()

    def __init__(self, platform: str = "unknown"):
        self._platform_name = platform

    @property
    def platform_name(self) -> str:
        return self._platform_name

    def generate_tuning_clauses(
        self,
        table_tuning: TableTuning | None,
        platform_opts: PlatformOptimizationConfiguration | None = None,
    ) -> TuningClauses:
        return TuningClauses()


DDLGeneratorType = DDLGenerator | BaseDDLGenerator

_TUNING_FREE_PLATFORMS: frozenset[str] = frozenset(
    {
        "sqlite",
        "sqlite3",
        "pandas",
        "cudf",
        "dask",
        "polars",
        "datafusion",
        "pyspark",
    }
)

_DATAFRAME_SUFFIX_BASES: frozenset[str] = frozenset(
    {
        "pandas",
        "cudf",
        "dask",
        "polars",
        "datafusion",
        "pyspark",
        "lakesail",
        "databricks",
    }
)

_DATAFRAME_PREFIX_BASES: frozenset[str] = frozenset(
    {
        "pandas",
        "cudf",
        "dask",
        "polars",
        "datafusion",
        "pyspark",
    }
)

_DATAFRAME_KEY_PREFIX = "dataframe-"
_DATAFRAME_KEY_SUFFIX = "-df"


def _normalize_dataframe_platform_key(platform_lower: str) -> str:
    if platform_lower.startswith(_DATAFRAME_KEY_PREFIX):
        base = platform_lower[len(_DATAFRAME_KEY_PREFIX) :]
        return base if base in _DATAFRAME_PREFIX_BASES else platform_lower
    if platform_lower.endswith(_DATAFRAME_KEY_SUFFIX):
        base = platform_lower[: -len(_DATAFRAME_KEY_SUFFIX)]
        return base if base in _DATAFRAME_SUFFIX_BASES else platform_lower
    return platform_lower


def get_ddl_generator(platform_type: str) -> BaseDDLGenerator:
    from benchbox.core.tuning.generators.azure_synapse import AzureSynapseDDLGenerator
    from benchbox.core.tuning.generators.bigquery import BigQueryDDLGenerator
    from benchbox.core.tuning.generators.cedardb import CedarDBDDLGenerator
    from benchbox.core.tuning.generators.clickhouse import ClickHouseDDLGenerator
    from benchbox.core.tuning.generators.doris import DorisDDLGenerator
    from benchbox.core.tuning.generators.duckdb import DuckDBDDLGenerator
    from benchbox.core.tuning.generators.firebolt import FireboltDDLGenerator
    from benchbox.core.tuning.generators.pg_duckdb import PgDuckDBDDLGenerator
    from benchbox.core.tuning.generators.pg_mooncake import PgMooncakeDDLGenerator
    from benchbox.core.tuning.generators.postgresql import PostgreSQLDDLGenerator
    from benchbox.core.tuning.generators.questdb import QuestDBDDLGenerator
    from benchbox.core.tuning.generators.redshift import RedshiftDDLGenerator
    from benchbox.core.tuning.generators.snowflake import SnowflakeDDLGenerator
    from benchbox.core.tuning.generators.spark_family import DeltaDDLGenerator
    from benchbox.core.tuning.generators.starrocks import StarRocksDDLGenerator
    from benchbox.core.tuning.generators.timescaledb import TimescaleDBDDLGenerator
    from benchbox.core.tuning.generators.trino import AthenaDDLGenerator, TrinoDDLGenerator

    generators: dict[str, type[BaseDDLGenerator]] = {
        "doris": DorisDDLGenerator,
        "duckdb": DuckDBDDLGenerator,
        "snowflake": SnowflakeDDLGenerator,
        "bigquery": BigQueryDDLGenerator,
        "redshift": RedshiftDDLGenerator,
        "postgresql": PostgreSQLDDLGenerator,
        "timescaledb": TimescaleDBDDLGenerator,
        "starrocks": StarRocksDDLGenerator,
        "clickhouse": ClickHouseDDLGenerator,
        "clickhouse-local": ClickHouseDDLGenerator,
        "clickhouse-server": ClickHouseDDLGenerator,
        "clickhouse-cloud": ClickHouseDDLGenerator,
        "chdb": ClickHouseDDLGenerator,
        "firebolt": FireboltDDLGenerator,
        "azure_synapse": AzureSynapseDDLGenerator,
        "synapse": AzureSynapseDDLGenerator,
        "trino": TrinoDDLGenerator,
        "presto": TrinoDDLGenerator,
        "athena": AthenaDDLGenerator,
        "databricks": DeltaDDLGenerator,
        "spark": DeltaDDLGenerator,
        "delta": DeltaDDLGenerator,
        "fabric_warehouse": DeltaDDLGenerator,
        "questdb": QuestDBDDLGenerator,
        "pg-duckdb": PgDuckDBDDLGenerator,
        "pg_duckdb": PgDuckDBDDLGenerator,
        "pg-mooncake": PgMooncakeDDLGenerator,
        "pg_mooncake": PgMooncakeDDLGenerator,
        "cedardb": CedarDBDDLGenerator,
    }

    platform_lower = platform_type.lower()

    if platform_lower in generators:
        return generators[platform_lower]()

    normalized = _normalize_dataframe_platform_key(platform_lower)
    if normalized != platform_lower and normalized in generators:
        return generators[normalized]()

    if normalized not in _TUNING_FREE_PLATFORMS:
        logger.warning(
            "No DDL generator registered for platform %r; tuning clauses will be "
            "empty (NoOp fallback). If %r supports physical tuning, register it "
            "in get_ddl_generator().",
            platform_type,
            platform_type,
        )

    return NoOpDDLGenerator(platform_type)


__all__ = [
    "ColumnDefinition",
    "ColumnNullability",
    "TuningClauses",
    "DDLGenerator",
    "BaseDDLGenerator",
    "NoOpDDLGenerator",
    "DDLGeneratorType",
    "get_ddl_generator",
]
