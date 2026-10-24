from __future__ import annotations

import ipaddress
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from benchbox.core.platform_manifest import get_all_platform_aliases

if TYPE_CHECKING:
    from benchbox.core.platform_registry import GatewayCapability

logger = logging.getLogger(__name__)

DEFAULT_GATEWAY = "native"
GATEWAY_ROUTED_PLATFORMS = frozenset({"snowflake"})
INSECURE_GATEWAY_ENV = "BENCHBOX_ALLOW_INSECURE_GATEWAY"
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

    if _manifest_platform_key(platform) not in GATEWAY_ROUTED_PLATFORMS:
        raise GatewayNotImplementedError(f"gateway routing is not implemented for {platform} yet")
    return gateway


@dataclass(frozen=True)
class GatewayEndpoint:
    name: str
    host: str
    port: int | None
    protocol: str
    ca_bundle: str | None
    ocsp_fail_open: bool | None
    insecure: bool


def is_loopback_host(host: str) -> bool:
    name = host.strip().strip("[]").lower()
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def resolve_gateway_endpoint(
    platform: str,
    gateway: str | None,
    *,
    host: str | None = None,
    port: int | None = None,
    protocol: str | None = None,
    ca_bundle: str | None = None,
    ocsp_fail_open: bool | None = None,
    allow_insecure: bool = False,
) -> GatewayEndpoint | None:
    selected = resolve_requested_gateway(platform, gateway)
    chosen_protocol = (protocol or "https").strip().lower()
    if selected == DEFAULT_GATEWAY:
        stray = [
            name
            for name, value in (
                ("gateway_host", host),
                ("gateway_port", port),
                ("gateway_ca_bundle", ca_bundle),
                ("gateway_ocsp_fail_open", ocsp_fail_open),
                ("allow_insecure_gateway", allow_insecure or None),
            )
            if value not in (None, "")
        ]
        if chosen_protocol != "https":
            stray.append("gateway_protocol")
        if stray:
            raise GatewayConfigurationError(f"{', '.join(stray)} require a gateway other than {DEFAULT_GATEWAY}")
        return None

    clean_host = host.strip() if host else ""
    if _has_host_markup(clean_host):
        raise GatewayConfigurationError(HOST_REJECTED_MESSAGE)
    if supported_gateways(platform)[selected].requires_host and not clean_host:
        raise GatewayConfigurationError(f"gateway {selected!r} requires gateway_host")
    if chosen_protocol not in ("https", "http"):
        raise GatewayConfigurationError(f"Invalid gateway_protocol {protocol!r}. Allowed: https, http")
    if port is not None and not 1 <= int(port) <= 65535:
        raise GatewayConfigurationError(f"Invalid gateway_port {port!r}. Allowed range: 1-65535")
    if ca_bundle and not Path(ca_bundle).expanduser().is_file():
        raise GatewayConfigurationError("gateway_ca_bundle does not name an existing file")

    plaintext_remote = chosen_protocol == "http" and not is_loopback_host(clean_host)
    if allow_insecure or plaintext_remote:
        if os.environ.get(INSECURE_GATEWAY_ENV) != "1":
            raise GatewayConfigurationError(
                f"Insecure gateway settings (allow_insecure_gateway or http to a non-loopback host) "
                f"require {INSECURE_GATEWAY_ENV}=1"
            )
        logger.warning(
            "Gateway %s is configured without TLS verification or over plain HTTP; credentials and results "
            "can be intercepted. Use only on a trusted network.",
            selected,
        )

    return GatewayEndpoint(
        name=selected,
        host=clean_host,
        port=None if port is None else int(port),
        protocol=chosen_protocol,
        ca_bundle=str(Path(ca_bundle).expanduser()) if ca_bundle else None,
        ocsp_fail_open=ocsp_fail_open,
        insecure=bool(allow_insecure),
    )


HOST_REJECTED_MESSAGE = "gateway_host must be a bare host name without credentials, scheme or path"


def _has_host_markup(host: str) -> bool:
    return "@" in host or "/" in host


def _parse_host(value: str) -> str:
    from benchbox.core.hooks.platform_hooks import PlatformOptionError

    if _has_host_markup(value):
        raise PlatformOptionError(HOST_REJECTED_MESSAGE)
    return value.strip()


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
            parser=_parse_host,
            help="Gateway endpoint host name or IP address, without credentials, scheme or path.",
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
    "GATEWAY_ROUTED_PLATFORMS",
    "INSECURE_GATEWAY_ENV",
    "GatewayConfigurationError",
    "GatewayEndpoint",
    "GatewayNotImplementedError",
    "UnsupportedGatewayError",
    "is_loopback_host",
    "register_gateway_connection_specs",
    "resolve_gateway_endpoint",
    "resolve_requested_gateway",
    "supported_gateways",
]
