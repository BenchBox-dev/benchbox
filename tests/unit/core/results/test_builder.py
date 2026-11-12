from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from benchbox.core.results.builder import (
    BenchmarkInfoInput,
    ResultBuilder,
    RunConfigInput,
    build_benchmark_results,
    normalize_benchmark_id,
)
from benchbox.core.results.platform_info import PlatformInfoInput
from benchbox.core.results.query_normalizer import QueryResultInput, normalize_query_result

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_failed_warmup_validation_is_exported_without_corrupting_measurement_metrics():
    from benchbox.core.results.schema import build_result_payload
    from benchbox.core.results.status import result_cli_failure_reason
    from benchbox.validation.bundle import ValidationResult, _validate_execution_consistency

    builder = ResultBuilder(
        benchmark=BenchmarkInfoInput(name="TPC-H", scale_factor=1.0),
        platform=PlatformInfoInput(name="duckdb", platform_version="1.3.2"),
    )
    builder.add_query_results(
        [
            normalize_query_result(
                {
                    "query_id": "18",
                    "status": "FAILED",
                    "run_type": "warmup",
                    "iteration": 0,
                    "execution_time_seconds": 1.0,
                    "rows_returned": 9,
                    "row_count_validation": {"status": "FAILED", "expected": 57, "actual": 9},
                }
            ),
            normalize_query_result(
                {"query_id": "18", "status": "SUCCESS", "iteration": 1, "execution_time_seconds": 0.5}
            ),
        ]
    )
    result = builder.build()
    payload = build_result_payload(result)
    assert result.total_queries == result.successful_queries == 1
    assert result.failed_queries == 0
    assert result.total_execution_time == 0.5
    assert result.validation_status == "FAILED"
    assert payload["summary"]["validation"] == "failed"
    assert payload["queries"][0]["row_count_validation"]["status"] == "FAILED"
    assert result_cli_failure_reason(result) is not None
    payload["summary"]["validation"] = "passed"
    validation = ValidationResult("test")
    _validate_execution_consistency(payload, validation)
    assert not validation.ok
    assert any("row_count_validation.status='FAILED'" in error for error in validation.errors)


def test_unvalidated_measurement_is_exported_and_never_summarised_as_passed():
    from benchbox.core.results.schema import build_result_payload
    from benchbox.core.results.status import result_cli_failure_reason, result_unvalidated_reason
    from benchbox.validation.bundle import ValidationResult, _validate_execution_consistency

    builder = ResultBuilder(
        benchmark=BenchmarkInfoInput(name="TPC-H", scale_factor=10.0),
        platform=PlatformInfoInput(name="duckdb", platform_version="1.3.2"),
    )
    builder.add_query_results(
        [
            normalize_query_result(
                {
                    "query_id": "2",
                    "status": "SUCCESS",
                    "iteration": 1,
                    "execution_time_seconds": 0.5,
                    "rows_returned": 100,
                    "row_count_validation": {
                        "status": "UNVALIDATED",
                        "expected": None,
                        "actual": 100,
                        "warning": "no reference answers",
                    },
                }
            )
        ]
    )

    result = builder.build()
    payload = build_result_payload(result)

    assert result.validation_status == "NOT_VALIDATED"
    assert payload["summary"]["validation"] == "not_validated"
    assert payload["queries"][0]["row_count_validation"]["status"] == "UNVALIDATED"
    assert result_cli_failure_reason(result) is None
    assert result_unvalidated_reason(result) == "validation_status=not_validated"
    payload["summary"]["validation"] = "passed"
    validation = ValidationResult("test")
    _validate_execution_consistency(payload, validation)
    assert any("counts queries[0] as validated" in error for error in validation.errors)


