from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from benchbox.core.exceptions import ConfigurationError
from benchbox.core.results.timer_scope import REQUEST_KIND_QUERY, RequestLedger, outside_query_timer
from benchbox.platforms.base.remote_work import StartedRemoteWork
from benchbox.utils.clock import elapsed_seconds, mono_time

FAILED_SESSION_STATES = frozenset({"error", "dead", "killed"})


class LivyStatementMixin:
    livy_endpoint: str
    timeout_minutes: int
    remote_cleanup_label: str
    _total_statement_time_seconds: float
    _query_count: int
    _started_work: StartedRemoteWork
    _session_id: int | None
    _session_created_by_us: bool

    def _get_headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _ensure_session(self) -> int:
        raise NotImplementedError

    def _wait_for_session_state(
        self,
        session_id: int,
        target_states: list[str],
        timeout_seconds: int = 600,
    ) -> str:
        from benchbox.platforms.azure.spark_execution_utils import livy_get, livy_pause

        start_time = mono_time()
        session_url = f"{self.livy_endpoint}/{session_id}"

        while elapsed_seconds(start_time) < timeout_seconds:
            response = livy_get(
                session_url,
                headers=self._get_headers(),
                timeout=30,
            )

            if response.status_code != 200:
                raise ConfigurationError(f"Failed to get session status: {response.status_code}")

            state = response.json()["state"]

            if state in target_states:
                return state
            if state in FAILED_SESSION_STATES:
                raise ConfigurationError(f"Session is in {state} state")

            livy_pause(5)

        raise ConfigurationError(f"Timeout waiting for session {session_id}")

    @contextmanager
    def _tracked_statement(
        self, session_id: int, ledger: RequestLedger | None = None
    ) -> Iterator[Callable[[int], None]]:
        from benchbox.platforms.azure.spark_execution_utils import LivyStatementTimeout

        submitted: list[str] = []

        def record(statement_id: int) -> None:
            submitted.append(str(statement_id))
            self._started_work.add(str(statement_id), session_id)
            if ledger is not None:
                ledger.add_request(REQUEST_KIND_QUERY, str(statement_id))

        try:
            yield record
        except LivyStatementTimeout:
            raise
        except Exception:
            for statement_id in submitted:
                self._started_work.discard(statement_id)
            raise
        for statement_id in submitted:
            self._started_work.discard(statement_id)

    def _execute_statement(self, code: str, kind: str = "sql") -> dict[str, Any]:
        from benchbox.platforms.azure.spark_execution_utils import execute_livy_statement

        session_id = self._ensure_session()
        with self._tracked_statement(session_id) as on_submit:
            result, execution_time = execute_livy_statement(
                livy_endpoint=self.livy_endpoint,
                session_id=session_id,
                code=code,
                kind=kind,
                get_headers=self._get_headers,
                timeout_minutes=self.timeout_minutes,
                on_submit=on_submit,
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
        with self._tracked_statement(session_id, ledger) as on_submit:
            result, execution_time = execute_livy_statement(
                livy_endpoint=self.livy_endpoint,
                session_id=session_id,
                code=code,
                kind=kind,
                get_headers=self._get_headers,
                timeout_minutes=self.timeout_minutes,
                on_submit=on_submit,
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

    def cancel_remote_work(self) -> None:
        from benchbox.platforms.azure.spark_execution_utils import cancel_livy_statement, delete_livy_session

        failures: list[str] = []
        try:
            self._started_work.cancel_all(
                self.remote_cleanup_label,
                lambda statement_id, session_id: cancel_livy_statement(
                    self.livy_endpoint, session_id, statement_id, self._get_headers
                ),
            )
        except Exception as exc:
            failures.append(str(exc))
        if self._session_id is not None and self._session_created_by_us:
            try:
                delete_livy_session(self.livy_endpoint, self._session_id, self._get_headers)
                self._session_id = None
            except Exception as exc:
                failures.append(f"{self.remote_cleanup_label} session delete failed: {type(exc).__name__}: {exc}")
        if failures:
            raise RuntimeError("; ".join(failures))
