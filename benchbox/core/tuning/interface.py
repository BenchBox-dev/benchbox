# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional


class TuningType(Enum):
    PARTITIONING = "partitioning"
    CLUSTERING = "clustering"
    DISTRIBUTION = "distribution"
    SORTING = "sorting"

    PRIMARY_KEYS = "primary_keys"
    FOREIGN_KEYS = "foreign_keys"
    UNIQUE_CONSTRAINTS = "unique_constraints"
    CHECK_CONSTRAINTS = "check_constraints"

    Z_ORDERING = "z_ordering"
    LIQUID_CLUSTERING = "liquid_clustering"
    AUTO_OPTIMIZE = "auto_optimize"
    AUTO_COMPACT = "auto_compact"
    BLOOM_FILTERS = "bloom_filters"
    MATERIALIZED_VIEWS = "materialized_views"

    def __str__(self) -> str:
        return self.value

    @classmethod
    def from_string(cls, value: str) -> "TuningType":
        value_lower = value.lower()
        for tuning_type in cls:
            if tuning_type.value == value_lower:
                return tuning_type
        raise ValueError(f"Invalid tuning type: {value}")

    def is_compatible_with_platform(self, platform: str) -> bool:
        platform_lower = platform.lower()
        return self in _PLATFORM_COMPATIBILITY_MAP.get(platform_lower, frozenset())

    @classmethod
    def is_known_platform(cls, platform: str) -> bool:
        return platform.lower() in _KNOWN_COMPATIBILITY_PLATFORMS


def _platform_compatibility_map() -> dict[str, frozenset[TuningType]]:
    from benchbox.core.tuning.capability_registry import interface_compatibility_map

    return interface_compatibility_map()


_PLATFORM_COMPATIBILITY_MAP: dict[str, frozenset[TuningType]] = _platform_compatibility_map()


_KNOWN_COMPATIBILITY_PLATFORMS = frozenset(_PLATFORM_COMPATIBILITY_MAP)


_CONSTRAINT_TUNING_TYPES = frozenset(
    {
        TuningType.PRIMARY_KEYS,
        TuningType.FOREIGN_KEYS,
        TuningType.UNIQUE_CONSTRAINTS,
        TuningType.CHECK_CONSTRAINTS,
    }
)


SortOrderType = Literal["ASC", "DESC"]
NullsPositionType = Literal["FIRST", "LAST", "DEFAULT"]


@dataclass
class TuningColumn:
    name: str
    type: str
    order: int

    sort_order: SortOrderType = "ASC"
    nulls_position: NullsPositionType = "DEFAULT"
    compression: Optional[str] = None

    def __post_init__(self) -> None:
        self._validate_name()
        self._validate_order()
        self._validate_sort_order()
        self._validate_nulls_position()

    def _validate_name(self) -> None:
        if not self.name:
            raise ValueError("Column name cannot be empty")

        if not isinstance(self.name, str):
            raise ValueError("Column name must be a string")

        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", self.name):
            raise ValueError(
                f"Invalid column name format: '{self.name}'. "
                "Column names must start with a letter or underscore and "
                "contain only letters, numbers, and underscores."
            )

    def _validate_order(self) -> None:
        if not isinstance(self.order, int):
            raise ValueError("Column order must be an integer")

        if self.order <= 0:
            raise ValueError("Column order must be a positive integer")

    def _validate_sort_order(self) -> None:
        if self.sort_order not in ("ASC", "DESC"):
            raise ValueError(f"Invalid sort_order: '{self.sort_order}'. Must be 'ASC' or 'DESC'.")

    def _validate_nulls_position(self) -> None:
        if self.nulls_position not in ("FIRST", "LAST", "DEFAULT"):
            raise ValueError(f"Invalid nulls_position: '{self.nulls_position}'. Must be 'FIRST', 'LAST', or 'DEFAULT'.")

    def to_dict(self) -> dict[str, Any]:
        result = {
            "name": self.name,
            "type": self.type,
            "order": self.order,
        }

        if self.sort_order != "ASC":
            result["sort_order"] = self.sort_order
        if self.nulls_position != "DEFAULT":
            result["nulls_position"] = self.nulls_position
        if self.compression is not None:
            result["compression"] = self.compression

        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TuningColumn":
        required_fields = {"name", "type", "order"}
        missing_fields = required_fields - data.keys()
        if missing_fields:
            raise ValueError(f"Missing required fields: {missing_fields}")

        return cls(
            name=data["name"],
            type=data["type"],
            order=data["order"],
            sort_order=data.get("sort_order", "ASC"),
            nulls_position=data.get("nulls_position", "DEFAULT"),
            compression=data.get("compression"),
        )


PartitionStrategyType = Literal["RANGE", "LIST", "HASH", "DATE"]
PartitionGranularityType = Literal["HOURLY", "DAILY", "MONTHLY", "YEARLY"]
SortKeyStyleType = Literal["COMPOUND", "INTERLEAVED", "AUTO"]
SortedIngestionModeType = Literal["off", "auto", "force"]
SortedIngestionMethodType = Literal["auto", "ctas", "z_order", "hilbert", "liquid_clustering", "vacuum_sort"]
DatabricksClusteringStrategyType = Literal["z_order", "liquid_clustering", "liquid_clustering_auto", "none"]
DatabricksPhysicalRenderingType = Literal[
    "databricks_z_order",
    "databricks_liquid_manual",
    "databricks_liquid_auto",
]


