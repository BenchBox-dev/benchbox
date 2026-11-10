# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License.

from __future__ import annotations

import logging
import statistics
import threading
from collections.abc import Callable
from typing import Any

from benchbox.core.results.platform_timing import PROBE_SAMPLE_COUNT
from benchbox.utils.clock import elapsed_seconds, mono_time

logger = logging.getLogger(__name__)

StatementIdOf = Callable[[Any], str | None]


def _allowlisted_error(exc: BaseException) -> dict[str, Any]:
    logger.debug("Statement overhead probe failed: %r", exc)
    return {
        "collection_status": "partial",
        "source": "unavailable",
        "collection_error_class": type(exc).__name__,
        "collection_error_message": f"{type(exc).__name__}: statement overhead probe failed",
    }


def _run_samples(
    execute_one: Callable[[], Any], sample_count: int, statement_id_of: StatementIdOf | None
) -> tuple[list[float], list[str]]:
    execute_one()
    samples: list[float] = []
    statement_ids: list[str] = []
    for _ in range(sample_count):
        query_start = mono_time()
        handle = execute_one()
        samples.append(elapsed_seconds(query_start) * 1000.0)
        statement_id = statement_id_of(handle) if statement_id_of is not None else None
        if statement_id:
            statement_ids.append(statement_id)
    return samples, statement_ids


def _sample_via_cursor(
    connection: Any, sample_count: int, statement_id_of: StatementIdOf | None
) -> tuple[list[float], list[str]]:
    cursor = connection.cursor()
    try:

        def execute_one() -> Any:
            cursor.execute("SELECT 1")
            fetchall = getattr(cursor, "fetchall", None)
            if callable(fetchall):
                fetchall()
            return cursor

        return _run_samples(execute_one, sample_count, statement_id_of)
    finally:
        close = getattr(cursor, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def _sample_via_query_api(
    connection: Any, timeout_seconds: float, sample_count: int, statement_id_of: StatementIdOf | None
) -> tuple[list[float], list[str]]:
    per_statement_timeout = max(0.5, timeout_seconds / (sample_count + 1))

    def execute_one() -> Any:
        job = connection.query("SELECT 1")
        result = getattr(job, "result", None)
        if callable(result):
            result(timeout=per_statement_timeout)
        elif hasattr(job, "__iter__"):
            list(job)
        return job

    return _run_samples(execute_one, sample_count, statement_id_of)


def probe_statement_overhead(
    connection: Any,
    timeout_seconds: float = 5.0,
    sample_count: int = PROBE_SAMPLE_COUNT,
    statement_id_of: StatementIdOf | None = None,
) -> dict[str, Any]:
    if connection is None:
        return {
            "collection_status": "partial",
            "source": "unavailable",
            "collection_error_class": "ValueError",
            "collection_error_message": "ValueError: statement overhead probe failed",
        }

    cursor_factory = getattr(connection, "cursor", None)
    query_api = getattr(connection, "query", None)
    if not callable(cursor_factory) and not callable(query_api):
        return _allowlisted_error(
            AttributeError(f"{type(connection).__name__} connection has no callable cursor() or query() method")
        )

    outcome: dict[str, Any] = {}

    def _sample() -> None:
        try:
            rollback = getattr(connection, "rollback", None)
            if callable(rollback):
                try:
                    rollback()
                except Exception as exc:
                    logger.debug("Link probe pre-rollback skipped: %r", exc)
            if callable(cursor_factory):
                samples, statement_ids = _sample_via_cursor(connection, sample_count, statement_id_of)
            else:
                samples, statement_ids = _sample_via_query_api(
                    connection, timeout_seconds, sample_count, statement_id_of
                )
            if not samples:
                raise RuntimeError("No statement overhead samples collected")
            outcome["result"] = {
                "collection_status": "available",
                "source": "observed",
                "statement_overhead_ms": {
                    "samples": len(samples),
                    "min": round(min(samples), 3),
                    "median": round(statistics.median(samples), 3),
                },
                "statement_ids": statement_ids,
            }
        except Exception as exc:
            outcome["result"] = _allowlisted_error(exc)

    worker = threading.Thread(target=_sample, name="benchbox-link-probe", daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        return _allowlisted_error(TimeoutError(f"Statement overhead probe timed out after {timeout_seconds}s"))
    result = outcome.get("result")
    if not isinstance(result, dict):
        return _allowlisted_error(RuntimeError("Statement overhead probe produced no result"))
    return result


__all__ = [
    "probe_statement_overhead",
]
