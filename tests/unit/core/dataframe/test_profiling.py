# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from benchbox.core.dataframe.profiling import (
    ComparisonResult,
    DataFrameProfiler,
    MemoryTracker,
    ProfiledExecutionResult,
    ProfileMetricType,
    QueryExecutionProfile,
    QueryPlan,
    QueryProfileContext,
    capture_query_plan,
    compare_execution_modes,
    get_current_memory_mb,
    profile_query_execution,
    track_memory,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestQueryExecutionProfile:
    def test_basic_creation(self):

        profile = QueryExecutionProfile(
            query_id="Q1",
            execution_time_ms=1000.0,
        )

        assert profile.query_id == "Q1"
        assert profile.execution_time_ms == 1000.0
        assert profile.planning_time_ms == 0.0
        assert profile.collect_time_ms == 0.0
        assert profile.rows_processed == 0
        assert profile.peak_memory_mb == 0.0
        assert profile.query_plan is None
        assert profile.platform == ""
        assert profile.lazy_evaluation is False
        assert profile.metrics == {}

    def test_full_creation(self):

        plan = QueryPlan(
            platform="polars",
            plan_type="optimized",
            plan_text="FILTER -> GROUP BY",
        )

        profile = QueryExecutionProfile(
            query_id="Q1",
            execution_time_ms=1000.0,
            planning_time_ms=50.0,
            collect_time_ms=200.0,
            rows_processed=1000,
            peak_memory_mb=512.0,
            query_plan=plan,
            platform="polars",
            lazy_evaluation=True,
            metrics={"custom_metric": 42},
        )

        assert profile.planning_time_ms == 50.0
        assert profile.collect_time_ms == 200.0
        assert profile.rows_processed == 1000
        assert profile.peak_memory_mb == 512.0
        assert profile.query_plan is not None
        assert profile.lazy_evaluation is True

    def test_lazy_overhead_ms(self):

        profile = QueryExecutionProfile(
            query_id="Q1",
            execution_time_ms=1000.0,
            planning_time_ms=100.0,
            collect_time_ms=200.0,
        )

        assert profile.lazy_overhead_ms == 300.0

    def test_lazy_overhead_percent(self):

        profile = QueryExecutionProfile(
            query_id="Q1",
            execution_time_ms=1000.0,
            planning_time_ms=100.0,
            collect_time_ms=200.0,
        )

        assert profile.lazy_overhead_percent == 30.0

    def test_lazy_overhead_percent_zero_execution_time(self):

        profile = QueryExecutionProfile(
            query_id="Q1",
            execution_time_ms=0.0,
            planning_time_ms=100.0,
            collect_time_ms=200.0,
        )

        assert profile.lazy_overhead_percent == 0.0


class TestQueryPlan:
    def test_basic_creation(self):

        plan = QueryPlan(
            platform="polars",
            plan_type="optimized",
            plan_text="FILTER col > 10",
        )

        assert plan.platform == "polars"
        assert plan.plan_type == "optimized"
        assert plan.plan_text == "FILTER col > 10"
        assert plan.plan_data is None
        assert plan.optimization_hints == []

    def test_str_method(self):

        plan = QueryPlan(
            platform="polars",
            plan_type="optimized",
            plan_text="FILTER -> GROUP BY -> SORT",
        )

        assert str(plan) == "FILTER -> GROUP BY -> SORT"

    def test_with_hints(self):

        plan = QueryPlan(
            platform="polars",
            plan_type="optimized",
            plan_text="SELECT * FROM table",
            optimization_hints=["Consider selecting only needed columns"],
        )

        assert len(plan.optimization_hints) == 1


class TestQueryProfileContext:
    def test_basic_context(self):

        ctx = QueryProfileContext("Q1", "polars")
        ctx._start_time = time.perf_counter()

        time.sleep(0.05)

        profile = ctx.get_profile()

        assert profile.query_id == "Q1"
        assert profile.platform == "polars"
        assert profile.execution_time_ms >= 10

    def test_planning_phase_timing(self):

        ctx = QueryProfileContext("Q1", "polars")
        ctx._start_time = time.perf_counter()

        ctx.start_planning()
        time.sleep(0.05)
        ctx.end_planning()

        profile = ctx.get_profile()

        assert profile.planning_time_ms >= 10
        assert profile.lazy_evaluation is True

    def test_collect_phase_timing(self):

        ctx = QueryProfileContext("Q1", "polars")
        ctx._start_time = time.perf_counter()

        ctx.start_collect()
        time.sleep(0.05)
        ctx.end_collect()

        profile = ctx.get_profile()

        assert profile.collect_time_ms >= 10

    def test_set_rows(self):

        ctx = QueryProfileContext("Q1", "polars")
        ctx._start_time = time.perf_counter()

        ctx.set_rows(1000)

        profile = ctx.get_profile()
        assert profile.rows_processed == 1000

    def test_set_query_plan(self):

        ctx = QueryProfileContext("Q1", "polars")
        ctx._start_time = time.perf_counter()

        plan = QueryPlan("polars", "optimized", "FILTER -> GROUP BY")
        ctx.set_query_plan(plan)

        profile = ctx.get_profile()
        assert profile.query_plan is not None
        assert profile.query_plan.platform == "polars"

    def test_set_peak_memory(self):

        ctx = QueryProfileContext("Q1", "polars")
        ctx._start_time = time.perf_counter()

        ctx.set_peak_memory(512.5)

        profile = ctx.get_profile()
        assert profile.peak_memory_mb == 512.5

    def test_add_metric(self):

        ctx = QueryProfileContext("Q1", "polars")
        ctx._start_time = time.perf_counter()

        ctx.add_metric("cache_hits", 100)
        ctx.add_metric("shuffle_writes", 1024)

        profile = ctx.get_profile()
        assert profile.metrics["cache_hits"] == 100
        assert profile.metrics["shuffle_writes"] == 1024


