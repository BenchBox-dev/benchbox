from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from benchbox.core.dataframe.query import DataFrameQuery
from benchbox.core.errors import PlanCaptureError
from benchbox.core.results.exporter import ResultExporter
from benchbox.core.results.schema import compute_plan_capture_stats
from benchbox.core.schemas import BenchmarkConfig
from benchbox.platforms.dataframe.benchmark_mixin import DataFramePhases, DataFrameRunOptions
from benchbox.platforms.dataframe.expression_family import ExpressionFamilyAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.usefixtures("sufficient_dataframe_memory"),
]


class _Frame:
    def __init__(self, *, fail_calls: set[int] | None = None) -> None:
        self.fail_calls = fail_calls
        self.calls = 0

    def _maybe_fail(self) -> None:
        self.calls += 1
        if self.fail_calls is None or self.calls in self.fail_calls:
            raise RuntimeError("explain exploded")

    def explain(self, optimized: bool = True, extended: bool = False) -> str:
        self._maybe_fail()
        return "plan text"

    def collect(self) -> dict[str, int]:
        return {"rows": 1}

    def logical_plan(self) -> str:
        self._maybe_fail()
        return "plan text"


class _Adapter(ExpressionFamilyAdapter[dict, Any, str]):
    table_mode = "native"
    config = None

    def __init__(self, platform: str) -> None:
        super().__init__()
        self._platform = platform
        self.collect_calls = 0

    @property
    def platform_name(self) -> str:
        return self._platform

    def col(self, name):
        return name

    def lit(self, value):
        return value

    def date_sub(self, column, days):
        return column

    def date_add(self, column, days):
        return column

    def cast_date(self, column):
        return column

    def cast_string(self, column):
        return column

    def read_csv(self, path, **kwargs):
        return {}

    def read_parquet(self, path):
        return {}

    def collect(self, df):
        self.collect_calls += 1
        return {"rows": 1}

    def get_row_count(self, df):
        return 1

    def scalar(self, df, column=None):
        return 1

    def scalar_to_df(self, data):
        return {}

    def window_rank(self, order_by, partition_by=None):
        return "x"

    window_row_number = window_dense_rank = window_rank

    def window_sum(self, column, partition_by=None, order_by=None):
        return "x"

    window_avg = window_count = window_sum

    def window_min(self, column, partition_by=None):
        return "x"

    window_max = window_min

    def union_all(self, *dataframes):
        return {}

    def rename_columns(self, df, mapping):
        return df


class _Benchmark:
    name = "dummy"
    display_name = "Dummy"
    scale_factor = 1.0
    tables: dict[str, Any] = {}

    def __init__(self, frame_factory) -> None:
        self._frame_factory = frame_factory

    def get_dataframe_queries(self):
        return [
            DataFrameQuery(
                query_id="Q1",
                query_name="q",
                description="q",
                expression_impl=lambda _ctx: self._frame_factory(),
            )
        ]


def _run(adapter, frame_factory, *, iterations: int = 1, strict: bool = False):
    config = BenchmarkConfig(
        name="dummy",
        display_name="Dummy",
        scale_factor=1.0,
        options={"power_warmup_iterations": 0, "power_iterations": iterations},
        capture_plans=True,
        strict_plan_capture=strict,
    )
    return adapter.run_benchmark(
        _Benchmark(frame_factory),
        benchmark_config=config,
        phases=DataFramePhases(load=False, execute=True),
        options=DataFrameRunOptions(prefer_parquet=False),
    )


@pytest.mark.parametrize("platform", ["Polars", "DataFusion", "PySpark"])
def test_helper_failure_is_counted_and_persisted(platform, tmp_path):
    adapter = _Adapter(platform)

    result = _run(adapter, lambda: _Frame())

    row = result.query_results[0]
    assert row["status"] == "SUCCESS"
    assert "query_plan" not in row or row["query_plan"] is None
    assert row["plan_capture_error"] == "explain exploded"
    assert result.query_plans_captured == 0
    assert result.plan_capture_failures == 1
    assert result.plan_capture_errors[0]["query_id"] == "Q1"
    assert result.plan_capture_errors[0]["error"] == "explain exploded"
    assert adapter.plan_capture_errors[0]["error"] == "explain exploded"
    assert adapter.collect_calls == 1

    result.output_filename = "run.json"
    ResultExporter(output_dir=tmp_path, anonymize=False).export_result(result, formats=["json"])
    payload = json.loads(next(Path(tmp_path).glob("*.plans.json")).read_text(encoding="utf-8"))
    assert payload["queries"] == {}
    assert payload["capture_failures"] == 1
    assert payload["errors"][0]["query_id"] == "Q1"
    assert payload["errors"][0]["error"] == "explain exploded"


