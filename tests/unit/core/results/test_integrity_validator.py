from __future__ import annotations

import copy
import glob
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from benchbox.core.results.benchmark_specs import LEGACY_ALIASES, get_spec
from benchbox.core.results.integrity_validator import (
    CheckCategory,
    CheckStatus,
    IntegrityReport,
    ResultIntegrityValidator,
    validate_directory,
    validate_file,
)
from benchbox.core.results.models import BenchmarkResults
from benchbox.core.results.schema import build_result_payload

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


RESULTS_DIR = Path("benchmark_runs/results")


def _make_valid_tpch_result(**overrides: Any) -> dict[str, Any]:
    queries = []
    for qid in range(1, 23):
        for iteration in range(3):
            queries.append(
                {
                    "id": str(qid),
                    "ms": 100.0 + qid * 10 + iteration,
                    "rows": 100,
                    "iter": iteration,
                    "stream": 0,
                    "run_type": "measurement" if iteration > 0 else "warmup",
                    "status": "SUCCESS",
                }
            )

    result: dict[str, Any] = {
        "version": "2.1",
        "run": {
            "id": "test-001",
            "timestamp": "2026-01-01T12:00:00",
            "total_duration_ms": 5000.0,
            "query_time_ms": 4000.0,
            "iterations": 3,
            "streams": 1,
        },
        "benchmark": {
            "id": "tpch",
            "name": "TPC-H",
            "scale_factor": 1.0,
        },
        "platform": {"name": "DuckDB"},
        "config": {"mode": "standard"},
        "summary": {
            "queries": {"total": 66, "passed": 66, "failed": 0},
            "timing": {
                "total_ms": 5000.0,
                "avg_ms": 150.0,
                "min_ms": 110.0,
                "max_ms": 330.0,
                "geometric_mean_ms": 145.0,
                "p90_ms": 280.0,
                "p95_ms": 300.0,
                "p99_ms": 320.0,
                "stdev_ms": 50.0,
            },
            "tpc_metrics": {"power_at_size": 50000.0},
        },
        "phases": {
            "data_generation": {"status": "SUCCESS"},
            "schema_creation": {"status": "SUCCESS"},
            "data_loading": {"status": "SUCCESS", "duration_ms": 1200.0},
            "power_test": {"status": "COMPLETED"},
        },
        "queries": queries,
        "tables": [
            {"name": "customer", "rows": 150000},
            {"name": "lineitem", "rows": 6001215},
            {"name": "nation", "rows": 25},
            {"name": "orders", "rows": 1500000},
            {"name": "part", "rows": 200000},
            {"name": "partsupp", "rows": 800000},
            {"name": "region", "rows": 5},
            {"name": "supplier", "rows": 10000},
        ],
        "execution": {"type": "sql"},
        "environment": {"os": "linux"},
        "export": {"format": "json"},
    }
    for key, value in overrides.items():
        result[key] = value
    return result


def _find_latest_per_benchmark() -> dict[str, Path]:
    files = glob.glob(str(RESULTS_DIR / "*_sf1_duckdb_*_2026*.json"))
    benchmarks: dict[str, Path] = {}
    for f in files:
        path = Path(f)
        with open(path, encoding="utf-8") as fp:
            data = json.load(fp)
        bid = data.get("benchmark", {}).get("id", "unknown")
        canonical = LEGACY_ALIASES.get(bid, bid)
        if canonical not in benchmarks or os.path.getmtime(f) > os.path.getmtime(str(benchmarks[canonical])):
            benchmarks[canonical] = path
    return benchmarks


_LATEST_BY_BENCHMARK = _find_latest_per_benchmark() if RESULTS_DIR.is_dir() else {}


def _reference_file_ids() -> list[str]:
    return sorted(_LATEST_BY_BENCHMARK.keys())