class TestMemoryTracker:
    def test_basic_tracking(self):

        tracker = MemoryTracker(sample_interval_ms=10)
        tracker.start()

        data = [0] * 100000
        time.sleep(0.05)

        peak = tracker.stop()

        assert peak >= 0
        del data

    def test_peak_memory_property(self):

        tracker = MemoryTracker(sample_interval_ms=10)
        tracker.start()
        time.sleep(0.03)
        tracker.stop()

        assert tracker.peak_memory_mb >= 0

    def test_samples_property(self):

        tracker = MemoryTracker(sample_interval_ms=10)
        tracker.start()
        time.sleep(0.05)
        tracker.stop()

        samples = tracker.samples
        assert len(samples) >= 1

    def test_get_statistics(self):

        tracker = MemoryTracker(sample_interval_ms=10)
        tracker.start()
        time.sleep(0.03)
        tracker.stop()

        stats = tracker.get_statistics()

        assert "baseline_mb" in stats
        assert "peak_mb" in stats
        assert "peak_delta_mb" in stats
        assert "avg_mb" in stats
        assert "sample_count" in stats
        assert stats["sample_count"] >= 1

    def test_empty_statistics(self):

        tracker = MemoryTracker()
        stats = tracker.get_statistics()

        assert stats["sample_count"] == 0
        assert stats["peak_mb"] == 0.0

    def test_stop_idempotent(self):
        tracker = MemoryTracker(sample_interval_ms=10)
        tracker.start()
        tracker.stop()

        peak = tracker.stop()
        assert peak >= 0


class TestTrackMemoryContextManager:
    def test_context_manager_usage(self):

        with track_memory(sample_interval_ms=10) as tracker:
            data = [0] * 100000
            time.sleep(0.03)
            del data

        assert tracker.peak_memory_mb >= 0


class TestGetCurrentMemoryMb:
    def test_returns_value(self):

        memory = get_current_memory_mb()
        assert memory >= 0


class TestDataFrameProfiler:
    def test_basic_profiling(self):

        profiler = DataFrameProfiler(platform="polars")

        with profiler.profile_query("Q1") as ctx:
            time.sleep(0.01)
            ctx.set_rows(100)

        profiles = profiler.get_profiles()
        assert len(profiles) == 1
        assert profiles[0].query_id == "Q1"
        assert profiles[0].rows_processed == 100

    def test_multiple_queries(self):

        profiler = DataFrameProfiler(platform="polars")

        for i in range(3):
            with profiler.profile_query(f"Q{i + 1}") as ctx:
                time.sleep(0.01)
                ctx.set_rows(100 * (i + 1))

        profiles = profiler.get_profiles()
        assert len(profiles) == 3

    def test_get_profile_by_id(self):

        profiler = DataFrameProfiler(platform="polars")

        with profiler.profile_query("Q1") as ctx:
            ctx.set_rows(100)

        with profiler.profile_query("Q2") as ctx:
            ctx.set_rows(200)

        profile = profiler.get_profile("Q2")
        assert profile is not None
        assert profile.rows_processed == 200

    def test_get_statistics(self):

        profiler = DataFrameProfiler(platform="polars")

        with profiler.profile_query("Q1") as ctx:
            time.sleep(0.01)
            ctx.set_rows(100)

        with profiler.profile_query("Q2") as ctx:
            time.sleep(0.02)
            ctx.set_rows(200)

        stats = profiler.get_statistics()

        assert stats["query_count"] == 2
        assert stats["total_rows_processed"] == 300
        assert stats["avg_rows_per_query"] == 150
        assert stats["total_execution_time_ms"] > 0
        assert stats["platform"] == "polars"

    def test_empty_statistics(self):

        profiler = DataFrameProfiler()
        stats = profiler.get_statistics()

        assert stats["query_count"] == 0
        assert stats["total_execution_time_ms"] == 0

    def test_add_profile(self):

        profiler = DataFrameProfiler()

        profile = QueryExecutionProfile(
            query_id="Q1",
            execution_time_ms=100.0,
            rows_processed=50,
        )

        profiler.add_profile(profile)

        assert len(profiler.get_profiles()) == 1
        assert profiler.get_profile("Q1") is not None

    def test_clear(self):

        profiler = DataFrameProfiler()

        with profiler.profile_query("Q1") as ctx:
            ctx.set_rows(100)

        assert len(profiler.get_profiles()) == 1

        profiler.clear()
        assert len(profiler.get_profiles()) == 0


