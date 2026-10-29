import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.results.query_normalizer import normalize_query_id
from tests.unit.mcp.public_api import get_tool
from tests.utilities.fixed_clock import set_mtimes

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


def _make_result_file(dir_path: Path, filename: str, data: dict) -> Path:
    file_path = dir_path / filename
    file_path.write_text(json.dumps(data))
    return file_path


def _make_benchmark_result(
    platform: str = "duckdb",
    benchmark: str = "tpch",
    scale_factor: float = 0.01,
    queries: list[dict] | None = None,
    timestamp: str = "2026-01-15T10:00:00",
    execution_id: str = "test_001",
    summary: dict | None = None,
) -> dict:
    if queries is None:
        queries = [
            {"query_id": "Q1", "runtime_ms": 100, "status": "success"},
            {"query_id": "Q6", "runtime_ms": 50, "status": "success"},
            {"query_id": "Q17", "runtime_ms": 200, "status": "success"},
        ]
    query_entries = []
    for q in queries:
        raw_id = q.get("query_id") or q.get("id") or ""
        normalized_id = normalize_query_id(raw_id)
        status = q.get("status", "SUCCESS")
        status = status.upper()
        if status in ("OK", "PASS", "PASSED", "SUCCESS", "SUCCEEDED"):
            status = "SUCCESS"
        elif status in ("FAILED", "FAIL", "ERROR"):
            status = "FAILED"
        query_entries.append(
            {
                "id": normalized_id,
                "ms": q.get("runtime_ms", q.get("ms", 0)),
                "status": status,
                "run_type": "measurement",
                "iter": 1,
                "stream": 0,
            }
        )

    total_runtime_ms = sum(q.get("ms", 0) for q in query_entries)
    total_queries = len(query_entries)
    passed = len([q for q in query_entries if q.get("status") == "SUCCESS"])
    failed = total_queries - passed

    if summary:
        summary_block = {
            "queries": {
                "total": summary.get("total_queries", total_queries),
                "passed": summary.get("successful_queries", passed),
                "failed": summary.get("failed_queries", failed),
            },
            "timing": {"total_ms": summary.get("total_runtime_ms", total_runtime_ms)},
        }
    else:
        avg_ms = total_runtime_ms / total_queries if total_queries else 0
        summary_block = {
            "queries": {"total": total_queries, "passed": passed, "failed": failed},
            "timing": {"total_ms": total_runtime_ms, "avg_ms": avg_ms},
        }

    return {
        "version": "2.1",
        "run": {
            "id": execution_id,
            "timestamp": timestamp,
            "total_duration_ms": total_runtime_ms,
            "query_time_ms": total_runtime_ms,
            "iterations": 1,
            "streams": 1,
        },
        "benchmark": {"id": benchmark, "name": benchmark.upper(), "scale_factor": scale_factor},
        "platform": {"name": platform},
        "summary": summary_block,
        "queries": query_entries,
    }


class TestGetResultsList:
    def test_no_results_dir_returns_empty(self, tmp_path):
        from benchbox.mcp import create_server

        server = create_server(results_dir=tmp_path / "nonexistent")
        fn = get_tool(server, "get_results").fn
        result = fn()

        assert result["runs"] == []
        assert result["count"] == 0

    def test_returns_runs_from_result_files(self, tmp_path):
        from benchbox.mcp import create_server

        data = _make_benchmark_result(platform="duckdb", benchmark="tpch")
        _make_result_file(tmp_path, "run1.json", data)

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "get_results").fn

        result = fn()

        assert result["count"] == 1
        assert result["runs"][0]["platform"] == "duckdb"
        assert result["runs"][0]["benchmark"] == "tpch"
        assert result["runs"][0]["file"] == "run1.json"

    def test_platform_filter(self, tmp_path):
        from benchbox.mcp import create_server

        _make_result_file(tmp_path, "duckdb_run.json", _make_benchmark_result(platform="duckdb"))
        _make_result_file(tmp_path, "sqlite_run.json", _make_benchmark_result(platform="sqlite"))

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "get_results").fn

        result = fn(platform="duckdb")

        assert result["count"] == 1
        assert result["runs"][0]["platform"] == "duckdb"

    def test_benchmark_filter(self, tmp_path):
        from benchbox.mcp import create_server

        _make_result_file(tmp_path, "tpch_run.json", _make_benchmark_result(benchmark="tpch"))
        _make_result_file(tmp_path, "tpcds_run.json", _make_benchmark_result(benchmark="tpcds"))

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "get_results").fn

        result = fn(benchmark="tpcds")

        assert result["count"] == 1
        assert result["runs"][0]["benchmark"] == "tpcds"

    def test_limit_parameter(self, tmp_path):
        from benchbox.mcp import create_server

        for i in range(5):
            _make_result_file(tmp_path, f"run_{i}.json", _make_benchmark_result())

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "get_results").fn

        result = fn(limit=2)

        assert result["count"] == 2
        assert result["total_available"] == 5

    def test_summary_included_when_present(self, tmp_path):
        from benchbox.mcp import create_server

        data = _make_benchmark_result(summary={"total_queries": 22, "total_runtime_ms": 5000})
        _make_result_file(tmp_path, "run.json", data)

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "get_results").fn

        result = fn()

        assert result["runs"][0]["summary"]["total_queries"] == 22
        assert result["runs"][0]["summary"]["total_runtime_ms"] == 5000

    def test_malformed_files_skipped(self, tmp_path):
        from benchbox.mcp import create_server

        (tmp_path / "bad.json").write_text("not json {{")
        _make_result_file(tmp_path, "good.json", _make_benchmark_result())

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "get_results").fn

        result = fn()

        assert result["count"] == 1

    def test_filters_applied_in_response(self, tmp_path):
        from benchbox.mcp import create_server

        _make_result_file(tmp_path, "run.json", _make_benchmark_result())

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "get_results").fn

        result = fn(platform="duckdb", benchmark="tpch", limit=5)

        assert result["filters_applied"]["platform"] == "duckdb"
        assert result["filters_applied"]["benchmark"] == "tpch"
        assert result["filters_applied"]["limit"] == 5

    def test_explicit_results_dir_override_takes_precedence(self, tmp_path, monkeypatch):
        from benchbox.mcp import create_server

        override_dir = tmp_path / "override_results"
        override_dir.mkdir(parents=True)
        _make_result_file(override_dir, "override.json", _make_benchmark_result(platform="duckdb"))

        server = create_server(results_dir=override_dir)
        fn = get_tool(server, "get_results").fn
        result = fn()

        assert result["count"] == 1
        assert result["runs"][0]["file"] == "override.json"


