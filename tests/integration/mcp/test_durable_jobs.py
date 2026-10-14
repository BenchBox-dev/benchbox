from __future__ import annotations

import json
import multiprocessing
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import anyio
import pytest
from mcp.shared.exceptions import MCPError
from mcp.types import TextContent

from benchbox.mcp.jobs import DurableJobRepository, DurableJobWorker
from benchbox.mcp.schemas import (
    MCP_CLICKHOUSE_PROFILE_ENV,
    MCP_DASK_MAX_TOTAL_THREADS_ENV,
    MCPValidationError,
    validate_platform_options,
)
from benchbox.mcp.security import JobLimits, TenantWorkspaceProvider
from benchbox.utils.clock import mono_time
from tests.integration.mcp._security import authenticated_http_client, write_security_config

pytestmark = [pytest.mark.integration, pytest.mark.fast]


def _request() -> dict[str, object]:
    return {"platform": "duckdb", "benchmark": "tpch", "scale_factor": 0.01}


def _executor(job, staging: Path) -> dict[str, object]:
    result = staging / "benchmark.json"
    result.write_text(json.dumps({"execution_id": job.execution_id}), encoding="utf-8")
    return {"mcp_metadata": {"execution_id": job.execution_id, "result_file": str(result)}}


def test_two_worker_submission_observation_and_completion(tmp_path: Path) -> None:
    limits = JobLimits()
    first_repository = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    second_repository = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    workspaces = TenantWorkspaceProvider(tmp_path / "workspaces")
    second_worker = DurableJobWorker(second_repository, workspaces, executor=_executor, worker_id="worker-b")
    submitted, _ = first_repository.submit("tenant-a", _request())

    claimed = second_repository.claim(second_worker.worker_id)
    assert claimed is not None
    anyio.run(second_worker._run_job, claimed)

    observed = first_repository.get_owned(submitted.execution_id, "tenant-a")
    assert observed is not None and observed.state == "completed"
    assert observed.artifact_path is not None and Path(observed.artifact_path).is_file()


def test_restart_records_unknown_instead_of_rerunning_lost_lease(tmp_path: Path) -> None:
    limits = JobLimits(lease_seconds=0.05, poll_seconds=0.01, max_attempts=2)
    first_repository = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    submitted, _ = first_repository.submit("tenant-a", _request())
    assert first_repository.claim("terminated-worker") is not None

    anyio.run(anyio.sleep, 0.06)
    restarted_repository = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    restarted_worker = DurableJobWorker(
        restarted_repository,
        TenantWorkspaceProvider(tmp_path / "workspaces"),
        executor=_executor,
        worker_id="restarted-worker",
    )
    restarted_worker.recover_expired()
    anyio.run(anyio.sleep, 0.06)
    restarted_worker.recover_expired()
    unknown = first_repository.get(submitted.execution_id)
    assert unknown is not None and unknown.state == "unknown"
    assert restarted_repository.claim(restarted_worker.worker_id) is None

    resubmitted, created = first_repository.submit("tenant-a", _request(), idempotency_key="restart-after-unknown")
    assert created is True
    claimed = restarted_repository.claim(restarted_worker.worker_id)
    assert claimed is not None and claimed.execution_id == resubmitted.execution_id
    anyio.run(restarted_worker._run_job, claimed)

    completed = first_repository.get(resubmitted.execution_id)
    assert completed is not None and completed.state == "completed"
    assert completed.attempts == 1


def test_tenant_cannot_observe_or_cancel_another_job(tmp_path: Path) -> None:
    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits())
    submitted, _ = repository.submit("tenant-a", _request())

    assert repository.get_owned(submitted.execution_id, "tenant-b") is None
    assert repository.cancel(submitted.execution_id, "tenant-b") is None
    assert repository.get_owned(submitted.execution_id, "tenant-a") is not None


def test_normalized_platform_options_survive_repository_round_trip(tmp_path: Path) -> None:
    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits())
    options = validate_platform_options("duckdb", {"threads": 4})

    submitted, created = repository.submit("tenant-a", {**_request(), "platform_options": options})

    assert created is True
    persisted = repository.get_owned(submitted.execution_id, "tenant-a")
    assert persisted is not None
    assert persisted.request["platform_options"] == {"threads": 4}


