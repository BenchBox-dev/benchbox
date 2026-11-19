from __future__ import annotations

from collections.abc import Callable
from typing import Any

import click

from benchbox.core.results.provenance import PLAN_TIERS

PLAN_TIER_HELP = (
    "Plan tier of the vendor service the run used (paid, free, trial, unspecified). Recorded in the "
    "bundle's provenance block; free and trial results cannot be submitted without vendor permission."
)
PREVIEW_FEATURE_HELP = (
    "Beta or Preview vendor feature the run used (repeatable). Recorded in the bundle's provenance "
    "block; results that use them cannot be submitted without vendor permission."
)


def vendor_provenance_options(command: Callable[..., Any]) -> Callable[..., Any]:
    command = click.option("--preview-feature", "preview_features", multiple=True, help=PREVIEW_FEATURE_HELP)(command)
    return click.option(
        "--plan-tier", type=click.Choice(PLAN_TIERS, case_sensitive=False), default=None, help=PLAN_TIER_HELP
    )(command)


def vendor_provenance_kwargs(settings: Any) -> dict[str, Any]:
    return {
        "plan_tier": getattr(settings, "plan_tier", None),
        "preview_features": getattr(settings, "preview_features", None),
    }


def record_vendor_provenance(result: Any, plan_tier: str | None, preview_features: tuple[str, ...] | None) -> None:
    if plan_tier:
        result.plan_tier = plan_tier.lower()
    if preview_features:
        result.preview_features = list(preview_features)