class TestGetResultsExport:
    def test_invalid_format_returns_error(self, tmp_path):
        from benchbox.mcp import create_server

        data = _make_benchmark_result()
        _make_result_file(tmp_path, "run.json", data)

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "get_results").fn

        result = fn(result_file="run.json", format="yaml")

        assert result["error"] is True
        assert result["error_code"] == "VALIDATION_INVALID_FORMAT"

    def test_details_preserves_query_run_type(self, tmp_path):
        from benchbox.mcp import create_server

        data = _make_benchmark_result(
            queries=[
                {"query_id": "Q1", "runtime_ms": 10, "status": "success"},
                {"query_id": "Q1", "runtime_ms": 12, "status": "success"},
            ]
        )
        data["queries"][0]["run_type"] = "warmup"
        data["queries"][0]["iter"] = 0
        _make_result_file(tmp_path, "run.json", data)

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "get_results").fn

        result = fn(result_file="run.json", format="details")

        assert result["queries"][0]["run_type"] == "warmup"
        assert result["queries"][1]["run_type"] == "measurement"

    def test_details_excludes_queries_when_include_queries_false(self, tmp_path):
        from benchbox.mcp import create_server

        data = _make_benchmark_result()
        _make_result_file(tmp_path, "run.json", data)

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "get_results").fn

        result = fn(result_file="run.json", format="details", include_queries=False)

        assert "queries" not in result
        assert "summary" in result

    def test_json_export(self, tmp_path):
        from benchbox.mcp import create_server

        data = _make_benchmark_result()
        _make_result_file(tmp_path, "run.json", data)

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "get_results").fn

        result = fn(result_file="run.json", format="json")

        assert result["status"] == "exported"
        assert result["format"] == "json"
        parsed = json.loads(result["content"])
        assert parsed["benchmark"]["id"] == "tpch"

    def test_csv_export_with_queries(self, tmp_path):
        from benchbox.mcp import create_server

        data = _make_benchmark_result()
        _make_result_file(tmp_path, "run.json", data)

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "get_results").fn

        result = fn(result_file="run.json", format="csv")

        assert result["status"] == "exported"
        assert result["format"] == "csv"
        assert "query_id,runtime_ms,status" in result["content"]
        assert "1" in result["content"]

    def test_csv_export_no_queries(self, tmp_path):
        from benchbox.mcp import create_server

        data = _make_benchmark_result(queries=[])
        _make_result_file(tmp_path, "run.json", data)

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "get_results").fn

        result = fn(result_file="run.json", format="csv")

        assert result["content"] == "query_id,runtime_ms,status\n"

    def test_html_export(self, tmp_path):
        from benchbox.mcp import create_server

        data = _make_benchmark_result(summary={"total_queries": 3, "total_runtime_ms": 350})
        _make_result_file(tmp_path, "run.json", data)

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "get_results").fn

        result = fn(result_file="run.json", format="html")

        assert result["status"] == "exported"
        assert result["format"] == "html"
        assert "<!DOCTYPE html>" in result["content"]
        assert "<table>" in result["content"]
        assert "1" in result["content"]

    def test_write_to_output_path(self, tmp_path):
        from benchbox.mcp import create_server

        data = _make_benchmark_result()
        _make_result_file(tmp_path, "run.json", data)

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "get_results").fn

        result = fn(result_file="run.json", format="json", output_path="export.json")

        assert result["status"] == "exported"
        assert "output_path" in result
        assert (tmp_path / "export.json").exists()

    def test_path_traversal_rejected(self, tmp_path):
        from benchbox.mcp import create_server

        data = _make_benchmark_result()
        _make_result_file(tmp_path, "run.json", data)

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "get_results").fn

        result = fn(result_file="run.json", format="json", output_path="../escape.json")

        assert result["error"] is True
        assert result["error_code"] == "VALIDATION_ERROR"

    def test_absolute_path_rejected(self, tmp_path):
        from benchbox.mcp import create_server

        data = _make_benchmark_result()
        _make_result_file(tmp_path, "run.json", data)

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "get_results").fn

        result = fn(result_file="run.json", format="json", output_path="/tmp/evil.json")

        assert result["error"] is True

    def test_missing_source_file_returns_error(self, tmp_path):
        from benchbox.mcp import create_server

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "get_results").fn

        result = fn(result_file="missing.json", format="json")

        assert result["error"] is True


