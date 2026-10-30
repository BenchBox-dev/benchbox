# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    psutil = None

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_pyspark_stdout_lock = threading.Lock()


class ProfileMetricType(Enum):
    TIMING = "timing"
    MEMORY = "memory"
    ROWS = "rows"
    PLAN = "plan"


@dataclass
class QueryPlan:
    platform: str
    plan_type: str
    plan_text: str
    plan_data: dict[str, Any] | None = None
    optimization_hints: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return self.plan_text


@dataclass
class QueryExecutionProfile:
    query_id: str
    execution_time_ms: float
    planning_time_ms: float = 0.0
    collect_time_ms: float = 0.0
    plan_capture_time_ms: float = 0.0
    rows_processed: int = 0
    peak_memory_mb: float = 0.0
    query_plan: QueryPlan | None = None
    platform: str = ""
    lazy_evaluation: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def lazy_overhead_ms(self) -> float:
        return self.planning_time_ms + self.collect_time_ms

    @property
    def lazy_overhead_percent(self) -> float:
        if self.execution_time_ms <= 0:
            return 0.0
        return (self.lazy_overhead_ms / self.execution_time_ms) * 100


class QueryProfileContext:
    def __init__(self, query_id: str, platform: str = ""):
        self.query_id = query_id
        self.platform = platform
        self._start_time: float = 0.0
        self._planning_start: float = 0.0
        self._planning_time: float = 0.0
        self._collect_start: float = 0.0
        self._collect_time: float = 0.0
        self._plan_capture_start: float = 0.0
        self._plan_capture_time: float = 0.0
        self._rows: int = 0
        self._query_plan: QueryPlan | None = None
        self._peak_memory: float = 0.0
        self._lazy_evaluation: bool = False
        self._metrics: dict[str, Any] = {}

    def start_planning(self) -> None:
        self._planning_start = time.perf_counter()

    def end_planning(self) -> None:
        if self._planning_start > 0:
            self._planning_time = (time.perf_counter() - self._planning_start) * 1000
            self._lazy_evaluation = True

    def start_collect(self) -> None:
        self._collect_start = time.perf_counter()

    def end_collect(self) -> None:
        if self._collect_start > 0:
            self._collect_time = (time.perf_counter() - self._collect_start) * 1000

    def start_plan_capture(self) -> None:
        self._plan_capture_start = time.perf_counter()

    def end_plan_capture(self) -> None:
        if self._plan_capture_start > 0:
            self._plan_capture_time += (time.perf_counter() - self._plan_capture_start) * 1000
            self._plan_capture_start = 0.0

    def set_rows(self, rows: int) -> None:
        self._rows = rows

    def set_query_plan(self, plan: QueryPlan) -> None:
        self._query_plan = plan

    def set_peak_memory(self, memory_mb: float) -> None:
        self._peak_memory = memory_mb

    def add_metric(self, name: str, value: Any) -> None:
        self._metrics[name] = value

    def get_profile(self) -> QueryExecutionProfile:
        wall_ms = (time.perf_counter() - self._start_time) * 1000
        execution_time = max(0.0, wall_ms - self._plan_capture_time)

        return QueryExecutionProfile(
            query_id=self.query_id,
            execution_time_ms=execution_time,
            planning_time_ms=self._planning_time,
            collect_time_ms=self._collect_time,
            plan_capture_time_ms=self._plan_capture_time,
            rows_processed=self._rows,
            peak_memory_mb=self._peak_memory,
            query_plan=self._query_plan,
            platform=self.platform,
            lazy_evaluation=self._lazy_evaluation,
            metrics=self._metrics,
        )


