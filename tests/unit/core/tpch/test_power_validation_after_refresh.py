from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from benchbox.core.expected_results.models import ValidationMode
from benchbox.core.tpch.power_test import TPCHPowerTest
from benchbox.core.validation.query_validation import QueryValidator
from benchbox.platforms.base.connection_wrappers import PlatformAdapterCursor

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.tpch]

BASE_Q13_ROWS = 33
REFRESHED_Q13_ROWS = 34


class _ValidatingConnection:
    def __init__(self, rows_for_query: Any) -> None:
        self.rows_for_query = rows_for_query
        self.query_id: int | None = None

    def set_query_context(self, query_id: int, stream_id: int | None = None) -> None:
        self.query_id = query_id

    def execute(self, _query: str) -> PlatformAdapterCursor:
        rows = self.rows_for_query(self.query_id)
        validation = QueryValidator().validate_query_result("tpch", self.query_id, rows, scale_factor=0.01, stream_id=0)
        evidence: dict[str, Any] = {"expected": validation.expected_row_count, "actual": rows}
        result: dict[str, Any] = {
            "query_id": str(self.query_id),
            "status": "SUCCESS",
            "execution_time_seconds": 0.001,
            "rows_returned": rows,
            "first_row": (1,),
        }
        if validation.validation_mode == ValidationMode.SKIP and validation.is_valid:
            evidence["status"] = "SKIPPED"
            if validation.warning_message:
                evidence["warning"] = validation.warning_message
        elif validation.is_valid:
            evidence["status"] = "PASSED"
        else:
            evidence["status"] = "FAILED"
            evidence["error"] = validation.error_message
            result["status"] = "FAILED"
            result["error"] = validation.error_message
        result["row_count_validation"] = evidence
        return PlatformAdapterCursor(result)

    def commit(self) -> None:
        return None


def _power_test(connection: Any, *, refresh: bool, query_subset: list[str] | None = None) -> TPCHPowerTest:
    benchmark = Mock()
    benchmark.get_query = Mock(return_value="SELECT 1")
    return TPCHPowerTest(
        benchmark=benchmark,
        connection=connection,
        scale_factor=0.01,
        validation=True,
        warm_up=False,
        query_subset=query_subset,
        refresh_update_set=SimpleNamespace(set_id=1) if refresh else None,
        refresh_connection_factory=(lambda: object()) if refresh else None,
    )


def _patch_refresh(power_test: TPCHPowerTest, state: dict[str, bool]) -> None:
    def execute(function: str, _connection: Any) -> dict[str, Any]:
        state["refreshed"] = state["refreshed"] or function == "RF1"
        return {"function": function, "status": "SUCCESS", "error": None, "duration_ms": 1, "update_set": 1}

    power_test._execute_refresh_function = execute
    power_test._preflight_validate_generation = lambda _permutation: None


def test_refresh_run_does_not_validate_queries_against_base_answers() -> None:
    state = {"refreshed": False}
    connection = _ValidatingConnection(lambda _q: REFRESHED_Q13_ROWS if state["refreshed"] else BASE_Q13_ROWS)
    power_test = _power_test(connection, refresh=True, query_subset=["Q13"])
    _patch_refresh(power_test, state)

    result = power_test.run()

    assert result.success is True
    assert result.errors == []
    evidence = result.query_results[0]["row_count_validation"]
    assert evidence["status"] == "SKIPPED"
    assert evidence["actual"] == REFRESHED_Q13_ROWS
    assert evidence["warning"].startswith("data refreshed")
    assert result.validation == {
        "data_state": "refreshed",
        "validated_against": None,
        "validated_queries": [],
        "failed_queries": [],
        "skipped_queries": [13],
        "errored_queries": [],
        "skip_reason": "data refreshed",
    }
    assert result.to_dict()["validation"] == result.validation


def test_run_without_refresh_validates_against_base_answers() -> None:
    connection = _ValidatingConnection(lambda _q: BASE_Q13_ROWS)
    power_test = _power_test(connection, refresh=False, query_subset=["Q13"])
    power_test._preflight_validate_generation = lambda _permutation: None

    result = power_test.run()

    assert result.success is True
    assert result.query_results[0]["row_count_validation"] == {"status": "PASSED", "expected": 33, "actual": 33}
    assert result.validation == {
        "data_state": "base",
        "validated_against": "base",
        "validated_queries": [13],
        "failed_queries": [],
        "skipped_queries": [],
        "errored_queries": [],
    }


def test_run_without_refresh_still_fails_a_wrong_row_count() -> None:
    connection = _ValidatingConnection(lambda _q: REFRESHED_Q13_ROWS)
    power_test = _power_test(connection, refresh=False, query_subset=["Q13"])
    power_test._preflight_validate_generation = lambda _permutation: None

    result = power_test.run()

    assert result.success is False
    assert "Query validation FAILED" in result.errors[0]
    assert result.validation["failed_queries"] == [13]


class _RaisingConnection:
    def execute(self, _query: str) -> Any:
        raise RuntimeError("syntax error")

    def commit(self) -> None:
        return None


@pytest.mark.parametrize("refresh", [True, False])
def test_query_that_raises_is_errored_not_skipped(refresh: bool) -> None:
    power_test = _power_test(_RaisingConnection(), refresh=refresh, query_subset=["Q13"])
    power_test._preflight_validate_generation = lambda _permutation: None
    if refresh:
        _patch_refresh(power_test, {"refreshed": False})

    result = power_test.run()

    assert result.validation["errored_queries"] == [13]
    assert result.validation["skipped_queries"] == []
    assert result.validation["validated_queries"] == []


def test_validation_disabled_records_no_summary() -> None:
    connection = _ValidatingConnection(lambda _q: BASE_Q13_ROWS)
    power_test = _power_test(connection, refresh=False, query_subset=["Q13"])
    power_test.config.validation = False
    power_test._preflight_validate_generation = lambda _permutation: None

    result = power_test.run()

    assert result.validation is None
    assert "validation" not in result.to_dict()


@pytest.mark.parametrize("refresh", [True, False])
def test_throughput_streams_mark_data_refreshed_only_with_refresh_functions(refresh: bool) -> None:
    from benchbox.core.tpch.throughput_test import TPCHThroughputTest, TPCHThroughputTestConfig
    from benchbox.core.validation.query_validation import get_data_refreshed_context

    seen: list[bool] = []

    class _Connection:
        def execute(self, _sql: str) -> SimpleNamespace:
            seen.append(get_data_refreshed_context())
            return SimpleNamespace(fetchall=list)

        def commit(self) -> None:
            return None

        def close(self) -> None:
            return None

    benchmark = SimpleNamespace(get_query=lambda query_id, **_kwargs: f"SELECT {query_id}")
    test = TPCHThroughputTest(benchmark=benchmark, connection_factory=_Connection, num_streams=1)
    config = TPCHThroughputTestConfig(num_streams=1, query_subset=["Q13"], refresh_functions=refresh)

    stream = test._execute_stream(0, 42, config)

    assert stream.queries_successful == 1
    assert seen == [refresh]
    assert get_data_refreshed_context() is False
