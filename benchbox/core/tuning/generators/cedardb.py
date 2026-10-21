from __future__ import annotations

from typing import TYPE_CHECKING

from benchbox.core.tuning.ddl_generator import TuningClauses
from benchbox.core.tuning.generators.postgresql import PartitionStrategy, PostgreSQLDDLGenerator
from benchbox.core.tuning.interface import TableTuning as RuntimeTableTuning

if TYPE_CHECKING:
    from benchbox.core.tuning.ddl_generator import ColumnDefinition
    from benchbox.core.tuning.interface import (
        PlatformOptimizationConfiguration,
        TableTuning,
    )


class CedarDBDDLGenerator(PostgreSQLDDLGenerator):
    @property
    def platform_name(self) -> str:
        return "cedardb"

    def _determine_partition_strategy(
        self,
        partition_columns,
        platform_opts: PlatformOptimizationConfiguration | None = None,
    ) -> PartitionStrategy:
        return PartitionStrategy.HASH

    def generate_tuning_clauses(
        self,
        table_tuning: TableTuning | None,
        platform_opts: PlatformOptimizationConfiguration | None = None,
    ) -> TuningClauses:
        if table_tuning is None:
            return TuningClauses()
        partition_only = RuntimeTableTuning(table_name=table_tuning.table_name, partitioning=table_tuning.partitioning)
        return super().generate_tuning_clauses(partition_only, platform_opts)

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
        return []


__all__ = [
    "CedarDBDDLGenerator",
]
