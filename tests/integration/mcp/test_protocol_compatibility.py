from __future__ import annotations

from pathlib import Path

import anyio
import pytest

from benchbox.mcp.readiness import CURRENT_PROTOCOL_VERSION, SUPPORTED_LEGACY_VERSION
from tests.integration.mcp._transport import http_client

pytestmark = [pytest.mark.integration, pytest.mark.medium]


@pytest.mark.parametrize(
    ("mode", "expected_version"),
    [("auto", CURRENT_PROTOCOL_VERSION), ("legacy", SUPPORTED_LEGACY_VERSION)],
)
def test_supported_protocol_is_sessionless(tmp_path: Path, mode: str, expected_version: str) -> None:
    request_headers: list[dict[str, str]] = []
    response_headers: list[dict[str, str]] = []

    async def exercise() -> None:
        async with http_client(
            tmp_path,
            mode=mode,
            request_headers=request_headers,
            response_headers=response_headers,
        ) as client:
            assert client.protocol_version == expected_version
            assert (await client.list_tools()).tools

    anyio.run(exercise)
    assert request_headers
    assert all("mcp-session-id" not in headers for headers in request_headers)
    assert all("mcp-session-id" not in headers for headers in response_headers)


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_conformance_baseline_assumptions_are_guarded(tmp_path: Path, mode: str) -> None:

    async def exercise() -> None:
        async with http_client(tmp_path, mode=mode, request_headers=[], response_headers=[]) as client:
            tool_names = {tool.name for tool in (await client.list_tools()).tools}
            first_tools = [tool.model_dump(mode="json") for tool in (await client.list_tools()).tools]
            second_tools = [tool.model_dump(mode="json") for tool in (await client.list_tools()).tools]
            first_prompts = [prompt.model_dump(mode="json") for prompt in (await client.list_prompts()).prompts]
            second_prompts = [prompt.model_dump(mode="json") for prompt in (await client.list_prompts()).prompts]
            capabilities = client.server_capabilities

            assert "test_missing_capability" not in tool_names
            assert first_tools == second_tools
            assert first_prompts == second_prompts
            assert capabilities.prompts is not None
            assert capabilities.prompts.list_changed is False
            assert capabilities.resources is not None
            assert capabilities.resources.list_changed is False
            assert capabilities.tools is not None
            assert capabilities.tools.list_changed is False

    anyio.run(exercise)