@pytest.mark.skipif(
    not RESULTS_DIR.is_dir(),
    reason="benchmark_runs/results/ not present",
)
class TestIntegrityValidatorReferenceFiles:
    @pytest.mark.parametrize("benchmark_id", _reference_file_ids())
    def test_reference_file_does_not_fail(self, benchmark_id: str) -> None:
        path = _LATEST_BY_BENCHMARK[benchmark_id]
        report = validate_file(path)
        assert report.overall_status != CheckStatus.FAIL, f"{path.name} ({benchmark_id}) has FAILs: " + "; ".join(
            c.message for c in report.checks if c.status == CheckStatus.FAIL
        )

    def test_tpch_passes_sf1_row_counts(self) -> None:
        path = _LATEST_BY_BENCHMARK.get("tpch")
        if path is None:
            pytest.skip("No tpch reference file")
        report = validate_file(path)
        row_check = next((c for c in report.checks if c.name == "sf1_row_counts"), None)
        assert row_check is not None, "sf1_row_counts check not found"
        assert row_check.status == CheckStatus.PASS

    def test_transaction_primitives_passes_despite_failures(self) -> None:
        path = _LATEST_BY_BENCHMARK.get("transaction_primitives")
        if path is None:
            pytest.skip("No transaction_primitives reference file")
        report = validate_file(path)
        assert report.overall_status != CheckStatus.FAIL
        rate_check = next((c for c in report.checks if c.name == "success_rate"), None)
        assert rate_check is not None
        assert rate_check.status == CheckStatus.PASS

    def test_legacy_alias_resolves_to_correct_spec(self) -> None:

        for alias, canonical in LEGACY_ALIASES.items():
            spec = get_spec(alias)
            assert spec is not None, f"No spec found for alias {alias}"
            assert spec.benchmark_id == canonical, (
                f"Alias {alias} resolved to {spec.benchmark_id}, expected {canonical}"
            )


