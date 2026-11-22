from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial, wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, ParamSpec, Protocol, TypeVar

from benchbox.core.config import BenchmarkConfig, RunConfig
from benchbox.core.config_utils import load_config_file
from benchbox.core.constants import (
    DEFAULT_OUTPUT_COMPRESSION_TYPE,
    GENERIC_POWER_DEFAULT_MEASUREMENT_ITERATIONS,
    GENERIC_POWER_DEFAULT_WARMUP_ITERATIONS,
    NO_COMPRESSION_ENV_VAR,
    QUERY_PHASES,
)
from benchbox.core.results.subscale_policy import normalize_tpc_benchmark_id

TPC_ALLOWED_SCALE_FACTORS: frozenset[int | float] = frozenset({1, 10, 30, 100, 300, 1000, 3000, 10000, 30000, 100000})

_P = ParamSpec("_P")
_R = TypeVar("_R")

TPCDI_UNAVAILABLE_MESSAGE = (
    "TPC-DI is unavailable while BenchBox reworks it to run the specification's workload "
    "(historical load, two incremental updates, automated audit). Earlier BenchBox TPC-DI results were withdrawn."
)

UNAVAILABLE_BENCHMARK_MESSAGES: dict[str, str] = {"tpcdi": TPCDI_UNAVAILABLE_MESSAGE}


class BenchmarkUnavailableError(RuntimeError):
    pass


def unavailable_benchmark_message(benchmark_id: object) -> str | None:
    return UNAVAILABLE_BENCHMARK_MESSAGES.get(normalize_tpc_benchmark_id(benchmark_id))


def refuse_unavailable_benchmark(benchmark_id: object) -> None:
    message = unavailable_benchmark_message(benchmark_id)
    if message is not None:
        raise BenchmarkUnavailableError(message)


