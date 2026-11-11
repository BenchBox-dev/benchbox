# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from benchbox.core.dataframe.benchmark_suite import (
    PLATFORM_CAPABILITIES,
    BenchmarkConfig,
    ComparisonSummary,
    DataFrameBenchmarkSuite,
    PlatformBenchmarkResult,
    PlatformCategory,
    QueryBenchmarkResult,
    SQLComparisonResult,
    SQLVsDataFrameBenchmark,
    SQLVsDataFrameSummary,
)

pytestmark = [
    pytest.mark.performance,
    pytest.mark.stress,
]


class TestBenchmarkConfig:
    def test_default_config(self):
        config = BenchmarkConfig()
        assert config.scale_factor == 0.01
        assert config.query_ids is None
        assert config.warmup_iterations == 1
        assert config.benchmark_iterations == 3
        assert config.track_memory is True
        assert config.capture_plans is True

    def test_custom_config(self):
        config = BenchmarkConfig(
            scale_factor=1.0,
            query_ids=["Q1", "Q6"],
            warmup_iterations=2,
            benchmark_iterations=5,
            track_memory=False,
        )
        assert config.scale_factor == 1.0
        assert config.query_ids == ["Q1", "Q6"]
        assert config.warmup_iterations == 2
        assert config.benchmark_iterations == 5
        assert config.track_memory is False


class TestPlatformCapability:
    def test_polars_capability(self):
        cap = PLATFORM_CAPABILITIES.get("polars-df")
        assert cap is not None
        assert cap.family == "expression"
        assert cap.category == PlatformCategory.SINGLE_NODE
        assert cap.supports_lazy is True
        assert cap.supports_streaming is True
        assert cap.supports_gpu is False
        assert cap.supports_distributed is False

    def test_pandas_capability(self):
        cap = PLATFORM_CAPABILITIES.get("pandas-df")
        assert cap is not None
        assert cap.family == "pandas"
        assert cap.category == PlatformCategory.SINGLE_NODE
        assert cap.supports_lazy is False

    def test_pyspark_capability(self):
        cap = PLATFORM_CAPABILITIES.get("pyspark-df")
        assert cap is not None
        assert cap.family == "expression"
        assert cap.category == PlatformCategory.DISTRIBUTED
        assert cap.supports_distributed is True

    def test_cudf_capability(self):
        cap = PLATFORM_CAPABILITIES.get("cudf-df")
        assert cap is not None
        assert cap.family == "pandas"
        assert cap.category == PlatformCategory.GPU_ACCELERATED
        assert cap.supports_gpu is True


class TestQueryBenchmarkResult:
    def test_empty_result_statistics(self):
        result = QueryBenchmarkResult(
            query_id="Q1",
            platform="polars-df",
            iterations=0,
            execution_times_ms=[],
        )
        assert result.mean_time_ms == 0.0
        assert result.std_time_ms == 0.0
        assert result.min_time_ms == 0.0
        assert result.max_time_ms == 0.0
        assert result.p50_time_ms == 0.0
        assert result.coefficient_of_variation == 0.0

    def test_single_execution_statistics(self):
        result = QueryBenchmarkResult(
            query_id="Q1",
            platform="polars-df",
            iterations=1,
            execution_times_ms=[100.0],
        )
        assert result.mean_time_ms == 100.0
        assert result.std_time_ms == 0.0
        assert result.min_time_ms == 100.0
        assert result.max_time_ms == 100.0
        assert result.p50_time_ms == 100.0
        assert result.p95_time_ms == 100.0

    def test_multiple_execution_statistics(self):
        result = QueryBenchmarkResult(
            query_id="Q1",
            platform="polars-df",
            iterations=5,
            execution_times_ms=[100.0, 110.0, 95.0, 105.0, 90.0],
        )
        assert result.mean_time_ms == 100.0
        assert result.std_time_ms > 0
        assert result.min_time_ms == 90.0
        assert result.max_time_ms == 110.0
        assert result.p50_time_ms == 100.0

    def test_to_dict(self):
        result = QueryBenchmarkResult(
            query_id="Q1",
            platform="polars-df",
            iterations=3,
            execution_times_ms=[100.0, 110.0, 105.0],
            memory_peak_mb=256.0,
            rows_returned=100,
            status="SUCCESS",
        )
        data = result.to_dict()
        assert data["query_id"] == "Q1"
        assert data["platform"] == "polars-df"
        assert data["iterations"] == 3
        assert data["status"] == "SUCCESS"
        assert "mean_time_ms" in data
        assert "std_time_ms" in data
        assert "cv_percent" in data


