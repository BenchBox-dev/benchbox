# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import time

import pytest

from benchbox.core.dataframe.profiling import (
    ComparisonResult,
    DataFrameProfiler,
    QueryExecutionProfile,
    QueryPlan,
    QueryProfileContext,
    compare_execution_modes,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestQueryPlan:
    def test_query_plan_creation(self):

        plan = QueryPlan(
            platform="polars",
            plan_type="optimized",
            plan_text="FILTER > 10\n  SCAN table",
        )

        assert plan.platform == "polars"
        assert plan.plan_type == "optimized"
        assert "FILTER" in plan.plan_text

    def test_query_plan_str(self):

        plan = QueryPlan(
            platform="datafusion",
            plan_type="logical",
            plan_text="TableScan: orders",
        )

        assert str(plan) == "TableScan: orders"

    def test_query_plan_with_hints(self):

        plan = QueryPlan(
            platform="pyspark",
            plan_type="extended",
            plan_text="ShuffleExchange",
            optimization_hints=["Consider broadcast join"],
        )

        assert len(plan.optimization_hints) == 1
        assert "broadcast" in plan.optimization_hints[0]


class TestQueryExecutionProfile:
    def test_profile_creation(self):

        profile = QueryExecutionProfile(
            query_id="q1",
            execution_time_ms=150.0,
            planning_time_ms=20.0,
            collect_time_ms=30.0,
            rows_processed=1000,
            platform="polars",
            lazy_evaluation=True,
        )

        assert profile.query_id == "q1"
        assert profile.execution_time_ms == 150.0
        assert profile.lazy_evaluation

    def test_lazy_overhead_calculation(self):

        profile = QueryExecutionProfile(
            query_id="q1",
            execution_time_ms=200.0,
            planning_time_ms=20.0,
            collect_time_ms=30.0,
            lazy_evaluation=True,
        )

        assert profile.lazy_overhead_ms == 50.0
        assert profile.lazy_overhead_percent == 25.0

    def test_lazy_overhead_zero_execution_time(self):

        profile = QueryExecutionProfile(
            query_id="q1",
            execution_time_ms=0.0,
            planning_time_ms=10.0,
            lazy_evaluation=True,
        )

        assert profile.lazy_overhead_percent == 0.0


class TestQueryProfileContext:
    def test_context_creation(self):

        ctx = QueryProfileContext("q1", "polars")

        assert ctx.query_id == "q1"
        assert ctx.platform == "polars"

    def test_planning_timing(self):

        ctx = QueryProfileContext("q1")
        ctx._start_time = time.perf_counter()

        ctx.start_planning()
        time.sleep(0.05)
        ctx.end_planning()

        assert ctx._planning_time >= 10.0
        assert ctx._lazy_evaluation is True

    def test_collect_timing(self):

        ctx = QueryProfileContext("q1")
        ctx._start_time = time.perf_counter()

        ctx.start_collect()
        time.sleep(0.05)
        ctx.end_collect()

        assert ctx._collect_time >= 10.0

    def test_set_rows(self):

        ctx = QueryProfileContext("q1")
        ctx.set_rows(5000)

        assert ctx._rows == 5000

    def test_set_query_plan(self):

        ctx = QueryProfileContext("q1")
        plan = QueryPlan(platform="polars", plan_type="logical", plan_text="SCAN")
        ctx.set_query_plan(plan)

        assert ctx._query_plan is not None
        assert ctx._query_plan.platform == "polars"

    def test_add_metric(self):

        ctx = QueryProfileContext("q1")
        ctx.add_metric("memory_peak_mb", 256.5)
        ctx.add_metric("shuffle_bytes", 1024)

        assert ctx._metrics["memory_peak_mb"] == 256.5
        assert ctx._metrics["shuffle_bytes"] == 1024

    def test_get_profile(self):

        ctx = QueryProfileContext("q1", "polars")
        ctx._start_time = time.perf_counter()

        ctx.set_rows(100)
        time.sleep(0.05)

        profile = ctx.get_profile()

        assert profile.query_id == "q1"
        assert profile.platform == "polars"
        assert profile.rows_processed == 100
        assert profile.execution_time_ms >= 10.0


class TestDataFrameProfiler:
    def test_profiler_creation(self):

        profiler = DataFrameProfiler(platform="polars")

        assert profiler.platform == "polars"
        assert len(profiler._profiles) == 0

    def test_profile_query_context_manager(self):

        profiler = DataFrameProfiler(platform="polars")

        with profiler.profile_query("q1") as ctx:
            ctx.set_rows(1000)
            time.sleep(0.01)

        profiles = profiler.get_profiles()
        assert len(profiles) == 1
        assert profiles[0].query_id == "q1"
        assert profiles[0].rows_processed == 1000

    def test_multiple_queries(self):

        profiler = DataFrameProfiler()

        for i in range(3):
            with profiler.profile_query(f"q{i + 1}") as ctx:
                ctx.set_rows(100 * (i + 1))

        profiles = profiler.get_profiles()
        assert len(profiles) == 3

    def test_get_profile_by_id(self):

        profiler = DataFrameProfiler()

        with profiler.profile_query("target_query") as ctx:
            ctx.set_rows(500)

        with profiler.profile_query("other_query") as ctx:
            ctx.set_rows(100)

        profile = profiler.get_profile("target_query")
        assert profile is not None
        assert profile.rows_processed == 500

    def test_get_nonexistent_profile(self):

        profiler = DataFrameProfiler()
        profile = profiler.get_profile("nonexistent")

        assert profile is None

    def test_get_statistics_empty(self):

        profiler = DataFrameProfiler()
        stats = profiler.get_statistics()

        assert stats["query_count"] == 0
        assert stats["total_execution_time_ms"] == 0

    def test_get_statistics(self):

        profiler = DataFrameProfiler(platform="polars")

        for i in range(3):
            profile = QueryExecutionProfile(
                query_id=f"q{i + 1}",
                execution_time_ms=100.0 * (i + 1),
                planning_time_ms=10.0,
                collect_time_ms=5.0,
                rows_processed=1000,
                lazy_evaluation=True,
            )
            profiler.add_profile(profile)

        stats = profiler.get_statistics()

        assert stats["query_count"] == 3
        assert stats["total_execution_time_ms"] == 600.0
        assert stats["avg_execution_time_ms"] == 200.0
        assert stats["min_execution_time_ms"] == 100.0
        assert stats["max_execution_time_ms"] == 300.0
        assert stats["lazy_evaluation_queries"] == 3
        assert stats["platform"] == "polars"

    def test_clear(self):

        profiler = DataFrameProfiler()

        with profiler.profile_query("q1"):
            pass

        assert len(profiler.get_profiles()) == 1

        profiler.clear()

        assert len(profiler.get_profiles()) == 0


class TestComparisonResult:
    def test_comparison_with_both_times(self):

        result = ComparisonResult(
            query_id="q1",
            dataframe_time_ms=100.0,
            sql_time_ms=200.0,
        )

        assert result.speedup == 2.0
        assert result.winner == "dataframe"

    def test_comparison_sql_faster(self):

        result = ComparisonResult(
            query_id="q1",
            dataframe_time_ms=200.0,
            sql_time_ms=100.0,
        )

        assert result.speedup == 0.5
        assert result.winner == "sql"

    def test_comparison_no_sql_time(self):

        result = ComparisonResult(
            query_id="q1",
            dataframe_time_ms=100.0,
        )

        assert result.sql_time_ms is None
        assert result.speedup is None
        assert result.winner == "unknown"


class TestCompareExecutionModes:
    def test_compare_with_matching_queries(self):

        df_profiles = [
            QueryExecutionProfile(
                query_id="q1",
                execution_time_ms=100.0,
                lazy_evaluation=True,
                planning_time_ms=5.0,
                collect_time_ms=5.0,
            ),
            QueryExecutionProfile(
                query_id="q2",
                execution_time_ms=200.0,
            ),
        ]

        sql_times = {
            "q1": 150.0,
            "q2": 100.0,
        }

        results = compare_execution_modes(df_profiles, sql_times)

        assert len(results) == 2

        assert results[0].query_id == "q1"
        assert results[0].winner == "dataframe"
        assert results[0].speedup == 1.5

        assert results[1].query_id == "q2"
        assert results[1].winner == "sql"
        assert results[1].speedup == 0.5

    def test_compare_with_missing_sql(self):

        df_profiles = [
            QueryExecutionProfile(query_id="q1", execution_time_ms=100.0),
            QueryExecutionProfile(query_id="q2", execution_time_ms=200.0),
        ]

        sql_times = {"q1": 150.0}

        results = compare_execution_modes(df_profiles, sql_times)

        assert len(results) == 2
        assert results[0].sql_time_ms == 150.0
        assert results[1].sql_time_ms is None
        assert results[1].winner == "unknown"

    def test_compare_with_high_lazy_overhead(self):

        df_profiles = [
            QueryExecutionProfile(
                query_id="q1",
                execution_time_ms=100.0,
                planning_time_ms=30.0,
                collect_time_ms=10.0,
                lazy_evaluation=True,
            ),
        ]

        sql_times = {"q1": 100.0}

        results = compare_execution_modes(df_profiles, sql_times)

        assert len(results) == 1
        assert any("overhead" in note.lower() for note in results[0].notes)


