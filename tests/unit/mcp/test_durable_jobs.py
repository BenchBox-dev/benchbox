from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anyio
import pytest
from mcp.shared.exceptions import MCPError

from benchbox.mcp.jobs import DurableJobRepository, DurableJobWorker, _response_has_outstanding_work
from benchbox.mcp.security import JobLimits, TenantWorkspaceProvider

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _request(scale: float = 0.01) -> dict[str, object]:
    return {"platform": "duckdb", "benchmark": "tpch", "scale_factor": scale}


def test_job_order_uses_sqlite_compatibility_statements(tmp_path: Path) -> None:
    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits())
    statements: list[str] = []
    with repository._connect() as connection:
        connection.set_trace_callback(statements.append)
        connection.execute("BEGIN IMMEDIATE")
        assert repository._next_order(connection, "enqueue") == 1
        assert repository._next_order(connection, "enqueue") == 2
        connection.commit()

    assert not any("RETURNING" in statement.upper() for statement in statements)


def test_job_submission_is_tenant_owned_and_idempotent(tmp_path: Path) -> None:
    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits())
    first, created = repository.submit("tenant-a", _request(), idempotency_key="request-1")
    duplicate, duplicate_created = repository.submit("tenant-a", _request(), idempotency_key="request-1")

    assert created is True
    assert duplicate_created is False
    assert duplicate.execution_id == first.execution_id
    assert repository.get_owned(first.execution_id, "tenant-a") == first
    assert repository.get_owned(first.execution_id, "tenant-b") is None
    with pytest.raises(MCPError, match="different request"):
        repository.submit("tenant-a", _request(0.1), idempotency_key="request-1")
    with pytest.raises(MCPError, match="1 to 200"):
        repository.submit("tenant-a", _request(), idempotency_key="")


def test_job_queue_is_bounded_across_repository_instances(tmp_path: Path) -> None:
    limits = JobLimits(queue_limit=1)
    first = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    second = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    first.submit("tenant-a", _request())

    with pytest.raises(MCPError, match="queue"):
        second.submit("tenant-b", _request())


def test_job_claim_lease_retry_and_terminal_failure(tmp_path: Path) -> None:
    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits(max_attempts=2))
    submitted, _ = repository.submit("tenant-a", _request())

    first = repository.claim("worker-a")
    assert first is not None
    assert first.state == "running"
    assert first.attempts == 1
    assert repository.claim("worker-b") is None
    assert repository.renew(first.execution_id, "worker-b") is False
    assert repository.fail_attempt(first.execution_id, "worker-a", "transient") == "queued"

    second = repository.claim("worker-b")
    assert second is not None
    assert second.execution_id == submitted.execution_id
    assert second.attempts == 2
    assert repository.fail_attempt(second.execution_id, "worker-b", "transient") == "failed"


def test_cancel_is_idempotent_and_fences_running_publication(tmp_path: Path) -> None:
    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits())
    queued, _ = repository.submit("tenant-a", _request())
    cancelled = repository.cancel(queued.execution_id, "tenant-a")
    repeated = repository.cancel(queued.execution_id, "tenant-a")
    assert cancelled is not None and cancelled[0].state == "cancelled" and cancelled[1] == "accepted"
    assert repeated is not None and repeated[0].state == "cancelled" and repeated[1] == "too_late"

    running, _ = repository.submit("tenant-a", _request())
    claimed = repository.claim("worker-a")
    assert claimed is not None and claimed.execution_id == running.execution_id
    requested = repository.cancel(running.execution_id, "tenant-a")
    assert requested is not None and requested[0].cancel_requested and requested[1] == "requested"
    assert repository.begin_publication(running.execution_id, "worker-a") is False
    assert repository.fail_attempt(running.execution_id, "worker-a", "cancelled") == "cancelled"


def test_cancel_after_publication_commit_point_does_not_revoke_result(tmp_path: Path) -> None:
    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits())
    submitted, _ = repository.submit("tenant-a", _request())
    claimed = repository.claim("worker-a")
    assert claimed is not None
    assert repository.begin_publication(submitted.execution_id, "worker-a")

    unchanged = repository.cancel(submitted.execution_id, "tenant-a")
    assert unchanged is not None and unchanged[0].state == "publishing" and unchanged[1] == "too_late"
    assert unchanged[0].cancel_requested is False
    artifact = tmp_path / "response.json"
    artifact.write_text("{}", encoding="utf-8")
    assert repository.complete(submitted.execution_id, "worker-a", artifact)

    completed = repository.cancel(submitted.execution_id, "tenant-a")
    assert completed is not None and completed[0].state == "completed" and completed[1] == "too_late"


def test_completed_job_is_recorded_only_after_atomic_artifact_publication(tmp_path: Path) -> None:
    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits())
    workspaces = TenantWorkspaceProvider(tmp_path / "workspaces")

    def executor(job, staging: Path):
        result = staging / "benchmark.json"
        result.write_text('{"ok": true}', encoding="utf-8")
        return {"mcp_metadata": {"execution_id": job.execution_id, "result_file": str(result)}}

    worker = DurableJobWorker(repository, workspaces, executor=executor, worker_id="worker-a")
    submitted, _ = repository.submit("tenant-a", _request())
    claimed = repository.claim(worker.worker_id)
    assert claimed is not None

    anyio.run(worker._run_job, claimed)

    completed = repository.get(submitted.execution_id)
    assert completed is not None and completed.state == "completed"
    assert completed.artifact_path is not None
    response_path = Path(completed.artifact_path)
    assert response_path.is_file()
    payload = json.loads(response_path.read_text(encoding="utf-8"))
    result_file = Path(payload["mcp_metadata"]["result_file"])
    assert result_file.is_file()
    assert ".staging" not in str(result_file)


def test_heartbeat_renews_lease_through_slow_artifact_flush(monkeypatch, tmp_path: Path) -> None:
    limits = JobLimits(lease_seconds=0.06, poll_seconds=0.01)
    repository = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    workspaces = TenantWorkspaceProvider(tmp_path / "workspaces")

    def executor(job, staging: Path):
        result = staging / "benchmark.json"
        result.write_text("{}", encoding="utf-8")
        return {"mcp_metadata": {"execution_id": job.execution_id, "result_file": str(result)}}

    worker = DurableJobWorker(repository, workspaces, executor=executor, worker_id="worker-a")
    original_sync_tree = worker._sync_tree

    def slow_sync_tree(root: Path) -> None:
        time.sleep(0.14)
        original_sync_tree(root)

    monkeypatch.setattr(worker, "_sync_tree", slow_sync_tree)
    submitted, _ = repository.submit("tenant-a", _request())
    claimed = repository.claim(worker.worker_id)
    assert claimed is not None

    anyio.run(worker._run_job, claimed)

    completed = repository.get(submitted.execution_id)
    assert completed is not None and completed.state == "completed"


def test_data_only_artifact_path_is_rewritten_into_final_tenant_job(tmp_path: Path) -> None:
    staging = tmp_path / ".staging" / "attempt"
    final_dir = tmp_path / "job"
    response = {"data_generation": {"data_path": str(staging / "generated_data")}}

    rewritten = DurableJobWorker._rewrite_result_paths(response, staging, final_dir)

    assert rewritten["data_generation"]["data_path"] == str(final_dir / "generated_data")