class TestGetResultsSummary:
    def test_markdown_format_returns_formatted_content(self, tmp_path):
        from benchbox.mcp import create_server

        data = _make_benchmark_result(summary={"total_queries": 3, "total_runtime_ms": 350})
        _make_result_file(tmp_path, "run.json", data)

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "get_results").fn

        result = fn(result_file="run.json", format="markdown")

        assert result["format"] == "markdown"
        assert "# Benchmark Results" in result["content"]
        assert "## Summary" in result["content"]
        assert "| Query |" in result["content"]

    def test_text_format_returns_plain_text(self, tmp_path):
        from benchbox.mcp import create_server

        data = _make_benchmark_result(summary={"total_queries": 3, "total_runtime_ms": 350})
        _make_result_file(tmp_path, "run.json", data)

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "get_results").fn

        result = fn(result_file="run.json", format="text")

        assert result["format"] == "text"
        assert "Benchmark Results:" in result["content"]
        assert "Platform:" in result["content"]
        assert "Total Queries:" in result["content"]

    def test_missing_file_returns_error(self, tmp_path):
        from benchbox.mcp import create_server

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "get_results").fn

        result = fn(result_file="nonexistent.json", format="text")

        assert result["error"] is True


class TestAnalyzeResultsCompare:
    def test_baseline_not_found(self, tmp_path):
        from benchbox.mcp import create_server

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="compare", file1="missing.json", file2="also_missing.json")

        assert result["error"] is True
        assert result["error_code"] == "RESOURCE_NOT_FOUND"
        assert "baseline" in result["details"]["file_type"]

    def test_comparison_file_not_found(self, tmp_path):
        from benchbox.mcp import create_server

        _make_result_file(tmp_path, "baseline.json", _make_benchmark_result())

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="compare", file1="baseline.json", file2="missing.json")

        assert result["error"] is True
        assert result["error_code"] == "RESOURCE_NOT_FOUND"
        assert "comparison" in result["details"]["file_type"]

    def test_detects_regression(self, tmp_path):
        from benchbox.mcp import create_server

        baseline = _make_benchmark_result(
            queries=[
                {"query_id": "Q1", "runtime_ms": 100, "status": "success"},
                {"query_id": "Q6", "runtime_ms": 50, "status": "success"},
            ]
        )
        comparison = _make_benchmark_result(
            queries=[
                {"query_id": "Q1", "runtime_ms": 200, "status": "success"},
                {"query_id": "Q6", "runtime_ms": 55, "status": "success"},
            ]
        )
        _make_result_file(tmp_path, "baseline.json", baseline)
        _make_result_file(tmp_path, "comparison.json", comparison)

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="compare", file1="baseline.json", file2="comparison.json", threshold_percent=10.0)

        assert "1" in result["regressions"]
        assert result["summary"]["regressions"] >= 1

    def test_detects_improvement(self, tmp_path):
        from benchbox.mcp import create_server

        baseline = _make_benchmark_result(
            queries=[
                {"query_id": "Q1", "runtime_ms": 200, "status": "success"},
            ]
        )
        comparison = _make_benchmark_result(
            queries=[
                {"query_id": "Q1", "runtime_ms": 50, "status": "success"},
            ]
        )
        _make_result_file(tmp_path, "baseline.json", baseline)
        _make_result_file(tmp_path, "comparison.json", comparison)

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="compare", file1="baseline.json", file2="comparison.json")

        assert "1" in result["improvements"]
        assert result["summary"]["improvements"] >= 1

    def test_stable_queries(self, tmp_path):
        from benchbox.mcp import create_server

        baseline = _make_benchmark_result(
            queries=[
                {"query_id": "Q1", "runtime_ms": 100, "status": "success"},
            ]
        )
        comparison = _make_benchmark_result(
            queries=[
                {"query_id": "Q1", "runtime_ms": 105, "status": "success"},
            ]
        )
        _make_result_file(tmp_path, "baseline.json", baseline)
        _make_result_file(tmp_path, "comparison.json", comparison)

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="compare", file1="baseline.json", file2="comparison.json")

        assert result["summary"]["stable"] >= 1

    def test_response_structure(self, tmp_path):
        from benchbox.mcp import create_server

        baseline = _make_benchmark_result()
        comparison = _make_benchmark_result()
        _make_result_file(tmp_path, "b.json", baseline)
        _make_result_file(tmp_path, "c.json", comparison)

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="compare", file1="b.json", file2="c.json")

        assert "baseline_file" in result
        assert "current_file" in result
        assert "summary" in result
        assert "query_comparisons" in result
        assert result["summary"]["threshold_percent"] == 10.0

    def test_auto_appends_json_extension(self, tmp_path):
        from benchbox.mcp import create_server

        _make_result_file(tmp_path, "run1.json", _make_benchmark_result())
        _make_result_file(tmp_path, "run2.json", _make_benchmark_result())

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="compare", file1="run1", file2="run2")

        assert "error" not in result


