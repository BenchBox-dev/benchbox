# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.dataframe.benchmark_suite import (
    PLATFORM_CAPABILITIES,
    BenchmarkConfig,
    ComparisonSummary,
    DataFrameBenchmarkSuite,
    PlatformBenchmarkResult,
    PlatformCapability,
    PlatformCategory,
    QueryBenchmarkResult,
    SQLComparisonResult,
    SQLVsDataFrameBenchmark,
    SQLVsDataFrameSummary,
    resolve_table_paths,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _real_time_with(**overrides):
    return SimpleNamespace(**{**vars(time), **overrides})


SUITE_TIME = "benchbox.core.dataframe.benchmark_suite.time"


def _make_query_result(
    query_id: str = "Q1",
    platform: str = "polars-df",
    times: list[float] | None = None,
    status: str = "SUCCESS",
    memory_peak_mb: float = 12.5,
    rows_returned: int = 4,
    error_message: str | None = None,
) -> QueryBenchmarkResult:
    return QueryBenchmarkResult(
        query_id=query_id,
        platform=platform,
        iterations=len(times) if times else 0,
        execution_times_ms=times or [],
        memory_peak_mb=memory_peak_mb,
        rows_returned=rows_returned,
        status=status,
        error_message=error_message,
    )


def _make_platform_result(
    platform: str = "polars-df",
    query_results: list[QueryBenchmarkResult] | None = None,
    config: BenchmarkConfig | None = None,
) -> PlatformBenchmarkResult:
    cfg = config or BenchmarkConfig(scale_factor=0.01)
    cap = PLATFORM_CAPABILITIES.get(platform)
    return PlatformBenchmarkResult(
        platform=platform,
        capability=cap,
        config=cfg,
        query_results=query_results or [],
    )


class TestPlatformCategory:
    def test_all_expected_categories_exist(self):
        expected = {"SINGLE_NODE", "GPU_ACCELERATED", "DISTRIBUTED", "MEMORY_EFFICIENT"}
        actual = {member.name for member in PlatformCategory}
        assert actual == expected

    def test_category_values_are_distinct(self):
        values = [member.value for member in PlatformCategory]
        assert len(values) == len(set(values))

    def test_category_string_values(self):
        assert PlatformCategory.SINGLE_NODE.value == "single_node"
        assert PlatformCategory.GPU_ACCELERATED.value == "gpu_accelerated"
        assert PlatformCategory.DISTRIBUTED.value == "distributed"
        assert PlatformCategory.MEMORY_EFFICIENT.value == "memory_efficient"


class TestPlatformCapability:
    def test_minimal_construction(self):
        cap = PlatformCapability(
            platform_name="test-df",
            family="expression",
            category=PlatformCategory.SINGLE_NODE,
        )
        assert cap.platform_name == "test-df"
        assert cap.family == "expression"
        assert cap.category == PlatformCategory.SINGLE_NODE
        assert cap.supports_lazy is False
        assert cap.supports_streaming is False
        assert cap.supports_gpu is False
        assert cap.supports_distributed is False
        assert cap.memory_notes == ""

    def test_full_construction(self):
        cap = PlatformCapability(
            platform_name="gpu-df",
            family="pandas",
            category=PlatformCategory.GPU_ACCELERATED,
            supports_lazy=True,
            supports_streaming=True,
            supports_gpu=True,
            supports_distributed=True,
            memory_notes="GPU VRAM bound",
        )
        assert cap.supports_lazy is True
        assert cap.supports_streaming is True
        assert cap.supports_gpu is True
        assert cap.supports_distributed is True
        assert cap.memory_notes == "GPU VRAM bound"


class TestPlatformCapabilitiesRegistry:
    def test_registry_is_non_empty(self):
        assert len(PLATFORM_CAPABILITIES) >= 5

    def test_known_platforms_present(self):
        for name in ("polars-df", "pandas-df", "datafusion-df", "pyspark-df"):
            assert name in PLATFORM_CAPABILITIES, f"{name} missing from registry"

    def test_polars_capabilities(self):
        cap = PLATFORM_CAPABILITIES["polars-df"]
        assert cap.platform_name == "polars-df"
        assert cap.family == "expression"
        assert cap.category == PlatformCategory.SINGLE_NODE
        assert cap.supports_lazy is True
        assert cap.supports_streaming is True
        assert cap.supports_gpu is False

    def test_pandas_capabilities(self):
        cap = PLATFORM_CAPABILITIES["pandas-df"]
        assert cap.platform_name == "pandas-df"
        assert cap.family == "pandas"
        assert cap.category == PlatformCategory.SINGLE_NODE
        assert cap.supports_lazy is False
        assert cap.supports_streaming is False

    def test_pyspark_is_distributed(self):
        cap = PLATFORM_CAPABILITIES["pyspark-df"]
        assert cap.category == PlatformCategory.DISTRIBUTED
        assert cap.supports_distributed is True

    def test_cudf_is_gpu_accelerated(self):
        cap = PLATFORM_CAPABILITIES["cudf-df"]
        assert cap.category == PlatformCategory.GPU_ACCELERATED
        assert cap.supports_gpu is True

    def test_every_entry_has_required_fields(self):
        for name, cap in PLATFORM_CAPABILITIES.items():
            assert cap.platform_name == name, f"Key/name mismatch for {name}"
            assert cap.family in ("expression", "pandas"), f"Unexpected family '{cap.family}' for {name}"
            assert isinstance(cap.category, PlatformCategory)
            assert isinstance(cap.memory_notes, str)


