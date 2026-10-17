# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING

from benchbox.core.tuning.ddl_generator import (
    BaseDDLGenerator,
    ColumnDefinition,
    TuningClauses,
)

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import (
        PlatformOptimizationConfiguration,
        TableTuning,
    )

logger = logging.getLogger(__name__)


class ConnectorType(str, Enum):
    HIVE = "hive"
    ICEBERG = "iceberg"
    DELTA = "delta"
    MEMORY = "memory"


class FileFormat(str, Enum):
    PARQUET = "PARQUET"
    ORC = "ORC"
    AVRO = "AVRO"
    JSON = "JSON"
    CSV = "CSV"


class TrinoDDLGenerator(BaseDDLGenerator):
    IDENTIFIER_QUOTE = '"'
    SUPPORTS_IF_NOT_EXISTS = True
    STATEMENT_TERMINATOR = ";"

    SUPPORTED_TUNING_TYPES = frozenset({"partitioning", "distribution", "sorting", "clustering"})

    def __init__(
        self,
        connector: ConnectorType | str = ConnectorType.HIVE,
        default_format: FileFormat = FileFormat.PARQUET,
        default_bucket_count: int = 32,
        external: bool = False,
        location: str | None = None,
    ):
        self._connector = ConnectorType(connector) if isinstance(connector, str) else connector
        self._default_format = default_format
        self._default_bucket_count = default_bucket_count
        self._external = external
        self._location = location

    @property
    def platform_name(self) -> str:
        return "trino"

    @property
    def connector(self) -> ConnectorType:
        return self._connector

    def generate_tuning_clauses(
        self,
        table_tuning: TableTuning | None,
        platform_opts: PlatformOptimizationConfiguration | None = None,
    ) -> TuningClauses:
        clauses = TuningClauses()

        if not table_tuning:
            clauses.table_properties["format"] = f"'{self._default_format.value}'"
            return clauses

        from benchbox.core.tuning.interface import TuningType

        clauses.table_properties["format"] = f"'{self._default_format.value}'"

        partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
        if partition_columns:
            sorted_cols = sorted(partition_columns, key=lambda c: c.order)

            if self._connector == ConnectorType.ICEBERG:
                transforms = self._generate_iceberg_partitioning(sorted_cols, platform_opts)
                clauses.table_properties["partitioning"] = transforms
            else:
                col_names = [f"'{c.name}'" for c in sorted_cols]
                clauses.table_properties["partitioned_by"] = f"ARRAY[{', '.join(col_names)}]"

        distribution_columns = table_tuning.get_columns_by_type(TuningType.DISTRIBUTION)
        if distribution_columns:
            if self._connector == ConnectorType.HIVE:
                sorted_cols = sorted(distribution_columns, key=lambda c: c.order)
                col_names = [f"'{c.name}'" for c in sorted_cols]
                clauses.table_properties["bucketed_by"] = f"ARRAY[{', '.join(col_names)}]"

                bucket_count = self._default_bucket_count
                if platform_opts and hasattr(platform_opts, "bucket_count"):
                    bucket_count = platform_opts.bucket_count
                clauses.table_properties["bucket_count"] = str(bucket_count)
            elif self._connector == ConnectorType.ICEBERG:
                logger.info(
                    f"Distribution columns for Iceberg table {table_tuning.table_name}: "
                    f"{[c.name for c in distribution_columns]}. "
                    f"Use bucket() transform in partitioning instead."
                )
            else:
                logger.warning(
                    f"Distribution not supported for {self._connector.value} connector. "
                    f"Columns {[c.name for c in distribution_columns]} will be ignored."
                )

        sort_columns = table_tuning.get_columns_by_type(TuningType.SORTING)
        cluster_columns = table_tuning.get_columns_by_type(TuningType.CLUSTERING)

        all_sort_cols = list(sort_columns) + [c for c in cluster_columns if c not in sort_columns]
        if all_sort_cols:
            if self._connector in (ConnectorType.HIVE, ConnectorType.ICEBERG):
                sorted_cols = sorted(all_sort_cols, key=lambda c: c.order)
                col_names = [f"'{c.name}'" for c in sorted_cols]
                clauses.table_properties["sorted_by"] = f"ARRAY[{', '.join(col_names)}]"
            else:
                logger.info(
                    f"Sorting hints for {self._connector.value} table {table_tuning.table_name}: "
                    f"{[c.name for c in all_sort_cols]}. May not be directly supported."
                )

        if self._location:
            clauses.table_properties["location"] = f"'{self._location}'"

        return clauses

    def _generate_iceberg_partitioning(
        self,
        partition_columns,
        platform_opts: PlatformOptimizationConfiguration | None = None,
    ) -> str:
        transforms = []

        for col in partition_columns:
            col_name = col.name
            col_type = col.type.upper() if col.type else ""

            transform = None
            if platform_opts:
                col_transforms = getattr(platform_opts, "partition_transforms", {})
                if isinstance(col_transforms, dict):
                    transform = col_transforms.get(col_name)

            if transform:
                transforms.append(f"'{transform}'")
            elif "DATE" in col_type or "TIMESTAMP" in col_type:
                transforms.append(f"'month({col_name})'")
            else:
                transforms.append(f"'{col_name}'")

        return f"ARRAY[{', '.join(transforms)}]"

    def format_qualified_name(self, table_name: str, schema: str | None = None) -> str:
        if schema:
            return f"{schema}.{self.quote_identifier(table_name)}"
        return self.quote_identifier(table_name)

    def _format_with_clause(self, properties: dict[str, str]) -> str:
        if not properties:
            return ""

        props = ", ".join(f"{k} = {v}" for k, v in sorted(properties.items()))
        return f"WITH (\n    {props}\n)"

    def generate_create_table_ddl(
        self,
        table_name: str,
        columns: list[ColumnDefinition],
        tuning: TuningClauses | None = None,
        if_not_exists: bool = False,
        schema: str | None = None,
    ) -> str:
        parts = ["CREATE"]

        if self._external:
            parts.append("EXTERNAL")

        parts.append("TABLE")

        if if_not_exists:
            parts.append("IF NOT EXISTS")

        parts.append(self.format_qualified_name(table_name, schema))

        statement = " ".join(parts)

        col_list = self.generate_column_list(columns)
        statement = f"{statement} (\n    {col_list}\n)"

        if tuning and tuning.table_properties:
            with_clause = self._format_with_clause(tuning.table_properties)
            if with_clause:
                statement = f"{statement}\n{with_clause}"

        statement = f"{statement}{self.STATEMENT_TERMINATOR}"

        return statement


