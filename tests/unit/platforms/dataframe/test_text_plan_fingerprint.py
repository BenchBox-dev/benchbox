from __future__ import annotations

from datetime import datetime
from typing import Any

import polars as pl
import pytest

from benchbox.core.dataframe.profiling import QueryPlan, capture_query_plan, redact_plan_local_paths
from benchbox.core.results.schema import build_plans_payload
from benchbox.platforms.dataframe.benchmark_mixin import BenchmarkExecutionMixin
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]

_FRAME = pl.LazyFrame({"a": [1, 2, 3, 1], "b": [1, 2, 3, 4]})


class _PolarsMixin(BenchmarkExecutionMixin):
    platform_name = "polars-df"


def _grouped() -> pl.LazyFrame:
    return _FRAME.group_by("a").agg(pl.col("b").sum().alias("s")).filter(pl.col("s") > 1)


def _shared_subexpression() -> pl.LazyFrame:
    return _FRAME.with_columns((pl.col("a") + pl.col("b")).alias("c"), ((pl.col("a") + pl.col("b")) * 2).alias("d"))


def _filtered() -> pl.LazyFrame:
    return _FRAME.filter(pl.col("b") > 2)


def _captured_row(frame: pl.LazyFrame) -> dict[str, Any]:
    captured = capture_query_plan(frame, "polars-df")
    assert captured is not None
    plan = redact_plan_local_paths(captured)
    mixin = _PolarsMixin.__new__(_PolarsMixin)
    structured = mixin._structure_dataframe_plan(plan, "Q1")
    row: dict[str, Any] = {"query_id": "Q1", "query_plan": structured}
    mixin._apply_plan_fingerprints(row, structured)
    return row


def test_polars_text_plan_gets_sha256_fingerprint():
    row = _captured_row(_grouped())

    assert len(row["plan_fingerprint"]) == 64
    int(row["plan_fingerprint"], 16)


@pytest.mark.parametrize("build", [_grouped, _shared_subexpression, _filtered])
def test_same_query_explained_twice_has_same_fingerprint(build):
    assert _captured_row(build())["plan_fingerprint"] == _captured_row(build())["plan_fingerprint"]


def test_different_plans_have_different_fingerprints():
    fingerprints = {
        _captured_row(build())["plan_fingerprint"] for build in (_grouped, _shared_subexpression, _filtered)
    }

    assert len(fingerprints) == 3


def test_fingerprint_follows_redacted_text_only():
    mixin = _PolarsMixin.__new__(_PolarsMixin)
    plans = [QueryPlan("polars", "optimized", f"Parquet SCAN [{root}/a.parquet]") for root in ("/srv/a", "/srv/b")]
    rows: list[dict[str, Any]] = []
    for plan in plans:
        row: dict[str, Any] = {}
        mixin._apply_plan_fingerprints(row, redact_plan_local_paths(plan, ["/srv/a", "/srv/b"]))
        rows.append(row)

    assert rows[0]["plan_fingerprint"] == rows[1]["plan_fingerprint"]


def test_empty_plan_text_gets_no_fingerprint():
    row: dict[str, Any] = {}

    _PolarsMixin.__new__(_PolarsMixin)._apply_plan_fingerprints(row, QueryPlan("polars", "optimized", "  "))

    assert row == {}


def test_plans_payload_flags_text_fingerprint_variation():
    rows = []
    for iteration, build in ((1, _grouped), (2, _filtered)):
        row = _captured_row(build())
        row.update(status="SUCCESS", stream_id=0, iteration=iteration, run_type="measurement", test_type="power")
        rows.append(row)
    results = make_benchmark_results(
        benchmark_id="tpch",
        benchmark_name="tpch",
        platform="polars-df",
        execution_id="polars_text_fp",
        timestamp=datetime(2026, 1, 1),
        duration_seconds=1.0,
        total_queries=2,
        successful_queries=2,
        query_plans_captured=2,
        query_results=rows,
    )

    queries = build_plans_payload(results)["queries"]
    assert len(queries) == 1
    entry = next(iter(queries.values()))

    assert entry["fingerprint_varied"] is True
    assert [v["fingerprint"] for v in entry["fingerprint_variants"]] == [r["plan_fingerprint"] for r in rows]
    assert entry["fingerprint_kind"] == "text"
    assert "fingerprint_version" not in entry
    assert entry["plan_format"] == "text"


def test_structured_plan_keeps_structural_fingerprint_without_text_kind():
    mixin = _PolarsMixin.__new__(_PolarsMixin)
    mixin.platform_name = "datafusion-df"
    raw = QueryPlan("datafusion", "logical", "Projection: lineitem.l_orderkey\n  TableScan: lineitem\n")
    dag = mixin._structure_dataframe_plan(raw, "Q1")
    row: dict[str, Any] = {}

    mixin._apply_plan_fingerprints(row, dag)

    assert row["plan_fingerprint"] == dag.plan_fingerprint
