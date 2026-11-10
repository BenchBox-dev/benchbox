from __future__ import annotations

import datetime as _dt
import decimal
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from benchbox.core.results.timer_scope import REQUEST_KIND_QUERY, TIMING_METHOD_QUERY_ONLY, RequestLedger
from benchbox.core.results.timing_fields import NETWORKED_ENDPOINT_CLASSES, PLATFORM_TIMING_DEFINITIONS

PROBE_SAMPLE_COUNT = 5
CLOCK_SKEW_TOLERANCE_MS = 1.0

NET_ESTIMATE_ELIGIBLE = "eligible"
NET_ESTIMATE_EMBEDDED = "embedded"
NET_ESTIMATE_ENDPOINT_UNKNOWN = "endpoint_unknown"
NET_ESTIMATE_TIMING_METHOD = "timing_method"
NET_ESTIMATE_PROBE_SAMPLES = "probe_samples"
NET_ESTIMATE_PROBE_UNVERIFIED = "probe_unverified"
NET_ESTIMATE_FLOOR_SUSPECT = "floor_suspect"

NET_ESTIMATE_CONFORMANCE: Mapping[str, str] = {}

_FIXED_WIDTH_BYTES = 8


def _value_bytes(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value.encode("utf-8", errors="replace"))
    if isinstance(value, (bytes, bytearray, memoryview)):
        return len(value)
    if isinstance(value, bool):
        return 1
    if isinstance(value, (int, float, _dt.date, _dt.time, _dt.timedelta)):
        return _FIXED_WIDTH_BYTES
    if isinstance(value, decimal.Decimal):
        return len(str(value))
    if isinstance(value, (list, tuple)):
        return sum(_value_bytes(item) for item in value)
    if isinstance(value, Mapping):
        return sum(_value_bytes(key) + _value_bytes(item) for key, item in value.items())
    return len(str(value).encode("utf-8", errors="replace"))


def rows_result_bytes(rows: Iterable[Any] | None) -> int | None:
    if rows is None:
        return None
    table = getattr(rows, "table", None)
    nbytes = getattr(table, "nbytes", None)
    if isinstance(nbytes, int):
        return nbytes
    return sum(_value_bytes(row) for row in rows)


def arrow_result_bytes(table: Any) -> int | None:
    nbytes = getattr(table, "nbytes", None)
    return nbytes if isinstance(nbytes, int) else None


@dataclass(frozen=True)
class StatementTiming:
    statement_id: str | None
    kind: str = REQUEST_KIND_QUERY
    attempt: int = 1
    fields: Mapping[str, float | int | bool | None] = field(default_factory=dict)
    platform_ms: float | None = None
    queue_ms: float | None = None
    compile_ms: float | None = None
    exec_ms: float | None = None
    cache_hit: bool | None = None


def _request_entry(statement: StatementTiming) -> dict[str, Any]:
    entry: dict[str, Any] = {"attempt": statement.attempt, "kind": statement.kind, "fields": dict(statement.fields)}
    if statement.statement_id is not None:
        entry["id"] = statement.statement_id
    return entry


def _sum_present(values: Sequence[float | None]) -> float | None:
    if not values or any(value is None for value in values):
        return None
    return round(sum(value for value in values if value is not None), 3)


def unavailable_platform_timing(source: str, reason: str, statements: Sequence[StatementTiming] = ()) -> dict[str, Any]:
    timing: dict[str, Any] = {"source": source, "status": "unavailable", "reason": reason}
    if statements:
        timing["requests"] = [_request_entry(statement) for statement in statements]
    return timing


def build_platform_timing(
    source: str,
    statements: Sequence[StatementTiming],
    *,
    client_ms: float | None,
) -> dict[str, Any]:
    if not statements:
        return unavailable_platform_timing(source, "no platform statement was recorded for this query")
    missing_id = [index for index, statement in enumerate(statements) if not statement.statement_id]
    if missing_id:
        return unavailable_platform_timing(
            source, f"platform statement id missing for request {missing_id[0]}", statements
        )
    missing_time = [index for index, statement in enumerate(statements) if statement.platform_ms is None]
    if missing_time:
        return unavailable_platform_timing(
            source, f"platform history returned no timing for request {missing_time[0]}", statements
        )
    timing: dict[str, Any] = {
        "source": source,
        "requests": [_request_entry(statement) for statement in statements],
        "platform_ms": _sum_present([statement.platform_ms for statement in statements]),
    }
    for name in ("queue_ms", "compile_ms", "exec_ms"):
        total = _sum_present([getattr(statement, name) for statement in statements])
        if total is not None:
            timing[name] = total
    cache_hits = [statement.cache_hit for statement in statements if statement.cache_hit is not None]
    if cache_hits:
        timing["cache_hit"] = all(cache_hits)
    platform_ms = timing["platform_ms"]
    if client_ms is not None and platform_ms > client_ms + CLOCK_SKEW_TOLERANCE_MS:
        timing["status"] = "suspect"
        timing["reason"] = "platform_ms exceeds the client-measured query time"
    else:
        timing["status"] = "valid"
    return timing