@dataclass
class PartitioningConfig:
    columns: list[TuningColumn]
    strategy: PartitionStrategyType = "RANGE"
    granularity: Optional[PartitionGranularityType] = None
    bucket_count: Optional[int] = None
    range_boundaries: Optional[list[Any]] = None

    def __post_init__(self) -> None:
        self._validate_strategy()
        self._validate_bucket_count()

    def _validate_strategy(self) -> None:
        valid_strategies = ("RANGE", "LIST", "HASH", "DATE")
        if self.strategy not in valid_strategies:
            raise ValueError(f"Invalid strategy: '{self.strategy}'. Must be one of {valid_strategies}.")

    def _validate_bucket_count(self) -> None:
        if (
            self.strategy == "HASH"
            and self.bucket_count is not None
            and (not isinstance(self.bucket_count, int) or self.bucket_count <= 0)
        ):
            raise ValueError("bucket_count must be a positive integer for HASH strategy")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "columns": [col.to_dict() for col in self.columns],
        }

        if self.strategy != "RANGE":
            result["strategy"] = self.strategy
        if self.granularity is not None:
            result["granularity"] = self.granularity
        if self.bucket_count is not None:
            result["bucket_count"] = self.bucket_count
        if self.range_boundaries is not None:
            result["range_boundaries"] = self.range_boundaries

        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PartitioningConfig":
        columns = [TuningColumn.from_dict(col) for col in data.get("columns", [])]
        return cls(
            columns=columns,
            strategy=data.get("strategy", "RANGE"),
            granularity=data.get("granularity"),
            bucket_count=data.get("bucket_count"),
            range_boundaries=data.get("range_boundaries"),
        )


@dataclass
class SortKeyConfig:
    columns: list[TuningColumn]
    style: SortKeyStyleType = "COMPOUND"

    def __post_init__(self) -> None:
        self._validate_style()

    def _validate_style(self) -> None:
        valid_styles = ("COMPOUND", "INTERLEAVED", "AUTO")
        if self.style not in valid_styles:
            raise ValueError(f"Invalid style: '{self.style}'. Must be one of {valid_styles}.")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "columns": [col.to_dict() for col in self.columns],
        }

        if self.style != "COMPOUND":
            result["style"] = self.style

        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SortKeyConfig":
        columns = [TuningColumn.from_dict(col) for col in data.get("columns", [])]
        return cls(
            columns=columns,
            style=data.get("style", "COMPOUND"),
        )


@dataclass
class ClusteringConfig:
    columns: list[TuningColumn]
    bucket_count: Optional[int] = None

    def __post_init__(self) -> None:
        if self.bucket_count is not None and (not isinstance(self.bucket_count, int) or self.bucket_count <= 0):
            raise ValueError("bucket_count must be a positive integer")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "columns": [col.to_dict() for col in self.columns],
        }

        if self.bucket_count is not None:
            result["bucket_count"] = self.bucket_count

        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ClusteringConfig":
        columns = [TuningColumn.from_dict(col) for col in data.get("columns", [])]
        return cls(
            columns=columns,
            bucket_count=data.get("bucket_count"),
        )


@dataclass
class TableTuning:
    table_name: str
    partitioning: Optional[list[TuningColumn]] = None
    clustering: Optional[list[TuningColumn]] = None
    distribution: Optional[list[TuningColumn]] = None
    sorting: Optional[list[TuningColumn]] = None

    def __post_init__(self) -> None:
        self._validate_table_name()
        self._validate_column_lists()
        self.validate()

    def _validate_table_name(self) -> None:
        if not self.table_name:
            raise ValueError("Table name cannot be empty")

        if not isinstance(self.table_name, str):
            raise ValueError("Table name must be a string")

        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", self.table_name):
            raise ValueError(
                f"Invalid table name format: '{self.table_name}'. "
                "Table names must start with a letter or underscore and "
                "contain only letters, numbers, and underscores."
            )

    def _validate_column_lists(self) -> None:
        for tuning_type, columns in self._get_tuning_columns().items():
            if columns is not None:
                for i, column in enumerate(columns):
                    if not isinstance(column, TuningColumn):
                        raise ValueError(f"{tuning_type} column at index {i} must be a TuningColumn instance")

    def _get_tuning_columns(self) -> dict[str, Optional[list[TuningColumn]]]:
        return {
            "partitioning": self.partitioning,
            "clustering": self.clustering,
            "distribution": self.distribution,
            "sorting": self.sorting,
        }

    def validate(self) -> list[str]:
        errors = []

        if not self.has_any_tuning():
            errors.append("Table tuning must specify at least one tuning configuration")

        errors.extend(self._validate_column_orders())

        errors.extend(self._detect_column_conflicts())

        return errors

    def has_any_tuning(self) -> bool:
        return any(columns is not None and len(columns) > 0 for columns in self._get_tuning_columns().values())

    def _validate_column_orders(self) -> list[str]:
        errors = []

        for tuning_type, columns in self._get_tuning_columns().items():
            if columns is not None and len(columns) > 1:
                orders = [col.order for col in columns]
                if len(set(orders)) != len(orders):
                    duplicates = [order for order in set(orders) if orders.count(order) > 1]
                    errors.append(f"{tuning_type} has duplicate column orders: {duplicates}")

        return errors

    def _detect_column_conflicts(self) -> list[str]:
        errors = []

        tuning_columns = {}
        for tuning_type, columns in self._get_tuning_columns().items():
            if columns is not None:
                tuning_columns[tuning_type] = {col.name for col in columns}

        all_columns = {}
        for tuning_type, column_names in tuning_columns.items():
            for col_name in column_names:
                if col_name not in all_columns:
                    all_columns[col_name] = []
                all_columns[col_name].append(tuning_type)

        for col_name, tuning_types in all_columns.items():
            if len(tuning_types) > 1:
                errors.append(f"Column '{col_name}' is used in multiple tuning types: {tuning_types}")

        return errors

    def get_columns_by_type(self, tuning_type: TuningType) -> list[TuningColumn]:
        type_mapping = {
            TuningType.PARTITIONING: self.partitioning,
            TuningType.CLUSTERING: self.clustering,
            TuningType.DISTRIBUTION: self.distribution,
            TuningType.SORTING: self.sorting,
        }

        columns = type_mapping.get(tuning_type)
        return columns if columns is not None else []

    def get_all_columns(self) -> set[str]:
        all_columns = set()
        for columns in self._get_tuning_columns().values():
            if columns is not None:
                all_columns.update(col.name for col in columns)
        return all_columns

    def to_dict(self) -> dict[str, Any]:
        result = {"table_name": self.table_name}

        for tuning_type, columns in self._get_tuning_columns().items():
            if columns is not None:
                result[tuning_type] = [col.to_dict() for col in columns]

        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TableTuning":
        if "table_name" not in data:
            raise ValueError("Missing required field: table_name")

        kwargs = {"table_name": data["table_name"]}

        for tuning_type in ["partitioning", "clustering", "distribution", "sorting"]:
            if tuning_type in data:
                kwargs[tuning_type] = [TuningColumn.from_dict(col_data) for col_data in data[tuning_type]]

        return cls(**kwargs)


