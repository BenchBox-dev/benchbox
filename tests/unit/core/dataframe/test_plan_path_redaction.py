from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from benchbox.core.dataframe.profiling import QueryPlan, capture_query_plan, redact_plan_local_paths
from benchbox.core.results.anonymization import find_public_path_leaks
from benchbox.core.results.exporter import ResultExporter
from benchbox.core.results.schema import build_plans_payload
from benchbox.platforms.dataframe.benchmark_mixin import BenchmarkExecutionMixin
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _FakeLazyFrame:
    def __init__(self, scan_paths: list[Path]):
        self._paths = scan_paths

    def explain(self, optimized: bool = True) -> str:
        scans = "\n".join(f"  Parquet SCAN [{path}]\n  PROJECT 2/16 COLUMNS" for path in self._paths)
        header = 'SELECT [col("revenue").sum()]' if optimized else "FROM"
        return f"{header}\n{scans}"


def _scan_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    data_dir = tmp_path / "datagen" / "tpch_sf001"
    return data_dir, data_dir / "lineitem.1.parquet", Path.home() / "benchmark_data" / "orders.parquet"


def _forbidden(tmp_path: Path) -> list[str]:
    return [str(Path.home()), str(tmp_path)]


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in _strings(child)]
    if isinstance(value, (list, tuple)):
        return [text for child in value for text in _strings(child)]
    return []


def _captured(tmp_path: Path) -> tuple[QueryPlan, QueryPlan]:
    data_dir, under_data, under_home = _scan_paths(tmp_path)
    frame = _FakeLazyFrame([under_data, under_home])
    captured = capture_query_plan(frame, "polars-df")
    assert captured is not None
    plan = redact_plan_local_paths(captured, (data_dir,))
    raw = QueryPlan(
        platform="polars",
        plan_type="optimized",
        plan_text=frame.explain(optimized=True),
        plan_data={"optimized_plan": frame.explain(optimized=True), "logical_plan": frame.explain(optimized=False)},
    )
    assert plan is not None
    return plan, raw


def test_captured_polars_plan_text_contains_no_local_paths(tmp_path):
    plan, _ = _captured(tmp_path)

    leaked = [
        text for text in _strings([plan.plan_text, plan.plan_data]) if any(p in text for p in _forbidden(tmp_path))
    ]

    assert leaked == []
    assert find_public_path_leaks([plan.plan_text, plan.plan_data]) == []


def test_redaction_preserves_plan_structure(tmp_path):
    plan, raw = _captured(tmp_path)
    data_dir, under_data, under_home = _scan_paths(tmp_path)

    expected = raw.plan_text.replace(str(under_data), "<redacted>").replace(str(under_home), "<redacted>")

    assert plan.plan_text == expected
    assert plan.plan_text.splitlines() == [
        line.replace(f"[{under_data}]", "[<redacted>]").replace(f"[{under_home}]", "[<redacted>]")
        for line in raw.plan_text.splitlines()
    ]
    assert plan.plan_data is not None
    assert list(plan.plan_data) == ["optimized_plan", "logical_plan"]
    assert plan.plan_data["optimized_plan"] == plan.plan_text


def test_redaction_leaves_path_free_plans_unchanged():
    plan = QueryPlan(platform="polars", plan_type="optimized", plan_text='SELECT [col("a")]\n  FILTER a < 1')

    assert redact_plan_local_paths(plan) == plan


def test_redaction_covers_roots_outside_the_detector(tmp_path):
    root = tmp_path / "elsewhere"
    plan = QueryPlan(platform="polars", plan_type="optimized", plan_text=f"Parquet SCAN [{root}/a.parquet]")

    assert str(root) in plan.plan_text
    assert str(root) not in redact_plan_local_paths(plan, [root]).plan_text


class _Structurer(BenchmarkExecutionMixin):
    platform_name = "datafusion-df"


def test_structured_dataframe_plan_fingerprint_is_independent_of_local_paths(tmp_path):
    template = (
        "Projection: lineitem.l_orderkey\n  TableScan: lineitem\n"
        "ParquetExec: file_groups={{1 group: [[{path}]]}}, projection=[l_orderkey]"
    )
    structurer = _Structurer.__new__(_Structurer)
    fingerprints = []
    for root in (tmp_path / "machine_a", Path.home() / "machine_b"):
        raw = QueryPlan("datafusion", "logical", template.format(path=root / "lineitem.parquet"))
        dag = structurer._structure_dataframe_plan(redact_plan_local_paths(raw, [root]), "Q1")
        fingerprints.append((dag.plan_fingerprint, dag.raw_explain_output))

    assert fingerprints[0] == fingerprints[1]
    assert not any(str(Path.home()) in (raw or "") or str(tmp_path) in (raw or "") for _, raw in fingerprints)


@pytest.mark.parametrize("anonymize", [False, True])
def test_exported_plans_companion_has_no_local_paths(tmp_path, anonymize):
    out_dir = tmp_path / "results"
    data_dir = tmp_path / "datagen" / "tpch_sf001"
    plan, _ = _captured(tmp_path)
    rows = [
        {
            "query_id": "Q1",
            "status": "SUCCESS",
            "stream_id": 0,
            "iteration": 1,
            "run_type": "measurement",
            "test_type": "power",
            "query_plan": plan,
        }
    ]
    result = make_benchmark_results(
        benchmark_id="tpch",
        benchmark_name="tpch",
        platform="polars-df",
        execution_id="polars_paths",
        timestamp=datetime(2026, 1, 1),
        duration_seconds=1.0,
        total_queries=1,
        successful_queries=1,
        query_plans_captured=1,
        query_results=rows,
        platform_info={"working_dir": str(data_dir)},
    )

    exported = ResultExporter(output_dir=out_dir, anonymize=anonymize).export_result(result, formats=["json"])
    plans_path = out_dir / f"{exported['json'].stem}.plans.json"
    text = plans_path.read_text(encoding="utf-8")
    payload = json.loads(text)

    for forbidden in _forbidden(tmp_path):
        assert forbidden not in text
    assert find_public_path_leaks(payload) == []
    entry = payload["queries"]["Q1"]
    assert entry["plan"]["plan_text"] == plan.plan_text
    assert "fingerprint" not in entry
    assert build_plans_payload(result)["queries"]["Q1"]["plan"]["plan_text"] == plan.plan_text
