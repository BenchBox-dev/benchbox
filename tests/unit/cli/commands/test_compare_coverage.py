from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

mod = importlib.import_module("benchbox.cli.commands.compare")

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _comparison_payload() -> dict:
    return {
        "baseline_file": "a.json",
        "current_file": "b.json",
        "summary": {
            "total_queries_compared": 3,
            "improved_queries": 1,
            "regressed_queries": 1,
            "unchanged_queries": 1,
            "overall_assessment": "mixed",
        },
        "performance_changes": {
            "total_execution_time": {
                "baseline": 10.0,
                "current": 12.0,
                "change_percent": 20.0,
                "improved": False,
            }
        },
        "query_comparisons": [
            {
                "query_id": "Q1",
                "baseline_time_ms": 100.0,
                "current_time_ms": 80.0,
                "change_percent": -20.0,
                "improved": True,
            },
            {
                "query_id": "Q2",
                "baseline_time_ms": 100.0,
                "current_time_ms": 180.0,
                "change_percent": 80.0,
                "improved": False,
            },
            {
                "query_id": "Q3",
                "baseline_time_ms": 100.0,
                "current_time_ms": 105.0,
                "change_percent": 5.0,
                "improved": False,
            },
        ],
        "plan_comparison": {
            "plans_compared": 3,
            "plans_unchanged": 1,
            "plans_changed": 2,
            "threshold_applied": 0.9,
            "regressions_detected": 1,
            "query_plans": [
                {
                    "query_id": "Q2",
                    "similarity": 0.6,
                    "type_mismatches": 1,
                    "property_mismatches": 2,
                    "perf_change_pct": 80.0,
                    "plans_identical": False,
                    "is_regression": True,
                },
                {
                    "query_id": "Q1",
                    "similarity": 0.99,
                    "type_mismatches": 0,
                    "property_mismatches": 0,
                    "perf_change_pct": -20.0,
                    "plans_identical": False,
                    "is_regression": False,
                },
            ],
            "regressions": [{"query_id": "Q2", "perf_change_pct": 80.0, "similarity": 0.6}],
        },
    }


def test_threshold_and_regression_helpers() -> None:
    assert mod._parse_threshold("10%") == 0.1
    assert mod._parse_threshold("0.25") == 0.25
    assert mod._parse_threshold("bad") is None

    cmp = {"performance_changes": {"x": {"change_percent": 15}}, "query_comparisons": [{"change_percent": 1}]}
    assert mod._check_regression(cmp, 0.1) is True
    assert (
        mod._check_regression({"performance_changes": {}, "query_comparisons": [{"change_percent": 2}]}, 0.1) is False
    )


def test_format_text_and_html_comparison() -> None:
    comparison = _comparison_payload()
    base = SimpleNamespace(benchmark_name="tpch", platform="duckdb", scale_factor=1)
    cur = SimpleNamespace(benchmark_name="tpch", platform="duckdb", scale_factor=1)

    txt = mod._format_text_comparison(comparison, base, cur, show_all=True)
    assert "BENCHMARK COMPARISON REPORT" in txt
    assert "PLAN-CORRELATED REGRESSIONS" in txt

    html = mod._format_html_comparison(comparison, base, cur)
    assert "<html>" in html
    assert "Query Plan Analysis" in html


def test_format_html_comparison_escapes_all_result_derived_labels() -> None:
    malicious = '"><img src=x onerror=alert(1)>'
    comparison = _comparison_payload()
    comparison["baseline_file"] = malicious
    comparison["current_file"] = malicious
    for key in ("total_queries_compared", "improved_queries", "regressed_queries", "unchanged_queries"):
        comparison["summary"][key] = malicious
    for query in comparison["query_comparisons"]:
        query["query_id"] = malicious
    plan_comparison = comparison["plan_comparison"]
    for plan in plan_comparison["query_plans"]:
        plan["query_id"] = malicious
    for regression in plan_comparison["regressions"]:
        regression["query_id"] = malicious

    baseline = SimpleNamespace(benchmark_name=malicious, platform=malicious, scale_factor=malicious)
    html = mod._format_html_comparison(comparison, baseline, SimpleNamespace())

    assert malicious not in html
    assert "&quot;&gt;&lt;img src=x onerror=alert(1)&gt;" in html


