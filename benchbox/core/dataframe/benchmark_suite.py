# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import logging
import math
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchbox.core.dataframe.profiling import (
    MemoryTracker,
    PlanCaptureSupport,
    capture_query_plan,
    plan_capture_support,
)
from benchbox.core.tpch.dataframe_queries import get_tpch_dataframe_queries
from benchbox.utils.path_utils import get_benchmark_runs_datagen_path

if TYPE_CHECKING:
    from benchbox.core.dataframe.context import DataFrameContext

logger = logging.getLogger(__name__)


def resolve_table_paths(parquet_dir: Path, table: str) -> list[Path]:
    single = parquet_dir / f"{table}.parquet"
    shards: list[tuple[int, Path]] = []
    for candidate in parquet_dir.glob(f"{table}.*.parquet"):
        try:
            suffix = candidate.stem.rsplit(".", 1)[1]
        except IndexError:
            continue
        if not suffix.isdigit():
            continue
        shards.append((int(suffix), candidate))
    if single.exists():
        if shards:
            logger.warning(
                "Both %s and %d numerically-suffixed shard(s) exist; using the single file",
                single,
                len(shards),
            )
        return [single]
    if not shards:
        logger.warning("No parquet files found for table %s under %s", table, parquet_dir)
    return [path for _, path in sorted(shards)]


class PlatformCategory(Enum):
    SINGLE_NODE = "single_node"
    GPU_ACCELERATED = "gpu_accelerated"
    DISTRIBUTED = "distributed"
    MEMORY_EFFICIENT = "memory_efficient"


@dataclass
class PlatformCapability:
    platform_name: str
    family: str
    category: PlatformCategory
    supports_lazy: bool = False
    supports_streaming: bool = False
    supports_gpu: bool = False
    supports_distributed: bool = False
    memory_notes: str = ""


PLATFORM_CAPABILITIES: dict[str, PlatformCapability] = {
    "polars-df": PlatformCapability(
        platform_name="polars-df",
        family="expression",
        category=PlatformCategory.SINGLE_NODE,
        supports_lazy=True,
        supports_streaming=True,
        memory_notes="Memory-efficient columnar format, lazy evaluation reduces memory pressure",
    ),
    "pandas-df": PlatformCapability(
        platform_name="pandas-df",
        family="pandas",
        category=PlatformCategory.SINGLE_NODE,
        supports_lazy=False,
        memory_notes="In-memory operations, requires full dataset in RAM",
    ),
    "datafusion-df": PlatformCapability(
        platform_name="datafusion-df",
        family="expression",
        category=PlatformCategory.SINGLE_NODE,
        supports_lazy=True,
        supports_streaming=True,
        memory_notes="Arrow-based, supports spilling to disk for large datasets",
    ),
    "pyspark-df": PlatformCapability(
        platform_name="pyspark-df",
        family="expression",
        category=PlatformCategory.DISTRIBUTED,
        supports_lazy=True,
        supports_distributed=True,
        memory_notes="Distributed execution, configurable memory management",
    ),
    "cudf-df": PlatformCapability(
        platform_name="cudf-df",
        family="pandas",
        category=PlatformCategory.GPU_ACCELERATED,
        supports_gpu=True,
        memory_notes="GPU memory bound, best for datasets that fit in VRAM",
    ),
    "dask-df": PlatformCapability(
        platform_name="dask-df",
        family="pandas",
        category=PlatformCategory.DISTRIBUTED,
        supports_lazy=True,
        supports_distributed=True,
        memory_notes="Partitioned dataframes, scales beyond memory",
    ),
}


@dataclass
class BenchmarkConfig:
    scale_factor: float = 0.01
    query_ids: list[str] | None = None
    warmup_iterations: int = 1
    benchmark_iterations: int = 3
    track_memory: bool = True
    capture_plans: bool = True
    memory_sample_interval_ms: int = 50
    timeout_seconds: float = 300.0


