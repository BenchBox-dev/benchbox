from __future__ import annotations

from pathlib import Path

import anyio
import pytest
from mcp.shared.exceptions import MCPError

from benchbox.mcp.security import AdmissionLimits, DurableSecurityStore

pytestmark = [pytest.mark.integration, pytest.mark.medium]


def test_two_workers_share_global_concurrency_and_bounded_queue(tmp_path: Path) -> None:
    limits = AdmissionLimits(
        requests_per_minute=100,
        global_concurrency=1,
        principal_concurrency=1,
        queue_limit=1,
        queue_timeout_seconds=2,
    )
    worker_a = DurableSecurityStore(tmp_path / "shared.sqlite3", limits)
    worker_b = DurableSecurityStore(tmp_path / "shared.sqlite3", limits)

    async def exercise() -> None:
        first = await worker_a.admit("principal-a")
        acquired: list[object] = []

        async def wait_on_other_worker() -> None:
            acquired.append(await worker_b.admit("principal-b"))

        async with anyio.create_task_group() as group:
            group.start_soon(wait_on_other_worker)
            await anyio.sleep(0.1)
            with pytest.raises(MCPError, match="queue is full"):
                await worker_a.admit("principal-c")
            worker_a.release(first)

        assert len(acquired) == 1
        worker_b.release(acquired[0])

    anyio.run(exercise)


def test_rate_limit_cannot_be_bypassed_by_alternating_workers(tmp_path: Path) -> None:
    limits = AdmissionLimits(requests_per_minute=2, global_concurrency=4, principal_concurrency=4)
    worker_a = DurableSecurityStore(tmp_path / "shared.sqlite3", limits)
    worker_b = DurableSecurityStore(tmp_path / "shared.sqlite3", limits)

    async def exercise() -> None:
        first = await worker_a.admit("principal-a")
        worker_a.release(first)
        second = await worker_b.admit("principal-a")
        worker_b.release(second)
        with pytest.raises(MCPError, match="rate limit"):
            await worker_a.admit("principal-a")

    anyio.run(exercise)