class TestNormalizeBenchmarkId:
    @pytest.mark.parametrize(
        ("input_name", "expected_id"),
        [
            ("TPC-H", "tpch"),
            ("TPC-H Benchmark", "tpch"),
            ("tpch", "tpch"),
            ("tpc-h", "tpch"),
            ("tpc_h", "tpch"),
            ("TPCH", "tpch"),
            ("TPC-DS", "tpcds"),
            ("TPC-DS Benchmark", "tpcds"),
            ("tpcds", "tpcds"),
            ("tpc-ds", "tpcds"),
            ("tpc_ds", "tpcds"),
            ("TPCDS", "tpcds"),
            ("SSB", "ssb"),
            ("ssb", "ssb"),
            ("SSB Benchmark", "ssb"),
            ("ClickBench", "clickbench"),
            ("clickbench", "clickbench"),
            ("ClickBench Benchmark", "clickbench"),
            ("tpcds_obt", "tpcds_obt"),
            ("tpcds-obt", "tpcds_obt"),
            ("TPC-DS One Big Table Benchmark", "tpcds_obt"),
            ("tpch_skew", "tpch_skew"),
            ("tpch-skew", "tpch_skew"),
            ("Custom Benchmark", "custom"),
            ("My-Custom-Test", "my_custom_test"),
            ("Some Other Benchmark", "some_other"),
        ],
    )
    def test_normalize_benchmark_id(self, input_name: str, expected_id: str) -> None:

        assert normalize_benchmark_id(input_name) == expected_id

    def test_normalize_removes_double_underscores(self) -> None:

        assert normalize_benchmark_id("my__test") == "my_test"
        assert normalize_benchmark_id("my - - test") == "my_test"

    def test_derived_benchmarks_not_confused_with_parents(self) -> None:
        assert normalize_benchmark_id("tpcds_obt") != "tpcds"
        assert normalize_benchmark_id("tpcds_obt") == "tpcds_obt"
        assert normalize_benchmark_id("tpch_skew") != "tpch"
        assert normalize_benchmark_id("tpch_skew") == "tpch_skew"


class TestBenchmarkInfoInput:
    def test_create_basic(self) -> None:

        info = BenchmarkInfoInput(name="TPC-H", scale_factor=1.0)

        assert info.name == "TPC-H"
        assert info.scale_factor == 1.0
        assert info.test_type == "power"
        assert info.display_name is None

    def test_create_with_all_fields(self) -> None:

        info = BenchmarkInfoInput(
            name="TPC-H",
            scale_factor=10.0,
            test_type="throughput",
            display_name="TPC-H Benchmark",
        )

        assert info.name == "TPC-H"
        assert info.scale_factor == 10.0
        assert info.test_type == "throughput"
        assert info.display_name == "TPC-H Benchmark"