class TestAnalyzeResultsRegressions:
    def test_no_results_dir(self, tmp_path):
        from benchbox.mcp import create_server

        server = create_server(results_dir=tmp_path / "nonexistent")
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="regressions")

        assert result["status"] == "no_data"
        assert result["regressions"] == []

    def test_insufficient_files(self, tmp_path):
        from benchbox.mcp import create_server

        _make_result_file(tmp_path, "only_one.json", _make_benchmark_result())

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="regressions")

        assert result["status"] == "insufficient_data"

    def test_detects_regression_between_runs(self, tmp_path):
        from benchbox.mcp import create_server

        older = _make_benchmark_result(
            queries=[
                {"query_id": "Q1", "runtime_ms": 100, "status": "success"},
            ]
        )
        older_file = _make_result_file(tmp_path, "older.json", older)

        newer = _make_benchmark_result(
            queries=[
                {"query_id": "Q1", "runtime_ms": 250, "status": "success"},
            ]
        )
        newer_file = _make_result_file(tmp_path, "newer.json", newer)
        set_mtimes([older_file, newer_file])

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="regressions")

        assert result["status"] == "completed"
        assert result["summary"]["regressions"] >= 1
        assert result["regressions"][0]["query_id"] == "1"
        assert result["regressions"][0]["severity"] == "critical"

    def test_detects_improvements(self, tmp_path):
        from benchbox.mcp import create_server

        older = _make_benchmark_result(
            queries=[
                {"query_id": "Q1", "runtime_ms": 200, "status": "success"},
            ]
        )
        older_file = _make_result_file(tmp_path, "older.json", older)

        newer = _make_benchmark_result(
            queries=[
                {"query_id": "Q1", "runtime_ms": 50, "status": "success"},
            ]
        )
        newer_file = _make_result_file(tmp_path, "newer.json", newer)
        set_mtimes([older_file, newer_file])

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="regressions")

        assert result["summary"]["improvements"] >= 1

    def test_platform_filter(self, tmp_path):
        from benchbox.mcp import create_server

        sqlite_file = _make_result_file(tmp_path, "sqlite1.json", _make_benchmark_result(platform="sqlite"))
        duckdb_older = _make_result_file(tmp_path, "duckdb1.json", _make_benchmark_result(platform="duckdb"))
        duckdb_newer = _make_result_file(tmp_path, "duckdb2.json", _make_benchmark_result(platform="duckdb"))
        set_mtimes([sqlite_file, duckdb_older, duckdb_newer])

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="regressions", platform="duckdb")

        assert result["status"] == "completed"
        assert result["comparison"]["baseline"]["platform"] == "duckdb"

    def test_recommendations_generated(self, tmp_path):
        from benchbox.mcp import create_server

        older = _make_benchmark_result(
            queries=[
                {"query_id": "Q1", "runtime_ms": 100, "status": "success"},
            ]
        )
        older_file = _make_result_file(tmp_path, "older.json", older)

        newer = _make_benchmark_result(
            queries=[
                {"query_id": "Q1", "runtime_ms": 500, "status": "success"},
            ]
        )
        newer_file = _make_result_file(tmp_path, "newer.json", newer)
        set_mtimes([older_file, newer_file])

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="regressions")

        assert result["status"] == "completed"
        assert result["summary"]["regressions"] > 0
        critical_regressions = [r for r in result["regressions"] if r.get("severity") == "critical"]
        assert len(critical_regressions) > 0

    def test_threshold_parameter(self, tmp_path):
        from benchbox.mcp import create_server

        older = _make_benchmark_result(
            queries=[
                {"query_id": "Q1", "runtime_ms": 100, "status": "success"},
            ]
        )
        older_file = _make_result_file(tmp_path, "older.json", older)

        newer = _make_benchmark_result(
            queries=[
                {"query_id": "Q1", "runtime_ms": 108, "status": "success"},
            ]
        )
        newer_file = _make_result_file(tmp_path, "newer.json", newer)
        set_mtimes([older_file, newer_file])

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result_10 = fn(analysis="regressions", threshold_percent=10.0)
        assert result_10["summary"]["regressions"] == 0

        result_5 = fn(analysis="regressions", threshold_percent=5.0)
        assert result_5["summary"]["regressions"] == 1


