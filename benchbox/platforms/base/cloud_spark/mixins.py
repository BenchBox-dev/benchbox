# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from benchbox.core.tuning.ddl_generator import ColumnDefinition
    from benchbox.core.tuning.interface import (
        ForeignKeyConfiguration,
        PlatformOptimizationConfiguration,
        PrimaryKeyConfiguration,
        TableTuning,
        UnifiedTuningConfiguration,
    )

from benchbox.platforms.base.cloud_spark.config import CloudPlatform, SparkConfigOptimizer

logger = logging.getLogger(__name__)


class SparkTuningMixin:
    def apply_primary_keys(
        self,
        config: PrimaryKeyConfiguration,
    ) -> list[str]:
        if config and config.enabled:
            logger.info("Primary keys noted (Spark does not enforce constraints)")
        return []

    def apply_foreign_keys(
        self,
        config: ForeignKeyConfiguration,
    ) -> list[str]:
        if config and config.enabled:
            logger.info("Foreign keys noted (Spark does not enforce constraints)")
        return []

    def apply_platform_optimizations(
        self,
        config: PlatformOptimizationConfiguration,
    ) -> list[str]:
        return []

    def apply_tuning_configuration(
        self,
        config: UnifiedTuningConfiguration,
    ) -> dict[str, Any]:
        results: dict[str, Any] = {}
        scale_factor = getattr(config, "scale_factor", None)
        if scale_factor:
            self._scale_factor = scale_factor
        if config.primary_keys:
            results["primary_keys"] = self.apply_primary_keys(config.primary_keys)
        if config.foreign_keys:
            results["foreign_keys"] = self.apply_foreign_keys(config.foreign_keys)
        if config.platform_optimizations:
            results["platform_optimizations"] = self.apply_platform_optimizations(config.platform_optimizations)
        return results

    def apply_constraint_configuration(
        self,
        primary_key_config: PrimaryKeyConfiguration,
        foreign_key_config: ForeignKeyConfiguration,
        connection: Any,
    ) -> None:
        if primary_key_config and primary_key_config.enabled:
            logger.info("Primary key constraints noted (Spark does not enforce constraints)")
        if foreign_key_config and foreign_key_config.enabled:
            logger.info("Foreign key constraints noted (Spark does not enforce constraints)")


class CloudSparkConfigMixin:
    cloud_platform: ClassVar[CloudPlatform]

    _benchmark_type: str | None
    _scale_factor: float
    _spark_config: dict[str, str]

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        self._benchmark_type = benchmark_type.lower()
        platform_name = self.cloud_platform.value.replace("_", " ").title()
        logger.info(f"Configuring {platform_name} for {benchmark_type} benchmark")
        adaptive_enabled = getattr(self, "adaptive_enabled", True)

        if self._benchmark_type == "tpch":
            spark_config = SparkConfigOptimizer.for_tpch(
                scale_factor=self._scale_factor,
                platform=self.cloud_platform,
                adaptive_enabled=adaptive_enabled,
            )
        elif self._benchmark_type == "tpcds":
            spark_config = SparkConfigOptimizer.for_tpcds(
                scale_factor=self._scale_factor,
                platform=self.cloud_platform,
                adaptive_enabled=adaptive_enabled,
            )
        elif self._benchmark_type == "ssb":
            spark_config = SparkConfigOptimizer.for_ssb(
                scale_factor=self._scale_factor,
                platform=self.cloud_platform,
                adaptive_enabled=adaptive_enabled,
            )
        else:
            spark_config = SparkConfigOptimizer.for_tpch(
                scale_factor=self._scale_factor,
                platform=self.cloud_platform,
                adaptive_enabled=adaptive_enabled,
            )

        if "_user_spark_config" not in self.__dict__:
            self._user_spark_config = dict(self._spark_config)
        self._spark_config = {**spark_config.to_dict(), **self._user_spark_config}

        ledger = getattr(self, "_applied_tuning_ledger", None)
        if ledger is not None:
            from benchbox.core.tuning.applied_ledger import PHASE_SESSION

            for _key, _value in self._spark_config.items():
                ledger.record(
                    f"SET {_key}={_value}",
                    PHASE_SESSION,
                    mechanism="spark_session_config",
                )


_DELTA_AUTO_OPTIMIZE_PROPERTIES = {
    "delta.autoOptimize.optimizeWrite": "true",
    "delta.autoOptimize.autoCompact": "true",
}


