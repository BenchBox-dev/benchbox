from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from benchbox.platforms.azure._livy_mixin import LivyStatementMixin

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _ConcreteAdapter(LivyStatementMixin):
    livy_endpoint = "https://livy.example.com"
    timeout_minutes = 5

    def __init__(self) -> None:
        self._total_statement_time_seconds: float = 0.0
        self._query_count: int = 0
        self._headers = {"Authorization": "Bearer tok"}
        self._session_id = 42

    def _get_headers(self) -> dict[str, str]:
        return self._headers

    def _ensure_session(self) -> int:
        return self._session_id


class TestExecuteStatement:
    def test_accumulates_time_on_first_call(self) -> None:
        adapter = _ConcreteAdapter()
        fake_result: dict[str, Any] = {"output": {"data": "ok"}}
        with patch(
            "benchbox.platforms.azure.spark_execution_utils.execute_livy_statement",
            return_value=(fake_result, 1.5),
        ):
            result = adapter._execute_statement("SELECT 1")
        assert result is fake_result
        assert adapter._total_statement_time_seconds == pytest.approx(1.5)
        assert adapter._query_count == 1

    def test_accumulates_time_across_multiple_calls(self) -> None:
        adapter = _ConcreteAdapter()
        with patch(
            "benchbox.platforms.azure.spark_execution_utils.execute_livy_statement",
            side_effect=[
                ({"output": {}}, 2.0),
                ({"output": {}}, 3.5),
            ],
        ):
            adapter._execute_statement("SELECT 1")
            adapter._execute_statement("SELECT 2")
        assert adapter._total_statement_time_seconds == pytest.approx(5.5)
        assert adapter._query_count == 2

    def test_passes_code_and_kind_to_execute_livy_statement(self) -> None:
        adapter = _ConcreteAdapter()
        with patch(
            "benchbox.platforms.azure.spark_execution_utils.execute_livy_statement",
            return_value=({}, 0.1),
        ) as mock_exec:
            adapter._execute_statement("SELECT 42", kind="pyspark")
        call_kwargs = mock_exec.call_args.kwargs
        assert call_kwargs["code"] == "SELECT 42"
        assert call_kwargs["kind"] == "pyspark"
        assert call_kwargs["livy_endpoint"] == "https://livy.example.com"
        assert call_kwargs["session_id"] == 42
        assert call_kwargs["timeout_minutes"] == 5

    def test_passes_get_headers_callable(self) -> None:
        adapter = _ConcreteAdapter()
        with patch(
            "benchbox.platforms.azure.spark_execution_utils.execute_livy_statement",
            return_value=({}, 0.0),
        ) as mock_exec:
            adapter._execute_statement("SELECT 1")
        assert mock_exec.call_args.kwargs["get_headers"] == adapter._get_headers

    def test_calls_ensure_session_to_get_session_id(self) -> None:
        adapter = _ConcreteAdapter()
        adapter._session_id = 99
        captured_session_ids: list[int] = []

        def _fake_exec(**kwargs: Any) -> tuple[dict[str, Any], float]:
            captured_session_ids.append(kwargs["session_id"])
            return {}, 0.0

        with patch(
            "benchbox.platforms.azure.spark_execution_utils.execute_livy_statement",
            side_effect=_fake_exec,
        ):
            adapter._execute_statement("SELECT 1")

        assert captured_session_ids == [99]


class TestWaitForStatement:
    def test_delegates_to_wait_for_livy_statement(self) -> None:
        adapter = _ConcreteAdapter()
        expected: dict[str, Any] = {"status": "ok"}
        with patch(
            "benchbox.platforms.azure.spark_execution_utils.wait_for_livy_statement",
            return_value=expected,
        ) as mock_wait:
            result = adapter._wait_for_statement(session_id=7, statement_id=3)
        assert result is expected
        mock_wait.assert_called_once_with(
            livy_endpoint="https://livy.example.com",
            session_id=7,
            statement_id=3,
            get_headers=adapter._get_headers,
            timeout_minutes=5,
        )

    def test_does_not_touch_counters(self) -> None:
        adapter = _ConcreteAdapter()
        with patch(
            "benchbox.platforms.azure.spark_execution_utils.wait_for_livy_statement",
            return_value={},
        ):
            adapter._wait_for_statement(session_id=1, statement_id=1)
        assert adapter._total_statement_time_seconds == 0.0
        assert adapter._query_count == 0


class TestInterfaceContract:
    def test_get_headers_raises_not_implemented_on_base(self) -> None:
        mixin = LivyStatementMixin()
        with pytest.raises(NotImplementedError):
            mixin._get_headers()

    def test_ensure_session_raises_not_implemented_on_base(self) -> None:
        mixin = LivyStatementMixin()
        with pytest.raises(NotImplementedError):
            mixin._ensure_session()


