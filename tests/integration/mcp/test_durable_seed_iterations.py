from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.shared.exceptions import MCPError
from mcp.types import TextContent

from tests.integration.mcp._security import authenticated_http_client, write_security_config

pytestmark = [pytest.mark.integration, pytest.mark.medium]

TOKEN = "durable-seed-token"


def _payload(result: Any) -> dict[str, Any]:
    assert not result.is_error, result
    assert isinstance(result.content[0], TextContent)
    return json.loads(result.content[0].text)


def test_start_benchmark_records_seed_and_iterations_and_bounds_them(tmp_path: Path) -> None:
    config = write_security_config(tmp_path, tokens={TOKEN: ("tenant-a", ("benchbox:read", "benchbox:execute"))})
    policy = json.loads(config.read_text(encoding="utf-8"))
    policy["max_iterations"] = 3
    config.write_text(json.dumps(policy), encoding="utf-8")
    base = {"platform": "duckdb", "benchmark": "tpch", "scale_factor": 0.01, "queries": "6"}

    async def exercise() -> dict[str, Any]:
        async with authenticated_http_client(config, TOKEN) as (client, _):
            with pytest.raises(MCPError, match="iterations exceed policy"):
                await client.call_tool("start_benchmark", {**base, "iterations": 4})
            for invalid in ({"seed": -1}, {"iterations": 0}):
                with pytest.raises(MCPError):
                    await client.call_tool("start_benchmark", {**base, **invalid})
            started = _payload(await client.call_tool("start_benchmark", {**base, "seed": 7, "iterations": 2}))
            execution_id = started["execution_id"]
            for _ in range(600):
                status = _payload(await client.call_tool("get_benchmark_status", {"execution_id": execution_id}))
                if status["status"] in ("failed", "cancelled"):
                    pytest.fail(f"durable benchmark ended {status['status']}: {status}")
                if status["status"] == "completed":
                    break
                await anyio.sleep(0.5)
            else:
                pytest.fail("durable benchmark did not complete")
            return _payload(await client.call_tool("get_benchmark_result", {"execution_id": execution_id}))

    result = anyio.run(exercise)

    assert result["mcp_metadata"]["seed"] == 7
    assert result["mcp_metadata"]["iterations"] == 2
    bundle = json.loads(Path(result["mcp_metadata"]["result_file"]).read_text(encoding="utf-8"))
    assert bundle["config"]["seed"] == 7
