from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from benchbox.core.deprecation_notices import ALIAS_REMOVED_IN as COMPUTE_ALIAS_REMOVED_IN, warn_deprecated_alias

COMPUTE_RESOURCE_OPTION = "compute_resource"
COMPUTE_SIZE_OPTION = "compute_size"


@dataclass(frozen=True)
class ComputeDeclaration:
    resource_kind: str | Mapping[str, str] | None
    resource_aliases: tuple[str, ...]
    size_aliases: tuple[str, ...]
    size_choices: tuple[str, ...] = ()
    size_default: str | None = None


def _manifest_key(platform: str) -> str:
    from benchbox.core.platform_manifest import get_all_platform_aliases

    name = platform.lower().strip().replace("_", "-")
    return get_all_platform_aliases().get(name, name)


def _raw_declaration(platform: str) -> Mapping[str, Any]:
    from benchbox.core.platform_manifest import PLATFORM_MANIFEST_BY_KEY

    entry = PLATFORM_MANIFEST_BY_KEY.get(_manifest_key(platform))
    if entry is None:
        return {}
    compute = entry.capabilities.get("compute", {})
    return compute if isinstance(compute, Mapping) else {}


def get_compute_declaration(platform: str) -> ComputeDeclaration | None:
    raw = _raw_declaration(platform)
    if not raw:
        return None
    resource_aliases = tuple(raw.get("native_resource_keys", ()))
    size_aliases = tuple(raw.get("native_size_keys", ()))
    if not resource_aliases and not size_aliases:
        return None
    return ComputeDeclaration(
        resource_kind=raw.get("resource_kind"),
        resource_aliases=resource_aliases,
        size_aliases=size_aliases,
        size_choices=tuple(raw.get("size_choices", ())),
        size_default=raw.get("size_default"),
    )


def declares_compute(platform: str) -> bool:
    return get_compute_declaration(platform) is not None


def warn_deprecated_compute_alias(platform: str, alias: str, target: str) -> None:
    warn_deprecated_alias(platform, alias, f"'{target}'")


def normalize_compute_options(platform: str, options: Mapping[str, Any]) -> dict[str, Any]:
    declaration = get_compute_declaration(platform)
    if declaration is None:
        return dict(options)
    resolved = dict(options)
    _normalize_compute_family(platform, resolved, COMPUTE_RESOURCE_OPTION, declaration.resource_aliases)
    _normalize_compute_family(platform, resolved, COMPUTE_SIZE_OPTION, declaration.size_aliases)
    _reject_ambiguous_resource_kind(platform, declaration, resolved)
    return resolved


def _reject_ambiguous_resource_kind(
    platform: str, declaration: ComputeDeclaration, resolved: Mapping[str, Any]
) -> None:
    from benchbox.core.hooks.platform_hooks import PlatformOptionError

    kind = declaration.resource_kind
    if not isinstance(kind, Mapping):
        return
    present = [alias for alias in declaration.resource_aliases if resolved.get(alias) is not None]
    if len(present) > 1:
        names = " and ".join(f"'{alias}={resolved[alias]}'" for alias in present)
        raise PlatformOptionError(
            f"Ambiguous compute resource for platform '{platform}': {names} "
            f"name different vendor objects. Keep only one native key."
        )
    if resolved.get(COMPUTE_RESOURCE_OPTION) is not None and not present:
        names = ", ".join(f"'{alias}'" for alias in declaration.resource_aliases)
        raise PlatformOptionError(
            f"Ambiguous compute resource for platform '{platform}': "
            f"'{COMPUTE_RESOURCE_OPTION}={resolved[COMPUTE_RESOURCE_OPTION]}' "
            f"could be {names}. Use one native key instead."
        )


def _normalize_compute_family(
    platform: str, resolved: dict[str, Any], canonical: str, aliases: tuple[str, ...]
) -> None:
    from benchbox.core.hooks.platform_hooks import PlatformOptionError

    seen: dict[str, Any] = {}
    for alias in aliases:
        if alias == canonical or resolved.get(alias) is None:
            continue
        for other, value in seen.items():
            if value != resolved[alias]:
                raise PlatformOptionError(
                    f"Conflicting compute settings for platform '{platform}': "
                    f"'{other}={value}' disagrees with '{alias}={resolved[alias]}'. "
                    f"Keep only '{canonical}'."
                )
        seen[alias] = resolved[alias]
        warn_deprecated_compute_alias(platform, alias, canonical)
    canonical_value = resolved.get(canonical)
    if canonical_value is not None:
        for alias, value in seen.items():
            if value != canonical_value:
                raise PlatformOptionError(
                    f"Conflicting compute settings for platform '{platform}': "
                    f"'{canonical}={canonical_value}' disagrees with '{alias}={value}'. "
                    f"Keep only '{canonical}'."
                )
    elif seen:
        resolved[canonical] = next(iter(seen.values()))