@dataclass
class QueryBenchmarkResult:
    query_id: str
    platform: str
    iterations: int
    execution_times_ms: list[float] = field(default_factory=list)
    memory_peak_mb: float = 0.0
    rows_returned: int = 0
    status: str = "SUCCESS"
    error_message: str | None = None
    query_plan: str | None = None

    @property
    def mean_time_ms(self) -> float:
        if not self.execution_times_ms:
            return 0.0
        return statistics.mean(self.execution_times_ms)

    @property
    def std_time_ms(self) -> float:
        if len(self.execution_times_ms) < 2:
            return 0.0
        return statistics.stdev(self.execution_times_ms)

    @property
    def min_time_ms(self) -> float:
        if not self.execution_times_ms:
            return 0.0
        return min(self.execution_times_ms)

    @property
    def max_time_ms(self) -> float:
        if not self.execution_times_ms:
            return 0.0
        return max(self.execution_times_ms)

    @property
    def p50_time_ms(self) -> float:
        if not self.execution_times_ms:
            return 0.0
        return statistics.median(self.execution_times_ms)

    @property
    def p95_time_ms(self) -> float:
        if len(self.execution_times_ms) < 2:
            return self.max_time_ms
        sorted_times = sorted(self.execution_times_ms)
        idx = int(len(sorted_times) * 0.95)
        return sorted_times[min(idx, len(sorted_times) - 1)]

    @property
    def coefficient_of_variation(self) -> float:
        if not self.execution_times_ms or self.mean_time_ms == 0:
            return 0.0
        return (self.std_time_ms / self.mean_time_ms) * 100

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "platform": self.platform,
            "iterations": self.iterations,
            "execution_times_ms": self.execution_times_ms,
            "mean_time_ms": self.mean_time_ms,
            "std_time_ms": self.std_time_ms,
            "min_time_ms": self.min_time_ms,
            "max_time_ms": self.max_time_ms,
            "p50_time_ms": self.p50_time_ms,
            "p95_time_ms": self.p95_time_ms,
            "cv_percent": self.coefficient_of_variation,
            "memory_peak_mb": self.memory_peak_mb,
            "rows_returned": self.rows_returned,
            "status": self.status,
            "error_message": self.error_message,
            "query_plan": self.query_plan,
        }


@dataclass
class PlatformBenchmarkResult:
    platform: str
    capability: PlatformCapability | None
    config: BenchmarkConfig
    query_results: list[QueryBenchmarkResult] = field(default_factory=list)
    total_time_ms: float = 0.0
    geometric_mean_ms: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        self._calculate_aggregates()

    def _calculate_aggregates(self) -> None:
        successful_results = [r for r in self.query_results if r.status == "SUCCESS"]

        if successful_results:
            mean_times = [r.mean_time_ms for r in successful_results if r.mean_time_ms > 0]
            self.total_time_ms = sum(mean_times)

            if mean_times:
                log_sum = sum(math.log(t) for t in mean_times if t > 0)
                self.geometric_mean_ms = math.exp(log_sum / len(mean_times))

    @property
    def successful_queries(self) -> int:
        return sum(1 for r in self.query_results if r.status == "SUCCESS")

    @property
    def failed_queries(self) -> int:
        return sum(1 for r in self.query_results if r.status == "ERROR")

    @property
    def success_rate(self) -> float:
        total = len(self.query_results)
        if total == 0:
            return 0.0
        return (self.successful_queries / total) * 100

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "capability": {
                "family": self.capability.family if self.capability else None,
                "category": self.capability.category.value if self.capability else None,
                "supports_lazy": self.capability.supports_lazy if self.capability else False,
                "supports_gpu": self.capability.supports_gpu if self.capability else False,
                "supports_distributed": self.capability.supports_distributed if self.capability else False,
            },
            "config": {
                "scale_factor": self.config.scale_factor,
                "query_ids": self.config.query_ids,
                "warmup_iterations": self.config.warmup_iterations,
                "benchmark_iterations": self.config.benchmark_iterations,
            },
            "query_results": [r.to_dict() for r in self.query_results],
            "summary": {
                "total_time_ms": self.total_time_ms,
                "geometric_mean_ms": self.geometric_mean_ms,
                "successful_queries": self.successful_queries,
                "failed_queries": self.failed_queries,
                "success_rate": self.success_rate,
            },
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ComparisonSummary:
    platforms: list[str]
    fastest_platform: str
    slowest_platform: str
    speedup_matrix: dict[str, dict[str, float]]
    query_winners: dict[str, str]
    total_queries: int
    scale_factor: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "platforms": self.platforms,
            "fastest_platform": self.fastest_platform,
            "slowest_platform": self.slowest_platform,
            "speedup_matrix": self.speedup_matrix,
            "query_winners": self.query_winners,
            "total_queries": self.total_queries,
            "scale_factor": self.scale_factor,
        }