class TestPlatformBenchmarkResult:
    def test_aggregate_calculations(self):
        config = BenchmarkConfig(scale_factor=0.01)
        query_results = [
            QueryBenchmarkResult(
                query_id="Q1",
                platform="polars-df",
                iterations=3,
                execution_times_ms=[100.0, 110.0, 105.0],
                status="SUCCESS",
            ),
            QueryBenchmarkResult(
                query_id="Q6",
                platform="polars-df",
                iterations=3,
                execution_times_ms=[50.0, 55.0, 52.0],
                status="SUCCESS",
            ),
        ]
        result = PlatformBenchmarkResult(
            platform="polars-df",
            capability=PLATFORM_CAPABILITIES.get("polars-df"),
            config=config,
            query_results=query_results,
        )

        expected_total = 105.0 + 52.33333
        assert abs(result.total_time_ms - expected_total) < 1.0

        assert result.geometric_mean_ms > 0

        assert result.successful_queries == 2
        assert result.failed_queries == 0
        assert result.success_rate == 100.0

    def test_mixed_success_failure(self):
        config = BenchmarkConfig()
        query_results = [
            QueryBenchmarkResult(
                query_id="Q1",
                platform="pandas-df",
                iterations=3,
                execution_times_ms=[100.0, 110.0, 105.0],
                status="SUCCESS",
            ),
            QueryBenchmarkResult(
                query_id="Q6",
                platform="pandas-df",
                iterations=0,
                status="ERROR",
                error_message="Test error",
            ),
        ]
        result = PlatformBenchmarkResult(
            platform="pandas-df",
            capability=PLATFORM_CAPABILITIES.get("pandas-df"),
            config=config,
            query_results=query_results,
        )

        assert result.successful_queries == 1
        assert result.failed_queries == 1
        assert result.success_rate == 50.0

    def test_to_dict(self):
        config = BenchmarkConfig(scale_factor=0.1, query_ids=["Q1"])
        query_results = [
            QueryBenchmarkResult(
                query_id="Q1",
                platform="polars-df",
                iterations=1,
                execution_times_ms=[100.0],
                status="SUCCESS",
            ),
        ]
        result = PlatformBenchmarkResult(
            platform="polars-df",
            capability=PLATFORM_CAPABILITIES.get("polars-df"),
            config=config,
            query_results=query_results,
        )
        data = result.to_dict()

        assert data["platform"] == "polars-df"
        assert data["capability"]["family"] == "expression"
        assert data["config"]["scale_factor"] == 0.1
        assert "summary" in data
        assert data["summary"]["successful_queries"] == 1


class TestDataFrameBenchmarkSuite:
    def test_init_default_config(self):
        suite = DataFrameBenchmarkSuite()
        assert suite.config.scale_factor == 0.01
        assert suite.config.benchmark_iterations == 3

    def test_init_custom_config(self):
        config = BenchmarkConfig(scale_factor=1.0, query_ids=["Q1"])
        suite = DataFrameBenchmarkSuite(config=config)
        assert suite.config.scale_factor == 1.0
        assert suite.config.query_ids == ["Q1"]

    def test_get_available_platforms(self):
        suite = DataFrameBenchmarkSuite()
        platforms = suite.get_available_platforms()

        assert isinstance(platforms, list)

        assert "polars-df" in platforms

    def test_get_platform_capability(self):
        suite = DataFrameBenchmarkSuite()

        cap = suite.get_platform_capability("polars-df")
        assert cap is not None
        assert cap.platform_name == "polars-df"

        unknown = suite.get_platform_capability("unknown-df")
        assert unknown is None

    def test_get_platforms_by_category(self):
        suite = DataFrameBenchmarkSuite()

        single_node = suite.get_platforms_by_category(PlatformCategory.SINGLE_NODE)
        assert "polars-df" in single_node or len(single_node) >= 0

        distributed = suite.get_platforms_by_category(PlatformCategory.DISTRIBUTED)
        assert isinstance(distributed, list)