class TestBenchmarkConfig:
    def test_default_values(self):
        cfg = BenchmarkConfig()
        assert cfg.scale_factor == 0.01
        assert cfg.query_ids is None
        assert cfg.warmup_iterations == 1
        assert cfg.benchmark_iterations == 3
        assert cfg.track_memory is True
        assert cfg.capture_plans is True
        assert cfg.memory_sample_interval_ms == 50
        assert cfg.timeout_seconds == 300.0

    def test_custom_config(self):
        cfg = BenchmarkConfig(
            scale_factor=1.0,
            query_ids=["Q1", "Q6"],
            warmup_iterations=2,
            benchmark_iterations=5,
            track_memory=False,
            capture_plans=False,
            memory_sample_interval_ms=100,
            timeout_seconds=600.0,
        )
        assert cfg.scale_factor == 1.0
        assert cfg.query_ids == ["Q1", "Q6"]
        assert cfg.warmup_iterations == 2
        assert cfg.benchmark_iterations == 5
        assert cfg.track_memory is False
        assert cfg.capture_plans is False
        assert cfg.memory_sample_interval_ms == 100
        assert cfg.timeout_seconds == 600.0

    def test_zero_scale_factor_allowed(self):
        cfg = BenchmarkConfig(scale_factor=0.0)
        assert cfg.scale_factor == 0.0

    def test_large_scale_factor(self):
        cfg = BenchmarkConfig(scale_factor=100.0)
        assert cfg.scale_factor == 100.0

    def test_empty_query_list(self):
        cfg = BenchmarkConfig(query_ids=[])
        assert cfg.query_ids == []

    def test_single_query(self):
        cfg = BenchmarkConfig(query_ids=["Q17"])
        assert cfg.query_ids == ["Q17"]