class DataFrameBenchmarkSuite:
    def __init__(self, config: BenchmarkConfig | None = None):
        self.config = config or BenchmarkConfig()
        self._query_registry = get_tpch_dataframe_queries()

    def get_available_platforms(self) -> list[str]:
        from benchbox.platforms import list_available_dataframe_platforms

        available = list_available_dataframe_platforms()
        return [name for name, is_available in available.items() if is_available]

    def get_platform_capability(self, platform_name: str) -> PlatformCapability | None:
        return PLATFORM_CAPABILITIES.get(platform_name)

    def get_platforms_by_category(self, category: PlatformCategory) -> list[str]:
        available = self.get_available_platforms()
        return [
            name
            for name in available
            if name in PLATFORM_CAPABILITIES and PLATFORM_CAPABILITIES[name].category == category
        ]

    def run_comparison(
        self,
        platforms: list[str],
        data_dir: str | Path,
    ) -> list[PlatformBenchmarkResult]:
        data_path = Path(data_dir)
        if not data_path.exists():
            raise ValueError(f"Data directory not found: {data_dir}")

        results = []
        for platform_name in platforms:
            logger.info(f"Benchmarking platform: {platform_name}")
            try:
                result = self._benchmark_platform(platform_name, data_path)
                results.append(result)
            except Exception as e:
                logger.error(f"Failed to benchmark {platform_name}: {e}")
                results.append(
                    PlatformBenchmarkResult(
                        platform=platform_name,
                        capability=self.get_platform_capability(platform_name),
                        config=self.config,
                        query_results=[
                            QueryBenchmarkResult(
                                query_id="ALL",
                                platform=platform_name,
                                iterations=0,
                                status="ERROR",
                                error_message=str(e),
                            )
                        ],
                    )
                )

        return results

    def _benchmark_platform(
        self,
        platform_name: str,
        data_dir: Path,
    ) -> PlatformBenchmarkResult:
        context = self._create_context(platform_name, data_dir)
        capability = self.get_platform_capability(platform_name)
        family = capability.family if capability else "expression"

        query_ids = self.config.query_ids or self._query_registry.get_query_ids()
        query_results = []

        for query_id in query_ids:
            logger.info(f"  Running {query_id}...")
            result = self._benchmark_query(query_id, context, family, platform_name)
            query_results.append(result)

        return PlatformBenchmarkResult(
            platform=platform_name,
            capability=capability,
            config=self.config,
            query_results=query_results,
        )

    def _create_context(self, platform_name: str, data_dir: Path) -> DataFrameContext:
        from benchbox.platforms import get_dataframe_adapter

        parquet_dir = data_dir / "parquet"
        if not parquet_dir.exists():
            parquet_dir = data_dir

        tables = ["lineitem", "orders", "customer", "supplier", "part", "partsupp", "nation", "region"]

        adapter = get_dataframe_adapter(platform_name, working_dir=str(data_dir))
        ctx = adapter.create_context()

        for table in tables:
            table_paths = resolve_table_paths(parquet_dir, table)
            if table_paths:
                adapter.load_table(ctx, table, table_paths)

        return ctx

    def _benchmark_query(
        self,
        query_id: str,
        context: DataFrameContext,
        family: str,
        platform_name: str,
    ) -> QueryBenchmarkResult:
        query = self._query_registry.get(query_id)
        if query is None:
            return QueryBenchmarkResult(
                query_id=query_id,
                platform=platform_name,
                iterations=0,
                status="ERROR",
                error_message=f"Query {query_id} not found",
            )

        impl = query.get_impl_for_family(family)
        if impl is None:
            return QueryBenchmarkResult(
                query_id=query_id,
                platform=platform_name,
                iterations=0,
                status="ERROR",
                error_message=f"No {family} implementation for {query_id}",
            )

        execution_times = []
        peak_memory = 0.0
        rows_returned = 0
        query_plan = None

        try:
            for _ in range(self.config.warmup_iterations):
                result = query.execute(context, family)
                if hasattr(result, "collect"):
                    result = result.collect()
                del result

            for i in range(self.config.benchmark_iterations):
                if self.config.track_memory:
                    tracker = MemoryTracker(sample_interval_ms=self.config.memory_sample_interval_ms)
                    tracker.start()

                start_time = time.perf_counter()
                result = query.execute(context, family)

                if hasattr(result, "collect"):
                    result = result.collect()

                elapsed_ms = (time.perf_counter() - start_time) * 1000
                execution_times.append(elapsed_ms)

                if self.config.track_memory:
                    peak = tracker.stop()
                    peak_memory = max(peak_memory, peak)

                if i == 0:
                    try:
                        rows_returned = len(result)
                    except Exception:
                        rows_returned = 0

                del result

            if self.config.capture_plans and plan_capture_support(platform_name) is not PlanCaptureSupport.UNSUPPORTED:
                plan_frame = None
                try:
                    plan_frame = query.execute(context, family)
                    plan = capture_query_plan(plan_frame, platform_name)
                    if plan is not None and plan.plan_type != "error":
                        query_plan = plan.plan_text
                except Exception as e:
                    logger.debug(f"Could not capture DataFrame plan for {query_id}: {e}")
                finally:
                    del plan_frame

            return QueryBenchmarkResult(
                query_id=query_id,
                platform=platform_name,
                iterations=len(execution_times),
                execution_times_ms=execution_times,
                memory_peak_mb=peak_memory,
                rows_returned=rows_returned,
                status="SUCCESS",
                query_plan=query_plan,
            )

        except Exception as e:
            logger.error(f"Error running {query_id} on {platform_name}: {e}")
            return QueryBenchmarkResult(
                query_id=query_id,
                platform=platform_name,
                iterations=len(execution_times),
                execution_times_ms=execution_times,
                status="ERROR",
                error_message=str(e),
            )

    def _build_speedup_matrix(self, platforms: list[str], geomeans: dict[str, float]) -> dict[str, dict[str, float]]:
        speedup_matrix: dict[str, dict[str, float]] = {}
        for p1 in platforms:
            speedup_matrix[p1] = {}
            for p2 in platforms:
                if p1 in geomeans and p2 in geomeans and geomeans[p1] > 0:
                    speedup_matrix[p1][p2] = geomeans[p2] / geomeans[p1]
                else:
                    speedup_matrix[p1][p2] = 1.0
        return speedup_matrix

    def _find_query_winners(self, results: list[PlatformBenchmarkResult]) -> dict[str, str]:
        all_query_ids: set[str] = set()
        for result in results:
            for qr in result.query_results:
                all_query_ids.add(qr.query_id)

        query_winners: dict[str, str] = {}
        for query_id in all_query_ids:
            best_time = float("inf")
            best_platform = ""
            for result in results:
                for qr in result.query_results:
                    if qr.query_id == query_id and qr.status == "SUCCESS" and qr.mean_time_ms < best_time:
                        best_time = qr.mean_time_ms
                        best_platform = result.platform
            if best_platform:
                query_winners[query_id] = best_platform
        return query_winners

    def get_summary(self, results: list[PlatformBenchmarkResult]) -> ComparisonSummary:
        if not results:
            raise ValueError("No results to summarize")

        platforms = [r.platform for r in results]
        geomeans = {r.platform: r.geometric_mean_ms for r in results if r.geometric_mean_ms > 0}

        fastest = min(geomeans, key=geomeans.get) if geomeans else platforms[0]
        slowest = max(geomeans, key=geomeans.get) if geomeans else platforms[0]

        speedup_matrix = self._build_speedup_matrix(platforms, geomeans)
        query_winners = self._find_query_winners(results)
        all_query_ids = {qr.query_id for r in results for qr in r.query_results}

        return ComparisonSummary(
            platforms=platforms,
            fastest_platform=fastest,
            slowest_platform=slowest,
            speedup_matrix=speedup_matrix,
            query_winners=query_winners,
            total_queries=len(all_query_ids),
            scale_factor=self.config.scale_factor,
        )

    def export_results(
        self,
        results: list[PlatformBenchmarkResult],
        output_path: str | Path,
        format: str = "json",
    ) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if format == "json":
            data = {
                "benchmark_suite": "dataframe_comparison",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "config": {
                    "scale_factor": self.config.scale_factor,
                    "query_ids": self.config.query_ids,
                    "warmup_iterations": self.config.warmup_iterations,
                    "benchmark_iterations": self.config.benchmark_iterations,
                },
                "results": [r.to_dict() for r in results],
                "summary": self.get_summary(results).to_dict() if results else None,
            }
            output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        elif format == "markdown":
            md_content = self._generate_markdown_report(results)
            output_path.write_text(md_content, encoding="utf-8")

        else:
            raise ValueError(f"Unsupported export format: {format}")

        return output_path

    def _generate_markdown_report(self, results: list[PlatformBenchmarkResult]) -> str:
        lines = []
        summary = self.get_summary(results)

        lines.append("# DataFrame Cross-Platform Benchmark Report")
        lines.append("")
        lines.append(f"**Scale Factor:** {self.config.scale_factor}")
        lines.append(f"**Benchmark Iterations:** {self.config.benchmark_iterations}")
        lines.append(f"**Total Queries:** {summary.total_queries}")
        lines.append(f"**Generated:** {datetime.now(timezone.utc).isoformat()}")
        lines.append("")

        lines.append("## Performance Summary")
        lines.append("")
        lines.append(f"**Fastest Platform:** {summary.fastest_platform}")
        lines.append(f"**Slowest Platform:** {summary.slowest_platform}")
        lines.append("")

        lines.append("### Geometric Mean Comparison")
        lines.append("")
        lines.append("| Platform | Geomean (ms) | Total Time (ms) | Success Rate |")
        lines.append("|----------|--------------|-----------------|--------------|")

        for result in sorted(results, key=lambda r: r.geometric_mean_ms or float("inf")):
            geomean = f"{result.geometric_mean_ms:.2f}" if result.geometric_mean_ms else "N/A"
            total = f"{result.total_time_ms:.2f}" if result.total_time_ms else "N/A"
            success = f"{result.success_rate:.1f}%"
            lines.append(f"| {result.platform} | {geomean} | {total} | {success} |")

        lines.append("")

        lines.append("## Query-by-Query Results")
        lines.append("")

        query_ids = sorted(summary.query_winners.keys())
        if query_ids:
            header = "| Query | " + " | ".join(r.platform for r in results) + " | Winner |"
            sep = "|-------|" + "|".join("-" * (len(r.platform) + 2) for r in results) + "|--------|"
            lines.append(header)
            lines.append(sep)

            for query_id in query_ids:
                row = f"| {query_id} |"
                for result in results:
                    qr = next((q for q in result.query_results if q.query_id == query_id), None)
                    if qr and qr.status == "SUCCESS":
                        row += f" {qr.mean_time_ms:.2f}ms |"
                    else:
                        row += " ERROR |"
                winner = summary.query_winners.get(query_id, "N/A")
                row += f" {winner} |"
                lines.append(row)

        lines.append("")

        if len(results) > 1:
            lines.append("## Speedup Matrix")
            lines.append("")
            lines.append("Shows how much faster the row platform is compared to the column platform.")
            lines.append("")

            header = "| | " + " | ".join(r.platform for r in results) + " |"
            sep = "|--|" + "|".join("--" for _ in results) + "|"
            lines.append(header)
            lines.append(sep)

            for r1 in results:
                row = f"| {r1.platform} |"
                for r2 in results:
                    speedup = summary.speedup_matrix.get(r1.platform, {}).get(r2.platform, 1.0)
                    row += f" {speedup:.2f}x |"
                lines.append(row)

        return "\n".join(lines)