def test_expired_lease_recovery_records_unknown_instead_of_requeueing(tmp_path: Path) -> None:
    limits = JobLimits(lease_seconds=0.05, poll_seconds=0.01, max_attempts=2)
    repository = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    workspaces = TenantWorkspaceProvider(tmp_path / "workspaces")
    worker = DurableJobWorker(repository, workspaces, worker_id="worker-b")
    submitted, _ = repository.submit("tenant-a", _request())
    claimed = repository.claim("lost-worker")
    assert claimed is not None

    worker.recover_expired()
    anyio.run(anyio.sleep, 0.06)
    worker.recover_expired()
    recovered = repository.get(submitted.execution_id)
    assert recovered is not None and recovered.state == "unknown"
    assert recovered.error_code == "unknown_outcome"
    assert recovered.completed_at is not None
    assert repository.claim("worker-b") is None
    assert repository.fail_attempt(submitted.execution_id, "worker-b", "late-report") is None
    assert repository.begin_publication(submitted.execution_id, "lost-worker") is False
    assert repository.complete(submitted.execution_id, "lost-worker", tmp_path / "stale.json") is False
    resubmitted, created = repository.submit("tenant-a", _request(), idempotency_key="retry-after-unknown")
    assert created is True
    assert resubmitted.execution_id != submitted.execution_id
    assert repository.claim("worker-b") is not None


def test_expired_recovery_fences_worker_before_artifact_cleanup(tmp_path: Path) -> None:
    limits = JobLimits(lease_seconds=0.05, poll_seconds=0.01, max_attempts=2)
    repository = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    submitted, _ = repository.submit("tenant-a", _request())
    claimed = repository.claim("lost-worker")
    assert claimed is not None
    assert repository.begin_publication(claimed.execution_id, "lost-worker")

    repository.claim_expired("observer")
    anyio.run(anyio.sleep, 0.06)
    fenced = repository.claim_expired("recovery-owner")

    assert fenced is not None and fenced.lease_owner == "recovery-owner"
    assert repository.complete(submitted.execution_id, "lost-worker", tmp_path / "stale.json") is False


def test_publication_commit_excludes_recovery_fence(tmp_path: Path) -> None:
    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits(lease_seconds=0.08))
    submitted, _ = repository.submit("tenant-a", _request())
    claimed = repository.claim("worker-a")
    assert claimed is not None
    assert repository.begin_publication(claimed.execution_id, "worker-a")
    assert repository.claim_expired("observer") is None
    entered_publish = threading.Event()
    release_publish = threading.Event()

    def publish() -> None:
        entered_publish.set()
        assert release_publish.wait(timeout=2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        completion = executor.submit(
            repository.complete,
            submitted.execution_id,
            "worker-a",
            tmp_path / "response.json",
            publish=publish,
        )
        assert entered_publish.wait(timeout=2)
        time.sleep(0.09)
        recovery = executor.submit(repository.claim_expired, "recovery-owner")
        time.sleep(0.02)
        assert not recovery.done()
        release_publish.set()
        assert completion.result(timeout=2)
        assert recovery.result(timeout=2) is None


def test_recovery_leaves_fenced_attempt_staging_until_quiescence_and_purge(tmp_path: Path) -> None:
    limits = JobLimits(lease_seconds=0.05, poll_seconds=0.01, max_attempts=2, retention_seconds=3600)
    repository = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    worker = DurableJobWorker(repository, TenantWorkspaceProvider(tmp_path / "workspaces"), worker_id="recovery")
    submitted, _ = repository.submit("tenant-a", _request())
    claimed = repository.claim("lost-worker")
    assert claimed is not None
    staging, _, _ = worker._job_paths(claimed)
    staging.mkdir(parents=True)
    (staging / "partial.bin").write_bytes(b"live")

    worker.recover_expired()
    anyio.run(anyio.sleep, 0.06)
    worker.recover_expired()

    recovered = repository.get(submitted.execution_id)
    assert recovered is not None and recovered.state == "unknown"
    assert (staging / "partial.bin").read_bytes() == b"live"
    (staging / "later.bin").write_bytes(b"still writable")

    worker.purge_expired()
    assert staging.exists()

    assert repository.attest_quiescence(submitted.execution_id, "lost-worker") is True
    with repository._connect() as connection:
        connection.execute(
            "UPDATE mcp_benchmark_jobs SET completed_at = ? WHERE execution_id = ?",
            ("2000-01-01T00:00:00+00:00", submitted.execution_id),
        )
    worker.purge_expired()
    assert not staging.exists()
    assert repository.get(submitted.execution_id) is None


def test_job_leases_use_shared_generations_across_host_clock_offsets(monkeypatch, tmp_path: Path) -> None:
    from benchbox.mcp import jobs

    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits())
    repository.submit("tenant-a", _request())
    claimed = repository.claim("worker-a")
    assert claimed is not None and claimed.lease_version == 2
    observer = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits())
    monkeypatch.setattr(jobs, "mono_time", lambda: 10.0)
    assert observer.claim_expired("recovery") is None

    assert repository.renew(claimed.execution_id, "worker-a")
    monkeypatch.setattr(jobs, "mono_time", lambda: 100_000.0)
    assert observer.claim_expired("recovery") is None


def test_legacy_epoch_lease_remains_compatible_during_rolling_upgrade(tmp_path: Path) -> None:
    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits())
    submitted, _ = repository.submit("tenant-a", _request())
    claimed = repository.claim("legacy-worker")
    assert claimed is not None
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            """UPDATE mcp_benchmark_jobs
               SET lease_version = 1, lease_generation = 0, lease_expires_at = unixepoch() + 60
               WHERE execution_id = ?""",
            (submitted.execution_id,),
        )
    assert repository.claim_expired("new-worker") is None

    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "UPDATE mcp_benchmark_jobs SET lease_expires_at = unixepoch() - 1 WHERE execution_id = ?",
            (submitted.execution_id,),
        )
    recovered = repository.claim_expired("new-worker")
    assert recovered is not None and recovered.execution_id == submitted.execution_id


def test_unexpected_worker_failure_retries_then_can_complete(tmp_path: Path) -> None:
    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits(max_attempts=2))
    workspaces = TenantWorkspaceProvider(tmp_path / "workspaces")

    def failing_executor(_job, _staging: Path):
        raise RuntimeError("opaque failure")

    submitted, _ = repository.submit("tenant-a", _request())
    first_worker = DurableJobWorker(repository, workspaces, executor=failing_executor, worker_id="worker-a")
    first_claim = repository.claim(first_worker.worker_id)
    assert first_claim is not None
    anyio.run(first_worker._run_job, first_claim)
    retried = repository.get(submitted.execution_id)
    assert retried is not None and retried.state == "queued"
    assert retried.error_code == "RuntimeError"

    second_worker = DurableJobWorker(
        repository,
        workspaces,
        executor=lambda job, _staging: {"mcp_metadata": {"execution_id": job.execution_id}},
        worker_id="worker-b",
    )
    second_claim = repository.claim(second_worker.worker_id)
    assert second_claim is not None
    anyio.run(second_worker._run_job, second_claim)
    completed = repository.get(submitted.execution_id)
    assert completed is not None and completed.state == "completed"


def test_publishing_recovery_completes_existing_durable_artifact(tmp_path: Path) -> None:
    limits = JobLimits(lease_seconds=0.05, poll_seconds=0.01)
    repository = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    workspaces = TenantWorkspaceProvider(tmp_path / "workspaces")
    worker = DurableJobWorker(repository, workspaces, worker_id="recovery-worker")
    submitted, _ = repository.submit("tenant-a", _request())
    claimed = repository.claim("lost-worker")
    assert claimed is not None
    assert repository.begin_publication(claimed.execution_id, "lost-worker")
    _, final_dir, response_path = worker._job_paths(claimed)
    final_dir.mkdir(parents=True)
    response_path.write_text('{"mcp_metadata": {"execution_id": "recovered"}}', encoding="utf-8")
    (final_dir / ".published").write_text(claimed.execution_id, encoding="ascii")

    worker.recover_expired()
    anyio.run(anyio.sleep, 0.06)
    worker.recover_expired()
    recovered = repository.get(submitted.execution_id)
    assert recovered is not None and recovered.state == "completed"
    assert recovered.artifact_path == str(response_path)