class TestQueryBenchmarkResult:
    def test_basic_creation(self):
        r = _make_query_result(times=[10.0, 12.0, 11.0])
        assert r.query_id == "Q1"
        assert r.platform == "polars-df"
        assert r.iterations == 3
        assert r.execution_times_ms == [10.0, 12.0, 11.0]
        assert r.status == "SUCCESS"
        assert r.error_message is None

    def test_error_result(self):
        r = _make_query_result(times=[], status="ERROR", error_message="timeout")
        assert r.status == "ERROR"
        assert r.error_message == "timeout"
        assert r.iterations == 0

    def test_mean_time_ms(self):
        r = _make_query_result(times=[10.0, 20.0, 30.0])
        assert r.mean_time_ms == pytest.approx(20.0)

    def test_mean_time_ms_empty(self):
        r = _make_query_result(times=[])
        assert r.mean_time_ms == 0.0

    def test_std_time_ms_multiple(self):
        times = [10.0, 20.0, 30.0]
        r = _make_query_result(times=times)
        assert r.std_time_ms == pytest.approx(statistics.stdev(times))

    def test_std_time_ms_single(self):
        r = _make_query_result(times=[42.0])
        assert r.std_time_ms == 0.0

    def test_std_time_ms_empty(self):
        r = _make_query_result(times=[])
        assert r.std_time_ms == 0.0

    def test_min_max_time_ms(self):
        r = _make_query_result(times=[5.0, 15.0, 10.0])
        assert r.min_time_ms == 5.0
        assert r.max_time_ms == 15.0

    def test_min_max_empty(self):
        r = _make_query_result(times=[])
        assert r.min_time_ms == 0.0
        assert r.max_time_ms == 0.0

    def test_p50_time_ms(self):
        r = _make_query_result(times=[1.0, 2.0, 3.0, 4.0, 5.0])
        assert r.p50_time_ms == pytest.approx(statistics.median([1.0, 2.0, 3.0, 4.0, 5.0]))

    def test_p50_empty(self):
        r = _make_query_result(times=[])
        assert r.p50_time_ms == 0.0

    def test_p95_time_ms(self):
        times = list(range(1, 101))
        r = _make_query_result(times=[float(t) for t in times])
        assert r.p95_time_ms == 96.0

    def test_p95_single_value(self):
        r = _make_query_result(times=[7.0])
        assert r.p95_time_ms == 7.0

    def test_coefficient_of_variation(self):
        times = [100.0, 100.0, 100.0]
        r = _make_query_result(times=times)
        assert r.coefficient_of_variation == pytest.approx(0.0)

    def test_cv_with_spread(self):
        times = [10.0, 20.0]
        r = _make_query_result(times=times)
        expected_cv = (statistics.stdev(times) / statistics.mean(times)) * 100
        assert r.coefficient_of_variation == pytest.approx(expected_cv)

    def test_cv_empty(self):
        r = _make_query_result(times=[])
        assert r.coefficient_of_variation == 0.0

    def test_to_dict_keys(self):
        r = _make_query_result(times=[10.0, 20.0])
        d = r.to_dict()
        expected_keys = {
            "query_id",
            "platform",
            "iterations",
            "execution_times_ms",
            "mean_time_ms",
            "std_time_ms",
            "min_time_ms",
            "max_time_ms",
            "p50_time_ms",
            "p95_time_ms",
            "cv_percent",
            "memory_peak_mb",
            "rows_returned",
            "status",
            "error_message",
            "query_plan",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_values(self):
        r = _make_query_result(query_id="Q6", platform="pandas-df", times=[8.0, 12.0])
        d = r.to_dict()
        assert d["query_id"] == "Q6"
        assert d["platform"] == "pandas-df"
        assert d["iterations"] == 2
        assert d["execution_times_ms"] == [8.0, 12.0]
        assert d["mean_time_ms"] == pytest.approx(10.0)
        assert d["min_time_ms"] == 8.0
        assert d["max_time_ms"] == 12.0
        assert d["status"] == "SUCCESS"


class TestPlatformBenchmarkResult:
    def test_empty_query_results(self):
        r = _make_platform_result(query_results=[])
        assert r.total_time_ms == 0.0
        assert r.geometric_mean_ms == 0.0
        assert r.successful_queries == 0
        assert r.failed_queries == 0
        assert r.success_rate == 0.0

    def test_single_successful_query(self):
        qr = _make_query_result(times=[10.0, 20.0, 30.0])
        r = _make_platform_result(query_results=[qr])

        assert r.successful_queries == 1
        assert r.failed_queries == 0
        assert r.success_rate == 100.0
        assert r.total_time_ms == pytest.approx(qr.mean_time_ms)
        assert r.geometric_mean_ms == pytest.approx(qr.mean_time_ms)

    def test_multiple_successful_queries(self):
        q1 = _make_query_result(query_id="Q1", times=[10.0])
        q2 = _make_query_result(query_id="Q6", times=[40.0])
        r = _make_platform_result(query_results=[q1, q2])

        assert r.successful_queries == 2
        assert r.total_time_ms == pytest.approx(50.0)
        expected_geomean = math.exp((math.log(10.0) + math.log(40.0)) / 2)
        assert r.geometric_mean_ms == pytest.approx(expected_geomean)

    def test_mixed_success_and_error(self):
        q_ok = _make_query_result(query_id="Q1", times=[20.0])
        q_err = _make_query_result(query_id="Q3", times=[], status="ERROR", error_message="crash")
        r = _make_platform_result(query_results=[q_ok, q_err])

        assert r.successful_queries == 1
        assert r.failed_queries == 1
        assert r.success_rate == pytest.approx(50.0)
        assert r.total_time_ms == pytest.approx(20.0)

    def test_all_errors(self):
        q1 = _make_query_result(query_id="Q1", times=[], status="ERROR")
        q2 = _make_query_result(query_id="Q6", times=[], status="ERROR")
        r = _make_platform_result(query_results=[q1, q2])

        assert r.success_rate == 0.0
        assert r.geometric_mean_ms == 0.0

    def test_capability_attached(self):
        r = _make_platform_result(platform="polars-df")
        assert r.capability is not None
        assert r.capability.platform_name == "polars-df"

    def test_capability_none_for_unknown(self):
        r = _make_platform_result(platform="unknown-df")
        assert r.capability is None

    def test_to_dict_structure(self):
        qr = _make_query_result(times=[5.0, 10.0])
        r = _make_platform_result(platform="polars-df", query_results=[qr])
        d = r.to_dict()

        assert d["platform"] == "polars-df"
        assert "capability" in d
        assert d["capability"]["family"] == "expression"
        assert d["capability"]["category"] == "single_node"
        assert d["capability"]["supports_lazy"] is True
        assert "config" in d
        assert d["config"]["scale_factor"] == 0.01
        assert len(d["query_results"]) == 1
        assert "summary" in d
        assert d["summary"]["successful_queries"] == 1
        assert d["summary"]["failed_queries"] == 0
        assert d["summary"]["success_rate"] == 100.0
        assert "timestamp" in d

    def test_to_dict_null_capability(self):
        r = _make_platform_result(platform="mystery-df")
        d = r.to_dict()
        assert d["capability"]["family"] is None
        assert d["capability"]["category"] is None
        assert d["capability"]["supports_lazy"] is False

    def test_timestamp_is_utc(self):
        r = _make_platform_result()
        assert r.timestamp.tzinfo is not None
        assert r.timestamp.tzinfo == timezone.utc


class TestComparisonSummary:
    def test_construction(self):
        s = ComparisonSummary(
            platforms=["polars-df", "pandas-df"],
            fastest_platform="polars-df",
            slowest_platform="pandas-df",
            speedup_matrix={"polars-df": {"polars-df": 1.0, "pandas-df": 2.0}},
            query_winners={"Q1": "polars-df"},
            total_queries=1,
            scale_factor=0.01,
        )
        assert s.fastest_platform == "polars-df"
        assert s.slowest_platform == "pandas-df"
        assert s.total_queries == 1
        assert s.scale_factor == 0.01

    def test_to_dict_keys(self):
        s = ComparisonSummary(
            platforms=["a"],
            fastest_platform="a",
            slowest_platform="a",
            speedup_matrix={},
            query_winners={},
            total_queries=0,
            scale_factor=0.01,
        )
        d = s.to_dict()
        expected_keys = {
            "platforms",
            "fastest_platform",
            "slowest_platform",
            "speedup_matrix",
            "query_winners",
            "total_queries",
            "scale_factor",
        }
        assert set(d.keys()) == expected_keys


class TestSQLComparisonResult:
    def test_speedup_calculated_on_init(self):
        r = SQLComparisonResult(
            query_id="Q1",
            sql_platform="duckdb",
            df_platform="polars-df",
            sql_time_ms=100.0,
            df_time_ms=50.0,
        )
        assert r.speedup == pytest.approx(2.0)

    def test_speedup_sql_faster(self):
        r = SQLComparisonResult(
            query_id="Q1",
            sql_platform="duckdb",
            df_platform="polars-df",
            sql_time_ms=50.0,
            df_time_ms=100.0,
        )
        assert r.speedup == pytest.approx(0.5)

    def test_speedup_zero_times_unchanged(self):
        r = SQLComparisonResult(
            query_id="Q1",
            sql_platform="duckdb",
            df_platform="polars-df",
            sql_time_ms=0.0,
            df_time_ms=0.0,
        )
        assert r.speedup == 1.0

    def test_to_dict_keys(self):
        r = SQLComparisonResult(
            query_id="Q1",
            sql_platform="duckdb",
            df_platform="polars-df",
        )
        d = r.to_dict()
        expected_keys = {
            "query_id",
            "sql_platform",
            "df_platform",
            "sql_time_ms",
            "df_time_ms",
            "speedup",
            "sql_rows",
            "df_rows",
            "results_match",
            "status",
            "error_message",
        }
        assert set(d.keys()) == expected_keys


class TestSQLVsDataFrameSummary:
    def test_df_wins_percentage(self):
        s = SQLVsDataFrameSummary(
            sql_platform="duckdb",
            df_platform="polars-df",
            total_queries=10,
            df_faster_count=7,
            sql_faster_count=3,
            average_speedup=1.5,
            max_speedup=3.0,
            min_speedup=0.5,
        )
        assert s.df_wins_percentage == pytest.approx(70.0)

    def test_df_wins_percentage_zero_queries(self):
        s = SQLVsDataFrameSummary(
            sql_platform="duckdb",
            df_platform="polars-df",
            total_queries=0,
            df_faster_count=0,
            sql_faster_count=0,
            average_speedup=1.0,
            max_speedup=1.0,
            min_speedup=1.0,
        )
        assert s.df_wins_percentage == 0.0

    def test_to_dict_includes_df_wins_percentage(self):
        s = SQLVsDataFrameSummary(
            sql_platform="duckdb",
            df_platform="polars-df",
            total_queries=4,
            df_faster_count=3,
            sql_faster_count=1,
            average_speedup=2.0,
            max_speedup=4.0,
            min_speedup=0.8,
        )
        d = s.to_dict()
        assert d["df_wins_percentage"] == pytest.approx(75.0)
        assert d["total_queries"] == 4
        assert d["average_speedup"] == 2.0

    def test_to_dict_includes_query_results(self):
        qr = SQLComparisonResult(
            query_id="Q1",
            sql_platform="duckdb",
            df_platform="polars-df",
            sql_time_ms=10.0,
            df_time_ms=5.0,
        )
        s = SQLVsDataFrameSummary(
            sql_platform="duckdb",
            df_platform="polars-df",
            total_queries=1,
            df_faster_count=1,
            sql_faster_count=0,
            average_speedup=2.0,
            max_speedup=2.0,
            min_speedup=2.0,
            query_results=[qr],
        )
        d = s.to_dict()
        assert len(d["query_results"]) == 1
        assert d["query_results"][0]["query_id"] == "Q1"


class TestDataFrameBenchmarkSuiteInit:
    def test_default_config(self):
        suite = DataFrameBenchmarkSuite()
        assert suite.config.scale_factor == 0.01
        assert suite.config.benchmark_iterations == 3

    def test_custom_config(self):
        cfg = BenchmarkConfig(scale_factor=1.0, query_ids=["Q1"])
        suite = DataFrameBenchmarkSuite(config=cfg)
        assert suite.config is cfg
        assert suite.config.scale_factor == 1.0

    def test_platform_capability_lookup_known(self):
        suite = DataFrameBenchmarkSuite()
        cap = suite.get_platform_capability("polars-df")
        assert cap is not None
        assert cap.platform_name == "polars-df"
        assert cap.family == "expression"

    def test_platform_capability_lookup_unknown(self):
        suite = DataFrameBenchmarkSuite()
        cap = suite.get_platform_capability("nonexistent-df")
        assert cap is None

    def test_query_registry_populated(self):
        suite = DataFrameBenchmarkSuite()
        assert suite._query_registry is not None


class TestSpeedupMatrix:
    def _suite(self) -> DataFrameBenchmarkSuite:
        return DataFrameBenchmarkSuite()

    def test_two_platforms(self):
        suite = self._suite()
        geomeans = {"polars-df": 10.0, "pandas-df": 20.0}
        matrix = suite._build_speedup_matrix(["polars-df", "pandas-df"], geomeans)

        assert matrix["polars-df"]["pandas-df"] == pytest.approx(2.0)
        assert matrix["pandas-df"]["polars-df"] == pytest.approx(0.5)
        assert matrix["polars-df"]["polars-df"] == pytest.approx(1.0)
        assert matrix["pandas-df"]["pandas-df"] == pytest.approx(1.0)

    def test_missing_geomean_defaults_to_one(self):
        suite = self._suite()
        geomeans = {"polars-df": 10.0}
        matrix = suite._build_speedup_matrix(["polars-df", "pandas-df"], geomeans)

        assert matrix["pandas-df"]["polars-df"] == 1.0
        assert matrix["polars-df"]["pandas-df"] == 1.0

    def test_zero_geomean_defaults_to_one(self):
        suite = self._suite()
        geomeans = {"polars-df": 0.0, "pandas-df": 10.0}
        matrix = suite._build_speedup_matrix(["polars-df", "pandas-df"], geomeans)

        assert matrix["polars-df"]["pandas-df"] == 1.0

    def test_single_platform(self):
        suite = self._suite()
        geomeans = {"polars-df": 10.0}
        matrix = suite._build_speedup_matrix(["polars-df"], geomeans)

        assert matrix["polars-df"]["polars-df"] == pytest.approx(1.0)

    def test_empty_platforms(self):
        suite = self._suite()
        matrix = suite._build_speedup_matrix([], {})
        assert matrix == {}


class TestFindQueryWinners:
    def _suite(self) -> DataFrameBenchmarkSuite:
        return DataFrameBenchmarkSuite()

    def test_single_platform_wins_all(self):
        suite = self._suite()
        q1 = _make_query_result(query_id="Q1", platform="polars-df", times=[10.0])
        q6 = _make_query_result(query_id="Q6", platform="polars-df", times=[20.0])
        r = _make_platform_result(platform="polars-df", query_results=[q1, q6])

        winners = suite._find_query_winners([r])
        assert winners["Q1"] == "polars-df"
        assert winners["Q6"] == "polars-df"

    def test_two_platforms_different_winners(self):
        suite = self._suite()
        polars_q1 = _make_query_result(query_id="Q1", platform="polars-df", times=[5.0])
        polars_q6 = _make_query_result(query_id="Q6", platform="polars-df", times=[30.0])
        pandas_q1 = _make_query_result(query_id="Q1", platform="pandas-df", times=[15.0])
        pandas_q6 = _make_query_result(query_id="Q6", platform="pandas-df", times=[10.0])

        r_polars = _make_platform_result(platform="polars-df", query_results=[polars_q1, polars_q6])
        r_pandas = _make_platform_result(platform="pandas-df", query_results=[pandas_q1, pandas_q6])

        winners = suite._find_query_winners([r_polars, r_pandas])
        assert winners["Q1"] == "polars-df"
        assert winners["Q6"] == "pandas-df"

    def test_error_results_excluded(self):
        suite = self._suite()
        q_ok = _make_query_result(query_id="Q1", platform="polars-df", times=[10.0])
        q_err = _make_query_result(query_id="Q1", platform="pandas-df", times=[], status="ERROR")
        r_polars = _make_platform_result(platform="polars-df", query_results=[q_ok])
        r_pandas = _make_platform_result(platform="pandas-df", query_results=[q_err])

        winners = suite._find_query_winners([r_polars, r_pandas])
        assert winners["Q1"] == "polars-df"

    def test_empty_results(self):
        suite = self._suite()
        winners = suite._find_query_winners([])
        assert winners == {}


class TestGetSummary:
    def _suite(self) -> DataFrameBenchmarkSuite:
        return DataFrameBenchmarkSuite(config=BenchmarkConfig(scale_factor=0.01))

    def test_raises_on_empty(self):
        suite = self._suite()
        with pytest.raises(ValueError, match="No results"):
            suite.get_summary([])

    def test_single_platform_summary(self):
        suite = self._suite()
        q1 = _make_query_result(query_id="Q1", platform="polars-df", times=[10.0])
        r = _make_platform_result(platform="polars-df", query_results=[q1])

        summary = suite.get_summary([r])
        assert summary.fastest_platform == "polars-df"
        assert summary.slowest_platform == "polars-df"
        assert summary.total_queries == 1
        assert summary.scale_factor == 0.01
        assert "Q1" in summary.query_winners

    def test_two_platform_summary(self):
        suite = self._suite()
        polars_q = _make_query_result(query_id="Q1", platform="polars-df", times=[10.0])
        pandas_q = _make_query_result(query_id="Q1", platform="pandas-df", times=[30.0])
        r_polars = _make_platform_result(platform="polars-df", query_results=[polars_q])
        r_pandas = _make_platform_result(platform="pandas-df", query_results=[pandas_q])

        summary = suite.get_summary([r_polars, r_pandas])
        assert summary.fastest_platform == "polars-df"
        assert summary.slowest_platform == "pandas-df"
        assert len(summary.platforms) == 2
        assert "polars-df" in summary.speedup_matrix
        assert "pandas-df" in summary.speedup_matrix

    def test_summary_with_all_errors(self):
        suite = self._suite()
        q_err = _make_query_result(query_id="Q1", platform="polars-df", times=[], status="ERROR")
        r = _make_platform_result(platform="polars-df", query_results=[q_err])

        summary = suite.get_summary([r])
        assert summary.fastest_platform == "polars-df"
        assert summary.total_queries == 1


class TestExportResults:
    def _suite(self) -> DataFrameBenchmarkSuite:
        return DataFrameBenchmarkSuite(config=BenchmarkConfig(scale_factor=0.01))

    def _sample_results(self) -> list[PlatformBenchmarkResult]:
        q1 = _make_query_result(query_id="Q1", platform="polars-df", times=[10.0, 12.0])
        return [_make_platform_result(platform="polars-df", query_results=[q1])]

    def test_export_json(self, tmp_path: Path):
        suite = self._suite()
        results = self._sample_results()
        out = suite.export_results(results, tmp_path / "results.json", format="json")

        assert out.exists()
        data = json.loads(out.read_text())
        assert data["benchmark_suite"] == "dataframe_comparison"
        assert data["config"]["scale_factor"] == 0.01
        assert len(data["results"]) == 1
        assert data["summary"] is not None

    def test_export_markdown(self, tmp_path: Path):
        suite = self._suite()
        results = self._sample_results()
        out = suite.export_results(results, tmp_path / "report.md", format="markdown")

        assert out.exists()
        content = out.read_text()
        assert "# DataFrame Cross-Platform Benchmark Report" in content
        assert "polars-df" in content
        assert "Scale Factor" in content

    def test_export_unsupported_format(self, tmp_path: Path):
        suite = self._suite()
        results = self._sample_results()
        with pytest.raises(ValueError, match="Unsupported export format"):
            suite.export_results(results, tmp_path / "out.csv", format="csv")

    def test_export_creates_parent_dirs(self, tmp_path: Path):
        suite = self._suite()
        results = self._sample_results()
        deep_path = tmp_path / "a" / "b" / "c" / "results.json"
        out = suite.export_results(results, deep_path, format="json")
        assert out.exists()

    def test_export_json_round_trip(self, tmp_path: Path):
        suite = self._suite()
        results = self._sample_results()
        out = suite.export_results(results, tmp_path / "results.json", format="json")

        data = json.loads(out.read_text())
        qr = data["results"][0]["query_results"][0]
        assert qr["query_id"] == "Q1"
        assert qr["execution_times_ms"] == [10.0, 12.0]
        assert qr["mean_time_ms"] == pytest.approx(11.0)


class TestRunComparisonValidation:
    def test_missing_data_dir_raises(self, tmp_path: Path):
        suite = DataFrameBenchmarkSuite()
        nonexistent = tmp_path / "does_not_exist"
        with pytest.raises(ValueError, match="Data directory not found"):
            suite.run_comparison(platforms=["polars-df"], data_dir=nonexistent)


class TestMarkdownReport:
    def test_report_contains_sections(self):
        suite = DataFrameBenchmarkSuite(config=BenchmarkConfig(scale_factor=0.01))
        q1_p = _make_query_result(query_id="Q1", platform="polars-df", times=[10.0])
        q1_pd = _make_query_result(query_id="Q1", platform="pandas-df", times=[20.0])
        r_p = _make_platform_result(platform="polars-df", query_results=[q1_p])
        r_pd = _make_platform_result(platform="pandas-df", query_results=[q1_pd])

        md = suite._generate_markdown_report([r_p, r_pd])

        assert "# DataFrame Cross-Platform Benchmark Report" in md
        assert "## Performance Summary" in md
        assert "### Geometric Mean Comparison" in md
        assert "## Query-by-Query Results" in md
        assert "## Speedup Matrix" in md
        assert "polars-df" in md
        assert "pandas-df" in md

    def test_report_single_platform_no_speedup_matrix(self):
        suite = DataFrameBenchmarkSuite()
        q1 = _make_query_result(query_id="Q1", platform="polars-df", times=[10.0])
        r = _make_platform_result(platform="polars-df", query_results=[q1])

        md = suite._generate_markdown_report([r])

        assert "# DataFrame Cross-Platform Benchmark Report" in md
        assert "## Speedup Matrix" not in md


class TestDataFrameComparisonPlotterInit:
    def test_empty_results_raises(self):
        from benchbox.core.dataframe.benchmark_suite import DataFrameComparisonPlotter

        with pytest.raises(ValueError, match="No results"):
            DataFrameComparisonPlotter(results=[])

    def test_valid_results_stored(self):
        from benchbox.core.dataframe.benchmark_suite import DataFrameComparisonPlotter

        q = _make_query_result(times=[10.0])
        r = _make_platform_result(query_results=[q])
        plotter = DataFrameComparisonPlotter(results=[r])
        assert len(plotter.results) == 1
        assert plotter.theme == "light"


class TestSQLVsDataFramePlotterInit:
    def test_valid_summary_stored(self):
        from benchbox.core.dataframe.benchmark_suite import SQLVsDataFramePlotter

        summary = SQLVsDataFrameSummary(
            sql_platform="duckdb",
            df_platform="polars-df",
            total_queries=1,
            df_faster_count=1,
            sql_faster_count=0,
            average_speedup=2.0,
            max_speedup=2.0,
            min_speedup=2.0,
        )
        plotter = SQLVsDataFramePlotter(summary=summary)
        assert plotter.summary is summary
        assert plotter.theme == "light"

    def test_custom_theme(self):
        from benchbox.core.dataframe.benchmark_suite import SQLVsDataFramePlotter

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
        plotter = SQLVsDataFramePlotter(summary=summary, theme="dark")
        assert plotter.theme == "dark"


class TestSQLVsDataFrameBenchmarkBuildSummary:
    def _benchmark(self):
        from benchbox.core.dataframe.benchmark_suite import SQLVsDataFrameBenchmark

        return SQLVsDataFrameBenchmark(config=BenchmarkConfig(scale_factor=0.01))

    def test_all_successful(self):
        bm = self._benchmark()
        results = [
            SQLComparisonResult(
                query_id="Q1",
                sql_platform="duckdb",
                df_platform="polars-df",
                sql_time_ms=100.0,
                df_time_ms=50.0,
            ),
            SQLComparisonResult(
                query_id="Q6",
                sql_platform="duckdb",
                df_platform="polars-df",
                sql_time_ms=80.0,
                df_time_ms=100.0,
            ),
        ]
        summary = bm._build_summary("duckdb", "polars-df", results)

        assert summary.total_queries == 2
        assert summary.df_faster_count == 1
        assert summary.sql_faster_count == 1
        assert summary.average_speedup == pytest.approx(statistics.mean([2.0, 0.8]))
        assert summary.max_speedup == pytest.approx(2.0)
        assert summary.min_speedup == pytest.approx(0.8)

    def test_all_errors(self):
        bm = self._benchmark()
        results = [
            SQLComparisonResult(
                query_id="Q1",
                sql_platform="duckdb",
                df_platform="polars-df",
                status="ERROR",
                error_message="fail",
            ),
        ]
        summary = bm._build_summary("duckdb", "polars-df", results)

        assert summary.total_queries == 1
        assert summary.df_faster_count == 0
        assert summary.sql_faster_count == 0
        assert summary.average_speedup == 1.0

    def test_generate_report_content(self):
        bm = self._benchmark()
        qr = SQLComparisonResult(
            query_id="Q1",
            sql_platform="duckdb",
            df_platform="polars-df",
            sql_time_ms=100.0,
            df_time_ms=50.0,
        )
        summary = bm._build_summary("duckdb", "polars-df", [qr])
        report = bm.generate_report(summary)

        assert "# SQL vs DataFrame Performance Comparison" in report
        assert "duckdb" in report
        assert "polars-df" in report
        assert "DataFrame wins" in report

    def test_generate_report_sql_faster(self):
        bm = self._benchmark()
        qr = SQLComparisonResult(
            query_id="Q1",
            sql_platform="duckdb",
            df_platform="polars-df",
            sql_time_ms=50.0,
            df_time_ms=100.0,
        )
        summary = bm._build_summary("duckdb", "polars-df", [qr])
        report = bm.generate_report(summary)

        assert "SQL execution is" in report
        assert "faster" in report


class TestSQLVsDataFrameBenchmarkExecution:
    def _benchmark(self, query_ids: list[str] | None = None):
        from benchbox.core.dataframe.benchmark_suite import SQLVsDataFrameBenchmark

        return SQLVsDataFrameBenchmark(config=BenchmarkConfig(scale_factor=0.01, query_ids=query_ids))

    def test_run_comparison_uses_configured_query_ids(self, tmp_path):
        bm = self._benchmark(query_ids=["Q1", "Q6"])

        seen: list[str] = []

        def _fake_compare_query(**kwargs):
            seen.append(kwargs["query_id"])
            return SQLComparisonResult(
                query_id=kwargs["query_id"],
                sql_platform=kwargs["sql_platform"],
                df_platform=kwargs["df_platform"],
                sql_time_ms=100.0,
                df_time_ms=80.0,
            )

        bm._compare_query = _fake_compare_query

        with patch.object(bm, "_build_summary", return_value="summary") as build_summary:
            summary = bm.run_comparison("duckdb", "polars-df", tmp_path)

        assert summary == "summary"
        assert seen == ["Q1", "Q6"]
        build_summary.assert_called_once()

    def test_compare_query_success_records_row_match(self, tmp_path):
        bm = self._benchmark()
        bm._run_sql_query = MagicMock(return_value=(110.0, 4))
        bm._run_df_query = MagicMock(return_value=(90.0, 4))

        result = bm._compare_query("Q1", "duckdb", "polars-df", tmp_path)

        assert result.status == "SUCCESS"
        assert result.results_match is True
        assert result.speedup == pytest.approx(110.0 / 90.0)

    def test_compare_query_error_returns_error_result(self, tmp_path):
        bm = self._benchmark()
        bm._run_sql_query = MagicMock(side_effect=RuntimeError("boom"))

        result = bm._compare_query("Q1", "duckdb", "polars-df", tmp_path)

        assert result.status == "ERROR"
        assert result.error_message == "boom"

    def test_run_sql_query_uses_duckdb_connection_and_root_data_dir(self, tmp_path):
        bm = self._benchmark()
        fake_conn = MagicMock()
        fake_query = SimpleNamespace(sql="SELECT 1")
        (tmp_path / "lineitem.parquet").write_text("stub")

        with (
            patch("benchbox.core.tpch.queries.TPCHQuery", create=True) as tpch_query,
            patch.object(bm, "_connect_duckdb", return_value=fake_conn) as connect_duckdb,
            patch.object(bm, "_warmup_and_benchmark", return_value=(12.5, 3)) as warmup,
        ):
            tpch_query.get_query.return_value = fake_query
            result = bm._run_sql_query("Q1", "duckdb", tmp_path)

        assert result == (12.5, 3)
        tpch_query.get_query.assert_called_once()
        connect_duckdb.assert_called_once_with(tmp_path)
        warmup.assert_called_once_with(fake_conn, "SELECT 1")
        fake_conn.close.assert_called_once()

    def test_run_sql_query_rejects_unknown_platform(self, tmp_path):
        bm = self._benchmark()

        with patch("benchbox.core.tpch.queries.TPCHQuery", create=True) as tpch_query:
            tpch_query.get_query.return_value = SimpleNamespace(sql="SELECT 1")
            with pytest.raises(ValueError, match="Unsupported SQL platform"):
                bm._run_sql_query("Q1", "unknown-sql", tmp_path)

    def test_warmup_and_benchmark_averages_timed_iterations(self):
        bm = self._benchmark()
        bm.config.warmup_iterations = 1
        bm.config.benchmark_iterations = 2

        class FakeConnection:
            def execute(self, _sql):
                return self

            def fetchall(self):
                return [(1,), (2,)]

        with patch(SUITE_TIME, _real_time_with(perf_counter=iter([1.0, 1.1, 2.0, 2.4]).__next__)):
            avg_ms, row_count = bm._warmup_and_benchmark(FakeConnection(), "SELECT 1")

        assert avg_ms == pytest.approx(250.0)
        assert row_count == 2

    def test_run_df_query_loads_existing_tables_and_benchmarks_query(self, tmp_path):
        bm = self._benchmark(query_ids=["Q1"])
        bm.config.warmup_iterations = 1
        bm.config.benchmark_iterations = 2
        fake_adapter = MagicMock()
        fake_context = object()
        fake_adapter.create_context.return_value = fake_context

        query = MagicMock()
        query.get_impl_for_family.return_value = object()
        query.execute.return_value = [1, 2, 3]
        bm._query_registry = MagicMock()
        bm._query_registry.get.return_value = query

        (tmp_path / "lineitem.parquet").write_text("stub")
        (tmp_path / "orders.parquet").write_text("stub")

        with (
            patch("benchbox.platforms.get_dataframe_adapter", return_value=fake_adapter),
            patch(SUITE_TIME, _real_time_with(perf_counter=iter([1.0, 1.2, 2.0, 2.3]).__next__)),
        ):
            avg_ms, row_count = bm._run_df_query("Q1", "polars-df", tmp_path)

        assert avg_ms == pytest.approx(250.0)
        assert row_count == 3
        loaded_tables = [call.args[1] for call in fake_adapter.load_table.call_args_list]
        assert loaded_tables == ["lineitem", "orders"]
        query.execute.assert_called()

    def test_run_df_query_missing_query_raises(self, tmp_path):
        bm = self._benchmark()
        bm._query_registry = MagicMock()
        bm._query_registry.get.return_value = None

        with patch("benchbox.platforms.get_dataframe_adapter", return_value=MagicMock(create_context=lambda: object())):
            with pytest.raises(ValueError, match="Query Q99 not found"):
                bm._run_df_query("Q99", "polars-df", tmp_path)


class TestBenchmarkSuiteConvenienceFunctions:
    def test_run_quick_comparison_uses_default_datagen_path(self):
        fake_results = [_make_platform_result()]

        with (
            patch(
                "benchbox.core.dataframe.benchmark_suite.get_benchmark_runs_datagen_path",
                return_value=Path("/tmp/tpch"),
            ),
            patch(
                "benchbox.core.dataframe.benchmark_suite.DataFrameBenchmarkSuite.run_comparison",
                return_value=fake_results,
            ) as run_comparison,
        ):
            result = __import__(
                "benchbox.core.dataframe.benchmark_suite", fromlist=["run_quick_comparison"]
            ).run_quick_comparison()

        assert result == fake_results
        run_comparison.assert_called_once()

    def test_run_sql_vs_dataframe_uses_default_datagen_path(self):
        fake_summary = SQLVsDataFrameSummary(
            sql_platform="duckdb",
            df_platform="polars-df",
            total_queries=1,
            df_faster_count=1,
            sql_faster_count=0,
            average_speedup=2.0,
            max_speedup=2.0,
            min_speedup=2.0,
            query_results=[],
        )

        with (
            patch(
                "benchbox.core.dataframe.benchmark_suite.get_benchmark_runs_datagen_path",
                return_value=Path("/tmp/tpch"),
            ),
            patch(
                "benchbox.core.dataframe.benchmark_suite.SQLVsDataFrameBenchmark.run_comparison",
                return_value=fake_summary,
            ) as run_comparison,
        ):
            result = __import__(
                "benchbox.core.dataframe.benchmark_suite", fromlist=["run_sql_vs_dataframe"]
            ).run_sql_vs_dataframe()

        assert result == fake_summary
        run_comparison.assert_called_once()

    def test_dataframe_comparison_plotter_delegates_to_chart_generator(self, tmp_path):
        from benchbox.core.dataframe.benchmark_suite import DataFrameComparisonPlotter

        result = _make_platform_result()
        output_paths = {"performance_bar": tmp_path / "performance_bar.txt"}

        with (
            patch(
                "benchbox.core.visualization.chart_generator.normalized_from_dataframe",
                return_value={"normalized": True},
            ) as normalized,
            patch(
                "benchbox.core.visualization.chart_generator.generate_comparison_charts",
                return_value=output_paths,
            ) as generate,
        ):
            plotter = DataFrameComparisonPlotter(results=[result])
            exported = plotter.generate_charts(tmp_path)

        assert exported == output_paths
        normalized.assert_called_once_with([result])
        generate.assert_called_once()

    def test_sql_vs_dataframe_plotter_delegates_to_chart_generator(self, tmp_path):
        from benchbox.core.dataframe.benchmark_suite import SQLVsDataFramePlotter

        summary = SQLVsDataFrameSummary(
            sql_platform="duckdb",
            df_platform="polars-df",
            total_queries=1,
            df_faster_count=1,
            sql_faster_count=0,
            average_speedup=2.0,
            max_speedup=2.0,
            min_speedup=2.0,
            query_results=[],
        )
        output_paths = {"query_heatmap": tmp_path / "query_heatmap.txt"}

        with (
            patch(
                "benchbox.core.visualization.chart_generator.normalized_from_sql_vs_df",
                return_value={"normalized": True},
            ) as normalized,
            patch(
                "benchbox.core.visualization.chart_generator.generate_comparison_charts",
                return_value=output_paths,
            ) as generate,
        ):
            plotter = SQLVsDataFramePlotter(summary=summary)
            exported = plotter.generate_charts(tmp_path)

        assert exported == output_paths
        normalized.assert_called_once_with(summary)
        generate.assert_called_once()


class TestResolveTablePaths:
    def test_single_file_preferred(self, tmp_path):
        single = tmp_path / "lineitem.parquet"
        single.write_text("stub")
        (tmp_path / "lineitem.1.parquet").write_text("stub")

        assert resolve_table_paths(tmp_path, "lineitem") == [single]

    def test_shards_returned_in_numeric_order(self, tmp_path):
        for shard in ("lineitem.2.parquet", "lineitem.10.parquet", "lineitem.1.parquet"):
            (tmp_path / shard).write_text("stub")

        assert resolve_table_paths(tmp_path, "lineitem") == [
            tmp_path / "lineitem.1.parquet",
            tmp_path / "lineitem.2.parquet",
            tmp_path / "lineitem.10.parquet",
        ]

    def test_non_numeric_suffix_ignored(self, tmp_path):
        (tmp_path / "lineitem.bak.parquet").write_text("stub")

        assert resolve_table_paths(tmp_path, "lineitem") == []

    def test_missing_table_returns_empty(self, tmp_path):
        assert resolve_table_paths(tmp_path, "orders") == []

    def test_other_tables_not_matched(self, tmp_path):
        (tmp_path / "partsupp.1.parquet").write_text("stub")

        assert resolve_table_paths(tmp_path, "part") == []
        assert resolve_table_paths(tmp_path, "partsupp") == [tmp_path / "partsupp.1.parquet"]

    def test_create_context_loads_sharded_tables(self, tmp_path):
        suite = DataFrameBenchmarkSuite(config=BenchmarkConfig())
        (tmp_path / "lineitem.1.parquet").write_text("stub")
        (tmp_path / "lineitem.2.parquet").write_text("stub")
        (tmp_path / "nation.parquet").write_text("stub")
        fake_adapter = MagicMock()
        fake_adapter.create_context.return_value = object()

        with patch("benchbox.platforms.get_dataframe_adapter", return_value=fake_adapter):
            suite._create_context("dask-df", tmp_path)

        loaded = {call.args[1]: call.args[2] for call in fake_adapter.load_table.call_args_list}
        assert loaded["lineitem"] == [tmp_path / "lineitem.1.parquet", tmp_path / "lineitem.2.parquet"]
        assert loaded["nation"] == [tmp_path / "nation.parquet"]
        assert "orders" not in loaded

    def test_single_plus_shards_warns_and_prefers_single(self, tmp_path, caplog):
        single = tmp_path / "lineitem.parquet"
        single.write_text("stub")
        (tmp_path / "lineitem.1.parquet").write_text("stub")

        with caplog.at_level("WARNING", logger="benchbox.core.dataframe.benchmark_suite"):
            assert resolve_table_paths(tmp_path, "lineitem") == [single]
        assert "lineitem.parquet" in caplog.text

    def test_duckdb_connect_loads_shards_via_parameter(self, tmp_path):
        pytest.importorskip("duckdb")
        pd = pytest.importorskip("pandas")
        odd_dir = tmp_path / "odd'dir"
        odd_dir.mkdir()
        pd.DataFrame({"a": [1, 2]}).to_parquet(odd_dir / "nation.1.parquet", index=False)
        pd.DataFrame({"a": [3]}).to_parquet(odd_dir / "nation.2.parquet", index=False)

        conn = SQLVsDataFrameBenchmark._connect_duckdb(odd_dir)
        try:
            assert conn.execute("SELECT COUNT(*) FROM nation").fetchone()[0] == 3
        finally:
            conn.close()

    def test_sqlite_connect_loads_shards_incrementally(self, tmp_path):
        pd = pytest.importorskip("pandas")
        pd.DataFrame({"a": [1, 2]}).to_parquet(tmp_path / "nation.1.parquet", index=False)
        pd.DataFrame({"a": [3, 4, 5]}).to_parquet(tmp_path / "nation.2.parquet", index=False)

        conn = SQLVsDataFrameBenchmark._connect_sqlite(tmp_path)
        try:
            assert conn.execute("SELECT COUNT(*) FROM nation").fetchone()[0] == 5
        finally:
            conn.close()


