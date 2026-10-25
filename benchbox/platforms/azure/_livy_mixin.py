from __future__ import annotations

from typing import Any


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

    def _wait_for_statement(self, session_id: int, statement_id: int) -> dict[str, Any]:
        from benchbox.platforms.azure.spark_execution_utils import wait_for_livy_statement

        return wait_for_livy_statement(
            livy_endpoint=self.livy_endpoint,
            session_id=session_id,
            statement_id=statement_id,
            get_headers=self._get_headers,
            timeout_minutes=self.timeout_minutes,
        )