def resolve_resource_kind(platform: str, merged: Mapping[str, Any]) -> str | None:
    declaration = get_compute_declaration(platform)
    if declaration is None:
        return None
    kind = declaration.resource_kind
    if kind is None or isinstance(kind, str):
        return kind
    present = [alias for alias in declaration.resource_aliases if merged.get(alias) is not None]
    if len(present) > 1:
        from benchbox.core.hooks.platform_hooks import PlatformOptionError

        names = " and ".join(f"'{alias}={merged[alias]}'" for alias in present)
        raise PlatformOptionError(
            f"Ambiguous compute resource for platform '{platform}': {names} "
            f"name different vendor objects. Keep only one native key."
        )
    for alias in present:
        if alias in kind:
            return kind[alias]
    if merged.get(COMPUTE_RESOURCE_OPTION) is not None:
        from benchbox.core.hooks.platform_hooks import PlatformOptionError

        names = ", ".join(f"'{alias}'" for alias in declaration.resource_aliases)
        raise PlatformOptionError(
            f"Ambiguous compute resource for platform '{platform}': "
            f"'{COMPUTE_RESOURCE_OPTION}={merged[COMPUTE_RESOURCE_OPTION]}' "
            f"could be {names}. Use one native key instead."
        )
    return None


def compute_value_keys(platform: str) -> tuple[str, ...]:
    declaration = get_compute_declaration(platform)
    if declaration is None:
        return ()
    return (COMPUTE_RESOURCE_OPTION, *declaration.resource_aliases)


def compute_size_keys(platform: str) -> tuple[str, ...]:
    declaration = get_compute_declaration(platform)
    if declaration is None:
        return ()
    return (COMPUTE_SIZE_OPTION, *declaration.size_aliases)


def register_compute_specs() -> None:
    from benchbox.core.hooks.platform_hooks import PlatformHookRegistry, PlatformOptionSpec
    from benchbox.core.platform_manifest import PLATFORM_MANIFEST

    for entry in PLATFORM_MANIFEST:
        raw = entry.capabilities.get("compute", {})
        resource_aliases = tuple(raw.get("native_resource_keys", ())) if isinstance(raw, Mapping) else ()
        size_aliases = tuple(raw.get("native_size_keys", ())) if isinstance(raw, Mapping) else ()
        specs: list[PlatformOptionSpec] = []
        if resource_aliases:
            specs.append(
                PlatformOptionSpec(
                    name=COMPUTE_RESOURCE_OPTION,
                    help=f"Named compute resource running this benchmark ({entry.key}).",
                    aliases=tuple(alias for alias in resource_aliases if alias != COMPUTE_RESOURCE_OPTION),
                )
            )
        if size_aliases:
            size_choices = tuple(raw.get("size_choices", ())) if isinstance(raw, Mapping) else ()
            size_default = raw.get("size_default") if isinstance(raw, Mapping) else None
            specs.append(
                PlatformOptionSpec(
                    name=COMPUTE_SIZE_OPTION,
                    help=f"Compute size for the named compute resource ({entry.key}).",
                    aliases=tuple(alias for alias in size_aliases if alias != COMPUTE_SIZE_OPTION),
                    choices=size_choices or None,
                    default=size_default,
                )
            )
        existing = PlatformHookRegistry.list_option_specs(entry.key)
        specs = [spec for spec in specs if spec.name not in existing]
        if specs:
            PlatformHookRegistry.register_option_specs(entry.key, *specs)


__all__ = [
    "COMPUTE_ALIAS_REMOVED_IN",
    "COMPUTE_RESOURCE_OPTION",
    "COMPUTE_SIZE_OPTION",
    "ComputeDeclaration",
    "compute_size_keys",
    "compute_value_keys",
    "declares_compute",
    "get_compute_declaration",
    "normalize_compute_options",
    "register_compute_specs",
    "resolve_resource_kind",
    "warn_deprecated_compute_alias",
]
