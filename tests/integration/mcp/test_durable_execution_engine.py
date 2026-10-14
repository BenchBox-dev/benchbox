from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.types import TextContent

from tests.integration.mcp._security import authenticated_http_client, write_security_config

pytestmark = [pytest.mark.integration, pytest.mark.medium]

pytest.importorskip("polars")

TOKEN = "durable-engine-token"


def _allow_polars(config_path: Path) -> None:
    policy = json.loads(config_path.read_text(encoding="utf-8"))
    policy["allowed_platforms"] = ["duckdb", "polars-df"]
    config_path.write_text(json.dumps(policy), encoding="utf-8")


def _payload(result: Any) -> dict[str, Any]:
    assert not result.is_error, result
    assert isinstance(result.content[0], TextContent)
    return json.loads(result.content[0].text)


def test_start_benchmark_carries_the_requested_engine_into_the_bundle(tmp_path: Path) -> None:
    config = write_security_config(tmp_path, tokens={TOKEN: ("tenant-a", ("benchbox:read", "benchbox:execute"))})
    _allow_polars(config)

    async def exercise() -> dict[str, Any]:
        async with authenticated_http_client(config, TOKEN) as (client, _):
            started = _payload(
                await client.call_tool(
                    "start_benchmark",
                    {
                        "platform": "polars-df",
                        "benchmark": "tpch",
                        "scale_factor": 0.01,
                        "queries": "6",
                        "execution_engine": "streaming",
                    },
                )
            )
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

    result_file = Path(result["mcp_metadata"]["result_file"])
    bundle = json.loads(result_file.read_text(encoding="utf-8"))
    assert bundle["config"]["execution_engine"] == "streaming"
    receipt = bundle["platform"]["execution_engine"]
    assert receipt["requested"] == "streaming"
    assert receipt["applied"] == "streaming"
    assert bundle["platform"]["variant"] == "polars-df+streaming"
