from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import anyio
import pytest
from mcp.server.context import ServerRequestContext
from opentelemetry.context import attach, detach
from opentelemetry.propagate import extract
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from benchbox.mcp.telemetry import RedactedTelemetryMiddleware, ScopeFilteringExporter
from tests.integration.mcp._transport import http_client

pytestmark = [pytest.mark.integration, pytest.mark.medium]
SECRET = "postgresql://user:password@example.invalid/database"


def test_cache_policy_never_shares_resource_bodies(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with http_client(tmp_path, mode="auto", request_headers=[], response_headers=[]) as client:
            discover = client.session.discover_result
            assert discover is not None
            assert discover.ttl_ms == 300_000
            assert discover.cache_scope == "public"
            public_results = [
                await client.list_tools(cache_mode="bypass"),
                await client.list_prompts(cache_mode="bypass"),
                await client.list_resources(cache_mode="bypass"),
                await client.list_resource_templates(cache_mode="bypass"),
            ]
            for result in public_results:
                assert result.ttl_ms == 300_000
                assert result.cache_scope == "public"

            for uri in ("benchbox://results/recent", "benchbox://system/profile"):
                result = await client.read_resource(uri, cache_mode="bypass")
                assert result.ttl_ms == 0
                assert result.cache_scope == "private"

    anyio.run(exercise)


def test_redacted_spans_inherit_w3c_parent_and_bound_attributes(monkeypatch: pytest.MonkeyPatch) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("benchbox.mcp")

    from benchbox.mcp import telemetry

    monkeypatch.setattr(telemetry.trace, "get_tracer", lambda _scope: tracer)
    parent_trace = "0af7651916cd43dd8448eb211c80319c"
    parent_span = "b7ad6b7169203331"
    token = attach(extract({"traceparent": f"00-{parent_trace}-{parent_span}-01"}))
    try:
        ctx = ServerRequestContext(
            session=cast(Any, object()),
            lifespan_context=None,
            protocol_version="2026-07-28",
            method="tools/call",
            params={"name": "x" * 100, "arguments": {"database_url": SECRET}},
            request_id=SECRET,
        )

        async def call_next(_ctx: ServerRequestContext[Any, Any]) -> dict[str, str]:
            return {"result": SECRET}

        anyio.run(RedactedTelemetryMiddleware().__call__, ctx, call_next)
    finally:
        detach(token)
        provider.force_flush()

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.parent is not None and span.parent.span_id == int(parent_span, 16)
    assert span.context is not None and span.context.trace_id == int(parent_trace, 16)
    assert set(span.attributes) == {
        "mcp.method.name",
        "mcp.protocol.version",
        "mcp.response.status",
    }
    assert all(len(str(value)) <= 64 for value in span.attributes.values())
    assert SECRET not in repr(span.attributes)


def test_exporter_drops_sdk_spans_with_raw_request_identifiers() -> None:
    delegate = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(ScopeFilteringExporter(delegate)))
    with provider.get_tracer("mcp-python-sdk").start_as_current_span(SECRET):
        pass
    with provider.get_tracer("benchbox.mcp").start_as_current_span("tools/list"):
        pass
    provider.force_flush()

    spans = delegate.get_finished_spans()
    assert [span.instrumentation_scope.name for span in spans] == ["benchbox.mcp"]
    assert SECRET not in repr(spans)
