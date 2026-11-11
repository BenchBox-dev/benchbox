from unittest.mock import Mock, patch

import pytest

from benchbox.platforms.redshift import RedshiftAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.cloud_import,
]


def _make_adapter() -> RedshiftAdapter:
    with patch.object(RedshiftAdapter, "_resolve_connect_timeout", return_value=10):
        try:
            return RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                port=5439,
                database="test_db",
                username="u",
                password="p",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")


def _run_failing_query(adapter: RedshiftAdapter, exc: BaseException) -> dict:
    cursor = Mock()
    cursor.execute.side_effect = exc
    connection = Mock()
    connection.cursor.return_value = cursor
    return adapter.execute_query(
        connection=connection,
        query="SELECT 1",
        query_id="Q1",
        validate_row_count=False,
    )


class TestRedshiftErrorCapture:
    def test_exception_message_populates_result_error(self):
        adapter = _make_adapter()
        result = _run_failing_query(adapter, RuntimeError("connection reset"))

        assert result["status"] == "FAILED"
        assert result["error"] == "connection reset"
        assert result["error_type"] == "RuntimeError"
        assert result["rows_returned"] == 0

    def test_empty_message_falls_back_to_repr_or_type(self):
        adapter = _make_adapter()

        class EmptyError(Exception):
            def __str__(self) -> str:
                return ""

        result = _run_failing_query(adapter, EmptyError())

        assert result["status"] == "FAILED"
        assert result["error"], "error field must never be empty"
        assert result["error_type"] == "EmptyError"


