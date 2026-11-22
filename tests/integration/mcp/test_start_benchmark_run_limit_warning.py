from __future__ import annotations

import json
from pathlib import Path

import anyio
import pytest
from mcp.types import TextContent

from benchbox.mcp.jobs import DurableJobWorker
from benchbox.utils.run_deadline import remote_cleanup_warning
from tests.integration.mcp._security import authenticated_http_client, write_security_config

pytestmark = [pytest.mark.integration, pytest.mark.medium]


def _executor(job, staging: Path) -> dict[str, object]:
    result = staging / "benchmark.json"
    result.write_text(json.dumps({"execution_id": job.execution_id}), encoding="utf-8")
    return {"mcp_metadata": {"execution_id": job.execution_id, "result_file": str(result)}}


def test_start_benchmark_warns_about_remote_work_only_for_a_cloud_platform_with_a_limit(
    tmp_path: Path, monkeypatch
) -> None:
    token = "tenant-limit-token"
    config = write_security_config(tmp_path, tokens={token: ("tenant-a", ("benchbox:read", "benchbox:execute"))})
    policy = json.loads(config.read_text(encoding="utf-8"))
    policy["allowed_platforms"] = ["duckdb", "snowflake", "quanton"]
    config.write_text(json.dumps(policy), encoding="utf-8")
    monkeypatch.setattr(DurableJobWorker, "_execute_benchmark", staticmethod(_executor))

    async def start(client, **arguments) -> dict[str, object]:
        result = await client.call_tool("start_benchmark", {"benchmark": "tpch", "scale_factor": 0.01, **arguments})
        assert not result.is_error, result.content
        assert isinstance(result.content[0], TextContent)
        return json.loads(result.content[0].text)

    async def exercise() -> None:
        async with authenticated_http_client(config, token) as (client, _):
            implemented = await start(client, platform="quanton", timeout_seconds=30)
            assert implemented["warnings"] == [remote_cleanup_warning("Onehouse Quanton", True)]

            not_implemented = await start(client, platform="snowflake", timeout_seconds=30)
            assert not_implemented["warnings"] == [remote_cleanup_warning("Snowflake", False)]

            assert not (await start(client, platform="quanton")).get("warnings")
            assert not (await start(client, platform="quanton", timeout_seconds=0)).get("warnings")
            assert not (await start(client, platform="duckdb", timeout_seconds=30)).get("warnings")

    anyio.run(exercise)
