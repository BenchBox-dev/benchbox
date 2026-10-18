# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from benchbox.core.tuning.ddl_generator import TuningClauses
from benchbox.core.tuning.generators.postgresql import PostgreSQLDDLGenerator

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import (
        PlatformOptimizationConfiguration,
        TableTuning,
    )

logger = logging.getLogger(__name__)


class TimescaleDBDDLGenerator(PostgreSQLDDLGenerator):
    def __init__(
        self,
        default_chunk_interval: str = "INTERVAL '1 day'",
        enable_compression: bool = False,
        compression_after: str = "INTERVAL '7 days'",
    ):
        super().__init__()
        self._default_chunk_interval = default_chunk_interval
        self._enable_compression = enable_compression
        self._compression_after = compression_after

    @property
    def platform_name(self) -> str:
        return "timescaledb"

    def generate_tuning_clauses(
        self,
        table_tuning: TableTuning | None,
        platform_opts: PlatformOptimizationConfiguration | None = None,
    ) -> TuningClauses:
        clauses = TuningClauses()

        if not table_tuning:
            return clauses

        from benchbox.core.tuning.interface import TuningType

        partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
        if not partition_columns:
            logger.info(
                f"No partitioning columns for table {table_tuning.table_name}. "
                f"Table will not be converted to hypertable."
            )
            return clauses

        sorted_cols = sorted(partition_columns, key=lambda c: c.order)
        time_column = sorted_cols[0].name

        chunk_interval = self._default_chunk_interval
        if platform_opts:
            chunk_interval = getattr(platform_opts, "chunk_interval", chunk_interval)

        hypertable_call = (
            f"SELECT create_hypertable('{{table_name}}', '{time_column}', chunk_time_interval => {chunk_interval}"
        )

        distribution_columns = table_tuning.get_columns_by_type(TuningType.DISTRIBUTION)
        if distribution_columns:
            space_col = sorted(distribution_columns, key=lambda c: c.order)[0]
            num_partitions = 4
            if platform_opts:
                num_partitions = getattr(platform_opts, "space_partitions", num_partitions)

            hypertable_call += f", partitioning_column => '{space_col.name}', number_partitions => {num_partitions}"

        hypertable_call += ", if_not_exists => TRUE)"

        clauses.post_create_statements.append(hypertable_call)

        enable_compression = self._enable_compression
        if platform_opts:
            enable_compression = getattr(platform_opts, "enable_compression", enable_compression)

        if enable_compression:
            compression_statements = self._generate_compression_statements(table_tuning, platform_opts)
            clauses.post_create_statements.extend(compression_statements)

        return clauses

    def _generate_compression_statements(
        self,
        table_tuning: TableTuning,
        platform_opts: PlatformOptimizationConfiguration | None = None,
    ) -> list[str]:
        from benchbox.core.tuning.interface import TuningType

        statements = []

        settings = ["timescaledb.compress"]

        distribution_cols = table_tuning.get_columns_by_type(TuningType.DISTRIBUTION)
        clustering_cols = table_tuning.get_columns_by_type(TuningType.CLUSTERING)

        segmentby_cols = list(distribution_cols) + [c for c in clustering_cols if c not in distribution_cols]

        if segmentby_cols:
            sorted_cols = sorted(segmentby_cols, key=lambda c: c.order)
            col_names = [c.name for c in sorted_cols]
            settings.append(f"timescaledb.compress_segmentby = '{', '.join(col_names)}'")

        sort_cols = table_tuning.get_columns_by_type(TuningType.SORTING)
        if sort_cols:
            sorted_cols = sorted(sort_cols, key=lambda c: c.order)
            orderby_parts = []
            for col in sorted_cols:
                direction = col.sort_order
                nulls = f" NULLS {col.nulls_position}" if col.nulls_position != "DEFAULT" else ""
                orderby_parts.append(f"{col.name} {direction}{nulls}")
            settings.append(f"timescaledb.compress_orderby = '{', '.join(orderby_parts)}'")

        settings_str = ", ".join(settings)
        statements.append(f"ALTER TABLE {{table_name}} SET ({settings_str})")

        compression_after = self._compression_after
        if platform_opts:
            compression_after = getattr(platform_opts, "compression_after", compression_after)

        statements.append(
            f"SELECT add_compression_policy('{{table_name}}', {compression_after}, if_not_exists => TRUE)"
        )

        return statements

    def generate_partition_children(
        self,
        parent_table: str,
        columns,
        tuning: TuningClauses,
        table_tuning: TableTuning | None = None,
        platform_opts: PlatformOptimizationConfiguration | None = None,
        schema: str | None = None,
    ) -> list[str]:
        return []


__all__ = [
    "TimescaleDBDDLGenerator",
]
