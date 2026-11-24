from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from benchbox.core.platform_registry import PlatformRegistry
from benchbox.core.removed_options import DEPLOYMENT_OPTION_KEYS, removed_deployment_value_message

DEPLOYMENT_SELECTOR_PLATFORMS: frozenset[str] = frozenset(DEPLOYMENT_OPTION_KEYS)
DEPLOYMENT_SELECTOR_OPTION = "_deployment_selector"
LEGACY_CLICKHOUSE_SELECTOR_BASE = "clickhouse"


@dataclass(frozen=True)
class DeploymentResolution:
    selected: str | None
    explicit: bool


@dataclass(frozen=True)
class PlatformSelector:
    platform: str
    deployment: str | None


def parse_platform_selector(selector: str) -> PlatformSelector:
    from benchbox.core.platform_manifest import PLATFORM_MANIFEST_BY_KEY, get_all_platform_aliases

    key = selector.strip().lower()
    base, separator, mode = key.partition(":")
    if not separator:
        return PlatformSelector(key, None)
    base = get_all_platform_aliases().get(base, base)
    available = PlatformRegistry.get_available_deployment_modes(base)
    if not available:
        raise ValueError(f"Platform '{base}' does not support deployment modes. Remove the ':{mode}' suffix.")
    removed = removed_deployment_value_message(base, mode)
    if removed is not None:
        raise ValueError(removed)
    if mode not in available:
        raise ValueError(
            f"Platform '{base}' does not support deployment mode '{mode}'. Available: {', '.join(available)}"
        )
    named = f"{base}-{mode}"
    if named in PLATFORM_MANIFEST_BY_KEY:
        return PlatformSelector(named, None)
    if base not in DEPLOYMENT_SELECTOR_PLATFORMS and mode != PlatformRegistry.get_default_deployment(base):
        raise ValueError(f"Platform '{base}' cannot select deployment mode '{mode}' by name.")
    return PlatformSelector(base, mode)


def normalize_deployment_value(platform: str, value: Any) -> str:
    platform_key = platform.lower()
    normalized = str(value).strip().lower()
    removed = removed_deployment_value_message(platform_key, normalized)
    if removed is not None:
        raise ValueError(removed)
    available = PlatformRegistry.get_available_deployment_modes(platform_key)
    if available and normalized not in available:
        modes = ", ".join(available)
        raise ValueError(f"Platform '{platform}' does not support deployment mode '{value}'. Available: {modes}")
    return normalized


def deployment_class(platform: str, selected: str) -> str | None:
    capability = PlatformRegistry.get_deployment_capability(platform.lower(), selected)
    return capability.mode if capability is not None else None


def resolve_deployment(platform: str, selector: str | None) -> DeploymentResolution:
    if selector is None:
        return DeploymentResolution(None, False)
    return DeploymentResolution(normalize_deployment_value(platform, selector), True)


def select_adapter_deployment(
    platform: str,
    config: Mapping[str, Any],
    default: str | None,
) -> str | None:
    selector = config.get(DEPLOYMENT_SELECTOR_OPTION)
    if selector is None:
        return default
    return normalize_deployment_value(platform, selector)


@dataclass(frozen=True)
class ConfigPlatformSelection:
    platform: str
    deployment: str | None
    implied_mode: str | None


def resolve_config_platform(platform_type: str) -> ConfigPlatformSelection:
    from benchbox.core.platform_manifest import get_platform_alias_modes

    key = platform_type.strip().lower()
    base, separator, _mode = key.partition(":")
    if not separator or base == LEGACY_CLICKHOUSE_SELECTOR_BASE:
        return ConfigPlatformSelection(platform_type, None, None)
    selector = parse_platform_selector(key)
    deployment = selector.deployment if selector.platform in DEPLOYMENT_SELECTOR_PLATFORMS else None
    return ConfigPlatformSelection(selector.platform, deployment, get_platform_alias_modes("cli").get(base))


def normalize_config_platform(
    platform_type: str,
    options: Mapping[str, Any],
    execution_mode: str | None,
) -> tuple[str, dict[str, Any], str | None]:
    selection = resolve_config_platform(platform_type)
    resolved_options = dict(options)
    if selection.deployment is not None:
        existing = resolved_options.get(DEPLOYMENT_SELECTOR_OPTION)
        if existing is not None and normalize_deployment_value(selection.platform, existing) != selection.deployment:
            raise ValueError(
                f"Deployment selector '{platform_type}' conflicts with explicit deployment '{existing}'. "
                "Use one deployment selection."
            )
        resolved_options[DEPLOYMENT_SELECTOR_OPTION] = selection.deployment
    return selection.platform, resolved_options, execution_mode or selection.implied_mode


def configured_deployment(database_config: Any) -> str | None:
    if not database_config:
        return None
    options = getattr(database_config, "options", None) or {}
    selected = options.get(DEPLOYMENT_SELECTOR_OPTION) or options.get("deployment_mode")
    return str(selected) if selected else None
