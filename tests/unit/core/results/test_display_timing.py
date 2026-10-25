from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from threading import Barrier, Thread
from types import SimpleNamespace

import pytest

from benchbox.core.results import timing as timing_module
from benchbox.core.results.display import (
    display_benchmark_list,
    display_configuration_summary,
    display_platform_list,
    display_results,
    display_verbose_config_feedback,
    print_completion_message,
    print_dry_run_summary,
    print_phase_header,
)
from benchbox.core.results.timing import QueryTiming, TimingAnalyzer, TimingCollector

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _real_time_with(**overrides):
    return SimpleNamespace(**{**vars(time), **overrides})


def test_display_results_success_and_failure(capsys):
    success_data = {
        "benchmark": "tpch",
        "scale_factor": 1,
        "platform": "duckdb",
        "success": True,
        "total_duration": 12.0,
        "schema_creation_time": 1.0,
        "data_loading_time": 2.0,
        "successful_queries": 10,
        "total_queries": 10,
        "total_execution_time": 9.0,
        "average_query_time": 0.9,
    }
    display_results(success_data, verbosity=1)
    out = capsys.readouterr().out
    assert "Benchmark: TPCH" in out
    assert "Benchmark Status: PASSED" in out
    assert "Queries: 10/10 successful" in out
    assert "benchmark completed" in out

    display_results({"benchmark": "tpch", "success": False, "total_queries": 1, "successful_queries": 0}, verbosity=0)
    out = capsys.readouterr().out
    assert "Benchmark Status: FAILED" in out
    assert "benchmark failed" in out


def test_display_benchmark_list_uses_registry_without_class_docstring(capsys):
    class NoDocMeta(type):
        def __getattribute__(cls, name):
            if name == "__doc__":
                raise AssertionError("benchmark display must not read class docstrings")
            return super().__getattribute__(name)

    class NoDoc(metaclass=NoDocMeta):
        pass

    display_benchmark_list({"tpch": NoDoc})

    assert "  tpch: Decision Support Benchmark" in capsys.readouterr().out


def test_display_benchmark_list_supports_custom_description_and_fallback(capsys):
    class CustomBenchmark:
        description = "Custom benchmark"

    display_benchmark_list({"custom": CustomBenchmark, "unknown": object})

    output = capsys.readouterr().out
    assert "  custom: Custom benchmark" in output
    assert "  unknown: No description available" in output
    assert output.index("custom:") < output.index("unknown:")


def test_display_helpers_and_phase_printers(capsys):
    display_platform_list({"duckdb": True, "snowflake": False}, get_requirements_func=lambda p: f"needs-{p}")
    display_benchmark_list({"alpha": type("Alpha", (), {"__doc__": "Alpha benchmark\nDetails"})})
    display_configuration_summary(
        {
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "platform": "duckdb",
            "phases": "power",
            "tuning_mode": "manual",
            "tuning_config": {"_metadata": {"configuration_type": "yaml"}},
        },
        verbosity=1,
    )
    display_verbose_config_feedback({"database_path": "/tmp/x.duckdb"}, "duckdb")
    display_verbose_config_feedback({"schema": "s", "catalog": "c"}, "databricks")
    display_verbose_config_feedback({"dataset_id": "d", "project_id": "p"}, "bigquery")
    display_verbose_config_feedback({"database": "db"}, "redshift")
    display_verbose_config_feedback({"data_path": "/tmp/data"}, "clickhouse")
    print_phase_header("power")
    print_completion_message("power")
    print_completion_message("load", output_location="/tmp/out")

    out = capsys.readouterr().out
    assert "Available platforms:" in out
    assert "needs-snowflake" in out
    assert "Available benchmarks:" in out
    assert "Tuning type: yaml" in out
    assert "Database file:" in out
    assert "BigQuery dataset:" in out
    assert "phase complete" in out


@dataclass
class _DryRunResult:
    benchmark_config: dict
    query_preview: dict
    platform_config: dict
    estimated_resources: dict
    queries: list[str]
    warnings: list[str]


def test_print_dry_run_summary_paths(capsys):
    result = _DryRunResult(
        benchmark_config={"name": "tpch", "scale_factor": 1, "display_name": "TPC-H"},
        query_preview={"query_count": 2, "estimated_time": "5m", "execution_context": "preview"},
        platform_config={
            "platform_name": "duckdb",
            "connection_mode": "local",
            "configuration": {"database_path": "/tmp/x.duckdb", "memory_limit": "2GB"},
        },
        estimated_resources={"estimated_data_size_mb": 128, "estimated_memory_usage_mb": 256, "cpu_cores_available": 8},
        queries=["Q1", "Q2"],
        warnings=["sample warning"],
    )
    print_dry_run_summary(result, "/tmp/out", saved_files={"json": "/tmp/out/a.json"})
    out = capsys.readouterr().out
    assert "Dry run completed successfully" in out
    assert "Artifacts:" in out
    assert "Resource Estimates:" in out
    assert "Warnings:" in out

    result2 = _DryRunResult({}, {}, {}, {}, [], [])
    print_dry_run_summary(result2, "/tmp/out2", saved_files=None)
    out = capsys.readouterr().out
    assert "Artifacts: JSON, YAML, and per-query SQL files emitted." in out


