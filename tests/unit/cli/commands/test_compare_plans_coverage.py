from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from benchbox.core.results.canonical_json import canonical_json_text
from benchbox.core.results.query_plan_models import LogicalOperator, LogicalOperatorType, QueryPlanDAG
from benchbox.core.results.schema import build_plans_payload, build_result_payload
from tests.fixtures.result_dict_fixtures import make_benchmark_results

cp = importlib.import_module("benchbox.cli.commands.compare_plans")

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@dataclass
class _Sim:
    overall_similarity: float
    structural_similarity: float = 0.9
    operator_similarity: float = 0.9
    property_similarity: float = 0.9
    type_mismatches: int = 0
    property_mismatches: int = 0
    structure_mismatches: int = 0


@dataclass
class _Cmp:
    similarity: _Sim
    plans_identical: bool = False
    fingerprints_match: bool = False
    summary: str = "summary"


@dataclass
class _Diff:
    query_id: str
    change_type: str
    similarity: float
    details: str


@dataclass
class _Perf:
    query_id: str
    baseline_time_ms: float
    current_time_ms: float
    perf_change_pct: float
    is_regression: bool


class _Summary:
    baseline_run_id = "r1"
    current_run_id = "r2"
    plans_compared = 2
    plans_unchanged = 1
    plans_changed = 1
    structural_differences = [_Diff("q1", "structure_change", 0.6, "detail"), _Diff("q2", "unchanged", 1.0, "ok")]
    performance_correlations = [_Perf("q1", 10.0, 15.0, 50.0, True)]

    def to_dict(self):
        return {"baseline": self.baseline_run_id, "current": self.current_run_id}


def _Results(ids=("q1",), with_plans=True):
    query_results = []
    for qid in ids:
        entry: dict = {"query_id": qid}
        if with_plans:
            entry["query_plan"] = object()
        query_results.append(entry)
    return make_benchmark_results(query_results=query_results)


def _write_json(path: Path) -> None:
    path.write_text(json.dumps({"schema_version": "1.0"}), encoding="utf-8")


def _make_load_seq(*results_list):
    seq = list(results_list)

    def _load(_p):
        return (seq.pop(0), {})

    return _load


def _write_real_bundle(path: Path, *, execution_id: str, query_id: str, table_name: str) -> None:
    root = LogicalOperator(operator_type=LogicalOperatorType.SCAN, operator_id="scan_1", table_name=table_name)
    plan = QueryPlanDAG(query_id=query_id, platform="duckdb", logical_root=root)
    results = make_benchmark_results(
        benchmark_id="tpch",
        benchmark_name="tpch",
        platform="duckdb",
        scale_factor=1.0,
        execution_id=execution_id,
        timestamp=datetime(2025, 1, 1, 12, 0, 0),
        duration_seconds=1.0,
        total_queries=1,
        successful_queries=1,
        query_plans_captured=1,
        query_results=[
            {
                "query_id": query_id,
                "status": "SUCCESS",
                "execution_time_ms": 100.0,
                "rows_returned": 4,
                "query_plan": plan,
                "plan_fingerprint": plan.plan_fingerprint,
            }
        ],
    )
    path.write_text(canonical_json_text(build_result_payload(results)), encoding="utf-8")
    plans_payload = build_plans_payload(results)
    assert plans_payload is not None
    companion = path.with_name(path.name[: -len(".json")] + ".plans.json")
    companion.write_text(canonical_json_text(plans_payload), encoding="utf-8")


def test_compare_plans_real_loader_compares_bundles(tmp_path: Path) -> None:
    p1 = tmp_path / "r1.json"
    p2 = tmp_path / "r2.json"
    _write_real_bundle(p1, execution_id="run1", query_id="1", table_name="lineitem")
    _write_real_bundle(p2, execution_id="run2", query_id="1", table_name="orders")

    result = CliRunner().invoke(
        cp.compare_plans,
        ["--run1", str(p1), "--run2", str(p2), "--query-id", "1", "--threshold", "1.0", "--output", "json"],
    )

    assert result.exit_code == 0, result.output
    assert '"query_id": "1"' in result.output
    assert '"plans_identical": false' in result.output
    assert '"property_mismatches": 1' in result.output


def test_compare_plans_explicit_query_reported_at_default_threshold(tmp_path: Path) -> None:
    p1 = tmp_path / "r1.json"
    p2 = tmp_path / "r2.json"
    _write_real_bundle(p1, execution_id="run1", query_id="1", table_name="lineitem")
    _write_real_bundle(p2, execution_id="run2", query_id="1", table_name="orders")

    result = CliRunner().invoke(
        cp.compare_plans,
        ["--run1", str(p1), "--run2", str(p2), "--query-id", "1", "--output", "json"],
    )

    assert result.exit_code == 0, result.output
    assert '"query_id": "1"' in result.output
    assert "No plans available" not in result.output


def test_build_comparisons_empty_explicit_behaves_as_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cp, "compare_query_plans", lambda *_a, **_k: _Cmp(similarity=_Sim(0.99)))
    results1 = _Results(ids=("q1",))
    results2 = _Results(ids=("q1",))

    assert cp._build_comparisons(["q1"], results1, results2, 0.95, "") == []
    assert len(cp._build_comparisons(["q1"], results1, results2, 0.95, "q1")) == 1


