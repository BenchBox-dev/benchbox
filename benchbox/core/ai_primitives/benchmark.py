# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Union

import yaml

from benchbox.base import BaseBenchmark
from benchbox.core.ai_primitives.cost import (
    CostEstimate,
    CostTracker,
    estimate_query_cost,
    format_cost_warning,
)
from benchbox.core.ai_primitives.queries import AIQueryManager
from benchbox.utils.cloud_storage import normalize_output_dir
from benchbox.utils.path_utils import get_benchmark_runs_datagen_path

logger = logging.getLogger(__name__)


def _load_specs() -> dict[str, Any]:
    with (Path(__file__).with_name("specs.yaml")).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


_SPECS = _load_specs()

SUPPORTED_PLATFORMS = set(_SPECS["supported_platforms"])

UNSUPPORTED_PLATFORMS = set(_SPECS["unsupported_platforms"])


@dataclass
class AIQueryResult:
    query_id: str
    category: str
    execution_time_ms: float = 0.0
    success: bool = True
    rows_processed: int = 0
    tokens_estimated: int = 0
    cost_estimated_usd: float = 0.0
    error: str | None = None
    result_sample: list[Any] = field(default_factory=list)


@dataclass
class AIBenchmarkResult:
    benchmark: str = "AI Primitives"
    platform: str = ""
    scale_factor: float = 1.0
    dry_run: bool = False
    total_queries: int = 0
    successful_queries: int = 0
    failed_queries: int = 0
    skipped_queries: int = 0
    total_execution_time_ms: float = 0.0
    total_cost_estimated_usd: float = 0.0
    cost_tracker: CostTracker | None = None
    query_results: list[AIQueryResult] = field(default_factory=list)


