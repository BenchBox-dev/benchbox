from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

from tests.integration.mcp._transport import http_client

pytestmark = [pytest.mark.integration, pytest.mark.medium]

PROMPT_ARGUMENTS: dict[str, dict[str, str]] = {
    "analyze_results": {"benchmark": "tpch", "platform": "duckdb", "focus": "slowest_queries"},
    "benchmark_planning": {"use_case": "comparison", "platforms": "duckdb,snowflake", "time_budget_minutes": "60"},
    "benchmark_run": {"platform": "duckdb", "benchmark": "tpch", "scale_factor": "0.01", "queries": "Q1,Q6"},
    "compare_platforms": {"benchmark": "tpch", "platforms": "duckdb,polars-df", "scale_factor": "0.1"},
    "identify_regressions": {
        "baseline_run": "run1.json",
        "comparison_run": "run2.json",
        "threshold_percent": "15",
    },
    "platform_tuning": {"platform": "duckdb", "workload": "OLAP heavy joins"},
    "troubleshoot_failure": {
        "error_message": "Connection refused",
        "platform": "snowflake",
        "benchmark": "tpch",
    },
}
RAW_TEXTCHARTS_PREFIX = "textcharts_"


async def _inventory(client: Client) -> dict[str, list[dict[str, Any]]]:

    def normalize(items: list[Any]) -> list[dict[str, Any]]:
        return sorted(
            (item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in items),
            key=lambda item: str(item.get("name", item.get("uri", item.get("uriTemplate", "")))),
        )

    tools = await client.list_tools()
    resources = await client.list_resources()
    templates = await client.list_resource_templates()
    prompts = await client.list_prompts()
    return {
        "tools": normalize(tools.tools),
        "resources": normalize(resources.resources),
        "resource_templates": normalize(templates.resource_templates),
        "prompts": normalize(prompts.prompts),
    }


async def _prompt_payloads(client: Client) -> dict[str, list[str]]:

    rendered: dict[str, list[str]] = {}
    for name, arguments in PROMPT_ARGUMENTS.items():
        result = await client.get_prompt(name, arguments)
        rendered[name] = [message.content.text for message in result.messages]
    return rendered


def test_stdio_and_streamable_http_publish_identical_contracts(tmp_path: Path) -> None:
    async def exercise() -> tuple[
        dict[str, list[dict[str, Any]]],
        dict[str, list[dict[str, Any]]],
        dict[str, list[str]],
        dict[str, list[str]],
    ]:
        results_dir = tmp_path / "results"
        charts_dir = tmp_path / "charts"
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "benchbox.mcp",
                "--results-dir",
                str(results_dir),
                "--charts-dir",
                str(charts_dir),
            ],
            env=dict(os.environ),
        )
        with open(os.devnull, "w", encoding="utf-8") as errlog:
            async with Client(stdio_client(params, errlog=errlog), mode="legacy") as stdio:
                stdio_inventory = await _inventory(stdio)
                stdio_prompts = await _prompt_payloads(stdio)

        async with http_client(
            tmp_path,
            mode="auto",
            request_headers=[],
            response_headers=[],
        ) as http:
            http_inventory = await _inventory(http)
            http_prompts = await _prompt_payloads(http)

        return stdio_inventory, http_inventory, stdio_prompts, http_prompts

    stdio_inventory, http_inventory, stdio_prompts, http_prompts = anyio.run(exercise)

    assert stdio_inventory == http_inventory
    assert stdio_prompts == http_prompts
    assert set(http_prompts) == {prompt["name"] for prompt in http_inventory["prompts"]}
    assert all(payload and all(text.strip() for text in payload) for payload in http_prompts.values())
    for inventory in (stdio_inventory, http_inventory):
        tool_names = {tool["name"] for tool in inventory["tools"]}
        assert {"suggest_charts", "generate_chart"} <= tool_names
        assert not any(name.startswith(RAW_TEXTCHARTS_PREFIX) for name in tool_names)
    assert len(http_inventory["tools"]) == 12
    assert len(http_inventory["resources"]) == 4
    assert len(http_inventory["resource_templates"]) == 2
    assert len(http_inventory["prompts"]) == 7