@dataclass
class SQLComparisonResult:
    query_id: str
    sql_platform: str
    df_platform: str
    sql_time_ms: float = 0.0
    df_time_ms: float = 0.0
    speedup: float = 1.0
    sql_rows: int = 0
    df_rows: int = 0
    results_match: bool = False
    status: str = "SUCCESS"
    error_message: str | None = None

    def __post_init__(self):
        if self.sql_time_ms > 0 and self.df_time_ms > 0:
            self.speedup = self.sql_time_ms / self.df_time_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "sql_platform": self.sql_platform,
            "df_platform": self.df_platform,
            "sql_time_ms": self.sql_time_ms,
            "df_time_ms": self.df_time_ms,
            "speedup": self.speedup,
            "sql_rows": self.sql_rows,
            "df_rows": self.df_rows,
            "results_match": self.results_match,
            "status": self.status,
            "error_message": self.error_message,
        }


@dataclass
class SQLVsDataFrameSummary:
    sql_platform: str
    df_platform: str
    total_queries: int
    df_faster_count: int
    sql_faster_count: int
    average_speedup: float
    max_speedup: float
    min_speedup: float
    query_results: list[SQLComparisonResult] = field(default_factory=list)

    @property
    def df_wins_percentage(self) -> float:
        if self.total_queries == 0:
            return 0.0
        return (self.df_faster_count / self.total_queries) * 100

    def to_dict(self) -> dict[str, Any]:
        return {
            "sql_platform": self.sql_platform,
            "df_platform": self.df_platform,
            "total_queries": self.total_queries,
            "df_faster_count": self.df_faster_count,
            "sql_faster_count": self.sql_faster_count,
            "df_wins_percentage": self.df_wins_percentage,
            "average_speedup": self.average_speedup,
            "max_speedup": self.max_speedup,
            "min_speedup": self.min_speedup,
            "query_results": [r.to_dict() for r in self.query_results],
        }


