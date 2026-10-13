from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from benchbox.core.deprecation_notices import warn_deprecated_alias
from benchbox.core.platform_registry import PlatformRegistry

DEPLOYMENT_ALIAS_KEYS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "velox": ("deployment",),
        "lakesail": ("sail_mode",),
        "influxdb": ("mode",),
        "clickhouse": ("deployment_mode", "mode"),
        "firebolt": ("deployment_mode", "firebolt_mode"),
        "ducklake": ("deployment_mode",),
        "pg-duckdb": ("deployment_mode",),
    }
)

DEPLOYMENT_VALUE_SYNONYMS: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "clickhouse": MappingProxyType({"embedded": "local"}),
    }
)


@dataclass(frozen=True)
class DeploymentResolution:
    selected: str | None
    explicit: bool


def _deployment_selector(platform: str, selected: str) -> str:
    from benchbox.core.platform_manifest import PLATFORM_MANIFEST_BY_KEY

    named = f"{platform}-{selected}".lower()
    return named if named in PLATFORM_MANIFEST_BY_KEY else f"{platform}:{selected}".lower()


def note_deprecated_alias(platform: str, key: str, selected: str) -> None:
    warn_deprecated_alias(
        platform, key, f"'--platform {_deployment_selector(platform, selected)}'", setting="deployment"
    )


def normalize_deployment_value(platform: str, value: Any) -> str:
    platform_key = platform.lower()
    normalized = str(value).strip().lower()
    synonyms = DEPLOYMENT_VALUE_SYNONYMS.get(platform_key, {})
    normalized = synonyms.get(normalized, normalized)
    available = PlatformRegistry.get_available_deployment_modes(platform_key)
    if available and normalized not in available:
        modes = ", ".join(available)
        raise ValueError(f"Platform '{platform}' does not support deployment mode '{value}'. Available: {modes}")
    return normalized


def deployment_class(platform: str, selected: str) -> str | None:
    capability = PlatformRegistry.get_deployment_capability(platform.lower(), selected)
    return capability.mode if capability is not None else None


def _explicit_alias_keys(platform: str, values: Mapping[str, Any], explicit: set[str] | None) -> list[str]:
    keys = DEPLOYMENT_ALIAS_KEYS.get(platform.lower(), ())
    if explicit is not None:
        lowered = {str(key).lower() for key in explicit}
        return [key for key in keys if key in lowered and values.get(key) is not None]
    return [key for key in keys if values.get(key) is not None]


def resolve_deployment(
    platform: str,
    selector: str | None,
    values: Mapping[str, Any],
    explicit: set[str] | None = None,
) -> DeploymentResolution:
    platform_key = platform.lower()
    selector_value = normalize_deployment_value(platform_key, selector) if selector is not None else None
    candidates: list[tuple[str, str]] = []
    for key in _explicit_alias_keys(platform_key, values, explicit):
        candidates.append((key, normalize_deployment_value(platform_key, values[key])))
    if selector_value is not None:
        for key, value in candidates:
            if value != selector_value:
                raise ValueError(
                    f"Deployment selector '{platform_key}:{selector_value}' conflicts with "
                    f"platform option '{key}={values[key]}'. "
                    f"Use the selector or the option, not both."
                )
    for first in range(len(candidates)):
        for second in range(first + 1, len(candidates)):
            if candidates[first][1] != candidates[second][1]:
                raise ValueError(
                    f"Platform options '{candidates[first][0]}={values[candidates[first][0]]}' and "
                    f"'{candidates[second][0]}={values[candidates[second][0]]}' select different "
                    f"deployments for '{platform_key}'. Use one deployment option."
                )
    for key, _value in candidates:
        note_deprecated_alias(platform_key, key, selector_value or candidates[0][1])
    if selector_value is not None:
        return DeploymentResolution(selector_value, True)
    if candidates:
        return DeploymentResolution(candidates[0][1], True)
    return DeploymentResolution(None, False)


def select_adapter_deployment(
    platform: str,
    config: Mapping[str, Any],
    default: str | None,
) -> str | None:
    platform_key = platform.lower()
    marker = config.get("_explicit_platform_options")
    explicit_keys = {str(key).lower() for key in marker} if isinstance(marker, Mapping) else None
    canonical = config.get("deployment_mode")
    legacy_keys = [key for key in DEPLOYMENT_ALIAS_KEYS.get(platform_key, ()) if key != "deployment_mode"]
    legacies: list[tuple[str, Any]] = []
    for key in legacy_keys:
        value = config.get(key)
        if value is None:
            continue
        if explicit_keys is not None and key not in explicit_keys:
            continue
        legacies.append((key, value))
    normalized_canonical = normalize_deployment_value(platform_key, canonical) if canonical is not None else None
    normalized_legacies = [(key, normalize_deployment_value(platform_key, value)) for key, value in legacies]
    if normalized_canonical is not None:
        for key, value in normalized_legacies:
            if value != normalized_canonical:
                raise ValueError(
                    f"Platform option 'deployment_mode={canonical}' conflicts with "
                    f"'{key}={dict(legacies)[key]}' for '{platform_key}'. "
                    "Use one deployment selection."
                )
        return normalized_canonical
    for key, value in normalized_legacies:
        note_deprecated_alias(platform_key, key, value)
    if normalized_legacies:
        first_value = normalized_legacies[0][1]
        for key, value in normalized_legacies[1:]:
            if value != first_value:
                raise ValueError(
                    f"Platform options select different deployments for '{platform_key}'. Use one deployment selection."
                )
        return first_value
    return default