def test_retention_removes_artifact_before_terminal_metadata(tmp_path: Path) -> None:
    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits(retention_seconds=60))
    worker = DurableJobWorker(repository, TenantWorkspaceProvider(tmp_path / "workspaces"))
    submitted, _ = repository.submit("tenant-a", _request())
    repository.cancel(submitted.execution_id, "tenant-a")
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "UPDATE mcp_benchmark_jobs SET completed_at = '2020-01-01T00:00:00+00:00' WHERE execution_id = ?",
            (submitted.execution_id,),
        )

    assert [job.execution_id for job in repository.expired_terminal()] == [submitted.execution_id]
    worker.purge_expired()
    assert repository.get(submitted.execution_id) is None


class TestDurableJobWindowsDirectoryFsync:
    def test_windows_directories_are_not_opened(self, tmp_path: Path, monkeypatch) -> None:
        directory = tmp_path / "stage"
        directory.mkdir()
        (directory / "file.txt").write_text("payload", encoding="utf-8")
        monkeypatch.setattr("benchbox.mcp.jobs.sys.platform", "win32")
        calls: list[tuple[Path, int]] = []

        original_open = __import__("os").open

        def tracking_open(path, *args, **kwargs):
            calls.append((Path(path), args[0]))
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr("benchbox.mcp.jobs.os.open", tracking_open)
        DurableJobWorker._sync_tree(directory)

        assert directory not in [path for path, _flags in calls]
        file_calls = [flags for path, flags in calls if path == directory / "file.txt"]
        assert file_calls == [__import__("os").O_RDWR]

    def test_windows_regular_files_still_fsync_and_close(self, tmp_path: Path, monkeypatch) -> None:
        regular = tmp_path / "response.json"
        regular.write_text("{}", encoding="utf-8")
        directory = tmp_path / "stage"
        directory.mkdir()
        (directory / "nested.txt").write_text("x", encoding="utf-8")
        monkeypatch.setattr("benchbox.mcp.jobs.sys.platform", "win32")
        monkeypatch.setattr("benchbox.mcp.jobs.os.fsync", lambda fd: None)
        closes: list[int] = []
        original_close = __import__("os").close
        monkeypatch.setattr("benchbox.mcp.jobs.os.close", lambda fd: closes.append(fd) or original_close(fd))

        DurableJobWorker._sync_path(regular)
        DurableJobWorker._sync_tree(directory)

        assert closes, "regular files must still be opened and closed on Windows"

    def test_posix_directories_are_still_flushed(self, tmp_path: Path, monkeypatch) -> None:
        directory = tmp_path / "stage-posix"
        directory.mkdir()
        monkeypatch.setattr("benchbox.mcp.jobs.sys.platform", "linux")
        opened: list[tuple[Path, int]] = []
        fsynced: list[int] = []
        closed: list[int] = []

        def fake_open(path, flags, *args, **kwargs):
            opened.append((Path(path), flags))
            return 41

        monkeypatch.setattr("benchbox.mcp.jobs.os.open", fake_open)
        monkeypatch.setattr("benchbox.mcp.jobs.os.fsync", fsynced.append)
        monkeypatch.setattr("benchbox.mcp.jobs.os.close", closed.append)
        DurableJobWorker._sync_tree(directory)

        assert opened == [(directory, os.O_RDONLY)]
        assert fsynced == [41]
        assert closed == [41]

    def test_regular_file_oserror_propagates(self, tmp_path: Path, monkeypatch) -> None:
        regular = tmp_path / "failure.json"
        regular.write_text("{}", encoding="utf-8")
        monkeypatch.setattr("benchbox.mcp.jobs.sys.platform", "win32")

        def failing_fsync(_fd: int) -> None:
            raise OSError("simulated fsync failure")

        monkeypatch.setattr("benchbox.mcp.jobs.os.fsync", failing_fsync)
        try:
            DurableJobWorker._sync_path(regular)
            raise AssertionError("OSError must propagate")
        except OSError as exc:
            assert "simulated" in str(exc)


def test_renew_trusts_ownership_not_wall_clock(tmp_path: Path) -> None:
    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits(lease_seconds=60))
    submitted, _ = repository.submit("tenant-a", _request())
    claimed = repository.claim("worker-a")
    assert claimed is not None
    assert repository.renew(submitted.execution_id, "worker-a") is True
    with repository._connect() as connection:
        connection.execute(
            "UPDATE mcp_benchmark_jobs SET lease_expires_at = ? WHERE execution_id = ?",
            (time.time() - 1.0, submitted.execution_id),
        )
    assert repository.renew(submitted.execution_id, "worker-a") is True
    with repository._connect() as connection:
        connection.execute(
            "UPDATE mcp_benchmark_jobs SET lease_owner = ? WHERE execution_id = ?",
            ("worker-b", submitted.execution_id),
        )
    assert repository.renew(submitted.execution_id, "worker-a") is False
    assert repository.renew(submitted.execution_id, "worker-b") is True


def test_stale_attempt_never_publishes_after_mid_run_lease_loss(tmp_path: Path, monkeypatch) -> None:
    limits = JobLimits(lease_seconds=0.05, poll_seconds=0.01, max_attempts=2)
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

    worker = DurableJobWorker(repository, workspaces, executor=blocking_executor, worker_id="worker-a")
    submitted, _ = repository.submit("tenant-a", _request())
    claimed = repository.claim("worker-a")
    assert claimed is not None
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(anyio.run, worker._run_job, claimed)
        assert entered.wait(timeout=10)
        monkeypatch.setattr(repository, "renew", lambda execution_id, worker_id: False)
        anyio.run(anyio.sleep, 0.08)
        with repository._connect() as connection:
            connection.execute(
                "UPDATE mcp_benchmark_jobs SET lease_expires_at = ? WHERE execution_id = ?",
                (time.time() - 1.0, submitted.execution_id),
            )
        worker.recover_expired()
        anyio.run(anyio.sleep, 0.06)
        worker.recover_expired()
        release.set()
        running.result(timeout=30)

    fenced = repository.get(submitted.execution_id)
    assert fenced is not None and fenced.state == "unknown"
    _, final_dir, response_path = worker._job_paths(claimed)
    assert not response_path.is_file()
    assert not (final_dir / ".published").is_file()


def test_run_job_honors_cancel_before_execution(tmp_path: Path) -> None:
    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits())
    workspaces = TenantWorkspaceProvider(tmp_path / "workspaces")
    calls: list[str] = []
    worker = DurableJobWorker(
        repository,
        workspaces,
        executor=lambda job, staging: calls.append(job.execution_id) or {"mcp_metadata": {}},
        worker_id="worker-a",
    )
    submitted, _ = repository.submit("tenant-a", _request())
    claimed = repository.claim("worker-a")
    assert claimed is not None
    assert repository.cancel(submitted.execution_id, "tenant-a") is not None
    anyio.run(worker._run_job, claimed)
    assert calls == []
    finished = repository.get(submitted.execution_id)
    assert finished is not None and finished.state == "cancelled"


