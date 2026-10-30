from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchbox.core.dataframe.profiling import (
    PlanCaptureSupport,
    capture_query_plan,
    plan_capture_support,
)
from benchbox.core.errors import PlanCaptureError
from benchbox.core.query_plans.parsers.registry import get_parser_for_platform
from benchbox.core.results.exporter import ResultExporter
from benchbox.core.results.loader import load_result_file
from benchbox.core.results.query_plan_models import QueryPlanDAG
from benchbox.core.schemas import BenchmarkConfig
from benchbox.platforms.dataframe.benchmark_mixin import DataFramePhases, DataFrameRunOptions

from .spark_plan_fixture import SPARK_EXTENDED_EXPLAIN, FakeSparkFrame
from .test_plan_capture_error_accounting import _Adapter, _Benchmark

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.usefixtures("sufficient_dataframe_memory"),
]


class _PolarsFrame:
    def explain(self, optimized: bool = True) -> str:
        return "FILTER col(a) > 1\nFROM\n  DF [a]"

    def collect(self) -> dict[str, int]:
        return {"rows": 1}


class _EagerFrame:
    def collect(self) -> dict[str, int]:
        return {"rows": 1}


def _run(adapter, frame_factory, *, iterations: int = 1, strict: bool = False, normalize: bool = False):
    config = BenchmarkConfig(
        name="dummy",
        display_name="Dummy",
        scale_factor=1.0,
        options={
            "power_warmup_iterations": 0,
            "power_iterations": iterations,
            "normalize_plan_literals": normalize,
        },
        capture_plans=True,
        strict_plan_capture=strict,
    )
    return adapter.run_benchmark(
        _Benchmark(frame_factory),
        benchmark_config=config,
        phases=DataFramePhases(load=False, execute=True),
        options=DataFrameRunOptions(prefer_parquet=False),
    )


@pytest.mark.parametrize("platform", ["pyspark", "spark", "lakesail", "databricks"])
def test_spark_family_platforms_resolve_to_spark_parser(platform):
    assert get_parser_for_platform(platform) is not None


@pytest.mark.parametrize("platform", ["PySpark", "LakeSail", "Databricks-df", "pyspark-df"])
def test_spark_family_dispatch_captures_extended_explain(platform):
    plan = capture_query_plan(FakeSparkFrame(), platform)

    assert plan is not None
    assert plan.platform == platform.lower().removesuffix("-df")
    assert plan.plan_text == SPARK_EXTENDED_EXPLAIN


@pytest.mark.parametrize("platform", ["PySpark", "LakeSail"])
def test_spark_family_rows_promote_to_dag_with_fingerprints(platform):
    adapter = _Adapter(platform)

    result = _run(adapter, lambda: FakeSparkFrame())

    row = result.query_results[0]
    assert isinstance(row["query_plan"], QueryPlanDAG)
    assert row["query_plan"].platform == "spark"
    assert row["plan_fingerprint"] == row["query_plan"].plan_fingerprint
    assert "plan_fingerprint_normalized" not in row
    assert row["plan_capture_time_ms"] > 0
    assert result.plan_capture_failures == 0


def test_normalized_fingerprint_added_only_when_requested():
    result = _run(_Adapter("PySpark"), lambda: FakeSparkFrame(), normalize=True)

    row = result.query_results[0]
    assert row["plan_fingerprint_normalized"] == row["query_plan"].normalized_fingerprint


def test_capture_time_is_excluded_from_execution_time(monkeypatch):
    import benchbox.platforms.dataframe.expression_family as family

    original = family.capture_query_plan

    def slow_capture(df, platform):
        import time

        time.sleep(0.2)
        return original(df, platform)

    monkeypatch.setattr(family, "capture_query_plan", slow_capture)

    result = _run(_Adapter("PySpark"), lambda: FakeSparkFrame())

    row = result.query_results[0]
    assert row["plan_capture_time_ms"] >= 200
    assert row["execution_time_seconds"] < 0.1


def test_polars_plan_stays_raw_text_without_fabricated_dag_or_fingerprint(tmp_path: Path):
    assert plan_capture_support("Polars") is PlanCaptureSupport.RAW_TEXT

    result = _run(_Adapter("Polars"), lambda: _PolarsFrame())

    row = result.query_results[0]
    assert not isinstance(row["query_plan"], QueryPlanDAG)
    assert row["query_plan"].platform == "polars"
    assert "plan_fingerprint" not in row
    assert result.query_plans_captured == 1
    assert result.plan_capture_failures == 0

    result.output_filename = "run.json"
    ResultExporter(output_dir=tmp_path, anonymize=False).export_result(result, formats=["json"])
    payload = json.loads(next(Path(tmp_path).glob("*.plans.json")).read_text(encoding="utf-8"))
    entry = next(iter(payload["queries"].values()))
    assert entry["plan_format"] == "text"
    assert "fingerprint" not in entry

    loaded, _ = load_result_file(tmp_path / "run.json")
    loaded_row = loaded.query_results[0]
    assert loaded_row.get("query_plan") is None
    assert loaded_row["query_plan_raw"]["plan_text"].startswith("FILTER")


@pytest.mark.parametrize("platform", ["Pandas", "Dask", "cuDF"])
def test_eager_engines_report_unsupported_without_a_plan(platform):
    assert plan_capture_support(platform) is PlanCaptureSupport.UNSUPPORTED
    assert capture_query_plan(_EagerFrame(), platform) is None

    adapter = _Adapter(platform)
    result = _run(adapter, lambda: _EagerFrame())

    row = result.query_results[0]
    assert row.get("query_plan") is None
    assert "no query plan to capture" in row["plan_capture_error"]
    assert result.query_plans_captured == 0
    assert result.plan_capture_failures == 1
    assert "no query plan to capture" in result.plan_capture_errors[0]["error"]


def test_strict_capture_rejects_unsupported_engine():
    with pytest.raises(PlanCaptureError) as excinfo:
        _run(_Adapter("Pandas"), lambda: _EagerFrame(), strict=True)

    assert excinfo.value.reason == "unsupported"


def test_dag_rows_round_trip_through_export_and_load(tmp_path: Path):
    result = _run(_Adapter("PySpark"), lambda: FakeSparkFrame(), iterations=3)

    result.output_filename = "run.json"
    ResultExporter(output_dir=tmp_path, anonymize=False).export_result(result, formats=["json"])
    payload = json.loads(next(Path(tmp_path).glob("*.plans.json")).read_text(encoding="utf-8"))
    assert sorted(payload["queries"]) == ["Q1#0", "Q1#1", "Q1#2"]
    assert all(entry["fingerprint"] for entry in payload["queries"].values())
    assert all(entry["plan_format"] == "dag" for entry in payload["queries"].values())

    loaded, _ = load_result_file(tmp_path / "run.json")
    plans = [row for row in loaded.query_results if row.get("query_plan") is not None]
    assert len(plans) == 3
    assert {row["plan_fingerprint"] for row in plans} == {plans[0]["query_plan"].plan_fingerprint}


def test_expression_family_captures_plan_after_collect():
    order: list[str] = []

    class _Frame(FakeSparkFrame):
        def explain(self, extended: bool = False) -> None:
            order.append("explain")
            super().explain(extended)

    adapter = _Adapter("PySpark")
    original = adapter.collect
    adapter.collect = lambda df: (order.append("collect"), original(df))[1]

    _run(adapter, lambda: _Frame())

    assert order == ["collect", "explain"]
