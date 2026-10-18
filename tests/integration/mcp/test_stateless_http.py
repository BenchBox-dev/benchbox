from __future__ import annotations

from pathlib import Path

import anyio
import pytest
from mcp.types import TextContent

from tests.integration.mcp._transport import http_client

pytestmark = [pytest.mark.integration, pytest.mark.medium]

EXPECTED_PROMPTS = {
    "analyze_results",
    "benchmark_planning",
    "benchmark_run",
    "compare_platforms",
    "identify_regressions",
    "platform_tuning",
    "troubleshoot_failure",
}


async def _prompt_text(client, name: str, arguments: dict[str, str]) -> str:
    result = await client.get_prompt(name, arguments)
    assert len(result.messages) == 1
    content = result.messages[0].content
    assert isinstance(content, TextContent)
    return content.text


def test_modern_discovery_and_tool_call_are_sessionless(tmp_path: Path) -> None:
    request_headers: list[dict[str, str]] = []
    response_headers: list[dict[str, str]] = []

    async def exercise() -> None:
        async with http_client(
            tmp_path,
            mode="auto",
            request_headers=request_headers,
            response_headers=response_headers,
        ) as client:
            assert client.session.discover_result is not None
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} >= {"list_available", "run_benchmark", "get_results"}

            result = await client.call_tool("list_available", {"category": "charts"})
            assert not result.is_error
            assert isinstance(result.content[0], TextContent)
            assert "charts" in result.content[0].text

    anyio.run(exercise)

    assert len(request_headers) >= 3
    assert all("mcp-session-id" not in headers for headers in request_headers)
    assert all("mcp-session-id" not in headers for headers in response_headers)


def test_modern_prompt_list_and_get_are_sessionless(tmp_path: Path) -> None:
    request_headers: list[dict[str, str]] = []
    response_headers: list[dict[str, str]] = []

    async def exercise() -> None:
        async with http_client(
            tmp_path,
            mode="auto",
            request_headers=request_headers,
            response_headers=response_headers,
        ) as client:
            prompts = await client.list_prompts()
            assert {prompt.name for prompt in prompts.prompts} == EXPECTED_PROMPTS

            rendered = await _prompt_text(
                client,
                "benchmark_run",
                {"platform": "duckdb", "benchmark": "tpch", "scale_factor": "0.01", "queries": "Q1,Q6"},
            )
            assert "Platform: duckdb" in rendered
            assert "Benchmark: TPCH" in rendered
            assert "Query subset: Q1,Q6" in rendered

    anyio.run(exercise)

    assert len(request_headers) >= 3
    assert all("mcp-session-id" not in headers for headers in request_headers)
    assert all("mcp-session-id" not in headers for headers in response_headers)


def test_supported_legacy_http_client_does_not_require_sticky_session(tmp_path: Path) -> None:
    request_headers: list[dict[str, str]] = []
    response_headers: list[dict[str, str]] = []

    async def exercise() -> None:
        async with http_client(
            tmp_path,
            mode="legacy",
            request_headers=request_headers,
            response_headers=response_headers,
        ) as client:
            tools = await client.list_tools()
            assert len(tools.tools) == 12

    anyio.run(exercise)

    assert request_headers
    assert all("mcp-session-id" not in headers for headers in request_headers)
    assert all("mcp-session-id" not in headers for headers in response_headers)
