# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from typing import Any, Optional

from benchbox.core.platform_registry import PlatformRegistry


def get_inherited_dialect(platform_name: str) -> str:
    current = PlatformRegistry.resolve_platform_name(platform_name)
    visited = set()

    while current:
        if current in visited:
            break
        visited.add(current)

        parent = PlatformRegistry.get_inherited_platform(current)
        if parent:
            current = parent
        else:
            break

    return current


def get_platform_family_dialect(platform_name: str) -> str:
    canonical = PlatformRegistry.resolve_platform_name(platform_name)
    family = PlatformRegistry.get_platform_family(canonical)

    if family:
        return family

    return get_inherited_dialect(canonical)


def build_inherited_config(
    platform_name: str,
    user_config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    canonical = PlatformRegistry.resolve_platform_name(platform_name)
    config: dict[str, Any] = {}

    parent = PlatformRegistry.get_inherited_platform(canonical)
    if parent:
        parent_caps = PlatformRegistry.get_platform_capabilities(parent)
        if parent_caps:
            config["_inherited_from"] = parent
            config["_inherited_dialect"] = get_inherited_dialect(parent)
            config["_platform_family"] = PlatformRegistry.get_platform_family(parent)

    caps = PlatformRegistry.get_platform_capabilities(canonical)
    if caps:
        if caps.platform_family:
            config["_platform_family"] = caps.platform_family

    if user_config:
        config.update(user_config)

    return config


def get_benchmark_compatibility(platform_name: str) -> dict[str, bool]:
    canonical = PlatformRegistry.resolve_platform_name(platform_name)
    compatibility: dict[str, bool] = {}

    parent = PlatformRegistry.get_inherited_platform(canonical)
    if parent:
        parent_compat = get_benchmark_compatibility(parent)
        compatibility.update(parent_compat)

    metadata = PlatformRegistry.get_platform_info(canonical)
    if metadata and hasattr(metadata, "benchmark_compatibility"):
        compatibility.update(getattr(metadata, "benchmark_compatibility", {}))

    return compatibility


def resolve_dialect_for_query_translation(platform_name: str) -> str:
    from benchbox.utils.dialect_utils import resolve_sql_dialect

    return resolve_sql_dialect(PlatformRegistry.resolve_platform_name(platform_name))