def test_query_timing_and_collector(monkeypatch):
    qt = QueryTiming(query_id="Q1", execution_time=2.0, rows_returned=10, bytes_processed=20)
    d = qt.to_dict()
    assert d["query_id"] == "Q1"
    assert qt.rows_per_second == 5.0
    assert qt.bytes_per_second == 10.0

    sequence = iter([100.0, 101.0, 200.0, 202.0, 300.0, 301.0])
    monkeypatch.setattr(timing_module, "time", _real_time_with(perf_counter=lambda: next(sequence)))

    collector = TimingCollector(enable_detailed_timing=True)
    with collector.time_query("Q2", "query-2"):
        with collector.time_phase("Q2", "parse"):
            pass
        collector.record_metric("Q2", "rows_returned", 42)
        collector.record_metric("Q2", "bytes_processed", 420)

    completed = collector.get_completed_timings()
    assert len(completed) == 1
    assert completed[0].query_id == "Q2"
    assert completed[0].execution_time == pytest.approx(202.0 - 100.0)
    assert completed[0].parse_time == pytest.approx(200.0 - 101.0)
    assert completed[0].rows_returned == 42

    collector_no_detail = TimingCollector(enable_detailed_timing=False)
    with collector_no_detail.time_phase("missing", "parse"):
        pass

    with pytest.raises(RuntimeError, match="boom"):
        with collector.time_query("QERR"):
            raise RuntimeError("boom")
    assert any(t.status == "ERROR" for t in collector.get_completed_timings())

    summary = collector.get_timing_summary()
    assert summary["total_queries"] >= 2
    collector.clear_completed_timings()
    assert collector.get_completed_timings() == []
    assert collector.get_timing_summary() == {}


def test_timing_collector_isolates_concurrent_repeated_query_ids():
    collector = TimingCollector()
    barrier = Barrier(2)
    errors = []

    def worker(label: str) -> None:
        try:
            with collector.time_query("Q1"):
                barrier.wait(timeout=5)
                collector.record_metric("Q1", "thread_id", label)
        except Exception as exc:
            errors.append((label, exc))

    threads = [Thread(target=worker, args=(label,)) for label in ("first", "second")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    completed = collector.get_completed_timings()
    assert sorted(t.thread_id for t in completed) == ["first", "second"]


def test_timing_summary_uses_a_consistent_completed_snapshot():
    collector = TimingCollector()
    with collector.time_query("Q1"):
        pass

    summary = collector.get_timing_summary()

    assert summary["total_queries"] == 1
    assert summary["successful_queries"] == 1
    assert summary["failed_queries"] == 0


def test_timing_analyzer_statistics_outliers_and_comparison():
    timings = [
        QueryTiming(query_id="Q1", execution_time=1.0, rows_returned=100),
        QueryTiming(query_id="Q2", execution_time=2.0, rows_returned=80, timing_breakdown={"parse": 0.2}),
        QueryTiming(query_id="Q3", execution_time=3.0, rows_returned=40, status="ERROR"),
        QueryTiming(query_id="Q4", execution_time=40.0, rows_returned=10),
    ]
    analyzer = TimingAnalyzer(timings)

    basic = analyzer.get_basic_statistics()
    assert basic["count"] == 3
    assert basic["mean"] > 0

    pcts = analyzer.get_percentiles([50, 90, -1, 101])
    assert 50 in pcts and 90 in pcts
    assert -1 not in pcts and 101 not in pcts

    perf = analyzer.analyze_query_performance()
    assert "status_breakdown" in perf
    assert perf["status_breakdown"]["ERROR"] == 1

    assert isinstance(analyzer.identify_outliers(method="iqr"), list)
    assert isinstance(analyzer.identify_outliers(method="zscore"), list)
    with pytest.raises(ValueError, match="Unknown outlier"):
        analyzer.identify_outliers(method="bogus")

    baseline = [QueryTiming(query_id="Q1", execution_time=2.0), QueryTiming(query_id="Q2", execution_time=2.0)]
    comparison = analyzer.compare_query_performance(baseline)
    assert "performance_change" in comparison or "error" in comparison