class AthenaDDLGenerator(TrinoDDLGenerator):
    def __init__(
        self,
        location: str | None = None,
        default_format: FileFormat = FileFormat.PARQUET,
        default_bucket_count: int = 32,
    ):
        super().__init__(
            connector=ConnectorType.HIVE,
            default_format=default_format,
            default_bucket_count=default_bucket_count,
            external=True,
            location=location,
        )

    @property
    def platform_name(self) -> str:
        return "athena"

    def generate_create_table_ddl(
        self,
        table_name: str,
        columns: list[ColumnDefinition],
        tuning: TuningClauses | None = None,
        if_not_exists: bool = False,
        schema: str | None = None,
    ) -> str:
        parts = ["CREATE EXTERNAL TABLE"]

        if if_not_exists:
            parts.append("IF NOT EXISTS")

        parts.append(self.format_qualified_name(table_name, schema))

        statement = " ".join(parts)

        col_list = self.generate_column_list(columns)
        statement = f"{statement} (\n    {col_list}\n)"

        if tuning and "partitioned_by" in tuning.table_properties:
            logger.warning(
                f"Partitioning for Athena table {table_name} is not rendered: BenchBox creates Athena tables "
                "without PARTITIONED BY, so the preview omits it as well."
            )

        file_format = self._default_format.value
        if tuning and "format" in tuning.table_properties:
            format_val = tuning.table_properties["format"].strip("'")
            file_format = format_val

        statement = f"{statement}\nSTORED AS {file_format}"

        location = self._location
        if tuning and "location" in tuning.table_properties:
            location = tuning.table_properties["location"].strip("'")

        if location:
            statement = f"{statement}\nLOCATION '{location}'"

        remaining_props = {
            k: v
            for k, v in (tuning.table_properties if tuning else {}).items()
            if k not in ("format", "location", "partitioned_by", "bucketed_by", "bucket_count", "sorted_by")
        }
        if remaining_props:
            props_str = ", ".join(f"'{k}' = {v}" for k, v in remaining_props.items())
            statement = f"{statement}\nTBLPROPERTIES ({props_str})"

        statement = f"{statement}{self.STATEMENT_TERMINATOR}"

        return statement


__all__ = [
    "AthenaDDLGenerator",
    "ConnectorType",
    "FileFormat",
    "TrinoDDLGenerator",
]
