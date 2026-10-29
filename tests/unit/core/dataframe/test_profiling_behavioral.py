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
    QueryExecutionProfile,
    QueryPlan,
    QueryProfileContext,
    _analyze_datafusion_plan,
    _analyze_polars_plan,
    _analyze_pyspark_plan,
    capture_datafusion_plan,
    capture_polars_plan,
    capture_pyspark_plan,
    capture_query_plan,
    compare_execution_modes,
    profile_query_execution,
    track_memory,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestAnalyzePolarisPlan:
    def test_detects_select_star(self):
        hints = _analyze_polars_plan("SCAN table selection: *")
        assert any("selecting only needed columns" in hint for hint in hints)

    def test_detects_multiple_filters(self):
        plan = "FILTER a > 1\nFILTER b > 2\nFILTER c > 3\nFILTER d > 4"
        hints = _analyze_polars_plan(plan)
        assert any("combining predicates" in hint for hint in hints)

    def test_detects_sort_without_limit(self):
        hints = _analyze_polars_plan("SORT by column_a ASC")
        assert any("limit" in hint.lower() for hint in hints)

    def test_no_hint_for_sort_with_limit(self):
        hints = _analyze_polars_plan("SORT by column_a ASC\nLIMIT 10")
        sort_hints = [h for h in hints if "limit" in h.lower() and "sort" in h.lower()]
        assert len(sort_hints) == 0

    def test_detects_cross_join(self):
        hints = _analyze_polars_plan("CROSS JOIN table_a, table_b")
        assert any("cross join" in hint.lower() for hint in hints)

    def test_detects_multiple_scans_without_cache(self):
        plan = "SCAN parquet file1\nFILTER\nSCAN parquet file2"
        hints = _analyze_polars_plan(plan)
        assert any("caching" in hint.lower() for hint in hints)

    def test_no_cache_hint_when_cache_present(self):
        plan = "SCAN parquet file1\nCACHE\nSCAN parquet file2"
        hints = _analyze_polars_plan(plan)
        cache_hints = [h for h in hints if "caching" in h.lower()]
        assert len(cache_hints) == 0

    def test_clean_plan_produces_no_hints(self):
        hints = _analyze_polars_plan("SELECT col_a, col_b FROM table LIMIT 100")
        assert len(hints) == 0


class TestAnalyzeDatafusionPlan:
    def test_detects_full_table_scan(self):
        hints = _analyze_datafusion_plan("tableScan some_table")
        assert any("full table scan" in hint.lower() for hint in hints)

    def test_no_hint_with_projection(self):
        hints = _analyze_datafusion_plan("tableScan some_table\nprojection: col_a, col_b")
        scan_hints = [h for h in hints if "full table scan" in h.lower()]
        assert len(scan_hints) == 0

    def test_detects_hash_join_without_build(self):
        hints = _analyze_datafusion_plan("HashJoin: left=orders, right=lineitem")
        assert any("hash join" in hint.lower() for hint in hints)

    def test_no_hint_hash_join_with_build(self):
        hints = _analyze_datafusion_plan("HashJoin: left=orders, right=lineitem Build: left")
        join_hints = [h for h in hints if "hash join" in h.lower()]
        assert len(join_hints) == 0


class TestAnalyzePysparkPlan:
    def test_detects_join_without_broadcast(self):
        hints = _analyze_pyspark_plan("SortMergeJoin [key], Inner")
        assert any("broadcast join" in hint.lower() for hint in hints)

    def test_no_broadcast_hint_with_broadcast(self):
        hints = _analyze_pyspark_plan("BroadcastExchange\nSortMergeJoin [key], Inner")
        broadcast_hints = [h for h in hints if "broadcast" in h.lower()]
        assert len(broadcast_hints) == 0

    def test_detects_shuffle(self):
        hints = _analyze_pyspark_plan("Exchange hashpartitioning\nShuffle write")
        assert any("shuffle" in hint.lower() for hint in hints)

    def test_detects_filescan_with_star(self):
        hints = _analyze_pyspark_plan("FileScan parquet [*]")
        assert any("column scan" in hint.lower() for hint in hints)


class TestCaptureQueryPlanDispatching:
    def test_polars_df_suffix_stripped(self):
        mock_lf = MagicMock()
        mock_lf.explain.return_value = "SCAN parquet"

        plan = capture_query_plan(mock_lf, "polars-df")
        assert plan is not None
        assert plan.platform == "polars"

    def test_datafusion_platform(self):
        mock_df = MagicMock()
        mock_df.logical_plan.return_value = "LogicalPlan: Scan"

        plan = capture_query_plan(mock_df, "datafusion")
        assert plan is not None
        assert plan.platform == "datafusion"

    def test_pyspark_platform(self):
        mock_df = MagicMock()
        mock_df.explain.side_effect = lambda extended=False: print("== Physical Plan ==\nScan parquet")

        plan = capture_query_plan(mock_df, "pyspark")
        assert plan is not None
        assert plan.platform == "pyspark"

    def test_unknown_platform_returns_none(self):
        result = capture_query_plan(MagicMock(), "unknown_platform")
        assert result is None

    def test_polars_without_explain_returns_none(self):
        mock_df = MagicMock(spec=[])
        result = capture_query_plan(mock_df, "polars")
        assert result is None