@dataclass
class BenchmarkTunings:
    benchmark_name: str
    table_tunings: dict[str, TableTuning] = field(default_factory=dict)
    enable_primary_keys: bool = True
    enable_foreign_keys: bool = True

    def __post_init__(self) -> None:
        self._validate_benchmark_name()

    def _validate_benchmark_name(self) -> None:
        if not self.benchmark_name:
            raise ValueError("Benchmark name cannot be empty")

        if not isinstance(self.benchmark_name, str):
            raise ValueError("Benchmark name must be a string")

    def add_table_tuning(self, table_tuning: TableTuning) -> None:
        if not isinstance(table_tuning, TableTuning):
            raise ValueError("table_tuning must be a TableTuning instance")

        errors = table_tuning.validate()
        if errors:
            raise ValueError(f"Invalid table tuning for '{table_tuning.table_name}': {errors}")

        if table_tuning.table_name in self.table_tunings:
            raise ValueError(
                f"Table tuning already exists for '{table_tuning.table_name}'. "
                "Use update_table_tuning() to modify existing tunings."
            )

        self.table_tunings[table_tuning.table_name] = table_tuning

    def update_table_tuning(self, table_tuning: TableTuning) -> None:
        if not isinstance(table_tuning, TableTuning):
            raise ValueError("table_tuning must be a TableTuning instance")

        errors = table_tuning.validate()
        if errors:
            raise ValueError(f"Invalid table tuning for '{table_tuning.table_name}': {errors}")

        self.table_tunings[table_tuning.table_name] = table_tuning

    def get_table_tuning(self, table_name: str) -> Optional[TableTuning]:
        return self.table_tunings.get(table_name)

    def remove_table_tuning(self, table_name: str) -> bool:
        if table_name in self.table_tunings:
            del self.table_tunings[table_name]
            return True
        return False

    def get_table_names(self) -> list[str]:
        return sorted(self.table_tunings.keys())

    def disable_primary_keys(self) -> None:
        self.enable_primary_keys = False

    def disable_foreign_keys(self) -> None:
        self.enable_foreign_keys = False

    def enable_all_constraints(self) -> None:
        self.enable_primary_keys = True
        self.enable_foreign_keys = True

    def disable_all_constraints(self) -> None:
        self.enable_primary_keys = False
        self.enable_foreign_keys = False

    def get_constraint_status(self) -> dict[str, bool]:
        return {
            "primary_keys": self.enable_primary_keys,
            "foreign_keys": self.enable_foreign_keys,
        }

    def validate_all(self) -> dict[str, list[str]]:
        validation_results = {}

        for table_name, table_tuning in self.table_tunings.items():
            validation_results[table_name] = table_tuning.validate()

        return validation_results

    def has_valid_tunings(self) -> bool:
        validation_results = self.validate_all()
        return all(len(errors) == 0 for errors in validation_results.values())

    def get_configuration_hash(self) -> str:
        config_dict = self.to_dict()

        import json

        config_json = json.dumps(config_dict, sort_keys=True, separators=(",", ":"))

        return hashlib.sha256(config_json.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark_name": self.benchmark_name,
            "table_tunings": {
                table_name: table_tuning.to_dict() for table_name, table_tuning in self.table_tunings.items()
            },
            "enable_primary_keys": self.enable_primary_keys,
            "enable_foreign_keys": self.enable_foreign_keys,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BenchmarkTunings":
        if "benchmark_name" not in data:
            raise ValueError("Missing required field: benchmark_name")

        enable_primary_keys = data.get("enable_primary_keys", True)
        enable_foreign_keys = data.get("enable_foreign_keys", True)

        benchmark_tunings = cls(
            benchmark_name=data["benchmark_name"],
            enable_primary_keys=enable_primary_keys,
            enable_foreign_keys=enable_foreign_keys,
        )

        if "table_tunings" in data:
            for _table_name, table_data in data["table_tunings"].items():
                table_tuning = TableTuning.from_dict(table_data)
                benchmark_tunings.add_table_tuning(table_tuning)

        return benchmark_tunings

    def __len__(self) -> int:
        return len(self.table_tunings)

    def __contains__(self, table_name: str) -> bool:
        return table_name in self.table_tunings

    def __iter__(self):
        return iter(self.table_tunings)


@dataclass
class ConstraintConfiguration:
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConstraintConfiguration":
        return cls(enabled=data.get("enabled", True))


@dataclass
class PrimaryKeyConfiguration(ConstraintConfiguration):
    enforce_uniqueness: bool = True
    nullable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "enforce_uniqueness": self.enforce_uniqueness,
            "nullable": self.nullable,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PrimaryKeyConfiguration":
        return cls(
            enabled=data.get("enabled", True),
            enforce_uniqueness=data.get("enforce_uniqueness", True),
            nullable=data.get("nullable", False),
        )


@dataclass
class ForeignKeyConfiguration(ConstraintConfiguration):
    enforce_referential_integrity: bool = True
    on_delete_action: str = "RESTRICT"
    on_update_action: str = "RESTRICT"

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "enforce_referential_integrity": self.enforce_referential_integrity,
            "on_delete_action": self.on_delete_action,
            "on_update_action": self.on_update_action,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ForeignKeyConfiguration":
        return cls(
            enabled=data.get("enabled", True),
            enforce_referential_integrity=data.get("enforce_referential_integrity", True),
            on_delete_action=data.get("on_delete_action", "RESTRICT"),
            on_update_action=data.get("on_update_action", "RESTRICT"),
        )


@dataclass
class UniqueConstraintConfiguration(ConstraintConfiguration):
    ignore_nulls: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "ignore_nulls": self.ignore_nulls}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UniqueConstraintConfiguration":
        return cls(
            enabled=data.get("enabled", True),
            ignore_nulls=data.get("ignore_nulls", False),
        )