class TestResultBuilder:
    def create_builder(
        self,
        benchmark_name: str = "TPC-H",
        scale_factor: float = 1.0,
        platform_name: str = "DuckDB",
        execution_mode: str = "sql",
    ) -> ResultBuilder:
        return ResultBuilder(
            benchmark=BenchmarkInfoInput(name=benchmark_name, scale_factor=scale_factor),
            platform=PlatformInfoInput(name=platform_name, execution_mode=execution_mode),
        )

    def create_query_result(
        self,
        query_id: str = "1",
        execution_time: float = 1.0,
        rows: int = 100,
        status: str = "SUCCESS",
    ) -> QueryResultInput:
        return QueryResultInput(
            query_id=query_id,
            execution_time_seconds=execution_time,
            rows_returned=rows,
            status=status,
        )

    def test_build_empty_results(self) -> None:

        builder = self.create_builder()
        result = builder.build()

        assert result.benchmark_name == "TPC-H"
        assert result.platform == "DuckDB"
        assert result.scale_factor == 1.0
        assert result.total_queries == 0
        assert result.successful_queries == 0
        assert result.failed_queries == 0

    def test_build_with_query_results(self) -> None:

        builder = self.create_builder()
        builder.add_query_result(self.create_query_result("1", 1.0, 100))
        builder.add_query_result(self.create_query_result("2", 2.0, 200))
        builder.add_query_result(self.create_query_result("3", 3.0, 300))

        result = builder.build()

        assert result.total_queries == 3
        assert result.successful_queries == 3
        assert result.failed_queries == 0
        assert len(result.query_results) == 3

    def test_build_with_failed_queries(self) -> None:

        builder = self.create_builder()
        builder.add_query_result(self.create_query_result("1", 1.0, 100, "SUCCESS"))
        builder.add_query_result(self.create_query_result("2", 0.0, 0, "FAILED"))

        result = builder.build()

        assert result.total_queries == 2
        assert result.successful_queries == 1
        assert result.failed_queries == 1
        assert result.validation_status == "PARTIAL"

    def test_build_with_validation_failed_query_counts_as_failed(self) -> None:
        builder = self.create_builder()
        builder.add_query_result(self.create_query_result("1", 1.0, 100, "SUCCESS"))
        builder.add_query_result(self.create_query_result("2", 1.0, 100, "VALIDATION_FAILED"))

        result = builder.build()

        assert result.total_queries == 2
        assert result.successful_queries == 1
        assert result.failed_queries == 1
        assert result.validation_status == "PARTIAL"

    @pytest.mark.parametrize(
        ("status", "expected_failed", "expected_validation"),
        [
            ("ERROR", 1, "PARTIAL"),
            ("TIMEOUT", 1, "PARTIAL"),
            ("UNKNOWN", 1, "PARTIAL"),
            ("SKIPPED", 0, "PASSED"),
        ],
    )
    def test_build_counts_failure_statuses_without_counting_skipped(
        self, status: str, expected_failed: int, expected_validation: str
    ) -> None:
        builder = self.create_builder()
        builder.add_query_result(self.create_query_result("1", 1.0, 100, "SUCCESS"))
        builder.add_query_result(self.create_query_result("2", 0.0, 0, status))

        result = builder.build()

        assert result.successful_queries == 1
        assert result.failed_queries == expected_failed
        assert result.validation_status == expected_validation
        if expected_failed:
            assert result.power_at_size is None

    def test_build_calculates_tpc_metrics(self) -> None:

        builder = self.create_builder(scale_factor=1.0)

        for i in range(1, 5):
            builder.add_query_result(self.create_query_result(str(i), 1.0, 100))

        result = builder.build()

        assert result.power_at_size is not None
        assert result.power_at_size == 3600.0
        assert result.geometric_mean_execution_time is not None
        assert abs(result.geometric_mean_execution_time - 1.0) < 0.0001

    def test_build_with_table_stats(self) -> None:

        builder = self.create_builder()
        builder.add_table_stats("lineitem", 6001215, load_time_ms=1000)
        builder.add_table_stats("orders", 1500000, load_time_ms=500)
        builder.set_loading_time(1500.0)

        result = builder.build()

        assert result.total_rows_loaded == 6001215 + 1500000
        assert result.data_loading_time == 1.5
        assert "lineitem" in result.table_statistics
        assert result.table_statistics["lineitem"] == {"rows": 6001215, "load_time_ms": 1000}

    def test_build_with_execution_phases(self) -> None:

        builder = self.create_builder()
        builder.add_table_stats("lineitem", 1000, load_time_ms=100)
        builder.set_loading_time(100.0)
        builder.add_query_result(self.create_query_result("1", 1.0, 100))

        result = builder.build()

        assert result.execution_phases is not None
        assert result.execution_phases.setup is not None
        assert result.execution_phases.setup.data_loading is not None
        assert result.execution_phases.power_test is not None

    def test_build_dataframe_platform_display(self) -> None:

        builder = self.create_builder(
            platform_name="Polars",
            execution_mode="dataframe",
        )
        result = builder.build()

        assert result.platform == "Polars"

    def test_build_with_timestamps(self) -> None:

        builder = self.create_builder()
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 30)
        builder.set_start_time(start)
        builder.set_end_time(end)

        result = builder.build()

        assert result.timestamp == start
        assert result.duration_seconds == 30.0

    def test_build_with_mark_timestamps(self) -> None:

        builder = self.create_builder()
        builder.mark_started()
        builder.mark_completed()

        result = builder.build()

        assert result.timestamp is not None
        assert result.duration_seconds >= 0

    def test_build_with_validation_status(self) -> None:

        builder = self.create_builder()
        builder.set_validation_status("FAILED", {"error": "Test error", "phase": "load"})

        result = builder.build()

        assert result.validation_status == "FAILED"
        assert result.validation_details == {"error": "Test error", "phase": "load"}

    def test_build_with_execution_metadata(self) -> None:

        builder = self.create_builder()
        builder.set_execution_metadata({"custom_key": "value"})
        builder.add_execution_metadata("another_key", "another_value")

        result = builder.build()

        assert "custom_key" in result.execution_metadata
        assert result.execution_metadata["custom_key"] == "value"
        assert result.execution_metadata["another_key"] == "another_value"

    def test_build_with_system_profile(self) -> None:

        builder = self.create_builder()
        profile = {"cpu": "Apple M1", "memory": "16GB"}
        builder.set_system_profile(profile)

        result = builder.build()

        assert result.system_profile == profile

    def test_build_with_tuning_info(self) -> None:

        builder = self.create_builder()
        builder.set_tuning_info(
            tunings_applied={"memory": "8GB"},
            config_hash="abc123",
            source_file="tuning.yaml",
        )

        result = builder.build()

        assert result.tunings_applied == {"memory": "8GB"}
        assert result.tuning_config_hash == "abc123"
        assert result.tuning_source_file == "tuning.yaml"

    def test_build_threads_applied_ledger_fields(self) -> None:
        builder = self.create_builder()
        ledger_payload = {
            "status": "applied_unverified",
            "applied_ledger_hash": "deadbeef" * 8,
            "statements": [{"statement": "CREATE INDEX i ON t (a)", "phase": "ddl", "status": "executed"}],
            "dropped": [],
        }
        builder.set_tuning_info(
            tunings_applied={"memory": "8GB"},
            config_hash="requested-hash",
            validation_status="applied_unverified",
            metadata_saved=True,
            applied_tuning_ledger=ledger_payload,
            applied_ledger_hash=ledger_payload["applied_ledger_hash"],
        )

        result = builder.build()

        assert result.applied_tuning_ledger == ledger_payload
        assert result.applied_ledger_hash == ledger_payload["applied_ledger_hash"]
        assert result.tuning_validation_status == "applied_unverified"
        assert result.tuning_metadata_saved is True
        assert result.tuning_config_hash == "requested-hash"
        assert result.applied_ledger_hash != result.tuning_config_hash

    def test_build_defaults_applied_ledger_fields_to_none(self) -> None:
        builder = self.create_builder()

        result = builder.build()

        assert result.applied_tuning_ledger is None
        assert result.applied_ledger_hash is None
        assert result.tuning_validation_status == "not_validated"

    def test_build_with_cost_summary(self) -> None:

        builder = self.create_builder()
        builder.set_cost_summary({"total_cost": 1.50, "currency": "USD"})

        result = builder.build()

        assert result.cost_summary == {"total_cost": 1.50, "currency": "USD"}

    def test_build_with_plan_capture_stats(self) -> None:
        builder = self.create_builder()
        builder.add_plan_capture_stats(
            plans_captured=20,
            capture_failures=2,
            capture_errors=[{"query_id": "Q1", "error": "Timeout"}],
        )

        result = builder.build()

        assert result.query_plans_captured == 20
        assert result.plan_capture_failures == 2
        assert len(result.plan_capture_errors) == 1

    def test_add_query_results_batch(self) -> None:

        builder = self.create_builder()
        results = [
            self.create_query_result("1", 1.0, 100),
            self.create_query_result("2", 2.0, 200),
        ]
        builder.add_query_results(results)

        result = builder.build()

        assert result.total_queries == 2

    def test_query_results_format(self) -> None:

        builder = self.create_builder()
        builder.add_query_result(
            QueryResultInput(
                query_id="1",
                execution_time_seconds=1.5,
                rows_returned=100,
                status="SUCCESS",
                iteration=2,
                stream_id=1,
            )
        )

        result = builder.build()
        qr = result.query_results[0]

        assert qr["query_id"] == "Q1"
        assert qr["execution_time"] == 1.5
        assert qr["execution_time_ms"] == 1500
        assert qr["status"] == "SUCCESS"
        assert qr["rows_returned"] == 100
        assert qr["iteration"] == 2
        assert qr["stream_id"] == 1

    def test_query_results_format_includes_plan_capture_error(self) -> None:
        builder = self.create_builder()
        builder.add_query_result(
            QueryResultInput(
                query_id="1",
                execution_time_seconds=1.5,
                rows_returned=100,
                status="SUCCESS",
                plan_capture_error="TypeError: unsupported operand",
            )
        )

        result = builder.build()
        qr = result.query_results[0]

        assert qr["plan_capture_error"] == "TypeError: unsupported operand"

    def test_query_results_format_omits_plan_capture_error_when_none(self) -> None:
        builder = self.create_builder()
        builder.add_query_result(self.create_query_result("1", 1.0, 100))

        result = builder.build()
        qr = result.query_results[0]

        assert "plan_capture_error" not in qr

    def test_platform_info_dict_format(self) -> None:

        builder = ResultBuilder(
            benchmark=BenchmarkInfoInput(name="TPC-H", scale_factor=1.0),
            platform=PlatformInfoInput(
                name="DuckDB",
                platform_version="1.0.0",
                client_library_version="2.0.0",
                execution_mode="sql",
                connection_mode="in-memory",
                config={"threads": 4},
            ),
        )

        result = builder.build()

        assert result.platform_info["platform_name"] == "DuckDB"
        assert result.platform_info["platform_version"] == "1.0.0"
        assert result.platform_info["client_library_version"] == "2.0.0"
        assert result.platform_info["execution_mode"] == "sql"
        assert result.platform_info["connection_mode"] == "in-memory"
        assert result.platform_info["configuration"] == {"threads": 4}

    def test_build_preserves_test_type_through_real_pipeline(self) -> None:
        power_raw = {
            "query_id": "Q6",
            "execution_time_seconds": 1.0,
            "rows_returned": 10,
            "status": "SUCCESS",
            "stream_id": 0,
            "test_type": "power",
        }
        throughput_raw = {
            "query_id": "Q6",
            "execution_time_seconds": 2.0,
            "rows_returned": 10,
            "status": "SUCCESS",
            "stream_id": 0,
            "test_type": "throughput",
        }

        builder = self.create_builder()
        builder.add_query_result(normalize_query_result(power_raw))
        builder.add_query_result(normalize_query_result(throughput_raw))

        result = builder.build()

        assert len(result.query_results) == 2
        power_row, throughput_row = result.query_results
        assert power_row["test_type"] == "power"
        assert throughput_row["test_type"] == "throughput"


