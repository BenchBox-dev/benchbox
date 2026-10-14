from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from benchbox.core.deployment import DEPLOYMENT_ALIAS_KEYS, parse_platform_selector, resolve_deployment
from benchbox.core.execution_engine import legacy_option_engine, resolve_requested
from benchbox.core.platform_manifest import DEFAULT_EXECUTION_ENGINE, get_all_platform_aliases
from benchbox.core.platform_registry import PlatformRegistry


@dataclass(frozen=True)
class RunSelection:
    platform: str
    execution_engine: str
    effective_execution_engine: str
    deployment: str | None
    selector_deployment: str | None


def resolve_run_selection(
    platform: str,
    execution_engine: str,
    platform_options: Mapping[str, Any] | None = None,
) -> RunSelection:
    options = dict(platform_options or {})
    selector = parse_platform_selector(platform)
    engine = resolve_requested(selector.platform, execution_engine)
    manifest_key = get_all_platform_aliases().get(selector.platform, selector.platform)
    effective_engine = engine
    for key, value in options.items():
        if not isinstance(value, bool):
            continue
        legacy = legacy_option_engine(manifest_key, key, value)
        if legacy is None:
            continue
        if engine != DEFAULT_EXECUTION_ENGINE and legacy != engine:
            raise ValueError(
                "Conflicting execution engine sources: "
                f"explicit setting requests {engine!r} vs legacy option {key} requests {legacy!r}"
            )
        effective_engine = legacy
    deployment = selector.deployment
    if selector.platform in DEPLOYMENT_ALIAS_KEYS:
        deployment = resolve_deployment(selector.platform, selector.deployment, options).selected
    return RunSelection(
        platform=selector.platform,
        execution_engine=engine,
        effective_execution_engine=effective_engine,
        deployment=deployment or PlatformRegistry.get_default_deployment(selector.platform),
        selector_deployment=selector.deployment,
    )


__all__ = ["RunSelection", "resolve_run_selection"]
