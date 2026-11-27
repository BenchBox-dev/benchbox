from __future__ import annotations

from collections.abc import Iterable, Mapping
from functools import lru_cache
from types import MappingProxyType

REMOVED_IN = "0.6.0"

DEPLOYMENT_OPTION_KEYS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "clickhouse": ("deployment_mode", "mode", "embedded"),
        "databend": ("deployment_mode",),
        "ducklake": ("deployment_mode",),
        "firebolt": ("deployment_mode", "firebolt_mode"),
        "influxdb": ("deployment_mode", "mode"),
        "lakesail": ("deployment_mode", "sail_mode"),
        "pg-duckdb": ("deployment_mode",),
        "timescaledb": ("deployment_mode",),
        "velox": ("deployment", "deployment_mode"),
    }
)

REMOVED_DEPLOYMENT_VALUES: Mapping[tuple[str, str], str] = MappingProxyType({("clickhouse", "embedded"): "local"})

_ENGINE_REPLACEMENTS: Mapping[tuple[str, str], str] = MappingProxyType(
    {
        ("athena-spark", "engine_version"): "'runtime_version'",
        ("dask", "use_distributed"): "'--execution-engine threaded' or '--execution-engine distributed'",
        ("fabric_dw", "warehouse"): "'database'",
        ("pg-duckdb", "force_execution"): "'--execution-engine postgres' or '--execution-engine duckdb'",
        ("polars", "streaming"): "'--execution-engine streaming' (execution_engine='streaming')",
    }
)


def manifest_key(platform: str) -> str:
    from benchbox.core.platform_manifest import PLATFORM_MANIFEST_BY_KEY, get_all_platform_aliases

    name = platform.lower().strip().split(":", 1)[0]
    name = get_all_platform_aliases().get(name, name)
    if name not in PLATFORM_MANIFEST_BY_KEY and name.replace("_", "-") in PLATFORM_MANIFEST_BY_KEY:
        return name.replace("_", "-")
    return name


@lru_cache(maxsize=1)
def _adapter_platforms() -> Mapping[tuple[str, str], str]:
    from benchbox.core.platform_manifest import PLATFORM_MANIFEST

    return MappingProxyType(
        {
            (entry.adapter.module, entry.adapter.class_name): entry.key
            for entry in PLATFORM_MANIFEST
            if entry.adapter is not None
        }
    )


def adapter_manifest_key(adapter_class: type) -> str | None:
    platforms = _adapter_platforms()
    for cls in adapter_class.__mro__:
        for (module, class_name), key in platforms.items():
            if cls.__name__ == class_name and (cls.__module__ == module or cls.__module__.startswith(module + ".")):
                return key
    return None


def _selector_replacement(platform: str) -> str:
    from benchbox.core.platform_registry import PlatformRegistry

    modes = PlatformRegistry.get_available_deployment_modes(platform) or []
    suffix = "|".join(modes) if modes else "<deployment>"
    return f"the platform selector '--platform {platform}:{suffix}' (Python: get_adapter('{platform}:<deployment>'))"


def _compute_replacement(platform: str, key: str) -> str | None:
    from benchbox.core.compute_resource import (
        COMPUTE_RESOURCE_OPTION,
        COMPUTE_SIZE_OPTION,
        get_compute_declaration,
    )

    declaration = get_compute_declaration(platform)
    if declaration is None:
        return None
    if key in declaration.native_resource_keys and key != COMPUTE_RESOURCE_OPTION:
        return f"'{COMPUTE_RESOURCE_OPTION}'"
    if key in declaration.native_size_keys and key != COMPUTE_SIZE_OPTION:
        return f"'{COMPUTE_SIZE_OPTION}'"
    return None


def removed_option_replacement(platform: str, key: str) -> str | None:
    platform_key = manifest_key(platform)
    name = key.lower()
    if name in DEPLOYMENT_OPTION_KEYS.get(platform_key, ()):
        return _selector_replacement(platform_key)
    engine = _ENGINE_REPLACEMENTS.get((platform_key, name))
    if engine is not None:
        return engine
    return _compute_replacement(platform_key, name)


def removed_option_pairs() -> tuple[tuple[str, str], ...]:
    from benchbox.core.compute_resource import COMPUTE_RESOURCE_OPTION, COMPUTE_SIZE_OPTION, get_compute_declaration
    from benchbox.core.platform_manifest import PLATFORM_MANIFEST

    pairs: set[tuple[str, str]] = set(_ENGINE_REPLACEMENTS)
    pairs.update((platform, key) for platform, keys in DEPLOYMENT_OPTION_KEYS.items() for key in keys)
    for entry in PLATFORM_MANIFEST:
        declaration = get_compute_declaration(entry.key)
        if declaration is None:
            continue
        pairs.update((entry.key, key) for key in declaration.native_resource_keys if key != COMPUTE_RESOURCE_OPTION)
        pairs.update((entry.key, key) for key in declaration.native_size_keys if key != COMPUTE_SIZE_OPTION)
    return tuple(sorted(pairs))


def removed_message(subject: str, platform: str, replacement: str) -> str:
    kind = "Option" if subject.startswith("-") else "Platform option"
    return f"{kind} '{subject}' for '{platform}' was removed in BenchBox {REMOVED_IN}; use {replacement} instead."


def removed_option_message(platform: str, key: str) -> str | None:
    replacement = removed_option_replacement(platform, key)
    if replacement is None:
        return None
    return removed_message(key, manifest_key(platform), replacement)


def removed_polars_streaming_flag_message() -> str:
    return removed_message("--polars-streaming", "polars", _ENGINE_REPLACEMENTS[("polars", "streaming")])


def first_removed_option_message(platform: str, keys: Iterable[object]) -> str | None:
    for key in keys:
        message = removed_option_message(platform, str(key))
        if message is not None:
            return message
    return None


def removed_deployment_value_message(platform: str, value: object) -> str | None:
    platform_key = manifest_key(platform)
    replacement = REMOVED_DEPLOYMENT_VALUES.get((platform_key, str(value).strip().lower()))
    if replacement is None:
        return None
    return (
        f"Deployment mode '{value}' for '{platform_key}' was removed in BenchBox {REMOVED_IN}; "
        f"use '{replacement}' instead."
    )


def reject_removed_keywords(platform: str, config: Mapping[str, object]) -> None:
    message = first_removed_option_message(platform, config)
    if message is not None:
        raise TypeError(message)


def reject_removed_adapter_keywords(adapter_class: type, config: Mapping[str, object]) -> None:
    platform = adapter_manifest_key(adapter_class)
    if platform is not None:
        reject_removed_keywords(platform, config)


__all__ = [
    "DEPLOYMENT_OPTION_KEYS",
    "REMOVED_DEPLOYMENT_VALUES",
    "REMOVED_IN",
    "adapter_manifest_key",
    "first_removed_option_message",
    "manifest_key",
    "reject_removed_adapter_keywords",
    "reject_removed_keywords",
    "removed_deployment_value_message",
    "removed_message",
    "removed_option_message",
    "removed_option_pairs",
    "removed_option_replacement",
    "removed_polars_streaming_flag_message",
]