class AIPrimitivesBenchmark(BaseBenchmark):
    def __init__(
        self,
        scale_factor: float = 0.01,
        output_dir: Union[str, Path] | None = None,
        max_cost_usd: float = 0.0,
        dry_run: bool = False,
        **config: Any,
    ):
        config = dict(config)
        quiet = config.pop("quiet", False)

        super().__init__(scale_factor, quiet=quiet, **config)

        self._name = "AI/ML Primitives Benchmark"
        self._version = "1.0"
        self._description = "AI/ML Primitives benchmark - Testing SQL-based AI functions using TPC-H data"

        if output_dir is None:
            output_dir = get_benchmark_runs_datagen_path("tpch", scale_factor)
        self.output_dir = normalize_output_dir(output_dir)

        self.query_manager: AIQueryManager = AIQueryManager()
        self.max_cost_usd = max_cost_usd
        self.dry_run = dry_run
        self.cost_tracker = CostTracker(budget_usd=max_cost_usd)

        self.tables: dict[str, Any] = {}

    def get_data_source_benchmark(self) -> str | None:
        return "tpch"

    def get_schema(self) -> dict[str, dict[str, Any]]:
        from benchbox.core.tpch.benchmark import TPCHBenchmark

        return TPCHBenchmark(scale_factor=self.scale_factor, output_dir=self.output_dir).get_schema()

    def get_create_tables_sql(self, dialect: str = "standard", tuning_config: Any | None = None) -> str:
        from benchbox.core.tpch.benchmark import TPCHBenchmark

        return TPCHBenchmark(scale_factor=self.scale_factor, output_dir=self.output_dir).get_create_tables_sql(
            dialect=dialect,
            tuning_config=tuning_config,
        )

    def generate_data(self) -> list[Union[str, Path]]:
        from benchbox.core.tpch.benchmark import TPCHBenchmark

        tpch = TPCHBenchmark(scale_factor=self.scale_factor, output_dir=self.output_dir)
        tpch.generate_data()

        self.tables = dict(tpch.tables)

        flattened: list[Union[str, Path]] = []
        for table_files in self.tables.values():
            if isinstance(table_files, (list, tuple)):
                flattened.extend(table_files)
            else:
                flattened.append(table_files)
        return flattened

    def is_platform_supported(self, platform: str) -> bool:
        return platform.lower() in SUPPORTED_PLATFORMS

    def get_supported_queries(self, platform: str) -> dict[str, str]:
        if not self.is_platform_supported(platform):
            return {}
        return self.query_manager.get_supported_queries(platform)

    def get_query(self, query_id: Union[int, str], *, params: dict[str, Any] | None = None) -> str:
        if params is not None:
            raise ValueError("AI Primitives queries are static and don't accept parameters")
        return self.query_manager.get_query(str(query_id))

    def get_queries(self, dialect: str | None = None) -> dict[str, str]:
        if dialect:
            return self.get_supported_queries(dialect)
        return self.query_manager.get_all_queries()

    def get_all_queries(self) -> dict[str, str]:
        return self.query_manager.get_all_queries()

    def get_queries_by_category(self, category: str) -> dict[str, str]:
        return self.query_manager.get_queries_by_category(category)

    def get_query_categories(self) -> list[str]:
        return self.query_manager.get_query_categories()

    def estimate_cost(
        self,
        platform: str,
        queries: list[str] | None = None,
        categories: list[str] | None = None,
    ) -> tuple[float, list[CostEstimate]]:
        estimates: list[CostEstimate] = []
        total_cost = 0.0

        if queries is not None:
            query_ids = queries
        elif categories:
            query_ids = []
            for category in categories:
                cat_queries = self.query_manager.get_queries_by_category(category)
                query_ids.extend(cat_queries.keys())
        else:
            query_ids = list(self.query_manager.get_all_queries().keys())

        for query_id in query_ids:
            try:
                entry = self.query_manager.get_query_entry(query_id)

                if entry.skip_on and platform.lower() in entry.skip_on:
                    continue

                estimate = estimate_query_cost(
                    query_id=query_id,
                    platform=platform,
                    model=entry.model,
                    estimated_tokens=entry.estimated_tokens,
                    num_rows=entry.batch_size,
                    cost_per_1k_tokens=entry.cost_per_1k_tokens,
                )
                estimates.append(estimate)
                total_cost += estimate.estimated_cost_usd

            except ValueError:
                continue

        return total_cost, estimates

    def execute_query(
        self,
        query_id: Union[int, str],
        connection: Any,
        platform: str,
        params: dict[str, Any] | None = None,
    ) -> AIQueryResult:
        query_id_str = str(query_id)
        entry = self.query_manager.get_query_entry(query_id_str)

        result = AIQueryResult(
            query_id=query_id_str,
            category=entry.category,
            tokens_estimated=entry.estimated_tokens * entry.batch_size,
            cost_estimated_usd=(entry.estimated_tokens * entry.batch_size / 1000) * entry.cost_per_1k_tokens,
        )

        if entry.skip_on and platform.lower() in entry.skip_on:
            result.success = False
            result.error = f"Query not supported on platform '{platform}'"
            return result

        try:
            sql = self.query_manager.get_query(query_id_str, dialect=platform)
        except ValueError as e:
            result.success = False
            result.error = str(e)
            return result

        start_time = time.perf_counter()
        try:
            if hasattr(connection, "execute"):
                cursor = connection.execute(sql)
                rows = cursor.fetchall() if hasattr(cursor, "fetchall") else []
            elif hasattr(connection, "cursor"):
                cursor = connection.cursor()
                cursor.execute(sql)
                rows = cursor.fetchall()
            else:
                raise ValueError("Unsupported connection type")

            end_time = time.perf_counter()
            result.execution_time_ms = (end_time - start_time) * 1000
            result.rows_processed = len(rows)
            result.success = True

            if rows:
                result.result_sample = [list(row) if hasattr(row, "__iter__") else [row] for row in rows[:3]]

        except Exception as e:
            end_time = time.perf_counter()
            result.execution_time_ms = (end_time - start_time) * 1000
            result.success = False
            result.error = str(e)
            logger.warning(f"Query {query_id_str} failed: {e}")

        return result

    def run_benchmark(
        self,
        connection: Any,
        platform: str,
        queries: list[str] | None = None,
        categories: list[str] | None = None,
        dry_run: bool = False,
    ) -> AIBenchmarkResult:
        if not dry_run:
            dry_run = self.dry_run

        result = AIBenchmarkResult(
            platform=platform,
            scale_factor=self.scale_factor,
            dry_run=dry_run,
        )

        if not self.is_platform_supported(platform):
            logger.warning(f"Platform '{platform}' does not support AI functions")
            result.skipped_queries = len(self.query_manager.get_all_queries())
            return result

        query_ids = self._resolve_query_ids(queries, categories, platform)
        result.total_queries = len(query_ids)

        total_cost, estimates = self._prepare_cost_tracking(platform, query_ids, dry_run)
        result.total_cost_estimated_usd = total_cost

        if dry_run:
            return self._build_dry_run_result(result, estimates)

        self._execute_ai_queries(result, query_ids, connection, platform)
        result.cost_tracker = self.cost_tracker
        return result

    def _resolve_query_ids(self, queries: list[str] | None, categories: list[str] | None, platform: str) -> list[str]:
        if queries is not None:
            return queries
        if categories:
            query_ids: list[str] = []
            for category in categories:
                cat_queries = self.query_manager.get_queries_by_category(category)
                query_ids.extend(cat_queries.keys())
            return query_ids
        return list(self.query_manager.get_supported_queries(platform).keys())

    def _prepare_cost_tracking(
        self, platform: str, query_ids: list[str], dry_run: bool
    ) -> tuple[float, list[CostEstimate]]:
        total_cost, estimates = self.estimate_cost(platform, query_ids)

        self.cost_tracker = CostTracker(platform=platform, budget_usd=self.max_cost_usd)
        for estimate in estimates:
            self.cost_tracker.add_estimate(estimate)

        if total_cost > 0:
            warning = format_cost_warning(total_cost, self.max_cost_usd or None, platform)
            logger.info(warning)

        if self.max_cost_usd > 0 and total_cost > self.max_cost_usd:
            logger.error(f"Estimated cost ${total_cost:.4f} exceeds budget ${self.max_cost_usd:.4f}")
            if not dry_run:
                raise ValueError(
                    f"Estimated cost ${total_cost:.4f} exceeds budget ${self.max_cost_usd:.4f}. "
                    "Use --dry-run to preview costs or increase --max-ai-cost."
                )

        return total_cost, estimates

    def _build_dry_run_result(self, result: AIBenchmarkResult, estimates: list[CostEstimate]) -> AIBenchmarkResult:
        logger.info("Dry run mode - returning cost estimates only")
        for estimate in estimates:
            query_result = AIQueryResult(
                query_id=estimate.query_id,
                category=self.query_manager.get_query_entry(estimate.query_id).category,
                tokens_estimated=estimate.estimated_tokens,
                cost_estimated_usd=estimate.estimated_cost_usd,
                success=True,
            )
            result.query_results.append(query_result)
        return result

    def _execute_ai_queries(
        self, result: AIBenchmarkResult, query_ids: list[str], connection: Any, platform: str
    ) -> None:
        start_time = time.perf_counter()

        for query_id in query_ids:
            try:
                entry = self.query_manager.get_query_entry(query_id)

                if entry.skip_on and platform.lower() in entry.skip_on:
                    result.skipped_queries += 1
                    continue

                estimated_cost = (entry.estimated_tokens * entry.batch_size / 1000) * entry.cost_per_1k_tokens
                if not self.cost_tracker.check_budget(estimated_cost):
                    logger.warning(f"Skipping {query_id} - would exceed budget")
                    result.skipped_queries += 1
                    continue

                query_result = self.execute_query(query_id, connection, platform)
                result.query_results.append(query_result)

                if query_result.success:
                    result.successful_queries += 1
                    self.cost_tracker.record_execution(
                        query_id, query_result.tokens_estimated, query_result.cost_estimated_usd, success=True
                    )
                else:
                    result.failed_queries += 1
                    self.cost_tracker.record_execution(query_id, 0, 0, success=False)

            except Exception as e:
                logger.error(f"Error executing query {query_id}: {e}")
                result.failed_queries += 1

        end_time = time.perf_counter()
        result.total_execution_time_ms = (end_time - start_time) * 1000

    def get_benchmark_info(self) -> dict[str, Any]:
        return {
            "name": self._name,
            "version": self._version,
            "description": self._description,
            "scale_factor": self.scale_factor,
            "total_queries": len(self.query_manager.get_all_queries()),
            "categories": self.get_query_categories(),
            "supported_platforms": list(SUPPORTED_PLATFORMS),
            "unsupported_platforms": list(UNSUPPORTED_PLATFORMS),
            "max_cost_usd": self.max_cost_usd,
            "dry_run": self.dry_run,
            "data_source": "tpch",
        }

    def _get_default_benchmark_type(self) -> str:
        return "analytical"

    def supports_dataframe_mode(self) -> bool:
        return True

    def get_dataframe_operations(self) -> Any:
        from benchbox.core.ai_primitives.dataframe_operations import (
            DataFrameAIOperationsManager,
        )

        return DataFrameAIOperationsManager("generic")

    def get_dataframe_skip_queries(self) -> list[str]:
        from benchbox.core.ai_primitives.dataframe_operations import (
            get_skip_for_dataframe,
        )

        return get_skip_for_dataframe()

    def get_dataframe_supported_queries(self) -> list[str]:
        all_query_ids = list(self.query_manager.get_all_queries().keys())
        skip_ids = set(self.get_dataframe_skip_queries())
        return [qid for qid in all_query_ids if qid not in skip_ids]


__all__ = [
    "AIPrimitivesBenchmark",
    "AIQueryResult",
    "AIBenchmarkResult",
    "SUPPORTED_PLATFORMS",
    "UNSUPPORTED_PLATFORMS",
]
