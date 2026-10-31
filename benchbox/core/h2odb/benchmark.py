# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

from benchbox.base import BaseBenchmark, GeneratorOutputDirMixin
from benchbox.core.benchmark_mixins import DataGenerationMixin
from benchbox.core.h2odb.generator import H2ODataGenerator
from benchbox.core.h2odb.queries import H2OQueryManager
from benchbox.core.h2odb.schema import TABLES, get_all_create_table_sql
from benchbox.core.query_catalog_base import TranslatableQueryMixin
from benchbox.core.query_utils import get_queries_with_translation
from benchbox.core.utils.tuning import extract_constraint_flags
from benchbox.utils.clock import elapsed_seconds, mono_time

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import UnifiedTuningConfiguration


class H2OBenchmark(GeneratorOutputDirMixin, TranslatableQueryMixin, DataGenerationMixin, BaseBenchmark):
    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Optional[Union[str, Path]] = None,
        **config: Any,
    ):

        config = dict(config)
        quiet = config.pop("quiet", False)

        super().__init__(scale_factor, output_dir=output_dir, quiet=quiet, **config)

        self._name = "H2O Database Benchmark"
        self._version = "1.0"
        self._description = "H2O DB Benchmark - Analytical database performance benchmark using taxi trip data"

        self.query_manager: H2OQueryManager = H2OQueryManager()
        self.data_generator = H2ODataGenerator(
            scale_factor,
            self.output_dir,
            **config,
        )

        self.tables: dict[str, Path] = {}

    def _get_table_schema(self) -> dict[str, dict]:

        return TABLES

    def _get_data_loading_batch_size(self) -> int | None:

        return 10000

    def get_query(self, query_id: Union[int, str], *, params: Optional[dict[str, Any]] = None) -> str:

        if params is not None:
            raise ValueError("H2O DB queries are static and don't accept parameters")
        return self.query_manager.get_query(str(query_id))

    def get_queries(self, dialect: Optional[str] = None) -> dict[str, str]:

        from benchbox.sql_compat.actions import CompatAction
        from benchbox.sql_compat.context import CompatibilityContext, Phase
        from benchbox.sql_compat.registry import REGISTRY
        from benchbox.sql_compat.rules.query_source.h2odb_variants import (
            BIGQUERY_Q9_SQL,
            CLICKHOUSE_Q9_SQL,
            QUESTDB_Q9_SQL,
            SQLITE_Q9_SQL,
            STARROCKS_Q9_SQL,
        )

        queries = get_queries_with_translation(self.query_manager, dialect, self.translate_query_text)
        if not dialect:
            return queries

        d = dialect.lower()

        _q9_variants: dict[str, str] = {
            "bigquery": BIGQUERY_Q9_SQL,
            "clickhouse": CLICKHOUSE_Q9_SQL,
            "questdb": QUESTDB_Q9_SQL,
            "sqlite": SQLITE_Q9_SQL,
            "starrocks": STARROCKS_Q9_SQL,
        }
        for platform, legacy_sql in _q9_variants.items():
            if platform not in d:
                continue
            ctx = CompatibilityContext(
                platform=platform,
                platform_version=None,
                benchmark="h2odb",
                query_id="Q9",
                phase=Phase.QUERY_SOURCE,
                mode="sql",
                dialect=dialect,
            )
            registry_decision = REGISTRY.resolve(ctx)
            if registry_decision is not None:
                if registry_decision.action is CompatAction.SELECT_VARIANT:
                    queries["Q9"] = registry_decision.payload.variant_sql
            else:
                queries["Q9"] = legacy_sql

        return queries

    def get_all_queries(self) -> dict[str, str]:

        return self.query_manager.get_all_queries()

    def execute_query(
        self,
        query_id: Union[int, str],
        connection: Any,
        params: Optional[dict[str, Any]] = None,
    ) -> Any:

        sql = self.get_query(query_id)

        if hasattr(connection, "execute"):
            cursor = connection.execute(sql)
            return cursor.fetchall()
        elif hasattr(connection, "cursor"):
            cursor = connection.cursor()
            cursor.execute(sql)
            return cursor.fetchall()
        else:
            raise ValueError("Unsupported connection type")

    def get_schema(self, dialect: str = "standard") -> dict[str, dict]:

        return TABLES

    def get_create_tables_sql(
        self,
        dialect: str = "standard",
        tuning_config: Optional["UnifiedTuningConfiguration"] = None,
    ) -> str:

        enable_primary_keys, enable_foreign_keys = extract_constraint_flags(tuning_config)
        return get_all_create_table_sql(dialect, enable_primary_keys, enable_foreign_keys)

    def run_benchmark(
        self, connection: Any, queries: Optional[list[str]] = None, iterations: int = 1
    ) -> dict[str, Any]:

        if queries is None:
            queries = list(self.query_manager.get_all_queries().keys())

        results = {
            "benchmark": "H2O Database Benchmark",
            "scale_factor": self.scale_factor,
            "iterations": iterations,
            "queries": {},
        }

        for query_id in queries:
            query_results = {
                "query_id": query_id,
                "iterations": [],
                "avg_time": 0,
                "min_time": float("inf"),
                "max_time": 0,
                "sql_text": self.get_query(query_id),
            }

            for i in range(iterations):
                start_time = mono_time()
                try:
                    result = self.execute_query(query_id, connection)
                    execution_time = elapsed_seconds(start_time)

                    query_results["iterations"].append(
                        {
                            "iteration": i + 1,
                            "time": execution_time,
                            "rows": len(result) if result else 0,
                            "success": True,
                        }
                    )

                    query_results["min_time"] = min(query_results["min_time"], execution_time)
                    query_results["max_time"] = max(query_results["max_time"], execution_time)

                except Exception as e:
                    query_results["iterations"].append(
                        {
                            "iteration": i + 1,
                            "time": 0,
                            "error": str(e),
                            "success": False,
                        }
                    )

            iterations_list: list[dict[str, Any]] = query_results["iterations"]
            successful_iterations = [iter_result for iter_result in iterations_list if iter_result["success"]]
            if successful_iterations:
                successful_times = [iter_result["time"] for iter_result in successful_iterations]
                successful_rows = [iter_result.get("rows", 0) for iter_result in successful_iterations]
                query_results["avg_time"] = sum(successful_times) / len(successful_times)
                query_results["rows_returned"] = (
                    int(sum(successful_rows) / len(successful_rows)) if successful_rows else 0
                )

            results["queries"][query_id] = query_results

        return results
