# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Optional, Union

from benchbox.core.benchmark_result_validation import BenchmarkResultValidationMixin
from benchbox.core.connection import DatabaseConnection
from benchbox.core.contracts import SQLBenchmarkExecutor, as_sql_benchmark_executor
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.cloud_storage import create_path_handler
from benchbox.utils.path_utils import get_benchmark_runs_datagen_path
from benchbox.utils.verbosity import VerbosityMixin, compute_verbosity

BENCHMARK_API_SURFACE = "beta-public"
RUN_WITH_PLATFORM_API_SURFACE = "beta-public"

if TYPE_CHECKING:
    from benchbox.core.results.models import BenchmarkResults


class GeneratorOutputDirMixin:
    OUTPUT_DIR_GENERATOR_ATTRS: tuple[str, ...] = ("data_generator",)

    @property
    def output_dir(self) -> Any:
        return getattr(self, "_output_dir", None)

    @output_dir.setter
    def output_dir(self, value: Optional[Union[str, Path]]) -> None:
        self._output_dir = None if value is None else create_path_handler(value)
        self._sync_output_dir_to_generators(self._output_dir)
        impl = self.__dict__.get("_impl")
        if impl is not None and hasattr(impl, "output_dir"):
            impl.output_dir = value

    def _sync_output_dir_to_generators(self, path: Any) -> None:
        for attr in self.OUTPUT_DIR_GENERATOR_ATTRS:
            generator = self.__dict__.get(attr)
            if generator is None or not hasattr(generator, "output_dir"):
                continue
            generator.output_dir = path
            nested = getattr(generator, "tpch_generator", None)
            if nested is not None and hasattr(nested, "output_dir"):
                nested.output_dir = path


