from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

TIMING_FIELDS_SCHEMA_VERSION = (2, 4)
QUERY_TIMING_FIELDS = ("request_count", "result_bytes", "platform_timing")
RUN_TIMING_FIELDS = ("timing_method", "net_estimate_status", "platform_timing")
CLIENT_LINK_TIMING_FIELDS = ("probed_at",)

KNOWN_TIMING_METHODS = frozenset({1, 2})
NET_ESTIMATE_STATUSES = frozenset(
    {
        "eligible",
        "embedded",
        "endpoint_unknown",
        "timing_method",
        "probe_samples",
        "probe_unverified",
        "floor_suspect",
    }
)
PLATFORM_TIMING_STATUSES = frozenset({"valid", "suspect", "unavailable"})
PLATFORM_TIMING_DEFINITIONS = frozenset({"server_complete", "includes_fetch"})
PLATFORM_TIMING_FIELDS = frozenset(
    {"source", "requests", "platform_ms", "queue_ms", "compile_ms", "exec_ms", "cache_hit", "status", "reason"}
)
PLATFORM_TIMING_DURATION_FIELDS = ("platform_ms", "queue_ms", "compile_ms", "exec_ms")
PLATFORM_REQUEST_FIELDS = frozenset({"id", "attempt", "kind", "fields"})
PLATFORM_REQUEST_KINDS = frozenset({"query", "begin", "commit", "setup"})
RUN_PLATFORM_TIMING_FIELDS = frozenset({"source", "definition", "coverage"})


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _is_non_negative_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _request_problems(index: int, request: Any) -> list[str]:
    prefix = f"requests[{index}]"
    if not isinstance(request, Mapping):
        return [f"{prefix} must be an object"]
    problems = [f"{prefix} has unknown field {key!r}" for key in sorted(set(request) - PLATFORM_REQUEST_FIELDS)]
    request_id = request.get("id")
    if request_id is not None and not _non_empty_string(request_id):
        problems.append(f"{prefix}.id must be a non-empty string")
    attempt = request.get("attempt")
    if not _is_non_negative_int(attempt) or attempt < 1:
        problems.append(f"{prefix}.attempt must be a positive int")
    if request.get("kind") not in PLATFORM_REQUEST_KINDS:
        problems.append(f"{prefix}.kind must be one of {sorted(PLATFORM_REQUEST_KINDS)}")
    fields = request.get("fields")
    if not isinstance(fields, Mapping):
        problems.append(f"{prefix}.fields must be an object")
    else:
        for name, value in fields.items():
            if not isinstance(name, str):
                problems.append(f"{prefix}.fields keys must be strings")
            elif value is not None and not isinstance(value, (str, bool)) and not _is_number(value):
                problems.append(f"{prefix}.fields.{name} must be a finite number, string, boolean or null")
    return problems


def platform_timing_problems(value: Any) -> list[str]:
    if not isinstance(value, Mapping):
        return ["must be an object"]
    problems = [f"has unknown field {key!r}" for key in sorted(set(value) - PLATFORM_TIMING_FIELDS)]
    status = value.get("status")
    if status not in PLATFORM_TIMING_STATUSES:
        problems.append(f"status must be one of {sorted(PLATFORM_TIMING_STATUSES)}, got {status!r}")
    source = value.get("source")
    if source is not None and not _non_empty_string(source):
        problems.append("source must be a non-empty string")
    for key in PLATFORM_TIMING_DURATION_FIELDS:
        duration = value.get(key)
        if duration is not None and (not _is_number(duration) or duration < 0):
            problems.append(f"{key} must be a non-negative finite number, got {duration!r}")
    if status == "valid" and value.get("platform_ms") is None:
        problems.append("status 'valid' requires platform_ms")
    reason = value.get("reason")
    if status in {"suspect", "unavailable"} and not _non_empty_string(reason):
        problems.append(f"status {status!r} requires a reason")
    elif reason is not None and not _non_empty_string(reason):
        problems.append("reason must be a non-empty string")
    cache_hit = value.get("cache_hit")
    if cache_hit is not None and not isinstance(cache_hit, bool):
        problems.append("cache_hit must be a boolean")
    requests = value.get("requests")
    if requests is not None:
        if not isinstance(requests, list):
            problems.append("requests must be a list")
        else:
            for index, request in enumerate(requests):
                problems.extend(_request_problems(index, request))
    return problems


def run_timing_problems(run: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    timing_method = run.get("timing_method")
    if timing_method is not None and (isinstance(timing_method, bool) or timing_method not in KNOWN_TIMING_METHODS):
        problems.append(f"timing_method must be one of {sorted(KNOWN_TIMING_METHODS)}, got {timing_method!r}")
    net_estimate_status = run.get("net_estimate_status")
    if net_estimate_status is not None and net_estimate_status not in NET_ESTIMATE_STATUSES:
        problems.append(
            f"net_estimate_status must be one of {sorted(NET_ESTIMATE_STATUSES)}, got {net_estimate_status!r}"
        )
    platform_timing = run.get("platform_timing")
    if platform_timing is None:
        return problems
    if not isinstance(platform_timing, Mapping):
        return [*problems, "platform_timing must be an object"]
    problems.extend(
        f"platform_timing has unknown field {key!r}"
        for key in sorted(set(platform_timing) - RUN_PLATFORM_TIMING_FIELDS)
    )
    if not _non_empty_string(platform_timing.get("source")):
        problems.append("platform_timing.source must be a non-empty string")
    definition = platform_timing.get("definition")
    if definition not in PLATFORM_TIMING_DEFINITIONS:
        problems.append(
            f"platform_timing.definition must be one of {sorted(PLATFORM_TIMING_DEFINITIONS)}, got {definition!r}"
        )
    coverage = platform_timing.get("coverage")
    if not _is_number(coverage) or not 0 <= coverage <= 1:
        problems.append(f"platform_timing.coverage must be a number from 0 to 1, got {coverage!r}")
    return problems


def bundle_timing_field_paths(payload: Mapping[str, Any]) -> list[str]:
    paths: list[str] = []
    run = payload.get("run")
    if isinstance(run, Mapping):
        paths.extend(f"run.{key}" for key in RUN_TIMING_FIELDS if key in run)
    environment = payload.get("environment")
    client_link = environment.get("client_link") if isinstance(environment, Mapping) else None
    if isinstance(client_link, Mapping):
        paths.extend(f"environment.client_link.{key}" for key in CLIENT_LINK_TIMING_FIELDS if key in client_link)
    queries = payload.get("queries")
    if isinstance(queries, list):
        for index, query in enumerate(queries):
            if isinstance(query, Mapping):
                paths.extend(f"queries[{index}].{key}" for key in QUERY_TIMING_FIELDS if key in query)
    return paths


def supports_timing_fields(version_tuple: tuple[int, ...] | None) -> bool:
    return version_tuple is not None and version_tuple >= TIMING_FIELDS_SCHEMA_VERSION


__all__ = [
    "CLIENT_LINK_TIMING_FIELDS",
    "KNOWN_TIMING_METHODS",
    "NET_ESTIMATE_STATUSES",
    "PLATFORM_TIMING_DEFINITIONS",
    "PLATFORM_TIMING_STATUSES",
    "QUERY_TIMING_FIELDS",
    "RUN_TIMING_FIELDS",
    "TIMING_FIELDS_SCHEMA_VERSION",
    "bundle_timing_field_paths",
    "platform_timing_problems",
    "run_timing_problems",
    "supports_timing_fields",
]