def test_serialized_outstanding_work_remains_unknown_and_holds_capacity(tmp_path: Path) -> None:
    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits(max_running=1))
    workspaces = TenantWorkspaceProvider(tmp_path / "workspaces")
    worker = DurableJobWorker(
        repository,
        workspaces,
        executor=lambda _job, _staging: {
            "results": [
                {
                    "status": "FAILED",
                    "cleanup_state": "outstanding",
                    "outstanding_stream_ids": [3],
                }
            ]
        },
        worker_id="worker-a",
    )
    submitted, _ = repository.submit("tenant-a", _request())
    repository.submit("tenant-b", _request())
    claimed = repository.claim("worker-a")
    assert claimed is not None

    anyio.run(worker._run_job, claimed)

    contained = repository.get(submitted.execution_id)
    assert contained is not None and contained.state == "unknown"
    assert contained.error_code == "outstanding_work"
    assert contained.quiesced_at is None
    assert repository.claim("worker-b") is None


def test_run_job_skips_claim_fenced_before_start(tmp_path: Path) -> None:
    limits = JobLimits(lease_seconds=0.05, poll_seconds=0.01)
    repository = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    workspaces = TenantWorkspaceProvider(tmp_path / "workspaces")
    calls: list[str] = []
    worker = DurableJobWorker(
        repository,
        workspaces,
        executor=lambda job, staging: calls.append(job.execution_id) or {"mcp_metadata": {}},
        worker_id="worker-a",
    )
    submitted, _ = repository.submit("tenant-a", _request())
    claimed = repository.claim("worker-a")
    assert claimed is not None
    repository.claim_expired("observer")
    anyio.run(anyio.sleep, 0.06)
    fenced = repository.claim_expired("recovery-owner")
    assert fenced is not None
    anyio.run(worker._run_job, claimed)
    assert calls == []
    current = repository.get(submitted.execution_id)
    assert current is not None and current.lease_owner == fenced.lease_owner


def test_retry_is_allowed_only_for_quiescent_owner_reports(tmp_path: Path) -> None:
    repository = DurableJobRepository(
        tmp_path / "state.sqlite3", JobLimits(lease_seconds=0.05, poll_seconds=0.01, max_attempts=2)
    )
    submitted, _ = repository.submit("tenant-a", _request())
    claimed = repository.claim("worker-a")
    assert claimed is not None
    assert repository.fail_attempt(submitted.execution_id, "worker-b", "impostor") is None
    assert repository.fail_attempt(submitted.execution_id, "worker-a", "transient") == "queued"

    retried = repository.claim("worker-b")
    assert retried is not None
    assert repository.begin_publication(submitted.execution_id, "worker-b")
    artifact = tmp_path / "response.json"
    artifact.write_text("{}", encoding="utf-8")
    assert repository.complete(submitted.execution_id, "worker-b", artifact)
    assert repository.fail_attempt(submitted.execution_id, "worker-b", "late") is None

    lost, _ = repository.submit("tenant-a", _request())
    assert repository.claim("worker-c") is not None
    fence_worker = DurableJobWorker(repository, TenantWorkspaceProvider(tmp_path / "workspaces"))
    fence_worker.recover_expired()
    anyio.run(anyio.sleep, 0.06)
    fence_worker.recover_expired()
    unknown = repository.get(lost.execution_id)
    assert unknown is not None and unknown.state == "unknown"
    assert repository.fail_attempt(lost.execution_id, "worker-c", "late") is None
    assert repository.fail_attempt(lost.execution_id, "stranger", "late") is None


def test_unknown_outcome_requires_quiescence_before_retention(tmp_path: Path) -> None:
    limits = JobLimits(lease_seconds=0.05, poll_seconds=0.01, max_attempts=2, retention_seconds=3600)
    repository = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    worker = DurableJobWorker(repository, TenantWorkspaceProvider(tmp_path / "workspaces"))
    submitted, _ = repository.submit("tenant-a", _request())
    assert repository.claim("lost-worker") is not None
    worker.recover_expired()
    anyio.run(anyio.sleep, 0.06)
    worker.recover_expired()
    unknown = repository.get(submitted.execution_id)
    assert unknown is not None and unknown.state == "unknown"
    assert repository.expired_terminal() == []
    with repository._connect() as connection:
        connection.execute(
            "UPDATE mcp_benchmark_jobs SET completed_at = ? WHERE execution_id = ?",
            ("2000-01-01T00:00:00+00:00", submitted.execution_id),
        )
    expired = repository.expired_terminal()
    assert expired == []
    assert repository.delete_terminal(submitted.execution_id) is False
    assert repository.attest_quiescence(submitted.execution_id, "stranger") is False
    assert repository.attest_quiescence(submitted.execution_id, "lost-worker") is True
    expired = repository.expired_terminal()
    assert [job.execution_id for job in expired] == [submitted.execution_id]
    assert repository.delete_terminal(submitted.execution_id) is True
    assert repository.get(submitted.execution_id) is None


def test_quiescence_attestation_survives_a_later_recovery_fence(tmp_path: Path) -> None:
    limits = JobLimits(lease_seconds=0.05, poll_seconds=0.01, max_running=1)
    repository = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    first, _ = repository.submit("tenant-a", _request())
    second, _ = repository.submit("tenant-b", _request())
    assert repository.claim("worker-a") is not None

    assert repository.attest_quiescence(first.execution_id, "worker-a") is True
    worker = DurableJobWorker(repository, TenantWorkspaceProvider(tmp_path / "workspaces"))
    worker.recover_expired()
    anyio.run(anyio.sleep, 0.06)
    worker.recover_expired()

    recovered = repository.get(first.execution_id)
    assert recovered is not None and recovered.state == "unknown"
    assert recovered.quiesced_at is not None
    replacement = repository.claim("worker-b")
    assert replacement is not None and replacement.execution_id == second.execution_id


def test_quiescence_attestation_between_fence_and_recovery_is_owner_fenced(tmp_path: Path) -> None:
    limits = JobLimits(lease_seconds=0.05, poll_seconds=0.01, max_running=1)
    repository = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    submitted, _ = repository.submit("tenant-a", _request())
    assert repository.claim("worker-a") is not None

    assert repository.claim_expired("observer") is None
    anyio.run(anyio.sleep, 0.06)
    fenced = repository.claim_expired("recovery-owner")
    assert fenced is not None
    assert fenced.state == "running"
    assert fenced.unproven_owner == "worker-a"
    assert repository.attest_quiescence(submitted.execution_id, "stranger") is False
    assert repository.attest_quiescence(submitted.execution_id, "worker-a") is True
    assert repository.recover(fenced) == "unknown"

    recovered = repository.get(submitted.execution_id)
    assert recovered is not None and recovered.state == "unknown"
    assert recovered.quiesced_at is not None


