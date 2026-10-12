from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from benchbox.core.platform_manifest import get_all_platform_aliases

if TYPE_CHECKING:
    from benchbox.core.platform_registry import GatewayCapability

DEFAULT_GATEWAY = "native"
GATEWAY_CONNECTION_OPTIONS = (
    "gateway_host",
    "gateway_port",
    "gateway_protocol",
    "gateway_ca_bundle",
    "gateway_ocsp_fail_open",
    "allow_insecure_gateway",
)


class UnsupportedGatewayError(ValueError):
    pass


class GatewayConfigurationError(ValueError):
    pass


def _manifest_platform_key(platform: str) -> str:
    name = platform.lower().strip().split(":", 1)[0]
    return get_all_platform_aliases().get(name, name)


def supported_gateways(platform: str) -> Mapping[str, GatewayCapability]:
    from benchbox.core.platform_registry import PlatformRegistry

    caps = PlatformRegistry.get_platform_capabilities(_manifest_platform_key(platform))
    return {} if caps is None else dict(caps.gateways)


def resolve_requested_gateway(
    platform: str,
    gateway: str | None,
    gateway_host: str | None = None,
) -> str:
    if not gateway or gateway == DEFAULT_GATEWAY:
        return DEFAULT_GATEWAY

    gateways = supported_gateways(platform)
    if gateway not in gateways:
        allowed = ", ".join([DEFAULT_GATEWAY, *sorted(gateways.keys())])
        raise UnsupportedGatewayError(f"Platform {platform!r} does not support gateway {gateway!r}. Allowed: {allowed}")

    spec = gateways[gateway]
    if spec.requires_host and not gateway_host:
        raise GatewayConfigurationError(f"Gateway {gateway!r} on platform {platform!r} requires gateway_host")

    return gateway


def _requested_engine(
    bundle_or_platform: Any,
    platform_data: Mapping[str, Any],
    config_data: Mapping[str, Any],
    result_engine: Mapping[str, Any],
) -> str:
    if isinstance(bundle_or_platform, Mapping) and isinstance(bundle_or_platform.get("platform"), Mapping):
        from benchbox.core.results.execution_variant import read_execution_engine

        return str(read_execution_engine(bundle_or_platform).get("requested") or "default")
    execution_engine = platform_data.get("execution_engine")
    if isinstance(execution_engine, Mapping):
        return str(execution_engine.get("requested") or "default")
    if result_engine.get("requested"):
        return str(result_engine["requested"])
    return str(config_data.get("execution_engine") or "default")


def extract_variant_tuple(bundle_or_platform: Any) -> tuple[str, str, str]:
    platform_data: Mapping[str, Any] = {}
    config_data: Mapping[str, Any] = {}
    result_engine: Mapping[str, Any] = {}

    if hasattr(bundle_or_platform, "platform_info"):
        p_info = getattr(bundle_or_platform, "platform_info", None)
        if isinstance(p_info, Mapping):
            platform_data = p_info
        recorded_engine = getattr(bundle_or_platform, "execution_engine", None)
        if isinstance(recorded_engine, Mapping):
            result_engine = recorded_engine
        e_meta = getattr(bundle_or_platform, "execution_metadata", None)
        if isinstance(e_meta, Mapping) and isinstance(e_meta.get("run_config"), Mapping):
            config_data = e_meta["run_config"]
    elif isinstance(bundle_or_platform, Mapping):
        if "platform" in bundle_or_platform and isinstance(bundle_or_platform["platform"], Mapping):
            platform_data = bundle_or_platform["platform"]
            if "config" in bundle_or_platform and isinstance(bundle_or_platform["config"], Mapping):
                config_data = bundle_or_platform["config"]
        else:
            platform_data = bundle_or_platform
            if "config" in platform_data and isinstance(platform_data["config"], Mapping):
                config_data = platform_data["config"]
    else:
        return ("local", "default", DEFAULT_GATEWAY)
    deployment = "local"
    if "deployment" in platform_data and isinstance(platform_data["deployment"], Mapping):
        deployment = str(platform_data["deployment"].get("selected") or "local")
    elif "deployment" in config_data:
        deployment = str(config_data["deployment"])
    elif "platform_mode" in config_data:
        deployment = str(config_data["platform_mode"])

    engine = _requested_engine(bundle_or_platform, platform_data, config_data, result_engine)

    gateway = DEFAULT_GATEWAY
    if "gateway" in platform_data and isinstance(platform_data["gateway"], Mapping):
        gw = platform_data["gateway"]
        if gw.get("routed") is True and gw.get("name"):
            gateway = str(gw["name"])
    elif "gateway" in config_data and config_data["gateway"]:
        gateway = str(config_data["gateway"])

    return (deployment, engine, gateway)


def variants_comparable(a: Any, b: Any) -> bool:
    return extract_variant_tuple(a) == extract_variant_tuple(b)


def register_gateway_connection_specs() -> None:
    from benchbox.core.hooks.platform_hooks import PlatformHookRegistry, PlatformOptionSpec
    from benchbox.core.platform_manifest import PLATFORM_MANIFEST

    specs = [
        PlatformOptionSpec(
            name="gateway_host",
            help="Gateway endpoint host or IP address.",
        ),
        PlatformOptionSpec(
            name="gateway_port",
            help="Gateway endpoint port number.",
        ),
        PlatformOptionSpec(
            name="gateway_protocol",
            help="Gateway endpoint protocol (https or http).",
            choices=("https", "http"),
            default="https",
        ),
        PlatformOptionSpec(
            name="gateway_ca_bundle",
            help="Path to custom CA certificate bundle for gateway TLS validation.",
        ),
        PlatformOptionSpec(
            name="gateway_ocsp_fail_open",
            help="Allow OCSP validation to fail open when connecting via gateway.",
        ),
        PlatformOptionSpec(
            name="allow_insecure_gateway",
            help="Allow insecure (TLS disabled / self-signed) gateway connection.",
        ),
    ]

    for entry in PLATFORM_MANIFEST:
        if entry.capabilities.get("gateways"):
            existing = PlatformHookRegistry.list_option_specs(entry.key)
            missing = [s for s in specs if s.name not in existing]
            if missing:
                PlatformHookRegistry.register_option_specs(entry.key, *missing)


__all__ = [
    "DEFAULT_GATEWAY",
    "GATEWAY_CONNECTION_OPTIONS",
    "GatewayConfigurationError",
    "UnsupportedGatewayError",
    "extract_variant_tuple",
    "register_gateway_connection_specs",
    "resolve_requested_gateway",
    "supported_gateways",
    "variants_comparable",
]
