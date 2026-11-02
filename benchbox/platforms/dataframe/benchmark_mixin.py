# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import inspect
import logging
from abc import abstractmethod
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import DEFAULT

from benchbox.core.benchmark_loader import COMPLIANCE_GATED_BENCHMARKS
from benchbox.core.constants import (
    GENERIC_POWER_DEFAULT_MEASUREMENT_ITERATIONS,
    GENERIC_POWER_DEFAULT_WARMUP_ITERATIONS,
)
from benchbox.core.dataframe import (
    check_sufficient_memory,
    format_memory_warning,
    validate_scale_factor,
)
from benchbox.core.dataframe.data_loader import DataFrameDataLoader, get_tpch_column_names
from benchbox.core.dataframe.load_validation import (
    combine_dataframe_validation,
    validate_dataframe_loaded_tables,
    validate_managed_dataframe_tables,
)
from benchbox.core.dataframe.profiling import PlanCaptureSupport, plan_capture_support
from benchbox.core.dataframe.query_resolution import (
    benchmark_provides_dataframe_queries,
    build_dataframe_query_filter_from_config,
    get_dataframe_queries_for_benchmark,
    get_tpcds_dataframe_queries,
    get_tpch_dataframe_queries,
)
from benchbox.core.dataframe.query_validation import DataFrameQueryValidationSummary, validate_dataframe_query_results
from benchbox.core.dataframe.schema_utils import get_benchmark_schema_columns
from benchbox.core.errors import PlanCaptureError
from benchbox.core.exceptions import ConfigurationError, InsufficientMemoryError
from benchbox.core.execution_engine import DEFAULT_EXECUTION_ENGINE, ExecutionEngineHook
from benchbox.core.results import (
    BenchmarkInfoInput,
    ResultBuilder,
    build_platform_info,
    normalize_query_result,
)
from benchbox.core.results.builder import RunConfigInput, normalize_benchmark_id
from benchbox.core.results.environment import system_profile_snapshot
from benchbox.core.results.models import (
    BenchmarkResults,
    TableLoadingStats,
)
from benchbox.core.results.query_plan_models import QueryPlanDAG
from benchbox.core.results.schema import compute_plan_capture_stats
from benchbox.core.schemas import BenchmarkConfig, SystemProfile
from benchbox.platforms.base.adapter import DriverIsolationCapability
from benchbox.platforms.base.client_region import discover_client_region
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.printing import quiet_console

if TYPE_CHECKING:
    from benchbox.core.dataframe.query import DataFrameQuery

logger = logging.getLogger(__name__)

console = quiet_console


def _benchmark_defines_hook(benchmark: Any | None, hook_name: str) -> bool:
    if benchmark is None:
        return False

    hook = type(benchmark).__dict__.get(hook_name)
    if callable(hook):
        return True

    instance_hook = benchmark.__dict__.get(hook_name)
    if callable(instance_hook):
        return True

    mock_children = getattr(benchmark, "_mock_children", None)
    if not isinstance(mock_children, dict):
        return False

    child = mock_children.get(hook_name)
    if child is None:
        return False

    return (
        getattr(child, "_mock_return_value", DEFAULT) is not DEFAULT
        or getattr(child, "_mock_side_effect", None) is not None
        or getattr(child, "_mock_wraps", None) is not None
    )


def _benchmark_manages_dataframe_loading(benchmark: Any | None) -> bool:
    if not _benchmark_defines_hook(benchmark, "skip_dataframe_data_loading"):
        return False

    return bool(benchmark.skip_dataframe_data_loading())


def _benchmark_supports_dataframe_workload(benchmark: Any | None) -> bool:
    return _benchmark_defines_hook(benchmark, "execute_dataframe_workload")


def _benchmark_provides_dataframe_queries(benchmark: Any | None) -> bool:
    return benchmark_provides_dataframe_queries(benchmark)


def dataframe_compliance_class(benchmark_instance: object | None, benchmark_config: object) -> str | None:
    from_instance = getattr(benchmark_instance, "compliance_class", None)
    if from_instance is not None:
        return _compliance_value(from_instance)

    name = str(getattr(benchmark_config, "name", "") or "").lower()
    if name not in COMPLIANCE_GATED_BENCHMARKS:
        return None
    if name == "tpch":
        from benchbox.core.tpch.compliance import classify_tpch_run

        classify = classify_tpch_run
    else:
        from benchbox.core.tpcds.compliance import classify_tpcds_run

        classify = classify_tpcds_run

    return _compliance_value(
        classify(
            float(getattr(benchmark_config, "scale_factor", 0) or 0),
            official=bool(getattr(benchmark_config, "official", False)),
        )
    )


def _compliance_value(compliance_class: object) -> str:
    return str(getattr(compliance_class, "value", compliance_class))


def no_dataframe_queries_message(benchmark_id: str, query_filter: set[str] | None) -> str:
    if query_filter:
        return (
            f"No DataFrame queries matched {sorted(query_filter)} for benchmark {benchmark_id!r}. "
            "Check the --queries selection against the benchmark's query IDs."
        )
    return (
        f"Benchmark {benchmark_id!r} has no DataFrame query source, so DataFrame mode would "
        "execute nothing. Run it in SQL mode instead -- use the platform's plain name rather "
        "than its -df alias -- or choose a benchmark with DataFrame support."
    )


@dataclass
class DataFramePhases:
    load: bool = True
    execute: bool = True


@dataclass
class DataFrameRunOptions:
    ignore_memory_warnings: bool = False
    force_regenerate: bool = False
    prefer_parquet: bool = True
    cache_dir: Path | None = None
    verbose: bool = False
    very_verbose: bool = False


class DataLoadingError(RuntimeError):
    def __init__(
        self,
        message: str,
        table_stats: dict[str, int] | None = None,
        per_table_stats: dict[str, TableLoadingStats] | None = None,
    ) -> None:
        super().__init__(message)
        self.table_stats = table_stats or {}
        self.per_table_stats = per_table_stats or {}