class TestAnalyzeResultsTrends:
    def test_invalid_metric_returns_error(self, tmp_path):
        from benchbox.mcp import create_server

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="trends", metric="invalid_metric")

        assert result["error"] is True
        assert result["error_code"] == "VALIDATION_ERROR"

    def test_no_results_dir(self, tmp_path):
        from benchbox.mcp import create_server

        server = create_server(results_dir=tmp_path / "nonexistent")
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="trends")

        assert result["status"] == "no_data"

    def test_returns_chronological_data_points(self, tmp_path):
        from benchbox.mcp import create_server

        files = []
        for i in range(3):
            data = _make_benchmark_result(
                timestamp=f"2026-01-{15 + i}T10:00:00",
                queries=[{"query_id": "Q1", "runtime_ms": 100 + i * 10, "status": "success"}],
            )
            files.append(_make_result_file(tmp_path, f"run_{i}.json", data))
        set_mtimes(files)

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="trends")

        assert result["status"] == "success"
        assert len(result["data_points"]) == 3
        assert result["data_points"][0]["value"] <= result["data_points"][-1]["value"]

    def test_geometric_mean_metric(self, tmp_path):
        from benchbox.mcp import create_server

        data = _make_benchmark_result(
            queries=[
                {"query_id": "Q1", "runtime_ms": 100, "status": "success"},
                {"query_id": "Q2", "runtime_ms": 400, "status": "success"},
            ]
        )
        first = _make_result_file(tmp_path, "run.json", data)

        data2 = _make_benchmark_result(
            queries=[
                {"query_id": "Q1", "runtime_ms": 100, "status": "success"},
                {"query_id": "Q2", "runtime_ms": 400, "status": "success"},
            ]
        )
        second = _make_result_file(tmp_path, "run2.json", data2)
        set_mtimes([first, second])

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="trends", metric="geometric_mean")

        assert result["data_points"][0]["metric"] == "geometric_mean"
        assert abs(result["data_points"][0]["value"] - 200.0) < 1.0

    def test_total_time_metric(self, tmp_path):
        from benchbox.mcp import create_server

        data = _make_benchmark_result(
            queries=[
                {"query_id": "Q1", "runtime_ms": 100, "status": "success"},
                {"query_id": "Q2", "runtime_ms": 200, "status": "success"},
            ]
        )
        first = _make_result_file(tmp_path, "run.json", data)
        second = _make_result_file(tmp_path, "run2.json", data)
        set_mtimes([first, second])

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="trends", metric="total_time")

        assert result["data_points"][0]["value"] == 300.0

    def test_trend_direction_degrading(self, tmp_path):
        from benchbox.mcp import create_server

        files = []
        for i in range(3):
            data = _make_benchmark_result(
                queries=[
                    {"query_id": "Q1", "runtime_ms": 100 + i * 50, "status": "success"},
                ]
            )
            files.append(_make_result_file(tmp_path, f"run_{i}.json", data))
        set_mtimes(files)

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="trends")

        assert result["summary"]["trend_direction"] == "degrading"

    def test_trend_direction_improving(self, tmp_path):
        from benchbox.mcp import create_server

        files = []
        for i in range(3):
            data = _make_benchmark_result(
                queries=[
                    {"query_id": "Q1", "runtime_ms": 300 - i * 100, "status": "success"},
                ]
            )
            files.append(_make_result_file(tmp_path, f"run_{i}.json", data))
        set_mtimes(files)

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="trends")

        assert result["summary"]["trend_direction"] == "improving"

    def test_limit_parameter(self, tmp_path):
        from benchbox.mcp import create_server

        files = []
        for i in range(5):
            data = _make_benchmark_result(
                queries=[
                    {"query_id": "Q1", "runtime_ms": 100, "status": "success"},
                ]
            )
            files.append(_make_result_file(tmp_path, f"run_{i}.json", data))
        set_mtimes(files)

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="trends", limit=3)

        assert len(result["data_points"]) == 3

    def test_no_matching_data(self, tmp_path):
        from benchbox.mcp import create_server

        _make_result_file(tmp_path, "run.json", _make_benchmark_result(platform="duckdb"))

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="trends", platform="snowflake")

        assert result["status"] == "no_matching_data"