@dataclass
class CheckConstraintConfiguration(ConstraintConfiguration):
    enforce_on_insert: bool = True
    enforce_on_update: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "enforce_on_insert": self.enforce_on_insert,
            "enforce_on_update": self.enforce_on_update,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CheckConstraintConfiguration":
        return cls(
            enabled=data.get("enabled", True),
            enforce_on_insert=data.get("enforce_on_insert", True),
            enforce_on_update=data.get("enforce_on_update", True),
        )


_SPARK_STATIC_KEYS = frozenset(
    {
        "spark.sql.extensions",
        "spark.sql.warehouse.dir",
    }
)
_SPARK_STATIC_PREFIXES = ("spark.sql.catalog.",)
_SPARK_CORE_PREFIXES = ("spark.driver.", "spark.executor.")
_SPARK_ADAPTER_OWNED_KEYS = frozenset(
    {
        "spark.app.name",
        "spark.plugins",
        "spark.memory.offheap.enabled",
        "spark.memory.offheap.size",
        "spark.shuffle.manager",
    }
)
_SPARK_CREDENTIAL_SUBSTRINGS = ("secret", "password", "token", "credential", "access.key")


def _canonicalize_spark_config(raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("Invalid 'spark' session-config map: expected a mapping of Spark key to value")
    canonical: dict[str, str] = {}
    seen: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Invalid 'spark' session-config map: every key must be a non-empty string")
        bare = key.strip()
        qualified = bare if bare.startswith("spark.") else f"spark.{bare}"
        if qualified in canonical:
            raise ValueError(
                f"Invalid 'spark' session-config map: duplicate key '{qualified}' "
                f"(from '{seen[qualified]}' and '{key}'); use only the fully-qualified 'spark.*' form"
            )
        if isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, int | float):
            text = str(value)
        elif isinstance(value, str):
            text = value
        else:
            raise ValueError(
                f"Invalid 'spark' session-config map: key '{qualified}' has unsupported value type "
                f"{type(value).__name__}; use only bool, int, float, or str"
            )
        seen[qualified] = key
        canonical[qualified] = text
    for qualified in canonical:
        lowered = qualified.lower()
        if lowered in _SPARK_STATIC_KEYS or lowered.startswith(_SPARK_STATIC_PREFIXES):
            raise ValueError(
                f"Invalid 'spark' session-config map: key '{qualified}' is static and cannot be changed "
                "with spark.conf.set; configure it through the adapter table-format or warehouse options instead"
            )
        if lowered.startswith(_SPARK_CORE_PREFIXES):
            raise ValueError(
                f"Invalid 'spark' session-config map: key '{qualified}' is a core deploy setting; "
                "set it through the adapter resource options instead"
            )
        if lowered in _SPARK_ADAPTER_OWNED_KEYS:
            raise ValueError(
                f"Invalid 'spark' session-config map: key '{qualified}' is owned by the adapter; "
                "configure it through the adapter options instead"
            )
        if any(marker in lowered for marker in _SPARK_CREDENTIAL_SUBSTRINGS):
            raise ValueError(
                f"Invalid 'spark' session-config map: key '{qualified}' looks like a credential; "
                "credentials are not accepted in the spark session-config map"
            )
    return dict(sorted(canonical.items()))