def test_legacy_database_migrates_state_check_for_unknown(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    _write_legacy_database(path)

    repository = DurableJobRepository(path, JobLimits(lease_seconds=0.05, poll_seconds=0.01, max_attempts=2))
    legacy = repository.get("mcp_job_legacy")
    assert legacy is not None and legacy.state == "queued"
    claimed = repository.claim("worker-a")
    assert claimed is not None and claimed.execution_id == "mcp_job_legacy"
    worker = DurableJobWorker(repository, TenantWorkspaceProvider(tmp_path / "workspaces"))
    worker.recover_expired()
    anyio.run(anyio.sleep, 0.06)
    worker.recover_expired()
    migrated = repository.get("mcp_job_legacy")
    assert migrated is not None and migrated.state == "unknown"


def _write_legacy_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE mcp_benchmark_jobs (
            execution_id TEXT PRIMARY KEY,
            principal_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK (
                state IN ('queued', 'running', 'publishing', 'completed', 'failed', 'cancelled')
            ),
            request_json TEXT NOT NULL,
            idempotency_key TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            lease_owner TEXT,
            lease_expires_at REAL,
            lease_version INTEGER NOT NULL DEFAULT 1,
            lease_generation INTEGER NOT NULL DEFAULT 0,
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            artifact_path TEXT,
            error_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO mcp_benchmark_jobs (execution_id, principal_id, state, request_json,"
        " created_at, updated_at) VALUES (?, ?, 'queued', ?, ?, ?)",
        (
            "mcp_job_legacy",
            "tenant-a",
            json.dumps(_request()),
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    connection.commit()
    connection.close()


def test_migration_loser_proceeds_when_winner_finished(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "state.sqlite3"
    _write_legacy_database(path)
    DurableJobRepository(path, JobLimits())

    def racy_rebuild(connection) -> None:
        raise sqlite3.OperationalError("table mcp_benchmark_jobs_legacy already exists")

    monkeypatch.setattr(DurableJobRepository, "_rebuild_state_table", staticmethod(racy_rebuild))
    reopened = DurableJobRepository(path, JobLimits())
    assert reopened.get("mcp_job_legacy") is not None


def test_migration_failure_still_raises_when_unmigrated(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "state.sqlite3"
    _write_legacy_database(path)

    def racy_rebuild(connection) -> None:
        raise sqlite3.OperationalError("table mcp_benchmark_jobs_legacy already exists")

    monkeypatch.setattr(DurableJobRepository, "_rebuild_state_table", staticmethod(racy_rebuild))
    with pytest.raises(sqlite3.OperationalError):
        DurableJobRepository(path, JobLimits())


def test_submit_enforces_global_and_per_principal_queued_bounds(tmp_path: Path) -> None:
    limits = JobLimits(queue_limit=2, max_queued_per_principal=1)
    repository = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    repository.submit("tenant-a", _request())
    with pytest.raises(MCPError, match="for this principal$"):
        repository.submit("tenant-a", _request())
    repository.submit("tenant-b", _request())
    with pytest.raises(MCPError, match="queue is full$"):
        repository.submit("tenant-c", _request())


def test_retry_respects_global_queued_bound(tmp_path: Path) -> None:
    repository = DurableJobRepository(
        tmp_path / "state.sqlite3",
        JobLimits(queue_limit=1, max_queued_per_principal=5, max_attempts=2),
    )
    retrying, _ = repository.submit("tenant-a", _request())
    assert repository.claim("worker-a") is not None
    queued, _ = repository.submit("tenant-b", _request())

    assert repository.fail_attempt(retrying.execution_id, "worker-a", "transient") == "failed"
    failed = repository.get(retrying.execution_id)
    assert failed is not None and failed.error_code == "retry_queue_full"
    claimed = repository.claim("worker-b")
    assert claimed is not None and claimed.execution_id == queued.execution_id


def test_retry_respects_per_principal_queued_bound(tmp_path: Path) -> None:
    repository = DurableJobRepository(
        tmp_path / "state.sqlite3",
        JobLimits(queue_limit=5, max_queued_per_principal=1, max_attempts=2),
    )
    retrying, _ = repository.submit("tenant-a", _request())
    assert repository.claim("worker-a") is not None
    queued, _ = repository.submit("tenant-a", _request())

    assert repository.fail_attempt(retrying.execution_id, "worker-a", "transient") == "failed"
    failed = repository.get(retrying.execution_id)
    assert failed is not None and failed.error_code == "retry_queue_full"
    claimed = repository.claim("worker-b")
    assert claimed is not None and claimed.execution_id == queued.execution_id


def test_claim_enforces_global_running_bound_including_unknown(tmp_path: Path) -> None:
    limits = JobLimits(lease_seconds=0.05, poll_seconds=0.01, max_attempts=2, max_running=1)
    repository = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    first, _ = repository.submit("tenant-a", _request())
    repository.submit("tenant-b", _request())
    assert repository.claim("worker-a") is not None
    assert repository.claim("worker-b") is None

    worker = DurableJobWorker(repository, TenantWorkspaceProvider(tmp_path / "workspaces"))
    worker.recover_expired()
    anyio.run(anyio.sleep, 0.06)
    worker.recover_expired()
    lost = repository.get(first.execution_id)
    assert lost is not None and lost.state == "unknown"
    assert repository.claim("worker-b") is None
    summary = repository.capacity_summary()
    assert summary["outstanding"] == 1
    assert summary["queued"] == 1


def test_claim_enforces_per_principal_running_bound(tmp_path: Path) -> None:
    limits = JobLimits(max_running=4, max_running_per_principal=1)
    repository = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    repository.submit("tenant-a", _request())
    repository.submit("tenant-a", _request())
    repository.submit("tenant-b", _request())
    assert repository.claim("worker-a") is not None
    claimed = repository.claim("worker-b")
    assert claimed is not None and claimed.principal_id == "tenant-b"
    assert repository.claim("worker-c") is None


def test_claim_serves_least_recently_served_principal_first(tmp_path: Path) -> None:
    limits = JobLimits(max_running=8, max_running_per_principal=8)
    repository = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    noisy = [repository.submit("tenant-noisy", _request())[0].execution_id for _ in range(4)]
    quiet, _ = repository.submit("tenant-quiet", _request())
    order = []
    for worker in ("worker-1", "worker-2", "worker-3", "worker-4", "worker-5"):
        claimed = repository.claim(worker)
        assert claimed is not None
        order.append(claimed.principal_id)
    assert order[:2] == ["tenant-noisy", "tenant-quiet"]
    assert set(order[2:]) == {"tenant-noisy"}
    noisy_claimed = [
        job.execution_id
        for job in (repository.get(execution_id) for execution_id in noisy)
        if job is not None and job.state == "running"
    ]
    assert noisy_claimed == noisy[:4]


def test_claim_fairness_and_fifo_ignore_wall_clock_rollback(tmp_path: Path, monkeypatch) -> None:
    from datetime import datetime, timedelta, timezone

    from benchbox.mcp import jobs

    times = iter(
        [
            datetime(2026, 1, 3, tzinfo=timezone.utc),
            datetime(2026, 1, 2, tzinfo=timezone.utc),
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2025, 12, 31, tzinfo=timezone.utc),
            datetime(2025, 12, 30, tzinfo=timezone.utc),
            datetime(2025, 12, 29, tzinfo=timezone.utc),
        ]
    )
    monkeypatch.setattr(jobs, "utc_now", lambda: next(times))
    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits(max_running=3, max_running_per_principal=3))
    first, _ = repository.submit("tenant-a", _request())
    second, _ = repository.submit("tenant-a", _request())
    quiet, _ = repository.submit("tenant-b", _request())

    claimed_first = repository.claim("worker-1")
    claimed_quiet = repository.claim("worker-2")
    claimed_second = repository.claim("worker-3")

    assert claimed_first is not None and claimed_first.execution_id == first.execution_id
    assert claimed_quiet is not None and claimed_quiet.execution_id == quiet.execution_id
    assert claimed_second is not None and claimed_second.execution_id == second.execution_id


def test_capacity_summary_names_quarantine_reason(tmp_path: Path) -> None:
    limits = JobLimits(lease_seconds=0.05, poll_seconds=0.01, max_attempts=2)
    repository = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    repository.submit("tenant-a", _request())
    repository.submit("tenant-b", _request())
    assert repository.claim("lost-worker") is not None
    worker = DurableJobWorker(repository, TenantWorkspaceProvider(tmp_path / "workspaces"))
    worker.recover_expired()
    anyio.run(anyio.sleep, 0.06)
    worker.recover_expired()
    summary = repository.capacity_summary()
    assert summary["limits"]["max_running"] == limits.max_running
    assert summary["states"]["unknown"] == 1
    assert summary["states"]["queued"] == 1
    assert len(summary["quarantined"]) == 1
    entry = summary["quarantined"][0]
    assert entry["reason"] == "unknown_outcome"
    assert entry["principal_id"] == "tenant-a"
    assert summary["per_principal"]["tenant-a"] == {"queued": 0, "outstanding": 1}
    assert summary["per_principal"]["tenant-b"] == {"queued": 1, "outstanding": 0}


def test_concurrent_claims_from_separate_handles_admit_exactly_one(tmp_path: Path) -> None:
    limits = JobLimits(max_running=1)
    first = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    second = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    first.submit("tenant-a", _request())
    with ThreadPoolExecutor(max_workers=2) as pool:
        winners = list(
            pool.map(lambda worker: first.claim(worker) if worker == "a" else second.claim(worker), ("a", "b"))
        )
    assert sum(1 for claimed in winners if claimed is not None) == 1


def test_exhausted_unreported_attempt_recovers_unknown_not_failed(tmp_path: Path) -> None:
    limits = JobLimits(lease_seconds=0.05, poll_seconds=0.01, max_attempts=1)
    repository = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    worker = DurableJobWorker(repository, TenantWorkspaceProvider(tmp_path / "workspaces"))
    submitted, _ = repository.submit("tenant-a", _request())
    claimed = repository.claim("lost-worker")
    assert claimed is not None and claimed.attempts == 1
    worker.recover_expired()
    anyio.run(anyio.sleep, 0.06)
    worker.recover_expired()
    recovered = repository.get(submitted.execution_id)
    assert recovered is not None and recovered.state == "unknown"
    assert recovered.error_code == "unknown_outcome"


def test_stranded_legacy_migration_resumes_without_losing_jobs(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    _write_legacy_database(path)
    legacy = sqlite3.connect(path)
    legacy.execute("ALTER TABLE mcp_benchmark_jobs RENAME TO mcp_benchmark_jobs_legacy")
    legacy.execute(
        """
        CREATE TABLE mcp_benchmark_jobs (
            execution_id TEXT PRIMARY KEY,
            principal_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK (
                state IN (
                    'queued', 'running', 'publishing', 'completed',
                    'failed', 'cancelled', 'unknown'
                )
            ),
            request_json TEXT NOT NULL,
            idempotency_key TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            lease_owner TEXT,
            lease_expires_at REAL,
            lease_version INTEGER NOT NULL DEFAULT 1,
            lease_generation INTEGER NOT NULL DEFAULT 0,
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            artifact_path TEXT,
            error_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT
        );
        """
    )
    legacy.commit()
    legacy.close()

    repository = DurableJobRepository(path, JobLimits())
    resumed = repository.get("mcp_job_legacy")
    assert resumed is not None and resumed.state == "queued"
    with repository._connect() as connection:
        stranded = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'mcp_benchmark_jobs_legacy'"
        ).fetchone()
    assert stranded is None


def test_concurrent_initializers_migrate_legacy_database_once(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    _write_legacy_database(path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        repositories = list(pool.map(lambda _: DurableJobRepository(path, JobLimits()), range(4)))
    for repository in repositories:
        assert repository.get("mcp_job_legacy") is not None
    with repositories[0]._connect() as connection:
        total = connection.execute("SELECT COUNT(*) FROM mcp_benchmark_jobs").fetchone()[0]
        stranded = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'mcp_benchmark_jobs_legacy'"
        ).fetchone()
    assert total == 1
    assert stranded is None


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_leaked_throughput_work_attests_quiescence_when_its_futures_finish(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from benchbox.core.throughput.containment import record_outstanding_result

    release = threading.Event()
    pool = ThreadPoolExecutor(max_workers=1)
    leaked = SimpleNamespace(
        outstanding_stream_ids=[3],
        cleanup_state="outstanding",
        outstanding_notes=[],
        _outstanding_futures={3: pool.submit(release.wait, 5.0)},
    )

    def executor(_job, _staging):
        record_outstanding_result(leaked)
        return {
            "phases": {
                "throughput_test": {
                    "status": "FAILED",
                    "outstanding_work": {"stream_ids": [3], "cleanup_state": "outstanding"},
                }
            }
        }

    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits(max_running=1, poll_seconds=0.01))
    worker = DurableJobWorker(
        repository, TenantWorkspaceProvider(tmp_path / "workspaces"), executor=executor, worker_id="worker-a"
    )
    submitted, _ = repository.submit("tenant-a", _request())
    waiting, _ = repository.submit("tenant-b", _request())
    claimed = repository.claim("worker-a")
    assert claimed is not None
    staging, _, _ = worker._job_paths(claimed)

    try:
        anyio.run(worker._run_job, claimed)

        contained = repository.get(submitted.execution_id)
        assert contained is not None and contained.state == "unknown" and contained.quiesced_at is None
        assert repository.claim("worker-b") is None
        assert staging.exists()

        release.set()
        assert _wait_until(lambda: (repository.get(submitted.execution_id) or contained).quiesced_at is not None)
        assert _wait_until(lambda: not staging.exists())
        replacement = repository.claim("worker-b")
        assert replacement is not None and replacement.execution_id == waiting.execution_id
    finally:
        release.set()
        pool.shutdown(wait=True)


def test_leaked_work_without_observable_handles_stays_quarantined(tmp_path: Path) -> None:
    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits(max_running=1))
    worker = DurableJobWorker(
        repository,
        TenantWorkspaceProvider(tmp_path / "workspaces"),
        executor=lambda _job, _staging: {
            "phases": {"throughput_test": {"outstanding_work": {"stream_ids": [1], "cleanup_state": "outstanding"}}}
        },
        worker_id="worker-a",
    )
    submitted, _ = repository.submit("tenant-a", _request())
    claimed = repository.claim("worker-a")
    assert claimed is not None

    anyio.run(worker._run_job, claimed)

    contained = repository.get(submitted.execution_id)
    assert contained is not None and contained.state == "unknown" and contained.quiesced_at is None
    assert repository.capacity_summary()["outstanding"] == 1


@pytest.mark.parametrize(
    ("response", "state", "outcome"),
    [
        (
            {"summary": {"queries": {"total": 2, "passed": 2, "failed": 0}}, "mcp_metadata": {"status": "completed"}},
            "completed",
            "completed",
        ),
        (
            {"summary": {"queries": {"total": 2, "passed": 1, "failed": 1}}, "mcp_metadata": {"status": "completed"}},
            "completed",
            "failed",
        ),
        (
            {"summary": {"queries": {"failed": 0}}, "phases": {"power_test": {"status": "FAILED"}}},
            "completed",
            "failed",
        ),
        ({"summary": {"queries": {"failed": 0}, "validation": "failed"}}, "completed", "failed"),
        ({"mcp_metadata": {"status": "no_results"}}, "completed", "incomplete"),
        ({"mcp_metadata": {"status": "incomplete"}}, "completed", "incomplete"),
        ({"status": "failed", "execution_id": "x"}, "failed", "failed"),
    ],
)
def test_job_stores_and_exposes_shared_outcome_derived_from_result(
    tmp_path: Path, response: dict[str, object], state: str, outcome: str
) -> None:
    from benchbox.mcp.jobs import _public_status

    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits())
    worker = DurableJobWorker(
        repository,
        TenantWorkspaceProvider(tmp_path / "workspaces"),
        executor=lambda _job, _staging: dict(response),
        worker_id="worker-a",
    )
    submitted, _ = repository.submit("tenant-a", _request())
    claimed = repository.claim("worker-a")
    assert claimed is not None

    anyio.run(worker._run_job, claimed)

    finished = repository.get(submitted.execution_id)
    assert finished is not None and finished.state == state
    assert finished.outcome == outcome
    assert _public_status(finished)["outcome"] == outcome


def test_public_outcome_reports_non_terminal_and_quarantined_states(tmp_path: Path) -> None:
    from benchbox.mcp.jobs import _public_status

    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits(max_running=2))
    submitted, _ = repository.submit("tenant-a", _request())
    assert _public_status(submitted)["outcome"] is None
    assert repository.claim("worker-a") is not None
    assert repository.mark_unknown_outstanding(submitted.execution_id, "worker-a") is True
    contained = repository.get(submitted.execution_id)
    assert contained is not None
    assert _public_status(contained)["outcome"] == "outstanding_work"


def test_recovered_published_artifact_keeps_its_derived_outcome(tmp_path: Path) -> None:
    limits = JobLimits(lease_seconds=0.05, poll_seconds=0.01)
    repository = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    worker = DurableJobWorker(repository, TenantWorkspaceProvider(tmp_path / "workspaces"), worker_id="recovery")
    submitted, _ = repository.submit("tenant-a", _request())
    claimed = repository.claim("lost-worker")
    assert claimed is not None
    assert repository.begin_publication(submitted.execution_id, "lost-worker")
    _, final_dir, response_path = worker._job_paths(claimed)
    final_dir.mkdir(parents=True)
    response_path.write_text(json.dumps({"summary": {"queries": {"failed": 2}}}), encoding="utf-8")
    (final_dir / ".published").write_text(submitted.execution_id, encoding="ascii")

    worker.recover_expired()
    anyio.run(anyio.sleep, 0.06)
    worker.recover_expired()

    recovered = repository.get(submitted.execution_id)
    assert recovered is not None and recovered.state == "completed"
    assert recovered.outcome == "failed"


@pytest.mark.parametrize("response", [None, {"summary": {"queries": {"total": 1, "passed": 1, "failed": 0}}}])
def test_leaked_work_quarantines_even_when_the_executor_raises_or_the_response_hides_it(
    tmp_path: Path, response: dict[str, object] | None
) -> None:
    from types import SimpleNamespace

    from benchbox.core.throughput.containment import record_outstanding_result

    release = threading.Event()
    pool = ThreadPoolExecutor(max_workers=1)
    leaked = SimpleNamespace(
        outstanding_stream_ids=[5],
        cleanup_state="outstanding",
        outstanding_notes=[],
        _outstanding_futures={5: pool.submit(release.wait, 5.0)},
    )

    def executor(_job, _staging):
        record_outstanding_result(leaked)
        if response is None:
            raise RuntimeError("driver failed after leaking a stream")
        return dict(response)

    repository = DurableJobRepository(
        tmp_path / "state.sqlite3", JobLimits(max_running=1, poll_seconds=0.01, max_attempts=3)
    )
    worker = DurableJobWorker(
        repository, TenantWorkspaceProvider(tmp_path / "workspaces"), executor=executor, worker_id="worker-a"
    )
    submitted, _ = repository.submit("tenant-a", _request())
    claimed = repository.claim("worker-a")
    assert claimed is not None

    try:
        anyio.run(worker._run_job, claimed)

        contained = repository.get(submitted.execution_id)
        assert contained is not None and contained.state == "unknown" and contained.quiesced_at is None
        assert repository.claim("worker-b") is None

        release.set()
        assert _wait_until(lambda: (repository.get(submitted.execution_id) or contained).quiesced_at is not None)
        assert repository.capacity_summary()["outstanding"] == 0
    finally:
        release.set()
        pool.shutdown(wait=True)


def test_quiescence_attestation_is_retried_and_logged_when_the_store_fails(tmp_path: Path, caplog, monkeypatch) -> None:
    from types import SimpleNamespace

    finished = _done_future()
    leaked = SimpleNamespace(
        outstanding_stream_ids=[1],
        cleanup_state="outstanding",
        outstanding_notes=[],
        _outstanding_futures={1: finished},
    )
    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits(max_running=1, poll_seconds=0.01))
    worker = DurableJobWorker(repository, TenantWorkspaceProvider(tmp_path / "workspaces"), worker_id="worker-a")
    submitted, _ = repository.submit("tenant-a", _request())
    assert repository.claim("worker-a") is not None
    assert repository.mark_unknown_outstanding(submitted.execution_id, "worker-a") is True
    real_attest = repository.attest_quiescence
    calls: list[int] = []

    def flaky(execution_id: str, worker_id: str) -> bool:
        calls.append(1)
        if len(calls) < 3:
            raise sqlite3.OperationalError("database is locked")
        return real_attest(execution_id, worker_id)

    monkeypatch.setattr(repository, "attest_quiescence", flaky)
    caplog.set_level(logging.WARNING, logger="benchbox.mcp.jobs")
    worker._attest_when_leaked_work_ends(submitted.execution_id, tmp_path / "staging", [leaked])

    assert _wait_until(lambda: (repository.get(submitted.execution_id) or submitted).quiesced_at is not None)
    assert len(calls) == 3
    assert "Quiescence attestation failed" in caplog.text


def _done_future():
    from concurrent.futures import Future

    future: Future = Future()
    future.set_result(None)
    return future


def test_outcome_of_a_row_written_before_the_upgrade_is_derived_from_its_artifact(tmp_path: Path) -> None:
    from benchbox.mcp.jobs import _public_status

    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits())
    worker = DurableJobWorker(
        repository,
        TenantWorkspaceProvider(tmp_path / "workspaces"),
        executor=lambda _job, _staging: {"summary": {"queries": {"failed": 1}}},
        worker_id="worker-a",
    )
    submitted, _ = repository.submit("tenant-a", _request())
    claimed = repository.claim("worker-a")
    assert claimed is not None
    anyio.run(worker._run_job, claimed)
    with repository._connect() as connection:
        connection.execute(
            "UPDATE mcp_benchmark_jobs SET outcome = NULL WHERE execution_id = ?", (submitted.execution_id,)
        )

    legacy = repository.get(submitted.execution_id)
    assert legacy is not None and legacy.outcome is None
    assert _public_status(legacy)["outcome"] == "failed"

    _, _, response_path = worker._job_paths(claimed)
    response_path.unlink()
    assert _public_status(legacy)["outcome"] is None