class TestAnalyzeResultsAggregate:
    def test_invalid_group_by(self, tmp_path):
        from benchbox.mcp import create_server

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="aggregate", group_by="invalid")

        assert result["error"] is True
        assert result["error_code"] == "VALIDATION_ERROR"

    def test_no_results_dir(self, tmp_path):
        from benchbox.mcp import create_server

        server = create_server(results_dir=tmp_path / "nonexistent")
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="aggregate")

        assert result["status"] == "no_data"

    def test_group_by_platform(self, tmp_path):
        from benchbox.mcp import create_server

        _make_result_file(tmp_path, "duckdb1.json", _make_benchmark_result(platform="duckdb"))
        _make_result_file(tmp_path, "sqlite1.json", _make_benchmark_result(platform="sqlite"))

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="aggregate", group_by="platform")

        assert result["status"] == "success"
        assert "duckdb" in result["aggregates"]
        assert "sqlite" in result["aggregates"]
        assert result["aggregates"]["duckdb"]["run_count"] == 1
        assert "query_stats" in result["aggregates"]["duckdb"]
        assert "mean_ms" in result["aggregates"]["duckdb"]["query_stats"]

    def test_group_by_benchmark(self, tmp_path):
        from benchbox.mcp import create_server

        _make_result_file(tmp_path, "tpch.json", _make_benchmark_result(benchmark="tpch"))
        _make_result_file(tmp_path, "tpcds.json", _make_benchmark_result(benchmark="tpcds"))

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="aggregate", group_by="benchmark")

        assert "tpch" in result["aggregates"]
        assert "tpcds" in result["aggregates"]

    def test_group_by_date(self, tmp_path):
        from benchbox.mcp import create_server

        _make_result_file(tmp_path, "day1.json", _make_benchmark_result(timestamp="2026-01-15T10:00:00"))
        _make_result_file(tmp_path, "day2.json", _make_benchmark_result(timestamp="2026-01-16T10:00:00"))

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="aggregate", group_by="date")

        assert result["status"] == "success"
        assert "2026-01-15" in result["aggregates"]
        assert "2026-01-16" in result["aggregates"]

    def test_statistics_calculated(self, tmp_path):
        from benchbox.mcp import create_server

        data = _make_benchmark_result(
            queries=[
                {"query_id": "Q1", "runtime_ms": 100, "status": "success"},
                {"query_id": "Q6", "runtime_ms": 200, "status": "success"},
                {"query_id": "Q17", "runtime_ms": 300, "status": "success"},
            ]
        )
        _make_result_file(tmp_path, "run.json", data)

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="aggregate", group_by="platform")

        stats = result["aggregates"]["duckdb"]["query_stats"]
        assert stats["mean_ms"] == 200.0
        assert stats["min_ms"] == 100.0
        assert stats["max_ms"] == 300.0

    def test_platform_filter(self, tmp_path):
        from benchbox.mcp import create_server

        _make_result_file(tmp_path, "duckdb.json", _make_benchmark_result(platform="duckdb"))
        _make_result_file(tmp_path, "sqlite.json", _make_benchmark_result(platform="sqlite"))

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="aggregate", platform="duckdb", group_by="platform")

        assert "duckdb" in result["aggregates"]
        assert "sqlite" not in result["aggregates"]

    def test_no_matching_data(self, tmp_path):
        from benchbox.mcp import create_server

        _make_result_file(tmp_path, "run.json", _make_benchmark_result(platform="duckdb"))

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="aggregate", platform="snowflake")

        assert result["status"] == "no_matching_data"

    def test_summary_section(self, tmp_path):
        from benchbox.mcp import create_server

        _make_result_file(tmp_path, "run1.json", _make_benchmark_result(platform="duckdb"))
        _make_result_file(tmp_path, "run2.json", _make_benchmark_result(platform="sqlite"))

        server = create_server(results_dir=tmp_path)
        fn = get_tool(server, "analyze_results").fn

        result = fn(analysis="aggregate")

        assert result["summary"]["total_groups"] == 2
        assert result["summary"]["total_runs"] == 2