class TestIntegrityValidatorSyntheticData:
    def test_missing_required_key(self) -> None:
        data = _make_valid_tpch_result()
        del data["queries"]
        validator = ResultIntegrityValidator()
        report = validator.validate(data)
        assert report.overall_status == CheckStatus.FAIL
        schema_check = next(c for c in report.checks if c.name == "schema_v2")
        assert schema_check.status == CheckStatus.FAIL

    def test_query_count_math_wrong(self) -> None:
        data = _make_valid_tpch_result()
        data["summary"]["queries"] = {"total": 100, "passed": 50, "failed": 30}
        validator = ResultIntegrityValidator()
        report = validator.validate(data)
        check = next(c for c in report.checks if c.name == "query_count_math")
        assert check.status == CheckStatus.FAIL

    def test_negative_timing(self) -> None:
        data = _make_valid_tpch_result()
        data["summary"]["timing"]["avg_ms"] = -1.0
        validator = ResultIntegrityValidator()
        report = validator.validate(data)
        check = next(c for c in report.checks if c.name == "timing_non_negative")
        assert check.status == CheckStatus.FAIL

    def test_percentile_inversion(self) -> None:
        data = _make_valid_tpch_result()
        data["summary"]["timing"]["p90_ms"] = 300.0
        data["summary"]["timing"]["p95_ms"] = 200.0
        data["summary"]["timing"]["p99_ms"] = 320.0
        validator = ResultIntegrityValidator()
        report = validator.validate(data)
        check = next(c for c in report.checks if c.name == "percentile_ordering")
        assert check.status == CheckStatus.FAIL

    def test_invalid_phase_status(self) -> None:
        data = _make_valid_tpch_result()
        data["phases"]["power_test"]["status"] = "INVALID"
        validator = ResultIntegrityValidator()
        report = validator.validate(data)
        check = next(c for c in report.checks if c.name == "phase_status_values")
        assert check.status == CheckStatus.FAIL

    def test_query_entry_missing_field(self) -> None:
        data = _make_valid_tpch_result()
        del data["queries"][0]["ms"]
        validator = ResultIntegrityValidator()
        report = validator.validate(data)
        check = next(c for c in report.checks if c.name == "query_entry_fields")
        assert check.status == CheckStatus.FAIL

    def test_query_ms_negative(self) -> None:
        data = _make_valid_tpch_result()
        data["queries"][0]["ms"] = -5.0
        validator = ResultIntegrityValidator()
        report = validator.validate(data)
        check = next(c for c in report.checks if c.name == "query_ms_non_negative")
        assert check.status == CheckStatus.FAIL

    def test_missing_query_ids(self) -> None:
        data = _make_valid_tpch_result()

        data["queries"] = [q for q in data["queries"] if int(q["id"]) <= 11]
        validator = ResultIntegrityValidator()
        report = validator.validate(data)
        check = next(c for c in report.checks if c.name == "expected_query_ids")
        assert check.status == CheckStatus.FAIL

    def test_no_measurement_queries(self) -> None:
        data = _make_valid_tpch_result()
        for q in data["queries"]:
            q["iter"] = 0
            q["run_type"] = "warmup"
        validator = ResultIntegrityValidator()
        report = validator.validate(data)
        check = next(c for c in report.checks if c.name == "measurement_queries_present")
        assert check.status == CheckStatus.FAIL

    def test_missing_tables_object(self) -> None:
        data = _make_valid_tpch_result()
        del data["tables"]
        validator = ResultIntegrityValidator()
        report = validator.validate(data)
        check = next(c for c in report.checks if c.name == "tables_object")
        assert check.status == CheckStatus.WARN

    def test_power_phase_failed(self) -> None:
        data = _make_valid_tpch_result()
        data["phases"]["power_test"]["status"] = "FAILED"
        validator = ResultIntegrityValidator()
        report = validator.validate(data)
        check = next(c for c in report.checks if c.name == "power_phase_completed")
        assert check.status == CheckStatus.WARN

    def test_tpc_metrics_missing(self) -> None:
        data = _make_valid_tpch_result()
        del data["summary"]["tpc_metrics"]
        validator = ResultIntegrityValidator()
        report = validator.validate(data)
        check = next(c for c in report.checks if c.name == "tpc_metrics")
        assert check.status == CheckStatus.INFO

        assert report.overall_status == CheckStatus.PASS

    def test_avg_exceeds_max(self) -> None:
        data = _make_valid_tpch_result()
        data["summary"]["timing"]["avg_ms"] = 500.0
        data["summary"]["timing"]["max_ms"] = 330.0
        validator = ResultIntegrityValidator()
        report = validator.validate(data)
        check = next(c for c in report.checks if c.name == "avg_between_min_max")
        assert check.status == CheckStatus.FAIL

    def test_geomean_outside_range(self) -> None:
        data = _make_valid_tpch_result()
        data["summary"]["timing"]["geometric_mean_ms"] = 50.0
        validator = ResultIntegrityValidator()
        report = validator.validate(data)
        check = next(c for c in report.checks if c.name == "geomean_plausible")
        assert check.status == CheckStatus.FAIL

    def test_wrong_sf1_row_counts(self) -> None:
        data = _make_valid_tpch_result()

        for t in data["tables"]:
            if t["name"] == "lineitem":
                t["rows"] = 1000
        validator = ResultIntegrityValidator()
        report = validator.validate(data)
        check = next(c for c in report.checks if c.name == "sf1_row_counts")
        assert check.status == CheckStatus.FAIL

    def test_sf1_missing_tables_fails(self) -> None:
        data = _make_valid_tpch_result()

        data["tables"] = [t for t in data["tables"] if t["name"] != "lineitem"]
        validator = ResultIntegrityValidator()
        report = validator.validate(data)
        check = next(c for c in report.checks if c.name == "sf1_row_counts")
        assert check.status == CheckStatus.FAIL
        assert "missing" in check.message.lower()

    def test_timing_outlier(self) -> None:
        data = _make_valid_tpch_result()
        data["queries"][0]["ms"] = 2_000_000
        validator = ResultIntegrityValidator()
        report = validator.validate(data)
        check = next(c for c in report.checks if c.name == "timing_outliers")
        assert check.status == CheckStatus.WARN

    def test_load_time_zero(self) -> None:
        data = _make_valid_tpch_result()
        data["phases"]["data_loading"]["duration_ms"] = 0
        validator = ResultIntegrityValidator()
        report = validator.validate(data)
        check = next(c for c in report.checks if c.name == "load_time_nonzero")
        assert check.status == CheckStatus.WARN

    def test_high_failure_exempt(self) -> None:
        data = _make_valid_tpch_result()
        data["benchmark"]["id"] = "transaction_primitives"
        data["summary"]["queries"] = {"total": 100, "passed": 5, "failed": 95}
        validator = ResultIntegrityValidator()
        report = validator.validate(data)
        check = next(c for c in report.checks if c.name == "success_rate")
        assert check.status == CheckStatus.PASS

    def test_duplicate_execution(self) -> None:
        data = _make_valid_tpch_result()

        dupe = copy.deepcopy(data["queries"][0])
        data["queries"].append(dupe)
        validator = ResultIntegrityValidator()
        report = validator.validate(data)
        check = next(c for c in report.checks if c.name == "no_duplicate_executions")
        assert check.status == CheckStatus.WARN

    def test_passed_method(self) -> None:
        data = _make_valid_tpch_result()
        validator = ResultIntegrityValidator()
        report = validator.validate(data)
        assert report.passed()

    def test_has_warnings_method(self) -> None:
        data = _make_valid_tpch_result()
        del data["tables"]
        validator = ResultIntegrityValidator()
        report = validator.validate(data)
        assert report.has_warnings()

    def test_validate_file(self, tmp_path: Path) -> None:
        data = _make_valid_tpch_result()
        filepath = tmp_path / "test_result.json"
        filepath.write_text(json.dumps(data))
        report = validate_file(filepath)
        assert report.passed()
        assert report.file == str(filepath)

    def test_validate_directory(self, tmp_path: Path) -> None:
        for i in range(3):
            data = _make_valid_tpch_result()
            filepath = tmp_path / f"result_{i}.json"
            filepath.write_text(json.dumps(data))

        (tmp_path / "result_0.plans.json").write_text("{}")

        (tmp_path / "result_0.override.json").write_text("{}")
        reports = validate_directory(tmp_path)
        assert len(reports) == 3
        assert all(r.passed() for r in reports)

    def test_validate_directory_bad_json(self, tmp_path: Path) -> None:
        (tmp_path / "bad.json").write_text("not json")
        reports = validate_directory(tmp_path)
        assert len(reports) == 1
        assert reports[0].overall_status == CheckStatus.FAIL

    def test_validate_file_bad_json(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json")
        report = validate_file(bad_file)
        assert report.overall_status == CheckStatus.FAIL
        assert report.checks[0].name == "file_readable"


def _make_vector_search_result() -> BenchmarkResults:
    query_results = [
        {
            "query_id": f"Q{i}",
            "execution_time_ms": 100.0 + i * 10,
            "rows_returned": 10,
            "status": "SUCCESS",
            "iteration": 1,
            "stream_id": 0,
            "run_type": "measurement",
        }
        for i in range(1, 7)
    ]
    return BenchmarkResults(
        benchmark_name="vector_search",
        platform="duckdb",
        scale_factor=1.0,
        execution_id="vector-001",
        timestamp=datetime(2026, 1, 1),
        duration_seconds=5.0,
        total_queries=6,
        successful_queries=6,
        failed_queries=0,
        query_results=query_results,
        table_statistics={"vectors": 1000000, "vector_queries": 100},
        execution_metadata={
            "mode": "standard",
            "phase_status": {"power_test": {"status": "COMPLETED"}},
        },
    )


class TestVectorSearchExportIntegrity:
    def test_export_normalizes_query_ids(self) -> None:
        payload = build_result_payload(_make_vector_search_result())
        assert [q["id"] for q in payload["queries"]] == ["1", "2", "3", "4", "5", "6"]

    def test_complete_export_passes_expected_query_ids(self) -> None:
        payload = build_result_payload(_make_vector_search_result())
        payload["export"] = {"format": "json"}
        report = ResultIntegrityValidator().validate(payload)
        check = next(c for c in report.checks if c.name == "expected_query_ids")
        assert check.status == CheckStatus.PASS
        assert check.message.startswith("6/6 expected query IDs found")
        assert report.overall_status == CheckStatus.PASS

    def test_skipped_queries_satisfy_count_math(self) -> None:
        data = _make_valid_tpch_result()
        data["summary"]["queries"] = {"total": 6, "passed": 5, "failed": 0, "skipped": 1}
        report = ResultIntegrityValidator().validate(data)
        check = next(c for c in report.checks if c.name == "query_count_math")
        assert check.status == CheckStatus.PASS

    def test_skipped_queries_discounted_from_success_rate(self) -> None:
        data = _make_valid_tpch_result()
        data["summary"]["queries"] = {"total": 6, "passed": 5, "failed": 0, "skipped": 1}
        report = ResultIntegrityValidator().validate(data)
        check = next(c for c in report.checks if c.name == "success_rate")
        assert check.status == CheckStatus.PASS

    @pytest.mark.parametrize("benchmark_id", ["tpch", "vector_search", "tpchavoc", "unknown_benchmark"])
    def test_all_skipped_queries_do_not_pass_success_rate(self, benchmark_id: str) -> None:
        if benchmark_id == "vector_search":
            data = build_result_payload(_make_vector_search_result())
            data["export"] = {"format": "json"}
        else:
            data = _make_valid_tpch_result()
            data["benchmark"]["id"] = benchmark_id
        for query in data["queries"]:
            query["status"] = "SKIPPED"
        total = len(data["queries"])
        data["summary"]["queries"] = {"total": total, "passed": 0, "failed": 0, "skipped": total}

        report = ResultIntegrityValidator().validate(data)
        check = next(c for c in report.checks if c.name == "success_rate")
        assert check.status == CheckStatus.FAIL
        assert report.overall_status == CheckStatus.FAIL


