from __future__ import annotations

import functools
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from benchbox.core.results.fetch_mode import EXCLUSION_DEPTH, ConversionClock
from benchbox.utils.clock import mono_time

TIMING_METHOD_LEGACY = 1
TIMING_METHOD_QUERY_ONLY = 2

REQUEST_KIND_QUERY = "query"
REQUEST_KIND_BEGIN = "begin"
REQUEST_KIND_COMMIT = "commit"
REQUEST_KIND_SETUP = "setup"


@contextmanager
def outside_query_timer() -> Iterator[None]:
    depth = EXCLUSION_DEPTH.get()
    token = EXCLUSION_DEPTH.set(depth + 1)
    started = mono_time()
    try:
        yield
    finally:
        EXCLUSION_DEPTH.reset(token)
        if depth == 0:
            ConversionClock.record_since(started)


def excluded_from_query_timer(function: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with outside_query_timer():
            return function(*args, **kwargs)

    return wrapper


def _query_field(query: Any, name: str) -> Any:
    return query.get(name) if isinstance(query, Mapping) else getattr(query, name, None)


def effective_timing_method(declared: int, queries: Iterable[Any]) -> int:
    if declared < TIMING_METHOD_QUERY_ONLY:
        return declared
    for query in queries:
        if _query_field(query, "status") != "SUCCESS" or _query_field(query, "test_type") == "maintenance":
            continue
        if _query_field(query, "request_count") is None or _query_field(query, "result_bytes") is None:
            return TIMING_METHOD_LEGACY
    return declared


@dataclass
class RecordedRequest:
    kind: str
    statement_id: str | None = None
    attempt: int = 1


class RequestLedger:
    def __init__(self) -> None:
        self._requests: list[RecordedRequest] = []

    @property
    def count(self) -> int:
        return len(self._requests)

    @property
    def requests(self) -> Sequence[RecordedRequest]:
        return tuple(self._requests)

    def add_request(
        self, kind: str = REQUEST_KIND_QUERY, statement_id: str | None = None, attempt: int = 1
    ) -> RecordedRequest:
        request = RecordedRequest(kind=kind, statement_id=statement_id, attempt=attempt)
        self._requests.append(request)
        return request

    def call(self, send: Callable[..., Any], *args: Any, kind: str = REQUEST_KIND_QUERY, **kwargs: Any) -> Any:
        self.add_request(kind)
        return send(*args, **kwargs)

    def statement_ids(self, kind: str | None = None) -> list[str]:
        return [
            request.statement_id
            for request in self._requests
            if request.statement_id is not None and (kind is None or request.kind == kind)
        ]


__all__ = [
    "REQUEST_KIND_BEGIN",
    "REQUEST_KIND_COMMIT",
    "REQUEST_KIND_QUERY",
    "REQUEST_KIND_SETUP",
    "TIMING_METHOD_LEGACY",
    "TIMING_METHOD_QUERY_ONLY",
    "RecordedRequest",
    "RequestLedger",
    "effective_timing_method",
    "excluded_from_query_timer",
    "outside_query_timer",
]
