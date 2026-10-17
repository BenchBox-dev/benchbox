from __future__ import annotations

import json
import logging
import os
import sys
import types
from pathlib import Path
from typing import Any

import click
from rich.panel import Panel
from rich.prompt import Confirm
from rich.text import Text

from benchbox.cli.benchmarks import BenchmarkConfig, BenchmarkManager
from benchbox.cli.cloud_storage import prompt_cloud_output_location
from benchbox.cli.composite_params import (
    COMPRESSION,
    FORCE,
    PLAN_CONFIG,
    TABLE_FORMAT,
    VALIDATION,
    CompressionConfig,
    ForceConfig,
    PlanCaptureConfig,
    TableFormatConfig,
    ValidationConfig,
)
from benchbox.cli.database import DatabaseManager
from benchbox.cli.exceptions import (
    CloudStorageError,
    ErrorContext,
    ValidationError,
    ValidationRules,
    create_error_handler,
)
from benchbox.cli.help import BenchBoxCommand, advanced_option
from benchbox.cli.logo import rich_logo
from benchbox.cli.orchestrator import BenchmarkOrchestrator, resolved_deployment_mode
from benchbox.cli.output import ResultExporter
from benchbox.cli.platform import (
    get_platform_manager,
    normalize_platform_name,
)
from benchbox.cli.platform_checks import check_and_setup_platform_credentials
from benchbox.cli.platform_hooks import PlatformOptionError
from benchbox.cli.presentation.system import display_system_recommendations
from benchbox.cli.progress import BenchmarkProgress, should_show_progress
from benchbox.cli.run_platform_resolution import (
    BENCHMARK_ALIASES as BENCHMARK_ALIASES,
    _apply_benchmark_default_scale,
    _apply_dataframe_suffix_mode as _apply_dataframe_suffix_mode,
    _check_benchmark_platform_compatibility,
    _check_platforms_status,
    _interactive_resolve_mode_for_selected_platform,
    _parse_plat_bench_options,
    _platform_option_config_entries,
    _reject_external_tuned,
    _resolve_platform_mode,
    _tuning_arg_is_tuning_bearing,
    normalize_benchmark_name,
)
from benchbox.cli.run_resolution import (
    ResolvedRunPlan,
    capture_resolved_run_plan,
    current_run_request as _current_run_request,
    resolve_quick_restart_atomically,
)
from benchbox.cli.shared import console, silence_output
from benchbox.cli.system import SystemProfiler
from benchbox.cli.tuning_resolver import (
    TuningMode,
    TuningResolution,
    TuningSource,
    display_tuning_resolution,
    is_dataframe_tuning_file,
    promote_tuning_provenance,
    resolve_template_reference,
    resolve_tuning,
    warn_sql_auto_mode,
)
from benchbox.cli.tuning_runtime import (
    build_baseline_unified_config,
    build_default_unified_config,
    infer_runtime_tuning_mode,
    resolve_dataframe_tuning_config,
)
from benchbox.core.benchmark_registry import (
    get_presort_table_configs,
    presort_capable_benchmarks,
)
from benchbox.core.config import DatabaseConfig
from benchbox.core.constants import RUN_MODES, VALID_PHASES
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.core.results.provenance import FUNDING_SOURCES, RESULT_SOURCES
from benchbox.core.results.status import result_cli_failure_reason, result_non_clean_reason
from benchbox.core.schemas import ExecutionContext
from benchbox.core.tuning import modes as tuning_modes
from benchbox.utils.cloud_storage import is_cloud_path
from benchbox.utils.compression import CompressionManager
from benchbox.utils.input_validation import MAX_QUERY_ID_LENGTH
from benchbox.utils.output_path import normalize_output_root
from benchbox.utils.verbosity import VerbositySettings

logger = logging.getLogger(__name__)
DATA_ORGANIZATION_ENV = "BENCHBOX_DATA_ORGANIZATION_CONFIG_JSON"
_GUIDED_CREDENTIAL_PLATFORMS = frozenset(
    {"athena", "bigquery", "databricks", "motherduck", "redshift", "singlestore", "snowflake"}
)


def _reject_official_fallback(s: types.SimpleNamespace, resolution: TuningResolution) -> None:
    if not s.official or resolution.canonical_mode != tuning_modes.TUNED_FALLBACK:
        return
    console.print("[red]❌ Error: --official runs cannot use a 'tuned-fallback' configuration[/red]")
    console.print(
        "[yellow]No tuning template was found for this platform/benchmark. "
        "Add one under examples/tunings/, or rerun with --tuning notuning "
        "or an explicit --tuning <file>.[/yellow]"
    )
    if s.logger:
        s.logger.error("Official run refused: tuning resolved to tuned-fallback under --official")
    s.ctx.exit(1)


def _apply_platform_optimization_overrides(
    unified_tuning: Any,
    *,
    sorted_ingestion_mode: str | None,
    sorted_ingestion_method: str | None,
    parsed_platform_options: dict[str, Any] | None = None,
) -> None:
    platform_optimizations = unified_tuning.platform_optimizations
    platform_options = parsed_platform_options or {}

    if sorted_ingestion_mode:
        platform_optimizations.sorted_ingestion_mode = sorted_ingestion_mode.lower()
    if sorted_ingestion_method:
        platform_optimizations.sorted_ingestion_method = sorted_ingestion_method.lower()
    if (
        platform_optimizations.sorted_ingestion_mode == "off"
        and platform_optimizations.sorted_ingestion_method != "auto"
    ):
        raise ValueError("--sorted-ingestion-method requires --sorted-ingestion-mode auto or force")

    resolved_strategy = platform_options.get("databricks_clustering_strategy")
    if resolved_strategy:
        strategy = str(resolved_strategy).lower()
        platform_optimizations.databricks_clustering_strategy = strategy
        platform_optimizations.liquid_clustering_enabled = strategy in {"liquid_clustering", "liquid_clustering_auto"}
        platform_optimizations.physical_rendering_id = None

    resolved_liquid_columns = platform_options.get("liquid_clustering_columns")
    if resolved_liquid_columns:
        columns = [column.strip() for column in str(resolved_liquid_columns).split(",") if column.strip()]
        platform_optimizations.liquid_clustering_columns = columns
        if columns:
            platform_optimizations.liquid_clustering_enabled = True

    platform_optimizations.__post_init__()


def _build_data_organization_from_tuning(unified_tuning: Any) -> dict[str, Any] | None:
    if unified_tuning is None:
        return None

    table_configs: dict[str, list[dict[str, str]]] = {}
    table_partition_configs: dict[str, list[str]] = {}
    table_cluster_configs: dict[str, list[str]] = {}

    table_tunings = getattr(unified_tuning, "table_tunings", {}) or {}
    for table_name, table_tuning in table_tunings.items():
        sorting = sorted(getattr(table_tuning, "sorting", []) or [], key=lambda c: c.order)
        partitioning = sorted(getattr(table_tuning, "partitioning", []) or [], key=lambda c: c.order)
        clustering = sorted(getattr(table_tuning, "clustering", []) or [], key=lambda c: c.order)

        if sorting:
            table_configs[table_name] = [{"name": c.name, "order": str(c.sort_order).lower()} for c in sorting]
        if partitioning:
            table_partition_configs[table_name] = [c.name for c in partitioning]
        if clustering:
            table_cluster_configs[table_name] = [c.name for c in clustering]

    if not table_configs and not table_partition_configs and not table_cluster_configs:
        return None

    platform_opts = getattr(unified_tuning, "platform_optimizations", None)
    method = "z_order"
    method_hint = str(getattr(platform_opts, "sorted_ingestion_method", "auto") or "auto").lower()
    databricks_strategy = str(getattr(platform_opts, "databricks_clustering_strategy", "") or "").lower()
    if databricks_strategy in {"liquid_clustering", "liquid_clustering_auto"} or getattr(
        platform_opts, "liquid_clustering_enabled", False
    ):
        method = "liquid_clustering"
    elif method_hint == "hilbert":
        method = "hilbert"
    elif method_hint == "z_order" or getattr(platform_opts, "z_ordering_enabled", False):
        method = "z_order"

    return {
        "table_configs": table_configs,
        "table_partition_configs": table_partition_configs,
        "table_cluster_configs": table_cluster_configs,
        "clustering_method": method,
    }


