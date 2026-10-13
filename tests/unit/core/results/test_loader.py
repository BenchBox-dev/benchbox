from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from benchbox.core.results.canonical_json import canonical_json_text
from benchbox.core.results.loader import (
    ResultLoadError,
    UnsupportedSchemaError,
    find_latest_result,
    load_result_file,
    reconstruct_benchmark_results,
)
from benchbox.core.results.models import BenchmarkResults
from benchbox.core.results.query_plan_models import LogicalOperator, LogicalOperatorType, QueryPlanDAG
from benchbox.core.results.schema import build_plans_payload, build_result_payload, build_tuning_payload
from tests.fixtures.result_dict_fixtures import make_benchmark_results, make_v2_result_dict, write_v2_result_file

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestFindLatestResult:
    def test_find_latest_result_empty_directory(self, tmp_path):

        result = find_latest_result(tmp_path)
        assert result is None

    def test_find_latest_result_nonexistent_directory(self):

        result = find_latest_result(Path("/nonexistent/directory"))
        assert result is None

    def test_find_latest_result_returns_most_recent(self, tmp_path):

        older_result = tmp_path / "result1.json"
        newer_result = tmp_path / "result2.json"

        write_v2_result_file(older_result, version="2.0", timestamp="2025-01-01T10:00:00")
        write_v2_result_file(newer_result, version="2.0", timestamp="2025-01-02T10:00:00")

        result = find_latest_result(tmp_path)
        assert result == newer_result

    def test_find_latest_result_with_benchmark_filter(self, tmp_path):

        tpch_result = tmp_path / "tpch.json"
        tpcds_result = tmp_path / "tpcds.json"

        write_v2_result_file(tpch_result, version="2.0", timestamp="2025-01-02T10:00:00")
        write_v2_result_file(
            tpcds_result,
            version="2.0",
            benchmark_id="tpcds",
            timestamp="2025-01-01T10:00:00",
        )

        result = find_latest_result(tmp_path, benchmark="tpch")
        assert result == tpch_result

        result = find_latest_result(tmp_path, benchmark="tpcds")
        assert result == tpcds_result

    def test_find_latest_result_with_platform_filter(self, tmp_path):

        duckdb_result = tmp_path / "duckdb.json"
        databricks_result = tmp_path / "databricks.json"

        write_v2_result_file(
            duckdb_result,
            version="2.0",
            platform="DuckDB",
            timestamp="2025-01-02T10:00:00",
        )
        write_v2_result_file(
            databricks_result,
            version="2.0",
            platform="Databricks",
            timestamp="2025-01-01T10:00:00",
        )

        result = find_latest_result(tmp_path, platform="duckdb")
        assert result == duckdb_result

        result = find_latest_result(tmp_path, platform="databricks")
        assert result == databricks_result

    def test_find_latest_result_with_both_filters(self, tmp_path):

        matching_result = tmp_path / "match.json"
        non_matching_result = tmp_path / "nomatch.json"

        write_v2_result_file(
            matching_result,
            version="2.0",
            platform="DuckDB",
            timestamp="2025-01-02T10:00:00",
        )
        write_v2_result_file(
            non_matching_result,
            version="2.0",
            benchmark_id="tpcds",
            platform="Databricks",
            timestamp="2025-01-03T10:00:00",
        )

        result = find_latest_result(tmp_path, benchmark="tpch", platform="duckdb")
        assert result == matching_result

    def test_find_latest_result_skips_corrupted_files(self, tmp_path):

        good_result = tmp_path / "good.json"
        bad_result = tmp_path / "bad.json"

        write_v2_result_file(good_result, version="2.0", timestamp="2025-01-01T10:00:00")

        with open(bad_result, "w", encoding="utf-8") as f:
            f.write("{ invalid json")

        result = find_latest_result(tmp_path)
        assert result == good_result

    def test_find_latest_result_no_matching_filters(self, tmp_path):

        result_file = tmp_path / "result.json"
        write_v2_result_file(result_file, version="2.0")

        result = find_latest_result(tmp_path, benchmark="nonexistent")
        assert result is None

    def test_find_latest_result_skips_companion_files(self, tmp_path):
        result_file = tmp_path / "result.json"
        plans_file = tmp_path / "result.plans.json"
        tuning_file = tmp_path / "result.tuning.json"

        write_v2_result_file(result_file, version="2.0")

        with open(plans_file, "w", encoding="utf-8") as f:
            json.dump({"version": "2.0", "plans_captured": 1}, f)
        with open(tuning_file, "w", encoding="utf-8") as f:
            json.dump({"version": "2.0", "clauses": {}}, f)

        result = find_latest_result(tmp_path)
        assert result == result_file

    def test_find_latest_result_skips_v1_files(self, tmp_path):
        v2_result = tmp_path / "v2_result.json"
        v1_result = tmp_path / "v1_result.json"

        write_v2_result_file(v2_result, version="2.0", timestamp="2025-01-01T10:00:00")

        v1_data = {
            "schema_version": "1.0",
            "benchmark": {"id": "tpch", "name": "TPC-H"},
            "execution": {"platform": "DuckDB", "timestamp": "2025-01-02T10:00:00"},
        }
        with open(v1_result, "w", encoding="utf-8") as f:
            json.dump(v1_data, f)

        result = find_latest_result(tmp_path)
        assert result == v2_result