class TestCaptureDatafusionPlan:
    def test_uses_explain_when_no_logical_plan(self):
        mock_df = MagicMock(spec=["explain"])
        mock_df.explain.return_value = "Explain output"

        plan = capture_datafusion_plan(mock_df)
        assert plan.plan_text == "Explain output"

    def test_neither_method_available(self):
        mock_df = MagicMock(spec=[])

        with pytest.raises(TypeError, match="neither logical_plan nor explain"):
            capture_datafusion_plan(mock_df)


class TestCompareExecutionModesNotes:
    def test_dataframe_much_faster_note(self):
        profiles = [
            QueryExecutionProfile(query_id="Q1", execution_time_ms=50.0),
        ]
        sql_times = {"Q1": 200.0}

        results = compare_execution_modes(profiles, sql_times)
        assert len(results) == 1
        assert any("faster" in note.lower() and "DataFrame" in note for note in results[0].notes)

    def test_sql_much_faster_note(self):
        profiles = [
            QueryExecutionProfile(query_id="Q1", execution_time_ms=200.0),
        ]
        sql_times = {"Q1": 50.0}

        results = compare_execution_modes(profiles, sql_times)
        assert len(results) == 1
        assert any("faster" in note.lower() and "SQL" in note for note in results[0].notes)

    def test_high_lazy_overhead_note(self):
        profiles = [
            QueryExecutionProfile(
                query_id="Q1",
                execution_time_ms=100.0,
                planning_time_ms=15.0,
                collect_time_ms=10.0,
                lazy_evaluation=True,
            ),
        ]
        sql_times = {}

        results = compare_execution_modes(profiles, sql_times)
        assert len(results) == 1
        assert any("lazy evaluation overhead" in note.lower() for note in results[0].notes)

    def test_no_sql_time_for_query(self):
        profiles = [
            QueryExecutionProfile(query_id="Q1", execution_time_ms=100.0),
        ]
        sql_times = {}

        results = compare_execution_modes(profiles, sql_times)
        assert results[0].sql_time_ms is None
        assert results[0].winner == "unknown"


class TestComparisonResultEdgeCases:
    def test_zero_dataframe_time(self):
        result = ComparisonResult(
            query_id="Q1",
            dataframe_time_ms=0.0,
            sql_time_ms=100.0,
        )
        assert result.winner == "unknown" or result.speedup is not None

    def test_equal_times(self):
        result = ComparisonResult(
            query_id="Q1",
            dataframe_time_ms=100.0,
            sql_time_ms=100.0,
        )
        assert result.speedup == 1.0
        assert result.winner == "sql"


class TestProfilerLazyEvaluationStats:
    def test_statistics_with_lazy_evaluation(self):
        profiler = DataFrameProfiler(platform="polars")

        lazy_profile = QueryExecutionProfile(
            query_id="Q1",
            execution_time_ms=100.0,
            planning_time_ms=10.0,
            collect_time_ms=20.0,
            lazy_evaluation=True,
        )
        profiler.add_profile(lazy_profile)

        eager_profile = QueryExecutionProfile(
            query_id="Q2",
            execution_time_ms=50.0,
            lazy_evaluation=False,
        )
        profiler.add_profile(eager_profile)

        stats = profiler.get_statistics()
        assert stats["lazy_evaluation_queries"] == 1
        assert stats["avg_lazy_overhead_percent"] == 30.0

    def test_statistics_no_lazy_profiles(self):
        profiler = DataFrameProfiler()
        profiler.add_profile(QueryExecutionProfile(query_id="Q1", execution_time_ms=100.0, lazy_evaluation=False))

        stats = profiler.get_statistics()
        assert stats["lazy_evaluation_queries"] == 0
        assert stats["avg_lazy_overhead_percent"] == 0.0

    def test_get_profile_returns_none_for_missing(self):
        profiler = DataFrameProfiler()
        assert profiler.get_profile("nonexistent") is None

    def test_profiler_profiles_are_copies(self):
        profiler = DataFrameProfiler()
        profiler.add_profile(QueryExecutionProfile(query_id="Q1", execution_time_ms=100.0))

        profiles = profiler.get_profiles()
        profiles.clear()

        assert len(profiler.get_profiles()) == 1

    def test_statistics_planning_and_collect_times(self):
        profiler = DataFrameProfiler(platform="test")

        profiler.add_profile(
            QueryExecutionProfile(query_id="Q1", execution_time_ms=200.0, planning_time_ms=30.0, collect_time_ms=40.0)
        )
        profiler.add_profile(
            QueryExecutionProfile(query_id="Q2", execution_time_ms=100.0, planning_time_ms=10.0, collect_time_ms=20.0)
        )

        stats = profiler.get_statistics()
        assert stats["total_planning_time_ms"] == 40.0
        assert stats["avg_planning_time_ms"] == 20.0
        assert stats["total_collect_time_ms"] == 60.0
        assert stats["avg_collect_time_ms"] == 30.0


