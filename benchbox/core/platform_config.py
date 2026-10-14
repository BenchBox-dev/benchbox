# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
from typing import Any

from benchbox.core.hooks.platform_hooks import PlatformHookRegistry
from benchbox.core.schemas import DatabaseConfig, SystemProfile

logger = logging.getLogger(__name__)

REQUIRED_FROM_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "tuning_config",
        "tuning_enabled",
        "unified_tuning_configuration",
        "tuning_source",
        "tuning_source_file",
    }
)


OPTION_CONFIG_KEYS: dict[str, dict[str, str]] = {"duckdb": {"threads": "thread_limit"}}

_DATABASE_CONFIG_FIELDS: frozenset[str] = frozenset(DatabaseConfig.model_fields)


def lift_explicit_options(platform: str, options: dict[str, Any], platform_config: dict[str, Any]) -> None:
    marker = options.get("_explicit_platform_options")
    if not isinstance(marker, dict):
        return
    renamed = OPTION_CONFIG_KEYS.get(platform.lower(), {})
    registered = PlatformHookRegistry.list_option_specs(platform)
    for name, value in marker.items():
        key = renamed.get(name, name)
        if name not in registered or value is None or key in _DATABASE_CONFIG_FIELDS:
            continue
        if platform_config.get(key) is None:
            platform_config[key] = value


def get_platform_config(
    database_config: DatabaseConfig,
    system_profile: SystemProfile | None,
    benchmark_name: str | None = None,
    scale_factor: float | None = None,
    tuning_config: Any | None = None,
) -> dict[str, Any]:
    platform_config = {}

    db_config_dict = database_config.model_dump(exclude_none=False)

    for field in ["type", "name", "connection_string"]:
        db_config_dict.pop(field, None)

    platform_config.update(db_config_dict)
    options = database_config.options if isinstance(database_config.options, dict) else {}
    lift_explicit_options(database_config.type, options, platform_config)

    if benchmark_name:
        platform_config["benchmark"] = benchmark_name
    if scale_factor is not None:
        platform_config["scale_factor"] = scale_factor
    if tuning_config is not None:
        platform_config["tuning_config"] = tuning_config
        logger.debug(
            "Tuning configuration passed to platform adapter: %s",
            type(tuning_config).__name__,
        )

    if system_profile:
        total_memory_gb = getattr(system_profile, "memory_total_gb", 4)
        try:
            total_memory_gb_val = float(total_memory_gb)
        except Exception:
            total_memory_gb_val = 4.0

        if "memory_limit" in platform_config:
            configured_memory_limit = platform_config["memory_limit"]
        else:
            configured_memory_limit = options.get("memory_limit")
        if configured_memory_limit is not None:
            platform_config["memory_limit"] = configured_memory_limit
        else:
            max_memory_limit = options.get("max_memory_limit_gb", 16)
            platform_config["memory_limit"] = f"{min(int(total_memory_gb_val * 0.8), max_memory_limit)}GB"

        cpu_cores = getattr(system_profile, "cpu_cores_logical", 2)
        try:
            cpu_cores_val = int(cpu_cores)
        except Exception:
            cpu_cores_val = 2
        if platform_config.get("thread_limit") is None:
            platform_config["thread_limit"] = min(cpu_cores_val, 8)

    return platform_config