def test_durable_replay_applies_duckdb_threads_to_the_real_adapter(tmp_path: Path) -> None:
    from benchbox.core.run_service import translate_platform_options_for_adapter as _prepare_adapter_platform_options
    from benchbox.platforms.duckdb import DuckDBAdapter

    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits())
    options = validate_platform_options("duckdb", {"threads": 5})
    submitted, _ = repository.submit("tenant-a", {**_request(), "platform_options": options})

    persisted = repository.get_owned(submitted.execution_id, "tenant-a")
    assert persisted is not None
    assert persisted.request["platform_options"] == {"threads": 5}

    prepared = _prepare_adapter_platform_options("duckdb", persisted.request["platform_options"])
    adapter = DuckDBAdapter.from_config(
        {
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "database_path": str(tmp_path / "replay.duckdb"),
            **prepared,
        }
    )

    assert adapter.thread_limit == 5


def test_durable_admission_refuses_contradictory_databricks_clustering(tmp_path: Path) -> None:
    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits())

    with pytest.raises(MCPValidationError, match="clustering options conflict"):
        validate_platform_options(
            "databricks",
            {"databricks_clustering_strategy": "z_order", "liquid_clustering_columns": "a,b"},
        )

    with repository._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM mcp_benchmark_jobs").fetchone()[0] == 0


def test_durable_databricks_requests_persist_only_normalized_intent(tmp_path: Path) -> None:
    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits())
    options = validate_platform_options(
        "databricks",
        {"databricks_clustering_strategy": "liquid_clustering", "liquid_clustering_columns": "a,b"},
    )

    submitted, _ = repository.submit("tenant-a", {**_request(), "platform": "databricks", "platform_options": options})

    persisted = repository.get_owned(submitted.execution_id, "tenant-a")
    assert persisted is not None
    assert persisted.request["platform_options"] == {
        "databricks_clustering_strategy": "liquid_clustering",
        "liquid_clustering_columns": "a,b",
    }
    assert validate_platform_options("databricks", persisted.request["platform_options"]) == options


def test_durable_admission_refuses_an_oversized_dask_envelope(tmp_path: Path) -> None:
    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits())

    with pytest.raises(MCPValidationError, match="thread budget"):
        validate_platform_options("dask", {"n_workers": 16, "threads_per_worker": 256})

    with repository._connect() as connection:
        queued = connection.execute("SELECT COUNT(*) FROM mcp_benchmark_jobs").fetchone()[0]
    assert queued == 0


def test_durable_worker_replay_re_enforces_the_dask_envelope(tmp_path: Path, monkeypatch) -> None:
    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits())
    options = validate_platform_options("dask", {"n_workers": 8, "threads_per_worker": 4})
    submitted, _ = repository.submit(
        "tenant-a", {**_request(), "platform": "dask-df", "mode": "dataframe", "platform_options": options}
    )

    monkeypatch.setenv(MCP_DASK_MAX_TOTAL_THREADS_ENV, "8")

    claimed = repository.claim("worker-a")
    assert claimed is not None
    with patch("benchbox.mcp.tools.benchmark._get_platform_adapter") as get_adapter:
        response = DurableJobWorker._execute_benchmark(claimed, tmp_path / "staging")

    assert response["status"] == "failed"
    get_adapter.assert_not_called()
    assert repository.get(submitted.execution_id) is not None


def test_durable_clickhouse_requests_never_persist_a_connection_tuple(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(MCP_CLICKHOUSE_PROFILE_ENV, json.dumps({"reviewed": {"port": 9440, "secure": True}}))
    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits())
    options = validate_platform_options("clickhouse-server", {"connection_profile": "reviewed"})

    submitted, _ = repository.submit(
        "tenant-a", {**_request(), "platform": "clickhouse-server", "platform_options": options}
    )

    persisted = repository.get_owned(submitted.execution_id, "tenant-a")
    assert persisted is not None
    assert persisted.request["platform_options"] == {"connection_profile": "reviewed"}
    assert "port" not in persisted.request["platform_options"]
    assert "secure" not in persisted.request["platform_options"]


@pytest.mark.parametrize("platform", ["clickhouse", "clickhouse-server"])
def test_durable_admission_refuses_clickhouse_port_and_tls_overrides(platform: str) -> None:
    for options in ({"port": 9001}, {"secure": False}):
        with pytest.raises(MCPValidationError, match="not authorized"):
            validate_platform_options(platform, options)


