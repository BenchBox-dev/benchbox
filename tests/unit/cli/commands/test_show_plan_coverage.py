from __future__ import annotations

import importlib
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from benchbox.core.results.canonical_json import canonical_json_text
from benchbox.core.results.query_plan_models import LogicalOperator, LogicalOperatorType, QueryPlanDAG
from benchbox.core.results.schema import build_plans_payload, build_result_payload
from tests.fixtures.result_dict_fixtures import make_benchmark_results

mod = importlib.import_module("benchbox.cli.commands.show_plan")

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_results(query_id: str = "q1", has_plan: bool = True):
    plan = SimpleNamespace(to_dict=lambda: {"node": "scan"}) if has_plan else None
    query_result: dict = {"query_id": query_id}
    if plan is not None:
        query_result["query_plan"] = plan
    return make_benchmark_results(query_results=[query_result])


def _write_real_bundle(tmp_path: Path, query_id: str = "1") -> Path:
    root = LogicalOperator(operator_type=LogicalOperatorType.SCAN, operator_id="scan_1", table_name="lineitem")
    plan = QueryPlanDAG(query_id=query_id, platform="duckdb", logical_root=root)
    results = make_benchmark_results(
        benchmark_id="tpch",
        benchmark_name="tpch",
        platform="duckdb",
        scale_factor=1.0,
        execution_id="show-plan-real",
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
    main_path = tmp_path / "r.json"
    main_path.write_text(canonical_json_text(build_result_payload(results)), encoding="utf-8")
    plans_payload = build_plans_payload(results)
    assert plans_payload is not None
    (tmp_path / "r.plans.json").write_text(canonical_json_text(plans_payload), encoding="utf-8")
    return main_path


def _make_load(results):
    return lambda _p: (results, {})


def _write(path: Path, payload: str = "{}") -> None:
    path.write_text(payload, encoding="utf-8")


def test_show_plan_tree_summary_and_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    p = tmp_path / "r.json"
    _write(p)
    monkeypatch.setattr(mod, "load_result_file", _make_load(_make_results()))
    monkeypatch.setattr(mod, "render_plan", lambda *_a, **_k: "TREE")
    monkeypatch.setattr(mod, "render_summary", lambda *_a, **_k: "SUMMARY")

    r1 = CliRunner().invoke(mod.show_plan, ["--run", str(p), "--query-id", "q1", "--format", "tree"])
    assert r1.exit_code == 0
    r2 = CliRunner().invoke(mod.show_plan, ["--run", str(p), "--query-id", "q1", "--format", "summary"])
    assert r2.exit_code == 0
    r3 = CliRunner().invoke(mod.show_plan, ["--run", str(p), "--query-id", "q1", "--format", "json"])
    assert r3.exit_code == 0
    assert '"node": "scan"' in r3.output


def test_show_plan_uses_load_result_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    p = tmp_path / "r.json"
    _write(p)
    calls = []

    def _spy(path):
        calls.append(path)
        return (_make_results(), {})

    monkeypatch.setattr(mod, "load_result_file", _spy)
    monkeypatch.setattr(mod, "render_plan", lambda *_a, **_k: "TREE")
    CliRunner().invoke(mod.show_plan, ["--run", str(p), "--query-id", "q1"])
    assert len(calls) == 1


def test_show_plan_missing_query_and_plan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    p = tmp_path / "r.json"
    _write(p)

    monkeypatch.setattr(mod, "load_result_file", _make_load(_make_results(query_id="q2", has_plan=True)))
    miss_q = CliRunner().invoke(mod.show_plan, ["--run", str(p), "--query-id", "q1"])
    assert miss_q.exit_code == 1

    monkeypatch.setattr(mod, "load_result_file", _make_load(_make_results(query_id="q1", has_plan=False)))
    miss_p = CliRunner().invoke(mod.show_plan, ["--run", str(p), "--query-id", "q1"])
    assert miss_p.exit_code == 1


def test_show_plan_real_loader_renders_plan_from_bundle(tmp_path: Path) -> None:
    main_path = _write_real_bundle(tmp_path, query_id="1")

    result = CliRunner().invoke(mod.show_plan, ["--run", str(main_path), "--query-id", "1", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert '"logical_root"' in result.output
    assert '"lineitem"' in result.output


def test_show_plan_reports_corrupt_companion_distinctly_from_no_plan(tmp_path: Path) -> None:
    main_path = _write_real_bundle(tmp_path, query_id="1")
    (tmp_path / "r.plans.json").write_text("{ not valid json", encoding="utf-8")

    result = CliRunner().invoke(mod.show_plan, ["--run", str(main_path), "--query-id", "1"])

    assert result.exit_code == 1
    assert "failed to load" in result.output
    assert "capture-plans" not in result.output


def test_show_plan_no_companion_says_no_plan_captured(tmp_path: Path) -> None:
    main_path = _write_real_bundle(tmp_path, query_id="1")
    (tmp_path / "r.plans.json").unlink()

    result = CliRunner().invoke(mod.show_plan, ["--run", str(main_path), "--query-id", "1"])

    assert result.exit_code == 1
    assert "No query plan captured" in result.output
    assert "capture-plans" in result.output


def test_show_plan_load_error_and_verbose_exception(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    _write(bad, "{bad")
    out = CliRunner().invoke(mod.show_plan, ["--run", str(bad), "--query-id", "q1"])
    assert out.exit_code == 1

    p = tmp_path / "ok.json"
    _write(p)
    monkeypatch.setattr(mod, "load_result_file", _make_load(_make_results(query_id="q1", has_plan=True)))
    monkeypatch.setattr(mod, "render_plan", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    res = CliRunner().invoke(mod.show_plan, ["--run", str(p), "--query-id", "q1"], obj={"verbose": True})
    assert res.exit_code == 1
    assert isinstance(res.exception, RuntimeError)