def test_per_principal_queue_bound_holds_across_repository_handles(tmp_path: Path) -> None:
    limits = JobLimits(queue_limit=5, max_queued_per_principal=1)
    first = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    second = DurableJobRepository(tmp_path / "state.sqlite3", limits)
    first.submit("tenant-a", _request())

    with pytest.raises(MCPError, match="for this principal$"):
        second.submit("tenant-a", _request())
    second.submit("tenant-b", _request())


def _quiesced_result(stream_id: int = 2):
    from types import SimpleNamespace

    return SimpleNamespace(
        outstanding_stream_ids=[stream_id],
        cleanup_state="outstanding",
        outstanding_notes=[],
        _outstanding_futures={stream_id: _done_future()},
    )


def _run_with_tracked_result(tmp_path: Path, tracked, response: dict[str, object]):
    from benchbox.core.throughput.containment import record_outstanding_result

    def executor(_job, _staging):
        record_outstanding_result(tracked)
        return response

    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits(max_running=1, poll_seconds=0.01))
    worker = DurableJobWorker(
        repository, TenantWorkspaceProvider(tmp_path / "workspaces"), executor=executor, worker_id="worker-a"
    )
    submitted, _ = repository.submit("tenant-a", _request())
    waiting, _ = repository.submit("tenant-b", _request())
    claimed = repository.claim("worker-a")
    assert claimed is not None
    anyio.run(worker._run_job, claimed)
    return repository, worker, submitted, waiting


