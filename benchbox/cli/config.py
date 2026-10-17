# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field

from benchbox.core.config_utils import (
    deep_merge_dicts,
    load_config_file,
    save_config_file,
)
from benchbox.core.tuning.interface import (
    BenchmarkTunings,
    TableTuning,
    TuningColumn,
    TuningType,
    UnifiedTuningConfiguration,
)
from benchbox.utils.config_interface import ConfigInterface, set_config_provider
from benchbox.utils.database_naming import generate_database_filename
from benchbox.utils.printing import quiet_console
from benchbox.utils.scale_factor import format_scale_factor

console = quiet_console

_CONFIG_VALIDATION_RULES: list[tuple[str, int, Any, str]] = [
    ("benchmarks.default_scale", 0, lambda v: v > 0, "Invalid default scale factor"),
    ("benchmarks.timeout_minutes", 0, lambda v: v > 0, "Invalid timeout value"),
    ("execution.max_workers", 0, lambda v: v > 0, "Invalid max workers value"),
    ("execution.power_run.iterations", 4, lambda v: v > 0, "Invalid power run iterations value"),
    ("execution.power_run.warm_up_iterations", 0, lambda v: v >= 0, "Invalid power run warm-up iterations value"),
    (
        "execution.power_run.timeout_per_iteration_minutes",
        60,
        lambda v: v > 0,
        "Invalid power run timeout value",
    ),
    ("execution.concurrent_queries.max_concurrent", 2, lambda v: v > 0, "Invalid max concurrent queries value"),
    (
        "execution.concurrent_queries.query_timeout_seconds",
        300,
        lambda v: v > 0,
        "Invalid concurrent query timeout value",
    ),
    (
        "execution.concurrent_queries.stream_timeout_seconds",
        3600,
        lambda v: v >= 0,
        "Invalid concurrent stream timeout value",
    ),
    ("execution.concurrent_queries.max_retries", 3, lambda v: v >= 0, "Invalid max retries value"),
]


def load_config(
    cli_args: Optional[dict[str, Any]] = None,
    config_file: Optional[Path] = None,
    validate: bool = True,
) -> "BenchBoxConfig":
    manager = ConfigManager(config_path=config_file)

    config_dict = manager.config.model_dump()

    config_dict = _apply_environment_overrides(config_dict)

    if cli_args:
        config_dict = deep_merge_dicts(config_dict, cli_args)

    config = BenchBoxConfig(**config_dict)

    if validate:
        manager.config = config
        if not manager.validate_config():
            raise ValueError("Configuration validation failed")

    return config


def _apply_environment_overrides(config_dict: dict[str, Any]) -> dict[str, Any]:
    env_mappings = {
        "BENCHBOX_DATABASE_PREFERRED": ("database", "preferred", str),
        "BENCHBOX_SCALE_FACTOR": ("benchmarks", "default_scale", float),
        "BENCHBOX_VERBOSE": ("execution", "verbose", lambda v: v.lower() in ["true", "1", "yes", "on"]),
        "BENCHBOX_MAX_WORKERS": ("execution", "max_workers", int),
        "BENCHBOX_TUNING_CONFIG": ("tuning", "default_config_file", str),
        "BENCHBOX_MEMORY_LIMIT_GB": ("execution", "memory_limit_gb", int),
    }

    for env_var, (section, key, converter) in env_mappings.items():
        env_value = os.getenv(env_var)
        if env_value is not None:
            try:
                converted_value = converter(env_value)
                if section not in config_dict:
                    config_dict[section] = {}
                config_dict[section][key] = converted_value
            except (ValueError, TypeError) as e:
                console.print(f"[yellow]Warning: Invalid environment variable {env_var}={env_value}: {e}[/yellow]")

    return config_dict


class BenchBoxConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    system: dict[str, Any] = Field(default_factory=dict)
    database: dict[str, Any] = Field(default_factory=dict)
    benchmarks: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    execution: dict[str, Any] = Field(default_factory=dict)
    tuning: dict[str, Any] = Field(default_factory=dict)