class TestLoadResultFile:
    def test_load_result_file_success(self, tmp_path):
        result_file = tmp_path / "result.json"
        write_v2_result_file(
            result_file,
            version="2.0",
            platform="DuckDB",
            scale_factor=1.0,
        )

        result, raw_data = load_result_file(result_file)

        assert isinstance(result, BenchmarkResults)
        assert result.benchmark_name == "TPC-H"
        assert result.platform == "DuckDB"
        assert result.scale_factor == 1.0
        assert result.total_queries == 22
        assert raw_data["version"] == "2.0"

    def test_load_result_file_loads_companion_with_extra_dots_in_basename(self, tmp_path):
        bundle_dir = tmp_path / "bundle.json"
        bundle_dir.mkdir()
        result_file = bundle_dir / "result_sf0.1.json"
        write_v2_result_file(
            result_file,
            version="2.0",
            platform="DuckDB",
            scale_factor=0.1,
            queries=[{"id": "1", "ms": 100.0, "rows": 4}],
        )
        plans_file = bundle_dir / "result_sf0.1.plans.json"
        with open(plans_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": "2.0",
                    "run_id": "test",
                    "plans_captured": 1,
                    "capture_failures": 0,
                    "queries": {
                        "1": {
                            "fingerprint": "a" * 64,
                            "plan": {"query_id": "1", "platform": "duckdb", "logical_root": None},
                        }
                    },
                },
                f,
            )
        assert not (bundle_dir / "result_sf0.plans.json").exists()
        assert not (tmp_path / "bundle.plans.json").exists()

        result, _raw_data = load_result_file(result_file)

        assert result.query_plans_captured == 1
        assert result.query_results[0]["plan_fingerprint"] == "a" * 64

    def test_load_result_file_not_found(self):

        with pytest.raises(FileNotFoundError):
            load_result_file(Path("/nonexistent/file.json"))

    def test_corrupt_plans_companion_sets_plans_load_error(self, tmp_path, caplog):
        import logging

        result_file = tmp_path / "r.json"
        write_v2_result_file(
            result_file,
            version="2.0",
            platform="DuckDB",
            queries=[{"id": "1", "ms": 100.0, "rows": 4}],
        )
        (tmp_path / "r.plans.json").write_text("{ not valid json", encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger="benchbox.core.results.loader"):
            result, _raw = load_result_file(result_file)

        assert result.plans_load_error is not None
        assert "r.plans.json" in result.plans_load_error
        assert any("could not be loaded" in rec.getMessage() for rec in caplog.records)

    def test_no_plans_companion_leaves_plans_load_error_none(self, tmp_path):
        result_file = tmp_path / "r.json"
        write_v2_result_file(
            result_file,
            version="2.0",
            platform="DuckDB",
            queries=[{"id": "1", "ms": 100.0, "rows": 4}],
        )

        result, _raw = load_result_file(result_file)

        assert result.plans_load_error is None

    def test_load_result_file_invalid_json(self, tmp_path):

        result_file = tmp_path / "invalid.json"

        with open(result_file, "w", encoding="utf-8") as f:
            f.write("{ invalid json")

        with pytest.raises(ResultLoadError, match="Invalid JSON"):
            load_result_file(result_file)

    def test_load_result_file_unsupported_schema_v1(self, tmp_path):
        result_file = tmp_path / "old_schema.json"

        data = {
            "schema_version": "1.0",
            "benchmark": {"id": "tpch", "name": "TPC-H"},
            "execution": {"platform": "DuckDB"},
        }

        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

        with pytest.raises(UnsupportedSchemaError, match="Unsupported schema version"):
            load_result_file(result_file)

    def test_load_result_file_unsupported_schema_unknown(self, tmp_path):

        result_file = tmp_path / "future_schema.json"

        data = {
            "version": "3.0",
            "benchmark": {"id": "tpch", "name": "TPC-H"},
        }

        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

        with pytest.raises(UnsupportedSchemaError, match="Unsupported schema version"):
            load_result_file(result_file)

    @pytest.mark.parametrize(
        ("version", "message"),
        [
            ("2.99", "runtime loader schema policy"),
            ("2.x", "runtime loader schema policy"),
            (None, "<missing>"),
        ],
    )
    def test_load_result_file_reports_loader_policy_for_unsupported_versions(
        self,
        tmp_path,
        version,
        message,
    ):
        result_file = tmp_path / "unsupported_schema.json"
        data = {
            "benchmark": {"id": "tpch", "name": "TPC-H"},
            "run": {"id": "test", "timestamp": "2026-05-21T00:00:00", "total_duration_ms": 1},
        }
        if version is not None:
            data["version"] = version

        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

        with pytest.raises(UnsupportedSchemaError) as exc_info:
            load_result_file(result_file)

        assert message in str(exc_info.value)
        assert "schema versions 2.0, 2.1, 2.2, and 2.3" in str(exc_info.value)