class MemoryTracker:
    def __init__(self, sample_interval_ms: int = 50):
        self._sample_interval = sample_interval_ms / 1000.0
        self._running = False
        self._thread: threading.Thread | None = None
        self._samples: list[float] = []
        self._peak_memory: float = 0.0
        self._baseline_memory: float = 0.0
        self._lock = threading.Lock()

    @property
    def peak_memory_mb(self) -> float:
        with self._lock:
            return self._peak_memory

    @property
    def peak_memory_delta_mb(self) -> float:
        with self._lock:
            return max(0.0, self._peak_memory - self._baseline_memory)

    @property
    def samples(self) -> list[float]:
        with self._lock:
            return self._samples.copy()

    def start(self) -> None:
        if not PSUTIL_AVAILABLE:
            logger.debug("psutil not available - memory tracking disabled")
            return

        with self._lock:
            if self._running:
                return

            self._baseline_memory = self._get_current_memory()
            self._peak_memory = self._baseline_memory
            self._samples = [self._baseline_memory]
            self._running = True

        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()

    def stop(self) -> float:
        with self._lock:
            if not self._running:
                return self._peak_memory
            self._running = False
            thread = self._thread
            self._thread = None
            peak = self._peak_memory

        if thread is not None:
            try:
                thread.join(timeout=1.0)
            except Exception as e:
                logger.warning(f"Error joining memory tracker thread: {e}")

        return peak

    def _sample_loop(self) -> None:
        while True:
            with self._lock:
                if not self._running:
                    break

            current = self._get_current_memory()

            with self._lock:
                if not self._running:
                    break
                self._samples.append(current)
                if current > self._peak_memory:
                    self._peak_memory = current

            time.sleep(self._sample_interval)

    def _get_current_memory(self) -> float:
        if not PSUTIL_AVAILABLE:
            return 0.0

        try:
            process = psutil.Process(os.getpid())
            return process.memory_info().rss / (1024 * 1024)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as e:
            logger.debug(f"Cannot access process memory: {e}")
            return 0.0
        except OSError as e:
            logger.debug(f"OS error reading process memory: {e}")
            return 0.0

    def get_statistics(self) -> dict[str, float]:
        with self._lock:
            samples = self._samples.copy()
            baseline = self._baseline_memory
            peak = self._peak_memory

        if not samples:
            return {
                "baseline_mb": 0.0,
                "peak_mb": 0.0,
                "peak_delta_mb": 0.0,
                "avg_mb": 0.0,
                "sample_count": 0,
            }

        return {
            "baseline_mb": baseline,
            "peak_mb": peak,
            "peak_delta_mb": max(0.0, peak - baseline),
            "avg_mb": sum(samples) / len(samples),
            "sample_count": len(samples),
        }


def get_current_memory_mb() -> float:
    if not PSUTIL_AVAILABLE:
        return 0.0

    try:
        process = psutil.Process(os.getpid())
        return process.memory_info().rss / (1024 * 1024)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as e:
        logger.debug(f"Cannot access process memory: {e}")
        return 0.0
    except OSError as e:
        logger.debug(f"OS error reading process memory: {e}")
        return 0.0


@contextmanager
def track_memory(sample_interval_ms: int = 50) -> Generator[MemoryTracker, None, None]:
    tracker = MemoryTracker(sample_interval_ms=sample_interval_ms)
    tracker.start()
    try:
        yield tracker
    finally:
        try:
            tracker.stop()
        except Exception as e:
            logger.error(f"Error stopping memory tracker: {e}")


class DataFrameProfiler:
    def __init__(self, platform: str = ""):
        self.platform = platform
        self._profiles: list[QueryExecutionProfile] = []

    @contextmanager
    def profile_query(self, query_id: str) -> Generator[QueryProfileContext, None, None]:
        ctx = QueryProfileContext(query_id, self.platform)
        ctx._start_time = time.perf_counter()

        try:
            yield ctx
        finally:
            profile = ctx.get_profile()
            self._profiles.append(profile)

    def add_profile(self, profile: QueryExecutionProfile) -> None:
        self._profiles.append(profile)

    def get_profiles(self) -> list[QueryExecutionProfile]:
        return self._profiles.copy()

    def get_profile(self, query_id: str) -> QueryExecutionProfile | None:
        for profile in self._profiles:
            if profile.query_id == query_id:
                return profile
        return None

    def get_statistics(self) -> dict[str, Any]:
        if not self._profiles:
            return {
                "query_count": 0,
                "total_execution_time_ms": 0,
                "avg_execution_time_ms": 0,
                "min_execution_time_ms": 0,
                "max_execution_time_ms": 0,
            }

        execution_times = [p.execution_time_ms for p in self._profiles]
        planning_times = [p.planning_time_ms for p in self._profiles]
        collect_times = [p.collect_time_ms for p in self._profiles]
        rows = [p.rows_processed for p in self._profiles]

        lazy_profiles = [p for p in self._profiles if p.lazy_evaluation]
        avg_lazy_overhead = 0.0
        if lazy_profiles:
            overheads = [p.lazy_overhead_percent for p in lazy_profiles]
            avg_lazy_overhead = sum(overheads) / len(overheads)

        return {
            "query_count": len(self._profiles),
            "total_execution_time_ms": sum(execution_times),
            "avg_execution_time_ms": sum(execution_times) / len(execution_times),
            "min_execution_time_ms": min(execution_times),
            "max_execution_time_ms": max(execution_times),
            "total_planning_time_ms": sum(planning_times),
            "avg_planning_time_ms": sum(planning_times) / len(planning_times) if planning_times else 0,
            "total_collect_time_ms": sum(collect_times),
            "avg_collect_time_ms": sum(collect_times) / len(collect_times) if collect_times else 0,
            "total_rows_processed": sum(rows),
            "avg_rows_per_query": sum(rows) / len(rows) if rows else 0,
            "lazy_evaluation_queries": len(lazy_profiles),
            "avg_lazy_overhead_percent": avg_lazy_overhead,
            "platform": self.platform,
        }

    def clear(self) -> None:
        self._profiles.clear()