SPARK_SESSION_CONFIG_EXAMPLE = """
# Spark session settings. BenchBox sets each key with spark.conf.set after the
# session starts, reads it back, and records it in the applied-tuning ledger.
# To use them, move this block under platform_optimizations above and remove
# the leading '#'. Keys may omit the 'spark.' prefix. Static keys (warehouse,
# catalogs, extensions), core deploy keys (spark.driver.*, spark.executor.*),
# adapter-owned keys and credentials are rejected; set those through the
# adapter options instead.
#
#   spark:
#     spark.sql.shuffle.partitions: "200"
#     spark.sql.adaptive.enabled: "true"
#     spark.sql.autoBroadcastJoinThreshold: "10485760"
"""


class ConfigManager:
    def __init__(self, config_path: Optional[Path] = None, *, strict: bool = False):
        self.console = quiet_console
        self.strict = strict
        self.config_path = config_path or self._get_default_config_path()
        self.config = self._load_config()
        self.apply_environment_overrides()

    def _get_default_config_path(self) -> Path:
        current_config = Path("benchbox.yaml")
        if current_config.exists():
            return current_config

        home_config = Path.home() / ".benchbox" / "config.yaml"
        return home_config

    def _load_config(self) -> BenchBoxConfig:
        try:
            if self.strict and not self.config_path.exists():
                raise FileNotFoundError(f"Configuration file not found: {self.config_path}")

            if self.config_path.exists():
                try:
                    with open(self.config_path, encoding="utf-8") as f:
                        config_data = yaml.safe_load(f) or {}

                    if (
                        not config_data
                        or not isinstance(config_data, dict)
                        or not any(
                            key in config_data
                            for key in [
                                "system",
                                "database",
                                "benchmarks",
                                "output",
                                "execution",
                            ]
                        )
                    ):
                        if self.strict:
                            raise ValueError(
                                f"Configuration file {self.config_path} is empty or has no recognized sections"
                            )
                        return self._get_default_config()

                    default_config = self._get_default_config()
                    merged_config = deep_merge_dicts(default_config.model_dump(), config_data)
                    return BenchBoxConfig(**merged_config)
                except Exception as e:
                    if self.strict:
                        raise ValueError(f"Failed to load config from {self.config_path}: {e}") from e
                    console.print(f"[yellow]Warning: Failed to load config from {self.config_path}: {e}[/yellow]")
                    return self._get_default_config()
            else:
                return self._get_default_config()
        except (PermissionError, OSError) as e:
            if self.strict:
                raise ValueError(f"Failed to access config from {self.config_path}: {e}") from e
            console.print(f"[yellow]Warning: Permission denied accessing {self.config_path}: {e}[/yellow]")
            return self._get_default_config()

    def _get_default_config(self) -> BenchBoxConfig:
        return BenchBoxConfig(
            system={
                "auto_profile": True,
                "save_profile": True,
                "profile_cache_hours": 24,
            },
            database={
                "preferred": "duckdb",
                "connection_timeout": 30,
                "auto_detect": True,
            },
            benchmarks={
                "default_scale": 0.01,
                "timeout_minutes": 60,
                "max_memory_gb": 8,
                "continue_on_error": False,
            },
            output={
                "formats": ["json", "console"],
                "timestamp_format": "%Y%m%d_%H%M%S",
                "submit_to_service": False,
                "service_url": "https://api.benchbox.dev/v1",
                "compression": {
                    "enabled": True,
                    "type": "zstd",
                    "level": None,
                },
            },
            execution={
                "parallel_queries": False,
                "max_workers": 4,
                "memory_limit_gb": 0,
                "verbose": True,
                "power_run": {
                    "iterations": 3,
                    "warm_up_iterations": 1,
                    "timeout_per_iteration_minutes": 60,
                    "fail_fast": False,
                    "collect_metrics": True,
                },
                "concurrent_queries": {
                    "enabled": False,
                    "max_concurrent": 2,
                    "query_timeout_seconds": 300,
                    "cancel_on_timeout": False,
                    "retry_failed_queries": True,
                    "max_retries": 3,
                },
            },
            tuning={
                "default_config_file": None,
                "validate_on_load": True,
                "allow_platform_incompatible": False,
                "environment_overrides": {
                    "BENCHBOX_TUNING_CONFIG": "default_config_file",
                },
            },
        )

    def save_config(self):
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)

            config_dict = self.config.model_dump()

            config_dict["_metadata"] = {
                "version": "1.0",
                "created": datetime.now().isoformat(),
                "generated_by": "benchbox-cli",
            }

            with open(self.config_path, "w", encoding="utf-8") as f:
                yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False)

            console.print(f"[green]✅ Configuration saved to {self.config_path}[/green]")
        except Exception as e:
            console.print(f"[red]❌ Failed to save configuration: {e}[/red]")

    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split(".")
        value = self.config.model_dump()

        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default

        return value

    def set(self, key: str, value: Any):
        keys = key.split(".")
        config_dict = self.config.model_dump()
        current = config_dict

        for k in keys[:-1]:
            if k not in current:
                current[k] = {}
            current = current[k]

        current[keys[-1]] = value
        self.config = BenchBoxConfig(**config_dict)

    def update_from_system_profile(self, profile):
        self.set("system.detected_os", profile.os_name)
        self.set("system.detected_arch", profile.architecture)
        self.set("system.detected_memory_gb", profile.memory_total_gb)
        self.set("system.detected_cpu_cores", profile.cpu_cores_logical)
        self.set("system.available_databases", profile.available_databases)
        self.set("system.last_profile_time", profile.timestamp.isoformat())

        if profile.memory_total_gb > 0:
            memory_limit = max(1, int(profile.memory_total_gb * 0.75))
            self.set("benchmarks.max_memory_gb", memory_limit)
            self.set("execution.memory_limit_gb", memory_limit)

        max_workers = min(8, max(2, profile.cpu_cores_logical // 2))
        self.set("execution.max_workers", max_workers)

        concurrent_max = min(max_workers, max(2, profile.cpu_cores_logical // 4))
        self.set("execution.concurrent_queries.max_concurrent", concurrent_max)

        if profile.memory_total_gb < 8:
            self.set("execution.power_run.timeout_per_iteration_minutes", 120)
            self.set("execution.concurrent_queries.query_timeout_seconds", 600)
        elif profile.memory_total_gb > 16:
            self.set("execution.power_run.timeout_per_iteration_minutes", 45)
            self.set("execution.concurrent_queries.query_timeout_seconds", 180)

        if "duckdb" in profile.available_databases:
            self.set("database.preferred", "duckdb")
        elif profile.available_databases:
            self.set("database.preferred", profile.available_databases[0])

    def validate_config(self) -> bool:
        try:
            for key_path, default, check_fn, error_msg in _CONFIG_VALIDATION_RULES:
                if not check_fn(self.get(key_path, default)):
                    console.print(f"[red]❌ {error_msg}[/red]")
                    return False

            console.print("[green]✅ Configuration validation passed[/green]")
            return True
        except Exception as e:
            console.print(f"[red]❌ Configuration validation failed: {e}[/red]")
            return False

    def show_config(self):
        from rich.syntax import Syntax

        config_yaml = yaml.dump(self.config.model_dump(), default_flow_style=False, sort_keys=False)
        syntax = Syntax(config_yaml, "yaml", theme="monokai", line_numbers=True)

        console.print("\n[bold]Current Configuration:[/bold]")
        console.print(f"Config file: [cyan]{self.config_path}[/cyan]")
        console.print(syntax)

    def create_sample_config(self, path: Optional[Path] = None):
        if path is None:
            path = Path("benchbox.yaml")

        sample_config = self._get_default_config()

        config_dict = sample_config.model_dump()
        config_dict["_comments"] = {
            "system": "System profiling and detection settings",
            "database": "Database connection and preference settings",
            "benchmarks": "Default benchmark execution parameters",
            "output": "Result formatting and export settings",
            "execution": "Performance and execution control settings",
            "execution.power_run": "Configuration for power run test iterations and metrics collection",
            "execution.concurrent_queries": "Configuration for concurrent query execution and throughput testing",
            "tuning": "Table tuning and optimization settings",
        }

        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False)

        console.print(f"[green]✅ Sample configuration created at {path}[/green]")
        console.print("Edit this file to customize BenchBox behavior.")

    def load_tuning_config(self, config_path: Union[str, Path]) -> dict[str, BenchmarkTunings]:
        config_path = Path(config_path)

        try:
            config_data = load_config_file(config_path)
            if not config_data:
                raise ValueError("Configuration file is empty")

            benchmark_tunings = {}

            for benchmark_name, benchmark_data in config_data.items():
                if benchmark_name.startswith("_"):
                    continue

                if not isinstance(benchmark_data, dict):
                    raise ValueError(f"Invalid benchmark configuration for '{benchmark_name}': must be a dictionary")

                tunings = BenchmarkTunings(benchmark_name=benchmark_name)

                for table_name, table_data in benchmark_data.items():
                    if table_name.startswith("_"):
                        continue

                    table_tuning = self._parse_table_tuning(table_name, table_data)
                    tunings.add_table_tuning(table_tuning)

                benchmark_tunings[benchmark_name] = tunings

            if self.get("tuning.validate_on_load", True):
                self._validate_tuning_configs(benchmark_tunings)

            return benchmark_tunings

        except (yaml.YAMLError, json.JSONDecodeError) as e:
            raise ValueError(f"Failed to parse tuning configuration file: {e}") from e
        except Exception as e:
            raise ValueError(f"Error loading tuning configuration: {e}") from e

    def _parse_table_tuning(self, table_name: str, table_data: dict[str, Any]) -> TableTuning:
        partitioning: Optional[list[TuningColumn]] = None
        clustering: Optional[list[TuningColumn]] = None
        distribution: Optional[list[TuningColumn]] = None
        sorting: Optional[list[TuningColumn]] = None

        for tuning_type_str in [
            "partitioning",
            "clustering",
            "distribution",
            "sorting",
        ]:
            if tuning_type_str in table_data:
                columns_data = table_data[tuning_type_str]
                if not isinstance(columns_data, list):
                    raise ValueError(f"'{tuning_type_str}' must be a list of column configurations")

                columns = []
                for i, col_data in enumerate(columns_data):
                    if isinstance(col_data, str):
                        columns.append(TuningColumn(name=col_data, type="UNKNOWN", order=i + 1))
                    elif isinstance(col_data, dict):
                        columns.append(TuningColumn.from_dict(col_data))
                    else:
                        raise ValueError(f"Invalid column configuration at index {i} in {tuning_type_str}")

                if tuning_type_str == "partitioning":
                    partitioning = columns
                elif tuning_type_str == "clustering":
                    clustering = columns
                elif tuning_type_str == "distribution":
                    distribution = columns
                elif tuning_type_str == "sorting":
                    sorting = columns

        return TableTuning(
            table_name=table_name,
            partitioning=partitioning,
            clustering=clustering,
            distribution=distribution,
            sorting=sorting,
        )

    def _validate_tuning_configs(self, benchmark_tunings: dict[str, BenchmarkTunings]) -> None:
        for benchmark_name, tunings in benchmark_tunings.items():
            if not tunings.has_valid_tunings():
                validation_results = tunings.validate_all()

                error_messages = []
                for table_name, errors in validation_results.items():
                    if errors:
                        error_messages.extend([f"{table_name}: {error}" for error in errors])

                if error_messages:
                    console.print(f"[red]⚠️ Tuning validation errors in '{benchmark_name}':[/red]")
                    for msg in error_messages:
                        console.print(f"  - {msg}")

                    if not self.get("tuning.allow_platform_incompatible", False):
                        raise ValueError(f"Invalid tuning configuration for benchmark '{benchmark_name}'")

    def save_tuning_config(
        self,
        benchmark_tunings: dict[str, BenchmarkTunings],
        config_path: Union[str, Path],
        format: str = "yaml",
    ) -> None:
        config_path = Path(config_path)

        config_data = {}
        for benchmark_name, tunings in benchmark_tunings.items():
            config_data[benchmark_name] = tunings.to_dict()["table_tunings"]

        config_data["_metadata"] = {
            "version": "1.0",
            "created": datetime.now().isoformat(),
            "generated_by": "benchbox-cli",
        }

        try:
            save_config_file(config_data, config_path, format)
            console.print(f"[green]✅ Tuning configuration saved to {config_path}[/green]")

        except Exception as e:
            console.print(f"[red]❌ Failed to save tuning configuration: {e}[/red]")
            raise

    def apply_environment_overrides(self) -> None:
        env_overrides = self.get("tuning.environment_overrides", {})

        for env_var, config_key in env_overrides.items():
            env_value = os.getenv(env_var)
            if env_value is not None:
                if config_key == "enabled":
                    value = env_value.lower() in ["true", "1", "yes", "on", "enabled"]
                    self.set(f"tuning.{config_key}", value)
                else:
                    self.set(f"tuning.{config_key}", env_value)

                console.print(f"[yellow]Applied environment override: {env_var}={env_value}[/yellow]")

    def load_unified_tuning_config(
        self, config_path: Union[str, Path], platform: Optional[str] = None
    ) -> UnifiedTuningConfiguration:
        config_path = Path(config_path)

        try:
            config_data = load_config_file(config_path)
            if not config_data:
                raise ValueError("Configuration file is empty")

            unified_config = UnifiedTuningConfiguration.from_dict(config_data)

            if self.get("tuning.validate_on_load", True):
                self._validate_unified_tuning_config(unified_config, platform)

            return unified_config

        except (yaml.YAMLError, json.JSONDecodeError) as e:
            raise ValueError(f"Failed to parse unified tuning configuration file: {e}") from e
        except Exception as e:
            raise ValueError(f"Error loading unified tuning configuration: {e}") from e

    def save_unified_tuning_config(
        self,
        config: UnifiedTuningConfiguration,
        config_path: Union[str, Path],
        format: str = "yaml",
    ) -> None:
        config_path = Path(config_path)

        config_data = config.to_dict()

        config_data["_metadata"] = {
            "version": "2.0",
            "format": "unified_tuning",
            "created": datetime.now().isoformat(),
            "generated_by": "benchbox-cli",
        }

        try:
            save_config_file(config_data, config_path, format)
            console.print(f"[green]✅ Unified tuning configuration saved to {config_path}[/green]")

        except Exception as e:
            console.print(f"[red]❌ Failed to save unified tuning configuration: {e}[/red]")
            raise

    def _validate_unified_tuning_config(
        self, config: UnifiedTuningConfiguration, platform: Optional[str] = None
    ) -> None:
        if platform is None:
            platform = self.get("database.preferred", "duckdb")

        constraint_errors = []

        if config.primary_keys.enabled is None:
            constraint_errors.append("primary_keys.enabled must be explicitly specified (true or false)")

        if config.foreign_keys.enabled is None:
            constraint_errors.append("foreign_keys.enabled must be explicitly specified (true or false)")

        platform_errors, platform_warnings = config.validate_for_platform_detailed(platform)

        if platform_warnings:
            console.print(f"[yellow]⚠️ Unified tuning validation warnings for platform '{platform}':[/yellow]")
            for warning in platform_warnings:
                console.print(f"  - {warning}")

        all_errors = constraint_errors + platform_errors

        if all_errors:
            console.print(f"[red]⚠️ Unified tuning validation errors for platform '{platform}':[/red]")
            for error in all_errors:
                console.print(f"  - {error}")

            if not self.get("tuning.allow_platform_incompatible", False):
                raise ValueError(f"Invalid unified tuning configuration for platform '{platform}'")

    def create_sample_unified_tuning_config(self, path: Optional[Path] = None, platform: Optional[str] = None) -> None:
        if path is None:
            path = Path("unified_tuning.yaml")

        if platform is None:
            platform = self.get("database.preferred", "duckdb")

        sample_config = UnifiedTuningConfiguration()

        sample_config.primary_keys.enabled = True
        sample_config.foreign_keys.enabled = True
        sample_config.foreign_keys.on_delete_action = "CASCADE"

        sample_config.unique_constraints.enabled = True
        sample_config.check_constraints.enabled = True

        if platform.lower() == "databricks":
            sample_config.platform_optimizations.z_ordering_enabled = True
            sample_config.platform_optimizations.z_ordering_columns = [
                "date_col",
                "partition_col",
            ]
            sample_config.platform_optimizations.databricks_clustering_strategy = "z_order"
            sample_config.platform_optimizations.liquid_clustering_enabled = False
            sample_config.platform_optimizations.liquid_clustering_columns = []
            sample_config.platform_optimizations.sorted_ingestion_mode = "off"
            sample_config.platform_optimizations.sorted_ingestion_method = "auto"
            sample_config.platform_optimizations.auto_optimize_enabled = True
            sample_config.platform_optimizations.bloom_filters_enabled = True
            sample_config.platform_optimizations.bloom_filter_columns = [
                "customer_id",
                "product_id",
            ]
        elif platform.lower() in ["snowflake", "bigquery"]:
            sample_config.platform_optimizations.materialized_views_enabled = True
        elif platform.lower() in ["postgresql", "redshift"]:
            sample_config.platform_optimizations.bloom_filters_enabled = True
            sample_config.platform_optimizations.bloom_filter_columns = ["customer_id"]
            sample_config.platform_optimizations.materialized_views_enabled = True

        from benchbox.core.tuning.interface import TableTuning, TuningColumn

        if TuningType.PARTITIONING.is_compatible_with_platform(platform):
            sample_config.table_tunings["orders"] = TableTuning(
                table_name="orders",
                partitioning=[TuningColumn(name="order_date", type="DATE", order=1)],
            )

            sample_config.table_tunings["lineitem"] = TableTuning(
                table_name="lineitem",
                partitioning=[TuningColumn(name="shipdate", type="DATE", order=1)],
            )

        if TuningType.CLUSTERING.is_compatible_with_platform(platform):
            if "orders" in sample_config.table_tunings:
                sample_config.table_tunings["orders"].clustering = [
                    TuningColumn(name="customer_id", type="INTEGER", order=1)
                ]

        if TuningType.SORTING.is_compatible_with_platform(platform):
            if "lineitem" not in sample_config.table_tunings:
                sample_config.table_tunings["lineitem"] = TableTuning(table_name="lineitem")

            sample_config.table_tunings["lineitem"].sorting = [
                TuningColumn(name="orderkey", type="INTEGER", order=1),
                TuningColumn(name="linenumber", type="INTEGER", order=2),
            ]

        from benchbox.core.tuning.capability_registry import get_capability, unapplied_reason

        platform_known = TuningType.is_known_platform(platform)
        for tuning_type in list(sample_config.get_enabled_tuning_types()):
            capability = get_capability(platform, tuning_type)
            if capability is not None:
                if unapplied_reason(capability, sample_config.platform_optimizations.sorted_ingestion_mode):
                    sample_config.disable_tuning_type(tuning_type)
            elif platform_known and not tuning_type.is_compatible_with_platform(platform):
                sample_config.disable_tuning_type(tuning_type)

        self.save_unified_tuning_config(sample_config, path)
        if platform.split(":", 1)[0].strip().lower() in UnifiedTuningConfiguration._SPARK_SESSION_CONFIG_PLATFORMS:
            with Path(path).open("a", encoding="utf-8") as handle:
                handle.write(SPARK_SESSION_CONFIG_EXAMPLE)
        console.print(
            f"[green]✅ Sample unified tuning configuration created for platform '{platform}' at {path}[/green]"
        )

        _print_sample_summary(platform, sample_config.get_enabled_tuning_types())


_SAMPLE_CONSTRAINT_LABELS = (
    (TuningType.PRIMARY_KEYS, "primary keys"),
    (TuningType.FOREIGN_KEYS, "foreign keys"),
    (TuningType.UNIQUE_CONSTRAINTS, "unique"),
    (TuningType.CHECK_CONSTRAINTS, "check"),
)
_SAMPLE_LAYOUT_TYPES = frozenset(
    {TuningType.PARTITIONING, TuningType.CLUSTERING, TuningType.SORTING, TuningType.DISTRIBUTION}
)


def _print_sample_summary(platform: str, enabled_types: set[TuningType]) -> None:
    console.print("This configuration includes:")
    constraint_labels = [label for tuning_type, label in _SAMPLE_CONSTRAINT_LABELS if tuning_type in enabled_types]
    if constraint_labels:
        console.print(f"  • Schema constraints ({', '.join(constraint_labels)})")
    if any(t.value.startswith(("z_ordering", "auto_", "bloom_", "materialized_")) for t in enabled_types):
        console.print(f"  • Platform optimizations for {platform}")
    if enabled_types & _SAMPLE_LAYOUT_TYPES:
        console.print("  • Table-level tunings (partitioning, clustering, sorting)")
    console.print(f"All settings are compatible with {platform}.")
    console.print("Edit this file to customize your tuning settings.")


class DirectoryManager:
    def __init__(self, base_dir: Optional[str] = None):
        from benchbox.utils.path_utils import resolve_benchmark_runs_dir

        self.base_dir = Path(base_dir) if base_dir else resolve_benchmark_runs_dir()
        self.results_dir = self.base_dir / "results"
        self.datagen_dir = self.base_dir / "datagen"
        self.databases_dir = self.base_dir / "databases"

        self._ensure_directories()

    def _ensure_directories(self):
        for directory in [
            self.base_dir,
            self.results_dir,
            self.datagen_dir,
            self.databases_dir,
        ]:
            directory.mkdir(parents=True, exist_ok=True)

    def _format_scale_factor(self, scale_factor: float) -> str:
        return format_scale_factor(scale_factor)

    def get_result_filename(
        self,
        benchmark_name: str,
        scale_factor: float,
        platform: str,
        timestamp: str,
        execution_id: str,
        mode: str | None = None,
    ) -> str:
        from benchbox.core.results.filenames import build_result_filename

        return build_result_filename(
            benchmark_id=benchmark_name,
            scale_factor=scale_factor,
            platform=platform,
            timestamp=timestamp,
            execution_id=execution_id,
            mode=mode,
        )

    def get_result_path(
        self,
        benchmark_name: str,
        scale_factor: float,
        platform: str,
        timestamp: str,
        execution_id: str,
        mode: str | None = None,
    ) -> Path:
        filename = self.get_result_filename(benchmark_name, scale_factor, platform, timestamp, execution_id, mode=mode)
        return self.results_dir / filename

    def get_database_filename(
        self,
        benchmark_name: str,
        scale_factor: float,
        platform: str,
        tuning_config: Optional[dict[str, Any]] = None,
        custom_name: Optional[str] = None,
    ) -> str:
        return generate_database_filename(
            benchmark_name=benchmark_name,
            scale_factor=scale_factor,
            platform=platform,
            tuning_config=tuning_config,
            custom_name=custom_name,
        )

    def get_database_path(
        self,
        benchmark_name: str,
        scale_factor: float,
        platform: str,
        tuning_config: Optional[dict[str, Any]] = None,
        custom_name: Optional[str] = None,
    ) -> Path:
        filename = self.get_database_filename(benchmark_name, scale_factor, platform, tuning_config, custom_name)
        return self.databases_dir / filename

    def get_datagen_path(self, benchmark_name: str, scale_factor: float) -> Path:
        sf_str = self._format_scale_factor(scale_factor)
        return self.datagen_dir / f"{benchmark_name}_{sf_str}"

    def clean_old_files(self, benchmark_name: Optional[str] = None, max_age_days: int = 30):
        import time

        current_time = time.time()
        cutoff_time = current_time - (max_age_days * 24 * 60 * 60)

        cleaned_files = []

        for directory in [self.results_dir, self.datagen_dir, self.databases_dir]:
            if not directory.exists():
                continue

            for file_path in directory.rglob("*"):
                if file_path.is_file():
                    if benchmark_name and not file_path.name.startswith(benchmark_name):
                        continue

                    if file_path.stat().st_mtime < cutoff_time:
                        try:
                            file_path.unlink()
                            cleaned_files.append(str(file_path))
                        except Exception as e:
                            console.print(f"[yellow]Warning: Could not delete {file_path}: {e}[/yellow]")

        return cleaned_files

    def list_files(self, file_type: str = "all") -> dict[str, list[Path]]:
        files = {
            "results": list(self.results_dir.glob("*.json")) if self.results_dir.exists() else [],
            "databases": list(self.databases_dir.glob("*")) if self.databases_dir.exists() else [],
            "datagen": list(self.datagen_dir.glob("*")) if self.datagen_dir.exists() else [],
        }

        if file_type == "all":
            return files
        else:
            return {file_type: files.get(file_type, [])}

    def get_directory_sizes(self) -> dict[str, float]:

        def get_dir_size(path: Path) -> float:
            if not path.exists():
                return 0.0
            total = 0
            for file_path in path.rglob("*"):
                if file_path.is_file():
                    total += file_path.stat().st_size
            return total / (1024 * 1024)

        return {
            "results": get_dir_size(self.results_dir),
            "datagen": get_dir_size(self.datagen_dir),
            "databases": get_dir_size(self.databases_dir),
            "total": get_dir_size(self.base_dir),
        }


class CLIConfigProvider(ConfigInterface):
    def __init__(self, config_manager: "ConfigManager | None" = None):
        self._config_manager = config_manager or ConfigManager()

    def get(self, key: str, default: Any = None) -> Any:
        return self._config_manager.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._config_manager.set(key, value)


def install_cli_config_provider(config_manager: "ConfigManager | None" = None) -> CLIConfigProvider:
    provider = CLIConfigProvider(config_manager)
    set_config_provider(provider)
    return provider
