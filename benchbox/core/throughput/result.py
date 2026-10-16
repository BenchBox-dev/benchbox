from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

THROUGHPUT_FIRST_STREAM_ID = 1
THROUGHPUT_STREAM_NUMBERING_BASIS = "tpc_spec_throughput_streams_1_to_s"


def throughput_stream_ids(num_streams: int) -> range:
    return range(THROUGHPUT_FIRST_STREAM_ID, THROUGHPUT_FIRST_STREAM_ID + max(num_streams, 0))


def throughput_stream_numbering() -> dict[str, Any]:
    return {
        "basis": THROUGHPUT_STREAM_NUMBERING_BASIS,
        "first_stream_id": THROUGHPUT_FIRST_STREAM_ID,
    }


def throughput_stream_succeeded(stream: Any) -> bool:
    executed = getattr(stream, "queries_executed", None)
    successful = getattr(stream, "queries_successful", None)
    if not isinstance(executed, int) or not isinstance(successful, int) or executed <= 0:
        return False
    failed = getattr(stream, "queries_failed", None)
    if not isinstance(failed, int):
        failed = max(executed - successful, 0)
    return bool(getattr(stream, "success", False)) and failed == 0 and successful == executed


def throughput_result_succeeded(result: Any, requested_streams: int | None = None) -> bool:
    if requested_streams is None:
        requested_streams = getattr(getattr(result, "config", None), "num_streams", None)
    if not isinstance(requested_streams, int) or requested_streams <= 0:
        return False

    stream_results = list(getattr(result, "stream_results", []) or [])
    refresh_stream = getattr(result, "refresh_stream", None)
    return (
        getattr(result, "streams_executed", None) == requested_streams
        and getattr(result, "streams_successful", None) == requested_streams
        and len(stream_results) == requested_streams
        and not (getattr(result, "errors", []) or [])
        and (refresh_stream is None or getattr(refresh_stream, "success", False))
        and all(throughput_stream_succeeded(stream) for stream in stream_results)
    )


@dataclass
class ThroughputStreamResult:
    stream_id: int
    start_time: float
    end_time: float
    duration: float
    queries_executed: int
    queries_successful: int
    queries_failed: int
    query_results: list[dict[str, Any]] = field(default_factory=list)
    success: bool = True
    error: Optional[str] = None
    start_wall_time: str = ""
    end_wall_time: str = ""


@dataclass
class ThroughputResult:
    start_time: str
    end_time: str
    total_time: float
    throughput_at_size: float | None
    streams_executed: int
    streams_successful: int
    stream_results: list[ThroughputStreamResult] = field(default_factory=list)
    query_throughput: float = 0.0
    success: bool = True
    errors: list[str] = field(default_factory=list)
    outstanding_stream_ids: list[int] = field(default_factory=list)
    cancelled_stream_ids: list[int] = field(default_factory=list)
    outstanding_notes: list[str] = field(default_factory=list)
    cleanup_state: str = "complete"
    stream_numbering: dict[str, Any] = field(default_factory=throughput_stream_numbering)

    @property
    def has_outstanding_work(self) -> bool:
        return bool(self.outstanding_stream_ids)