def capture_polars_plan(lazy_frame: Any) -> QueryPlan:
    optimized_plan = lazy_frame.explain(optimized=True)

    logical_plan = lazy_frame.explain(optimized=False)

    hints = _analyze_polars_plan(optimized_plan)

    return QueryPlan(
        platform="polars",
        plan_type="optimized",
        plan_text=optimized_plan,
        plan_data={
            "optimized_plan": optimized_plan,
            "logical_plan": logical_plan,
        },
        optimization_hints=hints,
    )


def capture_datafusion_plan(df: Any) -> QueryPlan:
    if hasattr(df, "logical_plan"):
        plan = str(df.logical_plan())
    elif hasattr(df, "explain"):
        plan = df.explain()
    else:
        raise TypeError(f"{type(df).__name__} exposes neither logical_plan nor explain")

    hints = _analyze_datafusion_plan(plan)

    return QueryPlan(
        platform="datafusion",
        plan_type="logical",
        plan_text=plan,
        optimization_hints=hints,
    )


def capture_pyspark_plan(df: Any, platform: str = "pyspark") -> QueryPlan:
    import io
    import sys

    with _pyspark_stdout_lock:
        old_stdout = sys.stdout
        sys.stdout = buffer = io.StringIO()

        try:
            df.explain(extended=True)
            plan = buffer.getvalue()
        finally:
            sys.stdout = old_stdout

    hints = _analyze_pyspark_plan(plan)

    return QueryPlan(
        platform=platform,
        plan_type="extended",
        plan_text=plan,
        optimization_hints=hints,
    )


class PlanCaptureSupport(str, Enum):
    DAG = "dag"
    RAW_TEXT = "raw_text"
    UNSUPPORTED = "unsupported"


_PLAN_CAPTURE_SUPPORT: dict[str, PlanCaptureSupport] = {
    "datafusion": PlanCaptureSupport.DAG,
    "pyspark": PlanCaptureSupport.DAG,
    "lakesail": PlanCaptureSupport.DAG,
    "databricks": PlanCaptureSupport.DAG,
    "polars": PlanCaptureSupport.RAW_TEXT,
    "pandas": PlanCaptureSupport.UNSUPPORTED,
    "dask": PlanCaptureSupport.UNSUPPORTED,
    "cudf": PlanCaptureSupport.UNSUPPORTED,
}

_SPARK_FAMILY_PLATFORMS = frozenset({"pyspark", "lakesail", "databricks"})


def normalize_dataframe_platform(platform: str) -> str:
    return platform.lower().removesuffix("-df")


def plan_capture_support(platform: str) -> PlanCaptureSupport:
    return _PLAN_CAPTURE_SUPPORT.get(normalize_dataframe_platform(platform), PlanCaptureSupport.UNSUPPORTED)


def capture_query_plan(df: Any, platform: str) -> QueryPlan | None:
    platform_lower = normalize_dataframe_platform(platform)

    if platform_lower == "polars":
        if hasattr(df, "explain"):
            return capture_polars_plan(df)
    elif platform_lower == "datafusion":
        return capture_datafusion_plan(df)
    elif platform_lower in _SPARK_FAMILY_PLATFORMS:
        return capture_pyspark_plan(df, platform=platform_lower)

    return None


def _analyze_polars_plan(plan: str) -> list[str]:
    hints = []

    plan_lower = plan.lower()

    if "select *" in plan_lower or "selection: *" in plan_lower:
        hints.append("Consider selecting only needed columns to reduce memory usage")

    if plan_lower.count("filter") > 3:
        hints.append("Multiple filter operations - consider combining predicates")

    if "sort" in plan_lower and "limit" not in plan_lower:
        hints.append("Sorting without limit may be expensive - add limit if only top N needed")

    if "cross join" in plan_lower:
        hints.append("Cross join detected - ensure this is intentional")

    if "cache" not in plan_lower and plan_lower.count("scan") > 1:
        hints.append("Multiple scans of same data - consider caching intermediate results")

    return hints


