# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

from benchbox.base import BaseBenchmark, GeneratorOutputDirMixin
from benchbox.core.benchmark_mixins import DataGenerationMixin
from benchbox.core.query_catalog_base import QuerySkippedError, TranslatableQueryMixin
from benchbox.core.utils.tuning import extract_constraint_flags
from benchbox.core.vector_search.generator import VectorSearchDataGenerator
from benchbox.core.vector_search.metrics import validate_search_result
from benchbox.core.vector_search.queries import VectorSearchQueryManager
from benchbox.core.vector_search.schema import TABLES, get_all_create_table_sql
from benchbox.utils.clock import elapsed_seconds, mono_time

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import UnifiedTuningConfiguration

DEFAULT_DIMENSIONS = 128


class VectorSearchBenchmark(GeneratorOutputDirMixin, TranslatableQueryMixin, DataGenerationMixin, BaseBenchmark):
    _source_dialect = "duckdb"
    _native_source_passthrough = True

    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Optional[Union[str, Path]] = None,
        *,
        dimensions: int = DEFAULT_DIMENSIONS,
        **config: Any,
    ) -> None:
        config = dict(config)
        quiet = config.pop("quiet", False)

        super().__init__(scale_factor, output_dir=output_dir, quiet=quiet, **config)

        self._name = "Vector Search Benchmark"
        self._version = "1.0"
        self._description = (
            "Benchmarks similarity search (kNN, ANN, filtered) across OLAP databases with vector/embedding support."
        )

        self.dimensions = dimensions

        self.query_manager = VectorSearchQueryManager()
        self.data_generator = VectorSearchDataGenerator(
            scale_factor,
            self.output_dir,
            dimensions=dimensions,
            **config,
        )

        self.tables: dict[str, Path] = {}
        self._csv_delimiter: str = "|"
        self.csv_has_header: bool = True

    def _get_table_schema(self) -> dict[str, dict]:
        return TABLES

    def get_schema(self, dialect: str = "duckdb") -> dict[str, dict]:
        return TABLES

    def get_create_tables_sql(
        self,
        dialect: str = "duckdb",
        tuning_config: Optional[UnifiedTuningConfiguration] = None,
    ) -> str:
        enable_primary_keys, _ = extract_constraint_flags(tuning_config)
        return get_all_create_table_sql(
            dialect=dialect,
            dimensions=self.dimensions,
            enable_primary_keys=enable_primary_keys,
        )

    def get_query(
        self,
        query_id: Union[int, str],
        *,
        params: Optional[dict[str, Any]] = None,
        dialect: Optional[str] = None,
        platform_version: str | None = None,
    ) -> str:
        if params is not None:
            raise ValueError(
                "Vector search queries are static and do not accept parameters. "
                "To use a different dialect pass dialect= to get_query()."
            )
        return self.query_manager.get_query(str(query_id), dialect=dialect, platform_version=platform_version)

    def get_queries(self, dialect: Optional[str] = None, platform_version: str | None = None) -> dict[str, str]:
        return self.query_manager.get_all_queries(dialect=dialect, platform_version=platform_version)

    def get_all_queries(
        self,
        dialect: Optional[str] = None,
        platform_version: str | None = None,
    ) -> dict[str, str]:
        return self.query_manager.get_all_queries(dialect=dialect, platform_version=platform_version)

    def validate_query_result(self, query_id: Union[int, str], rows: Sequence[Sequence[object]]) -> None:
        validate_search_result(str(query_id), rows)

    def execute_query(
        self,
        query_id: Union[int, str],
        connection: Any,
        params: Optional[dict[str, Any]] = None,
        *,
        dialect: Optional[str] = None,
        platform_version: str | None = None,
    ) -> Any:
        sql = self.get_query(query_id, dialect=dialect, params=params, platform_version=platform_version)

        if hasattr(connection, "execute"):
            cursor = connection.execute(sql)
            return cursor.fetchall()
        elif hasattr(connection, "cursor"):
            cursor = connection.cursor()
            cursor.execute(sql)
            return cursor.fetchall()
        else:
            raise ValueError(f"Unsupported connection type: {type(connection)}")

    def run_benchmark(
        self,
        connection: Any,
        queries: Optional[list[str]] = None,
        iterations: int = 1,
        *,
        dialect: Optional[str] = None,
        platform_version: str | None = None,
    ) -> dict[str, Any]:
        if queries is None:
            queries = list(self.query_manager.ALL_QUERY_IDS)

        results: dict[str, Any] = {
            "benchmark": "Vector Search Benchmark",
            "scale_factor": self.scale_factor,
            "dimensions": self.dimensions,
            "iterations": iterations,
            "queries": {},
        }

        for query_id in queries:
            description = self.query_manager.get_description(query_id)
            try:
                sql_text = self.get_query(query_id, dialect=dialect, platform_version=platform_version)
            except QuerySkippedError as exc:
                results["queries"][query_id] = {
                    "query_id": query_id,
                    "description": description,
                    "iterations": [],
                    "avg_time": 0.0,
                    "min_time": 0.0,
                    "max_time": 0.0,
                    "sql_text": None,
                    "rows_returned": 0,
                    "status": "SKIPPED",
                    "skip_reason": str(exc),
                }
                continue

            query_results: dict[str, Any] = {
                "query_id": query_id,
                "description": description,
                "iterations": [],
                "avg_time": 0.0,
                "min_time": float("inf"),
                "max_time": 0.0,
                "sql_text": sql_text,
            }

            for i in range(iterations):
                start = mono_time()
                try:
                    result = self.execute_query(
                        query_id,
                        connection,
                        dialect=dialect,
                        platform_version=platform_version,
                    )
                    duration = elapsed_seconds(start)
                    self.validate_query_result(query_id, result)
                    query_results["iterations"].append(
                        {
                            "iteration": i + 1,
                            "time": duration,
                            "rows": len(result) if result else 0,
                            "success": True,
                        }
                    )
                    query_results["min_time"] = min(query_results["min_time"], duration)
                    query_results["max_time"] = max(query_results["max_time"], duration)
                except Exception as exc:
                    query_results["iterations"].append(
                        {
                            "iteration": i + 1,
                            "time": 0.0,
                            "error": str(exc),
                            "success": False,
                        }
                    )

            successful = [it for it in query_results["iterations"] if it["success"]]
            if successful:
                times = [it["time"] for it in successful]
                rows = [it.get("rows", 0) for it in successful]
                query_results["avg_time"] = sum(times) / len(times)
                query_results["rows_returned"] = int(sum(rows) / len(rows))

            results["queries"][query_id] = query_results

        return results


from benchbox.core.hooks.benchmark_hooks import (
    BenchmarkHookRegistry,
    BenchmarkOptionSpec,
    parse_int,
)

BenchmarkHookRegistry.register_option_specs(
    "vector_search",
    BenchmarkOptionSpec(
        name="dimensions",
        parser=parse_int,
        default=DEFAULT_DIMENSIONS,
        help="Embedding vector dimensions",
    ),
    benchmark_class=VectorSearchBenchmark,
)
