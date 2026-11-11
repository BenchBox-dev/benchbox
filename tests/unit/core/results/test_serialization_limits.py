from __future__ import annotations

import json

import pytest

from benchbox.core.results.query_plan_models import (
    LogicalOperator,
    LogicalOperatorType,
    QueryPlanDAG,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _build_linear_plan(depth: int) -> QueryPlanDAG:
    child = None
    for i in reversed(range(depth)):
        child = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id=f"scan_{i}",
            children=[child] if child else [],
        )
    assert child is not None
    return QueryPlanDAG(query_id="q_linear", platform="duckdb", logical_root=child)


def test_depth_limit_truncates_with_marker_instead_of_dropping() -> None:
    plan = _build_linear_plan(depth=60)
    fingerprint_before = plan.plan_fingerprint

    serialized = plan.to_dict(max_depth=50)

    node = serialized["logical_root"]
    while "truncated_at_depth" not in node:
        assert node["children"], "reached a leaf before hitting the truncation marker"
        node = node["children"][0]
    assert node["truncated_at_depth"] == 51
    assert node["children_omitted"] >= 1

    assert plan.plan_fingerprint == fingerprint_before
    assert plan.compute_plan_fingerprint() == fingerprint_before


def test_depth_limit_at_boundary_succeeds() -> None:
    plan = _build_linear_plan(depth=50)

    serialized = plan.to_dict(max_depth=50)
    assert serialized["logical_root"]["operator_id"] == "scan_0"

    node = serialized["logical_root"]
    depth_count = 1
    while node["children"]:
        node = node["children"][0]
        depth_count += 1
    assert depth_count == 50


def test_estimate_serialized_size_matches_json_length() -> None:
    root = LogicalOperator(
        operator_type=LogicalOperatorType.SCAN,
        operator_id="scan_1",
        table_name="lineitem",
    )
    plan = QueryPlanDAG(query_id="q_size", platform="duckdb", logical_root=root)

    estimated = plan.estimate_serialized_size()
    actual = len(json.dumps(plan.to_dict(), indent=None))

    assert estimated == actual