class TestProfileQueryExecution:
    def test_basic_execution(self):

        def query_fn():
            return [1, 2, 3]

        result, profile = profile_query_execution(
            query_id="Q1",
            platform="test",
            query_fn=query_fn,
            track_memory=False,
        )

        assert result == [1, 2, 3]
        assert profile.query_id == "Q1"
        assert profile.platform == "test"
        assert profile.execution_time_ms > 0

    def test_with_collect(self):

        def query_fn():
            return range(100)

        def collect_fn(lazy):
            return list(lazy)

        def row_count_fn(result):
            return len(result)

        result, profile = profile_query_execution(
            query_id="Q1",
            platform="test",
            query_fn=query_fn,
            collect_fn=collect_fn,
            row_count_fn=row_count_fn,
            track_memory=False,
        )

        assert len(result) == 100
        assert profile.rows_processed == 100
        assert profile.collect_time_ms > 0

    def test_with_memory_tracking(self):

        def query_fn():
            return [0] * 100000

        result, profile = profile_query_execution(
            query_id="Q1",
            platform="test",
            query_fn=query_fn,
            track_memory=True,
            memory_sample_interval_ms=10,
        )

        assert profile.peak_memory_mb >= 0
        assert "memory_baseline_mb" in profile.metrics

    def test_with_plan_capture(self):
        mock_plan = QueryPlan(
            platform="test",
            plan_type="logical",
            plan_text="TEST PLAN",
        )

        def query_fn():
            return [1, 2, 3]

        def plan_capture_fn(df):
            return mock_plan

        result, profile = profile_query_execution(
            query_id="Q1",
            platform="test",
            query_fn=query_fn,
            plan_capture_fn=plan_capture_fn,
            track_memory=False,
        )

        assert profile.query_plan is not None
        assert profile.query_plan.plan_text == "TEST PLAN"


class TestProfiledExecutionResult:
    def test_basic_creation(self):

        profile = QueryExecutionProfile(
            query_id="Q1",
            execution_time_ms=1000.0,
            rows_processed=100,
            peak_memory_mb=512.0,
        )

        result = ProfiledExecutionResult(
            result=[1, 2, 3],
            profile=profile,
        )

        assert result.result == [1, 2, 3]
        assert result.execution_time_ms == 1000.0
        assert result.execution_time_seconds == 1.0
        assert result.rows_returned == 100
        assert result.peak_memory_mb == 512.0


class TestComparisonResult:
    def test_dataframe_faster(self):

        result = ComparisonResult(
            query_id="Q1",
            dataframe_time_ms=100.0,
            sql_time_ms=200.0,
        )

        assert result.speedup == 2.0
        assert result.winner == "dataframe"

    def test_sql_faster(self):

        result = ComparisonResult(
            query_id="Q1",
            dataframe_time_ms=200.0,
            sql_time_ms=100.0,
        )

        assert result.speedup == 0.5
        assert result.winner == "sql"

    def test_no_sql_time(self):

        result = ComparisonResult(
            query_id="Q1",
            dataframe_time_ms=100.0,
        )

        assert result.speedup is None
        assert result.winner == "unknown"


class TestCompareExecutionModes:
    def test_comparison(self):

        profiles = [
            QueryExecutionProfile(query_id="Q1", execution_time_ms=100.0),
            QueryExecutionProfile(query_id="Q2", execution_time_ms=200.0),
        ]

        sql_times = {"Q1": 150.0, "Q2": 100.0}

        results = compare_execution_modes(profiles, sql_times)

        assert len(results) == 2

        assert results[0].winner == "dataframe"

        assert results[1].winner == "sql"


class TestCaptureQueryPlan:
    def test_capture_query_plan_unknown_platform(self):

        result = capture_query_plan({}, "unknown")
        assert result is None

    def test_capture_query_plan_polars_no_explain(self):

        mock_df = MagicMock()
        del mock_df.explain

        result = capture_query_plan(mock_df, "polars")
        assert result is None


class TestProfileMetricType:
    def test_metric_types(self):

        assert ProfileMetricType.TIMING.value == "timing"
        assert ProfileMetricType.MEMORY.value == "memory"
        assert ProfileMetricType.ROWS.value == "rows"
        assert ProfileMetricType.PLAN.value == "plan"