class TestBuildBenchmarkResults:
    def test_basic_usage(self) -> None:

        query_results = [
            QueryResultInput(
                query_id="1",
                execution_time_seconds=1.0,
                rows_returned=100,
                status="SUCCESS",
            ),
        ]

        result = build_benchmark_results(
            benchmark_name="TPC-H",
            platform_name="DuckDB",
            scale_factor=1.0,
            query_results=query_results,
        )

        assert result.benchmark_name == "TPC-H"
        assert result.platform == "DuckDB"
        assert result.scale_factor == 1.0
        assert result.total_queries == 1

    def test_with_all_options(self) -> None:

        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 30)
        query_results = [
            QueryResultInput(
                query_id="1",
                execution_time_seconds=1.0,
                rows_returned=100,
                status="SUCCESS",
            ),
        ]

        result = build_benchmark_results(
            benchmark_name="TPC-H",
            platform_name="DuckDB",
            scale_factor=1.0,
            query_results=query_results,
            execution_mode="sql",
            test_type="power",
            platform_version="1.0.0",
            table_stats={"lineitem": 1000},
            loading_time_ms=500.0,
            start_time=start,
            end_time=end,
        )

        assert result.platform_info["platform_version"] == "1.0.0"
        assert result.total_rows_loaded == 1000
        assert result.data_loading_time == 0.5
        assert result.timestamp == start
        assert result.duration_seconds == 30.0

    def test_dataframe_mode(self) -> None:

        query_results = [
            QueryResultInput(
                query_id="1",
                execution_time_seconds=1.0,
                rows_returned=100,
                status="SUCCESS",
            ),
        ]

        result = build_benchmark_results(
            benchmark_name="TPC-H",
            platform_name="Polars",
            scale_factor=1.0,
            query_results=query_results,
            execution_mode="dataframe",
        )

        assert result.platform == "Polars"
        assert result.execution_metadata["execution_mode"] == "dataframe"


