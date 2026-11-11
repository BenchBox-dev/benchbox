from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.benchmark_mixins import CursorValidationQueryExecutionMixin
from benchbox.platforms.base.sql_execution import execute_sql_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _Cursor:
    def __init__(self, *, rows: list[tuple[Any, ...]] | None = None, error: Exception | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.connection: _Connection | None = None
        self.closed = False

    def execute(self, _query: str) -> None:
        if self.error is not None:
            raise self.error

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows

    def close(self) -> None:
        self.closed = True


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor
        self._cursor.connection = self
        self.rollback_count = 0

    def cursor(self) -> _Cursor:
        return self._cursor

    def rollback(self) -> None:
        self.rollback_count += 1


class _CoreHooks:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def log_verbose(self, message: str) -> None:
        self.messages.append(message)

    def log_very_verbose(self, message: str) -> None:
        self.messages.append(message)

    def _build_query_result_with_validation(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "SUCCESS", **kwargs}


class _CoreSqlPrototype(CursorValidationQueryExecutionMixin, _CoreHooks):
    pass


def _incumbent_platform_helper(
    connection_or_cursor: Any,
    *,
    hooks: _CoreHooks,
    benchmark_type: str | None = None,
    stream_id: int | None = None,
) -> dict[str, Any]:

    return execute_sql_query(
        connection_or_cursor,
        "SELECT 1",
        "q1",
        log_verbose=hooks.log_verbose,
        build_query_result_with_validation=hooks._build_query_result_with_validation,
        benchmark_type=benchmark_type,
        stream_id=stream_id,
    )


def _passed_validation() -> MagicMock:
    validation = MagicMock()
    validation.warning_message = None
    validation.is_valid = True
    validation.error_message = None
    validation.expected_row_count = 1
    return validation


def test_sql_prototypes_characterize_success_contracts() -> None:

    core_cursor = _Cursor(rows=[(1,)])
    core_connection = _Connection(core_cursor)
    core_result = _CoreSqlPrototype().execute_query(core_connection, "SELECT 1", "q1")

    platform_cursor = _Cursor(rows=[(1,)])
    platform_connection = _Connection(platform_cursor)
    platform_result = _incumbent_platform_helper(platform_cursor, hooks=_CoreHooks())

    assert core_result["query_statistics"] is core_result["resource_usage"]
    assert "query_statistics" not in platform_result
    assert core_cursor.closed is True
    assert platform_cursor.closed is False
    assert platform_cursor.connection is platform_connection


def test_sql_prototypes_characterize_failure_recovery_contracts() -> None:

    core_cursor = _Cursor(error=RuntimeError("core failure"))
    core_connection = _Connection(core_cursor)
    core_result = _CoreSqlPrototype().execute_query(core_connection, "SELECT 1", "q1")

    platform_cursor = _Cursor(error=RuntimeError("platform failure"))
    platform_connection = _Connection(platform_cursor)
    platform_result = _incumbent_platform_helper(platform_cursor, hooks=_CoreHooks())

    assert core_result["status"] == "FAILED"
    assert platform_result["status"] == "FAILED"
    assert core_connection.rollback_count == 0
    assert platform_connection.rollback_count == 1
    assert core_cursor.closed is True
    assert platform_cursor.closed is False
    assert "error_type" in core_result
    assert "error" in platform_result


def test_sql_prototypes_characterize_helper_owned_cursor_close() -> None:

    cursor = _Cursor(rows=[(1,)])
    connection = _Connection(cursor)
    _incumbent_platform_helper(connection, hooks=_CoreHooks())
    assert cursor.closed is True


def test_sql_prototypes_characterize_validation_logging_and_digest() -> None:

    proto = _CoreSqlPrototype()
    platform_hooks = _CoreHooks()
    validation = _passed_validation()

    with (
        patch("benchbox.core.validation.query_validation.QueryValidator") as validator_cls,
        patch("benchbox.core.results.result_digest.result_digest_enabled", return_value=True),
        patch("benchbox.core.results.result_digest.compute_result_digest", return_value="digest"),
    ):
        validator_cls.return_value.validate_query_result.return_value = validation
        core_result = proto.execute_query(
            _Connection(_Cursor(rows=[(1,)])),
            "SELECT 1",
            "q1",
            benchmark_type="tpch",
        )
        platform_result = _incumbent_platform_helper(
            _Cursor(rows=[(1,)]),
            hooks=platform_hooks,
            benchmark_type="tpch",
        )

    assert any("PASSED" in message for message in proto.messages)
    assert not any("PASSED" in message or "FAILED" in message for message in platform_hooks.messages)
    assert "result_digest" not in core_result
    assert platform_result.get("result_digest") == "digest"