def test_tenant_job_tools_are_remote_only_and_cross_tenant_fail_closed(tmp_path: Path, monkeypatch) -> None:
    token_a = "tenant-a-job-token"
    token_b = "tenant-b-job-token"
    config = write_security_config(
        tmp_path,
        tokens={
            token_a: ("tenant-a", ("benchbox:read", "benchbox:execute")),
            token_b: ("tenant-b", ("benchbox:read", "benchbox:execute")),
        },
    )
    monkeypatch.setattr(DurableJobWorker, "_execute_benchmark", staticmethod(_executor))

    async def exercise() -> None:
        async with authenticated_http_client(config, token_a) as (client_a, _):
            tools = await client_a.list_tools()
            names = {tool.name for tool in tools.tools}
            assert {"start_benchmark", "get_benchmark_status", "get_benchmark_result", "cancel_benchmark"} <= names
            started = await client_a.call_tool(
                "start_benchmark",
                {
                    "platform": "duckdb",
                    "benchmark": "tpch",
                    "scale_factor": 0.01,
                    "platform_options": {"threads": 2},
                    "idempotency_key": "acceptance-1",
                },
            )
            assert not started.is_error
            assert isinstance(started.content[0], TextContent)
            execution_id = json.loads(started.content[0].text)["execution_id"]

            for _ in range(100):
                current = await client_a.call_tool("get_benchmark_status", {"execution_id": execution_id})
                assert isinstance(current.content[0], TextContent)
                payload = json.loads(current.content[0].text)
                if payload["status"] in ("failed", "cancelled"):
                    pytest.fail(
                        f"durable benchmark terminally {payload['status']} (error_code={payload.get('error_code')!r})"
                    )
                if payload["status"] == "completed":
                    break
                await anyio.sleep(0.01)
            else:
                pytest.fail("durable benchmark did not complete")
            result = await client_a.call_tool("get_benchmark_result", {"execution_id": execution_id})
            assert not result.is_error
            assert execution_id in result.content[0].text
            synchronous = await client_a.call_tool(
                "run_benchmark", {"platform": "duckdb", "benchmark": "tpch", "scale_factor": 0.01}
            )
            assert "requires start_benchmark" in synchronous.content[0].text

            with pytest.raises(MCPError, match="not authorized") as rejected:
                await client_a.call_tool(
                    "start_benchmark",
                    {
                        "platform": "duckdb",
                        "benchmark": "tpch",
                        "platform_options": {"password": "SECRET_SENTINEL"},
                    },
                )
            assert "SECRET_SENTINEL" not in str(rejected.value)

        async with authenticated_http_client(config, token_b) as (client_b, _):
            for tool_name in ("get_benchmark_status", "cancel_benchmark", "get_benchmark_result"):
                with pytest.raises(MCPError, match="not found"):
                    await client_b.call_tool(tool_name, {"execution_id": execution_id})

    anyio.run(exercise)


def test_start_and_status_report_deprecations_and_accept_deployment_selectors(tmp_path: Path, monkeypatch) -> None:
    token = "tenant-deprecation-token"
    config = write_security_config(tmp_path, tokens={token: ("tenant-a", ("benchbox:read", "benchbox:execute"))})
    policy = json.loads(config.read_text(encoding="utf-8"))
    policy["allowed_platforms"] = ["duckdb", "polars-df", "clickhouse:server"]
    config.write_text(json.dumps(policy), encoding="utf-8")
    monkeypatch.setattr(DurableJobWorker, "_execute_benchmark", staticmethod(_executor))

    async def start(client, **arguments) -> dict[str, object]:
        result = await client.call_tool("start_benchmark", {"benchmark": "tpch", "scale_factor": 0.01, **arguments})
        assert not result.is_error
        assert isinstance(result.content[0], TextContent)
        return json.loads(result.content[0].text)

    async def status(client, execution_id: str) -> dict[str, object]:
        result = await client.call_tool("get_benchmark_status", {"execution_id": execution_id})
        assert isinstance(result.content[0], TextContent)
        return json.loads(result.content[0].text)

    async def exercise() -> None:
        async with authenticated_http_client(config, token) as (client, _):
            deprecated = await start(client, platform="polars-df", platform_options={"streaming": True})
            expected = [
                {
                    "option": "streaming",
                    "replacement": "execution_engine='streaming'",
                    "removed_in": "0.6.0",
                    "message": (
                        "Platform option 'streaming' for 'polars' is deprecated and will be removed in "
                        "BenchBox 0.6.0; use execution_engine='streaming' instead."
                    ),
                }
            ]
            assert deprecated["deprecations"] == expected
            observed = await status(client, str(deprecated["execution_id"]))
            assert observed["deprecations"] == expected

            plain = await start(client, platform="duckdb")
            assert plain["deprecations"] == []
            assert (await status(client, str(plain["execution_id"])))["deprecations"] == []

            selected = await start(client, platform="clickhouse:server")
            assert selected["deprecations"] == []
            persisted = DurableJobRepository(tmp_path / "security.sqlite3", JobLimits()).get(
                str(selected["execution_id"])
            )
            assert persisted is not None and persisted.request["platform"] == "clickhouse:server"

            with pytest.raises(MCPError, match="not authorized"):
                await start(client, platform="duckdb", platform_options={"mode": "server"})

    anyio.run(exercise)


