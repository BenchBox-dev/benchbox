from __future__ import annotations

import concurrent.futures
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_outstanding_sink: ContextVar[list[Any] | None] = ContextVar("benchbox_outstanding_sink", default=None)


@contextmanager
def track_outstanding_results() -> Iterator[list[Any]]:
    sink: list[Any] = []
    token = _outstanding_sink.set(sink)
    try:
        yield sink
    finally:
        _outstanding_sink.reset(token)


def record_outstanding_result(throughput_result: Any) -> None:
    sink = _outstanding_sink.get()
    if sink is not None:
        throughput_result._tracked_stream_ids = frozenset(
            int(stream_id) for stream_id in getattr(throughput_result, "outstanding_stream_ids", None) or []
        )
        sink.append(throughput_result)


def tracked_stream_ids(throughput_result: Any) -> frozenset[int]:
    recorded = getattr(throughput_result, "_tracked_stream_ids", None)
    if recorded is not None:
        return recorded
    return frozenset(int(stream_id) for stream_id in getattr(throughput_result, "outstanding_stream_ids", None) or [])


@dataclass(frozen=True)
class PhaseBoundaryDecision:
    proceed: bool
    reason: str
    outstanding_stream_ids: list[int] = field(default_factory=list)


def check_phase_boundary(throughput_result: Any | None) -> PhaseBoundaryDecision:
    outstanding = getattr(throughput_result, "outstanding_stream_ids", None)
    if not isinstance(outstanding, (list, tuple)) or not outstanding:
        return PhaseBoundaryDecision(
            proceed=True,
            reason="No outstanding throughput work; phase boundary clear.",
        )
    outstanding_ids = [int(stream_id) for stream_id in outstanding]
    return PhaseBoundaryDecision(
        proceed=False,
        reason=(
            f"Throughput phase has outstanding work on streams {outstanding_ids}: "
            f"those workers may still be executing queries and holding resources. "
            f"Refusing the next phase until termination is observed."
        ),
        outstanding_stream_ids=outstanding_ids,
    )


def await_quiescence(throughput_result: Any, timeout: float) -> bool:
    handles: dict[int, concurrent.futures.Future] = dict(getattr(throughput_result, "_outstanding_futures", None) or {})
    if not handles:
        unobservable = bool(getattr(throughput_result, "outstanding_stream_ids", None) or [])
        if unobservable and timeout > 0:
            time.sleep(timeout)
        return not unobservable

    done, not_done = concurrent.futures.wait(handles.values(), timeout=timeout)
    done_ids = {stream_id for stream_id, future in handles.items() if future in done}
    remaining = {stream_id: future for stream_id, future in handles.items() if future in not_done}

    outstanding = getattr(throughput_result, "outstanding_stream_ids", None)
    if isinstance(outstanding, list):
        remaining_ids = [stream_id for stream_id in outstanding if stream_id in remaining]
        outstanding[:] = remaining_ids

    resources = getattr(throughput_result, "outstanding_notes", None)
    if isinstance(resources, list) and done_ids:
        resources.append(f"Termination observed for streams {sorted(done_ids)}.")

    throughput_result._outstanding_futures = remaining

    if remaining:
        return False

    if isinstance(getattr(throughput_result, "cleanup_state", None), str):
        throughput_result.cleanup_state = "quiesced"
    logger.debug("Throughput outstanding work quiesced; phase boundary released.")
    return True
