from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from typing import Any

from benchbox.core.results.models import QueryExecution
from benchbox.core.results.query_execution import query_execution_to_legacy_dict
from benchbox.utils.clock import elapsed_seconds, mono_time

logger = logging.getLogger(__name__)


def join_explain_rows(plan_rows: Sequence[Any] | None) -> str | None:
    if not plan_rows:
        return None
    parts: list[str] = []
    for row in plan_rows:
        if row is None:
            continue
        if isinstance(row, dict):
            values = list(row.values())
            cell = values[0] if len(values) == 1 else row
        elif isinstance(row, (str, bytes, bytearray)):
            cell = row
        else:
            try:
                cell = row[0]
            except IndexError:
                cell = None
            except (TypeError, KeyError):
                cell = row
        if cell is None:
            continue
        if isinstance(cell, (dict, list)):
            parts.append(json.dumps(cell))
        elif isinstance(cell, (bytes, bytearray)):
            parts.append(bytes(cell).decode("utf-8", errors="replace"))
        else:
            parts.append(str(cell))
    text = "\n".join(parts)
    return text or None


def get_query_plan_from_cursor(
    connection: Any,
    query: str,
    explain_prefix: str = "EXPLAIN",
    logger: logging.Logger | None = None,
) -> str | None:
    log = logger or logging.getLogger(__name__)
    cursor = connection.cursor()
    try:
        cursor.execute(f"{explain_prefix} {query}")
        plan_rows = cursor.fetchall()
        return join_explain_rows(plan_rows) or ""
    except Exception as e:
        log.warning("Could not get query plan via EXPLAIN: %s", e)
        return None
    finally:
        cursor.close()


def execute_sql_query(
    connection: Any,
    query: str,
    query_id: str,
    *,
    log_verbose: Callable[[str], None],
    build_query_result_with_validation: Callable[..., dict[str, Any]],
    benchmark_type: str | None = None,
    scale_factor: float | None = None,
    validate_row_count: bool = True,
    stream_id: int | None = None,
) -> dict[str, Any]:
    start_time = mono_time()
    log_verbose(f"Executing query {query_id}")

    _owns_cursor = callable(getattr(connection, "cursor", None))
    cursor = connection.cursor() if _owns_cursor else connection
    try:
        cursor.execute(query)
        results = [] if getattr(cursor, "description", ()) is None else cursor.fetchall()

        execution_time = elapsed_seconds(start_time)
        actual_row_count = len(results)

        validation_result = None
        if validate_row_count and benchmark_type:
            from benchbox.core.validation.query_validation import QueryValidator

            validator = QueryValidator()
            validation_result = validator.validate_query_result(
                benchmark_type=benchmark_type,
                query_id=query_id,
                actual_row_count=actual_row_count,
                scale_factor=scale_factor,
                stream_id=stream_id,
            )

        from benchbox.core.results.result_digest import compute_result_digest, result_digest_enabled

        result_digest = compute_result_digest(results) if result_digest_enabled() and stream_id in (None, 0) else None

        return build_query_result_with_validation(
            query_id=query_id,
            execution_time=execution_time,
            actual_row_count=actual_row_count,
            first_row=results[0] if results else None,
            validation_result=validation_result,
            result_digest=result_digest,
            materialized_rows=results,
        )

    except Exception as exc:
        execution_time = elapsed_seconds(start_time)
        try:
            real_conn = connection if _owns_cursor else getattr(cursor, "connection", None)
            if real_conn is not None and hasattr(real_conn, "rollback"):
                real_conn.rollback()
        except Exception:
            pass
        execution = QueryExecution(
            query_id=query_id,
            stream_id=None,
            execution_order=None,
            execution_time_seconds=execution_time,
            status="FAILED",
            rows_returned=0,
            error_message=str(exc),
            error_type=type(exc).__name__,
            iteration=None,
            run_type=None,
        )
        return query_execution_to_legacy_dict(
            execution,
            include_milliseconds=False,
            include_seconds=True,
            error_field="error",
        )
    finally:
        if _owns_cursor:
            cursor.close()