class BaseBenchmark(BenchmarkResultValidationMixin, VerbosityMixin, ABC):
    api_surface = BENCHMARK_API_SURFACE
    run_with_platform_api_surface = RUN_WITH_PLATFORM_API_SURFACE

    DATA_SOURCE_BENCHMARK: ClassVar[Optional[str]] = None

    SKIP_DATA_LOADING: ClassVar[bool] = False

    DATAFRAME_WORKLOAD_USES_PLATFORM_ENGINE: ClassVar[bool] = True

    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Optional[Union[str, Path]] = None,
        **kwargs: Any,
    ) -> None:
        if scale_factor <= 0:
            raise ValueError("Scale factor must be positive")

        if scale_factor >= 1 and scale_factor != int(scale_factor):
            raise ValueError(
                f"Scale factors >= 1 must be whole integers. Got: {scale_factor}. "
                f"Use values like 1, 2, 10, etc. for large scale factors. "
                f"Use values like 0.1, 0.01, 0.001, etc. for small scale factors."
            )

        self.scale_factor = scale_factor

        if output_dir is None:
            benchmark_name = self._get_benchmark_name().lower()
            self.output_dir = get_benchmark_runs_datagen_path(benchmark_name, scale_factor)
        else:
            self.output_dir = create_path_handler(output_dir)

        verbose_value = kwargs.pop("verbose", 0)
        quiet_value = kwargs.pop("quiet", False)
        verbosity_settings = compute_verbosity(verbose_value, quiet_value)
        self.apply_verbosity(verbosity_settings)

        self.logger = logging.getLogger(f"benchbox.core.{self._get_benchmark_name()}")

        for key, value in kwargs.items():
            setattr(self, key, value)

    def cleanup(self) -> None:
        pass

    @property
    def output_dir(self) -> Any:
        return getattr(self, "_output_dir", None)

    @output_dir.setter
    def output_dir(self, value: Optional[Union[str, Path]]) -> None:
        self._output_dir = None if value is None else create_path_handler(value)
        impl = self.__dict__.get("_impl")
        if impl is not None and hasattr(impl, "output_dir"):
            impl.output_dir = value

    def _validate_scale_factor_type(self, scale_factor: float) -> None:
        if not isinstance(scale_factor, (int, float)):
            raise TypeError(f"scale_factor must be a number, got {type(scale_factor).__name__}")

    def _initialize_benchmark_implementation(
        self,
        implementation_class,
        scale_factor: float,
        output_dir: Optional[Union[str, Path]],
        **kwargs,
    ):
        verbose = kwargs.pop("verbose", False)
        force_regenerate = kwargs.pop("force_regenerate", False)

        self._impl = implementation_class(
            scale_factor=scale_factor,
            output_dir=output_dir,
            verbose=verbose,
            force_regenerate=force_regenerate,
            **kwargs,
        )

    @property
    def tables(self) -> dict:
        if hasattr(self, "_impl") and hasattr(self._impl, "tables"):
            return self._impl.tables
        return getattr(self, "_tables", {})

    @tables.setter
    def tables(self, value: dict) -> None:
        if hasattr(self, "_impl"):
            self._impl.tables = value
        else:
            self._tables = value

    @property
    def csv_delimiter(self) -> "str | None":
        if hasattr(self, "_impl") and hasattr(self._impl, "csv_delimiter"):
            return self._impl.csv_delimiter
        return getattr(self, "_csv_delimiter", None)

    @property
    def csv_null_marker(self) -> "str | None":
        if hasattr(self, "_impl") and hasattr(self._impl, "csv_null_marker"):
            return self._impl.csv_null_marker
        return getattr(self, "_csv_null_marker", None)

    @csv_null_marker.setter
    def csv_null_marker(self, value: "str | None") -> None:
        self._csv_null_marker = value

    def get_csv_loading_config(self, table_name: str) -> "list[str] | None":
        if hasattr(self, "_impl") and hasattr(self._impl, "get_csv_loading_config"):
            return self._impl.get_csv_loading_config(table_name)
        return None

    def _get_benchmark_name(self) -> str:
        class_name = self.__class__.__name__

        try:
            from benchbox.core.benchmark_registry import get_benchmark_id_for_class_name
        except ImportError:
            benchmark_id = None
        else:
            benchmark_id = get_benchmark_id_for_class_name(class_name)
        if benchmark_id is not None:
            return benchmark_id

        if class_name.endswith("Benchmark"):
            return class_name[:-9].lower()

        return class_name.lower()

    def get_data_source_benchmark(self) -> Optional[str]:
        if hasattr(self, "_impl") and hasattr(self._impl, "get_data_source_benchmark"):
            return self._impl.get_data_source_benchmark()
        return self.DATA_SOURCE_BENCHMARK

    @abstractmethod
    def generate_data(self) -> list[Union[str, Path]]:
        pass

    @abstractmethod
    def get_queries(self) -> dict[str, str]:
        pass

    @abstractmethod
    def get_query(self, query_id: Union[int, str], *, params: Optional[dict[str, Any]] = None) -> str:
        pass

    def _load_data(self, connection: DatabaseConnection) -> None:
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement _load_data() method to support database execution functionality"
        )

    def setup_database(self, connection: DatabaseConnection) -> None:
        logger = logging.getLogger(__name__)

        try:
            logger.info("Setting up database schema and loading data...")
            start_time = mono_time()

            if not hasattr(self, "_data_generated") or not self._data_generated:
                logger.info("Generating benchmark data...")
                self.generate_data()
                self._data_generated = True

            self._load_data(connection)

            setup_time = elapsed_seconds(start_time)
            logger.info(f"Database setup completed in {setup_time:.2f} seconds")

        except Exception as e:
            logger.error(f"Database setup failed: {str(e)}")
            raise

    def run_query(
        self,
        query_id: Union[int, str],
        connection: DatabaseConnection,
        params: Optional[dict[str, Any]] = None,
        fetch_results: bool = False,
    ) -> dict[str, Any]:
        logger = logging.getLogger(__name__)

        try:
            query_text = self.get_query(query_id, params=params)

            logger.debug(f"Executing query {query_id}")
            start_time = mono_time()

            cursor = connection.execute(query_text)

            results = None
            row_count = 0
            if fetch_results:
                results = connection.fetchall(cursor)
                row_count = len(results) if results else 0

            execution_time = elapsed_seconds(start_time)

            logger.info(f"Query {query_id} completed in {execution_time:.3f} seconds")
            if fetch_results:
                logger.debug(f"Query {query_id} returned {row_count} rows")

            return {
                "query_id": query_id,
                "execution_time_seconds": execution_time,
                "query_text": query_text,
                "results": results,
                "row_count": row_count,
            }

        except Exception as e:
            logger.error(f"Query {query_id} execution failed: {str(e)}")
            raise

    def run_benchmark(
        self,
        connection: DatabaseConnection,
        query_ids: Optional[list[Union[int, str]]] = None,
        fetch_results: bool = False,
        setup_database: bool = True,
    ) -> dict[str, Any]:
        logger = logging.getLogger(__name__)

        benchmark_start_time = mono_time()
        setup_time = 0.0

        try:
            if setup_database:
                logger.info("Setting up database for benchmark...")
                setup_start_time = mono_time()
                self.setup_database(connection)
                setup_time = elapsed_seconds(setup_start_time)

            if query_ids is None:
                all_queries = self.get_queries()
                query_ids = list(all_queries.keys())

            logger.info(f"Running benchmark with {len(query_ids)} queries...")

            query_results = []
            successful_queries = 0
            failed_queries = 0

            for query_id in query_ids:
                try:
                    result = self.run_query(query_id, connection, fetch_results=fetch_results)
                    query_results.append(result)
                    successful_queries += 1

                except Exception as e:
                    logger.error(f"Query {query_id} failed: {str(e)}")
                    failed_queries += 1
                    query_results.append(
                        {
                            "query_id": query_id,
                            "execution_time_seconds": 0.0,
                            "query_text": None,
                            "results": None,
                            "row_count": 0,
                            "error": str(e),
                        }
                    )

            total_execution_time = elapsed_seconds(benchmark_start_time) - setup_time

            query_times = [r["execution_time_seconds"] for r in query_results if "error" not in r]

            benchmark_result = {
                "benchmark_name": self.__class__.__name__,
                "total_execution_time": total_execution_time,
                "total_queries": len(query_ids),
                "successful_queries": successful_queries,
                "failed_queries": failed_queries,
                "query_results": query_results,
                "setup_time": setup_time,
                "average_query_time": sum(query_times) / len(query_times) if query_times else 0.0,
                "min_query_time": min(query_times) if query_times else 0.0,
                "max_query_time": max(query_times) if query_times else 0.0,
            }

            logger.info(f"Benchmark completed: {successful_queries}/{len(query_ids)} queries successful")
            logger.info(f"Total execution time: {total_execution_time:.2f} seconds")

            return benchmark_result

        except Exception as e:
            logger.error(f"Benchmark execution failed: {str(e)}")
            raise

    def run_with_platform(self, platform_adapter: SQLBenchmarkExecutor, **run_config: Any) -> "BenchmarkResults":
        default_benchmark_type = self._get_default_benchmark_type()
        run_config.setdefault("benchmark_type", default_benchmark_type)

        return as_sql_benchmark_executor(platform_adapter).run_benchmark(self, **run_config)

    def _get_default_benchmark_type(self) -> str:
        return "olap"

    def format_results(self, benchmark_result: dict[str, Any]) -> str:
        lines = []
        lines.append(f"Benchmark: {benchmark_result['benchmark_name']}")
        lines.append("=" * 50)

        lines.append(f"Total Queries: {benchmark_result['total_queries']}")
        lines.append(f"Successful: {benchmark_result['successful_queries']}")
        lines.append(f"Failed: {benchmark_result['failed_queries']}")

        if benchmark_result["setup_time"] > 0:
            lines.append(f"Setup Time: {benchmark_result['setup_time']:.2f}s")

        lines.append(f"Total Execution Time: {benchmark_result['total_execution_time']:.2f}s")
        lines.append(f"Average Query Time: {benchmark_result['average_query_time']:.3f}s")
        lines.append(f"Min Query Time: {benchmark_result['min_query_time']:.3f}s")
        lines.append(f"Max Query Time: {benchmark_result['max_query_time']:.3f}s")

        lines.append("\nQuery Details:")
        lines.append("-" * 30)

        for result in benchmark_result["query_results"]:
            query_id = result["query_id"]
            if "error" in result:
                lines.append(f"Query {query_id}: FAILED - {result['error']}")
            else:
                exec_time = result["execution_time_seconds"]
                row_count = result["row_count"]
                lines.append(f"Query {query_id}: {exec_time:.3f}s ({row_count} rows)")

        return "\n".join(lines)

    def _format_time(self, seconds: float) -> str:
        if seconds < 1:
            return f"{seconds * 1000:.1f}ms"
        elif seconds < 60:
            return f"{seconds:.2f}s"
        else:
            minutes = int(seconds // 60)
            remaining_seconds = seconds % 60
            return f"{minutes}m {remaining_seconds:.1f}s"

    def translate_query(self, query_id: Union[int, str], dialect: str) -> str:
        from benchbox.utils.dialect_utils import SQLTranslationError, translate_sql_query

        query = self.get_query(query_id)

        try:
            return translate_sql_query(query, dialect, source_dialect="postgres", identify=True, strict=True)
        except SQLTranslationError as e:
            raise ValueError(f"Error translating to dialect '{dialect}': {e}") from e

    @property
    def benchmark_name(self) -> str:
        if hasattr(self, "_impl"):
            if hasattr(self._impl, "_name"):
                return self._impl._name
            elif hasattr(self._impl, "benchmark_name"):
                return self._impl.benchmark_name
        return getattr(self, "_name", type(self).__name__)

    def _create_result_builder(
        self,
        platform: str,
        execution_metadata: dict[str, Any],
        normalize_benchmark_id: Any,
        **kwargs: Any,
    ) -> Any:
        from benchbox.core.results.builder import (
            BenchmarkInfoInput,
            ResultBuilder,
            RunConfigInput,
        )
        from benchbox.core.results.platform_info import PlatformInfoInput

        benchmark_name = self.benchmark_name
        short_name = benchmark_name[:-10] if benchmark_name.lower().endswith(" benchmark") else benchmark_name
        benchmark_id = execution_metadata.get("benchmark_id") or normalize_benchmark_id(benchmark_name)

        platform_info = kwargs.get("platform_info", {}) or {}
        platform_input = PlatformInfoInput(
            name=platform,
            platform_version=platform_info.get("platform_version") or platform_info.get("version") or "unknown",
            client_library_version=platform_info.get("client_library_version")
            or platform_info.get("client_version")
            or "unknown",
            execution_mode=execution_metadata.get("mode", "sql"),
            config=platform_info if isinstance(platform_info, dict) else {},
        )

        builder = ResultBuilder(
            benchmark=BenchmarkInfoInput(
                name=short_name,
                scale_factor=self.scale_factor,
                test_type=kwargs.get("test_execution_type", "standard"),
                benchmark_id=benchmark_id,
                display_name=short_name,
                compliance_class=getattr(self, "compliance_class", None),
            ),
            platform=platform_input,
            execution_id=kwargs.get("execution_id"),
        )

        run_cfg = execution_metadata.get("run_config") if isinstance(execution_metadata, dict) else None
        if isinstance(run_cfg, dict):
            compression = run_cfg.get("compression") or {}
            builder.set_run_config(
                RunConfigInput(
                    compression_type=compression.get("type"),
                    compression_level=compression.get("level"),
                    seed=run_cfg.get("seed"),
                    phases=run_cfg.get("phases"),
                    query_subset=run_cfg.get("query_subset"),
                    tuning_mode=run_cfg.get("tuning_mode"),
                    tuning_config=run_cfg.get("tuning_config"),
                    platform_options=run_cfg.get("platform_options"),
                    table_mode=run_cfg.get("table_mode"),
                    execution_engine=run_cfg.get("execution_engine"),
                )
            )

        return builder

    def _populate_builder(
        self,
        builder: Any,
        execution_metadata: dict[str, Any],
        query_results: list[dict[str, Any]],
        normalize_query_result: Any,
        duration_seconds: Optional[float] = None,
        **kwargs: Any,
    ) -> None:
        for qr in query_results:
            builder.add_query_result(normalize_query_result(qr))

        table_statistics = kwargs.get("table_statistics", {}) or {}
        for table_name, row_count in table_statistics.items():
            builder.add_table_stats(table_name, row_count)

        if kwargs.get("data_loading_time"):
            builder.set_loading_time(float(kwargs.get("data_loading_time", 0.0)) * 1000)
        if duration_seconds is not None:
            builder.set_total_duration(duration_seconds)

        builder.set_execution_metadata(execution_metadata)
        builder.set_validation_status(kwargs.get("validation_status", "UNKNOWN"), kwargs.get("validation_details", {}))
        builder.set_system_profile(kwargs.get("system_profile", {}))
        builder.set_tuning_info(
            tunings_applied=kwargs.get("tunings_applied"),
            config_hash=kwargs.get("tuning_config_hash"),
            source_file=kwargs.get("tuning_source_file"),
            source=kwargs.get("tuning_source"),
        )
        builder.add_plan_capture_stats(
            kwargs.get("query_plans_captured", 0),
            kwargs.get("plan_capture_failures", 0),
            kwargs.get("plan_capture_errors", []),
        )

    def _apply_phases_to_builder(self, builder: Any, phases: Any, ExecutionPhases: type) -> None:
        phases_obj = phases if isinstance(phases, ExecutionPhases) else None
        if phases_obj:
            builder.set_execution_phases(phases_obj)
        if isinstance(phases, dict):
            for phase_name, phase_data in phases.items():
                if isinstance(phase_data, dict):
                    extra = {k: v for k, v in phase_data.items() if k not in ("status", "duration_ms")}
                    builder.set_phase_status(
                        phase_name,
                        phase_data.get("status", "NOT_RUN"),
                        phase_data.get("duration_ms"),
                        **extra,
                    )

    def _attach_performance_snapshot(
        self,
        result: "BenchmarkResults",
        performance_characteristics: Optional[dict[str, Any]],
        **kwargs: Any,
    ) -> None:
        snapshot_payload = kwargs.get("performance_snapshot")
        if snapshot_payload is not None:
            try:
                from benchbox.monitoring.performance import PerformanceSnapshot, attach_snapshot_to_result

                if isinstance(snapshot_payload, PerformanceSnapshot):
                    attach_snapshot_to_result(result, snapshot_payload)
                elif isinstance(snapshot_payload, dict):
                    result.performance_summary = dict(snapshot_payload)
                    if not result.performance_characteristics:
                        result.performance_characteristics = dict(snapshot_payload)
            except ImportError:
                if isinstance(snapshot_payload, dict):
                    result.performance_summary = dict(snapshot_payload)
                    if not result.performance_characteristics:
                        result.performance_characteristics = dict(snapshot_payload)
        elif performance_characteristics and not result.performance_summary:
            result.performance_summary = dict(performance_characteristics)