class TestRunBenchmarkDryRun:
    def test_unknown_benchmark_returns_error(self):
        from benchbox.mcp import create_server

        server = create_server()
        fn = get_tool(server, "run_benchmark").fn

        result = fn(platform="duckdb", benchmark="nonexistent_bench", scale_factor=0.01, dry_run=True)

        assert result["status"] == "error"
        assert result["error_code"] == "RESOURCE_NOT_FOUND"

    def test_successful_dry_run(self):
        from benchbox.mcp import create_server

        mock_result = MagicMock()
        mock_result.execution_mode = "sql"
        mock_result.query_preview = {
            "queries": ["Q1", "Q6", "Q17"],
            "query_count": 3,
            "test_execution_type": "standard",
            "execution_context": "TPC-H power run",
        }
        mock_result.estimated_resources = {
            "estimated_data_size_mb": 10,
            "cpu_cores_available": 8,
            "memory_gb_available": 16,
        }
        mock_result.warnings = []

        server = create_server()
        fn = get_tool(server, "run_benchmark").fn

        with patch("benchbox.core.dryrun.DryRunExecutor") as mock_executor_cls:
            mock_executor = MagicMock()
            mock_executor.execute_dry_run.return_value = mock_result
            mock_executor_cls.return_value = mock_executor

            result = fn(platform="duckdb", benchmark="tpch", scale_factor=0.01, dry_run=True)

        assert result["status"] == "dry_run"
        assert result["platform"] == "duckdb"
        assert result["benchmark"] == "tpch"
        assert result["execution_plan"]["total_queries"] == 3
        assert "resource_estimates" in result

    def test_query_subset_forwarded(self):
        from benchbox.mcp import create_server

        mock_result = MagicMock()
        mock_result.execution_mode = "sql"
        mock_result.query_preview = {"queries": ["Q1", "Q6"], "query_count": 2}
        mock_result.estimated_resources = {"estimated_data_size_mb": 5}
        mock_result.warnings = []

        server = create_server()
        fn = get_tool(server, "run_benchmark").fn

        with patch("benchbox.core.dryrun.DryRunExecutor") as mock_executor_cls:
            mock_executor = MagicMock()
            mock_executor.execute_dry_run.return_value = mock_result
            mock_executor_cls.return_value = mock_executor

            fn(platform="duckdb", benchmark="tpch", scale_factor=0.01, queries="1,6", dry_run=True)

            call_args = mock_executor.execute_dry_run.call_args[0]
            benchmark_config = call_args[0]
            assert benchmark_config.queries == ["1", "6"]

    def test_exception_returns_internal_error(self):
        from benchbox.mcp import create_server

        server = create_server()
        fn = get_tool(server, "run_benchmark").fn

        with patch("benchbox.core.dryrun.DryRunExecutor") as mock_executor_cls:
            mock_executor_cls.side_effect = RuntimeError("test failure")

            result = fn(platform="duckdb", benchmark="tpch", scale_factor=0.01, dry_run=True)

        assert result["status"] == "error"
        assert result["error_code"] == "INTERNAL_ERROR"


class TestRunBenchmarkDataOnly:
    def test_unknown_benchmark_returns_error(self):
        from benchbox.mcp import create_server

        server = create_server()
        fn = get_tool(server, "run_benchmark").fn

        result = fn(platform="duckdb", benchmark="nonexistent", scale_factor=0.01, mode="data_only")

        assert result.get("status") == "failed"
        assert result["error_code"] == "RESOURCE_NOT_FOUND"