class TestMemoryTrackerEdgeCases:
    def test_double_start_is_idempotent(self):
        tracker = MemoryTracker(sample_interval_ms=50)
        tracker.start()
        tracker.start()

        peak = tracker.stop()
        assert peak >= 0

    def test_stop_before_start_returns_zero(self):
        tracker = MemoryTracker()
        peak = tracker.stop()
        assert peak == 0.0

    def test_peak_memory_delta(self):
        tracker = MemoryTracker(sample_interval_ms=10)
        tracker.start()
        time.sleep(0.03)
        tracker.stop()

        assert tracker.peak_memory_delta_mb >= 0.0


class TestTrackMemoryExceptionSafety:
    def test_tracker_stops_on_exception(self):
        with pytest.raises(ValueError, match="test error"):
            with track_memory(sample_interval_ms=10) as tracker:
                time.sleep(0.02)
                raise ValueError("test error")

        assert tracker.peak_memory_mb >= 0


class TestProfileQueryExecutionErrorPaths:
    def test_plan_capture_exception_is_swallowed(self):

        def query_fn():
            return [1, 2, 3]

        def bad_plan_fn(df):
            raise RuntimeError("plan capture failed")

        result, profile = profile_query_execution(
            query_id="Q1",
            platform="test",
            query_fn=query_fn,
            plan_capture_fn=bad_plan_fn,
            track_memory=False,
        )

        assert result == [1, 2, 3]
        assert profile.query_plan is None

    def test_row_count_exception_is_swallowed(self):

        def query_fn():
            return "result"

        def bad_row_count_fn(result):
            raise TypeError("cannot count rows")

        result, profile = profile_query_execution(
            query_id="Q1",
            platform="test",
            query_fn=query_fn,
            row_count_fn=bad_row_count_fn,
            track_memory=False,
        )

        assert result == "result"
        assert profile.rows_processed == 0

    def test_plan_capture_returning_none_is_handled(self):

        def query_fn():
            return [1]

        def null_plan_fn(df):
            return None

        result, profile = profile_query_execution(
            query_id="Q1",
            platform="test",
            query_fn=query_fn,
            plan_capture_fn=null_plan_fn,
            track_memory=False,
        )

        assert profile.query_plan is None


class TestProfiledExecutionResultProperties:
    def test_execution_time_seconds_conversion(self):
        profile = QueryExecutionProfile(query_id="Q1", execution_time_ms=2500.0)
        result = ProfiledExecutionResult(result=None, profile=profile)
        assert result.execution_time_seconds == 2.5

    def test_query_plan_property(self):
        plan = QueryPlan(platform="test", plan_type="logical", plan_text="PLAN")
        profile = QueryExecutionProfile(query_id="Q1", execution_time_ms=100.0, query_plan=plan)
        result = ProfiledExecutionResult(result=None, profile=profile)
        assert result.query_plan is plan
        assert result.query_plan.plan_text == "PLAN"

    def test_query_plan_none(self):
        profile = QueryExecutionProfile(query_id="Q1", execution_time_ms=100.0)
        result = ProfiledExecutionResult(result=None, profile=profile)
        assert result.query_plan is None


class TestQueryProfileContextEdgeCases:
    def test_end_planning_without_start(self):
        ctx = QueryProfileContext("Q1", "polars")
        ctx._start_time = time.perf_counter()

        ctx.end_planning()

        profile = ctx.get_profile()
        assert profile.planning_time_ms == 0.0
        assert profile.lazy_evaluation is False

    def test_end_collect_without_start(self):
        ctx = QueryProfileContext("Q1", "polars")
        ctx._start_time = time.perf_counter()

        ctx.end_collect()

        profile = ctx.get_profile()
        assert profile.collect_time_ms == 0.0


class TestQueryPlanData:
    def test_plan_data_dict(self):
        plan = QueryPlan(
            platform="polars",
            plan_type="optimized",
            plan_text="PLAN TEXT",
            plan_data={"optimized_plan": "opt", "logical_plan": "log"},
        )
        assert plan.plan_data is not None
        assert plan.plan_data["optimized_plan"] == "opt"
        assert plan.plan_data["logical_plan"] == "log"


@pytest.mark.parametrize(
    ("helper", "attrs"),
    [
        (capture_polars_plan, {"explain": RuntimeError("boom")}),
        (capture_datafusion_plan, {"logical_plan": RuntimeError("boom")}),
        (capture_pyspark_plan, {"explain": RuntimeError("boom")}),
    ],
)
def test_capture_helpers_propagate_failures(helper, attrs):
    frame = MagicMock()
    for name, exc in attrs.items():
        getattr(frame, name).side_effect = exc

    with pytest.raises(RuntimeError, match="boom"):
        helper(frame)