class TestRunConfigInputTableMode:
    def test_external_mode_included_in_dict(self):
        rc = RunConfigInput(table_mode="external", phases=["power"])
        d = rc.to_dict()
        assert d["table_mode"] == "external"

    def test_native_mode_omitted_from_dict(self):
        rc = RunConfigInput(table_mode="native", phases=["power"])
        d = rc.to_dict()
        assert "table_mode" not in d

    def test_none_mode_omitted_from_dict(self):
        rc = RunConfigInput(table_mode=None, phases=["power"])
        d = rc.to_dict()
        assert "table_mode" not in d

    def test_round_trip_through_builder(self):
        builder = ResultBuilder(
            benchmark=BenchmarkInfoInput(name="tpch", scale_factor=0.01),
            platform=PlatformInfoInput(name="DuckDB", execution_mode="sql"),
        )
        builder.set_run_config(RunConfigInput(table_mode="external", phases=["power"]))
        builder.mark_started()
        builder.mark_completed()
        result = builder.build()
        config = result.execution_metadata.get("run_config", {})
        assert config.get("table_mode") == "external"


class TestRunConfigInputExternalFormat:
    def test_external_format_included_in_dict(self):
        rc = RunConfigInput(table_mode="external", external_format="parquet", phases=["power"])
        d = rc.to_dict()
        assert d["external_format"] == "parquet"

    def test_external_format_tbl_included(self):
        rc = RunConfigInput(table_mode="external", external_format="tbl", phases=["power"])
        d = rc.to_dict()
        assert d["external_format"] == "tbl"

    def test_none_format_omitted_from_dict(self):
        rc = RunConfigInput(table_mode="external", external_format=None, phases=["power"])
        d = rc.to_dict()
        assert "external_format" not in d

    def test_empty_format_omitted_from_dict(self):
        rc = RunConfigInput(table_mode="external", external_format="", phases=["power"])
        d = rc.to_dict()
        assert "external_format" not in d

    def test_round_trip_through_builder(self):
        builder = ResultBuilder(
            benchmark=BenchmarkInfoInput(name="tpch", scale_factor=0.01),
            platform=PlatformInfoInput(name="DuckDB", execution_mode="sql"),
        )
        builder.set_run_config(RunConfigInput(table_mode="external", external_format="parquet", phases=["power"]))
        builder.mark_started()
        builder.mark_completed()
        result = builder.build()
        config = result.execution_metadata.get("run_config", {})
        assert config.get("table_mode") == "external"
        assert config.get("external_format") == "parquet"

    def test_round_trip_tbl_format(self):
        builder = ResultBuilder(
            benchmark=BenchmarkInfoInput(name="tpch", scale_factor=1.0),
            platform=PlatformInfoInput(name="DuckDB", execution_mode="sql"),
        )
        builder.set_run_config(RunConfigInput(table_mode="external", external_format="tbl", phases=["power"]))
        builder.mark_started()
        builder.mark_completed()
        result = builder.build()
        config = result.execution_metadata.get("run_config", {})
        assert config.get("external_format") == "tbl"


