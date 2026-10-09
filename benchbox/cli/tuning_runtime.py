from __future__ import annotations

import logging
from typing import Any, Literal

from benchbox.core.dataframe.tuning.interface import DataFrameTuningConfiguration
from benchbox.core.tuning.capability_registry import resolve_platform_key
from benchbox.core.tuning.interface import UnifiedTuningConfiguration


def build_default_unified_config(platform: str | None) -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    if platform and resolve_platform_key(str(platform).split(":", 1)[0]) == "duckdb":
        config.check_constraints.enabled = False
    return config


def build_baseline_unified_config() -> UnifiedTuningConfiguration:
    baseline_config = UnifiedTuningConfiguration()
    baseline_config.disable_all_constraints()
    baseline_config.platform_optimizations.databricks_clustering_strategy = "none"
    return baseline_config


def infer_runtime_tuning_mode(
    unified_config: UnifiedTuningConfiguration | None,
) -> tuple[bool, Literal["tuned", "notuning"]]:
    if unified_config is None or not unified_config.get_enabled_tuning_types():
        platform_optimizations = getattr(unified_config, "platform_optimizations", None)
        if getattr(platform_optimizations, "sorted_ingestion_mode", "off") not in (None, "off"):
            return True, "tuned"
        return False, "notuning"
    return True, "tuned"


def resolve_dataframe_tuning_config(
    *,
    platform_name: str | None,
    mode_name: str | None,
    tuning_enabled_flag: bool,
    use_auto_tuning_flag: bool,
    tuning_config_file_path: str | None,
    console: Any,
    logger: logging.Logger | None,
    quiet: bool,
) -> DataFrameTuningConfiguration | None:
    if mode_name != "dataframe" or not tuning_enabled_flag or not platform_name:
        return None

    from benchbox.core.dataframe.tuning import (
        get_smart_defaults,
        load_dataframe_tuning,
        validate_dataframe_tuning,
    )

    if use_auto_tuning_flag:
        df_config = get_smart_defaults(platform_name)
        if not quiet:
            console.print("[green]✅ Using auto-detected DataFrame tuning configuration[/green]")
        if logger:
            logger.debug(f"Using smart defaults for DataFrame tuning: {df_config.get_summary()}")
        return df_config

    if tuning_config_file_path:
        try:
            df_config = load_dataframe_tuning(tuning_config_file_path)
        except Exception as e:  # pragma: no cover
            if logger:
                logger.error(f"Failed to load DataFrame tuning configuration: {e}", exc_info=True)
            raise ValueError(f"Failed to load DataFrame tuning configuration: {e}") from e

        if not quiet:
            console.print(f"[green]✅ Loaded DataFrame tuning configuration from {tuning_config_file_path}[/green]")
        if logger:
            logger.debug(f"Loaded DataFrame tuning config from: {tuning_config_file_path}")

        issues = validate_dataframe_tuning(df_config, platform_name)
        for issue in issues:
            if issue.level.value == "error":
                raise ValueError(f"DataFrame tuning error: {issue}")
            if issue.level.value == "warning":
                console.print(f"[yellow]⚠️ DataFrame tuning warning: {issue}[/yellow]")
        return df_config

    df_config = get_smart_defaults(platform_name)
    if not quiet:
        console.print("[green]✅ Using optimized DataFrame tuning configuration[/green]")
    if logger:
        logger.debug(f"Using smart defaults for 'tuned' mode: {df_config.get_summary()}")
    return df_config