def test_tracked_work_that_quiesced_in_run_publishes_with_its_derived_outcome(tmp_path: Path) -> None:
    from benchbox.mcp.jobs import _public_status

    response = {
        "summary": {"queries": {"total": 22, "passed": 22, "failed": 0}},
        "phases": {"throughput_test": {"status": "FAILED"}},
    }
    repository, worker, submitted, waiting = _run_with_tracked_result(tmp_path, _quiesced_result(), response)

    finished = repository.get(submitted.execution_id)
    assert finished is not None and finished.state == "completed"
    assert finished.artifact_path is not None and Path(finished.artifact_path).is_file()
    assert finished.outcome == "failed"
    assert _public_status(finished)["outcome"] == "failed"
    replacement = repository.claim("worker-b")
    assert replacement is not None and replacement.execution_id == waiting.execution_id


def test_stale_outstanding_export_of_quiesced_work_publishes_an_annotated_result(tmp_path: Path) -> None:
    response = {
        "mcp_metadata": {"result_file": None},
        "phases": {
            "throughput_test": {
                "status": "FAILED",
                "outstanding_work": {"stream_ids": [2], "cleanup_state": "outstanding"},
            }
        },
    }
    repository, _worker, submitted, waiting = _run_with_tracked_result(tmp_path, _quiesced_result(), response)

    finished = repository.get(submitted.execution_id)
    assert finished is not None and finished.state == "completed" and finished.outcome == "failed"
    payload = json.loads(Path(finished.artifact_path).read_text(encoding="utf-8"))
    assert not _response_has_outstanding_work(payload)
    assert payload["phases"]["throughput_test"]["outstanding_work"] == {
        "stream_ids": [],
        "quiesced_stream_ids": [2],
        "cleanup_state": "quiesced",
        "quiesced_before_publication": True,
    }
    assert "moment of export" in payload["mcp_metadata"]["result_file_note"]
    replacement = repository.claim("worker-b")
    assert replacement is not None and replacement.execution_id == waiting.execution_id