class TestRunConfigInputTableFormat:
    def test_table_format_fields_included_in_dict(self):
        rc = RunConfigInput(
            table_format="parquet",
            table_format_compression="zstd",
            table_format_partition_cols=["region"],
            phases=["power"],
        )
        d = rc.to_dict()
        assert d["table_format"] == "parquet"
        assert d["table_format_compression"] == "zstd"
        assert d["table_format_partition_cols"] == ["region"]

    def test_table_format_fields_omitted_without_table_format(self):
        rc = RunConfigInput(
            table_format=None,
            table_format_compression="zstd",
            table_format_partition_cols=["region"],
            phases=["power"],
        )
        d = rc.to_dict()
        assert "table_format" not in d
        assert "table_format_compression" not in d
        assert "table_format_partition_cols" not in d

    def test_table_format_round_trip_through_builder(self):
        builder = ResultBuilder(
            benchmark=BenchmarkInfoInput(name="tpch", scale_factor=0.01),
            platform=PlatformInfoInput(name="DuckDB", execution_mode="sql"),
        )
        builder.set_run_config(
            RunConfigInput(
                table_format="iceberg",
                table_format_compression="zstd",
                table_format_partition_cols=["region"],
                phases=["power"],
            )
        )
        builder.mark_started()
        builder.mark_completed()
        result = builder.build()
        config = result.execution_metadata.get("run_config", {})
        assert config.get("table_format") == "iceberg"
        assert config.get("table_format_compression") == "zstd"
        assert config.get("table_format_partition_cols") == ["region"]