class TestReconstructBenchmarkResults:
    def test_rejects_row_count_validation_before_schema_2_2(self):
        data = make_v2_result_dict(
            version="2.1",
            queries=[
                {
                    "id": "1",
                    "ms": 100.0,
                    "rows": 4,
                    "row_count_validation": {"status": "PASSED", "expected": 4, "actual": 4},
                }
            ],
        )

        with pytest.raises(ValueError, match="row_count_validation requires schema version 2.2"):
            reconstruct_benchmark_results(data)

    def test_accepts_valid_row_count_validation_in_schema_2_2(self):
        evidence = {"status": "PASSED", "expected": 4, "actual": 4}
        data = make_v2_result_dict(
            version="2.2",
            queries=[{"id": "1", "ms": 100.0, "rows": 4, "row_count_validation": evidence}],
        )

        result = reconstruct_benchmark_results(data)

        assert result.query_results[0]["row_count_validation"] == evidence

    def test_reconstruct_minimal_result(self):
        data = make_v2_result_dict(
            version="2.0",
            benchmark_name="TPC-H Benchmark",
            platform="DuckDB",
            scale_factor=1.0,
            execution_id="test123",
            timestamp="2025-01-01T10:00:00",
            query_time_ms=800,
            total_queries=0,
            passed_queries=0,
            failed_queries=0,
            total_ms=0,
        )

        result = reconstruct_benchmark_results(data)

        assert isinstance(result, BenchmarkResults)
        assert result.benchmark_name == "TPC-H Benchmark"
        assert result.platform == "DuckDB"
        assert result.execution_id == "test123"
        assert result.scale_factor == 1.0

    def test_reconstruct_complete_result(self):

        data = make_v2_result_dict(
            version="2.0",
            benchmark_name="TPC-H Benchmark",
            platform="DuckDB",
            platform_version="1.0.0",
            scale_factor=10.0,
            execution_id="test123",
            timestamp="2025-01-01T10:00:00",
            total_duration_ms=5000,
            query_time_ms=4000,
            iterations=3,
            total_ms=5000,
            queries=[
                {"id": "1", "ms": 100.0, "rows": 4},
                {"id": "2", "ms": 50.0, "rows": 100},
            ],
        )
        data["benchmark"]["mode"] = "power_test"
        data["platform"]["variant"] = "in-memory"
        data["summary"]["timing"].update(
            {
                "avg_ms": 227.27,
                "min_ms": 50,
                "max_ms": 500,
                "geometric_mean_ms": 150.5,
            }
        )
        data["summary"]["data"] = {"rows_loaded": 1000000, "load_time_ms": 1000}
        data["summary"]["validation"] = "passed"
        data["summary"]["tpc_metrics"] = {
            "power_at_size": 1234.5,
            "throughput_at_size": 5678.9,
            "qphh_at_size": 9012.3,
        }
        data["environment"] = {
            "os": "Darwin 24.0.0",
            "arch": "arm64",
            "cpu_count": 16,
            "memory_gb": 64,
            "python": "3.12.0",
        }
        data["tables"] = {
            "lineitem": {"rows": 600000, "load_ms": 500},
            "orders": {"rows": 150000, "load_ms": 200},
        }

        result = reconstruct_benchmark_results(data)

        assert result.benchmark_name == "TPC-H Benchmark"
        assert result.platform == "DuckDB"
        assert result.scale_factor == 10.0
        assert result.execution_id == "test123"

        assert result.total_queries == 22
        assert result.successful_queries == 22
        assert result.failed_queries == 0
        assert len(result.query_results) == 2

        assert result.total_execution_time == 5.0
        assert abs(result.average_query_time - 0.22727) < 0.0001
        assert result.data_loading_time == 1.0

        assert result.power_at_size == 1234.5
        assert result.throughput_at_size == 5678.9
        assert result.qph_at_size == 9012.3

        assert result.validation_status == "passed"
        assert result.test_execution_type == "power_test"

    def test_reconstruct_handles_missing_optional_fields(self):

        data = make_v2_result_dict(
            version="2.0",
            benchmark_id="test",
            benchmark_name="Test Benchmark",
            platform="Test Platform",
            scale_factor=1.0,
            execution_id="",
            timestamp="2025-01-01T10:00:00",
            total_duration_ms=0,
            query_time_ms=0,
            total_queries=0,
            passed_queries=0,
            failed_queries=0,
            total_ms=0,
        )

        result = reconstruct_benchmark_results(data)

        assert result.benchmark_name == "Test Benchmark"
        assert result.platform == "Test Platform"
        assert result.scale_factor == 1.0
        assert result.execution_id == ""
        assert result.total_queries == 0
        assert result.total_execution_time == 0.0

    def test_reconstruct_timestamp_parsing(self):

        data = make_v2_result_dict(
            version="2.0",
            benchmark_id="test",
            benchmark_name="Test",
            platform="Test",
            scale_factor=1.0,
            execution_id="test",
            timestamp="2025-10-28T15:30:45.123456",
            query_time_ms=800,
            total_queries=0,
            passed_queries=0,
            failed_queries=0,
            total_ms=0,
        )

        result = reconstruct_benchmark_results(data)

        assert isinstance(result.timestamp, datetime)
        assert result.timestamp.year == 2025
        assert result.timestamp.month == 10
        assert result.timestamp.day == 28

    def test_round_trip_preserves_values(self):
        query_results = [
            {"query_id": "1", "execution_time_ms": 100, "rows_returned": 4, "status": "SUCCESS"},
            {"query_id": "2", "execution_time_ms": 200, "rows_returned": 8, "status": "SUCCESS"},
            {"query_id": "3", "execution_time_ms": 300, "rows_returned": 12, "status": "SUCCESS"},
            {"query_id": "4", "execution_time_ms": 400, "rows_returned": 16, "status": "SUCCESS"},
        ]

        original = BenchmarkResults(
            benchmark_name="Test",
            _benchmark_id_override="test",
            platform="Test",
            scale_factor=1.0,
            execution_id="test123",
            timestamp=datetime(2025, 11, 1, 12, 0, 0),
            duration_seconds=10.0,
            total_queries=4,
            successful_queries=4,
            failed_queries=0,
            data_loading_time=1.456,
            query_results=query_results,
        )

        exported = build_result_payload(original)
        reimported = reconstruct_benchmark_results(exported)

        assert abs(reimported.total_execution_time - 1.0) < 0.01
        assert abs(reimported.average_query_time - 0.25) < 0.01

        assert abs(reimported.data_loading_time - 1.456) < 0.01

        assert reimported.geometric_mean_execution_time is not None
        assert abs(reimported.geometric_mean_execution_time - 0.2134) < 0.01

    def test_reconstruct_query_results_with_iteration(self):

        data = make_v2_result_dict(
            version="2.0",
            benchmark_id="test",
            benchmark_name="Test",
            platform="Test",
            scale_factor=1.0,
            execution_id="test",
            timestamp="2025-01-01T10:00:00",
            query_time_ms=800,
            iterations=3,
            total_queries=3,
            passed_queries=3,
            failed_queries=0,
            total_ms=300,
            queries=[
                {"id": "1", "ms": 100.0, "rows": 4, "iter": 1},
                {"id": "1", "ms": 90.0, "rows": 4, "iter": 2},
                {"id": "1", "ms": 110.0, "rows": 4, "iter": 3},
            ],
        )

        result = reconstruct_benchmark_results(data)

        assert len(result.query_results) == 3
        assert result.query_results[0]["iteration"] == 1
        assert result.query_results[1]["iteration"] == 2
        assert result.query_results[2]["iteration"] == 3

    def test_reconstruct_query_results_with_stream(self):

        data = make_v2_result_dict(
            version="2.0",
            benchmark_id="test",
            benchmark_name="Test",
            platform="Test",
            scale_factor=1.0,
            execution_id="test",
            timestamp="2025-01-01T10:00:00",
            query_time_ms=800,
            streams=2,
            total_queries=2,
            passed_queries=2,
            failed_queries=0,
            total_ms=200,
            queries=[
                {"id": "1", "ms": 100.0, "rows": 4, "stream": 1},
                {"id": "1", "ms": 90.0, "rows": 4, "stream": 2},
            ],
        )

        result = reconstruct_benchmark_results(data)

        assert len(result.query_results) == 2
        assert result.query_results[0]["stream_id"] == 1
        assert result.query_results[1]["stream_id"] == 2

    def test_reconstruct_query_results_multi_stream_plans_reattach_per_stream(self):
        data = make_v2_result_dict(
            version="2.0",
            benchmark_id="test",
            benchmark_name="Test",
            platform="Test",
            scale_factor=1.0,
            execution_id="test",
            timestamp="2025-01-01T10:00:00",
            query_time_ms=200,
            streams=2,
            total_queries=2,
            passed_queries=2,
            failed_queries=0,
            total_ms=200,
            queries=[
                {"id": "1", "ms": 100.0, "rows": 4, "stream": 1},
                {"id": "1", "ms": 90.0, "rows": 4, "stream": 2},
            ],
        )
        plans_data = {
            "queries": {
                "1#1": {"fingerprint": "a" * 64, "plan": {"query_id": "1", "platform": "duckdb", "logical_root": None}},
                "1#2": {"fingerprint": "b" * 64, "plan": {"query_id": "1", "platform": "duckdb", "logical_root": None}},
            }
        }

        result = reconstruct_benchmark_results(data, plans_data=plans_data)

        assert len(result.query_results) == 2
        assert result.query_results[0]["stream_id"] == 1
        assert result.query_results[0]["plan_fingerprint"] == "a" * 64
        assert result.query_results[1]["stream_id"] == 2
        assert result.query_results[1]["plan_fingerprint"] == "b" * 64

    def test_reconstruct_query_results_cross_phase_same_stream_id_reattach(self):
        data = make_v2_result_dict(
            version="2.0",
            benchmark_id="test",
            benchmark_name="Test",
            platform="Test",
            scale_factor=1.0,
            execution_id="test",
            timestamp="2025-01-01T10:00:00",
            query_time_ms=200,
            streams=2,
            total_queries=2,
            passed_queries=2,
            failed_queries=0,
            total_ms=200,
            queries=[
                {"id": "6", "ms": 100.0, "rows": 4, "stream": 0, "test_type": "power"},
                {"id": "6", "ms": 90.0, "rows": 4, "stream": 0, "test_type": "throughput"},
            ],
        )
        plans_data = {
            "queries": {
                "6#0:power": {
                    "fingerprint": "a" * 64,
                    "plan": {"query_id": "6", "platform": "duckdb", "logical_root": None},
                },
                "6#0:throughput": {
                    "fingerprint": "b" * 64,
                    "plan": {"query_id": "6", "platform": "duckdb", "logical_root": None},
                },
            }
        }

        result = reconstruct_benchmark_results(data, plans_data=plans_data)

        assert len(result.query_results) == 2
        assert result.query_results[0]["test_type"] == "power"
        assert result.query_results[0]["plan_fingerprint"] == "a" * 64
        assert result.query_results[1]["test_type"] == "throughput"
        assert result.query_results[1]["plan_fingerprint"] == "b" * 64

    def test_reconstruct_query_results_single_stream_bare_key_still_works(self):
        data = make_v2_result_dict(
            version="2.0",
            benchmark_id="test",
            benchmark_name="Test",
            platform="Test",
            scale_factor=1.0,
            execution_id="test",
            timestamp="2025-01-01T10:00:00",
            query_time_ms=100,
            total_queries=1,
            passed_queries=1,
            failed_queries=0,
            total_ms=100,
            queries=[{"id": "1", "ms": 100.0, "rows": 4}],
        )
        plans_data = {
            "queries": {
                "1": {"fingerprint": "c" * 64, "plan": {"query_id": "1", "platform": "duckdb", "logical_root": None}},
            }
        }

        result = reconstruct_benchmark_results(data, plans_data=plans_data)

        assert len(result.query_results) == 1
        assert result.query_results[0]["plan_fingerprint"] == "c" * 64

    def test_reconstruct_with_errors(self):

        data = make_v2_result_dict(
            version="2.0",
            benchmark_id="test",
            benchmark_name="Test",
            platform="Test",
            scale_factor=1.0,
            execution_id="test",
            timestamp="2025-01-01T10:00:00",
            query_time_ms=500,
            total_queries=2,
            passed_queries=1,
            failed_queries=1,
            total_ms=500,
            queries=[
                {"id": "1", "ms": 500.0, "rows": 4},
            ],
            errors=[
                {"phase": "query", "query_id": "2", "type": "QueryError", "message": "Syntax error"},
            ],
        )

        result = reconstruct_benchmark_results(data)

        assert len(result.query_results) == 2
        failed_query = [q for q in result.query_results if q.get("status") == "FAILED"]
        assert len(failed_query) == 1
        assert failed_query[0]["query_id"] == "2"
        assert failed_query[0]["error_type"] == "QueryError"

    def test_reconstruct_does_not_duplicate_current_format_failure(self):
        data = make_v2_result_dict(
            version="2.1",
            benchmark_id="test",
            benchmark_name="Test",
            platform="Test",
            scale_factor=1.0,
            execution_id="test",
            timestamp="2025-01-01T10:00:00",
            query_time_ms=100,
            total_queries=2,
            passed_queries=1,
            failed_queries=1,
            total_ms=100,
            queries=[
                {
                    "id": "1",
                    "ms": 100.0,
                    "rows": 4,
                    "iter": 1,
                    "stream": 0,
                    "run_type": "measurement",
                    "status": "SUCCESS",
                },
                {"id": "2", "iter": 1, "stream": 0, "run_type": "measurement", "status": "FAILED"},
            ],
            errors=[
                {"phase": "query", "query_id": "2", "type": "QueryError", "message": "Syntax error"},
            ],
        )

        result = reconstruct_benchmark_results(data)

        failed_rows = [q for q in result.query_results if q.get("status") == "FAILED"]
        assert len(result.query_results) == 2
        assert len(failed_rows) == 1
        assert failed_rows[0]["query_id"] == "2"
        assert failed_rows[0]["error_type"] == "QueryError"
        assert failed_rows[0]["error_message"] == "Syntax error"

    def test_reconstruct_attaches_distinct_error_to_each_repeated_failure(self):
        data = make_v2_result_dict(
            version="2.1",
            benchmark_id="test",
            benchmark_name="Test",
            platform="Test",
            scale_factor=1.0,
            execution_id="test",
            timestamp="2025-01-01T10:00:00",
            query_time_ms=100,
            total_queries=2,
            passed_queries=0,
            failed_queries=2,
            total_ms=100,
            queries=[
                {"id": "1", "iter": 1, "stream": 0, "run_type": "measurement", "status": "FAILED"},
                {"id": "1", "iter": 2, "stream": 0, "run_type": "measurement", "status": "FAILED"},
            ],
            errors=[
                {"phase": "query", "query_id": "1", "type": "TimeoutError", "message": "iteration 1 timed out"},
                {"phase": "query", "query_id": "1", "type": "MemoryError", "message": "iteration 2 ran out of memory"},
            ],
        )

        result = reconstruct_benchmark_results(data)

        failed_rows = [q for q in result.query_results if q.get("status") == "FAILED"]
        assert len(failed_rows) == 2
        assert failed_rows[0]["iteration"] == 1
        assert failed_rows[0]["error_type"] == "TimeoutError"
        assert failed_rows[0]["error_message"] == "iteration 1 timed out"
        assert failed_rows[1]["iteration"] == 2
        assert failed_rows[1]["error_type"] == "MemoryError"
        assert failed_rows[1]["error_message"] == "iteration 2 ran out of memory"

    def test_reconstruct_legacy_failure_not_suppressed_by_successful_same_id_row(self):
        data = make_v2_result_dict(
            version="2.0",
            benchmark_id="test",
            benchmark_name="Test",
            platform="Test",
            scale_factor=1.0,
            execution_id="test",
            timestamp="2025-01-01T10:00:00",
            query_time_ms=100,
            total_queries=2,
            passed_queries=1,
            failed_queries=1,
            total_ms=100,
            queries=[
                {"id": "1", "ms": 100.0, "rows": 4},
            ],
            errors=[
                {"phase": "query", "query_id": "1", "type": "QueryError", "message": "iteration 2 failed"},
            ],
        )

        result = reconstruct_benchmark_results(data)

        failed_rows = [q for q in result.query_results if q.get("status") == "FAILED"]
        assert len(result.query_results) == 2
        assert len(failed_rows) == 1
        assert failed_rows[0]["query_id"] == "1"
        assert failed_rows[0]["error_type"] == "QueryError"
        assert failed_rows[0]["error_message"] == "iteration 2 failed"

    def test_reconstruct_preserves_warmup_iteration_zero_and_stream_zero(self):
        data = make_v2_result_dict(
            version="2.1",
            benchmark_id="test",
            benchmark_name="Test",
            platform="Test",
            scale_factor=1.0,
            execution_id="test",
            timestamp="2025-01-01T10:00:00",
            query_time_ms=100,
            total_queries=1,
            passed_queries=1,
            failed_queries=0,
            total_ms=100,
            queries=[
                {
                    "id": "1",
                    "ms": 90.0,
                    "rows": 4,
                    "iter": 0,
                    "stream": 0,
                    "run_type": "warmup",
                    "status": "SUCCESS",
                },
            ],
        )

        result = reconstruct_benchmark_results(data)

        assert len(result.query_results) == 1
        row = result.query_results[0]
        assert row["iteration"] == 0
        assert row["stream_id"] == 0
        assert row["run_type"] == "warmup"


