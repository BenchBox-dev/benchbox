from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from typing import Any

from benchbox.core.execution_variant import (
    NO_GATEWAY,
    append_variant_suffix,
    manifest_default_deployment,
    resolve_variant_platform_id,
)
from benchbox.core.platform_manifest import DEFAULT_EXECUTION_ENGINE

UNKNOWN = "unknown"
LEGACY_STREAMING_ENGINE = "streaming"
_TRUE_STRINGS = frozenset({"true", "1", "yes", "on"})
_ATHENA_PLATFORM_NAME = "athena"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _record(value: Any) -> dict[str, Any]:
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: getattr(value, item.name) for item in fields(value)}
    return dict(value) if isinstance(value, Mapping) else {}


def execution_engine_payload(receipt: Any) -> dict[str, Any]:
    record = _record(receipt)
    if not record.get("requested"):
        return {}
    payload = {key: value for key, value in record.items() if value is not None and value != {}}
    if isinstance(payload.get("applied_native"), Mapping):
        payload["applied_native"] = dict(payload["applied_native"])
    return payload


def gateway_payload(gateway: Any) -> dict[str, Any]:
    record = _record(gateway)
    if not record.get("name") or record.get("routed") is not True:
        return {}
    return {"name": record["name"], "routed": True}


def _is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().lower() in _TRUE_STRINGS


def _legacy_streaming_requested(bundle: Mapping[str, Any], platform_config: Mapping[str, Any]) -> bool:
    platform_options = _mapping(_mapping(bundle.get("config")).get("platform_options"))
    return _is_true(platform_options.get("streaming")) or _is_true(platform_config.get("streaming"))


def read_execution_engine(bundle: Mapping[str, Any]) -> dict[str, Any]:
    platform = _mapping(bundle.get("platform"))
    recorded = execution_engine_payload(platform.get("execution_engine"))
    if recorded:
        return recorded

    reading: dict[str, Any] = {"requested": DEFAULT_EXECUTION_ENGINE, "applied": UNKNOWN, "observed": UNKNOWN}
    platform_config = _mapping(platform.get("config"))
    engine_requested = platform_config.get("engine_requested")
    if isinstance(engine_requested, str) and engine_requested and engine_requested != DEFAULT_EXECUTION_ENGINE:
        reading.update(requested=engine_requested, resolution="explicit")
    elif engine_requested == DEFAULT_EXECUTION_ENGINE:
        reading["resolution"] = "version_default"
    elif _legacy_streaming_requested(bundle, platform_config):
        reading.update(requested=LEGACY_STREAMING_ENGINE, resolution="legacy_option")
    else:
        requested = _mapping(bundle.get("config")).get("execution_engine")
        if isinstance(requested, str) and requested:
            reading.update(requested=requested)
    return reading


def read_requested_variant(bundle: Mapping[str, Any]) -> tuple[str | None, str, str | None]:
    platform = _mapping(bundle.get("platform"))
    selected = _mapping(platform.get("deployment")).get("selected")
    gateway = gateway_payload(platform.get("gateway")).get("name")
    requested = read_execution_engine(bundle).get("requested") or DEFAULT_EXECUTION_ENGINE
    return (str(selected) if selected else None, str(requested), str(gateway) if gateway else None)


def _variant_platform_id(bundle: Mapping[str, Any]) -> str | None:
    name = _mapping(bundle.get("platform")).get("name")
    config = _mapping(bundle.get("config"))
    return resolve_variant_platform_id(
        (str(name) if name else None,), config.get("execution_mode") or config.get("mode")
    )


def published_variant_id(bundle: Mapping[str, Any], base: str) -> str:
    return append_variant_suffix(base, _variant_platform_id(bundle), *read_requested_variant(bundle))


@dataclass(frozen=True)
class ExecutionVariant:
    deployment: str | None
    default_deployment: str | None
    execution_engine: str
    gateway: str

    @property
    def deployment_choice(self) -> str | None:
        return None if self.deployment == self.default_deployment else self.deployment

    def comparison_key(self) -> tuple[str | None, str, str]:
        return (self.deployment_choice, self.execution_engine, self.gateway)

    def describe(self) -> str:
        return (
            f"deployment={self.deployment or 'default'}, "
            f"execution engine={self.execution_engine}, gateway={self.gateway}"
        )


class IncomparableVariantsError(ValueError):
    def __init__(self, differences: Sequence[str]) -> None:
        self.differences = list(differences)
        super().__init__(
            "Runs use different execution variants: "
            + "; ".join(self.differences)
            + ". Compare runs of the same deployment, execution engine and gateway."
        )


def read_execution_variant(bundle: Mapping[str, Any]) -> ExecutionVariant:
    deployment, engine, gateway = read_requested_variant(bundle)
    default = manifest_default_deployment(_variant_platform_id(bundle))
    return ExecutionVariant(deployment or default, default, engine, gateway or NO_GATEWAY)


def platform_info_bundle(
    name: str | None,
    platform_info: Mapping[str, Any] | None,
    *,
    deployment: Any = None,
    execution_engine: Any = None,
) -> dict[str, Any]:
    info = _mapping(platform_info)
    return {
        "platform": {
            "name": name,
            "deployment": _record(deployment) or _record(info.get("deployment")),
            "execution_engine": _record(execution_engine) or _record(info.get("execution_engine")),
            "gateway": _record(info.get("gateway")),
            "config": dict(info),
        }
    }


def variant_differences(baseline: ExecutionVariant, current: ExecutionVariant) -> list[str]:
    parts = (
        (
            "deployment",
            baseline.deployment_choice != current.deployment_choice,
            baseline.deployment or "default",
            current.deployment or "default",
        ),
        (
            "execution engine",
            baseline.execution_engine != current.execution_engine,
            baseline.execution_engine,
            current.execution_engine,
        ),
        ("gateway", baseline.gateway != current.gateway, baseline.gateway, current.gateway),
    )
    return [f"{label} differs ({left} vs {right})" for label, differs, left, right in parts if differs]


def bundle_variant_differences(baseline: Mapping[str, Any], current: Mapping[str, Any]) -> list[str]:
    return variant_differences(read_execution_variant(baseline), read_execution_variant(current))


def variants_comparable(baseline: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    return not bundle_variant_differences(baseline, current)


def require_comparable_variants(baseline: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    differences = bundle_variant_differences(baseline, current)
    if differences:
        raise IncomparableVariantsError(differences)


def read_platform_compute(platform: Mapping[str, Any]) -> Any:
    compute = platform.get("compute")
    if not isinstance(compute, Mapping) or "engine" not in compute or "product" in compute:
        return compute
    if str(platform.get("name", "")).strip().lower() != _ATHENA_PLATFORM_NAME:
        return compute
    return {("product" if key == "engine" else key): value for key, value in compute.items()}
