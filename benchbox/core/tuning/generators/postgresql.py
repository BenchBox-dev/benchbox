# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
from datetime import date, timedelta
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


class PartitionStrategy(str, Enum):
    RANGE = "RANGE"
    LIST = "LIST"
    HASH = "HASH"


class PostgreSQLDDLGenerator(BaseDDLGenerator):
    IDENTIFIER_QUOTE = '"'
    SUPPORTS_IF_NOT_EXISTS = True
    STATEMENT_TERMINATOR = ";"

    SUPPORTED_TUNING_TYPES = frozenset({"partitioning", "clustering", "sorting"})

    def __init__(
        self,
        default_hash_partitions: int = 4,
        default_date_granularity: str = "YEARLY",
    ):
        self._default_hash_partitions = default_hash_partitions
        self._default_date_granularity = default_date_granularity

    @property
    def platform_name(self) -> str:
        return "postgresql"

    def generate_tuning_clauses(
        self,
        table_tuning: TableTuning | None,
        platform_opts: PlatformOptimizationConfiguration | None = None,
    ) -> TuningClauses:
        clauses = TuningClauses()

        if not table_tuning:
            return clauses

        from benchbox.core.tuning.interface import TuningType

        distribution_columns = table_tuning.get_columns_by_type(TuningType.DISTRIBUTION)
        if distribution_columns:
            logger.warning(
                f"Distribution tuning not applicable for PostgreSQL "
                f"(table: {table_tuning.table_name}). "
                f"PostgreSQL is single-node. "
                f"Configured columns {[c.name for c in distribution_columns]} will be ignored."
            )

        partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
        if partition_columns:
            sorted_cols = sorted(partition_columns, key=lambda c: c.order)

            strategy = self._determine_partition_strategy(sorted_cols, platform_opts)

            col_names = [c.name for c in sorted_cols]
            clauses.partition_by = f"PARTITION BY {strategy.value} ({', '.join(col_names)})"

        cluster_columns = table_tuning.get_columns_by_type(TuningType.CLUSTERING)
        sort_columns = table_tuning.get_columns_by_type(TuningType.SORTING)

        all_cluster_cols = list(cluster_columns) + [c for c in sort_columns if c not in cluster_columns]

        if all_cluster_cols:
            sorted_cols = sorted(all_cluster_cols, key=lambda c: c.order)
            col_names = [c.name for c in sorted_cols]
            table_name = table_tuning.table_name

            index_name = f"idx_{table_name.lower()}_cluster"

            clauses.post_create_statements.append(
                f"CREATE INDEX IF NOT EXISTS {index_name} ON {{table_name}} ({', '.join(col_names)})"
            )
            clauses.post_create_statements.append(f"CLUSTER {{table_name}} USING {index_name}")

        return clauses

    def _determine_partition_strategy(
        self,
        partition_columns,
        platform_opts: PlatformOptimizationConfiguration | None = None,
    ) -> PartitionStrategy:
        if platform_opts:
            strategy_str = getattr(platform_opts, "partition_strategy", None)
            if strategy_str:
                try:
                    return PartitionStrategy(strategy_str.upper())
                except ValueError:
                    logger.warning(f"Invalid partition_strategy '{strategy_str}', auto-detecting")

        if partition_columns:
            first_col = partition_columns[0]
            col_type = first_col.type.upper() if first_col.type else ""

            if "DATE" in col_type or "TIMESTAMP" in col_type:
                return PartitionStrategy.RANGE
            elif "INT" in col_type:
                return PartitionStrategy.HASH
            else:
                return PartitionStrategy.LIST

        return PartitionStrategy.RANGE

    def generate_partition_children(
        self,
        parent_table: str,
        columns: list[ColumnDefinition],
        tuning: TuningClauses,
        table_tuning: TableTuning | None = None,
        platform_opts: PlatformOptimizationConfiguration | None = None,
        schema: str | None = None,
        year_range: tuple[int, int] | None = None,
    ) -> list[str]:
        if not tuning.partition_by or not table_tuning:
            return []

        from benchbox.core.tuning.interface import TuningType

        partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
        if not partition_columns:
            return []

        strategy = self._determine_partition_strategy(partition_columns, platform_opts)
        qualified_parent = self.format_qualified_name(parent_table, schema)

        statements = []

        if strategy == PartitionStrategy.HASH:
            statements = self._generate_hash_partitions(parent_table, qualified_parent, platform_opts)
        elif strategy == PartitionStrategy.RANGE:
            statements = self._generate_range_partitions(
                parent_table, qualified_parent, partition_columns, platform_opts, year_range=year_range
            )
        elif strategy == PartitionStrategy.LIST:
            statements = self._generate_list_partitions(parent_table, qualified_parent, platform_opts)

        if strategy != PartitionStrategy.HASH:
            statements.append(
                f"CREATE TABLE {parent_table}_default PARTITION OF {qualified_parent} DEFAULT{self.STATEMENT_TERMINATOR}"
            )
        return statements

    def _generate_hash_partitions(
        self,
        parent_table: str,
        qualified_parent: str,
        platform_opts: PlatformOptimizationConfiguration | None = None,
    ) -> list[str]:
        num_partitions = self._default_hash_partitions
        if platform_opts:
            num_partitions = getattr(platform_opts, "hash_partitions", num_partitions)

        statements = []
        for i in range(num_partitions):
            child_name = f"{parent_table}_p{i}"
            statements.append(
                f"CREATE TABLE {child_name} PARTITION OF {qualified_parent} "
                f"FOR VALUES WITH (MODULUS {num_partitions}, REMAINDER {i}){self.STATEMENT_TERMINATOR}"
            )

        return statements

    def _generate_range_partitions(
        self,
        parent_table: str,
        qualified_parent: str,
        partition_columns,
        platform_opts: PlatformOptimizationConfiguration | None = None,
        *,
        year_range: tuple[int, int] | None = None,
    ) -> list[str]:
        granularity = self._default_date_granularity
        if platform_opts:
            granularity = getattr(platform_opts, "partition_granularity", granularity)

        start_year, end_year = year_range or (2020, 2026)
        if platform_opts:
            start_year = getattr(platform_opts, "range_start_year", start_year)
            end_year = getattr(platform_opts, "range_end_year", end_year)

        statements = []

        if granularity.upper() == "YEARLY":
            for year in range(start_year, end_year):
                child_name = f"{parent_table}_{year}"
                start_date = f"'{year}-01-01'"
                end_date = f"'{year + 1}-01-01'"
                statements.append(
                    f"CREATE TABLE {child_name} PARTITION OF {qualified_parent} "
                    f"FOR VALUES FROM ({start_date}) TO ({end_date}){self.STATEMENT_TERMINATOR}"
                )
        elif granularity.upper() == "MONTHLY":
            for year in range(start_year, end_year):
                for month in range(1, 13):
                    child_name = f"{parent_table}_{year}_{month:02d}"
                    start_date = date(year, month, 1)
                    end_date = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
                    statements.append(
                        f"CREATE TABLE {child_name} PARTITION OF {qualified_parent} "
                        f"FOR VALUES FROM ('{start_date}') TO ('{end_date}'){self.STATEMENT_TERMINATOR}"
                    )
        elif granularity.upper() == "DAILY":
            logger.info(
                f"Daily partitioning for {parent_table}: generating sample partitions only. "
                f"Use platform_opts to specify exact date range."
            )
            base_date = date(start_year, 1, 1)
            for i in range(30):
                current_date = base_date + timedelta(days=i)
                next_date = current_date + timedelta(days=1)
                child_name = f"{parent_table}_{current_date.strftime('%Y_%m_%d')}"
                statements.append(
                    f"CREATE TABLE {child_name} PARTITION OF {qualified_parent} "
                    f"FOR VALUES FROM ('{current_date}') TO ('{next_date}'){self.STATEMENT_TERMINATOR}"
                )

        return statements

    def _generate_list_partitions(
        self,
        parent_table: str,
        qualified_parent: str,
        platform_opts: PlatformOptimizationConfiguration | None = None,
    ) -> list[str]:
        if not platform_opts:
            logger.warning(
                f"LIST partitioning for {parent_table} requires list_values in platform_opts. "
                f"No partition children generated."
            )
            return []

        list_values = getattr(platform_opts, "list_values", None)
        if not list_values:
            logger.warning(
                f"No list_values provided for LIST partition {parent_table}. No partition children generated."
            )
            return []

        statements = []
        for value in list_values:
            safe_value = str(value).lower().replace(" ", "_").replace("-", "_")
            child_name = f"{parent_table}_{safe_value}"

            quoted_value = f"'{value}'" if isinstance(value, str) else str(value)

            statements.append(
                f"CREATE TABLE {child_name} PARTITION OF {qualified_parent} "
                f"FOR VALUES IN ({quoted_value}){self.STATEMENT_TERMINATOR}"
            )

        return statements

    def generate_create_table_ddl(
        self,
        table_name: str,
        columns: list[ColumnDefinition],
        tuning: TuningClauses | None = None,
        if_not_exists: bool = False,
        schema: str | None = None,
    ) -> str:
        parts = ["CREATE TABLE"]

        if if_not_exists:
            parts.append("IF NOT EXISTS")

        parts.append(self.format_qualified_name(table_name, schema))

        statement = " ".join(parts)

        col_list = self.generate_column_list(columns)
        statement = f"{statement} (\n    {col_list}\n)"

        if tuning and tuning.partition_by:
            statement = f"{statement}\n{tuning.partition_by}"

        statement = f"{statement}{self.STATEMENT_TERMINATOR}"

        return statement


__all__ = [
    "PartitionStrategy",
    "PostgreSQLDDLGenerator",
]
