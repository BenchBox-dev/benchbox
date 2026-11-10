from __future__ import annotations

import contextlib
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from benchbox.core.contracts import PlanCaptureRuntime, as_connection_factory, as_plan_capture_runtime


@dataclass
class PlanCapturePhaseResult:
    plans: dict[str, Any] = field(default_factory=dict)
    fingerprints: dict[str, str] = field(default_factory=dict)
    normalized_fingerprints: dict[str, str] = field(default_factory=dict)
    per_query_capture_ms: dict[str, float] = field(default_factory=dict)
    total_capture_ms: float = 0.0
    captured: int = 0
    failed: int = 0


def _normalize_queries(queries: Mapping[str, str] | Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    pairs: Iterable[tuple[str, str]] = queries.items() if isinstance(queries, Mapping) else queries
    return [(str(query_id), str(sql)) for query_id, sql in pairs]


def run_plan_capture_phase(
    adapter: PlanCaptureRuntime,
    queries: Mapping[str, str] | Iterable[tuple[str, str]],
    *,
    connection: Any | None = None,
    connection_config: Mapping[str, Any] | None = None,
    analyze_plans: bool = False,
    close_connection: bool | None = None,
) -> PlanCapturePhaseResult:
    adapter = as_plan_capture_runtime(adapter)
    items = _normalize_queries(queries)
    result = PlanCapturePhaseResult()

    owns_connection = connection is None
    connection_factory = as_connection_factory(adapter) if owns_connection else None
    caller_supplied_connection = not owns_connection
    owned_connections: list[Any] = []
    if close_connection is None:
        close_connection = owns_connection

    saved = {
        "analyze_plans": getattr(adapter, "analyze_plans", True),
        "capture_plans": getattr(adapter, "capture_plans", False),
    }

    phase_start = time.perf_counter()
    try:
        if owns_connection:
            assert connection_factory is not None
            connection = connection_factory.create_connection(**(dict(connection_config) if connection_config else {}))
            owned_connections.append(connection)

        adapter.analyze_plans = analyze_plans
        adapter.capture_plans = True

        last_index = len(items) - 1
        for index, (query_id, sql) in enumerate(items):
            failure_count_before = len(getattr(adapter, "plan_capture_errors", []))
            plan, capture_ms = adapter.capture_query_plan(connection, sql, query_id)
            result.per_query_capture_ms[query_id] = capture_ms
            if plan is not None:
                result.plans[query_id] = plan
                result.fingerprints[query_id] = plan.plan_fingerprint
                if getattr(adapter, "normalize_plan_literals", False):
                    result.normalized_fingerprints[query_id] = plan.normalized_fingerprint
                result.captured += 1
            else:
                result.failed += 1
            new_failures = getattr(adapter, "plan_capture_errors", [])[failure_count_before:]
            if index != last_index and any(failure.get("reason") == "timeout" for failure in new_failures):
                if caller_supplied_connection:
                    result.failed += last_index - index
                    break
                if owns_connection and connection is not None:
                    with contextlib.suppress(Exception):
                        connection.close()
                assert connection_factory is not None
                connection = connection_factory.create_connection(
                    **(dict(connection_config) if connection_config else {})
                )
                owned_connections.append(connection)
                owns_connection = True
    finally:
        adapter.analyze_plans = saved["analyze_plans"]
        adapter.capture_plans = saved["capture_plans"]
        if close_connection and connection is not None and connection not in owned_connections:
            with contextlib.suppress(Exception):
                connection.close()
        for owned_connection in owned_connections:
            with contextlib.suppress(Exception):
                owned_connection.close()

    result.total_capture_ms = (time.perf_counter() - phase_start) * 1000
    return result


PLAN_CAPTURE_PROPAGATION_KEYS = (
    "_plan_capture_key",
    "query_plan",
    "plan_fingerprint",
    "plan_fingerprint_normalized",
    "plan_capture_time_ms",
)


def propagate_plan_capture_fields(source: Mapping[str, Any], target: dict[str, Any]) -> None:
    for key in PLAN_CAPTURE_PROPAGATION_KEYS:
        value = source.get(key)
        if value is not None:
            target[key] = value


def propagate_query_execution_metadata(source: Mapping[str, Any], target: dict[str, Any]) -> None:
    propagate_plan_capture_fields(source, target)
    resource_usage = source.get("resource_usage")
    if resource_usage is not None:
        target["resource_usage"] = resource_usage
    for key in ("fetch_mode", "request_count", "result_bytes", "platform_timing"):
        value = source.get(key)
        if value is not None:
            target[key] = value
