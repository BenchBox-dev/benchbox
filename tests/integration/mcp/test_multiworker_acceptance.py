from __future__ import annotations

import json
import threading
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import httpx2
import pytest
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from mcp.types import TextContent

from benchbox.mcp import create_server
from benchbox.mcp.jobs import DurableJobWorker
from benchbox.mcp.security import RemoteSecurityRuntime
from tests.integration.mcp._security import write_security_config

pytestmark = [pytest.mark.integration, pytest.mark.medium]


class AlternatingASGITransport(httpx2.AsyncBaseTransport):
    def __init__(self, apps: Sequence[Any]) -> None:
        self._transports = [httpx2.ASGITransport(app=app) for app in apps]
        self._next = 0

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        transport = self._transports[self._next % len(self._transports)]
        self._next += 1
        return await transport.handle_async_request(request)

    async def aclose(self) -> None:
        for transport in self._transports:
            await transport.aclose()


@asynccontextmanager
async def _client(apps: Sequence[Any], token: str) -> AsyncIterator[Client]:
    async with httpx2.AsyncClient(
        transport=AlternatingASGITransport(apps),
        base_url="http://127.0.0.1:8000",
        headers={"Authorization": f"Bearer {token}"},
    ) as http_client:
        transport = streamable_http_client("http://127.0.0.1:8000/mcp", http_client=http_client)
        async with Client(transport, mode="auto") as client:
            yield client


def _content(result: Any) -> dict[str, Any]:
    assert isinstance(result.content[0], TextContent)
    return json.loads(result.content[0].text)


def test_remote_contract_survives_alternating_workers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    token_a = "tenant-a-token"
    token_b = "tenant-b-token"
    release_slow_job = threading.Event()
    config = write_security_config(
        tmp_path,
        tokens={
            token_a: ("tenant-a", ("benchbox:read", "benchbox:execute")),
            token_b: ("tenant-b", ("benchbox:read", "benchbox:execute")),
        },
        admission={
            "requests_per_minute": 1_000,
            "global_concurrency": 2,
            "principal_concurrency": 1,
            "queue_limit": 0,
            "queue_timeout_seconds": 0,
            "lease_seconds": 30,
        },
    )

    def executor(job: Any, staging: Path) -> dict[str, object]:
        if job.request["scale_factor"] == 0.02:
            release_slow_job.wait(timeout=5)
        result = staging / "benchmark.json"
        result.write_text(json.dumps({"execution_id": job.execution_id}), encoding="utf-8")
        return {"mcp_metadata": {"execution_id": job.execution_id, "result_file": str(result)}}

    monkeypatch.setattr(DurableJobWorker, "_execute_benchmark", staticmethod(executor))
    runtime_a = RemoteSecurityRuntime.from_file(config)
    runtime_b = RemoteSecurityRuntime.from_file(config)
    server_a = create_server(remote_security=runtime_a)
    server_b = create_server(remote_security=runtime_b)
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(runtime_a.config.allowed_hosts),
        allowed_origins=list(runtime_a.config.allowed_origins),
    )
    apps = [
        server_a.streamable_http_app(stateless_http=True, json_response=False, transport_security=transport_security),
        server_b.streamable_http_app(stateless_http=True, json_response=False, transport_security=transport_security),
    ]

    async def wait_for(client: Client, execution_id: str, state: str) -> dict[str, Any]:
        for _ in range(200):
            status = _content(await client.call_tool("get_benchmark_status", {"execution_id": execution_id}))
            if status["status"] in ("failed", "cancelled") and state == "completed":
                pytest.fail(
                    f"job {execution_id} terminally {status['status']} "
                    f"(error_code={status.get('error_code')!r}) instead of completed"
                )
            if status["status"] == state:
                return status
            await anyio.sleep(0.01)
        pytest.fail(f"job {execution_id} did not reach {state}")

    async def exercise() -> None:
        async with apps[0].router.lifespan_context(apps[0]), apps[1].router.lifespan_context(apps[1]):
            async with _client(apps, token_a) as client_a:
                tools = {tool.name for tool in (await client_a.list_tools()).tools}
                assert {"start_benchmark", "get_benchmark_status", "get_benchmark_result", "cancel_benchmark"} <= tools
                started = _content(
                    await client_a.call_tool(
                        "start_benchmark",
                        {
                            "platform": "duckdb",
                            "benchmark": "tpch",
                            "scale_factor": 0.01,
                            "idempotency_key": "alternating-complete",
                        },
                    )
                )
                execution_id = started["execution_id"]
                await wait_for(client_a, execution_id, "completed")
                result = _content(await client_a.call_tool("get_benchmark_result", {"execution_id": execution_id}))
                artifact = Path(result["mcp_metadata"]["result_file"])
                assert artifact.is_file()
                assert artifact.is_relative_to(runtime_a.config.workspace_root.resolve())
                assert "tenant-a" not in artifact.parts

                slow = _content(
                    await client_a.call_tool(
                        "start_benchmark",
                        {"platform": "duckdb", "benchmark": "tpch", "scale_factor": 0.02},
                    )
                )
                slow_id = slow["execution_id"]
                await wait_for(client_a, slow_id, "running")
                cancelled = _content(await client_a.call_tool("cancel_benchmark", {"execution_id": slow_id}))
                assert cancelled["cancel_requested"] is True
                assert cancelled["cancel_outcome"] == "requested"
                release_slow_job.set()
                await wait_for(client_a, slow_id, "cancelled")

            async with _client(apps, token_b) as client_b:
                for tool_name in ("get_benchmark_status", "get_benchmark_result", "cancel_benchmark"):
                    with pytest.raises(MCPError, match="not found"):
                        await client_b.call_tool(tool_name, {"execution_id": execution_id})

            first_lease = await runtime_a.store.admit("tenant-a")
            try:
                with pytest.raises(MCPError, match="queue is full"):
                    await runtime_b.store.admit("tenant-a")
            finally:
                runtime_a.store.release(first_lease)

    anyio.run(exercise)