def test_worker_response_carries_the_deprecations_of_the_run(monkeypatch) -> None:
    from benchbox.core.deprecation_notices import warn_deprecated_alias

    def request_run(job, staging: Path) -> dict[str, object]:
        warn_deprecated_alias("polars", "streaming", "'--execution-engine streaming' (execution_engine='streaming')")
        return {"mcp_metadata": {}}

    monkeypatch.setattr(DurableJobWorker, "_execute_benchmark_request", staticmethod(request_run))

    response = DurableJobWorker._execute_benchmark(SimpleNamespace(), Path("."))

    assert [entry["replacement"] for entry in response["deprecations"]] == ["execution_engine='streaming'"]


def test_no_overadmission_after_lease_loss_until_confirmed_termination(tmp_path: Path, monkeypatch) -> None:
    limits = JobLimits(lease_seconds=0.05, poll_seconds=0.01, max_attempts=2, max_running=1)
    repository = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    workspaces = TenantWorkspaceProvider(tmp_path / "workspaces")
    entered = threading.Event()
    release = threading.Event()

    def blocking_executor(job, staging: Path):
        entered.set()
        assert release.wait(timeout=10)
        result = staging / "benchmark.json"
        result.write_text(json.dumps({"execution_id": job.execution_id}), encoding="utf-8")
        return {"mcp_metadata": {"execution_id": job.execution_id, "result_file": str(result)}}

    first, _ = repository.submit("tenant-a", _request())
    repository.submit("tenant-b", _request())
    worker = DurableJobWorker(repository, workspaces, executor=blocking_executor, worker_id="worker-a")
    claimed = repository.claim("worker-a")
    assert claimed is not None
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(anyio.run, worker._run_job, claimed)
        assert entered.wait(timeout=10)
        monkeypatch.setattr(repository, "renew", lambda execution_id, worker_id: False)
        cancelled = repository.cancel(first.execution_id, "tenant-a")
        assert cancelled is not None and cancelled[1] == "requested"
        anyio.run(anyio.sleep, 0.08)
        with repository._connect() as connection:
            connection.execute(
                "UPDATE mcp_benchmark_jobs SET lease_expires_at = ? WHERE execution_id = ?",
                (time.time() - 1.0, first.execution_id),
            )
        worker.recover_expired()
        anyio.run(anyio.sleep, 0.06)
        worker.recover_expired()
        lost = repository.get(first.execution_id)
        assert lost is not None and lost.state == "unknown"
        assert lost.error_code == "cancellation_unconfirmed"
        assert repository.claim("worker-b") is None
        release.set()
        running.result(timeout=30)

    quiesced = repository.get(first.execution_id)
    assert quiesced is not None and quiesced.quiesced_at is not None
    assert repository.capacity_summary()["outstanding"] == 0

    with repository._connect() as connection:
        connection.execute(
            "UPDATE mcp_benchmark_jobs SET completed_at = ? WHERE execution_id = ?",
            ("2000-01-01T00:00:00+00:00", first.execution_id),
        )
    worker.purge_expired()
    assert repository.get(first.execution_id) is None
    rerun = DurableJobWorker(repository, workspaces, executor=_executor, worker_id="worker-b")
    claimed = repository.claim("worker-b")
    assert claimed is not None
    anyio.run(rerun._run_job, claimed)
    completed = repository.get(claimed.execution_id)
    assert completed is not None and completed.state == "completed"


def _spawn_claim(db_path: str, worker_id: str, outcome: multiprocessing.Queue) -> None:
    from benchbox.mcp.jobs import DurableJobRepository
    from benchbox.mcp.security import JobLimits

    repository = DurableJobRepository(Path(db_path), JobLimits(max_running=1))
    outcome.put(repository.claim(worker_id) is not None)


def test_multiprocess_claims_respect_global_running_limit(tmp_path: Path) -> None:
    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits(max_running=1))
    repository.submit("tenant-a", _request())
    repository.submit("tenant-b", _request())
    context = multiprocessing.get_context("spawn")
    outcomes: multiprocessing.Queue = context.Queue()
    processes = [
        context.Process(target=_spawn_claim, args=(str(tmp_path / "state.sqlite3"), worker, outcomes))
        for worker in ("proc-a", "proc-b")
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=60)
        assert process.exitcode == 0
    assert sorted(outcomes.get(timeout=10) for _ in processes) == [False, True]