def test_outstanding_streams_the_worker_never_tracked_keep_the_job_quarantined(tmp_path: Path) -> None:
    response = {
        "mcp_metadata": {"result_file": None},
        "phases": {
            "throughput_test": {
                "status": "FAILED",
                "outstanding_work": {"stream_ids": [2, 9], "cleanup_state": "outstanding"},
            }
        },
    }
    repository, _worker, submitted, _waiting = _run_with_tracked_result(tmp_path, _quiesced_result(2), response)

    contained = repository.get(submitted.execution_id)
    assert contained is not None and contained.state == "unknown" and contained.artifact_path is None
    assert not _wait_until(lambda: repository.get(submitted.execution_id).quiesced_at is not None, timeout=0.5)
    assert repository.claim("worker-b") is None


def test_unproven_tracked_work_is_quarantined_then_attested_even_after_it_quiesces(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from benchbox.core.throughput.containment import record_outstanding_result

    release = threading.Event()
    pool = ThreadPoolExecutor(max_workers=1)
    tracked = SimpleNamespace(
        outstanding_stream_ids=[2],
        cleanup_state="outstanding",
        outstanding_notes=[],
        _outstanding_futures={2: pool.submit(release.wait, 5.0)},
    )

    def executor(_job, _staging):
        record_outstanding_result(tracked)
        return {"summary": {"queries": {"failed": 0}}}

    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits(max_running=1, poll_seconds=0.01))
    worker = DurableJobWorker(
        repository, TenantWorkspaceProvider(tmp_path / "workspaces"), executor=executor, worker_id="worker-a"
    )
    submitted, _ = repository.submit("tenant-a", _request())
    claimed = repository.claim("worker-a")
    assert claimed is not None
    try:
        anyio.run(worker._run_job, claimed)
        contained = repository.get(submitted.execution_id)
        assert contained is not None and contained.state == "unknown" and contained.artifact_path is None
        release.set()
        assert _wait_until(lambda: repository.get(submitted.execution_id).quiesced_at is not None)
    finally:
        release.set()
        pool.shutdown(wait=True)


def test_public_outcome_stops_reporting_outstanding_work_once_quiescence_is_attested(tmp_path: Path) -> None:
    from benchbox.mcp.jobs import _public_status

    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits())
    submitted, _ = repository.submit("tenant-a", _request())
    assert repository.claim("worker-a") is not None
    assert repository.mark_unknown_outstanding(submitted.execution_id, "worker-a") is True
    contained = repository.get(submitted.execution_id)
    assert contained is not None and _public_status(contained)["outcome"] == "outstanding_work"

    assert repository.attest_quiescence(submitted.execution_id, "worker-a") is True
    released = repository.get(submitted.execution_id)
    assert released is not None and _public_status(released)["outcome"] == "unknown"


def test_untracked_stream_keeps_the_job_unobserved_even_while_tracked_work_still_runs(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from benchbox.core.throughput.containment import record_outstanding_result

    release = threading.Event()
    pool = ThreadPoolExecutor(max_workers=1)
    tracked = SimpleNamespace(
        outstanding_stream_ids=[2],
        cleanup_state="outstanding",
        outstanding_notes=[],
        _outstanding_futures={2: pool.submit(release.wait, 5.0)},
    )

    def executor(_job, _staging):
        record_outstanding_result(tracked)
        return {
            "phases": {"throughput_test": {"outstanding_work": {"stream_ids": [2, 9], "cleanup_state": "outstanding"}}}
        }

    repository = DurableJobRepository(tmp_path / "state.sqlite3", JobLimits(max_running=1, poll_seconds=0.01))
    worker = DurableJobWorker(
        repository, TenantWorkspaceProvider(tmp_path / "workspaces"), executor=executor, worker_id="worker-a"
    )
    submitted, _ = repository.submit("tenant-a", _request())
    claimed = repository.claim("worker-a")
    assert claimed is not None
    try:
        anyio.run(worker._run_job, claimed)
        release.set()
        assert not _wait_until(lambda: repository.get(submitted.execution_id).quiesced_at is not None, timeout=0.5)
        contained = repository.get(submitted.execution_id)
        assert contained is not None and contained.state == "unknown"
        assert repository.claim("worker-b") is None
    finally:
        release.set()
        pool.shutdown(wait=True)


def test_unparseable_stream_id_quarantines_instead_of_escaping_as_a_failure(tmp_path: Path) -> None:
    response = {
        "mcp_metadata": {"result_file": None},
        "phases": {
            "throughput_test": {"outstanding_work": {"stream_ids": [2, "not-a-stream"], "cleanup_state": "outstanding"}}
        },
    }
    repository, _worker, submitted, _waiting = _run_with_tracked_result(tmp_path, _quiesced_result(2), response)

    contained = repository.get(submitted.execution_id)
    assert contained is not None and contained.state == "unknown" and contained.attempts == 1
    assert contained.error_code == "outstanding_work" and contained.quiesced_at is None
    assert repository.claim("worker-b") is None