class TestExportLoadReexportRoundtrip:
    def test_export_load_reexport_roundtrip_byte_identical(self):
        root = LogicalOperator(operator_type=LogicalOperatorType.SCAN, operator_id="scan_1", table_name="lineitem")
        plan = QueryPlanDAG(query_id="2", platform="duckdb", logical_root=root)

        query_results = [
            {
                "query_id": "1",
                "status": "SUCCESS",
                "execution_time_ms": 120.0,
                "rows_returned": 4,
                "iteration": 0,
                "stream_id": 0,
                "run_type": "warmup",
            },
            {
                "query_id": "1",
                "status": "SUCCESS",
                "execution_time_ms": 100.0,
                "rows_returned": 4,
                "iteration": 1,
                "stream_id": 0,
                "run_type": "measurement",
            },
            {
                "query_id": "2",
                "status": "SUCCESS",
                "execution_time_ms": 200.0,
                "rows_returned": 8,
                "iteration": 1,
                "stream_id": 1,
                "run_type": "measurement",
                "query_plan": plan,
                "plan_fingerprint": plan.plan_fingerprint,
                "plan_capture_time_ms": 0.0,
            },
            {
                "query_id": "3",
                "status": "FAILED",
                "iteration": 1,
                "stream_id": 0,
                "run_type": "measurement",
                "error_type": "QueryError",
                "error_message": "Syntax error",
            },
        ]

        original = make_benchmark_results(
            benchmark_id="tpch",
            benchmark_name="tpch",
            platform="duckdb",
            scale_factor=1.0,
            execution_id="roundtrip-001",
            timestamp=datetime(2025, 1, 1, 12, 0, 0),
            duration_seconds=10.0,
            total_queries=4,
            successful_queries=3,
            failed_queries=1,
            query_plans_captured=1,
            query_results=query_results,
        )

        exported = build_result_payload(original)
        plans_payload = build_plans_payload(original)
        assert plans_payload is not None

        assert plans_payload["queries"]["2"]["capture_time_ms"] == 0.0

        reimported = reconstruct_benchmark_results(exported, plans_data=plans_payload)
        re_exported = build_result_payload(reimported)
        re_plans_payload = build_plans_payload(reimported)

        assert canonical_json_text(exported) == canonical_json_text(re_exported)
        assert re_plans_payload is not None
        assert canonical_json_text(plans_payload) == canonical_json_text(re_plans_payload)

    def test_recorded_phases_survive_reload_and_anonymized_reexport(self, tmp_path):
        from benchbox.core.results.exporter import ResultExporter

        original = make_benchmark_results(
            benchmark_id="tpch",
            benchmark_name="tpch",
            platform="duckdb",
            scale_factor=1.0,
            execution_id="phases-roundtrip-001",
            timestamp=datetime(2026, 1, 1, 12, 0, 0),
            duration_seconds=10.0,
            total_queries=1,
            successful_queries=1,
            failed_queries=0,
            query_results=[
                {
                    "query_id": "1",
                    "status": "SUCCESS",
                    "execution_time_ms": 100.0,
                    "rows_returned": 4,
                    "iteration": 1,
                    "stream_id": 0,
                    "run_type": "measurement",
                }
            ],
        )

        exported = ResultExporter(output_dir=tmp_path, anonymize=False).export_result(original, formats=["json"])
        payload_path = exported["json"]
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        payload["phases"] = {
            "data_generation": {"status": "SUCCESS", "duration_ms": 0},
            "schema_creation": {"status": "SUCCESS", "duration_ms": 3248},
            "data_loading": {"status": "SUCCESS", "duration_ms": 49488},
            "validation": {"status": "PASSED", "duration_ms": 2489},
            "power_test": {"status": "COMPLETED", "duration_ms": 96817},
            "throughput_test": {"status": "NOT_RUN"},
        }
        payload_path.write_text(json.dumps(payload), encoding="utf-8")

        reloaded, _raw = load_result_file(payload_path)
        re_exported = ResultExporter(output_dir=tmp_path / "public", anonymize=True).export_result(
            reloaded, formats=["json"]
        )
        reexported = json.loads(re_exported["json"].read_text(encoding="utf-8"))

        for name, duration in (
            ("data_generation", 0),
            ("schema_creation", 3248),
            ("data_loading", 49488),
            ("validation", 2489),
            ("power_test", 96817),
        ):
            assert reexported["phases"][name]["status"] == payload["phases"][name]["status"]
            assert reexported["phases"][name]["duration_ms"] == duration
        assert reexported["phases"]["throughput_test"] == {"status": "NOT_RUN"}