def finalize_query_timing_fields(
    result: dict[str, Any],
    ledger: RequestLedger,
    *,
    result_bytes: int | None,
    platform_timing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result["request_count"] = ledger.count
    if result_bytes is not None:
        result["result_bytes"] = result_bytes
    if platform_timing is not None:
        result["platform_timing"] = dict(platform_timing)
    return result


def summarize_run_platform_timing(
    queries: Iterable[Mapping[str, Any]],
    *,
    source: str,
    definition: str,
) -> dict[str, Any] | None:
    if definition not in PLATFORM_TIMING_DEFINITIONS:
        raise ValueError(f"platform timing definition must be one of {sorted(PLATFORM_TIMING_DEFINITIONS)}")
    measured = [
        query
        for query in queries
        if query.get("status") == "SUCCESS"
        and query.get("run_type", "measurement") == "measurement"
        and query.get("test_type") != "maintenance"
    ]
    if not any(query.get("platform_timing") is not None for query in measured):
        return None
    valid = sum(1 for query in measured if (query.get("platform_timing") or {}).get("status") == "valid")
    return {"source": source, "definition": definition, "coverage": round(valid / len(measured), 4)}


def _probe_floor_ms(client_link: Mapping[str, Any] | None) -> tuple[int, float | None]:
    overhead = client_link.get("statement_overhead_ms") if isinstance(client_link, Mapping) else None
    if not isinstance(overhead, Mapping):
        return 0, None
    samples = overhead.get("samples")
    floor = overhead.get("min")
    if isinstance(samples, bool) or not isinstance(samples, int):
        samples = 0
    if isinstance(floor, bool) or not isinstance(floor, (int, float)):
        return samples, None
    return samples, float(floor)


def _floor_is_suspect(queries: Iterable[Mapping[str, Any]], floor_ms: float) -> bool:
    for query in queries:
        if query.get("status") != "SUCCESS" or query.get("run_type", "measurement") != "measurement":
            continue
        if query.get("test_type") == "maintenance":
            continue
        timing = query.get("platform_timing") or {}
        request_count = query.get("request_count")
        elapsed = query.get("ms")
        platform_ms = timing.get("platform_ms")
        if not isinstance(request_count, int) or not isinstance(elapsed, (int, float)) or platform_ms is None:
            return True
        if timing.get("status") != "valid":
            return True
        if request_count * floor_ms >= elapsed - platform_ms:
            return True
    return False


def resolve_net_estimate_status(
    *,
    platform: str | None,
    timing_method: int,
    endpoint_class: str | None,
    client_link: Mapping[str, Any] | None,
    queries: Sequence[Mapping[str, Any]],
) -> str:
    if endpoint_class == "embedded_process":
        return NET_ESTIMATE_EMBEDDED
    if endpoint_class not in NETWORKED_ENDPOINT_CLASSES:
        return NET_ESTIMATE_ENDPOINT_UNKNOWN
    if timing_method < TIMING_METHOD_QUERY_ONLY:
        return NET_ESTIMATE_TIMING_METHOD
    samples, floor_ms = _probe_floor_ms(client_link)
    if floor_ms is None or samples < PROBE_SAMPLE_COUNT:
        return NET_ESTIMATE_PROBE_SAMPLES
    probe_timing = client_link.get("platform_timing") if isinstance(client_link, Mapping) else None
    if (
        not platform
        or platform.lower() not in NET_ESTIMATE_CONFORMANCE
        or not isinstance(probe_timing, Mapping)
        or probe_timing.get("status") != "valid"
    ):
        return NET_ESTIMATE_PROBE_UNVERIFIED
    if _floor_is_suspect(queries, floor_ms):
        return NET_ESTIMATE_FLOOR_SUSPECT
    return NET_ESTIMATE_ELIGIBLE


__all__ = [
    "CLOCK_SKEW_TOLERANCE_MS",
    "NET_ESTIMATE_CONFORMANCE",
    "NET_ESTIMATE_ELIGIBLE",
    "NET_ESTIMATE_EMBEDDED",
    "NET_ESTIMATE_ENDPOINT_UNKNOWN",
    "NET_ESTIMATE_FLOOR_SUSPECT",
    "NET_ESTIMATE_PROBE_SAMPLES",
    "NET_ESTIMATE_PROBE_UNVERIFIED",
    "NET_ESTIMATE_TIMING_METHOD",
    "PROBE_SAMPLE_COUNT",
    "StatementTiming",
    "arrow_result_bytes",
    "build_platform_timing",
    "finalize_query_timing_fields",
    "resolve_net_estimate_status",
    "rows_result_bytes",
    "summarize_run_platform_timing",
    "unavailable_platform_timing",
]