def test_format_markdown_comparison_returns_markdown(tmp_path: Path) -> None:
    comparison = _comparison_payload()
    baseline = SimpleNamespace(benchmark_name="tpch", platform="duckdb", scale_factor=1)
    current = SimpleNamespace(benchmark_name="tpch", platform="duckdb", scale_factor=1)
    output = tmp_path / "comparison.md"

    mod._output_file_comparison(comparison, baseline, current, "markdown", str(output), show_all_queries=True)

    content = output.read_text(encoding="utf-8")
    assert content.startswith("# Benchmark Comparison Report")
    assert "| Metric | Value |" in content
    assert "<!DOCTYPE html>" not in content


def test_discover_metadata_and_table_render(tmp_path: Path) -> None:
    good = tmp_path / "good.json"
    bad = tmp_path / "bad.json"
    plan = tmp_path / "skip.plans.json"
    good.write_text(
        json.dumps(
            {
                "version": "2.0",
                "benchmark": {"name": "TPC-H", "id": "tpch", "scale_factor": 1.0},
                "platform": {"name": "duckdb"},
                "run": {"timestamp": "2026-02-09T10:00:00", "id": "abc"},
            }
        ),
        encoding="utf-8",
    )
    bad.write_text(json.dumps({"version": "1.0"}), encoding="utf-8")
    plan.write_text("{}", encoding="utf-8")

    metas = mod._discover_result_files_with_metadata([tmp_path])
    assert len(metas) == 1
    assert metas[0].benchmark == "TPC-H"
    mod._display_results_table(metas)


def test_compare_mode_selection_and_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    f1 = tmp_path / "a.json"
    f2 = tmp_path / "b.json"
    f1.write_text("{}", encoding="utf-8")
    f2.write_text("{}", encoding="utf-8")

    called = {}
    monkeypatch.setattr(mod, "_list_available_platforms", lambda: called.setdefault("list", True))
    assert CliRunner().invoke(mod.compare, ["--list-platforms"]).exit_code == 0
    assert called["list"] is True

    conflict = CliRunner().invoke(mod.compare, [str(f1), str(f2), "-p", "duckdb"])
    assert conflict.exit_code == 1

    monkeypatch.setattr(mod, "_run_platform_comparison", lambda **_k: called.setdefault("run", True))
    run = CliRunner().invoke(mod.compare, ["-p", "duckdb", "-p", "sqlite"])
    assert run.exit_code == 0
    assert called["run"] is True

    monkeypatch.setattr(mod, "_run_file_comparison", lambda **_k: called.setdefault("file", True))
    file_mode = CliRunner().invoke(mod.compare, [str(f1), str(f2)])
    assert file_mode.exit_code == 0
    assert called["file"] is True

    noargs = CliRunner().invoke(mod.compare, ["--non-interactive"])
    assert noargs.exit_code == 1


def test_file_selectors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    r1 = tmp_path / "r1.json"
    r2 = tmp_path / "r2.json"
    r1.write_text("{}", encoding="utf-8")
    r2.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(mod, "_discover_result_files_with_metadata", list)
    assert mod._guided_file_selection() is None

    m1 = mod.ResultFileMetadata(r1, "TPC-H", "tpch", "duckdb", 1.0, "2026-02-09T01:00:00", "x")
    m2 = mod.ResultFileMetadata(r2, "TPC-H", "tpch", "duckdb", 1.0, "2026-02-09T02:00:00", "y")
    monkeypatch.setattr(mod, "_discover_result_files_with_metadata", lambda: [m1, m2])
    answers = iter(["1", "2", "1"])
    monkeypatch.setattr(mod.Prompt, "ask", lambda *a, **k: next(answers))
    picked = mod._guided_file_selection()
    assert picked is not None and picked[0] != picked[1]

    direct_answers = iter([str(r1), str(r2)])
    monkeypatch.setattr(mod.Prompt, "ask", lambda *a, **k: next(direct_answers))
    direct = mod._direct_file_selection()
    assert direct == (r1, r2)


