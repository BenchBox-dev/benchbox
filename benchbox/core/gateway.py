from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

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


class GatewayNotImplementedError(GatewayConfigurationError):
    pass


def _manifest_platform_key(platform: str) -> str:
    name = platform.lower().strip().split(":", 1)[0]
    return get_all_platform_aliases().get(name, name)


def supported_gateways(platform: str) -> Mapping[str, GatewayCapability]:
    from benchbox.core.platform_registry import PlatformRegistry

    caps = PlatformRegistry.get_platform_capabilities(_manifest_platform_key(platform))
    return {} if caps is None else dict(caps.gateways)


def resolve_requested_gateway(platform: str, gateway: str | None) -> str:
    if not gateway or gateway == DEFAULT_GATEWAY:
        return DEFAULT_GATEWAY

    gateways = supported_gateways(platform)
    if gateway not in gateways:
        allowed = ", ".join([DEFAULT_GATEWAY, *sorted(gateways.keys())])
        raise UnsupportedGatewayError(f"Platform {platform!r} does not support gateway {gateway!r}. Allowed: {allowed}")

    raise GatewayNotImplementedError(f"gateway routing is not implemented for {platform} yet")


def _parse_port(value: str) -> int:
    from benchbox.core.hooks.platform_hooks import PlatformOptionError

    try:
        port = int(value.strip())
    except ValueError:
        raise PlatformOptionError(f"Invalid port '{value}' for option 'gateway_port'") from None
    if not 1 <= port <= 65535:
        raise PlatformOptionError(f"Invalid port '{value}' for option 'gateway_port'. Allowed range: 1-65535")
    return port


def register_gateway_connection_specs() -> None:
    from benchbox.core.hooks.platform_hooks import PlatformHookRegistry, PlatformOptionSpec, parse_bool
    from benchbox.core.platform_manifest import PLATFORM_MANIFEST

    specs = [
        PlatformOptionSpec(
            name="gateway_host",
            help="Gateway endpoint host or IP address.",
        ),
        PlatformOptionSpec(
            name="gateway_port",
            parser=_parse_port,
            help="Gateway endpoint port number (1-65535).",
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
            parser=parse_bool,
            help="Allow OCSP validation to fail open when connecting via gateway (true or false).",
        ),
        PlatformOptionSpec(
            name="allow_insecure_gateway",
            parser=parse_bool,
            help="Allow insecure (TLS disabled / self-signed) gateway connection (true or false).",
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
    "GatewayNotImplementedError",
    "UnsupportedGatewayError",
    "register_gateway_connection_specs",
    "resolve_requested_gateway",
    "supported_gateways",
]
