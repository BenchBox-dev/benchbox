from __future__ import annotations

import json
import logging
import os
import sys
import types
from pathlib import Path
from typing import Any

import click
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm
from rich.text import Text

from benchbox.cli import run_time_limit, vendor_provenance_options as vendor_options
from benchbox.cli.benchmarks import BenchmarkConfig, BenchmarkManager
from benchbox.cli.billable_guard import ACKNOWLEDGEMENT_FLAG_HELP, enforce_billable_acknowledgement
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
from benchbox.cli.exit_codes import ExitCode
from benchbox.cli.help import BenchBoxCommand, advanced_option
from benchbox.cli.host_lock_optin import host_guarded
from benchbox.cli.logo import rich_logo
from benchbox.cli.orchestrator import BenchmarkOrchestrator
from benchbox.cli.output import ResultExporter
from benchbox.cli.platform import (
    get_platform_manager,
)
from benchbox.cli.platform_hooks import PlatformOptionError
from benchbox.cli.progress import BenchmarkProgress, should_show_progress
from benchbox.cli.publication_guard import PUBLISH_LABEL_HELP, enforce_maintainer_label
from benchbox.cli.run_platform_resolution import (
    BENCHMARK_ALIASES as BENCHMARK_ALIASES,
    _apply_benchmark_default_scale,
    _apply_dataframe_suffix_mode as _apply_dataframe_suffix_mode,
    _check_benchmark_platform_compatibility,
    _check_platforms_status,
    _parse_plat_bench_options,
    _platform_option_config_entries,
    _resolve_platform_mode,
    normalize_benchmark_name,
)
from benchbox.cli.run_resolution import (
    ResolvedRunPlan,
    capture_resolved_run_plan,
    current_run_request as _current_run_request,
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
from benchbox.core.constants import (
    DEFAULT_OUTPUT_COMPRESSION_TYPE,
    NO_COMPRESSION_ENV_VAR,
    RUN_MODES,
    VALID_PHASES,
)
from benchbox.core.results.provenance import FUNDING_SOURCES, RESULT_SOURCES
from benchbox.core.results.status import result_cli_failure_reason, result_non_clean_reason
from benchbox.core.schemas import ExecutionContext
from benchbox.core.tpc_disclaimer import official_mode_disclaimer
from benchbox.core.tpc_spec import refresh_functions_enabled, require_full_spec
from benchbox.core.tuning import modes as tuning_modes
from benchbox.utils.cloud_storage import is_cloud_path
from benchbox.utils.compression import CompressionManager
from benchbox.utils.input_validation import MAX_QUERY_ID_LENGTH
from benchbox.utils.output_path import normalize_output_root
from benchbox.utils.printing import fatal, replay_quiet_output_on_failure
from benchbox.utils.runtime_env import DriverAutoInstallRefused, InvalidDriverVersion

logger = logging.getLogger(__name__)
DATA_ORGANIZATION_ENV = "BENCHBOX_DATA_ORGANIZATION_CONFIG_JSON"


def _reject_official_fallback(s: types.SimpleNamespace, resolution: TuningResolution) -> None:
    if not s.official or resolution.canonical_mode != tuning_modes.TUNED_FALLBACK:
        return
    fatal("❌ Error: --official runs cannot use a 'tuned-fallback' configuration")
    fatal(
        "No tuning template was found for this platform/benchmark. "
        "Add one under examples/tunings/, or rerun with --tuning notuning "
        "or an explicit --tuning <file>."
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
    billable_acknowledgement: dict[str, object] | None = None,
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
        billable_platform_acknowledgement=billable_acknowledgement,
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


@run_time_limit.bounded_orchestrated_run
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
        platform_compute=getattr(result, "platform_compute", None),
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
    plan_tier: str | None = None,
    preview_features: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    from datetime import datetime

    if funding:
        result.funding = funding
    vendor_options.record_vendor_provenance(result, plan_tier, preview_features)
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


def _print_results_location(exported_files: dict[str, Any]) -> None:
    for filepath in exported_files.values():
        console.print(f"Results directory: [dim]{Path(str(filepath)).expanduser().resolve().parent}[/dim]")
        return


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


def dry_run_filename_prefix(benchmark: str, platform: str) -> str:
    return f"{benchmark}_{platform.replace(':', '~')}"


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
        from benchbox.core.run_service import official_scale_points

        tpc_allowed = official_scale_points(benchmark)
        if s.scale not in tpc_allowed:
            fatal(f"❌ Scale factor {s.scale} is not a TPC-specified scale factor")
            fatal(f"Allowed scale factors: {sorted(tpc_allowed)}")
            s.ctx.exit(1)

        console.print("[bold blue]TPC Official Mode Benchmark Run[/bold blue]")
        console.print(f"[dim]{official_mode_disclaimer(benchmark)}[/dim]")
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
        env_no_compression = os.getenv(NO_COMPRESSION_ENV_VAR, "").lower() in ["true", "1", "yes", "on"]
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
            s.compression_type = s.config.get("output.compression.type", DEFAULT_OUTPUT_COMPRESSION_TYPE)
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


def _enforce_full_spec(s: types.SimpleNamespace) -> None:
    if not s.official:
        return
    plan: ResolvedRunPlan = s.resolved_run_plan
    benchmark = normalize_benchmark_name(plan.benchmark) if plan.benchmark else None
    if not (plan.platform_key and plan.benchmark):
        return
    if not {"warmup", "power", "standard", "throughput", "maintenance"} & set(plan.phases):
        return
    try:
        require_full_spec(
            benchmark,
            plan.scale,
            seed=plan.seed,
            phases=plan.phases,
            streams=plan.concurrency,
            query_subset=plan.queries,
            refresh_functions=refresh_functions_enabled({"benchmark_options": s.parsed_benchmark_options or {}}),
        )
    except ValueError as exc:
        fatal(f"❌ {exc}")
        s.ctx.exit(1)


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
    if not s.dry_run:
        s.billable_acknowledgement = _enforce_billable_acknowledgement(s)

    s.platform_manager = get_platform_manager()
    _check_platforms_status(s)
    _resolve_platform_mode(s)
    _check_benchmark_platform_compatibility(s)
    _resolve_tuning(s, non_interactive_override=tuning_non_interactive)
    _resolve_data_organization(s)
    _resolve_compression_settings(s)
    _validate_output_dir(s)
    s.resolved_run_plan = _capture_resolved_run_plan(s)
    _enforce_full_spec(s)


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
        **({"allow_greybeam_inline_load": True} if getattr(s, "allow_greybeam_inline_load", False) else {}),
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


def _enforce_billable_acknowledgement(s: types.SimpleNamespace) -> dict[str, object] | None:
    if s.test_execution_type == "data_only" or not s.platform_key:
        return None
    return enforce_billable_acknowledgement(
        s.platform_key,
        deployment=getattr(s, "deployment_selector", None),
        options=s.parsed_platform_options,
        flag=bool(getattr(s, "acknowledge_billable_platform", False)),
        non_interactive=bool(s.non_interactive),
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
        billable_acknowledgement=getattr(s, "billable_acknowledgement", None),
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
        console.print("[yellow]Use values like 0.1, 0.01, etc. for small scale factors[/yellow]")
        if logger:
            logger.error(f"Invalid scale factor for dry run: {s.scale}")
        ctx.exit(1)


_SUBSCALE_WARNING_TAILS = {
    "tpch": ("TPC-H", "   QphH@Size is not computed."),
    "tpcds": ("TPC-DS", "   See _sources/tpcds-subscale-contract.md for the data contract."),
}


def _warn_unofficial_subscale(s: types.SimpleNamespace) -> None:
    warning = _SUBSCALE_WARNING_TAILS.get(s.benchmark)
    if warning is None or s.scale >= 1.0 or s.quiet:
        return
    name, tail = warning
    console.print(
        f"[yellow]⚠  {name} UNOFFICIAL SUBSCALE RUN[/yellow]\n"
        f"   Scale factor: [bold]{s.scale}[/bold] (< 1.0 - not {name} compliant)\n"
        "   Results are for development use only and must not be published or\n"
        f"   submitted as official {name} results. Power@Size is recorded for\n"
        "   comparison with other subscale runs only and is marked unofficial.\n"
        f"{tail}"
    )


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

    from benchbox.core.run_service import stamp_requested_phases

    stamp_requested_phases(benchmark_config, s.phases_to_run)

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

    filename_prefix = dry_run_filename_prefix(
        s.benchmark, "data-only" if s.execution_mode == "data_only" else s.platform
    )
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
            allow_driver_install=False,
        )
    except PlatformOptionError as exc:
        console.print(f"[red]❌ {exc}[/red]")
        if logger:
            logger.error(f"Database configuration failed: {exc}")
        ctx.exit(1)
    except InvalidDriverVersion:
        raise
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
        console.print("[yellow]Use values like 0.1, 0.01, etc. for small scale factors[/yellow]")
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

    orchestrator = BenchmarkOrchestrator(non_interactive=s.non_interactive)
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
            **vendor_options.vendor_provenance_kwargs(s),
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
            _print_results_location(exported_files)

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
        if s.quiet:
            fatal(_failure_summary(result))
        s.ctx.exit(1)


def _failure_summary(result: Any) -> str:
    details = getattr(result, "validation_details", None)
    cause = details.get("error") if isinstance(details, dict) else None
    summary = f"Benchmark failed: {result.validation_status}"
    return f"{summary}: {cause}" if cause else summary


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

    orchestrator = BenchmarkOrchestrator(non_interactive=s.non_interactive)
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
            **vendor_options.vendor_provenance_kwargs(s),
        )

        operation_status, operation_name = _data_or_load_operation_status(result, s.execution_mode)

        if s.quiet:
            for _format_name, filepath in exported_files.items():
                click.echo(filepath)
        else:
            console.print(f"\n[green]✅ {operation_name} completed: {operation_status}[/green]")
            for format_name, filepath in exported_files.items():
                console.print(f"{format_name.upper()}: {filepath}")
            _print_results_location(exported_files)

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
    help=(
        "Directory for generated data and databases; works for local paths and is how a run with its own "
        "BENCHBOX_OUTPUT_DIR reuses shared generated data. Required for cloud platforms (s3://, gs://, abfss://, "
        "dbfs:/Volumes/). Result JSON is not written here: it goes to <benchmark_runs>/results ($BENCHBOX_OUTPUT_DIR "
        "or the default). The absolute result path is printed when the run finishes."
    ),
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
    help=(
        "Force a rerun: all, datagen, upload, or datagen,upload (bare --force means all). "
        "datagen: regenerates the local data files and deletes the existing target database before the run, "
        "the same as --platform-option force_recreate=true: a local database file or directory is deleted; on a "
        "remote platform the database is dropped (Snowflake drops only this benchmark's tables and its tuning "
        "metadata; Databricks truncates every table in the schema); platforms that cannot drop a database refuse. "
        "upload: re-uploads data to remote storage even when a valid copy exists; deletes no database and no local "
        "data. all: both."
    ),
)
@click.option("-v", "--verbose", count=True, help="Verbose output (-vv for debug)")
@click.option("-q", "--quiet", is_flag=True, help="Suppress output")
@click.option("--non-interactive", is_flag=True, help="Non-interactive mode (CI/CD)")
@click.option(
    "--acknowledge-billable-platform",
    "acknowledge_billable_platform",
    is_flag=True,
    help=ACKNOWLEDGEMENT_FLAG_HELP,
)
@click.option(
    "--official",
    is_flag=True,
    help="TPC official mode: validates scale factors, requires seed for reproducibility; results are derived and do not comply with the TPC Specification",
)
@advanced_option(
    "--capture-plans",
    is_flag=True,
    help=(
        "Capture query execution plans into the result bundle, in an isolated "
        "post-measurement phase (never inside the timed loop). By default plans are "
        "estimated: a static EXPLAIN with no re-execution. Pass --analyze-plans for "
        "EXPLAIN ANALYZE actual timing/cardinality where the platform supports it."
    ),
)
@advanced_option(
    "--analyze-plans/--no-analyze-plans",
    "analyze_plans",
    default=None,
    help=(
        "With --capture-plans, control capture detail: --no-analyze-plans (the default "
        "when omitted) captures the static (estimated) plan with no re-execution; "
        "--analyze-plans opts into post-measurement EXPLAIN ANALYZE for actual "
        "timing/cardinality on platforms that support it (DuckDB, MotherDuck, PostgreSQL, "
        "GizmoSQL). Write statements are never re-executed: DML stays on a non-ANALYZE EXPLAIN."
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
        "default behavior (the result bundle is unchanged when unset)."
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
    help=(
        "Gateway for query routing. 'native' (no gateway) is the default; other gateways route queries on "
        "platforms that support them (Snowflake) and fail before work starts elsewhere."
    ),
)
@advanced_option(
    "--allow-greybeam-inline-load",
    "allow_greybeam_inline_load",
    is_flag=True,
    default=False,
    help=(
        "Allow generate/load phases through the greybeam gateway. Also requires BENCHBOX_I_UNDERSTAND_GREYBEAM_LOAD=1; "
        "results from such a run are invalid data."
    ),
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
@run_time_limit.timeout_option(run_time_limit.RUN_TIMEOUT_HELP)
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
    help=PUBLISH_LABEL_HELP,
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
@vendor_options.vendor_provenance_options
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
@host_guarded("run", skip=lambda kwargs: bool(kwargs.get("dry_run")))
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
    timeout: int | None,
    no_monitoring: bool,
    no_progress: bool,
    ignore_memory_warnings: bool,
    global_cache: bool,
    publish: bool,
    publish_target: str,
    publish_label: str,
    funding: str | None,
    plan_tier: str | None,
    preview_features: tuple[str, ...],
    result_source: str | None,
    client_region: str | None = None,
    client_cloud: str | None = None,
    no_link_probe: bool = False,
    polars_streaming: bool = False,
    allow_greybeam_inline_load: bool = False,
    acknowledge_billable_platform: bool = False,
) -> None:
    from benchbox.cli.unavailable_benchmark import exit_if_benchmark_unavailable, exit_unavailable_benchmark
    from benchbox.core.run_service import BenchmarkUnavailableError, apply_polars_streaming_flag, begin_run

    exit_if_benchmark_unavailable(benchmark)

    if publish:
        enforce_maintainer_label(publish_label)

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
        timeout=timeout,
        no_monitoring=no_monitoring,
        no_progress=no_progress,
        ignore_memory_warnings=ignore_memory_warnings,
        global_cache=global_cache,
        publish=publish,
        publish_target=publish_target,
        publish_label=publish_label,
        funding=funding,
        plan_tier=plan_tier,
        preview_features=preview_features,
        result_source=result_source,
        client_region=client_region,
        client_cloud=client_cloud,
        no_link_probe=no_link_probe,
        polars_streaming=polars_streaming,
        allow_greybeam_inline_load=allow_greybeam_inline_load,
        acknowledge_billable_platform=acknowledge_billable_platform,
    )
    with replay_quiet_output_on_failure(bool(s.quiet)):
        try:
            _prepare_run_state(s)

            if s.dry_run:
                _run_dry_run(s)
                return

            run_time_limit.run_within_time_limit(s)
        except DriverAutoInstallRefused as exc:
            console.print(f"[red]❌ {escape(str(exc))}[/red]")
            sys.exit(ExitCode.ACKNOWLEDGEMENT_REQUIRED)
        except InvalidDriverVersion as exc:
            console.print(f"[red]❌ {escape(str(exc))}[/red]")
            sys.exit(ExitCode.USAGE)
        except BenchmarkUnavailableError as exc:
            exit_unavailable_benchmark(exc)


__all__ = ["run", "BenchmarkOptionParamType", "PlatformOptionParamType", "setup_verbose_logging"]