def test_run_platform_comparison_formats(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class _PT:
        SQL = SimpleNamespace(value="sql")
        DATAFRAME = SimpleNamespace(value="dataframe")
        AUTO = SimpleNamespace(value="auto")

    class _Config:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _Result:
        platform = "duckdb"
        success_rate = 100.0
        query_results: list = []

        def to_dict(self):
            return {"ok": True}

    class _Summary:
        is_comparable = True
        speedup_ratio = 2.0

        def to_dict(self):
            return {"summary": True}

    class _Suite:
        def __init__(self, config, platform_runner=None):
            self.config = config
            self.platform_runner = platform_runner

        def run_comparison(self, **_kwargs):
            return [_Result()]

        def get_summary(self, _results):
            return _Summary()

        def _generate_text_report(self, _results):
            return "TEXT"

        def _generate_markdown_report(self, _results):
            return "# MD"

        def export_results(self, _results, output_path, format="json"):
            output_path.write_text("{}", encoding="utf-8")

    class _Plotter:
        def __init__(self, _results, theme="light"):
            self.theme = theme

        def generate_charts(self, output_dir):
            output_dir.mkdir(parents=True, exist_ok=True)
            return ["speedup"]

    monkeypatch.setitem(
        sys.modules,
        "benchbox.core.comparison",
        SimpleNamespace(
            PlatformType=_PT,
            UnifiedBenchmarkConfig=_Config,
            UnifiedBenchmarkSuite=_Suite,
            UnifiedComparisonPlotter=_Plotter,
        ),
    )

    mod._run_platform_comparison(
        platforms=["duckdb", "sqlite"],
        platform_type="auto",
        benchmark="tpch",
        scale=0.01,
        queries="Q1,Q2",
        warmup=1,
        iterations=2,
        data_dir=None,
        output_format="text",
        output_file=None,
        generate_charts=False,
        theme="light",
    )

    out = tmp_path / "cmp"
    mod._run_platform_comparison(
        platforms=["duckdb", "sqlite"],
        platform_type="sql",
        benchmark="tpch",
        scale=0.01,
        queries=None,
        warmup=1,
        iterations=1,
        data_dir=None,
        output_format="json",
        output_file=str(out),
        generate_charts=True,
        theme="dark",
    )
    assert (out / "comparison.json").exists()
    assert (out / "charts").exists()

    html = tmp_path / "r.html"
    mod._run_platform_comparison(
        platforms=["duckdb", "sqlite"],
        platform_type="sql",
        benchmark="tpch",
        scale=0.01,
        queries=None,
        warmup=1,
        iterations=1,
        data_dir=None,
        output_format="html",
        output_file=str(html),
        generate_charts=False,
        theme="light",
    )
    assert html.exists()


def test_run_platform_comparison_html_escapes_platform_labels(tmp_path: Path) -> None:
    malicious = '"><img src=x onerror=alert(1)>'
    output = tmp_path / "comparison.html"

    class _Suite:
        def _generate_text_report(self, _results):
            return f"Platforms: {malicious}"

    mod._output_comparison_results(
        output_format="html",
        output_file=str(output),
        suite=_Suite(),
        config=SimpleNamespace(),
        results=[],
        summary=SimpleNamespace(),
    )

    content = output.read_text(encoding="utf-8")
    assert malicious not in content
    assert "&quot;&gt;&lt;img src=x onerror=alert(1)&gt;" in content


def test_run_file_comparison_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    b = tmp_path / "b.json"
    c = tmp_path / "c.json"
    b.write_text("{}", encoding="utf-8")
    c.write_text("{}", encoding="utf-8")

    baseline = SimpleNamespace(benchmark_name="tpch", platform="duckdb", scale_factor=1.0)
    current = SimpleNamespace(benchmark_name="tpch", platform="duckdb", scale_factor=1.0)
    monkeypatch.setattr(mod, "load_result_file", lambda p: (baseline if "b.json" in str(p) else current, {}))
    monkeypatch.setattr(mod, "_compare_plans", lambda *_a, **_k: {"plans_compared": 1})
    monkeypatch.setattr(mod, "_format_text_comparison", lambda *_a, **_k: "T")
    monkeypatch.setattr(mod, "_format_html_comparison", lambda *_a, **_k: "<html></html>")

    class _Exporter:
        def compare_results(self, *_a, **_k):
            return {
                "baseline_file": "b",
                "current_file": "c",
                "summary": {},
                "performance_changes": {},
                "query_comparisons": [],
            }

    monkeypatch.setattr(mod, "ResultExporter", lambda *_a, **_k: _Exporter())

    t = tmp_path / "x.txt"
    mod._run_file_comparison((str(b), str(c)), None, "text", str(t), True, include_plans=False)
    assert t.exists()

    j = tmp_path / "x.json"
    mod._run_file_comparison((str(b), str(c)), None, "json", str(j), True, include_plans=True)
    assert j.exists()

    h = tmp_path / "x.html"
    mod._run_file_comparison((str(b), str(c)), "10%", "html", str(h), True, include_plans=False)
    assert h.exists()