def _resolve_data_organization_payload(
    benchmark_name: str | None,
    presort: str | None,
    tuning_payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if presort not in {"parquet-sorted", "delta-sorted", "iceberg-sorted"}:
        return tuning_payload

    if tuning_payload is not None:
        if presort in {"delta-sorted", "iceberg-sorted"}:
            payload = dict(tuning_payload)
            payload["output_format"] = "delta" if presort == "delta-sorted" else "iceberg"
            return payload
        return tuning_payload

    benchmark_key = (benchmark_name or "").lower()
    output_format_map = {
        "parquet-sorted": "parquet",
        "delta-sorted": "delta",
        "iceberg-sorted": "iceberg",
    }
    output_format = output_format_map[presort]

    table_configs = get_presort_table_configs(benchmark_key) if benchmark_key else None
    if table_configs:
        return {"table_configs": table_configs, "output_format": output_format}

    if benchmark_key:
        supported = ", ".join(sorted(presort_capable_benchmarks()))
        raise ValueError(f"--presort sorted modes are currently supported for {supported} benchmarks")
    return tuning_payload


def _render_post_run_charts(
    result: Any,
    console: Any,
    quiet: bool,
) -> None:
    if quiet or not result.query_results:
        return
    try:
        from benchbox.core.visualization.post_run_summary import generate_post_run_summary

        summary = generate_post_run_summary(result)
        for chart in summary.charts:
            console.print(Text.from_ansi(chart))
    except Exception:
        logger.debug("Failed to render post-run summary charts", exc_info=True)


def _build_execution_context(
    phases_to_run: list[str],
    seed: int | None,
    compression: CompressionConfig,
    mode: str | None,
    official: bool,
    validation: ValidationConfig,
    force: ForceConfig,
    queries_to_run: list[str] | None,
    capture_plans: bool,
    strict_plan_capture: bool,
    non_interactive: bool,
    tuning: str | None,
) -> ExecutionContext:
    return ExecutionContext(
        entry_point="cli",
        phases=phases_to_run,
        seed=seed,
        compression_enabled=compression.enabled,
        compression_type=compression.type,
        compression_level=compression.level,
        mode=mode or "sql",
        official=official,
        validation_mode=validation.mode if validation.mode != "exact" else None,
        force_datagen=force.datagen,
        force_upload=force.upload,
        query_subset=queries_to_run,
        capture_plans=capture_plans,
        strict_plan_capture=strict_plan_capture,
        non_interactive=non_interactive,
        tuning_mode=tuning_modes.NOTUNING if tuning == tuning_modes.NOTUNING else tuning,
    )


def _canonical_tuning_mode(s: types.SimpleNamespace) -> str | None:
    resolution = getattr(s, "tuning_resolution", None)
    if resolution is not None:
        return resolution.canonical_mode
    raw = s.tuning
    if not raw or raw.strip().lower() == "notuning":
        return None
    if raw.strip().lower() in {"tuned", "auto"}:
        return raw.strip().lower()
    return tuning_modes.CUSTOM


def _execute_orchestrated_run(
    orchestrator: BenchmarkOrchestrator,
    benchmark_config: BenchmarkConfig,
    system_profile: Any,
    database_config: DatabaseConfig | None,
    phases_to_run: list[str],
    *,
    quiet: bool,
    no_progress: bool,
    no_monitoring: bool,
    execution_context: ExecutionContext | None = None,
) -> Any:
    progress_enabled = not no_progress and should_show_progress()
    enable_monitoring = not no_monitoring

    if progress_enabled and not quiet:
        progress = BenchmarkProgress(
            console=console,
            enable_monitoring=enable_monitoring,
        )
        with progress:
            return orchestrator.execute_benchmark(
                benchmark_config,
                system_profile,
                database_config,
                phases_to_run,
                progress=progress,
                execution_context=execution_context,
            )

    with silence_output(enabled=bool(quiet)):
        return orchestrator.execute_benchmark(
            benchmark_config,
            system_profile,
            database_config,
            phases_to_run,
            progress=None,
            execution_context=execution_context,
        )


LOAD_FAILURE_MARKER = "BENCHBOX_LOAD_FAILURE_JSON="


def _print_template_evidence_warning(result: Any, *, quiet: bool) -> None:
    if quiet:
        return
    from benchbox.core.results.schema import _tuning_evidence_engine_version
    from benchbox.core.tuning.template_evidence import evidence_warning

    message = evidence_warning(
        getattr(result, "platform", None),
        getattr(result, "benchmark_name", None),
        source_file=getattr(result, "tuning_source_file", None),
        tunings_applied=getattr(result, "tunings_applied", None),
        scale_factor=getattr(result, "scale_factor", None),
        execution_environment=getattr(result, "execution_environment", None),
        system_profile=getattr(result, "system_profile", None),
        engine_version=_tuning_evidence_engine_version(result),
    )
    if message is not None:
        console.print(message, soft_wrap=True)


def _emit_load_failure_marker(result: Any) -> None:

    details = getattr(result, "validation_details", None)
    payload = details.get("load_failure") if isinstance(details, dict) else None
    if not isinstance(payload, dict):
        return
    click.echo(f"{LOAD_FAILURE_MARKER}{json.dumps(payload, sort_keys=True, separators=(',', ':'))}")


def _export_orchestrated_result(
    *,
    orchestrator: BenchmarkOrchestrator,
    result: Any,
    benchmark: str,
    scale: float,
    platform_label: str,
    mode_label: str,
    quiet: bool,
    export_formats: list[str] | None = None,
    funding: str | None = None,
    result_source: str | None = None,
) -> dict[str, Any]:
    from datetime import datetime

    if funding:
        result.funding = funding
    if result_source:
        result.result_source = result_source

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_path = orchestrator.directory_manager.get_result_path(
        benchmark,
        scale,
        platform_label,
        timestamp,
        result.execution_id,
        mode=mode_label,
    )

    exporter = ResultExporter(anonymize=False)
    exporter.output_dir = orchestrator.directory_manager.results_dir
    result.output_filename = result_path.name

    formats = export_formats or ["json"]
    with silence_output(enabled=bool(quiet)):
        exported_files = exporter.export_result(result, formats)
    _print_tuning_verification_summary(result, quiet=bool(quiet))
    _print_template_evidence_warning(result, quiet=bool(quiet))
    return exported_files


def _print_tuning_verification_summary(result: Any, *, quiet: bool) -> None:
    if quiet:
        return
    from benchbox.core.results.tuning_summary import format_tuning_verification, summarize_tuning_verification

    summary = summarize_tuning_verification(getattr(result, "applied_tuning_ledger", None))
    if summary is None:
        console.print("[dim]Tuning verification: not applicable (no tuning applied)[/dim]")
        return
    for line in format_tuning_verification(summary):
        console.print(line)


class PlatformOptionParamType(click.ParamType):
    name = "key=value"

    def convert(self, value: str, param, ctx) -> tuple[str, str]:
        if "=" not in value:
            self.fail("Expected KEY=VALUE format", param, ctx)
        key, raw = value.split("=", 1)
        key = key.strip()
        if not key:
            self.fail("Platform option key cannot be empty", param, ctx)
        return key, raw.strip()


class BenchmarkOptionParamType(click.ParamType):
    name = "key=value"

    def convert(self, value: str, param, ctx) -> tuple[str, str]:
        if "=" not in value:
            self.fail("Expected KEY=VALUE format", param, ctx)
        key, raw = value.split("=", 1)
        key = key.strip()
        if not key:
            self.fail("Benchmark option key cannot be empty", param, ctx)
        return key, raw.strip()

    def shell_complete(self, ctx, param, incomplete: str):
        from click.shell_completion import CompletionItem

        specs = _benchmark_specs_for_completion(ctx)
        if "=" in incomplete:
            key, _, value_prefix = incomplete.partition("=")
            return _complete_benchmark_option_value(specs, key.strip(), value_prefix)
        used = _used_benchmark_option_keys(ctx, specs)
        return [
            CompletionItem(f"{name}=", help=spec.help or "")
            for name, spec in sorted(specs.items())
            if name.startswith(incomplete.strip().lower()) and name not in used
        ]


def _benchmark_specs_for_completion(ctx) -> dict[str, Any]:
    from benchbox.cli.benchmark_hooks import BenchmarkHookRegistry

    params = getattr(ctx, "params", None) or {}
    benchmark = params.get("benchmark") or ""
    benchmark = str(benchmark).strip().lower()
    if not benchmark:
        return {}
    _ensure_benchmark_specs(benchmark)
    return BenchmarkHookRegistry.list_option_specs(benchmark)


def _ensure_benchmark_specs(benchmark: str) -> None:
    try:
        from benchbox.core.benchmark_loader import get_core_benchmark_class

        get_core_benchmark_class(benchmark)
    except (ValueError, ImportError):
        pass


def _find_benchmark_spec(specs: dict[str, Any], key: str):
    lowered = str(key).strip().lower()
    for name, spec in specs.items():
        if lowered == name or lowered in {str(a).lower() for a in spec.aliases}:
            return spec
    return None


def _used_benchmark_option_keys(ctx, specs: dict[str, Any]) -> set[str]:
    params = getattr(ctx, "params", None) or {}
    used: set = set()
    for key, _raw in params.get("benchmark_option_pairs") or ():
        spec = _find_benchmark_spec(specs, key)
        if spec is not None:
            used.add(spec.name.lower())
    return used


def _complete_benchmark_option_value(specs: dict[str, Any], key: str, value_prefix: str):
    from click.shell_completion import CompletionItem

    target = _find_benchmark_spec(specs, key)
    if target is None or not target.choices:
        return []
    if "," in value_prefix:
        stem, _, tail = value_prefix.rpartition(",")
        stem += ","
    else:
        stem, tail = "", value_prefix
    return [
        CompletionItem(f"{stem}{choice}", help=target.help or "")
        for choice in (str(choice) for choice in target.choices)
        if choice.startswith(tail)
    ]


def _derive_execution_type(phases: list[str]) -> str:
    from benchbox.core.run_service import map_phases_to_execution_type

    return map_phases_to_execution_type(phases)


from benchbox.cli.verbose_logging import setup_verbose_logging as setup_verbose_logging


def _force_recreate_requested(s: types.SimpleNamespace) -> bool:
    return bool(s.force_regenerate or s.parsed_platform_options.get("force_recreate"))


def _apply_cli_adapter(s: types.SimpleNamespace) -> None:
    force_config = s.force or ForceConfig()
    s.force_config = force_config
    s.force_regenerate = force_config.datagen
    s.force_upload = force_config.upload

    comp_config = s.compression or CompressionConfig()
    s.comp_config = comp_config
    s.compression_cli_set = s.compression is not None
    s.no_compression = not comp_config.enabled
    s.compression_type = comp_config.type
    s.compression_level = comp_config.level

    plan_cfg = s.plan_config or PlanCaptureConfig()
    s.plan_cfg = plan_cfg
    s.strict_plan_capture = plan_cfg.strict
    s.plan_queries_str = ",".join(plan_cfg.queries) if plan_cfg.queries else None
    s.show_query_plans = s.show_plans

    s.table_format_value = s.table_format.format if s.table_format else None
    s.table_format_compression = s.table_format.compression if s.table_format else "snappy"
    s.table_format_partition_cols = tuple(s.table_format.partition_cols) if s.table_format else ()

    val_config = s.validation or ValidationConfig()
    s.val_config = val_config
    s.validation_mode = val_config.mode if val_config.mode != "exact" or s.validation else None
    s.check_platforms = val_config.check_platforms
    s.enable_preflight_validation = val_config.preflight
    s.enable_postgen_manifest_validation = val_config.postgen
    s.enable_postload_validation = val_config.postload

    s.plan_queries = s.plan_queries_str


def _validate_initial_flags(s: types.SimpleNamespace) -> None:
    if s.quiet and s.verbose:
        console.print("[red]❌ --quiet cannot be used with -v/-vv flags[/red]")
        s.ctx.exit(2)

    if s.official:
        benchmark = normalize_benchmark_name(s.benchmark) if s.benchmark else None
        if benchmark == "tpch":
            from benchbox.core.tpch.compliance import OFFICIAL_SCALE_POINTS as tpc_allowed
        elif benchmark == "tpcds":
            from benchbox.core.tpcds.compliance import OFFICIAL_SCALE_POINTS as tpc_allowed
        else:
            tpc_allowed = frozenset({1, 10, 30, 100, 300, 1000, 3000, 10000, 30000, 100000})
        if s.scale not in tpc_allowed:
            console.print(f"[red]❌ Scale factor {s.scale} is not TPC-compliant[/red]")
            console.print(f"Allowed scale factors: {sorted(tpc_allowed)}")
            s.ctx.exit(1)

        if s.seed is None:
            console.print(
                "[yellow]⚠️  Warning: No --seed specified. Official TPC runs require a random seed for reproducibility.[/yellow]"
            )

        console.print("[bold blue]TPC-Compliant Official Benchmark Run[/bold blue]")
        console.print(f"Scale Factor: {s.scale} (TPC-allowed)")
        if s.seed is not None:
            console.print(f"Seed: {s.seed}")
        console.print()


def _strict_translation_config_entry(s: types.SimpleNamespace) -> dict[str, Any]:
    entries: dict[str, Any] = {"normalize_plan_literals": s.normalize_plan_literals}
    if getattr(s, "strict_translation", False):
        entries["strict_translation"] = True
    return entries


def _plan_capture_override_entries(s: types.SimpleNamespace) -> dict[str, Any]:
    return {
        "capture_plans": s.capture_plans,
        "strict_plan_capture": s.strict_plan_capture,
        "plan_queries": s.plan_queries,
        "normalize_plan_literals": s.normalize_plan_literals,
        "execution_mode": s.resolved_mode,
    }


def _tuning_override_entries(s: types.SimpleNamespace) -> dict[str, Any]:
    entries: dict[str, Any] = {"tuning_enabled": s.tuning_enabled, "force_upload": bool(s.force_upload)}
    entries.update(_plan_capture_override_entries(s))
    entries["execution_engine"] = getattr(s, "resolved_execution_engine", None) or "default"
    if s.loaded_unified_config:
        entries["unified_tuning_configuration"] = s.loaded_unified_config
    if s.df_tuning_config:
        entries["df_tuning_config"] = s.df_tuning_config
    return entries


def _validate_non_interactive(s: types.SimpleNamespace) -> None:
    if not s.non_interactive:
        return
    os.environ["BENCHBOX_NON_INTERACTIVE"] = "true"
    if s.logger:
        s.logger.debug("Non-interactive mode enabled via CLI flag")

    phase_list = [p.strip() for p in s.phases.split(",") if p.strip()]
    is_data_only = phase_list == ["generate"] or (
        "generate" in phase_list
        and not set(phase_list) & {"load", "statistics", "warmup", "power", "throughput", "maintenance"}
    )

    missing_args = []
    if not s.benchmark:
        missing_args.append("--benchmark")
    if not s.platform and not is_data_only:
        missing_args.append("--platform")

    if missing_args:
        console.print("[red]❌ Error: Non-interactive mode requires all parameters[/red]")
        console.print(f"[yellow]Missing: {', '.join(missing_args)}[/yellow]")
        console.print("[dim]Use interactive mode by omitting --non-interactive flag[/dim]")
        if s.logger:
            s.logger.error(f"Non-interactive mode missing required args: {missing_args}")
        s.ctx.exit(2)


def _parse_phases_list(s: types.SimpleNamespace) -> None:
    valid_phases = set(VALID_PHASES)
    phase_list = [p.strip() for p in s.phases.split(",") if p.strip()]

    invalid_phases = set(phase_list) - valid_phases
    if invalid_phases:
        console.print(f"[red]❌ Error: Invalid phases: {', '.join(invalid_phases)}[/red]")
        console.print(f"[yellow]Valid phases: {', '.join(sorted(valid_phases))}[/yellow]")
        if s.logger:
            s.logger.error(f"Invalid phases specified: {invalid_phases}")
        s.ctx.exit(1)

    seen = set()
    phases_to_run: list[str] = []
    for p in phase_list:
        if p not in seen:
            phases_to_run.append(p)
            seen.add(p)
    s.phases_to_run = phases_to_run


def _parse_queries_list(s: types.SimpleNamespace) -> None:
    s.queries_to_run = None
    if s.queries is None:
        return

    import re

    queries_to_run = [q.strip() for q in s.queries.split(",") if q.strip()]
    if not queries_to_run:
        console.print("[red]❌ Error: --queries flag provided but no valid query IDs found[/red]")
        if s.logger:
            s.logger.error("Empty queries list after parsing")
        s.ctx.exit(1)

    max_queries = 100
    if len(queries_to_run) > max_queries:
        console.print(f"[red]❌ Too many queries: {len(queries_to_run)} (max {max_queries})[/red]")
        if s.logger:
            s.logger.error(f"Query list too long: {len(queries_to_run)} exceeds limit of {max_queries}")
        s.ctx.exit(1)

    if any(len(q) > MAX_QUERY_ID_LENGTH for q in queries_to_run):
        too_long = [q for q in queries_to_run if len(q) > MAX_QUERY_ID_LENGTH]
        console.print(f"[red]❌ Query ID too long (max {MAX_QUERY_ID_LENGTH} chars): {', '.join(too_long[:3])}[/red]")
        if s.logger:
            s.logger.error(f"Query IDs exceed length limit: {too_long}")
        s.ctx.exit(1)

    query_id_pattern = re.compile(r"^[a-zA-Z0-9_.\-]+$")
    invalid_format = [q for q in queries_to_run if not query_id_pattern.match(q)]
    if invalid_format:
        console.print(
            f"[red]❌ Invalid query ID format: {', '.join(invalid_format[:5])} "
            f"(must be alphanumeric, dash, underscore, or dot)[/red]"
        )
        if s.logger:
            s.logger.error(f"Invalid query ID format: {invalid_format}")
        s.ctx.exit(1)

    incompatible_phases = {"warmup", "throughput", "maintenance"} & set(s.phases_to_run)
    query_phases = {"power", "standard"} & set(s.phases_to_run)
    if incompatible_phases and not query_phases:
        console.print(
            f"[red]❌ --queries only works with power/standard phases. "
            f"Current phases ({', '.join(sorted(s.phases_to_run))}) are incompatible.[/red]"
        )
        if s.logger:
            s.logger.error(f"--queries flag requires power or standard phase, got: {s.phases_to_run}")
        s.ctx.exit(1)
    elif incompatible_phases:
        console.print(f"[yellow]⚠️  --queries ignored for: {', '.join(sorted(incompatible_phases))}[/yellow]")
        if s.logger:
            s.logger.warning(f"--queries flag will be ignored for phases: {incompatible_phases}")

    s.queries_to_run = queries_to_run


def _derive_exec_type_and_banner(s: types.SimpleNamespace) -> None:
    if s.iterations is not None and "power" not in s.phases_to_run and "standard" not in s.phases_to_run:
        console.print("[yellow]⚠️  Warning: --iterations has no effect without a power or standard phase.[/yellow]")

    s.test_execution_type = _derive_execution_type(s.phases_to_run)
    if s.logger:
        s.logger.debug(f"Test execution type: {s.test_execution_type}")
    s.execution_mode = s.test_execution_type

    is_interactive = sys.stdin.isatty() and not (s.platform and s.benchmark)
    if not s.quiet and is_interactive:
        if (logo := rich_logo()) is not None:
            console.print(logo, end="\n\n")
        console.print(
            Panel.fit(
                Text("BenchBox Interactive Benchmark Runner", style="bold blue"),
                style="blue",
            )
        )


def _resolve_tuning(s: types.SimpleNamespace, *, non_interactive_override: bool | None = None) -> None:
    s.config = s.ctx.obj["config"]
    if s.logger:
        s.logger.debug(f"Loaded configuration from: {s.config.config_path}")
        s.logger.debug(f"Configuration validation status: {s.config.validate_config()}")

    try:
        tuning_resolution = resolve_tuning(
            tuning_arg=s.tuning,
            platform=s.platform,
            benchmark=s.benchmark,
            config_manager=s.config,
            console=console,
            logger=s.logger,
            quiet=bool(s.quiet),
            non_interactive=s.non_interactive if non_interactive_override is None else non_interactive_override,
            mode=getattr(s, "resolved_mode", None),
        )
    except ValueError as e:
        console.print(f"[red]❌ {e}[/red]")
        if s.logger:
            s.logger.error(f"Tuning resolution failed: {e}")
        s.ctx.exit(1)

    s.tuning_resolution = tuning_resolution
    s.tuning_enabled = tuning_resolution.enabled
    s.tuning_config_file = str(tuning_resolution.config_file) if tuning_resolution.config_file else None
    s.use_auto_tuning = tuning_resolution.mode == TuningMode.AUTO

    _reject_official_fallback(s, tuning_resolution)

    if not s.quiet:
        display_tuning_resolution(tuning_resolution, console, verbose=bool(s.verbose))

    if s.logger:
        s.logger.debug(
            f"Tuning resolution: mode={tuning_resolution.mode.value}, "
            f"source={tuning_resolution.source.value}, enabled={s.tuning_enabled}"
        )

    _load_unified_tuning_config(s, non_interactive_override=non_interactive_override)


def _load_unified_tuning_config(s: types.SimpleNamespace, *, non_interactive_override: bool | None = None) -> None:
    s.loaded_unified_config = None
    tuning_resolution = s.tuning_resolution
    if tuning_resolution.config_file:
        if getattr(s, "resolved_mode", None) == "sql" and is_dataframe_tuning_file(tuning_resolution.config_file):
            console.print(
                f"[red]❌ Tuning file {tuning_resolution.config_file} is a DataFrame tuning "
                "configuration, but this is a SQL-mode run; it would load as an empty "
                "configuration. Use a SQL tuning file "
                f"([cyan]benchbox tuning init --platform {s.platform} --mode sql[/cyan]) "
                "or run with [cyan]--mode dataframe[/cyan].[/red]"
            )
            s.ctx.exit(1)
        try:
            s.loaded_unified_config = s.config.load_unified_tuning_config(tuning_resolution.config_file, s.platform)
            if s.logger:
                s.logger.debug(f"Loaded tuning config from: {tuning_resolution.config_file}")
        except Exception as e:
            console.print(f"[red]❌ Failed to load tuning configuration: {e}[/red]")
            if s.logger:
                s.logger.error(f"Failed to load tuning configuration: {e}", exc_info=True)
            s.ctx.exit(1)
    elif tuning_resolution.source == TuningSource.BASELINE:
        s.loaded_unified_config = build_baseline_unified_config()
        if s.logger:
            s.logger.debug("No tuning mode: all constraints disabled for baseline")
    elif tuning_resolution.source == TuningSource.FALLBACK:
        effective_non_interactive = s.non_interactive if non_interactive_override is None else non_interactive_override
        if not effective_non_interactive:
            console.print("\n[bold cyan]Tuning Configuration[/bold cyan]")
            if Confirm.ask("Would you like to configure tuning options?", default=False):
                from benchbox.cli.tuning import run_tuning_wizard

                profiler = SystemProfiler()
                system_profile = profiler.get_system_profile()
                s.loaded_unified_config = run_tuning_wizard(
                    benchmark=s.benchmark,
                    platform=s.platform,
                    system_profile=system_profile,
                    interactive=True,
                )
                s.tuning_enabled, s.tuning = infer_runtime_tuning_mode(s.loaded_unified_config)
                if s.tuning_enabled:
                    tuning_resolution.mode = TuningMode.TUNED
                    tuning_resolution.source = TuningSource.INTERACTIVE_WIZARD
                else:
                    tuning_resolution.mode = TuningMode.NOTUNING
                    tuning_resolution.source = TuningSource.BASELINE
                console.print("[green]✅ Tuning configuration completed[/green]")
            else:
                s.loaded_unified_config = build_default_unified_config(s.platform)
        else:
            s.loaded_unified_config = build_default_unified_config(s.platform)
        if s.logger:
            s.logger.debug("Using basic constraints-only configuration (fallback)")
    else:
        s.loaded_unified_config = build_default_unified_config(s.platform)
        warn_sql_auto_mode(tuning_resolution, s.resolved_mode, console, s.logger, quiet=bool(s.quiet))

    s.tuning_source_value = tuning_resolution.source.value
    s.tuning_template_ref = resolve_template_reference(tuning_resolution.config_file)


def _resolve_data_organization(s: types.SimpleNamespace, *, resolve_dataframe: bool = True) -> None:
    try:
        _apply_platform_optimization_overrides(
            s.loaded_unified_config,
            sorted_ingestion_mode=s.sorted_ingestion_mode,
            sorted_ingestion_method=s.sorted_ingestion_method,
            parsed_platform_options=s.parsed_platform_options,
        )
    except ValueError as exc:
        console.print(f"[red]❌ {exc}[/red]")
        s.ctx.exit(1)

    data_organization_payload = _build_data_organization_from_tuning(s.loaded_unified_config)
    try:
        data_organization_payload = _resolve_data_organization_payload(
            s.benchmark, s.presort, data_organization_payload
        )
    except ValueError as exc:
        console.print(f"[red]❌ {exc}[/red]")
        s.ctx.exit(1)
    s.data_organization_payload = data_organization_payload
    if data_organization_payload is None:
        os.environ.pop(DATA_ORGANIZATION_ENV, None)
    else:
        os.environ[DATA_ORGANIZATION_ENV] = json.dumps(data_organization_payload)

    if not resolve_dataframe:
        return

    try:
        s.df_tuning_config = resolve_dataframe_tuning_config(
            platform_name=s.platform,
            mode_name=s.resolved_mode,
            tuning_enabled_flag=s.tuning_enabled,
            use_auto_tuning_flag=s.use_auto_tuning,
            tuning_config_file_path=s.tuning_config_file,
            console=console,
            logger=s.logger,
            quiet=bool(s.quiet),
        )
    except ValueError as exc:
        console.print(f"[red]❌ {exc}[/red]")
        s.ctx.exit(1)


def _resolve_compression_settings(s: types.SimpleNamespace) -> None:
    if s.no_compression:
        s.compress_data = False
        if s.compression_type != "none":
            s.compression_type = "none"
        if s.logger:
            s.logger.debug("Compression disabled via --no-compression flag")
    else:
        env_no_compression = os.getenv("BENCHBOX_NO_COMPRESSION", "").lower() in ["true", "1", "yes", "on"]
        if env_no_compression:
            s.compress_data = False
            s.compression_type = "none"
            if s.logger:
                s.logger.debug("Compression disabled via BENCHBOX_NO_COMPRESSION environment variable")
        elif s.compression_cli_set:
            s.compress_data = s.comp_config.enabled
            if s.logger:
                s.logger.debug(
                    "Compression set via CLI: enabled=%s, type=%s, level=%s",
                    s.compress_data,
                    s.compression_type,
                    s.compression_level,
                )
        else:
            config_compression_enabled = s.config.get("output.compression.enabled", False)
            s.compress_data = config_compression_enabled
            s.compression_type = s.config.get("output.compression.type", "zstd")
            s.compression_level = s.config.get("output.compression.level", None)
            if s.logger:
                s.logger.debug(f"Compression enabled: type={s.compression_type}, level={s.compression_level}")

    if s.compress_data and s.compression_type != "none":
        manager = CompressionManager()
        available = manager.get_available_compressors()
        if s.compression_type not in available:
            warning = (
                f"[yellow]⚠️ Compression type '{s.compression_type}' is not available. "
                "Falling back to uncompressed output.[/yellow]"
            )
            console.print(warning)
            if s.logger:
                s.logger.warning(
                    "Compression type %s unavailable (available: %s). Falling back to no compression.",
                    s.compression_type,
                    available,
                )
            s.compress_data = False
            s.compression_type = "none"
            s.compression_level = None

    if not s.compress_data:
        s.compression_type = "none"
        s.compression_level = None


def _capture_resolved_run_plan(s: types.SimpleNamespace) -> ResolvedRunPlan:
    return capture_resolved_run_plan(s, canonical_tuning_mode=_canonical_tuning_mode(s))


def _resolve_run_request(
    s: types.SimpleNamespace,
    *,
    apply_default_scale: bool,
    tuning_non_interactive: bool | None = None,
) -> None:
    _apply_cli_adapter(s)
    if apply_default_scale:
        _apply_benchmark_default_scale(s)
    _validate_initial_flags(s)
    _parse_plat_bench_options(s)
    _validate_non_interactive(s)
    _parse_phases_list(s)
    _parse_queries_list(s)
    _derive_exec_type_and_banner(s)

    s.platform_manager = get_platform_manager()
    _check_platforms_status(s)
    _resolve_platform_mode(s)
    _check_benchmark_platform_compatibility(s)
    _resolve_tuning(s, non_interactive_override=tuning_non_interactive)
    _resolve_data_organization(s)
    _resolve_compression_settings(s)
    _validate_output_dir(s)
    s.resolved_run_plan = _capture_resolved_run_plan(s)


def _validate_output_dir(s: types.SimpleNamespace) -> None:
    if not s.output:
        return
    try:
        ValidationRules.validate_output_directory(s.output, table_mode=s.table_mode)
        if is_cloud_path(s.output):
            console.print(f"\n[green]✅[/green] Cloud storage output validated: {s.output}")
        else:
            console.print(f"\n[green]✅[/green] Output directory validated: {s.output}")
    except (ValidationError, CloudStorageError) as e:
        error_handler = create_error_handler(console)
        context = ErrorContext(
            operation="output_validation",
            stage="pre_execution",
            user_input={"output_path": s.output},
        )
        error_handler.handle_error(e, context)
        s.ctx.exit(1)


def _prepare_run_state(s: types.SimpleNamespace) -> None:
    s.run_request = _current_run_request(s)
    _resolve_run_request(s, apply_default_scale=True)

    s.database_config = None
    s.benchmark_config = None


def _stream_timeout_config_entries(s: types.SimpleNamespace) -> dict[str, Any]:
    config = getattr(s, "config", None)
    if config is None:
        return {}
    entries: dict[str, Any] = {}
    stream_timeout = config.get("execution.concurrent_queries.stream_timeout_seconds")
    if stream_timeout is not None:
        entries["stream_timeout_seconds"] = int(stream_timeout)
        entries["stream_timeout_source"] = "config file"
    if config.get("execution.concurrent_queries.cancel_on_timeout", False):
        entries["cancel_on_timeout"] = True
    return entries


def _build_benchmark_config(
    s: types.SimpleNamespace,
    benchmark_info: dict[str, Any],
) -> BenchmarkConfig:
    plan: ResolvedRunPlan = s.resolved_run_plan
    assert plan.benchmark is not None
    options: dict[str, Any] = {
        **s.verbosity_payload,
        "estimated_time_range": benchmark_info.get("estimated_time_range", (0, 60)),
        "complexity": benchmark_info.get("complexity", "medium"),
        "tuning_enabled": plan.tuning_enabled,
        "table_mode": plan.table_mode,
        "unified_tuning_configuration": plan.loaded_unified_config,
        "force_regenerate": s.force_regenerate,
        "enable_preflight_validation": s.enable_preflight_validation,
        "enable_postgen_manifest_validation": s.enable_postgen_manifest_validation,
        "enable_postload_validation": s.enable_postload_validation,
        "ignore_memory_warnings": s.ignore_memory_warnings,
        **({"df_tuning_config": plan.dataframe_tuning_config} if plan.dataframe_tuning_config else {}),
        **({"data_organization": dict(plan.data_organization)} if plan.data_organization is not None else {}),
        **({"seed": plan.seed} if plan.seed is not None else {}),
        **({"power_iterations": plan.iterations} if plan.iterations is not None else {}),
        **_stream_timeout_config_entries(s),
        **({"validation_mode": s.validation_mode} if s.validation_mode is not None else {}),
        **({"cache_dir": str(Path.home() / ".benchbox" / "datagen")} if s.global_cache else {}),
        **({"table_format": s.table_format_value} if s.table_format_value is not None else {}),
        **({"table_format_compression": s.table_format_compression} if s.table_format_compression is not None else {}),
        **(
            {"table_format_partition_cols": list(s.table_format_partition_cols)}
            if s.table_format_partition_cols
            else {}
        ),
        **({"presort": s.presort} if s.presort is not None else {}),
        **_strict_translation_config_entry(s),
        **_platform_option_config_entries(s),
        **({"benchmark_options": s.parsed_benchmark_options} if s.parsed_benchmark_options else {}),
        **({"client_region": s.client_region} if getattr(s, "client_region", None) is not None else {}),
        **({"client_cloud": s.client_cloud} if getattr(s, "client_cloud", None) is not None else {}),
        **({"link_probe": not s.no_link_probe} if getattr(s, "no_link_probe", None) is not None else {}),
    }
    return BenchmarkConfig(
        name=plan.benchmark,
        display_name=benchmark_info.get("display_name", plan.benchmark.upper()),
        scale_factor=plan.scale,
        queries=list(plan.queries) if plan.queries is not None else None,
        concurrency=plan.concurrency,
        compress_data=plan.compression_enabled,
        compression_type=plan.compression_type,
        compression_level=plan.compression_level,
        test_execution_type=plan.test_execution_type,
        capture_plans=s.capture_plans,
        analyze_plans=s.analyze_plans,
        strict_plan_capture=s.strict_plan_capture,
        stats_reset=s.stats_reset,
        stats_per_table_timing=s.stats_per_table_timing,
        official=s.official,
        client_region=getattr(s, "client_region", None),
        client_cloud=getattr(s, "client_cloud", None),
        link_probe=not getattr(s, "no_link_probe", False),
        options=options,
    )


def _build_execution_context_from_plan(s: types.SimpleNamespace) -> ExecutionContext:
    plan: ResolvedRunPlan = s.resolved_run_plan
    return _build_execution_context(
        phases_to_run=list(plan.phases),
        seed=plan.seed,
        compression=CompressionConfig(
            enabled=plan.compression_enabled,
            type=plan.compression_type,
            level=plan.compression_level,
        ),
        mode=plan.resolved_mode,
        official=s.official,
        validation=s.val_config,
        force=s.force_config,
        queries_to_run=list(plan.queries) if plan.queries is not None else None,
        capture_plans=s.capture_plans,
        strict_plan_capture=s.strict_plan_capture,
        non_interactive=s.non_interactive,
        tuning=plan.canonical_tuning_mode,
    )


def _dry_run_validate_inputs(s: types.SimpleNamespace) -> None:
    ctx = s.ctx
    logger = s.logger
    if s.test_execution_type == "data_only":
        if not s.benchmark:
            console.print("[red]❌ Dry run with --phases generate requires --benchmark parameter[/red]")
            if logger:
                logger.error("Dry run with --phases generate requires --benchmark parameter")
            ctx.exit(1)
        if s.platform:
            console.print("[yellow]⚠️️  Note: Platform parameter ignored in data-only dry run[/yellow]")
    else:
        if not (s.platform and s.benchmark):
            console.print("[red]❌ Dry run mode requires --platform and --benchmark parameters[/red]")
            console.print("[yellow]Use --phases generate if you only want to preview data generation[/yellow]")
            if logger:
                logger.error("Dry run mode requires --platform and --benchmark parameters")
            ctx.exit(1)

    if logger:
        logger.debug(f"Validating scale factor for dry run: {s.scale}")
    if s.scale >= 1 and s.scale != int(s.scale):
        console.print(f"[red]❌ Scale factors >= 1 must be whole integers. Got: {s.scale}[/red]")
        console.print("[yellow]Use values like 1, 2, 10, etc. for large scale factors[/yellow]")
        console.print("[yellow]Use values like 0.1, 0.01, 0.001, etc. for small scale factors[/yellow]")
        if logger:
            logger.error(f"Invalid scale factor for dry run: {s.scale}")
        ctx.exit(1)


def _warn_tpcds_subscale(s: types.SimpleNamespace) -> None:
    if s.benchmark == "tpcds" and s.scale < 1.0 and not s.quiet:
        console.print(
            "[yellow]⚠  TPC-DS UNOFFICIAL SUBSCALE RUN[/yellow]\n"
            f"   Scale factor: [bold]{s.scale}[/bold] (< 1.0 - not TPC-DS compliant)\n"
            "   Results are for development use only and must not be published or\n"
            "   submitted as official TPC-DS results. Official TPC metrics\n"
            "   (power@size, throughput@size) will not be computed.\n"
            "   See _sources/tpcds-subscale-contract.md for the data contract."
        )


def _warn_tpch_subscale(s: types.SimpleNamespace) -> None:
    if s.benchmark == "tpch" and s.scale < 1.0 and not s.quiet:
        console.print(
            "[yellow]⚠  TPC-H UNOFFICIAL SUBSCALE RUN[/yellow]\n"
            f"   Scale factor: [bold]{s.scale}[/bold] (< 1.0 - not TPC-H compliant)\n"
            "   Results are for development use only and must not be published or\n"
            "   submitted as official TPC-H results. Official TPC metrics\n"
            "   (power@size, throughput@size) will not be computed."
        )


def _warn_unofficial_subscale(s: types.SimpleNamespace) -> None:
    _warn_tpcds_subscale(s)
    _warn_tpch_subscale(s)


def _run_dry_run(s: types.SimpleNamespace) -> None:
    ctx = s.ctx
    logger = s.logger
    if logger:
        logger.debug(f"Entering dry run mode, output directory: {s.dry_run}")

    _dry_run_validate_inputs(s)

    from benchbox.cli.dryrun import DryRunExecutor

    if logger:
        logger.debug("Creating database and benchmark managers for dry run")
    db_manager = DatabaseManager()
    db_manager.set_verbosity(s.verbosity_settings)
    bench_manager = BenchmarkManager()
    bench_manager.set_verbosity(s.verbosity_settings)

    if logger:
        logger.debug(f"Validating benchmark '{s.benchmark}' exists")
    if s.benchmark not in bench_manager.benchmarks:
        console.print(f"[red]❌ Unknown benchmark: {s.benchmark}[/red]")
        console.print(f"Available benchmarks: {', '.join(bench_manager.benchmarks.keys())}")
        if logger:
            logger.error(f"Unknown benchmark: {s.benchmark}. Available: {list(bench_manager.benchmarks.keys())}")
        ctx.exit(1)

    database_config = _dry_run_build_db_config(s, db_manager)

    if logger:
        logger.debug(f"Creating benchmark configuration for: {s.benchmark}")
    benchmark_info = bench_manager.benchmarks[s.benchmark]

    try:
        bench_manager.validate_scale_factor(s.benchmark, s.scale)
    except ValueError as e:
        console.print(f"[red]❌ {e}[/red]")
        if logger:
            logger.error(f"Scale factor validation failed: {e}")
        ctx.exit(1)

    _warn_unofficial_subscale(s)

    benchmark_config = _build_benchmark_config(s, benchmark_info)

    if logger:
        logger.debug(f"Benchmark config created: {benchmark_config}")
        logger.debug("Getting system profile for dry run")
    profiler = SystemProfiler()
    system_profile = profiler.get_system_profile()

    if logger:
        logger.debug(
            f"System profile: CPU cores={system_profile.cpu_cores_logical}, Memory={system_profile.memory_total_gb}GB"
        )
        logger.debug(f"Executing dry run with output directory: {s.dry_run}")
    dry_run_executor = DryRunExecutor(output_dir=s.dry_run)
    preview_platform = None
    if database_config is None and s.execution_mode != "data_only":
        preview_platform = s.platform_key
    dry_run_result = dry_run_executor.execute_dry_run(
        benchmark_config,
        system_profile,
        database_config,
        preview_platform=preview_platform,
        preview_platform_note=getattr(s, "dry_run_db_config_error", None),
    )

    if logger:
        logger.debug("Dry run completed")

    dry_run_executor.display_dry_run_results(dry_run_result)

    filename_prefix = f"{s.benchmark}_{'data-only' if s.execution_mode == 'data_only' else s.platform}"
    saved_files = dry_run_executor.save_dry_run_results(dry_run_result, filename_prefix)

    console.print("\n[green]✅ Dry run completed[/green]")
    if s.test_execution_type == "data_only":
        console.print(f"[dim]Data generation preview for {s.benchmark} at scale {s.scale}[/dim]")
    else:
        console.print(
            f"[dim]Configuration and queries previewed for {s.benchmark} on {s.platform} at scale {s.scale}[/dim]"
        )

    if saved_files:
        console.print("\n[dim]Output files saved to:[/dim]")
        for _file_type, file_path in saved_files.items():
            console.print(f"  [cyan]{file_path}[/cyan]")


def _dry_run_build_db_config(s: types.SimpleNamespace, db_manager: DatabaseManager) -> DatabaseConfig | None:
    ctx = s.ctx
    logger = s.logger
    if s.execution_mode == "data_only":
        if logger:
            logger.debug("Skipping database configuration for data-only dry run")
        return None

    if logger:
        logger.debug(f"Creating database configuration for: {s.platform}")
    assert s.platform_key is not None
    overrides = {
        **s.verbosity_payload,
        "force_recreate": _force_recreate_requested(s),
        **_tuning_override_entries(s),
    }
    database_config: DatabaseConfig | None = None
    try:
        database_config = db_manager.create_config(
            s.platform_key,
            dict(s.parsed_platform_options),
            overrides,
            deployment_selector=getattr(s, "deployment_selector", None),
            gateway_selector=getattr(s, "gateway", None),
            explicit_option_keys=getattr(s, "explicit_platform_option_keys", ()),
        )
    except PlatformOptionError as exc:
        console.print(f"[red]❌ {exc}[/red]")
        if logger:
            logger.error(f"Database configuration failed: {exc}")
        ctx.exit(1)
    except RuntimeError as exc:
        if logger:
            logger.debug(f"Driver not installed, using minimal config for dry run: {exc}")
        s.dry_run_db_config_error = str(exc).splitlines()[0]
        database_config = None
    promote_tuning_provenance(database_config, s.tuning_enabled, s.tuning_source_value, s.tuning_template_ref)
    if logger:
        logger.debug("Database configuration created for dry run")
    return database_config


def _run_direct(s: types.SimpleNamespace) -> None:
    ctx = s.ctx
    logger = s.logger
    if logger:
        logger.debug(f"Entering direct execution mode: platform={s.platform}, benchmark={s.benchmark}")
        logger.debug(f"Validating scale factor for direct execution: {s.scale}")
    if s.scale >= 1 and s.scale != int(s.scale):
        console.print(f"[red]❌ Scale factors >= 1 must be whole integers. Got: {s.scale}[/red]")
        console.print("[yellow]Use values like 1, 2, 10, etc. for large scale factors[/yellow]")
        console.print("[yellow]Use values like 0.1, 0.01, 0.001, etc. for small scale factors[/yellow]")
        if logger:
            logger.error(f"Invalid scale factor for direct execution: {s.scale}")
        return

    if logger:
        logger.info(f"Starting direct benchmark execution: {s.benchmark} on {s.platform} at scale {s.scale}")
        logger.debug("Creating database and benchmark managers for direct execution")
    db_manager = DatabaseManager()
    db_manager.set_verbosity(s.verbosity_settings)
    bench_manager = BenchmarkManager()
    bench_manager.set_verbosity(s.verbosity_settings)

    assert s.platform_key is not None
    overrides = {
        **s.verbosity_payload,
        "force_recreate": _force_recreate_requested(s),
        "show_query_plans": s.show_query_plans,
        **_tuning_override_entries(s),
    }
    try:
        database_config = db_manager.create_config(
            s.platform_key,
            dict(s.parsed_platform_options),
            overrides,
            deployment_selector=getattr(s, "deployment_selector", None),
            gateway_selector=getattr(s, "gateway", None),
            explicit_option_keys=getattr(s, "explicit_platform_option_keys", ()),
        )
    except PlatformOptionError as exc:
        console.print(f"[red]❌ {exc}[/red]")
        if logger:
            logger.error(f"Database configuration failed: {exc}")
        ctx.exit(1)
    promote_tuning_provenance(database_config, s.tuning_enabled, s.tuning_source_value, s.tuning_template_ref)

    _driver_version = database_config.driver_version_actual or database_config.driver_version_resolved
    _mode_tag = " \\[external]" if s.table_mode == "external" else ""
    if _driver_version:
        console.print(
            f"Running {s.benchmark} on {s.platform} at scale {s.scale} \\[driver {_driver_version}]{_mode_tag}"
        )
    else:
        console.print(f"Running {s.benchmark} on {s.platform} at scale {s.scale}{_mode_tag}")

    if s.benchmark not in bench_manager.benchmarks:
        console.print(f"[red]❌ Unknown benchmark: {s.benchmark}[/red]")
        console.print(f"Available benchmarks: {', '.join(bench_manager.benchmarks.keys())}")
        return

    benchmark_info = bench_manager.benchmarks[s.benchmark]

    try:
        bench_manager.validate_scale_factor(s.benchmark, s.scale)
    except ValueError as e:
        console.print(f"[red]❌ {e}[/red]")
        if logger:
            logger.error(f"Scale factor validation failed: {e}")
        ctx.exit(1)

    _warn_unofficial_subscale(s)

    benchmark_config = _build_benchmark_config(s, benchmark_info)

    profiler = SystemProfiler()
    system_profile = profiler.get_system_profile()

    execution_context = _build_execution_context_from_plan(s)

    orchestrator = BenchmarkOrchestrator()
    orchestrator.set_verbosity(s.verbosity_settings)

    if s.output:
        normalized_output = normalize_output_root(s.output, s.benchmark, s.scale)
        assert normalized_output is not None
        orchestrator.set_custom_output_dir(normalized_output)

    result = _execute_orchestrated_run(
        orchestrator,
        benchmark_config,
        system_profile,
        database_config,
        list(s.resolved_run_plan.phases),
        quiet=bool(s.quiet),
        no_progress=bool(s.no_progress),
        no_monitoring=bool(s.no_monitoring),
        execution_context=execution_context,
    )

    _direct_handle_result(s, result, orchestrator, benchmark_config)


def _direct_handle_result(
    s: types.SimpleNamespace,
    result: Any,
    orchestrator: BenchmarkOrchestrator,
    benchmark_config: BenchmarkConfig,
) -> None:
    non_clean_reason = result_non_clean_reason(result)
    if result.validation_status not in ["FAILED", "INTERRUPTED"]:
        if not s.quiet:
            console.print("\n[bold]Exporting results...[/bold]")

        exported_files = _export_orchestrated_result(
            orchestrator=orchestrator,
            result=result,
            benchmark=s.benchmark,
            scale=s.scale,
            platform_label=s.platform.lower(),
            mode_label=s.resolved_mode,
            quiet=bool(s.quiet),
            export_formats=["json"],
            funding=getattr(s, "funding", None),
            result_source=getattr(s, "result_source", None),
        )

        if s.quiet:
            for _format_name, filepath in exported_files.items():
                click.echo(filepath)
        else:
            if non_clean_reason:
                console.print(
                    f"\n[yellow]⚠ Benchmark completed with non-clean status: {result.validation_status}[/yellow]\n"
                    f"[yellow]Reason:[/yellow] {non_clean_reason}"
                )
            else:
                console.print(f"\n[green]✅ Benchmark completed: {result.validation_status}[/green]")
            for format_name, filepath in exported_files.items():
                console.print(f"{format_name.upper()}: [dim]{filepath}[/dim]")

        _render_post_run_charts(result, console, s.quiet)

        if s.publish and not non_clean_reason and (json_bundle := exported_files.get("json")):
            from benchbox.cli.commands.publish import publish_bundle

            if not s.quiet:
                console.print("\n[bold]Publishing result bundle...[/bold]")
            publish_bundle(
                source_bundle=json_bundle,
                target=s.publish_target,
                label=s.publish_label,
                quiet=bool(s.quiet),
            )

        from benchbox.cli.preferences import save_last_run_config

        save_last_run_config(
            database=s.platform,
            benchmark=s.benchmark,
            scale=s.scale,
            tuning_mode=s.tuning,
            phases=s.phases_to_run,
            concurrency=benchmark_config.concurrency,
            compress_data=s.compress_data,
            compression_type=s.compression_type,
            compression_level=s.compression_level,
            test_execution_type=s.test_execution_type,
            queries=list(benchmark_config.queries) if getattr(benchmark_config, "queries", None) is not None else None,
            mode=getattr(s, "resolved_mode", None),
            seed=s.seed,
            iterations=s.resolved_run_plan.iterations,
            non_replayable_options=list(s.resolved_run_plan.non_replayable_options),
            output=s.output,
            additional_options={"table_mode": s.table_mode},
        )
        if result_cli_failure_reason(result):
            s.ctx.exit(1)
    else:
        _emit_load_failure_marker(result)
        console.print(f"\n[red]❌ Benchmark failed: {result.validation_status}[/red]")
        s.ctx.exit(1)


def _data_or_load_build_db_config(s: types.SimpleNamespace, db_manager: DatabaseManager) -> DatabaseConfig | None:
    if s.execution_mode != "load_only":
        return None

    ctx = s.ctx
    logger = s.logger
    assert s.platform_key is not None
    overrides = {
        **s.verbosity_payload,
        "force_recreate": _force_recreate_requested(s),
        **_tuning_override_entries(s),
    }
    try:
        database_config = db_manager.create_config(
            s.platform_key,
            dict(s.parsed_platform_options),
            overrides,
            deployment_selector=getattr(s, "deployment_selector", None),
            gateway_selector=getattr(s, "gateway", None),
            explicit_option_keys=getattr(s, "explicit_platform_option_keys", ()),
        )
    except PlatformOptionError as exc:
        console.print(f"[red]❌ {exc}[/red]")
        if logger:
            logger.error(f"Database configuration failed: {exc}")
        ctx.exit(1)
    promote_tuning_provenance(database_config, s.tuning_enabled, s.tuning_source_value, s.tuning_template_ref)
    return database_config


def _data_or_load_validate_inputs(s: types.SimpleNamespace) -> None:
    ctx = s.ctx
    logger = s.logger
    if s.test_execution_type == "data_only":
        if s.platform:
            if not s.quiet:
                console.print("[yellow]⚠️️  Note: Platform parameter ignored in data-only mode[/yellow]")
    else:
        if not s.platform:
            console.print("[red]❌ Error: --platform parameter is required for --phases load[/red]")
            if logger:
                logger.error("Platform parameter is required for --phases load")
            ctx.exit(1)


def _run_data_or_load_only(s: types.SimpleNamespace) -> None:
    logger = s.logger
    if logger:
        logger.debug(f"Entering {s.execution_mode} mode")

    _data_or_load_validate_inputs(s)

    if not s.benchmark:
        console.print("[red]❌ Error: --benchmark parameter is required[/red]")
        if logger:
            logger.error("Benchmark parameter is required")
        return

    db_manager = DatabaseManager()
    db_manager.set_verbosity(s.verbosity_settings)
    bench_manager = BenchmarkManager()
    bench_manager.set_verbosity(s.verbosity_settings)

    if s.benchmark not in bench_manager.benchmarks:
        console.print(f"[red]❌ Unknown benchmark: {s.benchmark}[/red]")
        console.print(f"Available benchmarks: {', '.join(bench_manager.benchmarks.keys())}")
        if logger:
            logger.error(f"Unknown benchmark: {s.benchmark}. Available: {list(bench_manager.benchmarks.keys())}")
        return

    database_config = _data_or_load_build_db_config(s, db_manager)

    benchmark_info = bench_manager.benchmarks[s.benchmark]

    try:
        bench_manager.validate_scale_factor(s.benchmark, s.scale)
    except ValueError as e:
        console.print(f"[red]❌ {e}[/red]")
        if logger:
            logger.error(f"Scale factor validation failed: {e}")
        s.ctx.exit(1)

    _warn_unofficial_subscale(s)

    benchmark_config = _build_benchmark_config(s, benchmark_info)

    profiler = SystemProfiler()
    system_profile = profiler.get_system_profile()

    execution_context = _build_execution_context_from_plan(s)

    orchestrator = BenchmarkOrchestrator()
    orchestrator.set_verbosity(s.verbosity_settings)

    if s.output:
        normalized_output = normalize_output_root(s.output, s.benchmark, s.scale)
        assert normalized_output is not None
        orchestrator.set_custom_output_dir(normalized_output)

    if logger:
        logger.debug(
            f"Executing {'data-only' if s.execution_mode == 'data_only' else 'load-only'} mode with orchestrator"
        )

    result = _execute_orchestrated_run(
        orchestrator,
        benchmark_config,
        system_profile,
        database_config,
        list(s.resolved_run_plan.phases),
        quiet=bool(s.quiet),
        no_progress=bool(s.no_progress),
        no_monitoring=bool(s.no_monitoring),
        execution_context=execution_context,
    )

    _data_or_load_handle_result(s, result, orchestrator, benchmark_config)


def _data_or_load_operation_status(result: Any, execution_mode: str) -> tuple[str, str]:
    if execution_mode == "data_only":
        return "COMPLETED", "Data generation"

    operation_name = "Data loading"
    operation_status = "NOT_RUN"
    if hasattr(result, "execution_phases") and result.execution_phases:
        if hasattr(result.execution_phases, "setup") and result.execution_phases.setup:
            if hasattr(result.execution_phases.setup, "data_loading") and result.execution_phases.setup.data_loading:
                operation_status = result.execution_phases.setup.data_loading.status
    return operation_status, operation_name


def _data_or_load_handle_result(
    s: types.SimpleNamespace,
    result: Any,
    orchestrator: BenchmarkOrchestrator,
    benchmark_config: BenchmarkConfig,
) -> None:
    ctx = s.ctx
    if result.validation_status not in ["FAILED", "INTERRUPTED"]:
        if not s.quiet:
            console.print("\n[bold]Exporting results...[/bold]")
        platform_label = s.platform.lower() if s.platform else "unknown"
        mode_label = "data_only" if s.execution_mode == "data_only" else s.resolved_mode
        exported_files = _export_orchestrated_result(
            orchestrator=orchestrator,
            result=result,
            benchmark=s.benchmark,
            scale=s.scale,
            platform_label=platform_label,
            mode_label=mode_label,
            quiet=bool(s.quiet),
            export_formats=["json"],
            funding=getattr(s, "funding", None),
            result_source=getattr(s, "result_source", None),
        )

        operation_status, operation_name = _data_or_load_operation_status(result, s.execution_mode)

        if s.quiet:
            for _format_name, filepath in exported_files.items():
                click.echo(filepath)
        else:
            console.print(f"\n[green]✅ {operation_name} completed: {operation_status}[/green]")
            for format_name, filepath in exported_files.items():
                console.print(f"{format_name.upper()}: {filepath}")

        from benchbox.cli.preferences import save_last_run_config

        save_last_run_config(
            database=s.platform if s.platform else "none",
            benchmark=s.benchmark,
            scale=s.scale,
            tuning_mode=s.tuning,
            phases=s.phases_to_run,
            concurrency=benchmark_config.concurrency,
            compress_data=s.compress_data,
            compression_type=s.compression_type,
            compression_level=s.compression_level,
            test_execution_type=s.test_execution_type,
            queries=list(benchmark_config.queries) if getattr(benchmark_config, "queries", None) is not None else None,
            mode=getattr(s, "resolved_mode", None),
            seed=s.seed,
            iterations=s.resolved_run_plan.iterations,
            non_replayable_options=list(s.resolved_run_plan.non_replayable_options),
            output=s.output,
            additional_options={"table_mode": s.table_mode},
        )
    else:
        _emit_load_failure_marker(result)
        console.print(
            f"\n[red]❌ {'Data generation' if s.execution_mode == 'data_only' else 'Data loading'} failed: {result.validation_status}[/red]"
        )
        if result.validation_details:
            console.print(f"Error details: {result.validation_details}")
        ctx.exit(1)


def _run_interactive(s: types.SimpleNamespace) -> None:
    ctx = s.ctx
    logger = s.logger
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        console.print("[red]❌ Error: Interactive mode requires a terminal (TTY)[/red]")
        console.print(
            "[yellow]Please provide --platform and --benchmark parameters for non-interactive execution[/yellow]"
        )
        console.print("[dim]Or use --non-interactive flag with all required parameters[/dim]")
        if logger:
            logger.error("Interactive mode attempted in non-TTY environment")
        ctx.exit(2)

    if logger:
        logger.debug("Entering interactive mode")

    from benchbox.cli.onboarding import check_and_run_first_time_setup

    is_first_run = check_and_run_first_time_setup()
    if not is_first_run:
        console.print("\n[bold blue]BenchBox Interactive Setup[/bold blue]")
        console.print("We'll guide you through selecting the optimal configuration for your system.\n")

    console.print("[bold]Step 1 of 6:[/bold] [cyan]System Analysis[/cyan]")
    console.print("Analyzing your system resources to provide smart recommendations...")
    if logger:
        logger.debug("Starting system profiling for interactive mode")
    profiler = SystemProfiler()
    system_profile = profiler.get_system_profile()
    profiler.display_profile(system_profile)

    if logger:
        logger.debug(f"Interactive mode system profile: {system_profile}")

    display_system_recommendations(system_profile)

    use_last_run = _interactive_try_quick_restart(s)
    s.quick_restart_used = use_last_run
    if not use_last_run:
        _interactive_normal_flow(s, system_profile)

    _interactive_preflight_and_execute(s, system_profile)


def _explicit_run_fields(s: types.SimpleNamespace) -> frozenset[str]:
    if not hasattr(s.ctx, "get_parameter_source"):
        return frozenset({"table_mode"} if s.table_mode_cli_supplied else ())

    explicit: set[str] = set()
    for field in (
        "platform",
        "benchmark",
        "scale",
        "phases",
        "queries",
        "tuning",
        "table_mode",
        "output",
        "mode",
        "seed",
        "compression",
        "iterations",
        "concurrency",
    ):
        try:
            if s.ctx.get_parameter_source(field) == click.core.ParameterSource.COMMANDLINE:
                explicit.add(field)
        except Exception:
            continue
    return frozenset(explicit)


def _resolve_quick_restart_atomically(
    s: types.SimpleNamespace,
    last_run: dict[str, Any],
) -> ResolvedRunPlan:
    return resolve_quick_restart_atomically(
        s,
        last_run,
        current_request=getattr(s, "run_request", None) or _current_run_request(s),
        explicit_fields=_explicit_run_fields(s),
        resolve=lambda state: _resolve_run_request(
            state,
            apply_default_scale=False,
            tuning_non_interactive=True,
        ),
        data_organization_environment=DATA_ORGANIZATION_ENV,
    )


def _interactive_try_quick_restart(s: types.SimpleNamespace) -> bool:
    from benchbox.cli.preferences import format_last_run_summary, load_last_run_config

    last_run = load_last_run_config()
    if not (last_run and sys.stdin.isatty() and sys.stdout.isatty()):
        return False

    console.print("\n[bold cyan]Quick Restart[/bold cyan]")
    summary = format_last_run_summary(last_run)
    console.print(f"[dim]Last run: {summary}[/dim]")

    if not Confirm.ask("Reuse this configuration?", default=False):
        return False

    try:
        plan = _resolve_quick_restart_atomically(s, last_run)
    except ValueError as exc:
        console.print(f"[red]❌ Saved quick-restart configuration is invalid: {exc}[/red]")
        if s.logger:
            s.logger.error("Quick-restart resolution failed: %s", exc)
        s.ctx.exit(1)
        raise AssertionError("click.Context.exit() returned unexpectedly") from exc

    console.print("[green]✓ Using saved configuration[/green]")
    if not plan.request.exact_replay:
        console.print("[yellow]⚠ Configuration reuse is not an exact replay.[/yellow]")
        for note in plan.request.compatibility_notes:
            console.print(f"[dim yellow]  • {note}[/dim yellow]")
    db_manager = DatabaseManager()
    db_manager.set_verbosity(s.verbosity_settings)
    assert plan.platform_key is not None
    s.database_config = db_manager.create_config(
        plan.platform_key,
        dict(s.parsed_platform_options),
        {**s.verbosity_payload, "force_recreate": _force_recreate_requested(s), **_tuning_override_entries(s)},
        deployment_selector=getattr(s, "deployment_selector", None),
        gateway_selector=getattr(s, "gateway", None),
        explicit_option_keys=getattr(s, "explicit_platform_option_keys", ()),
    )
    promote_tuning_provenance(s.database_config, s.tuning_enabled, s.tuning_source_value, s.tuning_template_ref)

    bench_manager = BenchmarkManager()
    bench_manager.set_verbosity(s.verbosity_settings)
    benchmark_info = bench_manager.benchmarks.get(s.benchmark, {})

    try:
        bench_manager.validate_scale_factor(s.benchmark, s.scale)
    except ValueError as e:
        console.print(f"[red]❌ {e}[/red]")
        if s.logger:
            s.logger.error(f"Scale factor validation failed: {e}")
        s.ctx.exit(1)

    _warn_unofficial_subscale(s)

    s.benchmark_config = _build_benchmark_config(s, benchmark_info)
    console.print()
    return True


def _interactive_normal_flow(s: types.SimpleNamespace, system_profile: Any) -> None:
    console.print("\n[bold]Step 2 of 6:[/bold] [cyan]Execution Style[/cyan]")
    console.print("Choose how you want to run benchmarks...")
    db_manager = DatabaseManager()
    db_manager.set_verbosity(s.verbosity_settings)
    style_filter = db_manager.prompt_execution_style()

    console.print("\n[bold]Step 3 of 6:[/bold] [cyan]Database Selection[/cyan]")
    console.print("Selecting the best database for your benchmarks...")
    s.database_config = db_manager.select_database(style_filter=style_filter)

    _interactive_resolve_mode_for_selected_platform(s)
    if sys.stdin.isatty() and sys.stdout.isatty():
        _interactive_prompt_platform_options(s)
    _interactive_cloud_setup_if_needed(s)

    console.print("\n[bold]Step 4 of 6:[/bold] [cyan]Benchmark Configuration[/cyan]")
    console.print("Configuring benchmark parameters optimized for your system...")
    bench_manager = BenchmarkManager()
    bench_manager.set_verbosity(s.verbosity_settings)
    s.benchmark_config = bench_manager.select_benchmark()

    if sys.stdin.isatty() and sys.stdout.isatty():
        _interactive_collect_flags(s, db_manager, bench_manager)

    _interactive_tuning_step(s, system_profile)
    s.platform = s.database_config.type
    s.platform_key = normalize_platform_name(s.platform)
    s.benchmark = s.benchmark_config.name
    s.scale = s.benchmark_config.scale_factor
    interactive_platform_options = getattr(s, "_interactive_platform_options", None)
    if interactive_platform_options:
        s.parsed_platform_options = dict(interactive_platform_options)
    _resolve_data_organization(s, resolve_dataframe=False)
    if s.data_organization_payload is not None:
        s.benchmark_config.options["data_organization"] = s.data_organization_payload
    else:
        s.benchmark_config.options.pop("data_organization", None)
    if s.df_tuning_config:
        s.benchmark_config.options["df_tuning_config"] = s.df_tuning_config
    else:
        s.benchmark_config.options.pop("df_tuning_config", None)


def _run_stages_through_cloud_storage(s: types.SimpleNamespace) -> bool:
    mode = resolved_deployment_mode(s.database_config)
    return PlatformRegistry.requires_cloud_storage_for_deployment(s.database_config.type, mode)


def _run_requires_platform_credentials(s: types.SimpleNamespace) -> bool:
    mode = resolved_deployment_mode(s.database_config) or PlatformRegistry.get_default_deployment(
        s.database_config.type
    )
    capability = PlatformRegistry.get_deployment_capability(s.database_config.type, mode) if mode else None
    return bool(capability and capability.requires_credentials)


def _interactive_cloud_setup_if_needed(s: types.SimpleNamespace) -> None:
    ctx = s.ctx
    stages_remotely = _run_stages_through_cloud_storage(s)
    if not stages_remotely and not _run_requires_platform_credentials(s):
        return

    if s.database_config.type.casefold() in _GUIDED_CREDENTIAL_PLATFORMS:
        credentials_ok = check_and_setup_platform_credentials(
            platform=s.database_config.type,
            console_obj=console,
            interactive=not s.non_interactive,
        )

        if not credentials_ok:
            console.print(f"\n[red]❌ Cannot proceed without {s.database_config.type.upper()} credentials[/red]")
            console.print(f"\n[dim]To configure later: benchbox setup --platform {s.database_config.type}[/dim]")
            ctx.exit(1)
    elif not stages_remotely:
        return

    if not stages_remotely or s.output:
        return
    default_output = None
    from benchbox.security.credentials import CredentialManager

    cred_manager = CredentialManager()
    if cred_manager.has_credentials(s.database_config.type):
        creds = cred_manager.get_platform_credentials(s.database_config.type)
        default_output = creds.get("default_output_location") if creds else None

    cloud_output_hint = prompt_cloud_output_location(
        platform_name=s.database_config.type,
        benchmark_name="your benchmark",
        scale_factor=s.scale or 1.0,
        non_interactive=s.non_interactive,
        default_output=default_output,
    )
    if cloud_output_hint:
        s.output = cloud_output_hint


def _interactive_prompt_phases_queries(
    s: types.SimpleNamespace, bench_manager: BenchmarkManager, prompt_phases_fn: Any, prompt_query_subset_fn: Any
) -> None:
    s.phases_to_run = prompt_phases_fn(default_phases=s.phases_to_run)
    s.test_execution_type = _derive_execution_type(s.phases_to_run)
    s.execution_mode = s.test_execution_type
    s.benchmark_config.test_execution_type = s.test_execution_type

    if {"power", "standard"} & set(s.phases_to_run):
        bench_info = bench_manager.benchmarks.get(s.benchmark_config.name, {})
        num_queries = bench_info.get("num_queries", 0)
        if num_queries > 0:
            queries_selected = prompt_query_subset_fn(s.benchmark_config.name, num_queries)
            if queries_selected:
                s.benchmark_config.queries = queries_selected
                s.queries_to_run = queries_selected


def _interactive_prompt_force_and_validation(
    s: types.SimpleNamespace, prompt_force_fn: Any, prompt_validation_fn: Any
) -> None:
    if s.force is None:
        force_mode_result = prompt_force_fn()
        if force_mode_result is not None:
            force_mode: str = force_mode_result
            s.force = ForceConfig(
                datagen=force_mode in ("all", "datagen"),
                upload=force_mode in ("all", "upload"),
            )

    if s.validation is None:
        validation_selection = prompt_validation_fn()
        if validation_selection is not None:
            s.validation_mode = validation_selection
            s.benchmark_config.options["validation_mode"] = s.validation_mode


def _interactive_prompt_output_and_format(
    s: types.SimpleNamespace,
    prompt_output_fn: Any,
    prompt_table_format_fn: Any,
) -> None:
    if not s.output and not _run_stages_through_cloud_storage(s):
        custom_output = prompt_output_fn(default_output="benchmark_runs/")
        if custom_output:
            s.output = custom_output

    if s.table_format_value is None:
        selected_format, selected_compression = prompt_table_format_fn(s.database_config.type)
        if selected_format:
            s.table_format_value = selected_format
            if selected_compression:
                s.table_format_compression = selected_compression


def _interactive_collect_flags(
    s: types.SimpleNamespace, db_manager: DatabaseManager, bench_manager: BenchmarkManager
) -> None:
    from benchbox.cli.benchmarks import (
        prompt_capture_plans,
        prompt_force_regeneration,
        prompt_official_mode,
        prompt_output_location,
        prompt_phases,
        prompt_query_subset,
        prompt_seed,
        prompt_table_format,
        prompt_table_mode,
        prompt_validation_mode,
        prompt_verbose_output,
    )

    if not s.table_mode_cli_supplied:
        s.table_mode = prompt_table_mode(default_mode=s.table_mode)
        if s.table_mode == "external" and _tuning_arg_is_tuning_bearing(s.tuning):
            _reject_external_tuned(console, s.logger, s.ctx)
    else:
        console.print(f"[dim]Using table mode from CLI: {s.table_mode}[/dim]")

    _interactive_prompt_phases_queries(s, bench_manager, prompt_phases, prompt_query_subset)

    if not s.official:
        s.official, adjusted_scale = prompt_official_mode(s.benchmark_config.name, s.benchmark_config.scale_factor)
        if adjusted_scale is not None:
            s.benchmark_config.scale_factor = adjusted_scale

    _interactive_prompt_seed(s, prompt_seed)
    _interactive_prompt_force_and_validation(s, prompt_force_regeneration, prompt_validation_mode)

    if not s.capture_plans:
        s.capture_plans = prompt_capture_plans(s.database_config.type)
        if s.capture_plans:
            s.benchmark_config.capture_plans = True

    _interactive_prompt_output_and_format(s, prompt_output_location, prompt_table_format)

    if s.verbosity_settings.level == 0:
        verbose_level = prompt_verbose_output()
        if verbose_level > 0:
            s.verbosity_settings = VerbositySettings(
                level=verbose_level,
                verbose_enabled=verbose_level >= 1,
                very_verbose=verbose_level >= 2,
                quiet=False,
            )
            db_manager.set_verbosity(s.verbosity_settings)
            bench_manager.set_verbosity(s.verbosity_settings)

    _interactive_prompt_platform_options(s)


def _interactive_resolve_engine(s: types.SimpleNamespace) -> None:
    from benchbox.cli.benchmarks import prompt_execution_engine
    from benchbox.core.execution_engine import UnsupportedExecutionEngineError, resolve_requested
    from benchbox.core.platform_manifest import DEFAULT_EXECUTION_ENGINE

    requested = getattr(s, "execution_engine", None)
    if requested is None:
        requested = prompt_execution_engine(s.database_config.type)
    try:
        resolved = resolve_requested(s.database_config.type, requested)
    except UnsupportedExecutionEngineError as exc:
        console.print(f"[red]❌ {exc}[/red]")
        if s.logger:
            s.logger.error(str(exc))
        s.ctx.exit(1)
        return
    s.execution_engine = resolved
    s.resolved_execution_engine = resolved
    if resolved != DEFAULT_EXECUTION_ENGINE:
        s.database_config.execution_engine = resolved


def _interactive_prompt_platform_options(s: types.SimpleNamespace) -> None:
    _interactive_resolve_engine(s)
    if s.parsed_platform_options:
        return

    platform_opts = getattr(s, "_interactive_platform_options", None)
    if platform_opts is None:
        from benchbox.cli.benchmarks import prompt_platform_options

        platform_opts = prompt_platform_options(s.database_config.type) or {}
        s._interactive_platform_options = platform_opts
    _interactive_resolve_engine(s)

    if not platform_opts:
        return
    if s.database_config.options is None:
        s.database_config.options = {}
    s.database_config.options.update(platform_opts)
    benchmark_config = getattr(s, "benchmark_config", None)
    if benchmark_config is not None:
        benchmark_config.options["platform_options"] = dict(platform_opts)
        benchmark_config.options["platform_option_sources"] = {str(key): "runtime_override" for key in platform_opts}


def _interactive_prompt_seed(s: types.SimpleNamespace, prompt_seed_fn: Any) -> None:
    if s.seed is not None:
        return
    if s.official:
        console.print("\n[bold cyan]Seed (Required for Official Mode)[/bold cyan]")
        console.print("[dim]Official TPC runs require a seed for reproducibility.[/dim]")
        import random

        from rich.prompt import IntPrompt

        suggested_seed = random.randint(1, 999999)
        s.seed = IntPrompt.ask("Enter seed value", default=suggested_seed)
        console.print(f"[green]✓ Using seed: {s.seed}[/green]")
    else:
        s.seed = prompt_seed_fn()

    if s.seed is not None:
        s.benchmark_config.options["seed"] = s.seed


def _interactive_tuning_step(s: types.SimpleNamespace, system_profile: Any) -> None:
    console.print("\n[bold]Step 5 of 6:[/bold] [cyan]Tuning Configuration[/cyan]")
    console.print("Configure database optimizations for best performance...")

    from benchbox.cli.tuning import run_tuning_wizard

    tuning_source_value = TuningSource.BASELINE.value
    if sys.stdin.isatty() and sys.stdout.isatty():
        if Confirm.ask("\nWould you like to configure tuning options?", default=True):
            s.loaded_unified_config = run_tuning_wizard(
                benchmark=s.benchmark_config.name,
                platform=s.database_config.type,
                system_profile=system_profile,
                interactive=True,
            )
            s.tuning_enabled, s.tuning = infer_runtime_tuning_mode(s.loaded_unified_config)
            tuning_source_value = TuningSource.INTERACTIVE_WIZARD.value
            console.print("[green]✅ Tuning configuration completed[/green]")
            s.tuning_config_file = None
            s.use_auto_tuning = False
        else:
            s.loaded_unified_config = build_baseline_unified_config()
            console.print("[blue]Using baseline no-tuning configuration[/blue]")
            s.tuning_enabled = False
            s.tuning = "notuning"
            s.tuning_config_file = None
            s.use_auto_tuning = False
    else:
        s.loaded_unified_config = build_baseline_unified_config()
        console.print("[dim]Non-interactive mode: using baseline no-tuning configuration[/dim]")
        s.tuning_enabled = False
        s.tuning = "notuning"
        s.tuning_config_file = None
        s.use_auto_tuning = False

    s.tuning_resolution = (
        TuningResolution(mode=TuningMode.TUNED, source=TuningSource.INTERACTIVE_WIZARD, enabled=True)
        if s.tuning_enabled
        else TuningResolution(mode=TuningMode.NOTUNING, source=TuningSource.BASELINE, enabled=False)
    )

    if s.table_mode == "external" and _tuning_arg_is_tuning_bearing(s.tuning):
        _reject_external_tuned(console, s.logger, s.ctx)

    s.tuning_source_value, s.tuning_template_ref = tuning_source_value, None
    if getattr(s.database_config, "options", None) is None:
        s.database_config.options = {}
    s.database_config.options["tuning_enabled"] = s.tuning_enabled
    promote_tuning_provenance(s.database_config, s.tuning_enabled, s.tuning_source_value, s.tuning_template_ref)
    s.database_config.show_query_plans = s.show_query_plans

    s.benchmark_config.options["unified_tuning_configuration"] = s.loaded_unified_config
    s.benchmark_config.options["tuning_enabled"] = s.tuning_enabled
    s.benchmark_config.options["seed"] = s.seed
    s.benchmark_config.options["table_mode"] = s.table_mode
    try:
        s.df_tuning_config = resolve_dataframe_tuning_config(
            platform_name=s.database_config.type,
            mode_name=s.resolved_mode,
            tuning_enabled_flag=s.tuning_enabled,
            use_auto_tuning_flag=s.use_auto_tuning,
            tuning_config_file_path=s.tuning_config_file,
            console=console,
            logger=s.logger,
            quiet=bool(s.quiet),
        )
    except ValueError as exc:
        console.print(f"[red]❌ {exc}[/red]")
        s.ctx.exit(1)
    if s.df_tuning_config:
        s.benchmark_config.options["df_tuning_config"] = s.df_tuning_config
    else:
        s.benchmark_config.options.pop("df_tuning_config", None)

    if s.table_format_value is not None:
        s.benchmark_config.options["table_format"] = s.table_format_value
    if s.table_format_compression is not None:
        s.benchmark_config.options["table_format_compression"] = s.table_format_compression


def _finalize_normal_interactive_plan(s: types.SimpleNamespace) -> None:
    assert s.database_config is not None
    assert s.benchmark_config is not None
    s.platform = s.database_config.type
    s.platform_key = normalize_platform_name(s.platform)
    s.benchmark = s.benchmark_config.name
    s.scale = s.benchmark_config.scale_factor
    s.phases = ",".join(s.phases_to_run)
    s.test_execution_type = _derive_execution_type(s.phases_to_run)
    s.execution_mode = s.test_execution_type
    s.queries_to_run = list(s.benchmark_config.queries) if s.benchmark_config.queries else None
    s.queries = ",".join(s.queries_to_run) if s.queries_to_run else None
    if not s.compression_cli_set:
        selected_compression = CompressionConfig(
            enabled=s.benchmark_config.compress_data,
            type=s.benchmark_config.compression_type,
            level=s.benchmark_config.compression_level,
        )
        s.compression = selected_compression
        s.comp_config = selected_compression
        s.compress_data = selected_compression.enabled
        s.no_compression = not selected_compression.enabled
        s.compression_type = selected_compression.type
        s.compression_level = selected_compression.level
    s.concurrency = s.benchmark_config.concurrency
    s.run_request = _current_run_request(s)
    s.resolved_run_plan = _capture_resolved_run_plan(s)

    plan: ResolvedRunPlan = s.resolved_run_plan
    s.benchmark_config.official = bool(getattr(s, "official", False))
    s.benchmark_config.scale_factor = plan.scale
    s.benchmark_config.queries = list(plan.queries) if plan.queries is not None else None
    s.benchmark_config.compress_data = plan.compression_enabled
    s.benchmark_config.compression_type = plan.compression_type
    s.benchmark_config.compression_level = plan.compression_level
    s.benchmark_config.test_execution_type = plan.test_execution_type
    s.benchmark_config.stats_reset = getattr(s, "stats_reset", None)
    s.benchmark_config.stats_per_table_timing = bool(getattr(s, "stats_per_table_timing", False))
    s.benchmark_config.client_region = getattr(s, "client_region", None)
    s.benchmark_config.client_cloud = getattr(s, "client_cloud", None)
    s.benchmark_config.link_probe = not getattr(s, "no_link_probe", False)
    s.benchmark_config.options.update(
        {
            "table_mode": plan.table_mode,
            "tuning_enabled": plan.tuning_enabled,
            "unified_tuning_configuration": plan.loaded_unified_config,
        }
    )
    if getattr(s, "client_region", None) is not None:
        s.benchmark_config.options["client_region"] = s.client_region
    if getattr(s, "client_cloud", None) is not None:
        s.benchmark_config.options["client_cloud"] = s.client_cloud
    if getattr(s, "no_link_probe", None) is not None:
        s.benchmark_config.options["link_probe"] = not s.no_link_probe
    if plan.seed is None:
        s.benchmark_config.options.pop("seed", None)
    else:
        s.benchmark_config.options["seed"] = plan.seed
    if plan.data_organization is None:
        s.benchmark_config.options.pop("data_organization", None)
    else:
        s.benchmark_config.options["data_organization"] = dict(plan.data_organization)
    if plan.dataframe_tuning_config is None:
        s.benchmark_config.options.pop("df_tuning_config", None)
    else:
        s.benchmark_config.options["df_tuning_config"] = plan.dataframe_tuning_config
    s.database_config.execution_mode = plan.resolved_mode


def _interactive_preflight_and_execute(s: types.SimpleNamespace, system_profile: Any) -> None:
    ctx = s.ctx
    assert s.database_config is not None
    assert s.benchmark_config is not None
    if not getattr(s, "quick_restart_used", False):
        _finalize_normal_interactive_plan(s)
    else:
        s.benchmark_config.stats_reset = getattr(s, "stats_reset", None)
        s.benchmark_config.stats_per_table_timing = bool(getattr(s, "stats_per_table_timing", False))
        s.benchmark_config.client_region = getattr(s, "client_region", None)
        s.benchmark_config.client_cloud = getattr(s, "client_cloud", None)
        s.benchmark_config.link_probe = not getattr(s, "no_link_probe", False)
        if getattr(s, "concurrency", None) is not None:
            s.benchmark_config.concurrency = s.concurrency
    if _run_stages_through_cloud_storage(s) and not s.output:
        console.print()
        console.print("[red]❌ Error: Cloud platform requires --output parameter[/red]")
        console.print(
            f"[yellow]The {s.database_config.type.upper()} platform requires a cloud "
            f"storage location for data staging.[/yellow]"
        )

        examples = PlatformRegistry.get_cloud_path_examples(s.database_config.type)
        if examples:
            console.print("\n[bold]Example paths:[/bold]")
            for example in examples:
                console.print(f"  • {example}")

        console.print("\n[dim]Options:[/dim]")
        console.print(f"  1. Set default: benchbox setup --platform {s.database_config.type}")
        console.print(
            f"  2. Use --output flag: benchbox run --platform {s.database_config.type} "
            f"--benchmark {s.benchmark_config.name} --output <cloud-path>"
        )

        if s.logger:
            s.logger.error(f"Cloud platform {s.database_config.type} requires output parameter but none provided")

        ctx.exit(1)

    if sys.stdin.isatty() and sys.stdout.isatty() and not s.non_interactive:
        _interactive_show_preview(s)

        if not Confirm.ask("Proceed with execution?", default=True):
            console.print("[yellow]Benchmark execution cancelled.[/yellow]")
            ctx.exit(0)

    console.print("\n[bold]Step 6 of 6:[/bold] [cyan]Benchmark Execution[/cyan]")
    console.print("Executing benchmark with platform optimizations...")

    execution_context = _build_execution_context_from_plan(s)

    orchestrator = BenchmarkOrchestrator()
    orchestrator.set_verbosity(s.verbosity_settings)

    if s.output:
        normalized_output = normalize_output_root(s.output, s.benchmark_config.name, s.benchmark_config.scale_factor)
        assert normalized_output is not None
        orchestrator.set_custom_output_dir(normalized_output)

    result = _execute_orchestrated_run(
        orchestrator,
        s.benchmark_config,
        system_profile,
        s.database_config,
        list(s.resolved_run_plan.phases),
        quiet=bool(s.quiet),
        no_progress=bool(s.no_progress),
        no_monitoring=bool(s.no_monitoring),
        execution_context=execution_context,
    )

    _interactive_handle_result(s, result, orchestrator)


def _interactive_show_preview(s: types.SimpleNamespace) -> None:
    from benchbox.cli.dryrun import display_interactive_preview

    plan: ResolvedRunPlan = s.resolved_run_plan
    force_str = None
    if s.force:
        if s.force.datagen and s.force.upload:
            force_str = "all"
        elif s.force.datagen:
            force_str = "datagen"
        elif s.force.upload:
            force_str = "upload"

    plan_config_str = None
    if s.plan_config:
        plan_parts = []
        if s.plan_config.queries:
            plan_parts.append(f"queries:{','.join(s.plan_config.queries)}")
        if s.plan_config.strict:
            plan_parts.append("strict:true")
        if plan_parts:
            plan_config_str = ",".join(plan_parts)

    platform_options_dict = dict(s.platform_option_pairs) if s.platform_option_pairs else None

    display_interactive_preview(
        database_config=s.database_config,
        benchmark_config=s.benchmark_config,
        phases=list(plan.phases),
        output=s.output,
        table_mode=plan.table_mode,
        tuning=plan.request.tuning if plan.request.tuning != "notuning" else None,
        seed=plan.seed,
        force=force_str,
        official=s.official,
        capture_plans=s.capture_plans,
        analyze_plans=getattr(s, "analyze_plans", None),
        validation=s.validation_mode if s.validation_mode and s.validation_mode != "exact" else None,
        verbose=s.verbosity_settings.level,
        console_obj=console,
        platform_options=platform_options_dict,
        plan_config=plan_config_str,
        presort=s.presort,
        sorted_ingestion_mode=s.sorted_ingestion_mode,
        sorted_ingestion_method=s.sorted_ingestion_method,
        global_cache=s.global_cache,
        strict_translation=s.strict_translation,
        stats_reset=getattr(s, "stats_reset", None),
        stats_per_table_timing=bool(getattr(s, "stats_per_table_timing", False)),
        benchmark_options=dict(s.benchmark_option_pairs) if s.benchmark_option_pairs else None,
    )


def _interactive_handle_result(s: types.SimpleNamespace, result: Any, orchestrator: BenchmarkOrchestrator) -> None:
    ctx = s.ctx
    if result.validation_status not in ["FAILED", "INTERRUPTED"]:
        console.print("\n[bold]Step 5:[/bold] Export Results")
        export_formats = s.config.get("output.formats", ["json"])
        exported_files = _export_orchestrated_result(
            orchestrator=orchestrator,
            result=result,
            benchmark=s.benchmark_config.name,
            scale=s.benchmark_config.scale_factor,
            platform_label=s.database_config.type.lower(),
            mode_label=s.resolved_mode,
            quiet=bool(s.quiet),
            export_formats=export_formats,
            funding=getattr(s, "funding", None),
            result_source=getattr(s, "result_source", None),
        )

        if s.quiet:
            for _format_name, filepath in exported_files.items():
                click.echo(filepath)
        else:
            console.print(f"\n[green]✅ Benchmark completed with status: {result.validation_status}[/green]")
            console.print(f"Result ID: [cyan]{result.execution_id}[/cyan]")
            for format_name, filepath in exported_files.items():
                console.print(f"{format_name.upper()}: [dim]{filepath}[/dim]")

        _render_post_run_charts(result, console, s.quiet)

        if s.publish:
            json_bundle = exported_files.get("json")
            if json_bundle:
                from benchbox.cli.commands.publish import publish_bundle

                if not s.quiet:
                    console.print("\n[bold]Publishing result bundle...[/bold]")
                publish_bundle(
                    source_bundle=json_bundle,
                    target=s.publish_target,
                    label=s.publish_label,
                    quiet=bool(s.quiet),
                )

        from benchbox.cli.preferences import save_last_run_config

        save_last_run_config(
            database=s.database_config.type,
            benchmark=s.benchmark_config.name,
            scale=s.benchmark_config.scale_factor,
            tuning_mode=s.tuning,
            phases=s.phases_to_run,
            concurrency=s.benchmark_config.concurrency,
            compress_data=s.benchmark_config.compress_data,
            compression_type=s.benchmark_config.compression_type,
            compression_level=s.benchmark_config.compression_level,
            test_execution_type=s.benchmark_config.test_execution_type,
            queries=(
                list(s.benchmark_config.queries) if getattr(s.benchmark_config, "queries", None) is not None else None
            ),
            mode=getattr(s, "resolved_mode", None),
            seed=s.seed,
            iterations=s.resolved_run_plan.iterations,
            non_replayable_options=list(s.resolved_run_plan.non_replayable_options),
            output=s.output,
            additional_options={"table_mode": s.table_mode},
        )
    else:
        _emit_load_failure_marker(result)
        console.print(f"\n[red]❌ Benchmark failed with status: {result.validation_status}[/red]")
        ctx.exit(1)


@click.command(
    "run",
    cls=BenchBoxCommand,
    help=(
        "Run benchmarks.\n\n\b\nExamples:\n"
        "  benchbox run --platform duckdb --benchmark tpch\n"
        "  benchbox run --platform duckdb --benchmark tpch --queries Q1,Q6,Q17\n"
        "  benchbox run --dry-run ./preview --platform snowflake --benchmark tpch\n"
        "  benchbox run --official --platform snowflake --benchmark tpch --scale 100 --seed 42\n"
        "\n\b\nDeployment Targets:\n"
        "  benchbox run --platform clickhouse:local --benchmark tpch    # ClickHouse local mode via chDB\n"
        "  benchbox run --platform clickhouse:server --benchmark tpch   # ClickHouse server\n"
        "  benchbox run --platform clickhouse-cloud --benchmark tpch    # ClickHouse Cloud\n"
        "  benchbox run --platform firebolt:core --benchmark tpch       # Firebolt Core (Docker)\n"
        "  benchbox run --platform firebolt:cloud --benchmark tpch      # Firebolt Cloud\n"
        "\nPlatforms that declare no deployment modes (snowflake, databricks, polars,\n"
        "sqlite, ...) accept only the ':local' suffix, which is equivalent to the\n"
        "bare name (snowflake:local == snowflake) and does not mean local\n"
        "execution. Any other suffix is rejected.\n"
        "\nUse --help-topic examples for more, --help-topic all for advanced options."
    ),
)
@click.option(
    "--platform",
    type=str,
    help="Platform with optional deployment mode (platform:mode). Platforms that declare no "
    "deployment modes (e.g. snowflake, databricks, polars, sqlite) accept only the ':local' "
    "suffix, which is equivalent to the bare name; any other suffix is rejected.",
)
@click.option(
    "--benchmark", type=str, help="Benchmark (tpch, tpcds, ssb, joinorder, clickbench); joinorder is IMDb SF=1."
)
@click.option("--scale", type=float, default=0.01, help="Scale factor", show_default=True)
@click.option(
    "--output",
    type=str,
    help="Output directory (required for cloud platforms). Supports: s3://, gs://, abfss://, dbfs:/Volumes/",
)
@click.option(
    "--phases",
    type=str,
    default="power",
    help=f"Phases: {','.join(VALID_PHASES)}",
)
@click.option(
    "--queries",
    type=str,
    help="Query subset (e.g., 'Q1,Q6,Q17'). Only for power/standard phases.",
)
@click.option(
    "--tuning",
    type=str,
    default="notuning",
    help="Tuning: tuned, notuning, auto, or YAML path",
)
@click.option(
    "--table-mode",
    type=click.Choice(["native", "external"], case_sensitive=False),
    default="native",
    help="Table mode: native materialized tables or external table/view references",
)
@advanced_option(
    "--sorted-ingestion-mode",
    type=click.Choice(["off", "auto", "force"], case_sensitive=False),
    default=None,
    help="Cloud sorted-ingestion strategy mode: off, auto, force",
)
@advanced_option(
    "--sorted-ingestion-method",
    type=click.Choice(["auto", "ctas", "z_order", "hilbert", "liquid_clustering", "vacuum_sort"], case_sensitive=False),
    default=None,
    help="Cloud sorted-ingestion method override",
)
@click.option(
    "--dry-run",
    type=str,
    metavar="OUTPUT_DIR",
    callback=ValidationRules.validate_dry_run_output_dir,
    help="Preview configuration without execution",
)
@click.option(
    "--force",
    type=FORCE,
    default=None,
    is_flag=False,
    flag_value="all",
    help="Force regeneration: all, datagen, upload, or datagen,upload",
)
@click.option("-v", "--verbose", count=True, help="Verbose output (-vv for debug)")
@click.option("-q", "--quiet", is_flag=True, help="Suppress output")
@click.option("--non-interactive", is_flag=True, help="Non-interactive mode (CI/CD)")
@click.option(
    "--official",
    is_flag=True,
    help="TPC-compliant mode: validates scale factors, requires seed for reproducibility",
)
@advanced_option(
    "--capture-plans",
    is_flag=True,
    help=(
        "Capture query execution plans into the result bundle, in an isolated "
        "post-measurement phase (never inside the timed loop). Plans are captured "
        "with EXPLAIN ANALYZE by default (actual per-operator timing/cardinality, "
        "~1x extra query cost outside the measured window); pass --no-analyze-plans "
        "for estimated plans only (a static EXPLAIN, no re-execution)."
    ),
)
@advanced_option(
    "--analyze-plans/--no-analyze-plans",
    "analyze_plans",
    default=None,
    help=(
        "With --capture-plans, control capture detail: --analyze-plans (default) "
        "runs EXPLAIN ANALYZE post-measurement for actual timing/cardinality; "
        "--no-analyze-plans captures the static (estimated) plan with no re-execution. "
        "The single capture-detail knob. (Write statements are never re-executed "
        "regardless: DML stays on a non-ANALYZE EXPLAIN.)"
    ),
)
@advanced_option(
    "--show-plans",
    is_flag=True,
    help=(
        "Display query plans in the console after each query. "
        "Use for interactive inspection without --capture-plans. "
        "Suppressed when --capture-plans is active (capture already runs EXPLAIN; "
        "use benchbox show-plan to inspect captured plans afterwards)."
    ),
)
@advanced_option(
    "--plan-config",
    type=PLAN_CONFIG,
    default=None,
    help="Plan capture config: queries:1,6,17,strict:true (capture is once-per-query; use --analyze-plans for detail)",
)
@advanced_option(
    "--normalize-plan-literals",
    "normalize_plan_literals",
    is_flag=True,
    help=(
        "Also record a literal-normalized fingerprint (plan_fingerprint_normalized) so queries "
        "differing only in literal constants collapse to the same value. Requires --capture-plans."
    ),
)
@advanced_option(
    "--stats-reset/--no-stats-reset",
    "stats_reset",
    default=None,
    help=(
        "With --phases ...,statistics: control cold-stats vs warm-stats. "
        "--stats-reset drops/invalidates statistics before the rebuild for a "
        "cold-stats measurement (adapters with no drop-stats primitive fall "
        "back to a safe no-op and record that in the result); --no-stats-reset "
        "explicitly records a warm-stats/persist run. Omit entirely for the "
        "default PR #980 behavior (bundle stays byte-identical when unset)."
    ),
)
@advanced_option(
    "--stats-per-table-timing",
    "stats_per_table_timing",
    is_flag=True,
    help=(
        "With --phases ...,statistics: record a per-table wall-clock breakdown "
        "in the result payload (only available when the platform's statistics "
        "build falls back to a per-table ANALYZE loop; omitted otherwise)."
    ),
)
@advanced_option(
    "--compression",
    type=COMPRESSION,
    default=None,
    help="Compression: zstd, zstd:9, gzip:6, none",
)
@advanced_option(
    "--table-format",
    type=TABLE_FORMAT,
    default=None,
    help="Table format: parquet, vortex, delta:snappy, iceberg:zstd,partition:year,month",
)
@advanced_option(
    "--presort",
    type=click.Choice(["parquet-sorted", "delta-sorted", "iceberg-sorted"], case_sensitive=False),
    default=None,
    help="Pre-sort data into open table formats (parquet-sorted, delta-sorted, iceberg-sorted).",
)
@advanced_option(
    "--validation",
    type=VALIDATION,
    default=None,
    help="Validation: exact, loose, range, disabled, full",
)
@advanced_option(
    "--strict-translation",
    is_flag=True,
    help="Fail when SQL dialect translation falls back instead of returning source SQL.",
)
@click.option(
    "--platform-option",
    "platform_option_pairs",
    type=PlatformOptionParamType(),
    multiple=True,
    hidden=True,
    help=(
        "Platform option in KEY=VALUE form (repeatable). "
        "Well-known keys available on all platforms: "
        "driver_version (pin Python driver package version, e.g. '1.2.0'), "
        "driver_auto_install (auto-install driver via uv if missing, true/false). "
        "Athena Spark only: runtime_version (Spark engine version the workgroup runs, "
        "e.g. 'PySpark engine version 3'; recorded as requested, never sent). "
        "Platform-specific keys are also accepted (e.g. compute_resource=MY_WH for Snowflake). "
        "Run --help-topic examples for the full driver versioning workflow."
    ),
)
@click.option(
    "--benchmark-option",
    "benchmark_option_pairs",
    type=BenchmarkOptionParamType(),
    multiple=True,
    hidden=True,
    help=(
        "Benchmark option in KEY=VALUE form (repeatable). "
        "Benchmark-specific parameters (e.g. taxi_types=yellow,green for nyctaxi, "
        "skew_preset=heavy for tpch_skew). "
        "Use --help-topic benchmarks to see available options per benchmark."
    ),
)
@advanced_option(
    "--mode",
    type=click.Choice(list(RUN_MODES), case_sensitive=False),
    default=None,
    help="Execution mode: sql or dataframe",
)
@advanced_option(
    "--execution-engine",
    type=str,
    default=None,
    help="Execution engine: which execution machinery runs the query. "
    "Allowed values depend on the platform (see the platform manifest); "
    "'default' lets the platform or version decide.",
)
@advanced_option(
    "--polars-streaming",
    is_flag=True,
    default=False,
    hidden=True,
    help="Deprecated alias for --execution-engine streaming on polars-df.",
)
@advanced_option(
    "--gateway",
    default=None,
    help="Gateway for query routing. Only 'native' (no gateway) runs today; declared gateways fail closed.",
)
@advanced_option("--seed", type=int, help="RNG seed for query parameter generation")
@advanced_option(
    "--streams",
    "--concurrency",
    "concurrency",
    type=click.IntRange(min=2),
    default=None,
    hidden=True,
    help="Concurrent streams for throughput (canonical; --concurrency accepted as alias for run-official forwarding)",
)
@advanced_option(
    "--iterations",
    type=click.IntRange(min=1),
    default=None,
    help="Number of power test measurement iterations (default: 3). Power phase only.",
)
@advanced_option("--no-monitoring", is_flag=True, help="Disable metrics collection")
@advanced_option("--no-progress", is_flag=True, help="Disable progress bars")
@advanced_option("--ignore-memory-warnings", is_flag=True, help="Proceed despite insufficient memory warnings")
@advanced_option(
    "--global-cache",
    is_flag=True,
    help="Use ~/.benchbox/datagen/ as DataFrame cache (shared across projects). Default: project-local benchmark_runs/.",
)
@click.option(
    "--publish",
    is_flag=True,
    help="Publish the exported result bundle after a successful run (uses benchbox publish defaults).",
)
@click.option(
    "--publish-target",
    default="benchmark_runs/published",
    show_default=True,
    help="Destination for --publish: local directory or cloud URI (s3://, gs://, abfss://).",
)
@click.option(
    "--publish-label",
    default="maintainer-run",
    show_default=True,
    help=(
        "Trust label for --publish (maintainer-run, community-submission, "
        "vendor-supplied, ci, local, unofficial-research)."
    ),
)
@click.option(
    "--funding",
    type=click.Choice(FUNDING_SOURCES, case_sensitive=False),
    default=None,
    help=(
        "Disclose how this run was funded (employer, personal, free-trial, "
        "vendor-sponsored, grant, unspecified). Recorded in the bundle's "
        "provenance block; omitted when not set."
    ),
)
@click.option(
    "--result-source",
    type=click.Choice(RESULT_SOURCES, case_sensitive=False),
    default=None,
    help=(
        "Advisory producer hint (internal, community, vendor) recorded in the "
        "bundle's provenance block. NOT the authoritative trust label — the "
        "vendor label is assigned downstream under maintainer control."
    ),
)
@click.option(
    "--client-region",
    type=str,
    default=None,
    help="Attested client cloud region (e.g. us-east-1).",
)
@click.option(
    "--client-cloud",
    type=str,
    default=None,
    help="Attested client cloud provider (e.g. aws, gcp, azure).",
)
@click.option(
    "--no-link-probe",
    is_flag=True,
    default=False,
    help="Disable post-benchmark statement overhead probe.",
)
@click.pass_context
def run(
    ctx: click.Context,
    platform: str | None,
    benchmark: str | None,
    scale: float,
    output: str | None,
    phases: str,
    queries: str | None,
    tuning: str,
    table_mode: str,
    sorted_ingestion_mode: str | None,
    sorted_ingestion_method: str | None,
    dry_run: str | None,
    force: ForceConfig | None,
    verbose: int,
    quiet: bool,
    non_interactive: bool,
    official: bool,
    capture_plans: bool,
    analyze_plans: bool | None,
    show_plans: bool,
    strict_translation: bool,
    plan_config: PlanCaptureConfig | None,
    normalize_plan_literals: bool,
    stats_reset: bool | None,
    stats_per_table_timing: bool,
    compression: CompressionConfig | None,
    table_format: TableFormatConfig | None,
    presort: str | None,
    validation: ValidationConfig | None,
    platform_option_pairs: tuple[tuple[str, str], ...],
    benchmark_option_pairs: tuple[tuple[str, str], ...],
    mode: str | None,
    execution_engine: str | None,
    gateway: str | None,
    seed: int | None,
    concurrency: int | None,
    iterations: int | None,
    no_monitoring: bool,
    no_progress: bool,
    ignore_memory_warnings: bool,
    global_cache: bool,
    publish: bool,
    publish_target: str,
    publish_label: str,
    funding: str | None,
    result_source: str | None,
    client_region: str | None = None,
    client_cloud: str | None = None,
    no_link_probe: bool = False,
    polars_streaming: bool = False,
) -> None:
    from benchbox.core.run_service import apply_polars_streaming_flag, begin_run

    begin_run()
    if polars_streaming:
        try:
            platform_option_pairs = apply_polars_streaming_flag(platform_option_pairs, execution_engine)
        except ValueError as exc:
            raise click.UsageError(str(exc)) from exc
    s = types.SimpleNamespace(
        ctx=ctx,
        platform=platform,
        benchmark=benchmark,
        scale=scale,
        output=output,
        phases=phases,
        queries=queries,
        tuning=tuning,
        table_mode=table_mode,
        sorted_ingestion_mode=sorted_ingestion_mode,
        sorted_ingestion_method=sorted_ingestion_method,
        dry_run=dry_run,
        force=force,
        verbose=verbose,
        quiet=quiet,
        non_interactive=non_interactive,
        official=official,
        capture_plans=capture_plans,
        analyze_plans=analyze_plans,
        show_plans=show_plans,
        strict_translation=strict_translation,
        plan_config=plan_config,
        normalize_plan_literals=normalize_plan_literals,
        stats_reset=stats_reset,
        stats_per_table_timing=stats_per_table_timing,
        compression=compression,
        table_format=table_format,
        presort=presort,
        validation=validation,
        platform_option_pairs=platform_option_pairs,
        benchmark_option_pairs=benchmark_option_pairs,
        mode=mode,
        execution_engine=execution_engine,
        gateway=gateway,
        seed=seed,
        concurrency=concurrency,
        iterations=iterations,
        no_monitoring=no_monitoring,
        no_progress=no_progress,
        ignore_memory_warnings=ignore_memory_warnings,
        global_cache=global_cache,
        publish=publish,
        publish_target=publish_target,
        publish_label=publish_label,
        funding=funding,
        result_source=result_source,
        client_region=client_region,
        client_cloud=client_cloud,
        no_link_probe=no_link_probe,
        polars_streaming=polars_streaming,
    )
    _prepare_run_state(s)

    if s.dry_run:
        _run_dry_run(s)
        return

    if s.test_execution_type not in {"data_only", "load_only"} and s.platform and s.benchmark:
        _run_direct(s)
        return

    if s.test_execution_type in {"data_only", "load_only"}:
        _run_data_or_load_only(s)
        return

    _run_interactive(s)


__all__ = ["run", "BenchmarkOptionParamType", "PlatformOptionParamType", "setup_verbose_logging"]