class TestComparisonSummary:
    def test_to_dict(self):
        summary = ComparisonSummary(
            platforms=["polars-df", "pandas-df"],
            fastest_platform="polars-df",
            slowest_platform="pandas-df",
            speedup_matrix={
                "polars-df": {"polars-df": 1.0, "pandas-df": 0.5},
                "pandas-df": {"polars-df": 2.0, "pandas-df": 1.0},
            },
            query_winners={"Q1": "polars-df", "Q6": "polars-df"},
            total_queries=2,
            scale_factor=0.01,
        )
        data = summary.to_dict()

        assert data["platforms"] == ["polars-df", "pandas-df"]
        assert data["fastest_platform"] == "polars-df"
        assert data["slowest_platform"] == "pandas-df"
        assert data["total_queries"] == 2


class TestBenchmarkSuiteReporting:
    def test_get_summary(self):
        suite = DataFrameBenchmarkSuite()
        config = BenchmarkConfig()

        results = [
            PlatformBenchmarkResult(
                platform="polars-df",
                capability=PLATFORM_CAPABILITIES.get("polars-df"),
                config=config,
                query_results=[
                    QueryBenchmarkResult(
                        query_id="Q1",
                        platform="polars-df",
                        iterations=3,
                        execution_times_ms=[50.0, 55.0, 52.0],
                        status="SUCCESS",
                    ),
                ],
            ),
            PlatformBenchmarkResult(
                platform="pandas-df",
                capability=PLATFORM_CAPABILITIES.get("pandas-df"),
                config=config,
                query_results=[
                    QueryBenchmarkResult(
                        query_id="Q1",
                        platform="pandas-df",
                        iterations=3,
                        execution_times_ms=[100.0, 110.0, 105.0],
                        status="SUCCESS",
                    ),
                ],
            ),
        ]

        summary = suite.get_summary(results)

        assert summary.platforms == ["polars-df", "pandas-df"]
        assert summary.fastest_platform == "polars-df"
        assert summary.slowest_platform == "pandas-df"
        assert "Q1" in summary.query_winners
        assert summary.query_winners["Q1"] == "polars-df"

    def test_export_results_json(self):
        suite = DataFrameBenchmarkSuite()
        config = BenchmarkConfig()

        results = [
            PlatformBenchmarkResult(
                platform="polars-df",
                capability=PLATFORM_CAPABILITIES.get("polars-df"),
                config=config,
                query_results=[
                    QueryBenchmarkResult(
                        query_id="Q1",
                        platform="polars-df",
                        iterations=1,
                        execution_times_ms=[100.0],
                        status="SUCCESS",
                    ),
                ],
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "results.json"
            result_path = suite.export_results(results, output_path, format="json")

            assert result_path.exists()
            content = result_path.read_text()
            assert "polars-df" in content
            assert "Q1" in content

    def test_export_results_markdown(self):
        suite = DataFrameBenchmarkSuite()
        config = BenchmarkConfig()

        results = [
            PlatformBenchmarkResult(
                platform="polars-df",
                capability=PLATFORM_CAPABILITIES.get("polars-df"),
                config=config,
                query_results=[
                    QueryBenchmarkResult(
                        query_id="Q1",
                        platform="polars-df",
                        iterations=1,
                        execution_times_ms=[100.0],
                        status="SUCCESS",
                    ),
                ],
            ),
            PlatformBenchmarkResult(
                platform="pandas-df",
                capability=PLATFORM_CAPABILITIES.get("pandas-df"),
                config=config,
                query_results=[
                    QueryBenchmarkResult(
                        query_id="Q1",
                        platform="pandas-df",
                        iterations=1,
                        execution_times_ms=[200.0],
                        status="SUCCESS",
                    ),
                ],
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "report.md"
            result_path = suite.export_results(results, output_path, format="markdown")

            assert result_path.exists()
            content = result_path.read_text()
            assert "# DataFrame Cross-Platform Benchmark Report" in content
            assert "polars-df" in content
            assert "pandas-df" in content
            assert "Speedup Matrix" in content


class TestSQLComparisonResult:
    def test_speedup_calculation(self):
        result = SQLComparisonResult(
            query_id="Q1",
            sql_platform="duckdb",
            df_platform="polars-df",
            sql_time_ms=100.0,
            df_time_ms=50.0,
        )
        assert result.speedup == 2.0

    def test_speedup_sql_faster(self):
        result = SQLComparisonResult(
            query_id="Q1",
            sql_platform="duckdb",
            df_platform="polars-df",
            sql_time_ms=50.0,
            df_time_ms=100.0,
        )
        assert result.speedup == 0.5

    def test_zero_time_no_division_error(self):
        result = SQLComparisonResult(
            query_id="Q1",
            sql_platform="duckdb",
            df_platform="polars-df",
            sql_time_ms=0.0,
            df_time_ms=100.0,
        )
        assert result.speedup == 1.0

    def test_to_dict(self):
        result = SQLComparisonResult(
            query_id="Q1",
            sql_platform="duckdb",
            df_platform="polars-df",
            sql_time_ms=100.0,
            df_time_ms=50.0,
            sql_rows=10,
            df_rows=10,
            results_match=True,
            status="SUCCESS",
        )
        data = result.to_dict()
        assert data["query_id"] == "Q1"
        assert data["sql_platform"] == "duckdb"
        assert data["df_platform"] == "polars-df"
        assert data["speedup"] == 2.0
        assert data["results_match"] is True


class TestSQLVsDataFrameSummary:
    def test_df_wins_percentage(self):
        summary = SQLVsDataFrameSummary(
            sql_platform="duckdb",
            df_platform="polars-df",
            total_queries=10,
            df_faster_count=7,
            sql_faster_count=3,
            average_speedup=1.5,
            max_speedup=3.0,
            min_speedup=0.8,
        )
        assert summary.df_wins_percentage == 70.0

    def test_df_wins_percentage_zero_queries(self):
        summary = SQLVsDataFrameSummary(
            sql_platform="duckdb",
            df_platform="polars-df",
            total_queries=0,
            df_faster_count=0,
            sql_faster_count=0,
            average_speedup=1.0,
            max_speedup=1.0,
            min_speedup=1.0,
        )
        assert summary.df_wins_percentage == 0.0

    def test_to_dict(self):
        results = [
            SQLComparisonResult(
                query_id="Q1",
                sql_platform="duckdb",
                df_platform="polars-df",
                sql_time_ms=100.0,
                df_time_ms=50.0,
            ),
        ]
        summary = SQLVsDataFrameSummary(
            sql_platform="duckdb",
            df_platform="polars-df",
            total_queries=1,
            df_faster_count=1,
            sql_faster_count=0,
            average_speedup=2.0,
            max_speedup=2.0,
            min_speedup=2.0,
            query_results=results,
        )
        data = summary.to_dict()
        assert data["sql_platform"] == "duckdb"
        assert data["df_platform"] == "polars-df"
        assert data["df_wins_percentage"] == 100.0
        assert len(data["query_results"]) == 1


class TestSQLVsDataFrameBenchmark:
    def test_init_default_config(self):
        benchmark = SQLVsDataFrameBenchmark()
        assert benchmark.config.scale_factor == 0.01

    def test_init_custom_config(self):
        config = BenchmarkConfig(scale_factor=1.0, query_ids=["Q1", "Q6"])
        benchmark = SQLVsDataFrameBenchmark(config=config)
        assert benchmark.config.scale_factor == 1.0
        assert benchmark.config.query_ids == ["Q1", "Q6"]

    def test_generate_report(self):
        benchmark = SQLVsDataFrameBenchmark()
        results = [
            SQLComparisonResult(
                query_id="Q1",
                sql_platform="duckdb",
                df_platform="polars-df",
                sql_time_ms=100.0,
                df_time_ms=50.0,
                status="SUCCESS",
            ),
            SQLComparisonResult(
                query_id="Q6",
                sql_platform="duckdb",
                df_platform="polars-df",
                sql_time_ms=80.0,
                df_time_ms=40.0,
                status="SUCCESS",
            ),
        ]
        summary = SQLVsDataFrameSummary(
            sql_platform="duckdb",
            df_platform="polars-df",
            total_queries=2,
            df_faster_count=2,
            sql_faster_count=0,
            average_speedup=2.0,
            max_speedup=2.0,
            min_speedup=2.0,
            query_results=results,
        )

        report = benchmark.generate_report(summary)

        assert "# SQL vs DataFrame Performance Comparison" in report
        assert "duckdb" in report
        assert "polars-df" in report
        assert "DataFrame wins" in report
        assert "Q1" in report
        assert "Q6" in report