@dataclass
class PlatformOptimizationConfiguration:
    z_ordering_enabled: bool = False
    z_ordering_columns: list[str] = field(default_factory=list)
    liquid_clustering_enabled: bool = False
    liquid_clustering_columns: list[str] = field(default_factory=list)
    databricks_clustering_strategy: DatabricksClusteringStrategyType = "none"
    physical_rendering_id: Optional[DatabricksPhysicalRenderingType] = None
    sorted_ingestion_mode: SortedIngestionModeType = "off"
    sorted_ingestion_method: SortedIngestionMethodType = "auto"
    auto_optimize_enabled: bool = False
    auto_compact_enabled: bool = False
    bloom_filters_enabled: bool = False
    bloom_filter_columns: list[str] = field(default_factory=list)
    materialized_views_enabled: bool = False
    spark: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.spark = _canonicalize_spark_config(self.spark)
        valid_modes = {"off", "auto", "force"}
        valid_methods = {"auto", "ctas", "z_order", "hilbert", "liquid_clustering", "vacuum_sort"}
        valid_dbx_strategies = {"z_order", "liquid_clustering", "liquid_clustering_auto", "none"}
        valid_dbx_renderings = {"databricks_z_order", "databricks_liquid_manual", "databricks_liquid_auto"}
        if self.sorted_ingestion_mode not in valid_modes:
            raise ValueError(
                f"Invalid sorted_ingestion_mode: '{self.sorted_ingestion_mode}'. Must be one of {sorted(valid_modes)}."
            )
        if self.sorted_ingestion_method not in valid_methods:
            raise ValueError(
                f"Invalid sorted_ingestion_method: '{self.sorted_ingestion_method}'. "
                f"Must be one of {sorted(valid_methods)}."
            )
        if self.databricks_clustering_strategy not in valid_dbx_strategies:
            raise ValueError(
                f"Invalid databricks_clustering_strategy: '{self.databricks_clustering_strategy}'. "
                f"Must be one of {sorted(valid_dbx_strategies)}."
            )
        if self.physical_rendering_id is not None and self.physical_rendering_id not in valid_dbx_renderings:
            raise ValueError(
                f"Invalid physical_rendering_id: '{self.physical_rendering_id}'. "
                f"Must be one of {sorted(valid_dbx_renderings)}."
            )
        if self.sorted_ingestion_mode == "off" and self.sorted_ingestion_method != "auto":
            raise ValueError("sorted_ingestion_method must be 'auto' when sorted_ingestion_mode is 'off'")
        if self.liquid_clustering_columns and not self.liquid_clustering_enabled:
            raise ValueError("liquid_clustering_columns requires liquid_clustering_enabled=true")
        liquid_strategy = self.databricks_clustering_strategy in {"liquid_clustering", "liquid_clustering_auto"}
        liquid_requested = liquid_strategy or self.liquid_clustering_enabled or bool(self.liquid_clustering_columns)
        z_order_requested = self.z_ordering_enabled or bool(self.z_ordering_columns)
        if self.databricks_clustering_strategy == "none" and (
            self.liquid_clustering_enabled or self.liquid_clustering_columns or z_order_requested
        ):
            raise ValueError(
                "databricks_clustering_strategy='none' cannot be combined with liquid_clustering_enabled, "
                "liquid_clustering_columns, z_ordering_enabled, or z_ordering_columns"
            )
        if liquid_requested and z_order_requested:
            raise ValueError(
                "Databricks Liquid Clustering cannot be combined with z_ordering_enabled or z_ordering_columns; "
                "set z_ordering_enabled=false and remove z_ordering_columns, or use databricks_z_order."
            )
        if self.databricks_clustering_strategy == "liquid_clustering_auto" and self.liquid_clustering_columns:
            raise ValueError(
                "liquid_clustering_columns cannot be set with databricks_clustering_strategy='liquid_clustering_auto'; "
                "automatic Liquid Clustering uses CLUSTER BY AUTO and Databricks selects keys asynchronously."
            )
        if self.physical_rendering_id == "databricks_z_order" and liquid_requested:
            raise ValueError(
                "physical_rendering_id=databricks_z_order cannot be combined with Liquid Clustering fields"
            )
        if self.physical_rendering_id in {"databricks_liquid_manual", "databricks_liquid_auto"} and z_order_requested:
            raise ValueError(
                f"physical_rendering_id={self.physical_rendering_id} cannot be combined with ZORDER fields"
            )
        if self.databricks_clustering_strategy == "z_order" and (
            self.liquid_clustering_enabled or self.liquid_clustering_columns
        ):
            raise ValueError(
                "databricks_clustering_strategy='z_order' cannot be combined with liquid_clustering_enabled or "
                "liquid_clustering_columns; set databricks_clustering_strategy='liquid_clustering' for explicit keys "
                "or 'liquid_clustering_auto' for CLUSTER BY AUTO."
            )
        if self.physical_rendering_id == "databricks_liquid_auto" and (
            self.databricks_clustering_strategy != "liquid_clustering_auto"
        ):
            raise ValueError(
                "physical_rendering_id=databricks_liquid_auto requires "
                "databricks_clustering_strategy='liquid_clustering_auto'"
            )
        if self.physical_rendering_id == "databricks_liquid_manual" and (
            self.databricks_clustering_strategy == "liquid_clustering_auto" or not liquid_requested
        ):
            raise ValueError(
                "physical_rendering_id=databricks_liquid_manual requires manual Liquid Clustering "
                "(databricks_clustering_strategy='liquid_clustering' or liquid_clustering_enabled/columns)"
            )
        if self.physical_rendering_id == "databricks_z_order" and self.databricks_clustering_strategy != "z_order":
            raise ValueError(
                "physical_rendering_id=databricks_z_order requires databricks_clustering_strategy='z_order'"
            )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "z_ordering_enabled": self.z_ordering_enabled,
            "z_ordering_columns": self.z_ordering_columns,
            "liquid_clustering_enabled": self.liquid_clustering_enabled,
            "liquid_clustering_columns": self.liquid_clustering_columns,
            "databricks_clustering_strategy": self.databricks_clustering_strategy,
            "sorted_ingestion_mode": self.sorted_ingestion_mode,
            "sorted_ingestion_method": self.sorted_ingestion_method,
            "auto_optimize_enabled": self.auto_optimize_enabled,
            "auto_compact_enabled": self.auto_compact_enabled,
            "bloom_filters_enabled": self.bloom_filters_enabled,
            "bloom_filter_columns": self.bloom_filter_columns,
            "materialized_views_enabled": self.materialized_views_enabled,
        }
        if self.physical_rendering_id is not None:
            result["physical_rendering_id"] = self.physical_rendering_id
        if self.spark:
            result["spark"] = dict(sorted(self.spark.items()))
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlatformOptimizationConfiguration":
        sorted_ingestion_mode = data.get("sorted_ingestion_mode", data.get("deep_sort_mode", "off"))
        sorted_ingestion_method = data.get("sorted_ingestion_method", data.get("deep_sort_method", "auto"))
        strategy = data.get("databricks_clustering_strategy")
        if strategy is None and data.get("physical_rendering_id") == "databricks_z_order":
            strategy = "z_order"
        if strategy is None and (data.get("liquid_clustering_enabled", False) or data.get("liquid_clustering_columns")):
            strategy = "liquid_clustering"
        if strategy is None and (data.get("z_ordering_enabled", False) or data.get("z_ordering_columns")):
            strategy = "z_order"
        if strategy is None:
            strategy = "none"
        return cls(
            z_ordering_enabled=data.get("z_ordering_enabled", False),
            z_ordering_columns=data.get("z_ordering_columns", []),
            liquid_clustering_enabled=data.get("liquid_clustering_enabled", False),
            liquid_clustering_columns=data.get("liquid_clustering_columns", []),
            databricks_clustering_strategy=strategy,
            physical_rendering_id=data.get("physical_rendering_id"),
            sorted_ingestion_mode=sorted_ingestion_mode,
            sorted_ingestion_method=sorted_ingestion_method,
            auto_optimize_enabled=data.get("auto_optimize_enabled", False),
            auto_compact_enabled=data.get("auto_compact_enabled", False),
            bloom_filters_enabled=data.get("bloom_filters_enabled", False),
            bloom_filter_columns=data.get("bloom_filter_columns", []),
            materialized_views_enabled=data.get("materialized_views_enabled", False),
            spark=_canonicalize_spark_config(data.get("spark")),
        )


