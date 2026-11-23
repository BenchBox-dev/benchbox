# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

from benchbox.base import BaseBenchmark, GeneratorOutputDirMixin
from benchbox.core.joinorder.schema import JoinOrderSchema
from benchbox.utils.clock import elapsed_seconds, mono_time

from .generator import JoinOrderGenerator
from .queries import JoinOrderQueryManager

if TYPE_CHECKING:
    from benchbox.core.dataframe.query import QueryRegistry
    from benchbox.core.tuning import UnifiedTuningConfiguration


class JoinOrderSyntheticBenchmark(GeneratorOutputDirMixin, BaseBenchmark):
    csv_delimiter = ","
    csv_null_marker = ""

    OUTPUT_DIR_GENERATOR_ATTRS = ("_generator",)

    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Union[str, Path] | None = None,
        queries_dir: str | None = None,
        verbose: int | bool = 0,
        *,
        parallel: int = 1,
        force_regenerate: bool = False,
        **kwargs: Any,
    ) -> None:
        if not isinstance(parallel, int) or parallel < 1:
            raise ValueError(f"parallel must be a positive integer, got {parallel}")

        from benchbox.core.benchmark_registry import validate_scale_factor

        validate_scale_factor("joinorder_synthetic", scale_factor)

        quiet = kwargs.pop("quiet", False)

        if output_dir is None:
            from benchbox.utils.path_utils import get_benchmark_runs_datagen_path

            output_dir = get_benchmark_runs_datagen_path("joinorder_synthetic", scale_factor)

        super().__init__(
            scale_factor=scale_factor,
            output_dir=output_dir,
            verbose=verbose,
            quiet=quiet,
        )

        self.parallel = parallel
        self.force_regenerate = force_regenerate

        self.queries_dir = queries_dir
        self._schema = JoinOrderSchema()
        self._query_manager = JoinOrderQueryManager(queries_dir)
        self._dataframe_registry: QueryRegistry | None = None

        generator_kwargs: dict[str, Any] = {
            "force_regenerate": force_regenerate,
            **kwargs,
        }

        self._generator = JoinOrderGenerator(
            scale_factor=scale_factor,
            output_dir=self.output_dir,
            verbose=verbose,
            quiet=quiet,
            **generator_kwargs,
        )

    def generate_data(self) -> list[Path]:
        self.log_verbose(f"Generating synthetic Join Order data at scale factor {self.scale_factor}...")

        start_time = mono_time()
        data_files = self._generator.generate_data()
        generation_time = elapsed_seconds(start_time)

        self.log_verbose(f"Generated {len(data_files)} data files in {generation_time:.2f}s")

        return data_files

    def get_schema(self) -> dict[str, dict]:
        return self._schema._tables

    def get_create_tables_sql(
        self,
        dialect: str = "sqlite",
        tuning_config: UnifiedTuningConfiguration | None = None,
    ) -> str:
        return self._schema.get_create_tables_sql(dialect)

    def get_table_names(self) -> list[str]:
        return self._schema.get_table_names()

    def get_query(self, query_id: str, *, params: dict[str, Any] | None = None) -> str:
        if params is not None:
            raise ValueError("JoinOrder queries are static and don't accept parameters")
        return self._query_manager.get_query(query_id)

    def get_queries(self) -> dict[str, str]:
        return self._query_manager.get_all_queries()

    def get_query_ids(self) -> list[str]:
        return self._query_manager.get_query_ids()

    def get_query_count(self) -> int:
        return self._query_manager.get_query_count()

    def get_queries_by_complexity(self) -> dict[str, list[str]]:
        return self._query_manager.get_queries_by_complexity()

    def get_queries_by_pattern(self) -> dict[str, list[str]]:
        return self._query_manager.get_queries_by_pattern()

    def load_queries_from_directory(self, queries_dir: str) -> None:
        self.queries_dir = queries_dir
        self._query_manager = JoinOrderQueryManager(queries_dir)
        self._dataframe_registry = None

    def get_table_info(self, table_name: str) -> dict[str, Any]:
        return self._schema.get_table_info(table_name)

    def get_relationship_tables(self) -> list[str]:
        return self._schema.get_relationship_tables()

    def get_dimension_tables(self) -> list[str]:
        return self._schema.get_dimension_tables()

    def get_estimated_data_size(self) -> int:
        return self._generator.get_total_size_estimate()

    def get_table_row_count(self, table_name: str) -> int:
        return self._generator.get_table_row_count(table_name)

    def validate_query(self, query_id: str) -> bool:
        try:
            query = self.get_query(query_id)
            query_upper = query.upper()
            required_keywords = ["SELECT", "FROM"]
            return all(keyword in query_upper for keyword in required_keywords)
        except Exception:
            return False

    def get_benchmark_info(self) -> dict[str, Any]:
        return {
            "benchmark_name": "Synthetic Join Order Benchmark",
            "description": "Uniformly-random Join Order schema smoke-test benchmark",
            "scale_factor": self.scale_factor,
            "output_dir": str(self.output_dir),
            "queries_dir": self.queries_dir,
            "total_queries": self.get_query_count(),
            "total_tables": len(self.get_table_names()),
            "relationship_tables": len(self.get_relationship_tables()),
            "dimension_tables": len(self.get_dimension_tables()),
            "estimated_size_bytes": self.get_estimated_data_size(),
            "query_complexity_distribution": self.get_queries_by_complexity(),
            "join_pattern_distribution": self.get_queries_by_pattern(),
            "reference_paper": "How Good Are Query Optimizers, Really? (VLDB 2015)",
            "authors": "Viktor Leis, Andrey Gubichev, Atanas Mirchev, Peter Boncz, Alfons Kemper, Thomas Neumann",
        }

    def get_dataframe_queries(self) -> QueryRegistry:
        from benchbox.core.joinorder_synthetic.dataframe_queries import get_dataframe_queries

        if self.queries_dir is None:
            return get_dataframe_queries()
        if self._dataframe_registry is None:
            self._dataframe_registry = get_dataframe_queries(self._query_manager)
        return self._dataframe_registry

    def __repr__(self) -> str:
        return f"JoinOrderSyntheticBenchmark(scale_factor={self.scale_factor}, queries={self.get_query_count()})"


from benchbox.core.hooks.benchmark_hooks import (
    BenchmarkHookRegistry,
    BenchmarkOptionSpec,
)

BenchmarkHookRegistry.register_option_specs(
    "joinorder_synthetic",
    BenchmarkOptionSpec(
        name="queries_dir",
        help="Directory containing custom query files",
        aliases=("queries-dir",),
    ),
    BenchmarkOptionSpec(
        name="force_regenerate",
        parser=lambda v: v.strip().lower() in ("true", "1", "yes"),
        help="Force data regeneration",
        aliases=("force-regenerate",),
    ),
    benchmark_class=JoinOrderSyntheticBenchmark,
)


JoinOrderBenchmark = JoinOrderSyntheticBenchmark
