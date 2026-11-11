from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from benchbox.core.results.loader import _reconstruct_query_results
from benchbox.core.results.query_plan_models import LogicalOperator, LogicalOperatorType, QueryPlanDAG
from benchbox.core.results.schema import build_plans_payload
from benchbox.core.tpch.platform_power import _power_query_result
from benchbox.platforms.dataframe.benchmark_mixin import BenchmarkExecutionMixin
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _plan() -> QueryPlanDAG:
    return QueryPlanDAG(
        query_id="Q1",
        platform="duckdb",
        logical_root=LogicalOperator(operator_type=LogicalOperatorType.SCAN, operator_id="s", table_name="t"),
    )


class _Adapter(BenchmarkExecutionMixin):
    platform_name = "polars-df"

    def execute_query(self, ctx, query):
        return {"query_id": query.query_id, "status": "SUCCESS", "execution_time_seconds": 0.1, "rows_returned": 1}


def _dataframe_rows() -> list[dict]:
    adapter = _Adapter()
    query = MagicMock()
    query.query_id = "Q1"
    rows = []
    for iteration in (1, 2):
        row = adapter._execute_single_measurement_query(
            ctx=None,
            query=query,
            capture_plans=False,
            supports_profiled=False,
            supports_capture_plan_kw=False,
            profiled_runner=None,
            iteration=iteration,
            stream_id=0,
            monitor=None,
        )
        row["query_plan"] = _plan()
        rows.append(row)
    return rows


def _sql_rows() -> list[dict]:
    rows = []
    for iteration in (1, 2):
        row = _power_query_result(
            {"query_id": "Q1", "execution_time_seconds": 0.1, "success": True},
            stream_id=0,
            iteration=iteration,
            run_type="measurement",
        )
        row["query_plan"] = _plan()
        rows.append(row)
    return rows


def _results(rows: list[dict], platform: str):
    return make_benchmark_results(
        benchmark_id="tpch",
        benchmark_name="tpch",
        platform=platform,
        execution_id=f"keys_{platform}",
        timestamp=datetime(2026, 1, 1),
        duration_seconds=1.0,
        total_queries=2,
        successful_queries=2,
        query_plans_captured=1,
        query_results=rows,
    )


def test_dataframe_rows_carry_the_power_test_type():
    assert {row["test_type"] for row in _dataframe_rows()} == {"power"}


def test_dataframe_and_sql_rows_produce_the_same_plan_key_shape():
    dataframe_keys = list(build_plans_payload(_results(_dataframe_rows(), "polars-df"))["queries"])
    sql_keys = list(build_plans_payload(_results(_sql_rows(), "duckdb"))["queries"])

    assert dataframe_keys == sql_keys == ["Q1#0:power"]


def test_loader_round_trips_dataframe_plan_keys():
    payload = build_plans_payload(_results(_dataframe_rows(), "polars-df"))
    compact = [
        {"id": "1", "iter": 2, "stream": 0, "run_type": "measurement", "status": "SUCCESS", "test_type": "power"}
    ]

    rows = _reconstruct_query_results(compact, [], payload)

    assert rows[0]["query_plan"].logical_root.table_name == "t"


def test_loader_still_reads_legacy_companion_keys_without_a_test_type():
    legacy = {"queries": {"Q1#0:": {"iteration": 2, "run_type": "measurement", "plan": _plan().to_dict()}}}
    compact = [{"id": "1", "iter": 2, "stream": 0, "run_type": "measurement", "status": "SUCCESS"}]

    rows = _reconstruct_query_results(compact, [], legacy)

    assert rows[0]["query_plan"].logical_root.table_name == "t"


