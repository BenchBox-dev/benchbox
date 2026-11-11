from __future__ import annotations

import json
from pathlib import Path

import anyio
import pytest
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult, TextContent

from benchbox.mcp.security import (
    READ_ONLY_TOOLS,
    AdmissionLease,
    AdmissionLimits,
    DurableSecurityStore,
    Principal,
    RemoteSecurityMiddleware,
    TenantWorkspaceProvider,
    load_remote_security_config,
)
from tests.integration.mcp._security import write_security_config

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_inline_chart_generation_is_read_only() -> None:
    assert "generate_chart" in READ_ONLY_TOOLS


def test_auth_config_contains_only_digests_and_verifies_identity(tmp_path: Path) -> None:
    token = "unit-secret-token"
    path = write_security_config(tmp_path, tokens={token: ("tenant-a", ("benchbox:read",))})
    assert token not in path.read_text(encoding="utf-8")
    config = load_remote_security_config(path)

    async def exercise() -> None:
        from benchbox.mcp.security import DigestTokenVerifier

        verifier = DigestTokenVerifier(config)
        access = await verifier.verify_token(token)
        assert access is not None
        assert access.subject == "tenant-a"
        assert await verifier.verify_token("wrong") is None

    anyio.run(exercise)


def test_tenant_workspace_is_server_owned_and_distinct(tmp_path: Path) -> None:
    provider = TenantWorkspaceProvider(tmp_path / "owned")
    first = Principal("a" * 32, "client", "issuer", "a", frozenset())
    second = Principal("b" * 32, "client", "issuer", "b", frozenset())
    first_paths = provider.paths_for(first)
    second_paths = provider.paths_for(second)
    assert first_paths.results_dir != second_paths.results_dir
    assert first_paths.results_dir.is_relative_to(tmp_path / "owned")
    assert second_paths.charts_dir.is_relative_to(tmp_path / "owned")


def test_authorization_rejects_platform_benchmark_and_scale_outside_policy(tmp_path: Path) -> None:
    path = write_security_config(
        tmp_path,
        tokens={"token": ("tenant-a", ("benchbox:read", "benchbox:execute"))},
    )
    config = load_remote_security_config(path)
    middleware = RemoteSecurityMiddleware(config, DurableSecurityStore(config.state_db, config.admission))
    principal = Principal("a" * 32, "client", "issuer", "a", frozenset({"benchbox:execute"}))
    middleware._authorize(principal, "run_benchmark", {"platform": "duckdb", "benchmark": "tpch", "scale_factor": 1})
    with pytest.raises(MCPError, match="Platform"):
        middleware._authorize(
            principal, "run_benchmark", {"platform": "snowflake", "benchmark": "tpch", "scale_factor": 1}
        )
    with pytest.raises(MCPError, match="Benchmark"):
        middleware._authorize(
            principal, "run_benchmark", {"platform": "duckdb", "benchmark": "tpcds", "scale_factor": 1}
        )
    with pytest.raises(MCPError, match="scale"):
        middleware._authorize(
            principal, "run_benchmark", {"platform": "duckdb", "benchmark": "tpch", "scale_factor": 2}
        )
    with pytest.raises(MCPError, match="finite"):
        middleware._authorize(
            principal, "run_benchmark", {"platform": "duckdb", "benchmark": "tpch", "scale_factor": "nan"}
        )


@pytest.mark.parametrize("field", ["allowed_platforms", "allowed_benchmarks"])
def test_empty_remote_execution_allowlists_deny_all(tmp_path: Path, field: str) -> None:
    path = write_security_config(tmp_path, tokens={"token": ("tenant-a", ("benchbox:execute",))})
    policy = json.loads(path.read_text(encoding="utf-8"))
    policy[field] = []
    path.write_text(json.dumps(policy), encoding="utf-8")
    config = load_remote_security_config(path)
    middleware = RemoteSecurityMiddleware(config, DurableSecurityStore(config.state_db, config.admission))
    principal = Principal("a" * 32, "client", "issuer", "a", frozenset({"benchbox:execute"}))

    with pytest.raises(MCPError, match="authorized"):
        middleware._authorize(
            principal, "run_benchmark", {"platform": "duckdb", "benchmark": "tpch", "scale_factor": 0.01}
        )


def test_durable_admission_is_shared_between_store_instances(tmp_path: Path) -> None:
    limits = AdmissionLimits(global_concurrency=1, principal_concurrency=1, queue_limit=0)
    first = DurableSecurityStore(tmp_path / "state.sqlite3", limits)
    second = DurableSecurityStore(tmp_path / "state.sqlite3", limits)

    async def exercise() -> None:
        lease = await first.admit("principal-a")
        with pytest.raises(MCPError, match="queue"):
            await second.admit("principal-b")
        first.release(lease)
        second_lease = await second.admit("principal-b")
        second.release(second_lease)

    anyio.run(exercise)