class TestTuningProvenanceLoaderFidelity:
    def test_legacy_summary_only_bundle_reconstructs_yaml_sentinel(self):
        data = make_v2_result_dict(
            benchmark_id="tpch",
            platform="duckdb",
            execution_id="legacy-tuning-001",
        )
        data["platform"]["tuning"] = {"source": "yaml"}

        result = reconstruct_benchmark_results(data, tuning_data=None)

        assert result.tuning_source_file == "yaml"

    def test_legacy_summary_only_bundle_with_auto_source_has_no_sentinel(self):
        data = make_v2_result_dict(
            benchmark_id="tpch",
            platform="duckdb",
            execution_id="legacy-tuning-002",
        )
        data["platform"]["tuning"] = {"source": "auto"}

        result = reconstruct_benchmark_results(data, tuning_data=None)

        assert result.tuning_source_file is None

    def test_tuning_source_survives_reconstruct_and_reexport_roundtrip(self):
        original = make_benchmark_results(
            benchmark_id="tpch",
            benchmark_name="tpch",
            platform="duckdb",
            scale_factor=0.01,
            execution_id="tuning-roundtrip-001",
            timestamp=datetime(2026, 7, 16, 12, 0, 0),
            duration_seconds=1.0,
            total_queries=1,
            successful_queries=1,
            failed_queries=0,
            query_results=[
                {
                    "query_id": "1",
                    "status": "SUCCESS",
                    "execution_time_ms": 10.0,
                    "rows_returned": 1,
                    "iteration": 1,
                    "stream_id": 0,
                    "run_type": "measurement",
                }
            ],
            tunings_applied={"table_tunings": {"lineitem": {"table_name": "lineitem", "sorting": []}}},
            tuning_source="auto_discovered",
            tuning_source_file="examples/tunings/duckdb/tpch_tuned.yaml",
            tuning_config_hash="a" * 64,
        )

        exported = build_result_payload(original)
        tuning_companion = build_tuning_payload(original)
        assert exported["platform"]["tuning"]["tuning_source"] == "auto_discovered"
        assert tuning_companion is not None

        reimported_summary_only = reconstruct_benchmark_results(exported)
        assert reimported_summary_only.tuning_source == "auto_discovered"

        reimported = reconstruct_benchmark_results(exported, tuning_data=tuning_companion)
        assert reimported.tuning_source == "auto_discovered"

        re_exported = build_result_payload(reimported)
        assert re_exported["platform"]["tuning"]["tuning_source"] == "auto_discovered"
        assert canonical_json_text(exported) == canonical_json_text(re_exported)

    def test_requested_constraints_survive_reconstruct_and_reexport_roundtrip(self):
        original = make_benchmark_results(
            benchmark_id="tpch",
            benchmark_name="tpch",
            platform="duckdb",
            scale_factor=0.01,
            execution_id="tuning-constraints-roundtrip-001",
            timestamp=datetime(2026, 7, 16, 12, 0, 0),
            duration_seconds=1.0,
            total_queries=1,
            successful_queries=1,
            failed_queries=0,
            query_results=[
                {
                    "query_id": "1",
                    "status": "SUCCESS",
                    "execution_time_ms": 10.0,
                    "rows_returned": 1,
                    "iteration": 1,
                    "stream_id": 0,
                    "run_type": "measurement",
                }
            ],
            tunings_applied={
                "primary_keys": {"enabled": True, "enforce_uniqueness": True, "nullable": False},
                "foreign_keys": {
                    "enabled": True,
                    "enforce_referential_integrity": True,
                    "on_delete_action": "RESTRICT",
                    "on_update_action": "RESTRICT",
                },
            },
            tuning_source="auto_discovered",
            tuning_source_file="examples/tunings/duckdb/tpch_tuned.yaml",
            tuning_config_hash="b" * 64,
        )

        exported = build_result_payload(original)
        tuning_companion = build_tuning_payload(original)
        assert tuning_companion is not None
        assert tuning_companion["requested"]["constraints"]["primary_keys"]["enabled"] is True
        assert exported["platform"]["tuning"]["counts"]["tuning_types"] == ["foreign_keys", "primary_keys"]

        reimported = reconstruct_benchmark_results(exported, tuning_data=tuning_companion)
        assert reimported.tunings_applied["primary_keys"]["enabled"] is True
        assert reimported.tunings_applied["foreign_keys"]["enabled"] is True

        re_exported = build_result_payload(reimported)
        assert re_exported["platform"]["tuning"]["counts"]["tuning_types"] == ["foreign_keys", "primary_keys"]
        assert canonical_json_text(exported) == canonical_json_text(re_exported)
        assert canonical_json_text(tuning_companion) == canonical_json_text(build_tuning_payload(reimported))