def test_partial_failure_across_iterations_is_not_masked():
    adapter = _Adapter("Polars")
    frames: list[_Frame] = []

    def factory():
        frame = _Frame(fail_calls=None if len(frames) == 1 else set())
        frames.append(frame)
        return frame

    result = _run(adapter, factory, iterations=2)

    assert result.query_plans_captured == 1
    assert result.plan_capture_failures == 1
    assert [e["iteration"] for e in result.plan_capture_errors] == [2]
    assert result.plan_capture_errors[0]["error"] == "explain exploded"


def test_strict_plan_capture_raises_without_reexecuting_query():
    adapter = _Adapter("Polars")

    with pytest.raises(PlanCaptureError, match="explain exploded"):
        _run(adapter, lambda: _Frame(), strict=True)

    assert adapter.collect_calls == 0


def test_adapter_reuse_resets_capture_state():
    adapter = _Adapter("Polars")

    first = _run(adapter, lambda: _Frame())
    assert len(first.plan_capture_errors) == 1
    assert adapter._plan_capture_warning_emitted is True

    second = _run(adapter, lambda: _Frame(fail_calls=set()))

    assert second.plan_capture_errors == []
    assert second.plan_capture_failures == 0
    assert second.query_plans_captured == 1
    assert adapter.plan_capture_errors == []
    assert adapter._plan_capture_warning_emitted is False


def test_strict_flag_resets_on_reuse():
    adapter = _Adapter("Polars")

    with pytest.raises(PlanCaptureError):
        _run(adapter, lambda: _Frame(), strict=True)

    result = _run(adapter, lambda: _Frame(), strict=False)

    assert result.plan_capture_failures == 1


def test_same_query_id_failure_in_other_stream_is_not_masked():
    query_results = [
        {"query_id": "Q1", "status": "SUCCESS", "run_type": "measurement", "stream_id": 1, "query_plan": {"p": 1}},
        {
            "query_id": "Q1",
            "status": "SUCCESS",
            "run_type": "measurement",
            "stream_id": 2,
            "plan_capture_error": "boom",
        },
    ]

    plans_captured, failures, errors = compute_plan_capture_stats(
        query_results,
        capture_plans=True,
        existing_errors=[{"query_id": "Q1", "error": "boom", "stream_id": 2}],
    )

    assert plans_captured == 1
    assert failures == 1
    assert errors == [{"query_id": "Q1", "error": "boom", "stream_id": 2}]


def test_same_query_id_failure_in_other_iteration_and_test_type_is_not_masked():
    query_results = [
        {"query_id": "Q1", "status": "SUCCESS", "run_type": "measurement", "iteration": 1, "query_plan": {"p": 1}},
        {"query_id": "Q1", "status": "SUCCESS", "run_type": "measurement", "iteration": 2},
        {"query_id": "Q2", "status": "SUCCESS", "run_type": "measurement", "test_type": "power", "query_plan": {}},
        {"query_id": "Q2", "status": "SUCCESS", "run_type": "measurement", "test_type": "throughput"},
    ]

    _, failures, errors = compute_plan_capture_stats(query_results, capture_plans=True, existing_errors=[])

    assert failures == 2
    assert errors == []


def test_failure_cause_recovered_from_row_when_error_list_empty():
    query_results = [
        {"query_id": "Q1", "status": "SUCCESS", "run_type": "measurement", "plan_capture_error": "cause"},
    ]

    _, failures, errors = compute_plan_capture_stats(query_results, capture_plans=True, existing_errors=[])

    assert failures == 1
    assert errors == [{"query_id": "Q1", "error": "cause"}]


def test_sql_failed_explain_shared_by_iterations_counts_once():
    query_results = [
        {"query_id": f"Q{q}", "status": "SUCCESS", "run_type": "measurement", "iteration": i, "stream_id": i}
        | ({} if q == 1 else {"query_plan": {"p": q}})
        for q in range(1, 23)
        for i in range(1, 4)
    ]

    plans_captured, failures, errors = compute_plan_capture_stats(
        query_results,
        capture_plans=True,
        existing_errors=[{"query_id": "Q1", "error": "EXPLAIN failed"}],
    )

    assert plans_captured == 21
    assert failures == 1
    assert errors == [{"query_id": "Q1", "error": "EXPLAIN failed"}]
