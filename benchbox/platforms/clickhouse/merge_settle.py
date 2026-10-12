from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from benchbox.utils.clock import elapsed_seconds, mono_time

MERGE_SETTLE_TIMEOUT_SECONDS = 600.0
MERGE_SETTLE_POLL_SECONDS = 1.0
MERGE_SETTLE_STABLE_POLLS = 3

_SNAPSHOT_SQL = (
    "SELECT "
    "(SELECT count() FROM system.merges WHERE database = currentDatabase()), "
    "(SELECT count() FROM system.parts WHERE database = currentDatabase() AND active)"
)


@dataclass(frozen=True)
class MergeSettleResult:
    settled: bool
    waited_seconds: float
    active_parts: int | None


def wait_for_merges_to_settle(
    connection: Any,
    *,
    timeout_seconds: float = MERGE_SETTLE_TIMEOUT_SECONDS,
    poll_seconds: float = MERGE_SETTLE_POLL_SECONDS,
    stable_polls: int = MERGE_SETTLE_STABLE_POLLS,
    sleep: Callable[[float], None] = time.sleep,
) -> MergeSettleResult:
    started = mono_time()
    stable = 0
    previous_parts: int | None = None
    parts = 0
    while True:
        merges, parts = (int(value) for value in connection.execute(_SNAPSHOT_SQL)[0])
        quiet = merges == 0 and (previous_parts is None or parts == previous_parts)
        stable = stable + 1 if quiet else 0
        previous_parts = parts
        if stable >= stable_polls:
            return MergeSettleResult(True, elapsed_seconds(started), parts)
        if elapsed_seconds(started) >= timeout_seconds:
            return MergeSettleResult(False, elapsed_seconds(started), parts)
        sleep(poll_seconds)