class TestResultSchemaVersionLoading:
    def test_load_result_file_with_result_schema_version_and_no_version(self, tmp_path: Path):
        data = make_v2_result_dict(version="2.2")
        del data["version"]
        data["result_schema_version"] = "2.2"
        file_path = tmp_path / "result.json"
        file_path.write_text(json.dumps(data), encoding="utf-8")

        result, _raw_data = load_result_file(file_path)
        assert result.benchmark_id == data["benchmark"]["id"]
        assert result.platform == data["platform"]["name"]

    def test_load_result_file_with_legacy_version(self, tmp_path: Path):
        data = make_v2_result_dict(version="2.0")
        assert "result_schema_version" not in data
        assert data["version"] == "2.0"
        file_path = tmp_path / "result.json"
        file_path.write_text(json.dumps(data), encoding="utf-8")

        result, _raw_data = load_result_file(file_path)
        assert result.benchmark_id == data["benchmark"]["id"]

    def test_load_result_file_missing_both_versions_raises(self, tmp_path: Path):
        data = make_v2_result_dict(version="2.2")
        del data["version"]
        assert "result_schema_version" not in data
        file_path = tmp_path / "result.json"
        file_path.write_text(json.dumps(data), encoding="utf-8")

        with pytest.raises(UnsupportedSchemaError):
            load_result_file(file_path)
