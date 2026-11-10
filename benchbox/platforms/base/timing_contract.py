from __future__ import annotations

import dataclasses
import datetime
import logging
import statistics
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from benchbox.core.results.platform_timing import (
    DeferredPlatformTimingEntry,
    StatementTiming,
    active_deferred_platform_timing,
    build_platform_timing,
    history_lookup_window,
    is_throughput_stream_active,
    rows_result_bytes,
    unavailable_platform_timing,
)
from benchbox.core.results.timer_scope import (
    TIMING_METHOD_LEGACY,
    RecordedRequest,
    RequestLedger,
    effective_timing_method,
    outside_query_timer,
)

logger = logging.getLogger(__name__)


class TimingContractMixin:
    timing_method: int = TIMING_METHOD_LEGACY
    platform_timing_source: str | None = None
    platform_timing_definition: str = "server_complete"
    platform_history_deferrable: bool = True

    def fetch_platform_timings(self, connection: Any, statement_ids: Sequence[str]) -> Mapping[str, StatementTiming]:
        return {}

    def probe_statement_id(self, cursor: Any) -> str | None:
        return None

    def _statements_for(
        self, connection: Any, requests: Sequence[RecordedRequest]
    ) -> tuple[list[StatementTiming], str | None]:
        ids = [request.statement_id for request in requests if request.statement_id]
        fetched: Mapping[str, StatementTiming] = {}
        failure: str | None = None
        if ids:
            try:
                fetched = self.fetch_platform_timings(connection, ids)
            except Exception as exc:
                logger.debug("Platform timing lookup failed: %r", exc)
                failure = f"platform history lookup failed: {type(exc).__name__}"
        statements = []
        for request in requests:
            found = fetched.get(request.statement_id) if request.statement_id else None
            if found is None:
                statements.append(StatementTiming(request.statement_id, request.kind, request.attempt))
            else:
                statements.append(dataclasses.replace(found, kind=request.kind, attempt=request.attempt))
        return statements, failure

    def capture_platform_timing(
        self,
        connection: Any,
        requests: Sequence[RecordedRequest],
        *,
        client_ms: float | None,
        stream_id: int | None = None,
        is_throughput: bool = False,
    ) -> dict[str, Any] | None:
        source = self.platform_timing_source
        if source is None:
            return None
        if is_throughput or (stream_id is not None and stream_id > 0) or is_throughput_stream_active():
            statements = [StatementTiming(request.statement_id, request.kind, request.attempt) for request in requests]
            return unavailable_platform_timing(
                source,
                "skipped during throughput stream to preserve stream timing",
                statements,
            )
        deferred = active_deferred_platform_timing()
        if deferred is not None:
            statements = [StatementTiming(request.statement_id, request.kind, request.attempt) for request in requests]
            if not self.platform_history_deferrable:
                return unavailable_platform_timing(
                    source,
                    "skipped inside a measured phase: platform history is kept too briefly to look up afterwards",
                    statements,
                )
            record = unavailable_platform_timing(
                source,
                "platform history lookup deferred until the measured phase ends",
                statements,
            )
            deferred.add(DeferredPlatformTimingEntry(self, connection, list(requests), client_ms, record))
            return record
        with outside_query_timer():
            statements, failure = self._statements_for(connection, requests)
            if failure is not None:
                return unavailable_platform_timing(source, failure, statements)
            return build_platform_timing(source, statements, client_ms=client_ms)

    def resolve_deferred_platform_timing(self, connection: Any, entries: Sequence[DeferredPlatformTimingEntry]) -> None:
        source = self.platform_timing_source
        if source is None:
            return
        requests = [request for entry in entries for request in entry.requests]
        since = min(entry.queued_at for entry in entries) - datetime.timedelta(minutes=1)
        with history_lookup_window(since):
            statements, failure = self._statements_for(connection, requests)
        offset = 0
        for entry in entries:
            mine = statements[offset : offset + len(entry.requests)]
            offset += len(entry.requests)
            if failure is not None:
                resolved = unavailable_platform_timing(source, failure, mine)
            else:
                resolved = build_platform_timing(source, mine, client_ms=entry.client_ms)
            entry.record.clear()
            entry.record.update(resolved)

    def capture_probe_platform_timing(self, connection: Any, statement_ids: Sequence[str]) -> dict[str, Any] | None:
        source = self.platform_timing_source
        if source is None or not statement_ids:
            return None
        requests = [RecordedRequest(kind="query", statement_id=statement_id) for statement_id in statement_ids]
        statements, failure = self._statements_for(connection, requests)
        if failure is not None:
            return unavailable_platform_timing(source, failure, statements)
        timing = build_platform_timing(source, statements, client_ms=None)
        if timing.get("status") == "valid":
            per_statement = [statement.platform_ms for statement in statements if statement.platform_ms is not None]
            timing["platform_ms"] = round(min(per_statement), 3)
            for name in ("queue_ms", "compile_ms", "exec_ms"):
                values = [getattr(statement, name) for statement in statements if getattr(statement, name) is not None]
                if len(values) == len(statements):
                    timing[name] = round(statistics.median(values), 3)
        return timing

    def timing_fields_for(
        self,
        connection: Any,
        ledger: RequestLedger,
        *,
        rows: Iterable[Any] | None = None,
        result_bytes: int | None = None,
        elapsed_seconds: float | None = None,
        stream_id: int | None = None,
        is_throughput: bool = False,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {"request_count": ledger.count}
        with outside_query_timer():
            measured_bytes = result_bytes if result_bytes is not None else rows_result_bytes(rows)
        if measured_bytes is not None:
            fields["result_bytes"] = measured_bytes
        client_ms = None if elapsed_seconds is None else elapsed_seconds * 1000.0
        platform_timing = self.capture_platform_timing(
            connection,
            ledger.requests,
            client_ms=client_ms,
            stream_id=stream_id,
            is_throughput=is_throughput,
        )
        if platform_timing is not None:
            fields["platform_timing"] = platform_timing
        return fields

    def stamp_timing_contract(self, result: Any) -> None:
        result.timing_method = effective_timing_method(self.timing_method, getattr(result, "query_results", None) or ())
        if self.platform_timing_source is not None:
            result.run_platform_timing = {
                "source": self.platform_timing_source,
                "definition": self.platform_timing_definition,
            }


__all__ = ["TimingContractMixin"]