_PLATFORM_EFFECTIVE_LAYOUT_VALIDATORS: dict[str, str] = {
    "databricks": "benchbox.platforms.databricks.tuning_validation:validate_effective_layout",
}


def _get_effective_layout_validator(platform_key: str):
    target = _PLATFORM_EFFECTIVE_LAYOUT_VALIDATORS.get(platform_key)
    if target is None:
        return None

    import importlib

    module_path, _, func_name = target.partition(":")
    module = importlib.import_module(module_path)
    return getattr(module, func_name)


def _clickhouse_primary_key_prefix_errors(
    config: "UnifiedTuningConfiguration",
    schema_primary_keys: Mapping[str, Sequence[str]],
) -> list[str]:
    if not config.primary_keys.enabled:
        return []

    from benchbox.core.tuning.generators.clickhouse import clickhouse_sort_key_columns, primary_key_prefix_violation

    primary_keys_by_table = {str(name).upper(): columns for name, columns in schema_primary_keys.items()}
    errors: list[str] = []
    for table_name, table_tuning in config.table_tunings.items():
        primary_key_columns = primary_keys_by_table.get(str(table_name).upper())
        if not primary_key_columns:
            continue
        violation = primary_key_prefix_violation(
            table_name, primary_key_columns, clickhouse_sort_key_columns(table_tuning)
        )
        if violation:
            errors.append(violation)
    return errors