def refuses_unavailable_benchmark(benchmark_id: str) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    def decorate(func: Callable[_P, _R]) -> Callable[_P, _R]:
        @wraps(func)
        def guarded(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            refuse_unavailable_benchmark(benchmark_id)
            return func(*args, **kwargs)

        return guarded

    return decorate


def official_scale_points(benchmark_id: str | None) -> frozenset[int | float]:
    if benchmark_id == "tpch":
        from benchbox.core.tpch.compliance import OFFICIAL_SCALE_POINTS as tpch_points

        return tpch_points
    if benchmark_id == "tpcds":
        from benchbox.core.tpcds.compliance import OFFICIAL_SCALE_POINTS as tpcds_points

        return tpcds_points
    return TPC_ALLOWED_SCALE_FACTORS


def begin_run() -> None:
    from benchbox.core.deprecation_notices import begin_run as reset_deprecation_notices
    from benchbox.core.option_notices import begin_run as reset_option_notices

    reset_deprecation_notices()
    reset_option_notices()


def apply_polars_streaming_flag(
    option_pairs: tuple[tuple[str, str], ...], execution_engine: str | None
) -> tuple[tuple[str, str], ...]:
    from benchbox.core.platform_manifest import DEFAULT_EXECUTION_ENGINE

    if execution_engine not in (None, DEFAULT_EXECUTION_ENGINE, "streaming"):
        raise ValueError(f"--polars-streaming conflicts with --execution-engine {execution_engine}")
    return (*option_pairs, ("streaming", "true"))


def warn_polars_streaming_flag() -> None:
    from benchbox.core.deprecation_notices import warn_deprecated_alias

    warn_deprecated_alias(
        "polars",
        "--polars-streaming",
        "'--execution-engine streaming' (execution_engine='streaming')",
        setting="streaming",
    )


def validate_tpc_scale_factor(scale: float) -> None:
    if scale not in TPC_ALLOWED_SCALE_FACTORS:
        raise ValueError(
            f"Scale factor {scale} is not a TPC-specified scale factor. Allowed: {sorted(TPC_ALLOWED_SCALE_FACTORS)}"
        )


def validate_stream_count(
    streams: int | None,
    phases: str | None = None,
    *,
    benchmark: str | None = None,
    scale_factor: float | None = None,
) -> None:
    phase_set: set[str] = set()
    if phases:
        phase_set = {p.strip().lower() for p in phases.split(",")}

    if "throughput" in phase_set and streams is None:
        raise ValueError("--streams is required for throughput test")
    if streams is not None and streams < 2 and "throughput" in phase_set:
        raise ValueError(f"--streams must be >= 2 (TPC throughput minimum); got: {streams}")
    if streams is not None and "throughput" in phase_set and benchmark and scale_factor is not None:
        minimum = min_streams_for_scale(benchmark, scale_factor)
        if minimum is not None and streams < minimum:
            raise ValueError(
                f"--streams must be >= {minimum} for {benchmark.upper()} scale factor {scale_factor:g} "
                f"(TPC throughput minimum); got: {streams}"
            )


def reject_unsupported_dataframe_benchmark(
    execution_mode: str | None, phases_to_run: list[str] | None, benchmark: str | None
) -> None:
    if execution_mode != "dataframe" or not phases_to_run or "throughput" not in phases_to_run:
        return
    if benchmark is not None and benchmark.lower() != "tpch":
        raise ValueError(
            f"The DataFrame throughput phase is implemented for TPC-H only; {benchmark} has no DataFrame "
            "stream runner. Use --phases power, or run the throughput phase on a SQL platform."
        )


def reject_unsupported_dataframe_phases(
    execution_mode: str | None, phases_to_run: list[str] | None, adapter: Any = None
) -> None:
    if execution_mode != "dataframe" or not phases_to_run or "throughput" not in phases_to_run:
        return
    if getattr(adapter, "supports_dataframe_throughput", False) is True:
        return
    platform_name = getattr(adapter, "platform_name", None) or "this DataFrame platform"
    raise ValueError(
        f"The throughput phase is not supported by {platform_name} in DataFrame mode. It declares no "
        "concurrent query-stream support, so a throughput request would only repeat the power-test "
        "iterations under a throughput label and ignore --streams. Use --phases power for "
        "this platform, or run the throughput phase on a SQL platform or on a DataFrame platform "
        "that supports it (polars-df, datafusion-df)."
    )


from benchbox.core.execution_engine import reject_engine_bypassing_benchmark
from benchbox.core.platform_manifest import DEFAULT_EXECUTION_ENGINE
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.core.results.driver_metadata import apply_driver_metadata
from benchbox.core.runner.runner import (
    LifecyclePhases,
    ValidationOptions,
    run_benchmark_lifecycle,
)
from benchbox.core.schemas import reject_single_stream_throughput
from benchbox.core.tpc_spec import min_streams_for_scale, refresh_functions_enabled, require_full_spec
from benchbox.utils.compression import CompressionManager
from benchbox.utils.disk_guard import ensure_run_output_space
from benchbox.utils.run_deadline import (
    RUN_TIMEOUT_OPTION_KEY,
    TIMED_OUT_VALIDATION_STATUS,
    RunDeadline,
    RunTimeoutError,
    active_run_deadline,
    current_run_deadline,
    mark_timed_out,
    register_remote_cleanup,
    timeout_details,
    validate_timeout_seconds,
)
from benchbox.utils.toggles import is_probe_requested

if TYPE_CHECKING:
    from collections.abc import Mapping

    from benchbox.core.results.models import BenchmarkResults
    from benchbox.core.schemas import ExecutionContext

_logger = logging.getLogger(__name__)


class VerbosityLike(Protocol):
    @property
    def verbose(self) -> bool: ...

    @property
    def level(self) -> int: ...

    @property
    def verbose_enabled(self) -> bool: ...

    @property
    def very_verbose(self) -> bool: ...

    @property
    def quiet(self) -> bool: ...


@dataclass(frozen=True)
class SilentVerbosity:
    verbose: bool = False
    level: int = 0
    verbose_enabled: bool = False
    very_verbose: bool = False
    quiet: bool = True


@dataclass(frozen=True)
class UnsupportedExecutionMode:
    platform: str
    mode: str
    supported: tuple[str, ...]


def resolve_run_config(
    config: BenchmarkConfig,
    *,
    database_path: str | Path,
    verbosity: VerbosityLike,
) -> RunConfig:
    options = config.options or {}
    iterations = int(
        options.get("power_iterations", GENERIC_POWER_DEFAULT_MEASUREMENT_ITERATIONS)
        or GENERIC_POWER_DEFAULT_MEASUREMENT_ITERATIONS
    )
    warmups = int(
        options.get("power_warmup_iterations", GENERIC_POWER_DEFAULT_WARMUP_ITERATIONS)
        or GENERIC_POWER_DEFAULT_WARMUP_ITERATIONS
    )
    fail_fast = bool(options.get("power_fail_fast", False))

    return RunConfig(
        query_subset=config.queries,
        concurrent_streams=config.concurrency,
        test_execution_type=getattr(config, "test_execution_type", "standard"),
        scale_factor=config.scale_factor,
        capture_plans=config.capture_plans,
        analyze_plans=getattr(config, "analyze_plans", None),
        strict_plan_capture=config.strict_plan_capture,
        seed=int(options.get("seed")) if options.get("seed") is not None else None,
        connection={"database_path": str(database_path)},
        verbose=verbosity.verbose,
        verbose_level=verbosity.level,
        verbose_enabled=verbosity.verbose_enabled,
        very_verbose=verbosity.very_verbose,
        quiet=verbosity.quiet,
        iterations=max(1, iterations),
        warm_up_iterations=max(0, warmups),
        power_fail_fast=fail_fast,
        stream_timeout_seconds=options.get("stream_timeout_seconds"),
        stream_timeout_source=options.get("stream_timeout_source"),
        cancel_on_timeout=bool(options.get("cancel_on_timeout", False)),
        client_region=getattr(config, "client_region", None) or options.get("client_region"),
        client_cloud=getattr(config, "client_cloud", None) or options.get("client_cloud"),
        link_probe=is_probe_requested(getattr(config, "link_probe", None)),
    )


class AdapterFactory(Protocol):
    def __call__(
        self,
        *,
        execution_mode: str | None,
        output_root: Any,
        phases: LifecyclePhases,
    ) -> Any | None: ...


def resolve_lifecycle_phases(phases_to_run: list[str] | None) -> LifecyclePhases:
    if not phases_to_run:
        return LifecyclePhases(generate=True, load=True, execute=True)

    return LifecyclePhases(
        generate="generate" in phases_to_run,
        load="load" in phases_to_run,
        execute=any(phase in phases_to_run for phase in ("warmup", *QUERY_PHASES)),
        statistics="statistics" in phases_to_run,
    )


def resolve_validation_options(options: Mapping[str, Any] | None) -> ValidationOptions:
    opts = options or {}
    return ValidationOptions(
        enable_preflight_validation=bool(opts.get("enable_preflight_validation")),
        enable_postgen_manifest_validation=bool(opts.get("enable_postgen_manifest_validation", False)),
        enable_postload_validation=bool(opts.get("enable_postload_validation", False)),
    )


def resolve_execution_mode(database_config: Any) -> str | None:
    if database_config is None:
        return None

    execution_mode = getattr(database_config, "execution_mode", None)
    if execution_mode is not None:
        return execution_mode

    execution_mode = PlatformRegistry.get_default_mode(database_config.type)
    if is_dataframe_execution(database_config):
        execution_mode = "dataframe"
    return execution_mode


def is_dataframe_execution(database_config: Any) -> bool:
    if database_config is None:
        return False

    platform_type = getattr(database_config, "type", "")
    explicit_mode = getattr(database_config, "mode", None)
    if explicit_mode is not None:
        return explicit_mode == "dataframe"

    platform_lower = platform_type.lower()
    if ":" in platform_lower:
        platform_lower = platform_lower.rsplit(":", 1)[0]
    if platform_lower.endswith("-df"):
        return True
    return PlatformRegistry.get_default_mode(platform_lower) == "dataframe"


def get_execution_mode(database_config: Any) -> str:
    return "dataframe" if is_dataframe_execution(database_config) else "sql"


def stamp_requested_phases(config: BenchmarkConfig, phases_to_run: list[str] | None) -> None:
    if not phases_to_run:
        return
    options = dict(getattr(config, "options", {}) or {})
    options["requested_phases"] = list(phases_to_run)
    config.options = options


def build_unfinished_result(
    config: BenchmarkConfig,
    *,
    platform: str,
    validation_status: str,
    validation_details: dict[str, Any],
    query_results: list[dict[str, Any]] | None = None,
) -> BenchmarkResults:
    import uuid
    from datetime import datetime

    from benchbox.core.results.models import BenchmarkResults, ExecutionPhases, SetupPhase

    rows = list(query_results or [])
    successful = len([row for row in rows if row.get("status") == "SUCCESS"])
    return BenchmarkResults(
        benchmark_name=getattr(config, "display_name", config.name.upper()),
        platform=platform,
        scale_factor=config.scale_factor,
        execution_id=uuid.uuid4().hex[:8],
        timestamp=datetime.now(),
        duration_seconds=0.0,
        total_queries=len(rows),
        successful_queries=successful,
        failed_queries=len(rows) - successful,
        total_execution_time=0.0,
        average_query_time=0.0,
        query_results=rows,
        query_definitions={},
        execution_phases=ExecutionPhases(setup=SetupPhase()),
        validation_status=validation_status,
        validation_details=validation_details,
        data_loading_time=0.0,
        schema_creation_time=0.0,
        total_rows_loaded=0,
        data_size_mb=0.0,
        table_statistics={},
    )


def build_timed_out_result(config: BenchmarkConfig, database_config: Any, deadline: RunDeadline) -> BenchmarkResults:
    return build_unfinished_result(
        config,
        platform=str(getattr(database_config, "type", None) or "unknown"),
        validation_status=TIMED_OUT_VALIDATION_STATUS,
        validation_details={"timeout": timeout_details(deadline)},
        query_results=deadline.partial_query_results,
    )


def execute_run(
    *,
    config: BenchmarkConfig,
    benchmark_instance: Any,
    database_config: Any,
    system_profile: Any,
    platform_config: Mapping[str, Any] | None,
    output_root: Any,
    phases_to_run: list[str] | None,
    adapter_factory: AdapterFactory,
    verbosity: VerbosityLike,
    monitor: Any = None,
    execution_context: ExecutionContext | None = None,
) -> BenchmarkResults:
    refuse_unavailable_benchmark(config.name)
    run = partial(
        _execute_run,
        config=config,
        benchmark_instance=benchmark_instance,
        database_config=database_config,
        system_profile=system_profile,
        platform_config=platform_config,
        output_root=output_root,
        phases_to_run=phases_to_run,
        adapter_factory=adapter_factory,
        verbosity=verbosity,
        monitor=monitor,
        execution_context=execution_context,
    )
    deadline = current_run_deadline()
    if deadline is None:
        timeout_seconds = validate_timeout_seconds((getattr(config, "options", None) or {}).get(RUN_TIMEOUT_OPTION_KEY))
        if timeout_seconds is None:
            return run()
        deadline = RunDeadline(timeout_seconds)

    with active_run_deadline(deadline):
        try:
            try:
                result = run()
            except RunTimeoutError:
                result = build_timed_out_result(config, database_config, deadline)
            deadline.record_overrun()
        finally:
            if deadline.timeout is not None or deadline.expired:
                for error in deadline.cancel_remote_work():
                    _logger.warning(f"Remote cleanup failed: {error}")
    if deadline.timeout is not None:
        mark_timed_out(result, deadline)
    return result


def _execute_run(
    *,
    config: BenchmarkConfig,
    benchmark_instance: Any,
    database_config: Any,
    system_profile: Any,
    platform_config: Mapping[str, Any] | None,
    output_root: Any,
    phases_to_run: list[str] | None,
    adapter_factory: AdapterFactory,
    verbosity: VerbosityLike,
    monitor: Any = None,
    execution_context: ExecutionContext | None = None,
) -> BenchmarkResults:
    reject_single_stream_throughput(getattr(config, "concurrency", None), phases_to_run)
    if getattr(config, "official", False) and (phases_to_run is None or set(QUERY_PHASES).intersection(phases_to_run)):
        config_options = getattr(config, "options", None) or {}
        require_full_spec(
            config.name,
            config.scale_factor,
            seed=config_options.get("seed"),
            phases=phases_to_run,
            streams=getattr(config, "concurrency", None),
            query_subset=getattr(config, "queries", None),
            refresh_functions=refresh_functions_enabled(config_options, benchmark_instance),
        )
    stamp_requested_phases(config, phases_to_run)

    phases = resolve_lifecycle_phases(phases_to_run)
    validation = resolve_validation_options(getattr(config, "options", None))
    execution_mode = resolve_execution_mode(database_config)
    reject_unsupported_dataframe_benchmark(execution_mode, phases_to_run, config.name)
    ensure_run_output_space(output_root)

    adapter = adapter_factory(execution_mode=execution_mode, output_root=output_root, phases=phases)
    cleanup = getattr(adapter, "cancel_remote_work", None)
    if callable(cleanup):
        register_remote_cleanup(adapter, cleanup, type(adapter).__name__)
    reject_unsupported_dataframe_phases(execution_mode, phases_to_run, adapter)
    if execution_mode == "dataframe" and adapter is not None:
        reject_engine_bypassing_benchmark(
            benchmark_instance,
            config.name,
            database_config.type,
            getattr(adapter, "requested_execution_engine", DEFAULT_EXECUTION_ENGINE),
        )

    result = run_benchmark_lifecycle(
        benchmark_config=config,
        database_config=database_config,
        system_profile=system_profile,
        platform_config=platform_config,
        phases=phases,
        validation_opts=validation,
        output_root=output_root,
        benchmark_instance=benchmark_instance,
        platform_adapter=adapter,
        verbosity=verbosity,
        monitor=monitor,
        enable_resource_monitoring=False,
        execution_context=execution_context,
    )

    apply_driver_metadata(result, database_config=database_config, platform_adapter=adapter)
    return result


def translate_platform_options_for_adapter(platform: str, options: dict) -> dict:
    normalized = dict(options)
    platform_name = platform.lower().removesuffix("-df")
    if platform_name == "duckdb" and "threads" in normalized:
        normalized["thread_limit"] = normalized.pop("threads")
    if platform_name == "databricks":
        tuning_config = build_databricks_clustering_intent(normalized)
        if tuning_config is not None:
            normalized.pop("databricks_clustering_strategy", None)
            normalized.pop("liquid_clustering_columns", None)
            normalized["tuning_config"] = tuning_config
            normalized["tuning_enabled"] = True
    return normalized


def _saved_compression_settings() -> Mapping[str, Any]:
    config_path = Path("benchbox.yaml")
    if not config_path.exists():
        config_path = Path.home() / ".benchbox" / "config.yaml"
    try:
        saved = load_config_file(config_path)
    except (FileNotFoundError, ValueError):
        return {}
    output = saved.get("output") if isinstance(saved, dict) else None
    compression = output.get("compression") if isinstance(output, dict) else None
    return compression if isinstance(compression, dict) else {}


def resolve_configured_compression() -> tuple[bool, str, int | None]:
    saved = _saved_compression_settings()
    compression_type = str(saved.get("type", DEFAULT_OUTPUT_COMPRESSION_TYPE))
    disabled = os.getenv(NO_COMPRESSION_ENV_VAR, "").lower() in {"true", "1", "yes", "on"}
    usable = (
        bool(saved.get("enabled", True))
        and compression_type != "none"
        and compression_type in CompressionManager().get_available_compressors()
    )
    if disabled or not usable:
        return False, "none", None
    return True, compression_type, saved.get("level")


def build_databricks_clustering_intent(options: dict[str, object]):
    strategy = options.get("databricks_clustering_strategy")
    columns = options.get("liquid_clustering_columns")
    if strategy is None and columns is None:
        return None

    from benchbox.core.tuning.interface import UnifiedTuningConfiguration

    tuning_config = UnifiedTuningConfiguration()
    platform_optimizations = tuning_config.platform_optimizations
    if strategy is not None:
        strategy = str(strategy).lower()
        platform_optimizations.databricks_clustering_strategy = strategy
        platform_optimizations.liquid_clustering_enabled = strategy in {
            "liquid_clustering",
            "liquid_clustering_auto",
        }
        platform_optimizations.physical_rendering_id = None
    if columns is not None:
        parsed_columns = [column.strip() for column in str(columns).split(",") if column.strip()]
        platform_optimizations.liquid_clustering_columns = parsed_columns
        platform_optimizations.liquid_clustering_enabled = bool(parsed_columns)
        if parsed_columns and strategy is None:
            platform_optimizations.databricks_clustering_strategy = "liquid_clustering"

    platform_optimizations.__post_init__()
    return tuning_config


def resolve_mode_with_registry(platform: str, mode: str | None):
    if mode is not None:
        mode = mode.lower()
        if mode in ("datagen", "generate"):
            mode = "data_only"
    if mode == "data_only":
        return "data_only", None
    platform_lower = platform.lower()
    base_platform = platform_lower.replace("-df", "")
    caps = PlatformRegistry.get_platform_capabilities(base_platform)
    if caps is None:
        return mode or "sql", None
    supported = []
    if caps.supports_sql:
        supported.append("sql")
    if caps.supports_dataframe:
        supported.append("dataframe")
    supported.append("data_only")
    if mode is not None:
        if not PlatformRegistry.supports_mode(base_platform, mode):
            return mode, UnsupportedExecutionMode(platform, mode, tuple(supported))
        return mode, None
    if platform_lower.endswith("-df"):
        return "dataframe", None
    return caps.default_mode, None


def map_phases_to_execution_type(phases: list[str]) -> str:
    phases_set = set(phases)
    query_phases = set(QUERY_PHASES)
    selected_query_phases = phases_set & query_phases
    if selected_query_phases:
        if len(selected_query_phases) > 1:
            return "combined"
        if "power" in selected_query_phases:
            return "power"
        if "throughput" in selected_query_phases:
            return "throughput"
        if "maintenance" in selected_query_phases:
            return "maintenance"
        return "standard"
    elif phases == ["load"] or ("load" in phases_set and not phases_set & query_phases):
        return "load_only"
    elif phases == ["generate"] or ("generate" in phases_set and not phases_set & ({"load"} | query_phases)):
        return "data_only"
    else:
        return "standard"


__all__ = [
    "refuses_unavailable_benchmark",
    "TPCDI_UNAVAILABLE_MESSAGE",
    "unavailable_benchmark_message",
    "UNAVAILABLE_BENCHMARK_MESSAGES",
    "AdapterFactory",
    "apply_polars_streaming_flag",
    "begin_run",
    "build_databricks_clustering_intent",
    "build_timed_out_result",
    "build_unfinished_result",
    "execute_run",
    "BenchmarkUnavailableError",
    "get_execution_mode",
    "is_dataframe_execution",
    "map_phases_to_execution_type",
    "official_scale_points",
    "reject_unsupported_dataframe_benchmark",
    "reject_unsupported_dataframe_phases",
    "resolve_configured_compression",
    "resolve_execution_mode",
    "resolve_lifecycle_phases",
    "resolve_mode_with_registry",
    "resolve_run_config",
    "resolve_validation_options",
    "SilentVerbosity",
    "stamp_requested_phases",
    "TPC_ALLOWED_SCALE_FACTORS",
    "translate_platform_options_for_adapter",
    "UnsupportedExecutionMode",
    "validate_stream_count",
    "validate_tpc_scale_factor",
    "refuse_unavailable_benchmark",
    "VerbosityLike",
    "warn_polars_streaming_flag",
]
