from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from benchbox.core.deployment import DEPLOYMENT_SELECTOR_PLATFORMS, parse_platform_selector, resolve_deployment
from benchbox.core.execution_engine import resolve_requested
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.core.removed_options import first_removed_option_message


@dataclass(frozen=True)
class RunSelection:
    platform: str
    execution_engine: str
    deployment: str | None
    selector_deployment: str | None


def resolve_run_selection(
    platform: str,
    execution_engine: str,
    platform_options: Mapping[str, Any] | None = None,
) -> RunSelection:
    selector = parse_platform_selector(platform)
    removed = first_removed_option_message(selector.platform, platform_options or {})
    if removed is not None:
        raise ValueError(removed)
    engine = resolve_requested(selector.platform, execution_engine)
    deployment = selector.deployment
    if selector.platform in DEPLOYMENT_SELECTOR_PLATFORMS:
        deployment = resolve_deployment(selector.platform, selector.deployment).selected
    return RunSelection(
        platform=selector.platform,
        execution_engine=engine,
        deployment=deployment or PlatformRegistry.get_default_deployment(selector.platform),
        selector_deployment=selector.deployment,
    )


__all__ = ["RunSelection", "resolve_run_selection"]
