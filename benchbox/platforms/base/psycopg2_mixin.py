# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from benchbox.core.results.timer_scope import (
    REQUEST_KIND_BEGIN,
    TIMING_METHOD_QUERY_ONLY,
    RequestLedger,
    outside_query_timer,
)


class PsycopgConnectionMixin:
    _max_identifier_length: int = 63
    timing_method = TIMING_METHOD_QUERY_ONLY

    def _implicit_begin_pending(self, connection: Any) -> bool:
        try:
            if connection.autocommit is not False:
                return False
            return int(connection.info.transaction_status) == 0
        except Exception:
            return False

    def _timing_fields_with_begin(self, connection: Any) -> Callable[..., dict[str, Any]]:
        with outside_query_timer():
            begin_pending = self._implicit_begin_pending(connection)

        def timing_fields(conn: Any, ledger: RequestLedger, **kwargs: Any) -> dict[str, Any]:
            if begin_pending:
                counted = RequestLedger()
                counted.record(REQUEST_KIND_BEGIN)
                for request in ledger.requests:
                    counted.record(request.kind, request.statement_id, request.attempt)
                ledger = counted
            return self.timing_fields_for(conn, ledger, **kwargs)

        return timing_fields

    def execute_query(
        self,
        connection: Any,
        query: str,
        query_id: str,
        benchmark_type: str | None = None,
        scale_factor: float | None = None,
        validate_row_count: bool = True,
        stream_id: int | None = None,
    ) -> dict[str, Any]:
        from benchbox.platforms.base.sql_execution import execute_sql_query

        return execute_sql_query(
            connection,
            query,
            query_id,
            log_verbose=self.log_verbose,
            build_query_result_with_validation=self._build_query_result_with_validation,
            benchmark_type=benchmark_type,
            scale_factor=scale_factor,
            validate_row_count=validate_row_count,
            stream_id=stream_id,
            timing_fields_for=self._timing_fields_with_begin(connection),
        )

    def _validate_identifier(self, identifier: str) -> bool:
        from benchbox.utils.sql_identifier import is_valid_sql_identifier

        return is_valid_sql_identifier(identifier, max_length=self._max_identifier_length)

    def close_connection(self, connection: Any) -> None:
        if connection:
            try:
                connection.close()
            except Exception as e:
                self.logger.debug(f"Error closing connection: {e}")

    def _get_existing_tables(self, connection: Any) -> list[str]:
        try:
            cursor = connection.cursor()
        except Exception as e:
            self.logger.warning(f"Failed to get existing tables: {e}")
            return []
        try:
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = %s AND table_type = 'BASE TABLE'
                """,
                (self.schema,),
            )
            result = cursor.fetchall()
            return [row[0].lower() for row in result]
        except Exception as e:
            self.logger.warning(f"Failed to get existing tables: {e}")
            return []
        finally:
            cursor.close()