def _spawn_per_principal_claim(db_path: str, worker_id: str, outcome: multiprocessing.Queue) -> None:
    from benchbox.mcp.jobs import DurableJobRepository
    from benchbox.mcp.security import JobLimits

    repository = DurableJobRepository(Path(db_path), JobLimits(max_running=8, max_running_per_principal=1))
    outcome.put(repository.claim(worker_id) is not None)


def test_multiprocess_claims_respect_per_principal_running_limit(tmp_path: Path) -> None:
    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits(max_running=8, max_running_per_principal=1))
    repository.submit("tenant-a", _request())
    repository.submit("tenant-a", _request())
    context = multiprocessing.get_context("spawn")
    outcomes: multiprocessing.Queue = context.Queue()
    processes = [
        context.Process(target=_spawn_per_principal_claim, args=(str(tmp_path / "state.sqlite3"), worker, outcomes))
        for worker in ("proc-a", "proc-b")
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=60)
        assert process.exitcode == 0
    assert sorted(outcomes.get(timeout=10) for _ in processes) == [False, True]


def test_fairness_state_is_shared_across_worker_handles(tmp_path: Path) -> None:
    first = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits())
    second = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits())
    first.submit("tenant-a", _request())
    first.submit("tenant-a", _request())
    second.submit("tenant-b", _request())
    assert second.claim("worker-x") is not None
    served = second.claim("worker-y")
    assert served is not None and served.principal_id == "tenant-b"


async def _recover_until_unknown(
    worker: DurableJobWorker,
    repository: DurableJobRepository,
    execution_id: str,
    *,
    timeout_seconds: float = 5.0,
    interval_seconds: float = 0.01,
):
    deadline = mono_time() + timeout_seconds
    job = repository.get(execution_id)
    while job is None or job.state != "unknown":
        if mono_time() >= deadline:
            state = None if job is None else job.state
            pytest.fail(f"job {execution_id} still {state!r} {timeout_seconds}s after its lease should have expired")
        await anyio.sleep(interval_seconds)
        worker.recover_expired()
        job = repository.get(execution_id)
    return job


def test_benchmark_capacity_tool_is_tenant_scoped(tmp_path: Path) -> None:
    token_a = "tenant-a-capacity-token"
    token_b = "tenant-b-capacity-token"
    config = write_security_config(
        tmp_path,
        tokens={
            token_a: ("tenant-a", ("benchbox:read", "benchbox:execute")),
            token_b: ("tenant-b", ("benchbox:read",)),
        },
        jobs={"poll_seconds": 30, "lease_seconds": 3600},
    )
    state_db = tmp_path / "security.sqlite3"
    direct = DurableJobRepository(state_db, JobLimits(lease_seconds=0.05, poll_seconds=0.01, max_attempts=2))

    async def exercise() -> None:
        async with authenticated_http_client(config, token_a) as (client_a, _):
            tools = {tool.name for tool in (await client_a.list_tools()).tools}
            assert "get_benchmark_capacity" in tools
            started = json.loads(
                (
                    await client_a.call_tool(
                        "start_benchmark",
                        {"platform": "duckdb", "benchmark": "tpch", "scale_factor": 0.01},
                    )
                )
                .content[0]
                .text
            )
            lost_execution_id = started["execution_id"]
            assert direct.claim("lost-worker") is not None
            fence = DurableJobWorker(direct, TenantWorkspaceProvider(tmp_path / "fence-workspaces"))
            fence.recover_expired()
            lost = await _recover_until_unknown(fence, direct, lost_execution_id)
            assert lost is not None and lost.state == "unknown"
            capacity = json.loads((await client_a.call_tool("get_benchmark_capacity", {})).content[0].text)
            assert capacity["states"]["unknown"] == 1
            assert capacity["outstanding"] == 1
            assert capacity["owned"] == {"queued": 0, "outstanding": 1}
            assert [entry["execution_id"] for entry in capacity["quarantined"]] == [lost_execution_id]
            assert all("principal_id" not in entry for entry in capacity["quarantined"])
        async with authenticated_http_client(config, token_b) as (client_b, _):
            other = json.loads((await client_b.call_tool("get_benchmark_capacity", {})).content[0].text)
            assert other["owned"] == {"queued": 0, "outstanding": 0}
            assert other["quarantined"] == []
            assert "per_principal" not in other
            assert "tenant-a" not in json.dumps(other)

    anyio.run(exercise)
