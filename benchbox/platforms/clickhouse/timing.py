from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from benchbox.core.results.platform_timing import StatementTiming
from benchbox.core.results.timer_scope import TIMING_METHOD_QUERY_ONLY

logger = logging.getLogger(__name__)

QUERY_LOG_SOURCE = "clickhouse.system.query_log"
QUERY_LOG_POLL_ATTEMPTS = 6
QUERY_LOG_POLL_INTERVAL_SECONDS = 0.5
CLOUD_QUERY_LOG_TABLE = "clusterAllReplicas(default, system.query_log)"
SERVER_QUERY_LOG_TABLE = "system.query_log"
QUERY_LOG_FIELDS = (
    "query_duration_ms",
    "read_rows",
    "read_bytes",
    "written_rows",
    "written_bytes",
    "result_rows",
    "result_bytes",
    "memory_usage",
)
_STATEMENT_ID = re.compile(r"^[0-9A-Za-z-]{1,64}$")


class _ProbeCursor:
    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self._rows: Any = []
        self.query_id: str | None = None

    def execute(self, sql: str) -> None:
        self.query_id = str(uuid.uuid4())
        self._rows = self._connection.execute(sql, query_id=self.query_id)

    def fetchall(self) -> Any:
        return list(self._rows or [])

    def close(self) -> None:
        self._rows = []


class _ProbeConnection:
    def __init__(self, connection: Any) -> None:
        self.wrapped = connection

    def cursor(self) -> _ProbeCursor:
        return _ProbeCursor(self.wrapped)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.wrapped, name)


def _unwrap(connection: Any) -> Any:
    return connection.wrapped if isinstance(connection, _ProbeConnection) else connection


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class ClickHouseTimingMixin:
    timing_method = TIMING_METHOD_QUERY_ONLY
    platform_timing_definition = "includes_fetch"

    @property
    def platform_timing_source(self) -> str | None:
        return None if self.deployment_mode == "local" else QUERY_LOG_SOURCE

    def new_statement_id(self) -> str | None:
        return str(uuid.uuid4()) if self.platform_timing_source is not None else None

    def probe_statement_id(self, cursor: Any) -> str | None:
        return getattr(cursor, "query_id", None)

    def _build_client_link_metadata(self, connection: Any, run_config: Mapping[str, Any]) -> dict[str, Any]:
        if self.platform_timing_source is not None:
            connection = _ProbeConnection(connection)
        return super()._build_client_link_metadata(connection, run_config)

    def _query_log_table(self) -> str:
        return CLOUD_QUERY_LOG_TABLE if self.deployment_mode == "cloud" else SERVER_QUERY_LOG_TABLE

    def _flush_query_log(self, connection: Any) -> None:
        try:
            connection.execute("SYSTEM FLUSH LOGS")
        except Exception as exc:
            logger.debug("ClickHouse SYSTEM FLUSH LOGS failed: %r", exc)

    def fetch_platform_timings(self, connection: Any, statement_ids: Sequence[str]) -> Mapping[str, StatementTiming]:
        connection = _unwrap(connection)
        for statement_id in statement_ids:
            if not _STATEMENT_ID.match(statement_id):
                raise ValueError("unsupported ClickHouse query id")
        id_list = ", ".join(f"'{statement_id}'" for statement_id in statement_ids)
        sql = (
            f"SELECT query_id, {', '.join(QUERY_LOG_FIELDS)} FROM {self._query_log_table()} "
            f"WHERE type = 'QueryFinish' AND event_date >= today() - 1 AND query_id IN ({id_list})"
        )
        found: dict[str, StatementTiming] = {}
        wanted = set(statement_ids)
        for attempt in range(QUERY_LOG_POLL_ATTEMPTS):
            if attempt:
                time.sleep(QUERY_LOG_POLL_INTERVAL_SECONDS)
            self._flush_query_log(connection)
            for row in connection.execute(sql) or []:
                statement_id = str(row[0])
                fields = {name: _number(value) for name, value in zip(QUERY_LOG_FIELDS, row[1:])}
                duration = fields.get("query_duration_ms")
                found[statement_id] = StatementTiming(
                    statement_id=statement_id,
                    fields={name: value for name, value in fields.items() if value is not None},
                    platform_ms=None if duration is None else float(duration),
                )
            if wanted <= found.keys():
                break
        return found


__all__ = ["ClickHouseTimingMixin"]
