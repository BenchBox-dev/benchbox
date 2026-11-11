from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.transport_security import TransportSecuritySettings

from benchbox.mcp.cli import parse_args
from benchbox.mcp.readiness import (
    AUTOMATED_GATES,
    CONFORMANCE_REVISION,
    CURRENT_PROTOCOL_VERSION,
    EXTERNAL_GATES,
    INSPECTOR_INTEGRITY,
    INSPECTOR_REVISION,
    INSPECTOR_VERSION,
)
from benchbox.mcp.security import RemoteSecurityRuntime
from benchbox.mcp.transport import MCPTransportSettings, is_supported_loopback_host, run_transport
from tests.integration.mcp._security import write_security_config

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_transport_defaults_preserve_stdio() -> None:
    args = parse_args([])

    assert args.transport == "stdio"
    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.streamable_http_path == "/mcp"
    assert args.security_config is None
    assert args.readiness_evidence is None


def test_streamable_http_cli_options_are_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    from benchbox.mcp import cli

    captured: dict[str, object] = {}
    monkeypatch.setattr(cli, "run_server", lambda **kwargs: captured.update(kwargs))

    cli.main(
        [
            "--transport",
            "streamable-http",
            "--host",
            "::1",
            "--port",
            "8765",
            "--streamable-http-path",
            "/benchbox-mcp",
            "--readiness-evidence",
            "/tmp/readiness.json",
        ]
    )

    assert captured["transport"] == "streamable-http"
    assert captured["host"] == "::1"
    assert captured["port"] == 8765
    assert captured["streamable_http_path"] == "/benchbox-mcp"
    assert captured["readiness_evidence"] == "/tmp/readiness.json"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_hosts_are_accepted(host: str) -> None:
    assert is_supported_loopback_host(host)
    MCPTransportSettings(transport="streamable-http", host=host)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.1", "mcp.example.com"])
def test_non_loopback_http_host_is_rejected(host: str) -> None:
    with pytest.raises(ValueError, match="loopback hosts only"):
        MCPTransportSettings(transport="streamable-http", host=host)


def test_stdio_uses_explicit_sdk_transport() -> None:
    server = MagicMock()

    run_transport(server, MCPTransportSettings())

    server.run.assert_called_once_with(transport="stdio")


def test_streamable_http_uses_streaming_capable_stateless_sdk_route() -> None:
    server = MagicMock()

    run_transport(
        server,
        MCPTransportSettings(
            transport="streamable-http",
            host="localhost",
            port=8765,
            streamable_http_path="/benchbox-mcp",
        ),
    )

    server.run.assert_called_once_with(
        transport="streamable-http",
        host="localhost",
        port=8765,
        streamable_http_path="/benchbox-mcp",
        stateless_http=True,
        json_response=False,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*", "[::1]", "[::1]:*"],
            allowed_origins=[
                "http://127.0.0.1",
                "http://127.0.0.1:*",
                "http://localhost",
                "http://localhost:*",
                "http://[::1]",
                "http://[::1]:*",
            ],
        ),
    )


def test_remote_binding_fail_closed_without_security_config() -> None:
    with patch("benchbox.mcp.create_server") as create_server:
        from benchbox.mcp import run_server

        with pytest.raises(ValueError, match="loopback hosts only"):
            run_server(transport="streamable-http", host="0.0.0.0")

    create_server.assert_not_called()


def test_remote_binding_accepts_current_revision_bound_evidence(tmp_path: Path) -> None:
    policy = write_security_config(
        tmp_path,
        tokens={"token": ("tenant-a", ("benchbox:read",))},
    )
    runtime = RemoteSecurityRuntime.from_file(policy)
    evidence = tmp_path / "readiness.json"
    evidence.write_text(
        json.dumps(
            {
                "source_revision": "a" * 40,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "protocol_version": CURRENT_PROTOCOL_VERSION,
                "conformance_revision": CONFORMANCE_REVISION,
                "inspector_version": INSPECTOR_VERSION,
                "inspector_revision": INSPECTOR_REVISION,
                "inspector_integrity": INSPECTOR_INTEGRITY,
                "automated": dict.fromkeys(AUTOMATED_GATES, True),
                "external": dict.fromkeys(EXTERNAL_GATES, True),
            }
        ),
        encoding="utf-8",
    )
    env = {
        "BENCHBOX_BUILD_SHA": "a" * 40,
        "BENCHBOX_MCP_READINESS_SHA256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://otel.example.test/v1/traces",
    }
    settings = MCPTransportSettings(
        transport="streamable-http",
        host="10.0.0.12",
        remote_security=runtime,
        readiness_evidence=evidence,
        env=env,
    )
    assert settings.host == "10.0.0.12"


def test_remote_binding_rejects_missing_readiness_evidence(tmp_path: Path) -> None:
    policy = write_security_config(tmp_path, tokens={"token": ("tenant-a", ("benchbox:read",))})
    runtime = RemoteSecurityRuntime.from_file(policy)
    with pytest.raises(ValueError, match="current readiness evidence"):
        MCPTransportSettings(transport="streamable-http", host="10.0.0.12", remote_security=runtime)


def test_stdio_rejects_remote_security_config(tmp_path: Path) -> None:
    policy = write_security_config(
        tmp_path,
        tokens={"token": ("tenant-a", ("benchbox:read",))},
    )
    runtime = RemoteSecurityRuntime.from_file(policy)
    with pytest.raises(ValueError, match="only valid with Streamable HTTP"):
        MCPTransportSettings(remote_security=runtime)


def test_loopback_rejects_misleading_readiness_evidence(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="only valid for a non-loopback bind"):
        MCPTransportSettings(
            transport="streamable-http",
            host="127.0.0.1",
            readiness_evidence=tmp_path / "unused.json",
        )


@pytest.mark.parametrize("port", ["0", "65536"])
def test_cli_rejects_invalid_http_port(port: str) -> None:
    with pytest.raises(SystemExit):
        parse_args(["--port", port])