def _describe_query_parameters(benchmark_config: Any) -> str | None:
    from benchbox.core.tpch.dataframe_queries import TPCH_FAMILY_DATAFRAME_IDS

    benchmark_id = normalize_benchmark_id(benchmark_config.name)
    if benchmark_id in TPCH_FAMILY_DATAFRAME_IDS:
        from benchbox.core.tpch.benchmark import describe_query_parameters

        seed = (getattr(benchmark_config, "options", {}) or {}).get("seed")
        return describe_query_parameters(None if seed is None else int(seed))
    if benchmark_id != "tpcds":
        return None
    from benchbox.core.tpcds.dataframe_queries.production_binding import (
        describe_query_parameters,
        run_seed,
        selected_query_numbers,
    )

    return describe_query_parameters(run_seed(benchmark_config), selected_query_numbers(benchmark_config))


def _client_host_profile(system_profile: Any) -> dict[str, Any]:
    if system_profile is not None:
        return system_profile_snapshot(system_profile)
    from benchbox.utils.system_info import get_system_info

    return get_system_info().to_dict()


def _client_link_block_for_dataframe(benchmark_config: Any, options_map: dict[str, Any]) -> dict[str, Any] | None:
    client_config = {
        **options_map,
        "client_region": getattr(benchmark_config, "client_region", None) or options_map.get("client_region"),
        "client_cloud": getattr(benchmark_config, "client_cloud", None) or options_map.get("client_cloud"),
    }
    try:
        region_info = discover_client_region(client_config)
    except Exception as exc:
        logger.warning("DataFrame client-link discovery failed: %r", exc)
        return None
    if not region_info.get("client_region") and not region_info.get("client_cloud"):
        return None
    return {
        "collection_status": "partial",
        "source": region_info.get("source", "unavailable"),
        "client_region": region_info.get("client_region"),
        "client_cloud": region_info.get("client_cloud"),
        "statement_overhead_ms": None,
        "collection_error_class": None,
        "collection_error_message": None,
    }