def _analyze_datafusion_plan(plan: str) -> list[str]:
    hints = []
    plan_lower = plan.lower()

    if "tablescan" in plan_lower and "projection" not in plan_lower:
        hints.append("Full table scan without projection - select specific columns")

    if "HashJoin" in plan and "Build" not in plan:
        hints.append("Hash join detected - ensure smaller table is on build side")

    return hints


def _analyze_pyspark_plan(plan: str) -> list[str]:
    hints = []
    plan_lower = plan.lower()

    if "broadcastexchange" not in plan_lower and "join" in plan_lower:
        hints.append("Consider broadcast join for small dimension tables")

    if "shuffle" in plan_lower:
        hints.append("Shuffle operations detected - may benefit from partitioning strategy")

    if "filescan" in plan_lower and "*" in plan:
        hints.append("Full column scan detected - select only needed columns")

    return hints


@dataclass
class ComparisonResult:
    query_id: str
    dataframe_time_ms: float
    sql_time_ms: float | None = None
    speedup: float | None = None
    winner: str = "unknown"
    notes: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.sql_time_ms is not None and self.dataframe_time_ms > 0:
            self.speedup = self.sql_time_ms / self.dataframe_time_ms
            self.winner = "dataframe" if self.speedup > 1 else "sql"


def compare_execution_modes(
    df_profiles: list[QueryExecutionProfile],
    sql_times: dict[str, float],
) -> list[ComparisonResult]:
    results = []

    for profile in df_profiles:
        sql_time = sql_times.get(profile.query_id)

        result = ComparisonResult(
            query_id=profile.query_id,
            dataframe_time_ms=profile.execution_time_ms,
            sql_time_ms=sql_time,
        )

        if sql_time is not None:
            if result.speedup and result.speedup > 2:
                result.notes.append(f"DataFrame is {result.speedup:.1f}x faster")
            elif result.speedup and result.speedup < 0.5:
                result.notes.append(f"SQL is {1 / result.speedup:.1f}x faster")

        if profile.lazy_overhead_percent > 20:
            result.notes.append(f"High lazy evaluation overhead: {profile.lazy_overhead_percent:.1f}%")

        results.append(result)

    return results


def profile_query_execution(
    query_id: str,
    platform: str,
    query_fn: Callable[[], Any],
    collect_fn: Callable[[Any], Any] | None = None,
    row_count_fn: Callable[[Any], int] | None = None,
    plan_capture_fn: Callable[[Any], QueryPlan | None] | None = None,
    track_memory: bool = True,
    memory_sample_interval_ms: int = 50,
) -> tuple[Any, QueryExecutionProfile]:
    ctx = QueryProfileContext(query_id, platform)
    ctx._start_time = time.perf_counter()
    memory_tracker: MemoryTracker | None = None

    if track_memory:
        memory_tracker = MemoryTracker(sample_interval_ms=memory_sample_interval_ms)
        memory_tracker.start()

    try:
        ctx.start_planning()
        lazy_result = query_fn()
        ctx.end_planning()

        query_plan = None
        if plan_capture_fn is not None:
            ctx.start_plan_capture()
            try:
                query_plan = plan_capture_fn(lazy_result)
                if query_plan:
                    ctx.set_query_plan(query_plan)
            except Exception as e:
                logger.debug(f"Plan capture failed: {e}")
            finally:
                ctx.end_plan_capture()

        if collect_fn is not None:
            ctx.start_collect()
            result = collect_fn(lazy_result)
            ctx.end_collect()
        else:
            result = lazy_result

        if row_count_fn is not None:
            try:
                rows = row_count_fn(result)
                ctx.set_rows(rows)
            except Exception as e:
                logger.debug(f"Row count failed: {e}")

    finally:
        if memory_tracker is not None:
            peak_memory = memory_tracker.stop()
            ctx.set_peak_memory(peak_memory)

            stats = memory_tracker.get_statistics()
            ctx.add_metric("memory_baseline_mb", stats["baseline_mb"])
            ctx.add_metric("memory_delta_mb", stats["peak_delta_mb"])
            ctx.add_metric("memory_samples", stats["sample_count"])

    profile = ctx.get_profile()
    return result, profile


@dataclass
class ProfiledExecutionResult:
    result: Any
    profile: QueryExecutionProfile

    @property
    def execution_time_ms(self) -> float:
        return self.profile.execution_time_ms

    @property
    def execution_time_seconds(self) -> float:
        return self.profile.execution_time_ms / 1000.0

    @property
    def rows_returned(self) -> int:
        return self.profile.rows_processed

    @property
    def peak_memory_mb(self) -> float:
        return self.profile.peak_memory_mb

    @property
    def query_plan(self) -> QueryPlan | None:
        return self.profile.query_plan
