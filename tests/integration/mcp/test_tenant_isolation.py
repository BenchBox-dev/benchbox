from __future__ import annotations

import json
from pathlib import Path

import anyio
import pytest
from mcp.types import CallToolResult, TextContent

from benchbox.mcp.security import Principal
from tests.integration.mcp._security import authenticated_http_client, write_security_config

pytestmark = [pytest.mark.integration, pytest.mark.medium]


def _text(result: CallToolResult) -> str:
    assert isinstance(result.content[0], TextContent)
    return result.content[0].text


def test_principal_cannot_list_read_or_export_another_workspace(tmp_path: Path) -> None:
    token_a = "tenant-a-token"
    token_b = "tenant-b-token"
    config = write_security_config(
        tmp_path,
        tokens={
            token_a: ("tenant-a", ("benchbox:read", "benchbox:write")),
            token_b: ("tenant-b", ("benchbox:read", "benchbox:write")),
        },
    )

    async def exercise() -> None:
        async with authenticated_http_client(config, token_a) as (client_a, runtime_a):
            access = await runtime_a.verifier.verify_token(token_a)
            assert access is not None
            workspace = runtime_a.workspaces.paths_for(Principal.from_access_token(access))
            artifact = workspace.results_dir / "tenant-a-result.json"
            artifact.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "platform": {"name": "duckdb"},
                        "benchmark": {"id": "tpch", "scale_factor": 0.01},
                        "run": {"id": "execution-a", "timestamp": "2026-08-01T00:00:00Z"},
                    }
                ),
                encoding="utf-8",
            )
            own_listing = await client_a.call_tool("get_results", {"format": "list"})
            assert "tenant-a-result.json" in _text(own_listing)

        async with authenticated_http_client(config, token_b) as (client_b, _):
            listing = await client_b.call_tool("get_results", {"format": "list"})
            assert "tenant-a-result.json" not in _text(listing)
            read = await client_b.call_tool("get_results", {"result_file": "tenant-a-result.json"})
            assert "RESOURCE_NOT_FOUND" in _text(read)
            exported = await client_b.call_tool(
                "get_results",
                {"result_file": "tenant-a-result.json", "format": "csv", "output_path": "stolen.csv"},
            )
            assert "RESOURCE_NOT_FOUND" in _text(exported)
            validation = await client_b.call_tool(
                "validate_results",
                {"directory": str(artifact.parent)},
            )
            assert "Absolute validation directories are not allowed" in _text(validation)
            traversal = await client_b.call_tool(
                "validate_results",
                {"directory": "../"},
            )
            assert "Validation directory escapes tenant workspace" in _text(traversal)

    anyio.run(exercise)
    assert not list((tmp_path / "workspaces").rglob("stolen.csv"))
