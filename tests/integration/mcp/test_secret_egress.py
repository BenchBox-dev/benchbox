from __future__ import annotations

import logging
from pathlib import Path

import anyio
import httpx2
import pytest
from mcp.server.transport_security import TransportSecuritySettings

from tests.integration.mcp._security import authenticated_http_client, start_remote_server, write_security_config

pytestmark = [pytest.mark.integration, pytest.mark.medium]
SENTINEL = "MCP_SECRET_SENTINEL"


def test_invalid_bearer_and_failing_driver_do_not_egress_secret(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = write_security_config(
        tmp_path,
        tokens={SENTINEL: ("tenant-a", ("benchbox:read", "benchbox:execute"))},
    )

    async def exercise() -> list[str]:
        observed: list[str] = []
        async with authenticated_http_client(config, SENTINEL) as (client, runtime):
            from benchbox.mcp.tools import benchmark as benchmark_tools

            def fail_adapter(*args: object, **kwargs: object) -> object:
                raise RuntimeError(f"password={SENTINEL}")

            monkeypatch.setattr(benchmark_tools, "_get_platform_adapter", fail_adapter)
            result = await client.call_tool(
                "run_benchmark",
                {"platform": "duckdb", "benchmark": "tpch", "scale_factor": 0.01},
            )
            observed.append(str(result))
            observed.append(repr(runtime.store.audit_events()))
        return observed

    caplog.set_level(logging.DEBUG)
    observed = anyio.run(exercise)
    assert SENTINEL not in "\n".join(observed)
    assert SENTINEL not in caplog.text


def test_unauthorized_http_response_does_not_echo_bearer(tmp_path: Path) -> None:
    config = write_security_config(
        tmp_path,
        tokens={"valid-token": ("tenant-a", ("benchbox:read",))},
    )

    async def exercise() -> str:
        server, _ = start_remote_server(config)
        app = server.streamable_http_app(stateless_http=True)
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            response = await client.post(
                "/mcp",
                headers={"Authorization": f"Bearer {SENTINEL}", "Content-Type": "application/json"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
            assert response.status_code == 401
            return response.text

    assert SENTINEL not in anyio.run(exercise)


def test_hostile_host_and_origin_are_rejected_without_log_egress(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = write_security_config(
        tmp_path,
        tokens={"valid-token": ("tenant-a", ("benchbox:read",))},
    )

    async def exercise() -> list[str]:
        server, runtime = start_remote_server(config)
        app = server.streamable_http_app(
            stateless_http=True,
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=list(runtime.config.allowed_hosts),
                allowed_origins=list(runtime.config.allowed_origins),
            ),
        )
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://127.0.0.1:8000",
            ) as client:
                common = {
                    "Authorization": "Bearer valid-token",
                    "Content-Type": "application/json",
                }
                hostile_host = await client.post(
                    "/mcp",
                    headers={**common, "Host": f"{SENTINEL}.invalid"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                )
                hostile_origin = await client.post(
                    "/mcp",
                    headers={**common, "Origin": f"https://{SENTINEL}.invalid"},
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                )
                assert hostile_host.status_code == 421
                assert hostile_origin.status_code == 403
                return [hostile_host.text, hostile_origin.text]

    caplog.set_level(logging.DEBUG)
    assert SENTINEL not in "\n".join(anyio.run(exercise))
    assert SENTINEL not in caplog.text