class SQLVsDataFrameBenchmark:
    def __init__(self, config: BenchmarkConfig | None = None):
        self.config = config or BenchmarkConfig()
        self._query_registry = get_tpch_dataframe_queries()

    def run_comparison(
        self,
        sql_platform: str,
        df_platform: str,
        data_dir: str | Path,
    ) -> SQLVsDataFrameSummary:
        data_path = Path(data_dir)

        query_ids = self.config.query_ids or self._query_registry.get_query_ids()
        results: list[SQLComparisonResult] = []

        for query_id in query_ids:
            logger.info(f"Comparing SQL vs DataFrame for {query_id}...")
            result = self._compare_query(
                query_id=query_id,
                sql_platform=sql_platform,
                df_platform=df_platform,
                data_path=data_path,
            )
            results.append(result)

        return self._build_summary(sql_platform, df_platform, results)

    def _compare_query(
        self,
        query_id: str,
        sql_platform: str,
        df_platform: str,
        data_path: Path,
    ) -> SQLComparisonResult:
        try:
            sql_time, sql_rows = self._run_sql_query(query_id, sql_platform, data_path)

            df_time, df_rows = self._run_df_query(query_id, df_platform, data_path)

            results_match = sql_rows == df_rows

            return SQLComparisonResult(
                query_id=query_id,
                sql_platform=sql_platform,
                df_platform=df_platform,
                sql_time_ms=sql_time,
                df_time_ms=df_time,
                sql_rows=sql_rows,
                df_rows=df_rows,
                results_match=results_match,
                status="SUCCESS",
            )

        except Exception as e:
            logger.error(f"Error comparing {query_id}: {e}")
            return SQLComparisonResult(
                query_id=query_id,
                sql_platform=sql_platform,
                df_platform=df_platform,
                status="ERROR",
                error_message=str(e),
            )

    def _run_sql_query(
        self,
        query_id: str,
        sql_platform: str,
        data_path: Path,
    ) -> tuple[float, int]:
        from benchbox.core.tpch.queries import TPCHQuery

        query_num = int(query_id.replace("Q", ""))
        tpch_query = TPCHQuery.get_query(query_num, dialect=sql_platform)

        parquet_dir = data_path / "parquet"
        if not parquet_dir.exists():
            parquet_dir = data_path

        if sql_platform == "duckdb":
            conn = self._connect_duckdb(parquet_dir)
        elif sql_platform == "sqlite":
            conn = self._connect_sqlite(parquet_dir)
        else:
            raise ValueError(f"Unsupported SQL platform: {sql_platform}")

        try:
            return self._warmup_and_benchmark(conn, tpch_query.sql)
        finally:
            conn.close()

    @staticmethod
    def _connect_duckdb(parquet_dir: Path) -> Any:
        import duckdb

        conn = duckdb.connect()
        tables = ["lineitem", "orders", "customer", "supplier", "part", "partsupp", "nation", "region"]
        for table in tables:
            table_paths = resolve_table_paths(parquet_dir, table)
            if table_paths:
                conn.execute(
                    f"CREATE TABLE {table} AS SELECT * FROM read_parquet(?)",
                    [[str(path) for path in table_paths]],
                )
        return conn

    @staticmethod
    def _connect_sqlite(parquet_dir: Path) -> Any:
        import sqlite3

        import pandas as pd

        conn = sqlite3.connect(":memory:")
        tables = ["lineitem", "orders", "customer", "supplier", "part", "partsupp", "nation", "region"]
        for table in tables:
            table_paths = resolve_table_paths(parquet_dir, table)
            for index, path in enumerate(table_paths):
                pd.read_parquet(str(path)).to_sql(
                    table, conn, index=False, if_exists="replace" if index == 0 else "append"
                )
        return conn

    def _warmup_and_benchmark(self, conn: Any, sql: str) -> tuple[float, int]:
        for _ in range(self.config.warmup_iterations):
            conn.execute(sql).fetchall()

        times = []
        row_count = 0
        for i in range(self.config.benchmark_iterations):
            start = time.perf_counter()
            result = conn.execute(sql).fetchall()
            elapsed_ms = (time.perf_counter() - start) * 1000
            times.append(elapsed_ms)
            if i == 0:
                row_count = len(result)

        return statistics.mean(times), row_count

    def _run_df_query(
        self,
        query_id: str,
        df_platform: str,
        data_path: Path,
    ) -> tuple[float, int]:
        from benchbox.platforms import get_dataframe_adapter

        parquet_dir = data_path / "parquet"
        if not parquet_dir.exists():
            parquet_dir = data_path

        tables = ["lineitem", "orders", "customer", "supplier", "part", "partsupp", "nation", "region"]

        adapter = get_dataframe_adapter(df_platform, working_dir=str(data_path))
        ctx = adapter.create_context()

        for table in tables:
            table_paths = resolve_table_paths(parquet_dir, table)
            if table_paths:
                adapter.load_table(ctx, table, table_paths)

        query = self._query_registry.get(query_id)
        if query is None:
            raise ValueError(f"Query {query_id} not found")

        capability = PLATFORM_CAPABILITIES.get(df_platform)
        family = capability.family if capability else "expression"

        impl = query.get_impl_for_family(family)
        if impl is None:
            raise ValueError(f"No {family} implementation for {query_id}")

        for _ in range(self.config.warmup_iterations):
            result = query.execute(ctx, family)
            if hasattr(result, "collect"):
                result = result.collect()
            del result

        times = []
        row_count = 0
        for i in range(self.config.benchmark_iterations):
            start = time.perf_counter()
            result = query.execute(ctx, family)
            if hasattr(result, "collect"):
                result = result.collect()
            elapsed_ms = (time.perf_counter() - start) * 1000
            times.append(elapsed_ms)
            if i == 0:
                try:
                    row_count = len(result)
                except Exception:
                    row_count = 0
            del result

        return statistics.mean(times), row_count

    def _build_summary(
        self,
        sql_platform: str,
        df_platform: str,
        results: list[SQLComparisonResult],
    ) -> SQLVsDataFrameSummary:
        successful = [r for r in results if r.status == "SUCCESS" and r.speedup > 0]

        if not successful:
            return SQLVsDataFrameSummary(
                sql_platform=sql_platform,
                df_platform=df_platform,
                total_queries=len(results),
                df_faster_count=0,
                sql_faster_count=0,
                average_speedup=1.0,
                max_speedup=1.0,
                min_speedup=1.0,
                query_results=results,
            )

        speedups = [r.speedup for r in successful]
        df_faster = sum(1 for r in successful if r.speedup > 1.0)
        sql_faster = sum(1 for r in successful if r.speedup < 1.0)

        return SQLVsDataFrameSummary(
            sql_platform=sql_platform,
            df_platform=df_platform,
            total_queries=len(results),
            df_faster_count=df_faster,
            sql_faster_count=sql_faster,
            average_speedup=statistics.mean(speedups),
            max_speedup=max(speedups),
            min_speedup=min(speedups),
            query_results=results,
        )

    def generate_report(self, summary: SQLVsDataFrameSummary) -> str:
        lines = []
        lines.append("# SQL vs DataFrame Performance Comparison")
        lines.append("")
        lines.append(f"**SQL Platform:** {summary.sql_platform}")
        lines.append(f"**DataFrame Platform:** {summary.df_platform}")
        lines.append(f"**Scale Factor:** {self.config.scale_factor}")
        lines.append(f"**Total Queries:** {summary.total_queries}")
        lines.append("")

        lines.append("## Summary")
        lines.append("")
        lines.append(f"- **DataFrame wins:** {summary.df_faster_count} queries ({summary.df_wins_percentage:.1f}%)")
        lines.append(f"- **SQL wins:** {summary.sql_faster_count} queries")
        lines.append(f"- **Average speedup:** {summary.average_speedup:.2f}x")
        lines.append(f"- **Max speedup (DF fastest):** {summary.max_speedup:.2f}x")
        lines.append(f"- **Min speedup (SQL fastest):** {summary.min_speedup:.2f}x")
        lines.append("")

        lines.append("## Query-by-Query Results")
        lines.append("")
        lines.append("| Query | SQL (ms) | DataFrame (ms) | Speedup | Winner |")
        lines.append("|-------|----------|----------------|---------|--------|")

        for result in summary.query_results:
            if result.status == "SUCCESS":
                winner = "DataFrame" if result.speedup > 1.0 else "SQL"
                lines.append(
                    f"| {result.query_id} | {result.sql_time_ms:.2f} | "
                    f"{result.df_time_ms:.2f} | {result.speedup:.2f}x | {winner} |"
                )
            else:
                lines.append(f"| {result.query_id} | ERROR | ERROR | N/A | - |")

        lines.append("")

        lines.append("## Interpretation")
        lines.append("")
        if summary.average_speedup > 1.0:
            lines.append(
                f"DataFrame execution is **{summary.average_speedup:.1f}x faster** on average than SQL "
                f"for this workload."
            )
        else:
            sql_speedup = 1.0 / summary.average_speedup if summary.average_speedup > 0 else 1.0
            lines.append(f"SQL execution is **{sql_speedup:.1f}x faster** on average than DataFrame for this workload.")

        return "\n".join(lines)


