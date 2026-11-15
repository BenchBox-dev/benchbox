from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from benchbox.core.platform_registry import PlatformRegistry

ACKNOWLEDGEMENT_ENV_VAR = "BENCHBOX_ACKNOWLEDGE_BILLABLE_PLATFORM"
ACKNOWLEDGEMENT_FLAG = "--acknowledge-billable-platform"

SOURCE_CLI_FLAG = "cli_flag"
SOURCE_ENVIRONMENT = "environment"
SOURCE_MCP_SERVER_ENVIRONMENT = "mcp_server_environment"

BILLABLE_CATEGORY = "cloud"
MANAGED_DEPLOYMENT_CLASS = "managed"

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_DEPLOYMENT_OPTION_KEYS = ("deployment_mode", "deployment", "mode")


@dataclass(frozen=True)
class BillableAssessment:
    platform: str
    billable: bool
    reasons: tuple[str, ...]
    deployment: str | None


@dataclass(frozen=True)
class BillableAcknowledgement:
    source: str

    def record(self) -> dict[str, object]:
        return {"acknowledged": True, "source": self.source}


class BillablePlatformRefused(RuntimeError):
    def __init__(self, assessment: BillableAssessment) -> None:
        self.assessment = assessment
        super().__init__(refusal_message(assessment))


def refusal_message(assessment: BillableAssessment) -> str:
    return (
        f"Refusing to start billable platform '{assessment.platform}' without acknowledgement "
        f"({'; '.join(assessment.reasons)}). Set {ACKNOWLEDGEMENT_ENV_VAR}=1 or pass {ACKNOWLEDGEMENT_FLAG} "
        "to confirm that running it may incur cost."
    )


def _canonical_platform(platform: str) -> tuple[str, str | None]:
    from benchbox.core.platform_manifest import PLATFORM_MANIFEST_BY_KEY, get_all_platform_aliases

    key = platform.strip().lower()
    base, separator, mode = key.partition(":")
    base = get_all_platform_aliases().get(base, base)
    if separator and f"{base}-{mode}" in PLATFORM_MANIFEST_BY_KEY:
        return f"{base}-{mode}", None
    return PlatformRegistry.resolve_platform_name(base), (mode if separator else None)


def _option_deployments(platform: str, options: Mapping[str, object] | None) -> list[str]:
    from benchbox.core.deployment import DEPLOYMENT_ALIAS_KEYS

    if not options:
        return []
    keys = {*DEPLOYMENT_ALIAS_KEYS.get(platform, ()), *_DEPLOYMENT_OPTION_KEYS}
    lowered = {str(key).lower(): value for key, value in options.items()}
    return [str(lowered[key]).strip().lower() for key in sorted(keys) if lowered.get(key) not in (None, "")]


def assess_platform(
    platform: str,
    deployment: str | None = None,
    options: Mapping[str, object] | None = None,
) -> BillableAssessment:
    name, selector_mode = _canonical_platform(platform)
    metadata = PlatformRegistry.get_all_platform_metadata().get(name)
    capabilities = PlatformRegistry.get_platform_capabilities(name)
    reasons: list[str] = []
    if metadata is not None and metadata.get("category") == BILLABLE_CATEGORY:
        reasons.append(f"registry category '{BILLABLE_CATEGORY}'")
    known_modes = capabilities.deployment_modes if capabilities is not None else {}
    requested = [value for value in (deployment, selector_mode) if value]
    requested.extend(_option_deployments(name, options))
    resolved: list[str] = [value for value in dict.fromkeys(requested) if value in known_modes]
    if not resolved and capabilities is not None and capabilities.deployment_modes:
        resolved = [capabilities.default_deployment]
    for selected in resolved:
        mode = capabilities.deployment_modes.get(selected) if capabilities is not None else None
        if mode is not None and mode.mode == MANAGED_DEPLOYMENT_CLASS:
            reasons.append(f"deployment mode '{selected}' is {MANAGED_DEPLOYMENT_CLASS}")
    return BillableAssessment(
        platform=name,
        billable=bool(reasons),
        reasons=tuple(reasons),
        deployment=resolved[0] if resolved else None,
    )


def environment_acknowledged(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return source.get(ACKNOWLEDGEMENT_ENV_VAR, "").strip().lower() in _TRUTHY


def resolve_acknowledgement(
    flag: bool = False,
    env: Mapping[str, str] | None = None,
) -> BillableAcknowledgement | None:
    if flag:
        return BillableAcknowledgement(SOURCE_CLI_FLAG)
    if environment_acknowledged(env):
        return BillableAcknowledgement(SOURCE_ENVIRONMENT)
    return None


def require_acknowledgement(
    assessment: BillableAssessment,
    acknowledgement: BillableAcknowledgement | None,
) -> None:
    if assessment.billable and acknowledgement is None:
        raise BillablePlatformRefused(assessment)


def acknowledgement_record_for(
    assessment: BillableAssessment,
    acknowledgement: BillableAcknowledgement | None,
) -> dict[str, object] | None:
    if assessment.billable and acknowledgement is not None:
        return acknowledgement.record()
    return None


def billable_platform_names() -> tuple[str, ...]:
    names: list[str] = []
    for name, metadata in PlatformRegistry.get_all_platform_metadata().items():
        capabilities = PlatformRegistry.get_platform_capabilities(name)
        managed = capabilities is not None and any(
            mode.mode == MANAGED_DEPLOYMENT_CLASS for mode in capabilities.deployment_modes.values()
        )
        if metadata.get("category") == BILLABLE_CATEGORY or managed:
            names.append(name)
    return tuple(sorted(names))
