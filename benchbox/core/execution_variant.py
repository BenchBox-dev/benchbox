from __future__ import annotations

from collections.abc import Iterable

from benchbox.core.platform_manifest import (
    DEFAULT_EXECUTION_ENGINE,
    PLATFORM_MANIFEST,
    get_all_platform_aliases,
    get_platform_manifest_entry,
)

VARIANT_SEPARATORS = ("~", "+", "@")
NO_GATEWAY = "native"


class VariantIdError(ValueError):
    pass


def _variant_part(label: str, value: str) -> str:
    if not value or any(separator in value for separator in VARIANT_SEPARATORS):
        raise VariantIdError(f"Variant {label} {value!r} must be non-empty and must not contain '~', '+' or '@'")
    return value


def append_variant_suffix(
    base: str,
    platform: str | None,
    deployment: str | None = None,
    requested_engine: str | None = DEFAULT_EXECUTION_ENGINE,
    gateway: str | None = None,
) -> str:
    variant = _variant_part("platform", base)
    entry = get_platform_manifest_entry(get_all_platform_aliases().get(platform, platform)) if platform else None
    default = entry.capabilities.get("default_deployment") if entry else None
    if deployment and deployment != default:
        variant += "~" + _variant_part("deployment", deployment)
    if requested_engine and requested_engine != DEFAULT_EXECUTION_ENGINE:
        variant += "+" + _variant_part("execution engine", requested_engine)
    if gateway and gateway != NO_GATEWAY:
        variant += "@" + _variant_part("gateway", gateway)
    return variant


def derive_variant_id(
    platform: str,
    deployment: str | None = None,
    requested_engine: str | None = DEFAULT_EXECUTION_ENGINE,
    gateway: str | None = None,
) -> str:
    return append_variant_suffix(platform, platform, deployment, requested_engine, gateway)


def resolve_variant_platform_id(candidates: Iterable[str | None], execution_mode: str | None = None) -> str | None:
    tokens = [
        candidate.strip().lower().removesuffix(" (dataframe)").removesuffix(" (sql)").replace(" ", "-")
        for candidate in candidates
        if candidate
    ]
    aliases = get_all_platform_aliases()
    for token in tokens:
        entry = get_platform_manifest_entry(aliases.get(token, token))
        if entry is None:
            entry = next(
                (
                    item
                    for item in PLATFORM_MANIFEST
                    if str(item.metadata.get("display_name", "")).lower().replace(" ", "-") == token
                ),
                None,
            )
        if entry is not None:
            for alias in entry.aliases:
                if alias.implied_mode and (
                    alias.implied_mode == execution_mode or (execution_mode is None and alias.name == token)
                ):
                    return alias.name
            return entry.key
    return tokens[0] if tokens else None