def test_compare_plans_reports_corrupt_companion_distinctly(tmp_path: Path) -> None:
    p1 = tmp_path / "r1.json"
    p2 = tmp_path / "r2.json"
    _write_real_bundle(p1, execution_id="run1", query_id="1", table_name="lineitem")
    _write_real_bundle(p2, execution_id="run2", query_id="1", table_name="orders")
    (tmp_path / "r2.plans.json").write_text("{ not valid json", encoding="utf-8")

    result = CliRunner().invoke(cp.compare_plans, ["--run1", str(p1), "--run2", str(p2), "--query-id", "1"])

    assert result.exit_code == 0, result.output
    assert "failed to load" in result.output


def test_compare_plans_uses_load_result_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    p1 = tmp_path / "r1.json"
    p2 = tmp_path / "r2.json"
    _write_json(p1)
    _write_json(p2)
    calls = []

    def _spy(_p):
        calls.append(_p)
        return (_Results(), {})

    monkeypatch.setattr(cp, "load_result_file", _spy)
    monkeypatch.setattr(cp, "generate_plan_comparison_summary", lambda *_a, **_k: _Summary())
    CliRunner().invoke(cp.compare_plans, ["--run1", str(p1), "--run2", str(p2), "--summary"])
    assert len(calls) == 2


def test_compare_plans_summary_mode_writes_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    p1 = tmp_path / "r1.json"
    p2 = tmp_path / "r2.json"
    out = tmp_path / "summary.json"
    _write_json(p1)
    _write_json(p2)

    monkeypatch.setattr(cp, "load_result_file", _make_load_seq(_Results(), _Results()))
    monkeypatch.setattr(cp, "generate_plan_comparison_summary", lambda *_a, **_k: _Summary())

    result = CliRunner().invoke(
        cp.compare_plans,
        ["--run1", str(p1), "--run2", str(p2), "--summary", "--output", "json", "--output-file", str(out)],
    )
    assert result.exit_code == 0
    assert out.exists()


def test_compare_plans_no_common_queries_exits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    p1 = tmp_path / "r1.json"
    p2 = tmp_path / "r2.json"
    _write_json(p1)
    _write_json(p2)

    monkeypatch.setattr(cp, "load_result_file", _make_load_seq(_Results(ids=("q1",)), _Results(ids=("q2",))))

    result = CliRunner().invoke(cp.compare_plans, ["--run1", str(p1), "--run2", str(p2)])
    assert result.exit_code == 1
    assert "No common queries" in result.output


def test_compare_plans_threshold_success_message(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    p1 = tmp_path / "r1.json"
    p2 = tmp_path / "r2.json"
    _write_json(p1)
    _write_json(p2)

    monkeypatch.setattr(cp, "load_result_file", _make_load_seq(_Results(ids=("q1", "q2")), _Results(ids=("q1", "q2"))))
    monkeypatch.setattr(cp, "compare_query_plans", lambda *_a, **_k: _Cmp(similarity=_Sim(0.99)))

    result = CliRunner().invoke(cp.compare_plans, ["--run1", str(p1), "--run2", str(p2), "--threshold", "0.95"])
    assert result.exit_code == 0
    assert "All queries have similarity" in result.output


def test_compare_plans_specific_query_missing_plan_warns(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    p1 = tmp_path / "r1.json"
    p2 = tmp_path / "r2.json"
    _write_json(p1)
    _write_json(p2)

    monkeypatch.setattr(
        cp,
        "load_result_file",
        _make_load_seq(_Results(ids=("q1",), with_plans=False), _Results(ids=("q1",), with_plans=True)),
    )

    result = CliRunner().invoke(cp.compare_plans, ["--run1", str(p1), "--run2", str(p2), "--query-id", "q1"])
    assert result.exit_code == 0
    assert "missing plan" in result.output


def test_output_helpers_text_json_and_summary() -> None:
    comparisons = [
        ("q1", _Cmp(similarity=_Sim(0.98))),
        ("q2", _Cmp(similarity=_Sim(0.7, type_mismatches=1, property_mismatches=1, structure_mismatches=1))),
    ]
    out = cp._output_text(comparisons, single_query=False, return_string=True)
    assert "queries compared" in out

    cp.render_comparison = lambda _comparison: "rendered"
    single = cp._output_text([("q1", _Cmp(similarity=_Sim(0.8)))], single_query=True, return_string=True)
    assert isinstance(single, str)

    json_out = cp._output_json(comparisons, return_string=True)
    assert '"query_id": "q1"' in json_out

    summary_text = cp._output_summary_text(_Summary(), return_string=True)
    assert "PLAN COMPARISON SUMMARY" in summary_text
    assert "REGRESSIONS DETECTED" in summary_text

    summary_json = cp._output_summary_json(_Summary(), return_string=True)
    assert '"baseline": "r1"' in summary_json


def test_output_html_helpers() -> None:
    comparisons = [("q1", _Cmp(similarity=_Sim(0.92), summary="ok"))]
    html1 = cp._output_summary_html(_Summary())
    assert "Query Plan Comparison Report" in html1
    assert "Performance Regressions" in html1
    assert "15.00" in html1
    assert "+50.0%" in html1

    results = SimpleNamespace(run_id="run")
    html2 = cp._output_html(comparisons, single_query=False, results1=results, results2=results)
    assert "Query Plan Comparison" in html2


