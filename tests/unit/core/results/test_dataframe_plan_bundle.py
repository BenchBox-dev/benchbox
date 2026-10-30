from __future__ import annotations

from datetime import datetime

import pytest

from benchbox.cli.commands.show_plan import _find_query
from benchbox.core.query_plans.comparison import build_execution_map
from benchbox.core.results.loader import _reconstruct_query_results
from benchbox.core.results.query_plan_models import LogicalOperator, LogicalOperatorType, QueryPlanDAG
from benchbox.core.results.schema import build_plans_payload
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _plan(table: str) -> QueryPlanDAG:
    return QueryPlanDAG(
        query_id="Q1",
        platform="spark",
        logical_root=LogicalOperator(operator_type=LogicalOperatorType.SCAN, operator_id="s", table_name=table),
    )


def _rows() -> list[dict]:
    plan_a, plan_b = _plan("a"), _plan("b")
    return [
        {"query_id": "Q1", "status": "SUCCESS", "stream_id": 0, "iteration": 0, "run_type": "warmup"},
        {
            "query_id": "Q1",
            "status": "SUCCESS",
            "stream_id": 0,
            "iteration": 1,
            "run_type": "measurement",
            "query_plan": plan_a,
            "plan_fingerprint": plan_a.plan_fingerprint,
        },
        {
            "query_id": "Q1",
            "status": "SUCCESS",
            "stream_id": 0,
            "iteration": 2,
            "run_type": "measurement",
            "query_plan": plan_b,
            "plan_fingerprint": plan_b.plan_fingerprint,
        },
    ]


def _results():
    return make_benchmark_results(
        benchmark_id="tpch",
        benchmark_name="tpch",
        platform="datafusion-df",
        scale_factor=1.0,
        execution_id="df_bundle",
        timestamp=datetime(2025, 1, 1),
        duration_seconds=1.0,
        total_queries=3,
        successful_queries=3,
        query_plans_captured=1,
        query_results=_rows(),
    )


def test_same_stream_iterations_share_one_plan_entry():
    payload = build_plans_payload(_results())

    assert list(payload["queries"]) == ["Q1#0:"]


def test_plan_entry_records_the_execution_it_came_from():
    entry = build_plans_payload(_results())["queries"]["Q1#0:"]

    assert entry["iteration"] == 2
    assert entry["run_type"] == "measurement"


def test_loader_attaches_plan_only_to_the_recorded_execution():
    payload = build_plans_payload(_results())
    compact = [
        {"id": "1", "iter": 0, "stream": 0, "run_type": "warmup", "status": "SUCCESS", "ms": 1.0},
        {"id": "1", "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS", "ms": 1.0},
        {"id": "1", "iter": 2, "stream": 0, "run_type": "measurement", "status": "SUCCESS", "ms": 1.0},
    ]

    rows = _reconstruct_query_results(compact, [], payload)

    assert [row.get("query_plan") is not None for row in rows] == [False, False, True]
    assert rows[2]["query_plan"].logical_root.table_name == "b"
    assert rows[2]["plan_fingerprint"] == _plan("b").plan_fingerprint
    assert "plan_capture_time_ms" not in rows[0]


def test_loader_keeps_raw_text_plans_out_of_the_dag_slot():
    payload = {"queries": {"Q1": {"plan": {"platform": "polars", "plan_text": "SCAN"}, "plan_format": "text"}}}
    compact = [{"id": "1", "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS", "ms": 1.0}]

    rows = _reconstruct_query_results(compact, [], payload)

    assert rows[0].get("query_plan") is None
    assert rows[0]["query_plan_raw"]["plan_text"] == "SCAN"


def test_plan_lookups_skip_warmup_rows_without_plans():
    rows = _rows()
    results = make_benchmark_results(
        benchmark_id="tpch",
        benchmark_name="tpch",
        platform="datafusion-df",
        scale_factor=1.0,
        execution_id="df_bundle",
        timestamp=datetime(2025, 1, 1),
        duration_seconds=1.0,
        total_queries=3,
        successful_queries=3,
        query_results=rows,
    )

    assert build_execution_map(results)["Q1"]["query_plan"] is rows[1]["query_plan"]
    assert _find_query(results, "Q1")["query_plan"] is rows[1]["query_plan"]