class SparkTableFormat(str, Enum):
    DELTA = "delta"
    ICEBERG = "iceberg"
    HUDI = "hudi"
    PARQUET = "parquet"
    HIVE = "hive"


class SparkDDLGeneratorMixin:
    table_format: ClassVar[SparkTableFormat] = SparkTableFormat.PARQUET

    default_bucket_count: ClassVar[int] = 32

    SUPPORTED_TUNING_TYPES: ClassVar[frozenset[str]] = frozenset(
        {"partitioning", "clustering", "sorting", "distribution"}
    )

    def get_table_format(
        self,
        platform_opts: PlatformOptimizationConfiguration | None = None,
    ) -> SparkTableFormat:
        if platform_opts:
            format_str = getattr(platform_opts, "table_format", None)
            if format_str:
                try:
                    return SparkTableFormat(format_str.lower())
                except ValueError:
                    logger.warning(f"Invalid table_format '{format_str}', using default {self.table_format}")

        return self.table_format

    def generate_table_layout(
        self,
        table_tuning: TableTuning | None,
        platform_opts: PlatformOptimizationConfiguration | None = None,
    ) -> dict[str, Any]:
        table_format = self.get_table_format(platform_opts)

        if table_format == SparkTableFormat.DELTA:
            return self._delta_layout(table_tuning, platform_opts)
        elif table_format == SparkTableFormat.ICEBERG:
            return self._iceberg_layout(table_tuning, platform_opts)
        elif table_format == SparkTableFormat.HUDI:
            return self._hudi_layout(table_tuning, platform_opts)
        elif table_format == SparkTableFormat.PARQUET:
            return self._parquet_layout(table_tuning, platform_opts)
        elif table_format == SparkTableFormat.HIVE:
            return self._hive_layout(table_tuning, platform_opts)

        return {}

    @staticmethod
    def _ordered_names(columns: list[Any]) -> list[str]:
        return [col.name for col in sorted(columns, key=lambda c: c.order)]

    @staticmethod
    def _auto_optimize_enabled(platform_opts: PlatformOptimizationConfiguration | None) -> bool:
        return getattr(platform_opts, "enable_auto_optimize", True) if platform_opts else True

    @staticmethod
    def _sort_and_cluster_columns(first: Any, second: Any) -> list[Any]:
        return list(first) + [c for c in second if c not in first]

    def _delta_zorder_columns(
        self,
        table_tuning: TableTuning | None,
        platform_opts: PlatformOptimizationConfiguration | None = None,
    ) -> list[str]:
        if not table_tuning:
            return []
        from benchbox.core.tuning.interface import TuningType

        cluster_columns = table_tuning.get_columns_by_type(TuningType.CLUSTERING)
        sort_columns = table_tuning.get_columns_by_type(TuningType.SORTING)
        all_cluster_cols = self._sort_and_cluster_columns(cluster_columns, sort_columns)
        use_liquid_clustering = getattr(platform_opts, "use_liquid_clustering", True) if platform_opts else True

        if all_cluster_cols and not use_liquid_clustering:
            return self._ordered_names(all_cluster_cols)
        distribution_columns = table_tuning.get_columns_by_type(TuningType.DISTRIBUTION)
        if distribution_columns and not all_cluster_cols:
            return self._ordered_names(distribution_columns)
        return []

    def _delta_layout(
        self,
        table_tuning: TableTuning | None,
        platform_opts: PlatformOptimizationConfiguration | None = None,
    ) -> dict[str, Any]:
        from benchbox.core.tuning.interface import TuningType

        layout: dict[str, Any] = {"format": "delta"}
        if self._auto_optimize_enabled(platform_opts):
            layout["properties"] = dict(_DELTA_AUTO_OPTIMIZE_PROPERTIES)
        if not table_tuning:
            return layout

        partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
        if partition_columns:
            layout["partition_by"] = self._ordered_names(partition_columns)

        cluster_columns = table_tuning.get_columns_by_type(TuningType.CLUSTERING)
        sort_columns = table_tuning.get_columns_by_type(TuningType.SORTING)
        all_cluster_cols = self._sort_and_cluster_columns(cluster_columns, sort_columns)
        use_liquid_clustering = getattr(platform_opts, "use_liquid_clustering", True) if platform_opts else True
        if all_cluster_cols and use_liquid_clustering:
            layout["cluster_by"] = self._ordered_names(all_cluster_cols)

        return layout

    def _iceberg_layout(
        self,
        table_tuning: TableTuning | None,
        platform_opts: PlatformOptimizationConfiguration | None = None,
    ) -> dict[str, Any]:
        from benchbox.core.tuning.interface import TuningType

        layout: dict[str, Any] = {"format": "iceberg"}
        if not table_tuning:
            return layout

        partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
        if partition_columns:
            sorted_cols = sorted(partition_columns, key=lambda c: c.order)
            layout["partition_by"] = [self._get_iceberg_transform(col, platform_opts) for col in sorted_cols]

        distribution_columns = table_tuning.get_columns_by_type(TuningType.DISTRIBUTION)
        if distribution_columns and not partition_columns:
            bucket_count = self.default_bucket_count
            if platform_opts:
                bucket_count = getattr(platform_opts, "bucket_count", bucket_count)
            layout["partition_by"] = [
                f"bucket({bucket_count}, {name})" for name in self._ordered_names(distribution_columns)
            ]

        sort_columns = table_tuning.get_columns_by_type(TuningType.SORTING)
        cluster_columns = table_tuning.get_columns_by_type(TuningType.CLUSTERING)
        all_sort_cols = self._sort_and_cluster_columns(sort_columns, cluster_columns)
        if all_sort_cols:
            sorted_cols = sorted(all_sort_cols, key=lambda c: c.order)
            layout["properties"] = {
                "write.sort-order": ", ".join(f"{col.name} {getattr(col, 'sort_order', 'ASC')}" for col in sorted_cols)
            }

        return layout

    def _get_iceberg_transform(
        self,
        col,
        platform_opts: PlatformOptimizationConfiguration | None = None,
    ) -> str:
        col_name = col.name
        col_type = col.type.upper() if col.type else ""

        if platform_opts:
            col_transforms = getattr(platform_opts, "partition_transforms", {})
            if isinstance(col_transforms, dict) and col_name in col_transforms:
                return col_transforms[col_name]

        if "DATE" in col_type:
            return f"months({col_name})"
        elif "TIMESTAMP" in col_type:
            return f"days({col_name})"
        elif "INT" in col_type or "BIGINT" in col_type:
            return f"bucket({self.default_bucket_count}, {col_name})"
        else:
            return col_name

    def _hudi_layout(
        self,
        table_tuning: TableTuning | None,
        platform_opts: PlatformOptimizationConfiguration | None = None,
    ) -> dict[str, Any]:
        from benchbox.core.tuning.interface import TuningType

        record_key = None
        precombine_field = None
        table_type = "COPY_ON_WRITE"
        write_operation = "upsert"

        if platform_opts:
            record_key = getattr(platform_opts, "record_key", None)
            precombine_field = getattr(platform_opts, "precombine_field", None)
            table_type = getattr(platform_opts, "hudi_table_type", table_type)
            write_operation = getattr(platform_opts, "hudi_write_operation", write_operation)

        properties: dict[str, str] = {}
        if record_key:
            properties["hoodie.datasource.write.recordkey.field"] = record_key
        if precombine_field:
            properties["hoodie.datasource.write.precombine.field"] = precombine_field
        properties["hoodie.table.type"] = table_type
        properties["hoodie.datasource.write.operation"] = write_operation
        layout: dict[str, Any] = {"format": "hudi", "properties": properties}

        if not table_tuning:
            return layout

        partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
        if partition_columns:
            layout["partition_by"] = self._ordered_names(partition_columns)
            properties["hoodie.datasource.write.hive_style_partitioning"] = "true"

        distribution_columns = table_tuning.get_columns_by_type(TuningType.DISTRIBUTION)
        if distribution_columns and not record_key:
            properties["hoodie.datasource.write.recordkey.field"] = self._ordered_names(distribution_columns)[0]

        sort_columns = table_tuning.get_columns_by_type(TuningType.SORTING)
        cluster_columns = table_tuning.get_columns_by_type(TuningType.CLUSTERING)
        all_sort_cols = self._sort_and_cluster_columns(sort_columns, cluster_columns)
        if all_sort_cols:
            properties["hoodie.clustering.inline"] = "true"
            properties["hoodie.clustering.inline.max.commits"] = "4"
            properties["hoodie.clustering.plan.strategy.sort.columns"] = ",".join(self._ordered_names(all_sort_cols))

        return layout

    def _parquet_layout(
        self,
        table_tuning: TableTuning | None,
        platform_opts: PlatformOptimizationConfiguration | None = None,
    ) -> dict[str, Any]:
        from benchbox.core.tuning.interface import TuningType

        layout: dict[str, Any] = {"format": "parquet"}
        if not table_tuning:
            return layout

        partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
        if partition_columns:
            layout["partition_by"] = self._ordered_names(partition_columns)

        distribution_columns = table_tuning.get_columns_by_type(TuningType.DISTRIBUTION)
        if distribution_columns:
            bucket_count = self.default_bucket_count
            if platform_opts:
                bucket_count = getattr(platform_opts, "bucket_count", bucket_count)
            layout["distribute_by"] = self._ordered_names(distribution_columns)
            layout["buckets"] = bucket_count

        sort_columns = table_tuning.get_columns_by_type(TuningType.SORTING)
        cluster_columns = table_tuning.get_columns_by_type(TuningType.CLUSTERING)
        all_sort_cols = self._sort_and_cluster_columns(sort_columns, cluster_columns)
        if all_sort_cols:
            sort_parts = []
            for col in sorted(all_sort_cols, key=lambda c: c.order):
                direction = getattr(col, "sort_order", "ASC")
                nulls = getattr(col, "nulls_position", "DEFAULT")
                part = col.name
                if direction != "ASC":
                    part = f"{part} {direction}"
                if nulls != "DEFAULT":
                    part = f"{part} NULLS {nulls}"
                sort_parts.append(part)
            layout["sort_by"] = sort_parts

        return layout

    def _hive_layout(
        self,
        table_tuning: TableTuning | None,
        platform_opts: PlatformOptimizationConfiguration | None = None,
    ) -> dict[str, Any]:
        from benchbox.core.tuning.interface import TuningType

        storage_format = "PARQUET"
        if platform_opts:
            storage_format = getattr(platform_opts, "storage_format", storage_format)
        layout: dict[str, Any] = {"stored_as": storage_format}
        if not table_tuning:
            return layout

        partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
        if partition_columns:
            layout["partition_by"] = self._ordered_names(partition_columns)

        distribution_columns = table_tuning.get_columns_by_type(TuningType.DISTRIBUTION)
        if distribution_columns:
            bucket_count = self.default_bucket_count
            if platform_opts:
                bucket_count = getattr(platform_opts, "bucket_count", bucket_count)
            layout["distribute_by"] = self._ordered_names(distribution_columns)
            layout["buckets"] = bucket_count
            sort_columns = table_tuning.get_columns_by_type(TuningType.SORTING)
            cluster_columns = table_tuning.get_columns_by_type(TuningType.CLUSTERING)
            all_sort_cols = self._sort_and_cluster_columns(sort_columns, cluster_columns)
            if all_sort_cols:
                layout["sort_by"] = self._ordered_names(all_sort_cols)

        return layout

    def generate_create_table_ddl(
        self,
        table_name: str,
        columns: list[ColumnDefinition],
        layout: dict[str, Any] | None = None,
        if_not_exists: bool = False,
        schema: str | None = None,
    ) -> str:
        from benchbox.utils.dialect_utils import translate_ddl

        layout = layout or {}
        column_list = ", ".join(f"{col.name} {col.data_type}" for col in columns)
        if layout.get("stored_as"):
            dialect = "hive"
        elif layout.get("cluster_by"):
            dialect = "databricks"
        else:
            dialect = "spark"
        statements = translate_ddl(
            f"CREATE TABLE {table_name} ({column_list})",
            dialect,
            identify=False,
            if_not_exists=if_not_exists,
            schema=schema,
            default_layout=layout,
        )
        return " ".join(statements)

    def get_post_load_statements(
        self,
        table_name: str,
        table_tuning: TableTuning | None,
        platform_opts: PlatformOptimizationConfiguration | None = None,
        schema: str | None = None,
    ) -> list[str]:
        if self.get_table_format(platform_opts) != SparkTableFormat.DELTA:
            return []
        zorder = self._delta_zorder_columns(table_tuning, platform_opts)
        if not zorder:
            return []
        qualified_name = f"{schema}.{table_name}" if schema else table_name
        return [f"OPTIMIZE {qualified_name} ZORDER BY ({', '.join(zorder)})"]

    def supports_tuning_type(self, tuning_type: str) -> bool:
        return tuning_type.lower() in self.SUPPORTED_TUNING_TYPES
