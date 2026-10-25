from __future__ import annotations

from collections.abc import Mapping
from typing import Any

DEFAULT_CLICKHOUSE_DEPLOYMENT_MODE = "local"
CLICKHOUSE_DEPLOYMENT_MODE_VALUES = frozenset({"local", "server"})
CLICKHOUSE_DEPLOYMENT_MODE_ALIASES = {
    "embedded": "local",
}


CLICKHOUSE_CANONICAL_PLATFORM_NAMES: frozenset[str] = frozenset(
    {"clickhouse-local", "clickhouse-server", "clickhouse-cloud"}
)

CLICKHOUSE_LEGACY_SELECTOR_MAP: dict[str, str] = {
    "clickhouse:local": "clickhouse-local",
    "clickhouse:server": "clickhouse-server",
    "clickhouse:cloud": "clickhouse-cloud",
}

CLICKHOUSE_DEFAULT_CANONICAL_PLATFORM: str = "clickhouse-local"

_CLICKHOUSE_ALL_PLATFORM_NAMES: frozenset[str] = frozenset(
    {
        "clickhouse",
        "clickhouse-local",
        "clickhouse-server",
        "clickhouse-cloud",
        "chdb",
    }
)


def is_clickhouse_platform(platform: str) -> bool:
    return platform.lower() in _CLICKHOUSE_ALL_PLATFORM_NAMES


def clickhouse_legacy_selector_warning(selector: str, target: str) -> str:
    return (
        f"ClickHouse selector '{selector}' is deprecated. "
        f"Use '--platform {target}' instead. "
        "See https://benchbox.dev/docs/platforms/clickhouse-migration.html for the full migration guide."
    )


def clickhouse_cloud_mode_error_message() -> str:
    return (
        "ClickHouse Cloud is now a separate first-class platform.\n"
        "Use --platform clickhouse-cloud instead of --platform clickhouse:cloud\n"
        "For more information: benchbox run --platform clickhouse-cloud --help"
    )


def normalize_clickhouse_deployment_mode(
    value: Any,
    *,
    default: str = DEFAULT_CLICKHOUSE_DEPLOYMENT_MODE,
    allow_cloud: bool = False,
) -> str:
    if value is None:
        return default

    if isinstance(value, bool):
        return "local" if value else "server"

    normalized = str(value).strip().lower()
    if not normalized:
        return default

    if normalized == "cloud":
        if allow_cloud:
            return "cloud"
        raise ValueError(clickhouse_cloud_mode_error_message())

    normalized = CLICKHOUSE_DEPLOYMENT_MODE_ALIASES.get(normalized, normalized)
    if normalized in CLICKHOUSE_DEPLOYMENT_MODE_VALUES:
        return normalized

    valid_modes = sorted(CLICKHOUSE_DEPLOYMENT_MODE_VALUES)
    raise ValueError(f"Invalid ClickHouse deployment mode '{normalized}'. Valid modes: {', '.join(valid_modes)}")


def resolve_clickhouse_deployment_mode(
    config: Mapping[str, Any],
    *,
    default: str = DEFAULT_CLICKHOUSE_DEPLOYMENT_MODE,
    allow_cloud: bool = False,
) -> str:
    if (value := config.get("deployment_mode")) not in (None, ""):
        return normalize_clickhouse_deployment_mode(value, default=default, allow_cloud=allow_cloud)

    if (value := config.get("mode")) not in (None, ""):
        return normalize_clickhouse_deployment_mode(value, default=default, allow_cloud=allow_cloud)

    if "embedded" in config and config.get("embedded") is not None:
        return normalize_clickhouse_deployment_mode(config["embedded"], default=default, allow_cloud=allow_cloud)

    return default


__all__ = [
    "CLICKHOUSE_CANONICAL_PLATFORM_NAMES",
    "CLICKHOUSE_DEFAULT_CANONICAL_PLATFORM",
    "CLICKHOUSE_DEPLOYMENT_MODE_ALIASES",
    "CLICKHOUSE_DEPLOYMENT_MODE_VALUES",
    "CLICKHOUSE_LEGACY_SELECTOR_MAP",
    "DEFAULT_CLICKHOUSE_DEPLOYMENT_MODE",
    "clickhouse_cloud_mode_error_message",
    "clickhouse_legacy_selector_warning",
    "is_clickhouse_platform",
    "normalize_clickhouse_deployment_mode",
    "resolve_clickhouse_deployment_mode",
]
