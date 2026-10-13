from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from benchbox.core.config import BenchmarkConfig, RunConfig
from benchbox.core.constants import (
    GENERIC_POWER_DEFAULT_MEASUREMENT_ITERATIONS,
    GENERIC_POWER_DEFAULT_WARMUP_ITERATIONS,
    QUERY_PHASES,
)

TPC_ALLOWED_SCALE_FACTORS: frozenset[int | float] = frozenset({1, 10, 30, 100, 300, 1000, 3000, 10000, 30000, 100000})


def begin_run() -> None:
    from benchbox.core.deprecation_notices import begin_run as reset_deprecation_notices

    reset_deprecation_notices()


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
        raise ValueError(f"Scale factor {scale} is not TPC-compliant. Allowed: {sorted(TPC_ALLOWED_SCALE_FACTORS)}")


def validate_stream_count(streams: int | None, phases: str | None = None) -> None:
    phase_set: set[str] = set()
    if phases:
        phase_set = {p.strip().lower() for p in phases.split(",")}

    if "throughput" in phase_set and streams is None:
        raise ValueError("--streams is required for throughput test")
    if streams is not None and streams < 2 and "throughput" in phase_set:
        raise ValueError(f"--streams must be >= 2 (TPC throughput minimum); got: {streams}")


_DATAFRAME_THROUGHPUT_UNSUPPORTED = (
    "The throughput phase is not supported in DataFrame mode. DataFrame platforms run no "
    "concurrent query streams, so a throughput request would only repeat the power-test "
    "iterations under a throughput label and ignore --streams. Use --phases power for "
    "DataFrame platforms, or run the throughput phase on a SQL platform."
)


def reject_unsupported_dataframe_phases(execution_mode: str | None, phases_to_run: list[str] | None) -> None:
    if execution_mode == "dataframe" and phases_to_run and "throughput" in phases_to_run:
        raise ValueError(_DATAFRAME_THROUGHPUT_UNSUPPORTED)


from benchbox.core.platform_registry import PlatformRegistry
from benchbox.core.results.driver_metadata import apply_driver_metadata
from benchbox.core.runner.runner import (
    LifecyclePhases,
    ValidationOptions,
    run_benchmark_lifecycle,
)
from benchbox.core.schemas import reject_single_stream_throughput
from benchbox.utils.toggles import is_probe_requested

if TYPE_CHECKING:
    from collections.abc import Mapping

    from benchbox.core.results.models import BenchmarkResults
    from benchbox.core.schemas import ExecutionContext


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
    reject_single_stream_throughput(getattr(config, "concurrency", None), phases_to_run)
    stamp_requested_phases(config, phases_to_run)

    phases = resolve_lifecycle_phases(phases_to_run)
    validation = resolve_validation_options(getattr(config, "options", None))
    execution_mode = resolve_execution_mode(database_config)
    reject_unsupported_dataframe_phases(execution_mode, phases_to_run)

    adapter = adapter_factory(execution_mode=execution_mode, output_root=output_root, phases=phases)

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
    "AdapterFactory",
    "apply_polars_streaming_flag",
    "begin_run",
    "build_databricks_clustering_intent",
    "execute_run",
    "get_execution_mode",
    "is_dataframe_execution",
    "map_phases_to_execution_type",
    "reject_unsupported_dataframe_phases",
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
    "VerbosityLike",
    "warn_polars_streaming_flag",
]