class BenchmarkExecutionMixin(ExecutionEngineHook):
    driver_isolation_capability: DriverIsolationCapability = DriverIsolationCapability.NOT_APPLICABLE
    is_dataframe_adapter: bool = True

    platform_name: str
    family: str

    @abstractmethod
    def create_context(self) -> Any:
        pass

    @abstractmethod
    def load_table(
        self,
        ctx: Any,
        table_name: str,
        file_paths: list[Path],
        column_names: list[str] | None = None,
        delimiter: str | None = None,
        format_hint: str | None = None,
        *,
        data_source: Any | None = None,
        benchmark: Any | None = None,
    ) -> int:
        pass

    @abstractmethod
    def execute_query(
        self,
        ctx: Any,
        query: DataFrameQuery,
        query_id: str | None = None,
    ) -> dict[str, Any]:
        pass

    def get_platform_info(self) -> dict[str, Any]:
        return {"platform": self.platform_name, "family": self.family}

    @property
    def collect_engine(self) -> str:
        return DEFAULT_EXECUTION_ENGINE

    def _enrich_with_driver_metadata(self, info: dict[str, Any]) -> None:
        for attr in (
            "driver_version_requested",
            "driver_version_resolved",
            "driver_version_actual",
            "driver_runtime_strategy",
        ):
            value = getattr(self, attr, None)
            if value:
                info[attr] = value

    def run_benchmark(
        self,
        benchmark: Any,
        *,
        benchmark_config: BenchmarkConfig | None = None,
        system_profile: SystemProfile | None = None,
        data_dir: Path | None = None,
        phases: DataFramePhases | None = None,
        options: DataFrameRunOptions | None = None,
        monitor: Any | None = None,
        **run_config: Any,
    ) -> BenchmarkResults:
        phases = phases or DataFramePhases()
        if isinstance(options, dict):
            options = DataFrameRunOptions(**options)
        else:
            options = options or DataFrameRunOptions()

        if benchmark_config is None:
            benchmark_config = self._extract_benchmark_config(benchmark, run_config)

        platform_name = self.platform_name
        scale_factor = getattr(benchmark_config, "scale_factor", 1.0)

        logger.info(f"Starting DataFrame benchmark: {benchmark_config.name}")
        logger.info(f"Platform: {platform_name}, Scale Factor: {scale_factor}")

        platform_info = build_platform_info(self, execution_mode="dataframe")

        builder = ResultBuilder(
            benchmark=BenchmarkInfoInput(
                name=benchmark_config.name,
                scale_factor=scale_factor,
                test_type=getattr(benchmark_config, "test_execution_type", "power"),
                benchmark_id=normalize_benchmark_id(benchmark_config.name),
                display_name=getattr(benchmark_config, "display_name", benchmark_config.name),
                compliance_class=dataframe_compliance_class(benchmark, benchmark_config),
            ),
            platform=platform_info,
        )
        builder.mark_started()
        builder.set_validation_status("NOT_RUN")
        builder.set_system_profile(_client_host_profile(system_profile))

        self.plan_capture_errors: list[dict[str, Any]] = []
        self._plan_capture_warning_emitted = False
        self.strict_plan_capture = bool(getattr(benchmark_config, "strict_plan_capture", False))

        options_map = getattr(benchmark_config, "options", {}) or {}
        self.normalize_plan_literals = bool(options_map.get("normalize_plan_literals", False))
        builder.set_run_config(
            RunConfigInput(
                compression_type=options_map.get("compression_type"),
                compression_level=options_map.get("compression_level"),
                seed=options_map.get("seed"),
                phases=options_map.get("phases"),
                query_subset=getattr(benchmark_config, "queries", None),
                tuning_mode=options_map.get("tuning_mode"),
                tuning_config=options_map.get("df_tuning_config"),
                query_parameters=_describe_query_parameters(benchmark_config),
                execution_engine=self.requested_execution_engine,
            )
        )

        client_link = _client_link_block_for_dataframe(benchmark_config, options_map)
        if client_link is not None:
            builder.set_execution_environment({"client_link": client_link})

        query_results: list[dict[str, Any]] = []
        table_stats: dict[str, int] = {}
        load_validation: DataFrameQueryValidationSummary | None = None
        load_time = 0.0

        try:
            if not options.ignore_memory_warnings:
                memory_check = check_sufficient_memory(benchmark_config.name, scale_factor, platform_name.lower())
                if not memory_check.is_safe:
                    warning = format_memory_warning(memory_check)
                    raise InsufficientMemoryError(warning)

            is_valid, sf_warning = validate_scale_factor(scale_factor, platform_name.lower())
            if sf_warning:
                logger.warning(sf_warning)

            ctx = self.create_context()

            skip_data_loading = _benchmark_manages_dataframe_loading(benchmark)

            if phases.load and not skip_data_loading:
                load_start = mono_time()
                try:
                    table_stats, per_table_stats = self._load_data_phase(
                        ctx=ctx,
                        benchmark_config=benchmark_config,
                        benchmark_instance=benchmark,
                        data_dir=data_dir,
                        options=options,
                    )
                    load_time = elapsed_seconds(load_start)
                    logger.info(f"Data loading completed in {load_time:.2f}s")

                    for table_name, stats in per_table_stats.items():
                        builder.add_table_stats(
                            table_name,
                            stats.rows,
                            load_time_ms=stats.load_time_ms,
                            status=stats.status,
                            error_message=stats.error_message,
                        )
                    builder.set_loading_time(load_time * 1000)
                    builder.set_phase_status("data_loading", "COMPLETED", load_time * 1000)

                    if str(options_map.get("validation_mode") or "exact").strip().lower() != "disabled":
                        load_validation = validate_dataframe_loaded_tables(table_stats, per_table_stats, benchmark)

                except DataLoadingError as load_err:
                    load_time = elapsed_seconds(load_start)

                    for table_name, stats in load_err.per_table_stats.items():
                        builder.add_table_stats(
                            table_name,
                            stats.rows,
                            load_time_ms=stats.load_time_ms,
                            status=stats.status,
                            error_message=stats.error_message,
                        )
                    builder.set_loading_time(load_time * 1000)
                    builder.set_validation_status(
                        "FAILED",
                        {"error": str(load_err), "phase": "data_loading"},
                    )
                    builder.set_phase_status("data_loading", "FAILED")
                    self._attach_applied_tuning_ledger(builder)
                    builder.mark_completed()
                    return builder.build()
            elif phases.load and skip_data_loading:
                logger.info("Skipping DataFrame table loading for benchmark-managed workload")
                builder.set_phase_status("data_loading", "SKIPPED")

            if phases.execute:
                query_results = self._execute_queries_phase(
                    ctx=ctx,
                    benchmark_config=benchmark_config,
                    benchmark_instance=benchmark,
                    monitor=monitor,
                    run_options=options,
                )

                validation_summary = validate_dataframe_query_results(
                    query_results,
                    benchmark_name=benchmark_config.name,
                    scale_factor=scale_factor,
                    validation_mode=options_map.get("validation_mode"),
                    seed=options_map.get("seed"),
                )
                load_validation = self._managed_load_validation(
                    load_validation, benchmark, ctx, options_map, managed=skip_data_loading and phases.load
                )
                validation_summary = combine_dataframe_validation(validation_summary, load_validation)
                builder.set_validation_status(validation_summary.status, validation_summary.details)
                if (
                    load_validation is not None
                    or validation_summary.status in {"PARTIAL", "FAILED"}
                    or validation_summary.details.get("checked")
                    or validation_summary.details.get("errors")
                ):
                    builder.set_phase_status("validation", validation_summary.status)

                for qr in query_results:
                    builder.add_query_result(normalize_query_result(qr))

                plans_captured, capture_failures, capture_errors = compute_plan_capture_stats(
                    query_results,
                    getattr(benchmark_config, "capture_plans", False),
                    existing_errors=list(getattr(self, "plan_capture_errors", [])) or None,
                )
                builder.add_plan_capture_stats(
                    plans_captured=plans_captured,
                    capture_failures=capture_failures,
                    capture_errors=capture_errors,
                )
                builder.set_phase_status("power_test", "COMPLETED")

            self._attach_applied_tuning_ledger(builder)
            builder.mark_completed()
            return builder.build()

        except PlanCaptureError:
            raise
        except Exception as e:
            logger.error(f"DataFrame benchmark failed: {e}")
            builder.set_validation_status(
                "FAILED",
                {"error": str(e), "error_type": type(e).__name__},
            )
            builder.add_execution_metadata("error", str(e))
            self._attach_applied_tuning_ledger(builder)
            builder.mark_completed()
            return builder.build()

    def _managed_load_validation(
        self, load_validation: Any, benchmark: Any, ctx: Any, options_map: Any, *, managed: bool
    ) -> Any:
        if load_validation is not None or not managed:
            return load_validation
        if str(options_map.get("validation_mode") or "exact").strip().lower() == "disabled":
            return None
        return validate_managed_dataframe_tables(benchmark, ctx, self)

    def _attach_applied_tuning_ledger(self, builder: Any) -> None:
        writer = getattr(self, "_write_applied_tuning_ledger", None)
        if callable(writer):
            writer(builder)

    def load_benchmark_into_context(
        self,
        benchmark: Any,
        data_dir: Path,
        *,
        scale_factor: float | None = None,
        options: DataFrameRunOptions | None = None,
    ) -> Any:
        if _benchmark_manages_dataframe_loading(benchmark):
            raise ValueError(
                f"{type(benchmark).__name__} manages its own DataFrame loading "
                "(skip_dataframe_data_loading); load_benchmark_into_context loads data in a "
                "discrete phase and does not apply - drive such a benchmark through run_benchmark."
            )
        ctx = self.create_context()
        run_config: dict[str, Any] = {}
        if scale_factor is not None:
            run_config["scale_factor"] = scale_factor
        config = self._extract_benchmark_config(benchmark, run_config)
        self._load_data_phase(ctx, config, benchmark, Path(data_dir), options or DataFrameRunOptions())
        return ctx

    def _extract_benchmark_config(self, benchmark: Any, run_config: dict[str, Any]) -> BenchmarkConfig:
        if hasattr(benchmark, "config") and isinstance(benchmark.config, BenchmarkConfig):
            return benchmark.config

        name = getattr(benchmark, "_name", getattr(benchmark, "name", type(benchmark).__name__.lower()))
        display_name = getattr(benchmark, "display_name", name.upper())
        scale_factor = run_config.get("scale_factor", getattr(benchmark, "scale_factor", 1.0))
        queries = run_config.get("query_subset", run_config.get("queries"))
        capture_plans = bool(run_config.get("capture_plans", False))

        return BenchmarkConfig(
            name=name,
            display_name=display_name,
            scale_factor=scale_factor,
            queries=queries,
            capture_plans=capture_plans,
        )

    @staticmethod
    def _normalize_table_paths(tables: dict[str, Any]) -> dict[str, Path | list[Path]]:
        normalized: dict[str, Path | list[Path]] = {}
        for name, path in tables.items():
            if isinstance(path, list):
                normalized[name] = [Path(p) if not isinstance(p, Path) else p for p in path]
            elif isinstance(path, Path):
                normalized[name] = path
            else:
                normalized[name] = Path(path)
        return normalized

    @staticmethod
    def _find_missing_paths(paths: dict[str, Path | list[Path]]) -> dict[str, list[Path]]:
        missing: dict[str, list[Path]] = {}
        for table_name, file_path in paths.items():
            file_list = file_path if isinstance(file_path, list) else [file_path]
            for entry in file_list:
                entry_path = entry if isinstance(entry, Path) else Path(entry)
                if not entry_path.exists():
                    missing.setdefault(table_name, []).append(entry_path)
        return missing

    @staticmethod
    def _raise_missing_paths_error(missing_paths: dict[str, list[Path]]) -> None:
        per_table_stats: dict[str, TableLoadingStats] = {}
        for table_name, paths in missing_paths.items():
            missing_list = ", ".join(str(p) for p in paths[:5])
            more = "..." if len(paths) > 5 else ""
            per_table_stats[table_name] = TableLoadingStats(
                rows=0,
                load_time_ms=0,
                status="FAILED",
                error_type="MissingDataError",
                error_message=f"Missing data files: {missing_list}{more}",
            )
        missing_summary = "; ".join(f"{t}: {len(p)} file(s)" for t, p in missing_paths.items())
        raise DataLoadingError(
            "Data files not found. Missing: "
            f"{missing_summary}. Run with --phases generate,load or --force-regenerate to recreate data.",
            per_table_stats=per_table_stats,
        )

    def _resolve_data_paths_from_tables(
        self,
        benchmark_instance: Any,
        options: DataFrameRunOptions,
    ) -> dict[str, Path | list[Path]]:
        parquet_dir = None
        if options.prefer_parquet:
            first_path = next(iter(benchmark_instance.tables.values()))
            if isinstance(first_path, list):
                first_path = first_path[0]
            first_path = Path(first_path)
            parent_dir = first_path.parent
            for pq_candidate in parent_dir.glob("*.parquet"):
                if pq_candidate.is_dir():
                    parquet_dir = pq_candidate
                    logger.info(f"Found parquet directory: {parquet_dir}")
                    break

        if parquet_dir and parquet_dir.is_dir():
            data_paths: dict[str, Path | list[Path]] = {}
            for pq_file in parquet_dir.glob("*.parquet"):
                table_name = pq_file.stem
                data_paths[table_name] = pq_file
            logger.info(f"Using parquet data: {len(data_paths)} tables from {parquet_dir}")
            return data_paths

        data_paths = self._normalize_table_paths(benchmark_instance.tables)
        logger.info(f"Using benchmark-provided data: {len(data_paths)} tables")
        return data_paths

    def _resolve_data_paths(
        self,
        benchmark_instance: Any | None,
        data_dir: Path | None,
        options: DataFrameRunOptions,
        loader: DataFrameDataLoader,
    ) -> dict[str, Path | list[Path]]:
        data_paths: dict[str, Path | list[Path]] = {}

        if options.force_regenerate and benchmark_instance and hasattr(benchmark_instance, "generate_data"):
            data_paths = self._generate_benchmark_data(benchmark_instance)

        if benchmark_instance and hasattr(benchmark_instance, "tables") and benchmark_instance.tables:
            data_paths = self._resolve_data_paths_from_tables(benchmark_instance, options)
        elif data_dir and data_dir.exists():
            data_paths = loader._discover_files(data_dir)
            logger.info(f"Discovered data in {data_dir}: {len(data_paths)} tables")
            if not data_paths and benchmark_instance and hasattr(benchmark_instance, "generate_data"):
                data_paths = self._generate_benchmark_data(benchmark_instance)
        elif benchmark_instance and hasattr(benchmark_instance, "generate_data"):
            try:
                data_paths = self._generate_benchmark_data(benchmark_instance)
                if data_paths:
                    logger.info(f"Generated data: {len(data_paths)} tables")
            except Exception as gen_err:
                logger.warning(f"Data generation failed: {gen_err}")

        return data_paths

    def _generate_benchmark_data(self, benchmark_instance: Any) -> dict[str, Path | list[Path]]:
        logger.info("Generating benchmark data...")
        benchmark_instance.generate_data()
        if hasattr(benchmark_instance, "tables") and benchmark_instance.tables:
            return self._normalize_table_paths(benchmark_instance.tables)
        return {}

    def _resolve_column_names_and_delimiter(
        self,
        benchmark_config: BenchmarkConfig,
        benchmark_instance: Any | None,
    ) -> tuple[dict[str, list[str]], str | None]:
        benchmark_id = normalize_benchmark_id(benchmark_config.name)
        if benchmark_id != "tpch" and benchmark_instance is not None:
            source_benchmark = getattr(benchmark_instance, "get_data_source_benchmark", lambda: None)()
            if source_benchmark == "tpch":
                benchmark_id = "tpch"
        column_names_map = {
            table_name: [column["name"] for column in columns]
            for table_name, columns in get_benchmark_schema_columns(benchmark_instance).items()
        }
        if benchmark_id == "tpch":
            column_names_map.update(get_tpch_column_names())

        csv_delimiter = getattr(benchmark_instance, "csv_delimiter", None)
        if not isinstance(csv_delimiter, str):
            csv_delimiter = None

        return column_names_map, csv_delimiter

    def _load_tables_into_context(
        self,
        ctx: Any,
        data_paths: dict[str, Path | list[Path]],
        column_names_map: dict[str, list[str]],
        csv_delimiter: str | None,
        benchmark: Any | None = None,
    ) -> tuple[dict[str, int], dict[str, TableLoadingStats]]:
        table_stats: dict[str, int] = {}
        per_table_stats: dict[str, TableLoadingStats] = {}

        for table_name, file_path in data_paths.items():
            load_start = mono_time()
            try:
                column_names = column_names_map.get(table_name.lower())
                file_paths = [file_path] if not isinstance(file_path, list) else file_path
                row_count = self.load_table(
                    ctx,
                    table_name.lower(),
                    file_paths,
                    column_names=column_names,
                    delimiter=csv_delimiter,
                    benchmark=benchmark,
                )
                load_time_ms = int((elapsed_seconds(load_start)) * 1000)

                table_stats[table_name] = row_count
                per_table_stats[table_name] = TableLoadingStats(
                    rows=row_count,
                    load_time_ms=load_time_ms,
                    status="SUCCESS",
                )
                logger.debug(f"Loaded {table_name}: {row_count:,} rows in {load_time_ms}ms")

            except Exception as e:
                load_time_ms = int((elapsed_seconds(load_start)) * 1000)
                per_table_stats[table_name] = TableLoadingStats(
                    rows=0,
                    load_time_ms=load_time_ms,
                    status="FAILED",
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
                logger.error(f"Failed to load {table_name}: {e}")

        failed_tables = [name for name, stats in per_table_stats.items() if stats.status == "FAILED"]
        if failed_tables:
            raise DataLoadingError(
                f"Failed to load {len(failed_tables)} table(s): {', '.join(failed_tables)}.",
                table_stats=table_stats,
                per_table_stats=per_table_stats,
            )

        return table_stats, per_table_stats

    def _load_data_phase(
        self,
        ctx: Any,
        benchmark_config: BenchmarkConfig,
        benchmark_instance: Any | None,
        data_dir: Path | None,
        options: DataFrameRunOptions,
    ) -> tuple[dict[str, int], dict[str, TableLoadingStats]]:
        platform_name = self.platform_name.lower()

        loader = DataFrameDataLoader(
            platform=platform_name,
            cache_dir=options.cache_dir,
            prefer_parquet=options.prefer_parquet,
            force_regenerate=options.force_regenerate,
        )

        options_map = getattr(benchmark_config, "options", {}) or {}
        df_tuning_config = options_map.get("df_tuning_config")
        write_config = getattr(df_tuning_config, "write", None) if df_tuning_config else None

        def _prepare_data_or_raise() -> dict[str, Path | list[Path]]:
            if benchmark_instance is None:
                raise DataLoadingError("Cannot prepare Parquet data without a benchmark instance.")
            try:
                prepared_paths = loader.prepare_benchmark_data(
                    benchmark_instance,
                    scale_factor=getattr(benchmark_config, "scale_factor", 1.0),
                    data_dir=data_dir,
                    write_config=write_config,
                )
            except Exception as prep_err:
                raise DataLoadingError(
                    f"Data preparation failed in prefer_parquet mode: {type(prep_err).__name__}: {prep_err}"
                ) from prep_err
            if not prepared_paths:
                raise DataLoadingError("Data preparation produced no table paths in prefer_parquet mode.")
            logger.info(f"Using prepared data: {len(prepared_paths)} tables")
            return prepared_paths

        data_paths = self._resolve_data_paths(benchmark_instance, data_dir, options, loader)

        if not data_paths:
            raise ValueError("No data source available. Either provide data_dir or generate data first.")

        if options.prefer_parquet:
            data_paths = _prepare_data_or_raise()

        missing_paths = self._find_missing_paths(data_paths)
        if missing_paths and benchmark_instance and hasattr(benchmark_instance, "generate_data"):
            logger.info("Missing data files detected, regenerating benchmark data...")
            data_paths = self._generate_benchmark_data(benchmark_instance)
            if options.prefer_parquet:
                data_paths = _prepare_data_or_raise()
            missing_paths = self._find_missing_paths(data_paths)

        if missing_paths:
            self._raise_missing_paths_error(missing_paths)

        column_names_map, csv_delimiter = self._resolve_column_names_and_delimiter(benchmark_config, benchmark_instance)

        loaded = self._load_tables_into_context(
            ctx, data_paths, column_names_map, csv_delimiter, benchmark=benchmark_instance
        )

        if hasattr(self, "_fold_write_layout_into_ledger"):
            self._fold_write_layout_into_ledger(getattr(loader, "applied_write_layout", None))

        return loaded

    def _execute_queries_phase(
        self,
        ctx: Any,
        benchmark_config: BenchmarkConfig,
        benchmark_instance: Any | None,
        monitor: Any | None,
        run_options: DataFrameRunOptions | None = None,
    ) -> list[dict[str, Any]]:
        from benchbox.core.tpch.dataframe_queries import set_scale_factor_for_benchmark

        benchmark_id = normalize_benchmark_id(benchmark_config.name)
        scale_factor = getattr(benchmark_config, "scale_factor", 1.0)
        set_scale_factor_for_benchmark(benchmark_id, scale_factor)
        try:
            return self._run_query_iterations(
                ctx=ctx,
                benchmark_config=benchmark_config,
                benchmark_instance=benchmark_instance,
                monitor=monitor,
                run_options=run_options,
            )
        finally:
            set_scale_factor_for_benchmark(benchmark_id, None)

    def _run_query_iterations(
        self,
        ctx: Any,
        benchmark_config: BenchmarkConfig,
        benchmark_instance: Any | None,
        monitor: Any | None,
        run_options: DataFrameRunOptions | None = None,
    ) -> list[dict[str, Any]]:
        config_options = getattr(benchmark_config, "options", {}) or {}
        warmup_iterations_raw = config_options.get("power_warmup_iterations")
        measurement_iterations_raw = config_options.get("power_iterations")
        warmup_iterations = (
            GENERIC_POWER_DEFAULT_WARMUP_ITERATIONS if warmup_iterations_raw is None else int(warmup_iterations_raw)
        )
        measurement_iterations = (
            GENERIC_POWER_DEFAULT_MEASUREMENT_ITERATIONS
            if measurement_iterations_raw is None
            else int(measurement_iterations_raw)
        )

        query_filter = self._build_query_filter(benchmark_config)

        if _benchmark_supports_dataframe_workload(benchmark_instance):
            return benchmark_instance.execute_dataframe_workload(
                ctx=ctx,
                adapter=self,
                benchmark_config=benchmark_config,
                query_filter=query_filter,
                monitor=monitor,
                run_options=run_options,
            )

        skip_query_ids = self._collect_skip_query_ids(benchmark_instance)

        initial_queries = self._filter_queries(
            self._get_queries_for_benchmark(benchmark_config, benchmark_instance, stream_id=0),
            skip_query_ids,
            query_filter,
        )

        filtered_skip_query_ids = skip_query_ids
        if query_filter and skip_query_ids:
            filtered_skip_query_ids = {query_id for query_id in skip_query_ids if query_id in query_filter}

        skipped_results = self._build_skipped_results(filtered_skip_query_ids)

        if not initial_queries:
            return self._handle_no_queries(
                skipped_results,
                filtered_skip_query_ids,
                benchmark_id=normalize_benchmark_id(benchmark_config.name),
                query_filter=query_filter,
            )

        total_queries = len(initial_queries)
        logger.info(f"Executing {total_queries} queries")
        logger.info(f"Warmup iterations: {warmup_iterations}, Measurement iterations: {measurement_iterations}")

        query_results: list[dict[str, Any]] = list(skipped_results)
        executed_query_ids: list[str] = []

        self._run_warmup_iterations(
            ctx=ctx,
            warmup_iterations=warmup_iterations,
            benchmark_config=benchmark_config,
            benchmark_instance=benchmark_instance,
            skip_query_ids=skip_query_ids,
            query_filter=query_filter,
            query_results=query_results,
        )

        self._run_measurement_iterations(
            ctx=ctx,
            measurement_iterations=measurement_iterations,
            warmup_iterations=warmup_iterations,
            total_queries=total_queries,
            benchmark_config=benchmark_config,
            benchmark_instance=benchmark_instance,
            skip_query_ids=skip_query_ids,
            query_filter=query_filter,
            monitor=monitor,
            run_options=run_options,
            query_results=query_results,
            executed_query_ids=executed_query_ids,
        )

        if filtered_skip_query_ids:
            self._append_skip_summary(query_results, set(executed_query_ids), filtered_skip_query_ids)

        return query_results

    @staticmethod
    def _build_query_filter(benchmark_config: BenchmarkConfig) -> set[str] | None:
        return build_dataframe_query_filter_from_config(benchmark_config)

    def _collect_skip_query_ids(self, benchmark_instance: Any | None) -> set[str]:
        skip_query_ids: set[str] = set()

        if benchmark_instance and hasattr(benchmark_instance, "get_dataframe_skip_queries"):
            raw_skip_ids = benchmark_instance.get_dataframe_skip_queries()
            skip_query_ids = {str(query_id).strip().upper() for query_id in raw_skip_ids}

        if (
            hasattr(self, "family")
            and self.family == "expression"
            and benchmark_instance
            and hasattr(benchmark_instance, "get_expression_family_skip_queries")
        ):
            expr_skip_ids = benchmark_instance.get_expression_family_skip_queries()
            skip_query_ids |= {str(query_id).strip().upper() for query_id in expr_skip_ids}

        if (
            hasattr(self, "platform_name")
            and benchmark_instance
            and hasattr(benchmark_instance, "get_platform_skip_queries")
        ):
            platform_skip_ids = benchmark_instance.get_platform_skip_queries(self.platform_name)
            skip_query_ids |= {str(query_id).strip().upper() for query_id in platform_skip_ids}

        if (
            hasattr(self, "platform_name")
            and benchmark_instance
            and hasattr(benchmark_instance, "get_df_platform_skip_queries")
        ):
            df_skip_ids = benchmark_instance.get_df_platform_skip_queries(self.platform_name)
            skip_query_ids |= {str(query_id).strip().upper() for query_id in df_skip_ids}

        return skip_query_ids

    @staticmethod
    def _filter_queries(
        queries: list[DataFrameQuery],
        skip_query_ids: set[str],
        query_filter: set[str] | None,
    ) -> list[DataFrameQuery]:
        if skip_query_ids:
            queries = [q for q in queries if q.query_id.upper() not in skip_query_ids]
        if query_filter:
            queries = [q for q in queries if q.query_id.upper() in query_filter]
        return queries

    @staticmethod
    def _build_skipped_results(filtered_skip_query_ids: set[str]) -> list[dict[str, Any]]:
        if not filtered_skip_query_ids:
            return []
        return [
            {
                "query_id": query_id,
                "status": "SKIPPED",
                "execution_time_seconds": 0.0,
                "error": "Skipped in DataFrame mode (SQL-only primitive)",
                "run_type": "metadata",
                "stream_id": 0,
                "iteration": 0,
            }
            for query_id in sorted(filtered_skip_query_ids)
        ]

    def _handle_no_queries(
        self,
        skipped_results: list[dict[str, Any]],
        filtered_skip_query_ids: set[str],
        benchmark_id: str = "",
        query_filter: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        logger.warning("No queries found for execution")
        if skipped_results:
            skipped_categories = self._count_query_categories(filtered_skip_query_ids)
            skipped_results.append(
                {
                    "query_id": "DF_SKIP_SUMMARY",
                    "status": "SUCCESS",
                    "execution_time_seconds": 0.0,
                    "run_type": "summary",
                    "stream_id": 0,
                    "iteration": 0,
                    "rows_returned": len(filtered_skip_query_ids),
                    "dataframe_skip_summary": {
                        "executed_total": 0,
                        "skipped_total": len(filtered_skip_query_ids),
                        "executed_by_category": {},
                        "skipped_by_category": skipped_categories,
                    },
                }
            )
            return skipped_results
        raise ConfigurationError(no_dataframe_queries_message(benchmark_id, query_filter))

    def _run_warmup_iterations(
        self,
        ctx: Any,
        warmup_iterations: int,
        benchmark_config: BenchmarkConfig,
        benchmark_instance: Any | None,
        skip_query_ids: set[str],
        query_filter: set[str] | None,
        query_results: list[dict[str, Any]],
    ) -> None:
        if warmup_iterations <= 0:
            return

        console.print(f"\n[yellow]Running {warmup_iterations} warmup iteration(s)...[/yellow]")
        for warmup_iter in range(warmup_iterations):
            warmup_stream_id = self._power_iteration_stream_id(benchmark_config, warmup_iter)
            warmup_queries = self._filter_queries(
                self._get_queries_for_benchmark(benchmark_config, benchmark_instance, stream_id=warmup_stream_id),
                skip_query_ids,
                query_filter,
            )

            console.print(f"[dim]Warmup {warmup_iter + 1}/{warmup_iterations} (stream {warmup_stream_id})[/dim]")
            with self._stream_parameter_scope(benchmark_config, warmup_stream_id):
                for query in warmup_queries:
                    try:
                        result = self.execute_query(ctx, query)
                        result = dict(result)
                    except Exception as e:
                        logger.warning(f"Warmup query {query.query_id} failed: {e}")
                        result = {
                            "query_id": query.query_id,
                            "status": "FAILED",
                            "error": str(e),
                            "execution_time_seconds": 0.0,
                        }
                    result["iteration"] = 0
                    result["stream_id"] = warmup_stream_id
                    result["run_type"] = "warmup"
                    query_results.append(result)

    @staticmethod
    def _power_iteration_stream_id(benchmark_config: BenchmarkConfig, default_stream_id: int) -> int:
        from benchbox.core.tpch.dataframe_queries import TPCH_FAMILY_DATAFRAME_IDS

        if normalize_benchmark_id(benchmark_config.name) not in TPCH_FAMILY_DATAFRAME_IDS:
            return default_stream_id
        return int(getattr(benchmark_config, "stream_id", 0) or 0)

    @staticmethod
    def _stream_parameter_scope(benchmark_config: BenchmarkConfig, stream_id: int) -> AbstractContextManager[None]:
        from benchbox.core.tpch.dataframe_queries import TPCH_FAMILY_DATAFRAME_IDS, seeded_parameter_overrides

        if normalize_benchmark_id(benchmark_config.name) not in TPCH_FAMILY_DATAFRAME_IDS:
            return nullcontext()

        seed = (getattr(benchmark_config, "options", {}) or {}).get("seed")
        scale_factor = getattr(benchmark_config, "scale_factor", 1.0)
        return seeded_parameter_overrides(None if seed is None else int(seed), scale_factor, stream_id)

    def _run_measurement_iterations(
        self,
        ctx: Any,
        measurement_iterations: int,
        warmup_iterations: int,
        total_queries: int,
        benchmark_config: BenchmarkConfig,
        benchmark_instance: Any | None,
        skip_query_ids: set[str],
        query_filter: set[str] | None,
        monitor: Any | None,
        run_options: DataFrameRunOptions | None,
        query_results: list[dict[str, Any]],
        executed_query_ids: list[str],
    ) -> None:
        capture_plans = bool(getattr(benchmark_config, "capture_plans", False))
        profiled_runner = getattr(self, "execute_query_profiled", None)
        supports_profiled = callable(profiled_runner)
        supports_capture_plan_kw = False
        if supports_profiled:
            try:
                supports_capture_plan_kw = "capture_plan" in inspect.signature(profiled_runner).parameters
            except (TypeError, ValueError):
                supports_capture_plan_kw = False

        console.print(
            f"\n[cyan]Running {measurement_iterations} measurement iteration(s) with {total_queries} queries each...[/cyan]"
        )

        for iteration in range(measurement_iterations):
            measurement_stream_id = self._power_iteration_stream_id(benchmark_config, warmup_iterations + iteration)
            measurement_queries = self._filter_queries(
                self._get_queries_for_benchmark(benchmark_config, benchmark_instance, stream_id=measurement_stream_id),
                skip_query_ids,
                query_filter,
            )

            console.print(
                f"\n[green]Iteration {iteration + 1}/{measurement_iterations} (stream {measurement_stream_id})[/green]"
            )

            iteration_results: list[dict[str, Any]] = []
            with self._stream_parameter_scope(benchmark_config, measurement_stream_id):
                for i, query in enumerate(measurement_queries, 1):
                    if run_options and (run_options.verbose or run_options.very_verbose):
                        console.print(f"[blue]Executing query {i}/{total_queries}: {query.query_id}[/blue]")

                    result = self._execute_single_measurement_query(
                        ctx=ctx,
                        query=query,
                        capture_plans=capture_plans,
                        supports_profiled=supports_profiled,
                        supports_capture_plan_kw=supports_capture_plan_kw,
                        profiled_runner=profiled_runner,
                        iteration=iteration + 1,
                        stream_id=measurement_stream_id,
                        monitor=monitor,
                    )
                    query_results.append(result)
                    executed_query_ids.append(str(query.query_id).strip().upper())
                    iteration_results.append(result)

            self._print_iteration_summary(
                iteration + 1,
                measurement_iterations,
                iteration_results,
                benchmark_config,
            )

    def _execute_single_measurement_query(
        self,
        ctx: Any,
        query: DataFrameQuery,
        capture_plans: bool,
        supports_profiled: bool,
        supports_capture_plan_kw: bool,
        profiled_runner: Any,
        iteration: int,
        stream_id: int,
        monitor: Any | None,
    ) -> dict[str, Any]:

        def _run() -> dict[str, Any]:
            try:
                if capture_plans and supports_profiled:
                    errors_before = len(getattr(self, "plan_capture_errors", []))
                    if supports_capture_plan_kw:
                        raw_result, profile = profiled_runner(ctx, query, capture_plan=True)
                    else:
                        raw_result, profile = profiled_runner(ctx, query)
                    raw_result = dict(raw_result)
                    if profile.query_plan is not None:
                        structured_plan = self._structure_dataframe_plan(profile.query_plan, str(query.query_id))
                        raw_result["query_plan"] = structured_plan
                        self._apply_plan_fingerprints(raw_result, structured_plan)
                    if profile.plan_capture_time_ms:
                        raw_result["plan_capture_time_ms"] = profile.plan_capture_time_ms
                    if (
                        plan_capture_support(self.platform_name) is PlanCaptureSupport.UNSUPPORTED
                        and str(raw_result.get("status", "")).upper() == "SUCCESS"
                    ):
                        self._report_unsupported_plan_capture(raw_result, str(query.query_id))
                    for record in getattr(self, "plan_capture_errors", [])[errors_before:]:
                        record.setdefault("stream_id", stream_id)
                        record.setdefault("iteration", iteration)
                else:
                    raw_result = self.execute_query(ctx, query)
                    raw_result = dict(raw_result)
            except PlanCaptureError:
                raise
            except Exception as e:
                logger.error(f"Query {query.query_id} failed: {e}")
                raw_result = {
                    "query_id": query.query_id,
                    "status": "FAILED",
                    "error": str(e),
                    "execution_time_seconds": 0.0,
                }
            raw_result["iteration"] = iteration
            raw_result["stream_id"] = stream_id
            raw_result["run_type"] = "measurement"
            return raw_result

        if monitor:
            with monitor.time_operation(f"query_{query.query_id}_iter{iteration}"):
                return _run()
        return _run()

    def _apply_plan_fingerprints(self, row: dict[str, Any], plan: Any) -> None:
        if not isinstance(plan, QueryPlanDAG):
            return
        row["plan_fingerprint"] = plan.plan_fingerprint
        if getattr(self, "normalize_plan_literals", False):
            row["plan_fingerprint_normalized"] = plan.normalized_fingerprint

    def _report_unsupported_plan_capture(self, row: dict[str, Any], query_id: str) -> None:
        message = f"{self.platform_name} is an eager DataFrame engine with no query plan to capture"
        row["plan_capture_error"] = message
        self._record_dataframe_plan_capture_failure(query_id, message)
        if getattr(self, "strict_plan_capture", False):
            raise PlanCaptureError(
                reason="unsupported",
                platform=self.platform_name,
                query_id=query_id,
                details=message,
            )

    def _record_dataframe_plan_capture_failure(self, query_id: str, message: str) -> None:
        if not hasattr(self, "plan_capture_errors"):
            self.plan_capture_errors: list[dict[str, Any]] = []
        self.plan_capture_errors.append({"query_id": query_id, "error": message})

        if not getattr(self, "_plan_capture_warning_emitted", False):
            self._plan_capture_warning_emitted = True
            logger.warning(
                "Query plan capture failed for %s on %s: %s (further capture failures this run are logged at debug)",
                query_id,
                self.platform_name,
                message,
            )
        else:
            logger.debug("Query plan capture failed for %s: %s", query_id, message)

    def _structure_dataframe_plan(self, captured_plan: Any, query_id: str) -> Any:
        if isinstance(captured_plan, QueryPlanDAG):
            return captured_plan

        plan_text = getattr(captured_plan, "plan_text", None)
        platform = getattr(captured_plan, "platform", None) or self.platform_name
        if not plan_text or not platform:
            return captured_plan

        from benchbox.core.query_plans.parsers.registry import get_parser_for_platform

        parser = get_parser_for_platform(str(platform).lower().replace("-df", ""))
        if parser is None:
            return captured_plan

        try:
            dag = parser.parse_explain_output(query_id, plan_text)
        except Exception as exc:
            logger.debug("DataFrame plan parse failed for %s on %s: %s", query_id, platform, exc)
            return captured_plan

        if dag is None:
            return captured_plan

        if getattr(dag, "raw_explain_output", None) is None:
            dag.raw_explain_output = plan_text
        return dag

    def _append_skip_summary(
        self,
        query_results: list[dict[str, Any]],
        executed_unique_ids: set[str],
        filtered_skip_query_ids: set[str],
    ) -> None:
        query_results.append(
            {
                "query_id": "DF_SKIP_SUMMARY",
                "status": "SUCCESS",
                "execution_time_seconds": 0.0,
                "run_type": "summary",
                "stream_id": 0,
                "iteration": 0,
                "rows_returned": len(filtered_skip_query_ids),
                "dataframe_skip_summary": {
                    "executed_total": len(executed_unique_ids),
                    "skipped_total": len(filtered_skip_query_ids),
                    "executed_by_category": self._count_query_categories(executed_unique_ids),
                    "skipped_by_category": self._count_query_categories(filtered_skip_query_ids),
                },
            }
        )

    def _print_iteration_summary(
        self,
        iteration: int,
        total_iterations: int,
        iteration_results: list[dict[str, Any]],
        benchmark_config: BenchmarkConfig,
    ) -> None:
        from benchbox.core.results.metrics import TPCMetricsCalculator

        total_queries = len(iteration_results)
        successful = [r for r in iteration_results if str(r.get("status", "")).upper() == "SUCCESS"]
        successful_count = len(successful)

        exec_times: list[float] = []
        for result in successful:
            exec_time = result.get("execution_time_seconds")
            if exec_time:
                exec_times.append(float(exec_time))

        total_time = sum(exec_times)
        scale_factor = getattr(benchmark_config, "scale_factor", 1.0)
        benchmark_id = normalize_benchmark_id(benchmark_config.name)

        if benchmark_id == "tpch":
            display_name = "TPC-H"
        elif benchmark_id == "tpcds":
            display_name = "TPC-DS"
        else:
            display_name = str(benchmark_config.name).upper()

        is_tpc = benchmark_id in ("tpch", "tpcds")
        power_at_size = None
        if is_tpc and successful_count == total_queries and exec_times:
            power_at_size = TPCMetricsCalculator.calculate_power_at_size(exec_times, scale_factor)

        test_name = f"{display_name} Power Test" if is_tpc else display_name
        console.print(f"\n[dim]Iteration {iteration}/{total_iterations} summary:[/dim]")
        if successful_count == total_queries:
            if power_at_size and power_at_size > 0:
                console.print(f"[green]{test_name} completed: Power@Size = {power_at_size:.2f}[/green]")
            else:
                console.print(f"[green]{test_name} completed[/green]")
            console.print(f"  Queries executed: {total_queries}, Successful: {successful_count}")
            success_rate = successful_count / max(total_queries, 1)
            if display_name == "TPC-H":
                console.print(f"  Success rate: {success_rate:.1%} (TPC-H requires >=95%)")
            else:
                console.print(f"  Success rate: {success_rate:.1%}")
            console.print(f"  Total execution time: {total_time:.2f}s")
        else:
            console.print(f"[yellow]{test_name} completed with failures[/yellow]")
            console.print(
                f"  Queries: {total_queries}, Successful: {successful_count}, "
                f"Failed: {total_queries - successful_count}"
            )

    def _get_queries_for_benchmark(
        self,
        benchmark_config: BenchmarkConfig,
        benchmark_instance: Any | None,
        stream_id: int | None = None,
    ) -> list[DataFrameQuery]:
        return get_dataframe_queries_for_benchmark(benchmark_config, benchmark_instance, stream_id)

    @staticmethod
    def _get_tpch_queries(stream_id: int) -> list[DataFrameQuery]:
        return get_tpch_dataframe_queries(stream_id)

    @staticmethod
    def _get_tpcds_queries(
        benchmark_config: BenchmarkConfig,
        benchmark_instance: Any | None,
        stream_id: int,
    ) -> list[DataFrameQuery]:
        return get_tpcds_dataframe_queries(benchmark_config, benchmark_instance, stream_id)

    @staticmethod
    def _query_category(query_id: str) -> str:
        normalized = str(query_id).strip().lower()
        if "_" in normalized:
            return normalized.split("_", 1)[0]
        if normalized.startswith("q") and normalized[1:].isdigit():
            return "q"
        return "other"

    def _count_query_categories(self, query_ids: set[str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for query_id in query_ids:
            category = self._query_category(query_id)
            counts[category] = counts.get(category, 0) + 1
        return counts
