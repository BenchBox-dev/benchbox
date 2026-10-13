# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from benchbox.core.dataframe.tuning.interface import (
    DataFrameTuningConfiguration,
    TuningMetadata,
)

logger = logging.getLogger(__name__)


class DataFrameTuningLoadError(Exception):
    pass


class DataFrameTuningSaveError(Exception):
    pass


class DataFrameTuningLoader:
    def load_config(
        self,
        path: Path | str,
        platform: str | None = None,
    ) -> DataFrameTuningConfiguration:
        path = Path(path)

        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {path}")

        try:
            data = self._load_file(path)
        except Exception as e:
            raise DataFrameTuningLoadError(f"Failed to parse {path}: {e}") from e

        try:
            config = DataFrameTuningConfiguration.from_dict(data)
        except Exception as e:
            raise DataFrameTuningLoadError(f"Invalid configuration in {path}: {e}") from e

        if platform:
            enabled = config.get_enabled_settings()

            for setting in enabled:
                if not setting.is_compatible_with_platform(platform):
                    logger.warning(f"Setting '{setting.value}' is not supported on platform '{platform}'")

        return config

    def _load_file(self, path: Path) -> dict[str, Any]:
        suffix = path.suffix.lower()

        with open(path, encoding="utf-8") as f:
            if suffix in {".yaml", ".yml"}:
                data = yaml.safe_load(f)
            elif suffix == ".json":
                data = json.load(f)
            else:
                raise ValueError(f"Unsupported file format: {suffix}. Use .yaml, .yml, or .json")

        return data or {}

    def save_config(
        self,
        config: DataFrameTuningConfiguration,
        path: Path | str,
        platform: str | None = None,
        description: str | None = None,
        include_defaults: bool = False,
    ) -> None:
        path = Path(path)

        if config.metadata is None:
            config.metadata = TuningMetadata()

        config.metadata.platform = platform or config.metadata.platform
        config.metadata.description = description or config.metadata.description
        config.metadata.created = datetime.now().strftime("%Y-%m-%d")
        config.metadata.generated_by = "benchbox"

        data = config.to_full_dict() if include_defaults else config.to_dict()

        try:
            self._save_file(data, path)
        except Exception as e:
            raise DataFrameTuningSaveError(f"Failed to save to {path}: {e}") from e

    def _save_file(self, data: dict[str, Any], path: Path) -> None:
        suffix = path.suffix.lower()

        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            if suffix in {".yaml", ".yml"}:
                yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
            elif suffix == ".json":
                json.dump(data, f, indent=2)
            else:
                raise ValueError(f"Unsupported file format: {suffix}. Use .yaml, .yml, or .json")

    def get_template(self, platform: str) -> DataFrameTuningConfiguration:
        from benchbox.core.dataframe.tuning.interface import (
            DataTypeConfiguration,
            ExecutionConfiguration,
            GPUConfiguration,
            IOConfiguration,
            MemoryConfiguration,
            ParallelismConfiguration,
        )

        platform_lower = platform.lower()
        if platform_lower.endswith("-df"):
            platform_lower = platform_lower[:-3]

        config = DataFrameTuningConfiguration(
            metadata=TuningMetadata(
                platform=platform_lower,
                description=f"Default template for {platform_lower}",
                generated_by="benchbox-template",
            )
        )

        if platform_lower == "polars":
            config.execution = ExecutionConfiguration(lazy_evaluation=True)
            config.memory = MemoryConfiguration(
                rechunk_after_filter=True,
            )
            config.data_types = DataTypeConfiguration(
                enable_string_cache=False,
            )

        elif platform_lower == "pandas":
            config.data_types = DataTypeConfiguration(
                dtype_backend="numpy_nullable",
                enable_string_cache=False,
            )
            config.io = IOConfiguration(
                memory_map=False,
                pre_buffer=True,
            )

        elif platform_lower == "dask":
            config.parallelism = ParallelismConfiguration(
                threads_per_worker=2,
            )
            config.memory = MemoryConfiguration(
                spill_to_disk=True,
            )
            config.data_types = DataTypeConfiguration(
                dtype_backend="pyarrow",
            )

        elif platform_lower == "cudf":
            config.gpu = GPUConfiguration(
                enabled=True,
                device_id=0,
                spill_to_host=True,
            )

        return config

    def get_optimized_template(self, platform: str) -> DataFrameTuningConfiguration:
        from benchbox.core.dataframe.tuning.interface import (
            DataTypeConfiguration,
            ExecutionConfiguration,
            GPUConfiguration,
            ParallelismConfiguration,
        )

        platform_lower = platform.lower()
        if platform_lower.endswith("-df"):
            platform_lower = platform_lower[:-3]

        config = self.get_template(platform)
        config.metadata.description = f"Performance-optimized template for {platform_lower}"

        if platform_lower == "polars":
            config.execution = ExecutionConfiguration(lazy_evaluation=True)
            config.data_types = DataTypeConfiguration(
                enable_string_cache=True,
            )

        elif platform_lower == "pandas":
            config.data_types = DataTypeConfiguration(
                dtype_backend="pyarrow",
                enable_string_cache=True,
                auto_categorize_strings=True,
                categorical_threshold=0.3,
            )
            config.io.memory_map = True

        elif platform_lower == "dask":
            config.parallelism = ParallelismConfiguration(
                threads_per_worker=4,
            )
            config.data_types = DataTypeConfiguration(
                dtype_backend="pyarrow",
            )

        elif platform_lower == "cudf":
            config.gpu = GPUConfiguration(
                enabled=True,
                device_id=0,
                spill_to_host=False,
                pool_type="pool",
            )

        return config

    def get_memory_constrained_template(self, platform: str) -> DataFrameTuningConfiguration:
        from benchbox.core.dataframe.tuning.interface import (
            DataTypeConfiguration,
            ExecutionConfiguration,
            GPUConfiguration,
            MemoryConfiguration,
            ParallelismConfiguration,
        )

        platform_lower = platform.lower()
        if platform_lower.endswith("-df"):
            platform_lower = platform_lower[:-3]

        config = self.get_template(platform)
        config.metadata.description = f"Memory-constrained template for {platform_lower}"

        if platform_lower == "polars":
            config.execution = ExecutionConfiguration(lazy_evaluation=True)
            config.memory = MemoryConfiguration(
                chunk_size=100_000,
                rechunk_after_filter=False,
            )

        elif platform_lower == "pandas":
            config.memory = MemoryConfiguration(
                chunk_size=50_000,
            )
            config.data_types = DataTypeConfiguration(
                dtype_backend="pyarrow",
                auto_categorize_strings=True,
                categorical_threshold=0.5,
            )

        elif platform_lower == "dask":
            config.parallelism = ParallelismConfiguration(
                worker_count=2,
                threads_per_worker=1,
            )
            config.memory = MemoryConfiguration(
                memory_limit="2GB",
                spill_to_disk=True,
                chunk_size=100_000,
            )

        elif platform_lower == "cudf":
            config.gpu = GPUConfiguration(
                enabled=True,
                device_id=0,
                spill_to_host=True,
            )

        return config

    def merge_configs(
        self,
        base: DataFrameTuningConfiguration,
        override: DataFrameTuningConfiguration,
        platform: str | None = None,
        validate: bool = True,
    ) -> DataFrameTuningConfiguration:
        base_dict = base.to_full_dict()

        override_dict = override.to_dict()

        merged = self._deep_merge(base_dict, override_dict)

        merged_config = DataFrameTuningConfiguration.from_dict(merged)

        if validate and platform:
            from benchbox.core.dataframe.tuning.validation import (
                ValidationLevel,
                validate_dataframe_tuning,
            )

            issues = validate_dataframe_tuning(merged_config, platform)
            errors = [i for i in issues if i.level == ValidationLevel.ERROR]
            if errors:
                error_msgs = "; ".join(i.message for i in errors)
                raise ValueError(f"Merged configuration validation failed: {error_msgs}")

            for issue in issues:
                if issue.level == ValidationLevel.WARNING:
                    logger.warning(f"Merged config: {issue}")

        return merged_config

    def _deep_merge(self, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        result = base.copy()

        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value

        return result


_default_loader = DataFrameTuningLoader()


def load_dataframe_tuning(path: Path | str, platform: str | None = None) -> DataFrameTuningConfiguration:
    return _default_loader.load_config(path, platform)


def save_dataframe_tuning(
    config: DataFrameTuningConfiguration,
    path: Path | str,
    platform: str | None = None,
    description: str | None = None,
) -> None:
    _default_loader.save_config(config, path, platform, description)
