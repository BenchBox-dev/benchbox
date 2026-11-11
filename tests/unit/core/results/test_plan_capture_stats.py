from __future__ import annotations

import pytest

from benchbox.core.results.schema import compute_plan_capture_stats

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_compute_plan_capture_stats_uses_measurement_when_present() -> None:
    query_results = [
        {"query_id": "Q1", "status": "SUCCESS", "run_type": "warmup", "query_plan": {"p": 1}},
        {"query_id": "Q1", "status": "SUCCESS", "run_type": "measurement", "query_plan": {"p": 1}},
        {"query_id": "Q2", "status": "SUCCESS", "run_type": "measurement"},
    ]

    plans_captured, failures, errors = compute_plan_capture_stats(query_results, capture_plans=True)

    assert plans_captured == 1
    assert failures == 1
    assert errors == [{"query_id": "Q2", "error": "Query plan not captured"}]


def test_compute_plan_capture_stats_falls_back_when_run_type_missing() -> None:
    query_results = [
        {"query_id": "Q1", "status": "SUCCESS", "query_plan": {"p": 1}},
        {"query_id": "Q2", "status": "SUCCESS"},
        {"query_id": "Q3", "status": "FAILED"},
    ]
    existing_errors = [
        {"query_id": "Q2", "error": "EXPLAIN timeout", "reason": "timeout"},
        {"query_id": "Q3", "error": "planner crashed", "reason": "planner_error"},
    ]

    plans_captured, failures, errors = compute_plan_capture_stats(
        query_results,
        capture_plans=True,
        existing_errors=existing_errors,
    )

    assert plans_captured == 1
    assert failures == 1
    assert errors == [{"query_id": "Q2", "error": "EXPLAIN timeout", "reason": "timeout"}]


def test_compute_plan_capture_stats_capture_disabled() -> None:
    query_results = [
        {"query_id": "Q1", "status": "SUCCESS", "query_plan": {"p": 1}},
        {"query_id": "Q2", "status": "SUCCESS"},
    ]

    plans_captured, failures, errors = compute_plan_capture_stats(query_results, capture_plans=False)

    assert plans_captured == 1
    assert failures == 0
    assert errors == []


