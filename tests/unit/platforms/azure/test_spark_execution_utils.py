# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock, call, patch

import pytest

from benchbox.core.exceptions import ConfigurationError
from benchbox.platforms.azure.spark_execution_utils import (
    LivyStatementState,
    execute_livy_statement,
    wait_for_livy_statement,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_response(state: str, status: str = "ok", output: dict | None = None):
    resp = MagicMock()
    resp.status_code = 200
    payload = {"state": state}
    if state == LivyStatementState.AVAILABLE:
        payload["output"] = output or {"status": status, "data": "result"}
    resp.json.return_value = payload
    return resp


class TestWaitForLivyStatement:
    def test_success_waiting_running_available(self):
        responses = [
            _make_response(LivyStatementState.WAITING),
            _make_response(LivyStatementState.RUNNING),
            _make_response(LivyStatementState.AVAILABLE, status="ok"),
        ]
        get_headers = lambda: {"Authorization": "Bearer tok"}

        with (
            patch("benchbox.platforms.azure.spark_execution_utils.requests.get") as mock_get,
            patch("benchbox.platforms.azure.spark_execution_utils.time.sleep"),
        ):
            mock_get.side_effect = responses
            result = wait_for_livy_statement(
                livy_endpoint="https://api/sessions",
                session_id=1,
                statement_id=2,
                get_headers=get_headers,
                timeout_minutes=1.0,
            )

        assert result["status"] == "ok"
        assert mock_get.call_count == 3

    def test_available_immediately(self):
        get_headers = dict
        with (
            patch("benchbox.platforms.azure.spark_execution_utils.requests.get") as mock_get,
            patch("benchbox.platforms.azure.spark_execution_utils.time.sleep"),
        ):
            mock_get.return_value = _make_response(LivyStatementState.AVAILABLE)
            result = wait_for_livy_statement(
                livy_endpoint="https://api/sessions",
                session_id=5,
                statement_id=10,
                get_headers=get_headers,
                timeout_minutes=0.5,
            )
        assert result is not None

    def test_error_state_raises(self):
        get_headers = dict
        with (
            patch("benchbox.platforms.azure.spark_execution_utils.requests.get") as mock_get,
            patch("benchbox.platforms.azure.spark_execution_utils.time.sleep"),
        ):
            mock_get.return_value = _make_response(LivyStatementState.ERROR)
            with pytest.raises(ConfigurationError, match="error"):
                wait_for_livy_statement(
                    livy_endpoint="https://api/sessions",
                    session_id=1,
                    statement_id=1,
                    get_headers=get_headers,
                    timeout_minutes=0.1,
                )

    def test_cancelled_state_raises(self):
        get_headers = dict
        with (
            patch("benchbox.platforms.azure.spark_execution_utils.requests.get") as mock_get,
            patch("benchbox.platforms.azure.spark_execution_utils.time.sleep"),
        ):
            mock_get.return_value = _make_response(LivyStatementState.CANCELLED)
            with pytest.raises(ConfigurationError, match="cancelled"):
                wait_for_livy_statement(
                    livy_endpoint="https://api/sessions",
                    session_id=1,
                    statement_id=1,
                    get_headers=get_headers,
                    timeout_minutes=0.1,
                )

    def test_available_with_error_output_raises(self):
        get_headers = dict
        output = {"status": "error", "evalue": "SparkException: bad query"}
        with (
            patch("benchbox.platforms.azure.spark_execution_utils.requests.get") as mock_get,
            patch("benchbox.platforms.azure.spark_execution_utils.time.sleep"),
        ):
            mock_get.return_value = _make_response(LivyStatementState.AVAILABLE, output=output)
            with pytest.raises(ConfigurationError, match="SparkException"):
                wait_for_livy_statement(
                    livy_endpoint="https://api/sessions",
                    session_id=1,
                    statement_id=1,
                    get_headers=get_headers,
                    timeout_minutes=0.1,
                )

    def test_http_non_200_raises(self):
        bad_resp = MagicMock()
        bad_resp.status_code = 404
        get_headers = dict
        with (
            patch("benchbox.platforms.azure.spark_execution_utils.requests.get", return_value=bad_resp),
            patch("benchbox.platforms.azure.spark_execution_utils.time.sleep"),
        ):
            with pytest.raises(ConfigurationError, match="404"):
                wait_for_livy_statement(
                    livy_endpoint="https://api/sessions",
                    session_id=1,
                    statement_id=1,
                    get_headers=get_headers,
                    timeout_minutes=0.1,
                )

    def test_timeout_raises(self):
        get_headers = dict

        with (
            patch("benchbox.platforms.azure.spark_execution_utils.elapsed_seconds", return_value=9999.0),
            patch("benchbox.platforms.azure.spark_execution_utils.requests.get") as mock_get,
            patch("benchbox.platforms.azure.spark_execution_utils.time.sleep"),
        ):
            mock_get.return_value = _make_response(LivyStatementState.RUNNING)
            with pytest.raises(ConfigurationError, match="Timeout"):
                wait_for_livy_statement(
                    livy_endpoint="https://api/sessions",
                    session_id=1,
                    statement_id=1,
                    get_headers=get_headers,
                    timeout_minutes=0.01,
                )


class TestExecuteLivyStatement:
    def test_success_returns_output_and_time(self):
        post_resp = MagicMock()
        post_resp.status_code = 201
        post_resp.json.return_value = {"id": 42}

        expected_output = {"status": "ok", "data": "42"}

        get_headers = lambda: {"Authorization": "Bearer tok"}

        with (
            patch("benchbox.platforms.azure.spark_execution_utils.requests.post", return_value=post_resp) as mock_post,
            patch(
                "benchbox.platforms.azure.spark_execution_utils.wait_for_livy_statement", return_value=expected_output
            ) as mock_wait,
            patch("benchbox.platforms.azure.spark_execution_utils.elapsed_seconds", return_value=1.23),
        ):
            output, elapsed = execute_livy_statement(
                livy_endpoint="https://api/sessions",
                session_id=3,
                code="SELECT 1",
                kind="sql",
                get_headers=get_headers,
                timeout_minutes=2.0,
            )

        assert output == expected_output
        assert elapsed == 1.23
        mock_post.assert_called_once()
        mock_wait.assert_called_once_with("https://api/sessions", 3, 42, get_headers, 2.0)

    def test_post_failure_raises(self):
        bad_post = MagicMock()
        bad_post.status_code = 500
        bad_post.text = "Internal Server Error"
        get_headers = dict

        with patch("benchbox.platforms.azure.spark_execution_utils.requests.post", return_value=bad_post):
            with pytest.raises(ConfigurationError, match="500"):
                execute_livy_statement(
                    livy_endpoint="https://api/sessions",
                    session_id=1,
                    code="SELECT 1",
                    kind="sql",
                    get_headers=get_headers,
                    timeout_minutes=1.0,
                )


class TestLivyStatementStateConstants:
    def test_all_expected_states_defined(self):
        assert LivyStatementState.WAITING == "waiting"
        assert LivyStatementState.RUNNING == "running"
        assert LivyStatementState.AVAILABLE == "available"
        assert LivyStatementState.ERROR == "error"
        assert LivyStatementState.CANCELLING == "cancelling"
        assert LivyStatementState.CANCELLED == "cancelled"


