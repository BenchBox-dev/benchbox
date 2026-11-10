from __future__ import annotations

from typing import Any

from benchbox.core.results.timer_scope import REQUEST_KIND_QUERY, RequestLedger, outside_query_timer


class LivyStatementMixin:
    livy_endpoint: str
    timeout_minutes: int
    _total_statement_time_seconds: float
    _query_count: int

    def _get_headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _ensure_session(self) -> int:
        raise NotImplementedError

    def _execute_statement(self, code: str, kind: str = "sql") -> dict[str, Any]:
        from benchbox.platforms.azure.spark_execution_utils import execute_livy_statement

        session_id = self._ensure_session()
        result, execution_time = execute_livy_statement(
            livy_endpoint=self.livy_endpoint,
            session_id=session_id,
            code=code,
            kind=kind,
            get_headers=self._get_headers,
            timeout_minutes=self.timeout_minutes,
        )
        self._total_statement_time_seconds += execution_time
        self._query_count += 1
        return result

    def _execute_statement_measured(
        self, code: str, ledger: RequestLedger, kind: str = "sql"
    ) -> tuple[dict[str, Any], float]:
        from benchbox.platforms.azure.spark_execution_utils import execute_livy_statement

        with outside_query_timer():
            session_id = self._ensure_session()
        result, execution_time = execute_livy_statement(
            livy_endpoint=self.livy_endpoint,
            session_id=session_id,
            code=code,
            kind=kind,
            get_headers=self._get_headers,
            timeout_minutes=self.timeout_minutes,
            on_submit=lambda statement_id: ledger.add_request(REQUEST_KIND_QUERY, str(statement_id)),
        )
        with outside_query_timer():
            self._total_statement_time_seconds += execution_time
            self._query_count += 1
        return result, execution_time

    def _wait_for_statement(self, session_id: int, statement_id: int) -> dict[str, Any]:
        from benchbox.platforms.azure.spark_execution_utils import wait_for_livy_statement

        return wait_for_livy_statement(
            livy_endpoint=self.livy_endpoint,
            session_id=session_id,
            statement_id=statement_id,
            get_headers=self._get_headers,
            timeout_minutes=self.timeout_minutes,
        )
