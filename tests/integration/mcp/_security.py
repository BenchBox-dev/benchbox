from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from benchbox.mcp import run_server
from benchbox.mcp.security import RemoteSecurityRuntime, token_sha256
from benchbox.mcp.transport import MCPTransportSettings


def write_security_config(
    tmp_path: Path,
    *,
    tokens: dict[str, tuple[str, tuple[str, ...]]],
    admission: dict[str, object] | None = None,
    jobs: dict[str, object] | None = None,
) -> Path:

    policy = {
        "issuer_url": "https://auth.example.test",
        "resource_server_url": "https://mcp.example.test/mcp",
        "allowed_hosts": ["127.0.0.1", "127.0.0.1:*"],
        "allowed_origins": ["https://client.example.test"],
        "workspace_root": str(tmp_path / "workspaces"),
        "state_db": str(tmp_path / "security.sqlite3"),
        "tokens": [
            {
                "token_sha256": token_sha256(token),
                "client_id": f"client-{subject}",
                "subject": subject,
                "scopes": list(scopes),
            }
            for token, (subject, scopes) in tokens.items()
        ],
        "allowed_platforms": ["duckdb"],
        "allowed_benchmarks": ["tpch"],
        "max_scale_factor": 1.0,
    }
    if admission is not None:
        policy["admission"] = admission
    if jobs is not None:
        policy["jobs"] = jobs
    path = tmp_path / "security.json"
    path.write_text(json.dumps(policy), encoding="utf-8")
    return path


def start_remote_server(config_path: Path) -> tuple[MCPServer, RemoteSecurityRuntime]:
    started: list[tuple[MCPServer, MCPTransportSettings]] = []
    with patch("benchbox.mcp.transport.run_transport", lambda server, settings: started.append((server, settings))):
        run_server(transport="streamable-http", security_config=config_path)
    server, settings = started[0]
    assert settings.remote_security is not None
    return server, settings.remote_security


@asynccontextmanager
async def authenticated_http_client(
    config_path: Path, token: str
) -> AsyncIterator[tuple[Client, RemoteSecurityRuntime]]:

    server, runtime = start_remote_server(config_path)
    app = server.streamable_http_app(
        host="127.0.0.1",
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=False,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(runtime.config.allowed_hosts),
            allowed_origins=list(runtime.config.allowed_origins),
        ),
    )
    transport = httpx2.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8000",
            headers={"Authorization": f"Bearer {token}"},
        ) as sdk_http_client:
            mcp_transport = streamable_http_client(
                "http://127.0.0.1:8000/mcp",
                http_client=sdk_http_client,
            )
            async with Client(mcp_transport, mode="auto") as client:
                yield client, runtime
