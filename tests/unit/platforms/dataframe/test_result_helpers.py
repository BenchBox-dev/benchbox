from __future__ import annotations

import pytest

from benchbox.platforms.dataframe._result_helpers import (
    build_failure_result_dict,
    build_success_result_dict,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class TestBuildSuccessResultDict:
    def test_required_keys_present(self) -> None:
        result = build_success_result_dict("Q1", 1.23, 100, (1,))
        assert set(result.keys()) == {"query_id", "status", "execution_time_seconds", "rows_returned", "first_row"}

    def test_status_is_success(self) -> None:
        result = build_success_result_dict("Q1", 0.5, 10, None)
        assert result["status"] == "SUCCESS"

    def test_query_id_stored_verbatim(self) -> None:
        result = build_success_result_dict("Q17", 0.1, 5, None)
        assert result["query_id"] == "Q17"

    def test_execution_time_stored(self) -> None:
        result = build_success_result_dict("Q1", 3.14, 0, None)
        assert result["execution_time_seconds"] == pytest.approx(3.14)

    def test_row_count_stored(self) -> None:
        result = build_success_result_dict("Q1", 0.0, 42, None)
        assert result["rows_returned"] == 42

    def test_first_row_tuple_stored(self) -> None:
        row = (1, "a", 3.0)
        result = build_success_result_dict("Q1", 0.0, 1, row)
        assert result["first_row"] == row

    def test_first_row_none_preserved(self) -> None:
        result = build_success_result_dict("Q1", 0.0, 0, None)
        assert result["first_row"] is None

    def test_zero_rows_is_valid(self) -> None:
        result = build_success_result_dict("Q1", 0.001, 0, None)
        assert result["rows_returned"] == 0
        assert result["status"] == "SUCCESS"


class TestBuildFailureResultDict:
    def test_required_keys_present(self) -> None:
        result = build_failure_result_dict("Q1", 0.5, "timeout")
        assert set(result.keys()) == {"query_id", "status", "execution_time_seconds", "error"}

    def test_status_is_failed(self) -> None:
        result = build_failure_result_dict("Q1", 0.0, "err")
        assert result["status"] == "FAILED"

    def test_query_id_stored_verbatim(self) -> None:
        result = build_failure_result_dict("Q22", 0.0, "err")
        assert result["query_id"] == "Q22"

    def test_execution_time_stored(self) -> None:
        result = build_failure_result_dict("Q1", 7.77, "err")
        assert result["execution_time_seconds"] == pytest.approx(7.77)

    def test_error_message_stored_verbatim(self) -> None:
        msg = "Connection reset by peer: [Errno 54]"
        result = build_failure_result_dict("Q1", 0.0, msg)
        assert result["error"] == msg

    def test_empty_error_message_allowed(self) -> None:
        result = build_failure_result_dict("Q1", 0.0, "")
        assert result["error"] == ""

    def test_multiline_error_message_preserved(self) -> None:
        msg = "line1\nline2\nline3"
        result = build_failure_result_dict("Q1", 0.0, msg)
        assert result["error"] == msg

    def test_no_first_row_key_in_failure(self) -> None:
        result = build_failure_result_dict("Q1", 0.0, "err")
        assert "first_row" not in result

    def test_no_rows_returned_key_in_failure(self) -> None:
        result = build_failure_result_dict("Q1", 0.0, "err")
        assert "rows_returned" not in result