class TestAnalyticsHelpers:
    def test_classify_regression_severity_critical(self):
        from benchbox.core.results.regression_policy import classify_severity as _classify_regression_severity

        assert _classify_regression_severity(100.0) == "critical"
        assert _classify_regression_severity(200.0) == "critical"

    def test_classify_regression_severity_high(self):
        from benchbox.core.results.regression_policy import classify_severity as _classify_regression_severity

        assert _classify_regression_severity(50.0) == "high"
        assert _classify_regression_severity(99.0) == "high"

    def test_classify_regression_severity_medium(self):
        from benchbox.core.results.regression_policy import classify_severity as _classify_regression_severity

        assert _classify_regression_severity(25.0) == "medium"
        assert _classify_regression_severity(49.0) == "medium"

    def test_classify_regression_severity_low(self):
        from benchbox.core.results.regression_policy import classify_severity as _classify_regression_severity

        assert _classify_regression_severity(10.0) == "low"
        assert _classify_regression_severity(24.0) == "low"

    def test_calculate_metric_geometric_mean(self):
        from benchbox.core.results.metrics import calculate_named_metric

        result = calculate_named_metric([4.0, 9.0], "geometric_mean")
        assert abs(result - 6.0) < 0.01

    def test_calculate_metric_p50(self):
        from benchbox.core.results.metrics import calculate_named_metric

        result = calculate_named_metric([10.0, 20.0, 30.0, 40.0, 50.0], "p50")
        assert result == 30.0

    def test_calculate_metric_total_time(self):
        from benchbox.core.results.metrics import calculate_named_metric

        result = calculate_named_metric([10.0, 20.0, 30.0], "total_time")
        assert result == 60.0

    def test_percentile_empty_list(self):
        from benchbox.core.results.metrics import percentile_ms

        assert percentile_ms([], 0.50) == 0

    def test_percentile_single_element(self):
        from benchbox.core.results.metrics import percentile_ms

        assert percentile_ms([42.0], 0.50) == 42.0
        assert percentile_ms([42.0], 0.95) == 42.0

    def test_std_dev_single_element(self):
        from benchbox.core.results.metrics import sample_stdev_ms

        assert sample_stdev_ms([42.0]) == 0

    def test_std_dev_known_values(self):
        from benchbox.core.results.metrics import sample_stdev_ms

        result = sample_stdev_ms([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
        assert abs(result - 2.138) < 0.01


def _canonical_plan(*, estimated_rows=None, estimated_cost=None) -> dict:
    from benchbox.core.results.query_plan_models import JoinType, LogicalOperator, LogicalOperatorType, QueryPlanDAG

    def scan(op_id, table):
        return LogicalOperator(LogicalOperatorType.SCAN, op_id, table_name=table)

    join = LogicalOperator(
        LogicalOperatorType.JOIN,
        "join1",
        children=[scan("scan1", "lineitem"), scan("scan2", "orders")],
        join_type=JoinType.INNER,
        join_conditions=["l_orderkey = o_orderkey"],
    )
    aggregate = LogicalOperator(
        LogicalOperatorType.AGGREGATE,
        "agg1",
        children=[join],
        aggregation_functions=["sum(l_quantity)"],
        group_by_keys=["l_returnflag"],
    )
    root = LogicalOperator(LogicalOperatorType.PROJECT, "proj1", children=[aggregate])
    return QueryPlanDAG(
        query_id="Q1",
        platform="duckdb",
        logical_root=root,
        estimated_rows=estimated_rows,
        estimated_cost=estimated_cost,
    ).to_dict()


class TestCanonicalPlanRendering:
    def test_summary_counts_logical_operators(self):
        from benchbox.mcp.tools.analytics import _format_plan_response

        response = _format_plan_response(_canonical_plan(), "summary", "1", 5.0)

        assert response["summary"] == {
            "operator_count": 5,
            "join_count": 1,
            "scan_count": 2,
            "aggregate_count": 1,
            "filter_count": 0,
            "sort_count": 0,
            "max_depth": 3,
            "estimated_rows": None,
            "estimated_cost": None,
        }

    def test_summary_reports_estimates_only_when_present(self):
        from benchbox.mcp.tools.analytics import _format_plan_response

        summary = _format_plan_response(_canonical_plan(estimated_rows=42, estimated_cost=1.5), "summary", "1", 5.0)[
            "summary"
        ]

        assert summary["estimated_rows"] == 42
        assert summary["estimated_cost"] == 1.5

    def test_tree_renders_hierarchy(self):
        from benchbox.mcp.tools.analytics import _format_plan_response

        tree = _format_plan_response(_canonical_plan(), "tree", "1", 5.0)["plan_tree"]

        assert "Node" not in tree
        lines = tree.splitlines()

        def depth(marker):
            line = next(line for line in lines if marker in line)
            return line.index(marker)

        assert depth("Project") < depth("Aggregate") < depth("Join") < depth("table=lineitem")
        assert depth("table=lineitem") == depth("table=orders")

    def test_json_returns_plan_unchanged(self):
        from benchbox.mcp.tools.analytics import _format_plan_response

        plan = _canonical_plan()

        assert _format_plan_response(plan, "json", "1", 5.0)["plan"] is plan

    def test_non_canonical_plan_is_a_validation_error(self):
        from benchbox.mcp.tools.analytics import _format_plan_response

        result = _format_plan_response({"type": "HashJoin", "children": []}, "tree", "1", 5.0)

        assert result["error_code"] == "VALIDATION_ERROR"

    def test_truncated_plan_is_flagged(self):
        from benchbox.mcp.tools.analytics import _format_plan_response

        plan = _canonical_plan()
        plan["logical_root"]["children"][0]["children"][0] = {
            "operator_type": "Join",
            "operator_id": "join1",
            "truncated_at_depth": 2,
            "children_omitted": 2,
        }

        summary = _format_plan_response(plan, "summary", "1", 5.0)["summary"]

        assert summary["truncated_at_depth"] == 2

    def test_truncated_tree_is_flagged(self):
        from benchbox.mcp.tools.analytics import _format_plan_response

        plan = _canonical_plan()
        plan["logical_root"]["children"][0]["children"][0] = {
            "operator_type": "Join",
            "operator_id": "join1",
            "truncated_at_depth": 2,
            "children_omitted": 2,
        }

        response = _format_plan_response(plan, "tree", "1", 5.0)

        assert response["truncated_at_depth"] == 2
        assert "plan truncated at depth 2" in response["plan_tree"]

    def test_complete_tree_has_no_truncation_marker(self):
        from benchbox.mcp.tools.analytics import _format_plan_response

        response = _format_plan_response(_canonical_plan(), "tree", "1", 5.0)

        assert "truncated_at_depth" not in response
        assert "truncated" not in response["plan_tree"]