def run_quick_comparison(
    platforms: list[str] | None = None,
    scale_factor: float = 0.01,
    query_ids: list[str] | None = None,
    data_dir: str | Path | None = None,
) -> list[PlatformBenchmarkResult]:
    suite = DataFrameBenchmarkSuite(
        config=BenchmarkConfig(
            scale_factor=scale_factor,
            query_ids=query_ids or ["Q1", "Q3", "Q6", "Q10"],
            warmup_iterations=1,
            benchmark_iterations=3,
        )
    )

    if platforms is None:
        platforms = suite.get_available_platforms()

    if data_dir is None:
        data_dir = get_benchmark_runs_datagen_path("tpch", scale_factor)

    return suite.run_comparison(platforms=platforms, data_dir=data_dir)


def run_sql_vs_dataframe(
    sql_platform: str = "duckdb",
    df_platform: str = "polars-df",
    scale_factor: float = 0.01,
    query_ids: list[str] | None = None,
    data_dir: str | Path | None = None,
) -> SQLVsDataFrameSummary:
    benchmark = SQLVsDataFrameBenchmark(
        config=BenchmarkConfig(
            scale_factor=scale_factor,
            query_ids=query_ids or ["Q1", "Q3", "Q6", "Q10"],
            warmup_iterations=1,
            benchmark_iterations=3,
        )
    )

    if data_dir is None:
        data_dir = get_benchmark_runs_datagen_path("tpch", scale_factor)

    return benchmark.run_comparison(
        sql_platform=sql_platform,
        df_platform=df_platform,
        data_dir=data_dir,
    )


class DataFrameComparisonPlotter:
    def __init__(
        self,
        results: list[PlatformBenchmarkResult],
        theme: str = "light",
    ):
        if not results:
            raise ValueError("No results provided for visualization")
        self.results = results
        self.theme = theme

    def generate_charts(self, output_dir: str | Path, **kwargs: object) -> dict[str, Path]:
        from benchbox.core.visualization.chart_generator import (
            generate_comparison_charts,
            normalized_from_dataframe,
        )

        normalized = normalized_from_dataframe(self.results)
        return generate_comparison_charts(normalized, output_dir, theme=self.theme)


class SQLVsDataFramePlotter:
    def __init__(
        self,
        summary: SQLVsDataFrameSummary,
        theme: str = "light",
    ):
        self.summary = summary
        self.theme = theme

    def generate_charts(self, output_dir: str | Path, **kwargs: object) -> dict[str, Path]:
        from benchbox.core.visualization.chart_generator import (
            generate_comparison_charts,
            normalized_from_sql_vs_df,
        )

        normalized = normalized_from_sql_vs_df(self.summary)
        return generate_comparison_charts(normalized, output_dir, theme=self.theme)
