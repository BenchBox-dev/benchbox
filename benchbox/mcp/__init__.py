# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from benchbox.mcp.security import RemoteSecurityRuntime

try:
    from mcp.server.mcpserver import MCPServer
except ImportError as e:
    raise ImportError(
        "MCP SDK not installed. Install with:\n"
        "  pip install 'benchbox[mcp]'  # pip/venv\n"
        "  uv add benchbox --extra mcp  # uv project"
    ) from e

from benchbox.mcp.transport import MCPTransport

__all__ = [
    "create_server",
    "run_server",
    "ErrorCode",
    "ErrorCategory",
    "MCPError",
    "make_error",
]


def create_server(
    *,
    results_dir: str | Path | None = None,
    charts_dir: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    remote_security: RemoteSecurityRuntime | None = None,
) -> MCPServer:
    from benchbox.mcp.server import create_benchbox_server

    kwargs: dict[str, Any] = {
        "results_dir": results_dir,
        "charts_dir": charts_dir,
        "env": env,
    }
    if remote_security is not None:
        kwargs["remote_security"] = remote_security
    return create_benchbox_server(**kwargs)


def run_server(
    *,
    results_dir: str | Path | None = None,
    charts_dir: str | Path | None = None,
    log_level: str | int | None = None,
    env: Mapping[str, str] | None = None,
    transport: MCPTransport = "stdio",
    host: str = "127.0.0.1",
    port: int = 8000,
    streamable_http_path: str = "/mcp",
    security_config: str | Path | None = None,
    readiness_evidence: str | Path | None = None,
) -> None:
    from benchbox.mcp.security import RemoteSecurityRuntime
    from benchbox.mcp.server import configure_server_process
    from benchbox.mcp.transport import MCPTransportSettings, run_transport

    remote_security = RemoteSecurityRuntime.from_file(security_config) if security_config is not None else None

    transport_settings = MCPTransportSettings(
        transport=transport,
        host=host,
        port=port,
        streamable_http_path=streamable_http_path,
        remote_security=remote_security,
        readiness_evidence=Path(readiness_evidence) if readiness_evidence is not None else None,
        env=env,
    )
    configure_server_process(log_level=log_level, env=env, remote=remote_security is not None)
    server_kwargs: dict[str, Any] = {
        "results_dir": results_dir,
        "charts_dir": charts_dir,
        "env": env,
    }
    if remote_security is not None:
        server_kwargs["remote_security"] = remote_security
    server = create_server(**server_kwargs)
    run_transport(server, transport_settings)


def __getattr__(name: str):
    if name in ("ErrorCode", "ErrorCategory", "MCPError", "make_error"):
        from benchbox.mcp.errors import ErrorCategory, ErrorCode, MCPError, make_error

        return {"ErrorCode": ErrorCode, "ErrorCategory": ErrorCategory, "MCPError": MCPError, "make_error": make_error}[
            name
        ]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