def test_admission_promotes_oldest_waiter_with_principal_capacity(tmp_path: Path) -> None:
    limits = AdmissionLimits(global_concurrency=2, principal_concurrency=1, queue_limit=2)
    store = DurableSecurityStore(tmp_path / "state.sqlite3", limits)
    active_a = store._enqueue("principal-a")
    active_c = store._enqueue("principal-c")
    waiting_a = store._enqueue("principal-a")
    waiting_b = store._enqueue("principal-b")

    store.release(AdmissionLease(active_c, "principal-c"))

    assert not store._try_promote(waiting_a, "principal-a")
    assert store._try_promote(waiting_b, "principal-b")

    store.release(AdmissionLease(active_a, "principal-a"))


def test_admission_state_resets_after_os_restart(monkeypatch, tmp_path: Path) -> None:
    from benchbox.mcp import security

    monkeypatch.setattr(security, "_boot_identity", lambda: "boot-a")
    first = DurableSecurityStore(tmp_path / "state.sqlite3", AdmissionLimits())
    first._enqueue("principal-a")

    monkeypatch.setattr(security, "_boot_identity", lambda: "boot-b")
    restarted = DurableSecurityStore(tmp_path / "state.sqlite3", AdmissionLimits())
    with restarted._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM mcp_admission_tickets").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM mcp_rate_windows").fetchone()[0] == 0


def test_concurrent_activity_during_store_startup_is_preserved(monkeypatch, tmp_path: Path) -> None:
    from benchbox.mcp import security

    monkeypatch.setattr(security, "_boot_identity", lambda: "same-boot")
    first = DurableSecurityStore(tmp_path / "state.sqlite3", AdmissionLimits())
    ticket_id = first._enqueue("principal-a")

    monkeypatch.setattr(security, "mono_time", lambda: 1.0)
    DurableSecurityStore(tmp_path / "state.sqlite3", AdmissionLimits())

    with first._connect() as connection:
        assert (
            connection.execute(
                "SELECT ticket_id FROM mcp_admission_tickets WHERE ticket_id = ?", (ticket_id,)
            ).fetchone()
            is not None
        )


def test_lost_admission_lease_cancels_running_request(tmp_path: Path) -> None:
    config = load_remote_security_config(
        write_security_config(tmp_path, tokens={"token": ("tenant-a", ("benchbox:read",))})
    )

    class LostLeaseStore:
        limits = AdmissionLimits(lease_seconds=0.003)

        def __init__(self) -> None:
            self.released = False

        async def admit(self, principal_id: str) -> AdmissionLease:
            return AdmissionLease("ticket", principal_id)

        def renew(self, lease: AdmissionLease) -> bool:
            return False

        def release(self, lease: AdmissionLease) -> None:
            self.released = True

        def audit(self, **_kwargs: object) -> None:
            return None

    async def exercise() -> None:
        store = LostLeaseStore()
        middleware = RemoteSecurityMiddleware(config, store)
        principal = Principal("a" * 32, "client", "issuer", "a", frozenset({"benchbox:read"}))

        async def call_next(_ctx: object) -> object:
            await anyio.sleep_forever()

        with pytest.raises(MCPError, match="lease was lost"):
            await middleware._call_admitted(principal, "tools/list", object(), call_next)
        assert store.released

    anyio.run(exercise)


def test_audit_schema_excludes_secrets_arguments_and_database_urls(tmp_path: Path) -> None:
    store = DurableSecurityStore(tmp_path / "state.sqlite3", AdmissionLimits())
    store.audit(
        principal_id="opaque-principal",
        tool_name="run_benchmark",
        decision="allowed",
        execution_id="execution-1",
        artifact_ref="/server/owned/result.json",
    )
    serialized = repr(store.audit_events())
    assert "opaque-principal" in serialized
    assert "result.json" in serialized
    assert "server/owned" not in serialized
    assert "token" not in serialized.lower()


def test_audit_reference_extraction_is_bounded_to_execution_and_basename() -> None:
    result = CallToolResult(
        content=[
            TextContent(
                type="text",
                text='{"mcp_metadata":{"execution_id":"execution-1","result_file":"/owned/result.json"}}',
            )
        ]
    )
    assert RemoteSecurityMiddleware._result_refs(result) == ("execution-1", "/owned/result.json")


