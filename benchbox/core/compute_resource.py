from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

COMPUTE_RESOURCE_OPTION = "compute_resource"
COMPUTE_SIZE_OPTION = "compute_size"


@dataclass(frozen=True)
class ComputeDeclaration:
    resource_kind: str | Mapping[str, str] | None
    native_resource_keys: tuple[str, ...]
    native_size_keys: tuple[str, ...]
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
    native_resource_keys = tuple(raw.get("native_resource_keys", ()))
    native_size_keys = tuple(raw.get("native_size_keys", ()))
    if not native_resource_keys and not native_size_keys:
        return None
    return ComputeDeclaration(
        resource_kind=raw.get("resource_kind"),
        native_resource_keys=native_resource_keys,
        native_size_keys=native_size_keys,
        size_choices=tuple(raw.get("size_choices", ())),
        size_default=raw.get("size_default"),
    )


def declares_compute(platform: str) -> bool:
    return get_compute_declaration(platform) is not None


def migrate_saved_compute_keys(platform: str, saved: Mapping[str, Any]) -> dict[str, Any]:
    declaration = get_compute_declaration(platform)
    if declaration is None:
        return dict(saved)
    renames = {
        **dict.fromkeys(declaration.native_resource_keys, COMPUTE_RESOURCE_OPTION),
        **dict.fromkeys(declaration.native_size_keys, COMPUTE_SIZE_OPTION),
    }
    migrated: dict[str, Any] = {}
    for key, value in saved.items():
        target = renames.get(key, key)
        if target != key and (target in saved or target in migrated):
            continue
        migrated[target] = value
    return migrated


def resolve_linked_compute_resource(
    merged: Mapping[str, Any],
    layers: Sequence[Mapping[str, Any]],
    linked_keys: tuple[str, ...],
) -> dict[str, Any]:
    keys = (COMPUTE_RESOURCE_OPTION, *linked_keys)
    resolved = {key: value for key, value in merged.items() if key not in keys}
    winner = next((layer for layer in layers if any(layer.get(key) is not None for key in keys)), None)
    if winner is not None:
        resolved.update({key: winner[key] for key in keys if winner.get(key) is not None})
    return resolved


def resolve_resource_kind(platform: str, merged: Mapping[str, Any]) -> str | None:
    declaration = get_compute_declaration(platform)
    if declaration is None:
        return None
    kind = declaration.resource_kind
    if kind is None or isinstance(kind, str):
        return kind
    return next((kind[key] for key in declaration.native_resource_keys if merged.get(key) is not None), None)


def compute_value_keys(platform: str) -> tuple[str, ...]:
    declaration = get_compute_declaration(platform)
    if declaration is None:
        return ()
    return (COMPUTE_RESOURCE_OPTION, *declaration.native_resource_keys)


def compute_size_keys(platform: str) -> tuple[str, ...]:
    declaration = get_compute_declaration(platform)
    if declaration is None:
        return ()
    return (COMPUTE_SIZE_OPTION, *declaration.native_size_keys)


def register_compute_specs() -> None:
    from benchbox.core.hooks.platform_hooks import PlatformHookRegistry, PlatformOptionSpec
    from benchbox.core.platform_manifest import PLATFORM_MANIFEST

    for entry in PLATFORM_MANIFEST:
        declaration = get_compute_declaration(entry.key)
        if declaration is None:
            continue
        specs: list[PlatformOptionSpec] = []
        if declaration.native_resource_keys:
            specs.append(
                PlatformOptionSpec(
                    name=COMPUTE_RESOURCE_OPTION,
                    help=f"Named compute resource running this benchmark ({entry.key}).",
                )
            )
        if declaration.native_size_keys:
            specs.append(
                PlatformOptionSpec(
                    name=COMPUTE_SIZE_OPTION,
                    help=f"Compute size for the named compute resource ({entry.key}).",
                    choices=declaration.size_choices or None,
                    default=declaration.size_default,
                )
            )
        existing = PlatformHookRegistry.list_option_specs(entry.key)
        specs = [spec for spec in specs if spec.name not in existing]
        if specs:
            PlatformHookRegistry.register_option_specs(entry.key, *specs)


__all__ = [
    "COMPUTE_RESOURCE_OPTION",
    "COMPUTE_SIZE_OPTION",
    "ComputeDeclaration",
    "compute_size_keys",
    "compute_value_keys",
    "declares_compute",
    "get_compute_declaration",
    "migrate_saved_compute_keys",
    "register_compute_specs",
    "resolve_linked_compute_resource",
    "resolve_resource_kind",
]