class TestResultFactoryExternalFormat:
    def _make_benchmark(self, name="TPC-H Benchmark", scale=0.01):
        from unittest.mock import Mock

        bm = Mock()
        bm.benchmark_name = name
        bm.scale_factor = scale
        return bm

    def test_external_format_reaches_result_config(self):
        from benchbox.core.results.result_factory import build_enhanced_benchmark_result

        result = build_enhanced_benchmark_result(
            benchmark=self._make_benchmark(),
            platform="duckdb",
            query_results=[],
            execution_metadata={
                "run_config": {
                    "table_mode": "external",
                    "external_format": "parquet",
                    "phases": ["power"],
                },
            },
        )
        config = result.execution_metadata.get("run_config", {})
        assert config.get("table_mode") == "external"
        assert config.get("external_format") == "parquet"

    def test_tbl_format_reaches_result_config(self):
        from benchbox.core.results.result_factory import build_enhanced_benchmark_result

        result = build_enhanced_benchmark_result(
            benchmark=self._make_benchmark(),
            platform="duckdb",
            query_results=[],
            execution_metadata={
                "run_config": {
                    "table_mode": "external",
                    "external_format": "tbl",
                },
            },
        )
        config = result.execution_metadata.get("run_config", {})
        assert config.get("external_format") == "tbl"

    def test_missing_external_format_omitted(self):
        from benchbox.core.results.result_factory import build_enhanced_benchmark_result

        result = build_enhanced_benchmark_result(
            benchmark=self._make_benchmark(),
            platform="duckdb",
            query_results=[],
            execution_metadata={
                "run_config": {
                    "table_mode": "external",
                },
            },
        )
        config = result.execution_metadata.get("run_config", {})
        assert config.get("table_mode") == "external"
        assert "external_format" not in config

    def test_table_format_reaches_result_config(self):
        from benchbox.core.results.result_factory import build_enhanced_benchmark_result

        result = build_enhanced_benchmark_result(
            benchmark=self._make_benchmark(),
            platform="duckdb",
            query_results=[],
            execution_metadata={
                "run_config": {
                    "table_format": "parquet",
                    "table_format_compression": "zstd",
                    "table_format_partition_cols": ["region"],
                },
            },
        )
        config = result.execution_metadata.get("run_config", {})
        assert config.get("table_format") == "parquet"
        assert config.get("table_format_compression") == "zstd"
        assert config.get("table_format_partition_cols") == ["region"]
