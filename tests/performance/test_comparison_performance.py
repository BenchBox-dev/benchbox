from __future__ import annotations

import time

import pytest

from benchbox.core.query_plans.comparison import QueryPlanComparator
from benchbox.core.results.query_plan_models import (
    LogicalOperator,
    LogicalOperatorType,
    QueryPlanDAG,
)

pytestmark = [
    pytest.mark.performance,
    pytest.mark.stress,
]


def _build_chain_plan(length: int) -> QueryPlanDAG:

    child = None
    for i in reversed(range(length)):
        child = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id=f"op_{i}",
            children=[child] if child else [],
        )
    assert child is not None
    return QueryPlanDAG(query_id="q_perf", platform="duckdb", logical_root=child)


def test_comparison_runs_within_time_budget() -> None:

    comparator = QueryPlanComparator()
    plan_left = _build_chain_plan(200)
    plan_right = _build_chain_plan(200)

    start = time.perf_counter()
    for _ in range(10):
        comparison = comparator.compare_plans(plan_left, plan_right)
        assert comparison.similarity.overall_similarity == 1.0
    duration = time.perf_counter() - start

    assert duration < 1.0