@dataclass
class UnifiedTuningConfiguration:
    primary_keys: PrimaryKeyConfiguration = field(default_factory=PrimaryKeyConfiguration)
    foreign_keys: ForeignKeyConfiguration = field(default_factory=ForeignKeyConfiguration)
    unique_constraints: UniqueConstraintConfiguration = field(default_factory=UniqueConstraintConfiguration)
    check_constraints: CheckConstraintConfiguration = field(default_factory=CheckConstraintConfiguration)

    platform_optimizations: PlatformOptimizationConfiguration = field(default_factory=PlatformOptimizationConfiguration)

    table_tunings: dict[str, TableTuning] = field(default_factory=dict)

    def enable_all_constraints(self) -> None:
        self.primary_keys.enabled = True
        self.foreign_keys.enabled = True
        self.unique_constraints.enabled = True
        self.check_constraints.enabled = True

    def disable_all_constraints(self) -> None:
        self.primary_keys.enabled = False
        self.foreign_keys.enabled = False
        self.unique_constraints.enabled = False
        self.check_constraints.enabled = False

    def enable_primary_keys(self) -> None:
        self.primary_keys.enabled = True

    def disable_primary_keys(self) -> None:
        self.primary_keys.enabled = False

    def enable_foreign_keys(self) -> None:
        self.foreign_keys.enabled = True

    def disable_foreign_keys(self) -> None:
        self.foreign_keys.enabled = False

    _TABLE_LAYOUT_TYPES = frozenset(
        {
            TuningType.PARTITIONING,
            TuningType.CLUSTERING,
            TuningType.DISTRIBUTION,
            TuningType.SORTING,
        }
    )

    def enable_platform_optimization(
        self,
        optimization_type: TuningType,
        benchmark: str = "tpch",
        columns: Optional[list[Any]] = None,
        table_name: Optional[str] = None,
        **kwargs,
    ) -> None:
        if optimization_type in self._TABLE_LAYOUT_TYPES:
            self._enable_table_layout(optimization_type, benchmark=benchmark, columns=columns, table_name=table_name)
            return
        column_values = columns if columns is not None else kwargs.get("columns")
        if optimization_type == TuningType.Z_ORDERING:
            self.platform_optimizations.z_ordering_enabled = True
            self.platform_optimizations.databricks_clustering_strategy = "z_order"
            if column_values is not None:
                self.platform_optimizations.z_ordering_columns = column_values
        elif optimization_type == TuningType.LIQUID_CLUSTERING:
            self.platform_optimizations.liquid_clustering_enabled = True
            self.platform_optimizations.databricks_clustering_strategy = "liquid_clustering"
            if column_values is not None:
                self.platform_optimizations.liquid_clustering_columns = column_values
        elif optimization_type == TuningType.AUTO_OPTIMIZE:
            self.platform_optimizations.auto_optimize_enabled = True
        elif optimization_type == TuningType.AUTO_COMPACT:
            self.platform_optimizations.auto_compact_enabled = True
        elif optimization_type == TuningType.BLOOM_FILTERS:
            self.platform_optimizations.bloom_filters_enabled = True
            if column_values is not None:
                self.platform_optimizations.bloom_filter_columns = column_values
        elif optimization_type == TuningType.MATERIALIZED_VIEWS:
            self.platform_optimizations.materialized_views_enabled = True

    def _enable_table_layout(
        self,
        layout_type: TuningType,
        benchmark: str = "tpch",
        columns: Optional[list[Any]] = None,
        table_name: Optional[str] = None,
    ) -> None:
        slot = layout_type.value
        resolved_columns = self._resolve_layout_columns(columns)
        if resolved_columns:
            target = self._explicit_layout_table(benchmark, table_name)
            self._record_layout_slot(target, slot, resolved_columns)
            return
        targets = self._default_layout_targets(layout_type, benchmark, table_name)
        if not targets:
            return
        for target_table, entry_columns in targets.items():
            self._record_layout_slot(target_table, slot, list(entry_columns))

    @staticmethod
    def _resolve_layout_columns(columns: Optional[list[Any]]) -> list[TuningColumn]:
        if not columns:
            return []
        resolved: list[TuningColumn] = []
        for position, column in enumerate(columns, start=1):
            if isinstance(column, TuningColumn):
                resolved.append(column)
            elif isinstance(column, dict):
                payload = dict(column)
                payload.setdefault("order", position)
                payload.setdefault("type", "UNKNOWN")
                resolved.append(TuningColumn.from_dict(payload))
            else:
                resolved.append(TuningColumn(name=str(column), type="UNKNOWN", order=position))
        return resolved

    @staticmethod
    def _read_template_table_tunings(benchmark: str) -> dict[str, Any]:
        from benchbox.core.tuning.packaged_templates import packaged_template_path

        template = packaged_template_path("duckdb", benchmark.lower())
        if not template.exists():
            return {}
        try:
            import yaml
        except ImportError:
            return {}
        try:
            payload = yaml.safe_load(template.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return {}
        table_tunings = payload.get("table_tunings") or {}
        return dict(table_tunings) if isinstance(table_tunings, dict) else {}

    @staticmethod
    def _first_template_column(entry: Any) -> list[TuningColumn]:
        if isinstance(entry, dict):
            for slot in ("partitioning", "clustering", "distribution", "sorting"):
                raw = entry.get(slot) or []
                if isinstance(raw, list) and raw:
                    first = dict(raw[0])
                    first["order"] = 1
                    return [TuningColumn.from_dict(first)]
        return []

    def _record_layout_slot(self, target_table: str, slot: str, entry_columns: list[TuningColumn]) -> None:
        existing = self.table_tunings.get(target_table)
        if existing is not None:
            setattr(existing, slot, list(entry_columns))
            return
        kwargs: dict[str, Any] = {"table_name": target_table, slot: list(entry_columns)}
        self.table_tunings[target_table] = TableTuning(**kwargs)

    @staticmethod
    def _explicit_layout_table(benchmark: str, table_name: Optional[str]) -> str:
        table_tunings = UnifiedTuningConfiguration._read_template_table_tunings(benchmark)
        ordered = sorted(table_tunings) if table_tunings else []
        if table_name is not None:
            match = next((name for name in ordered if name.lower() == table_name.lower()), None)
            return match if match is not None else table_name
        if ordered:
            return ordered[0]
        return "LINEITEM"

    @staticmethod
    def _default_layout_targets(
        layout_type: TuningType,
        benchmark: str,
        table_name: Optional[str],
    ) -> dict[str, list[TuningColumn]]:
        if layout_type == TuningType.PARTITIONING:
            table_tunings = UnifiedTuningConfiguration._read_default_partitioning(benchmark)
        else:
            table_tunings = UnifiedTuningConfiguration._read_template_table_tunings(benchmark.lower())
        return UnifiedTuningConfiguration._layout_targets_from(table_tunings, layout_type, table_name)

    @staticmethod
    def _read_default_partitioning(benchmark: str) -> dict[str, Any]:
        from pathlib import Path

        try:
            import yaml
        except ImportError:
            return {}
        path = Path(__file__).resolve().parent / "profiles" / "default_partitioning.yaml"
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return {}
        tables = payload.get(benchmark.lower())
        if not isinstance(tables, dict):
            return {}
        return {name: {"partitioning": columns} for name, columns in tables.items()}

    @staticmethod
    def _layout_targets_from(
        table_tunings: dict[str, Any],
        layout_type: TuningType,
        table_name: Optional[str],
    ) -> dict[str, list[TuningColumn]]:
        if not table_tunings:
            return {}
        ordered_tables = sorted(table_tunings)
        if table_name is not None:
            match = next(
                (name for name in ordered_tables if name.lower() == table_name.lower()),
                None,
            )
            target = match if match is not None else table_name
            entry = table_tunings.get(target)
            raw_columns: list[dict[str, Any]] = []
            if isinstance(entry, dict):
                raw_entry = entry.get(layout_type.value) or []
                raw_columns = list(raw_entry) if isinstance(raw_entry, list) else []
            if raw_columns:
                return {target: [TuningColumn.from_dict(col) for col in raw_columns]}
            return {}
        targets: dict[str, list[TuningColumn]] = {}
        for name in ordered_tables:
            entry = table_tunings.get(name)
            raw_columns = []
            if isinstance(entry, dict):
                raw_entry = entry.get(layout_type.value) or []
                raw_columns = list(raw_entry) if isinstance(raw_entry, list) else []
            if raw_columns:
                targets[name] = [TuningColumn.from_dict(col) for col in raw_columns]
        return targets

    def disable_platform_optimization(self, optimization_type: TuningType) -> None:
        if optimization_type in self._TABLE_LAYOUT_TYPES:
            slot = optimization_type.value
            for table_name in list(self.table_tunings):
                entry = self.table_tunings[table_name]
                setattr(entry, slot, None)
                if not entry.has_any_tuning():
                    del self.table_tunings[table_name]
            return
        if optimization_type == TuningType.Z_ORDERING:
            self.platform_optimizations.z_ordering_enabled = False
            self.platform_optimizations.z_ordering_columns = []
            if self.platform_optimizations.databricks_clustering_strategy == "z_order":
                self.platform_optimizations.databricks_clustering_strategy = "none"
        elif optimization_type == TuningType.LIQUID_CLUSTERING:
            self.platform_optimizations.liquid_clustering_enabled = False
        elif optimization_type == TuningType.AUTO_OPTIMIZE:
            self.platform_optimizations.auto_optimize_enabled = False
        elif optimization_type == TuningType.AUTO_COMPACT:
            self.platform_optimizations.auto_compact_enabled = False
        elif optimization_type == TuningType.BLOOM_FILTERS:
            self.platform_optimizations.bloom_filters_enabled = False
        elif optimization_type == TuningType.MATERIALIZED_VIEWS:
            self.platform_optimizations.materialized_views_enabled = False

    _CONSTRAINT_CHECKS = [
        ("primary_keys", TuningType.PRIMARY_KEYS),
        ("foreign_keys", TuningType.FOREIGN_KEYS),
        ("unique_constraints", TuningType.UNIQUE_CONSTRAINTS),
        ("check_constraints", TuningType.CHECK_CONSTRAINTS),
    ]
    _OPT_CHECKS = [
        ("z_ordering_enabled", TuningType.Z_ORDERING),
        ("liquid_clustering_enabled", TuningType.LIQUID_CLUSTERING),
        ("auto_optimize_enabled", TuningType.AUTO_OPTIMIZE),
        ("auto_compact_enabled", TuningType.AUTO_COMPACT),
        ("bloom_filters_enabled", TuningType.BLOOM_FILTERS),
        ("materialized_views_enabled", TuningType.MATERIALIZED_VIEWS),
    ]
    _TABLE_TUNING_CHECKS = [
        ("partitioning", TuningType.PARTITIONING),
        ("clustering", TuningType.CLUSTERING),
        ("distribution", TuningType.DISTRIBUTION),
        ("sorting", TuningType.SORTING),
    ]
    _SPARK_SESSION_CONFIG_PLATFORMS = frozenset({"spark", "velox", "lakesail"})

    def get_enabled_tuning_types(self) -> set[TuningType]:
        enabled_types: set[TuningType] = set()

        for attr, tt in self._CONSTRAINT_CHECKS:
            if getattr(self, attr).enabled:
                enabled_types.add(tt)
        for attr, tt in self._OPT_CHECKS:
            if getattr(self.platform_optimizations, attr):
                enabled_types.add(tt)
        for table_tuning in self.table_tunings.values():
            for attr, tt in self._TABLE_TUNING_CHECKS:
                if getattr(table_tuning, attr):
                    enabled_types.add(tt)

        return enabled_types

    def validate_for_platform(self, platform: str) -> list[str]:
        errors, _warnings = self.validate_for_platform_detailed(platform)
        return errors

    def validate_for_platform_detailed(
        self,
        platform: str,
        schema_primary_keys: Mapping[str, Sequence[str]] | None = None,
    ) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        enabled_types = self.get_enabled_tuning_types()
        platform_known = TuningType.is_known_platform(platform)

        for tuning_type in enabled_types:
            if tuning_type.is_compatible_with_platform(platform):
                continue
            message = f"Tuning type '{tuning_type.value}' is not supported by platform '{platform}'"
            if tuning_type in _CONSTRAINT_TUNING_TYPES or not platform_known:
                warnings.append(message)
            else:
                errors.append(message)

        platform_key = platform.lower().replace("_", "-")
        if self.platform_optimizations.spark and platform_key not in self._SPARK_SESSION_CONFIG_PLATFORMS:
            warnings.append(
                f"'spark' session-config map is not applied by platform '{platform}'; "
                "run on spark, velox, or lakesail to apply it"
            )
        validator = _get_effective_layout_validator(platform_key)
        if validator is not None:
            errors.extend(validator(self))
        if schema_primary_keys:
            from benchbox.core.tuning.capability_registry import resolve_platform_key

            if resolve_platform_key(platform_key) == "clickhouse":
                errors.extend(_clickhouse_primary_key_prefix_errors(self, schema_primary_keys))
        return errors, warnings

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_keys": self.primary_keys.to_dict(),
            "foreign_keys": self.foreign_keys.to_dict(),
            "unique_constraints": self.unique_constraints.to_dict(),
            "check_constraints": self.check_constraints.to_dict(),
            "platform_optimizations": self.platform_optimizations.to_dict(),
            "table_tunings": {
                table_name: table_tuning.to_dict() for table_name, table_tuning in self.table_tunings.items()
            },
        }

    def get_configuration_hash(self) -> str:
        canonical_json = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UnifiedTuningConfiguration":
        instance = cls()

        if "primary_keys" in data:
            instance.primary_keys = PrimaryKeyConfiguration.from_dict(data["primary_keys"])
        if "foreign_keys" in data:
            instance.foreign_keys = ForeignKeyConfiguration.from_dict(data["foreign_keys"])
        if "unique_constraints" in data:
            instance.unique_constraints = UniqueConstraintConfiguration.from_dict(data["unique_constraints"])
        if "check_constraints" in data:
            instance.check_constraints = CheckConstraintConfiguration.from_dict(data["check_constraints"])

        if "platform_optimizations" in data:
            instance.platform_optimizations = PlatformOptimizationConfiguration.from_dict(
                data["platform_optimizations"]
            )

        if "table_tunings" in data:
            for table_name, table_data in data["table_tunings"].items():
                instance.table_tunings[table_name] = TableTuning.from_dict(table_data)

        platform_data = data.get("platform_optimizations", {})
        if (
            "databricks_clustering_strategy" not in platform_data
            and instance.platform_optimizations.databricks_clustering_strategy == "none"
            and any(
                table_tuning.clustering or table_tuning.distribution for table_tuning in instance.table_tunings.values()
            )
        ):
            instance.platform_optimizations.databricks_clustering_strategy = "z_order"

        return instance
