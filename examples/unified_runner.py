#!/usr/bin/env python3
# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import benchbox
from benchbox.cli.exceptions import CloudStorageError, ValidationError, ValidationRules
from benchbox.cli.main import setup_verbose_logging
from benchbox.core.config_utils import (
    build_benchmark_config,
    build_platform_adapter_config,
    merge_all_configs,
)
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.core.results.display import (
    display_benchmark_list,
    display_configuration_summary,
    display_platform_list,
    display_results,
    display_verbose_config_feedback,
)
from benchbox.core.results.exporter import ResultExporter
from benchbox.core.runner.runner import LifecyclePhases, ValidationOptions, run_benchmark_lifecycle
from benchbox.core.schemas import BenchmarkConfig, DatabaseConfig
from benchbox.core.system import SystemProfiler
from benchbox.examples import ensure_output_directory, execute_example_dry_run
from benchbox.utils.cloud_storage import is_cloud_path
from benchbox.utils.path_utils import resolve_benchmark_runs_dir
from benchbox.utils.printing import (
    error as perror,
    info as pinfo,
    is_quiet as is_global_quiet,
    set_quiet as set_global_quiet,
)

_BENCHMARK_NAMES = [
    "TPCH",
    "TPCDS",
    "TPCDI",
    "SSB",
    "ClickBench",
    "H2ODB",
    "AMPLab",
    "JoinOrder",
    "Primitives",
    "Merge",
    "TPCHavoc",
    "CoffeeShop",
]


def _get_benchmark_name_map() -> dict[str, Any]:

    mapping: dict[str, Any] = {}
    for name in _BENCHMARK_NAMES:
        try:
            cls = getattr(benchbox, name)
        except (ImportError, AttributeError):
            continue
        mapping[name.lower()] = cls
    return mapping


@dataclass(frozen=True)
class _PhaseExecutionPlan:
    name: str
    execution_type: str
    lifecycle: LifecyclePhases
    requires_adapter: bool


def extract_platform_from_argv() -> Optional[str]:

    for idx, token in enumerate(sys.argv):
        if token == "--platform":
            if idx + 1 < len(sys.argv):
                return sys.argv[idx + 1]
        elif token.startswith("--platform="):
            value = token.split("=", 1)[1]
            if value:
                return value

    return None


def list_available_platforms() -> dict[str, bool]:
    return PlatformRegistry.get_platform_availability()


def get_platform_adapter_config(
    platform: str, args, system_profile=None, benchmark_name: str = None, scale_factor: float = None
) -> dict[str, Any]:
    return build_platform_adapter_config(
        platform=platform,
        args_or_config=args,
        system_profile=system_profile,
        benchmark_name=benchmark_name,
        scale_factor=scale_factor,
    )


def create_base_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified Multi-Platform Benchmark Runner", add_help=False)
    benchmark_choices = list(_get_benchmark_name_map().keys())

    core_group = parser.add_argument_group("Core Arguments")
    core_group.add_argument(
        "--list-platforms",
        action="store_true",
        help="List available platforms and exit",
    )
    core_group.add_argument(
        "--list-benchmarks",
        action="store_true",
        help="List available benchmarks and exit",
    )
    core_group.add_argument(
        "--platform",
        type=str,
        help="Target platform (use --list-platforms to see available options)",
    )
    core_group.add_argument(
        "--benchmark",
        type=str,
        choices=benchmark_choices,
        help="Benchmark to run (use --list-benchmarks to see available options)",
    )
    core_group.add_argument("--scale", type=float, default=0.01, help="Scale factor")
    core_group.add_argument(
        "--seed",
        type=int,
        help="RNG seed for reproducible power/throughput tests",
    )
    core_group.add_argument("--phases", type=str, default="power", help="Benchmark phases to run")

    exec_group = parser.add_argument_group("Execution Arguments")
    exec_group.add_argument("--streams", type=int, default=2, help="Number of streams for throughput test")
    exec_group.add_argument(
        "--compress",
        action="store_true",
        help="Enable data compression for generated source data files",
    )
    exec_group.add_argument(
        "--platform-config",
        type=str,
        metavar="FILE",
        help="Path to platform configuration YAML file (defaults to examples/config/{platform}.yaml)",
    )
    exec_group.add_argument(
        "--tuning",
        type=str,
        default="tuned",
        metavar="MODE_OR_FILE",
        help=(
            "Tuning mode: 'tuned', 'notuning', or path to custom config file. "
            "Default: tuned (Note: main CLI defaults to 'notuning')"
        ),
    )
    exec_group.add_argument(
        "--force",
        action="store_true",
        help="Force regeneration of data and recreation of database",
    )
    exec_group.add_argument(
        "--force-upload",
        action="store_true",
        help="Force re-upload of data to cloud storage (for cloud platforms)",
    )

    out_group = parser.add_argument_group("Output Arguments")
    out_group.add_argument(
        "--output",
        type=str,
        metavar="PATH",
        help=(
            "Data output location. REQUIRED for cloud platforms (Databricks, BigQuery, Snowflake, Redshift). "
            "Supports local paths and cloud storage: s3://, gs://, abfss://, dbfs:/Volumes/"
        ),
    )
    out_group.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (-v, -vv)",
    )
    out_group.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress all console output (still saves results JSON)",
    )
    out_group.add_argument(
        "--dry-run",
        type=str,
        metavar="OUTPUT_DIR",
        help="Preview the benchmark plan without execution; artifacts are written to OUTPUT_DIR.",
    )
    out_group.add_argument(
        "--output-dir",
        type=str,
        metavar="PATH",
        help="Directory to save benchmark results and dry run output.",
    )
    out_group.add_argument(
        "--formats",
        type=str,
        default="json",
        help="Comma-separated export formats: json,csv,html (default: json)",
    )
    out_group.add_argument(
        "--anonymize",
        action="store_true",
        help="Anonymize system metadata in exported results",
    )
    out_group.add_argument(
        "--compare-baseline",
        type=str,
        metavar="FILE",
        help="Baseline results JSON file to compare",
    )
    out_group.add_argument(
        "--compare-current",
        type=str,
        metavar="FILE",
        help="Current results JSON file to compare",
    )
    out_group.add_argument(
        "--comparison-output",
        type=str,
        metavar="FILE",
        help="Output HTML file for comparison report (optional)",
    )
    out_group.add_argument("-h", "--help", action="help", help="Show this help message and exit")

    return parser


def get_benchmark_config(args_or_config, platform: str = None) -> dict[str, Any]:
    return build_benchmark_config(args_or_config, platform)


def _make_console_summary(results, config):
    data = {
        "benchmark": config.get("benchmark"),
        "scale_factor": config.get("scale_factor"),
        "platform": config.get("platform", "unknown").title(),
    }
    if hasattr(results, "successful_queries") and hasattr(results, "total_queries"):
        data["success"] = results.successful_queries == results.total_queries
        data["successful_queries"] = results.successful_queries
        data["total_queries"] = results.total_queries
    if hasattr(results, "total_execution_time"):
        data["total_execution_time"] = results.total_execution_time
    if hasattr(results, "average_query_time"):
        data["average_query_time"] = results.average_query_time
    if hasattr(results, "schema_creation_time"):
        data["schema_creation_time"] = results.schema_creation_time
    if hasattr(results, "data_loading_time"):
        data["data_loading_time"] = results.data_loading_time
    if hasattr(results, "duration_seconds"):
        data["total_duration"] = results.duration_seconds
    return data


def prepare_result_data(results, args) -> dict[str, Any]:
    return {
        "benchmark": getattr(args, "benchmark", None),
        "scale_factor": getattr(args, "scale", None),
        "phases": getattr(args, "phases", None),
        "platform": getattr(args, "platform", "").title() or "Duckdb",
        "success": getattr(results, "successful_queries", 0) == getattr(results, "total_queries", 0),
        "successful_queries": getattr(results, "successful_queries", 0),
        "total_queries": getattr(results, "total_queries", 0),
        "total_duration": getattr(results, "total_duration", None),
        "total_execution_time": getattr(results, "total_execution_time", None),
        "average_query_time": getattr(results, "average_query_time", None),
    }


def _display_results(result_data: dict[str, Any], verbosity: int = 0) -> None:
    display_results(result_data, verbosity)


def _determine_test_execution_type(phases: list[str]) -> str:

    normalized = [phase.lower() for phase in phases]
    query_phases = {"warmup", "power", "throughput", "maintenance"}

    if not normalized:
        return "standard"

    has_query_phase = any(phase in query_phases for phase in normalized)
    if not has_query_phase:
        has_generate = "generate" in normalized
        has_load = "load" in normalized
        if has_generate and not has_load:
            return "data_only"
        return "load_only"

    if "throughput" in normalized:
        return "throughput"
    if "maintenance" in normalized:
        return "maintenance"
    if "power" in normalized:
        return "power"
    if "warmup" in normalized:
        return "standard"
    return "standard"


def _normalize_query_subset(value) -> Optional[list[str]]:

    if value is None:
        return None
    if isinstance(value, str):
        parts = [item.strip() for item in value.split(",") if item.strip()]
        return parts or None
    if isinstance(value, (list, tuple, set)):
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        return cleaned or None
    return None


def _build_phase_execution_plan(phases: list[str]) -> list[_PhaseExecutionPlan]:

    plan: list[_PhaseExecutionPlan] = []

    if "generate" in phases:
        plan.append(
            _PhaseExecutionPlan(
                name="generate",
                execution_type="data_only",
                lifecycle=LifecyclePhases(generate=True, load=False, execute=False),
                requires_adapter=False,
            )
        )

    if "load" in phases:
        plan.append(
            _PhaseExecutionPlan(
                name="load",
                execution_type="load_only",
                lifecycle=LifecyclePhases(generate="generate" in phases, load=True, execute=False),
                requires_adapter=True,
            )
        )

    query_phases_order = [phase for phase in phases if phase in {"warmup", "power", "throughput", "maintenance"}]
    for phase in query_phases_order:
        execution_type = "standard" if phase == "warmup" else phase
        plan.append(
            _PhaseExecutionPlan(
                name=phase,
                execution_type=execution_type,
                lifecycle=LifecyclePhases(generate=False, load=True, execute=True),
                requires_adapter=True,
            )
        )

    return plan


def _safe_int(value: Any) -> int:

    try:
        if value is None:
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _phase_succeeded(plan_item: _PhaseExecutionPlan, result: Any) -> bool:

    status_text = str(getattr(result, "validation_status", "") or "").lower()
    if "fail" in status_text:
        return False

    is_query_phase = plan_item.execution_type not in {"data_only", "load_only"}
    total_queries = _safe_int(getattr(result, "total_queries", 0))
    failed_queries = _safe_int(getattr(result, "failed_queries", 0))
    successful_queries = _safe_int(getattr(result, "successful_queries", 0))

    if is_query_phase:
        if total_queries <= 0:
            return False
        if failed_queries > 0:
            return False
        return successful_queries == total_queries

    return failed_queries == 0


def _handle_compare_only(args) -> int:
    results_dir = (
        Path(getattr(args, "output_dir", None))
        if getattr(args, "output_dir", None)
        else resolve_benchmark_runs_dir() / "results"
    )
    exporter = ResultExporter(output_dir=results_dir, anonymize=getattr(args, "anonymize", False))
    comparison = exporter.compare_results(Path(args.compare_baseline), Path(args.compare_current))
    output_path = Path(args.comparison_output) if getattr(args, "comparison_output", None) else None
    report_path = exporter.export_comparison_report(comparison, output_path)
    if not getattr(args, "quiet", False):
        pinfo(f"✅ Comparison report exported to {report_path}")
    return 0


def _validate_phases_and_scale(args, parser) -> list[str]:
    phase_choices = ["generate", "load", "warmup", "power", "throughput", "maintenance"]
    user_phases = [p.strip() for p in args.phases.split(",") if p.strip()]
    if any(p not in phase_choices for p in user_phases):
        parser.error(f"Invalid phases. Choices: {phase_choices}")

    canonical_order = ["generate", "load", "warmup", "power", "throughput", "maintenance"]
    phases_to_run = [p for p in canonical_order if p in user_phases]
    if not phases_to_run:
        parser.error("No phases selected. Provide --phases with at least one supported phase.")
    return phases_to_run


def _validate_output_path(args) -> bool:
    if not args.output:
        return True
    try:
        ValidationRules.validate_output_directory(args.output)
        if is_cloud_path(args.output) and not args.quiet:
            pinfo(f"✅ Cloud storage output validated: {args.output}")
        return True
    except (ValidationError, CloudStorageError) as e:
        if not args.quiet:
            perror(f"Output validation failed: {e}")
        return False


def _execute_dry_run(args, config: dict, phases_to_run: list[str]) -> int:
    dry_run_dir = ensure_output_directory(Path(args.dry_run))
    if not args.quiet:
        pinfo(" Running dry run preview...")

    profiler = SystemProfiler()
    system_profile = profiler.get_system_profile()

    platform_options = get_platform_adapter_config(
        args.platform,
        args,
        system_profile=system_profile,
        benchmark_name=args.benchmark,
        scale_factor=args.scale,
    )
    platform_options = platform_options or {}
    if args.platform.lower() == "duckdb" and "database_path" not in platform_options:
        platform_options["database_path"] = ":memory:"
    if getattr(args, "force_upload", False):
        platform_options["force_upload"] = True

    query_subset = _normalize_query_subset(config.get("query_subset") or config.get("queries"))

    options = {
        "force_regenerate": bool(config.get("force_regenerate") or config.get("force")),
        "enable_preflight_validation": bool(config.get("enable_preflight_validation")),
        "enable_postload_validation": bool(config.get("enable_postload_validation")),
        "seed": getattr(args, "seed", None) or config.get("seed"),
        "phases": ",".join(phases_to_run) if phases_to_run else None,
    }
    if config.get("tuning_config"):
        options["unified_tuning_configuration"] = config["tuning_config"]
        options["tuning_enabled"] = True
    if config.get("compress"):
        options["compress_data"] = True
        options["compression_type"] = config.get("compression_type", "zstd")
    if query_subset:
        options["query_subset"] = query_subset

    benchmark_config = BenchmarkConfig(
        name=args.benchmark,
        display_name=config.get("display_name", args.benchmark.upper()),
        scale_factor=float(config.get("scale_factor", args.scale)),
        queries=query_subset,
        concurrency=int(config.get("streams", 1)),
        options={k: v for k, v in options.items() if v is not None},
        compress_data=bool(config.get("compress")),
        compression_type=config.get("compression_type", "zstd"),
        test_execution_type=_determine_test_execution_type(phases_to_run),
    )
    database_config = DatabaseConfig(
        type=args.platform,
        name=f"{args.platform}_dry_run",
        options=platform_options,
    )
    execute_example_dry_run(
        benchmark_config=benchmark_config,
        database_config=database_config,
        output_dir=dry_run_dir,
        filename_prefix=f"{args.platform}_{args.benchmark}",
    )
    return 0


def _run_phase_plan(
    args,
    config: dict,
    phase_plan: list,
    benchmark_config: BenchmarkConfig,
    database_config: Optional[DatabaseConfig],
    adapter,
    platform_options: dict,
    benchmark_instance,
    system_profile,
    validation_opts: ValidationOptions,
    verbosity_settings,
    output_root,
    exporter: ResultExporter,
    formats: list[str],
) -> bool:
    phase_results: list[tuple[Any, Any]] = []
    for plan_item in phase_plan:
        phase_config = benchmark_config.model_copy(update={"test_execution_type": plan_item.execution_type})
        lifecycle_result = run_benchmark_lifecycle(
            benchmark_config=phase_config,
            database_config=database_config if plan_item.requires_adapter else None,
            system_profile=system_profile,
            platform_config=platform_options if plan_item.requires_adapter else None,
            platform_adapter=adapter if plan_item.requires_adapter else None,
            benchmark_instance=benchmark_instance,
            phases=plan_item.lifecycle,
            validation_opts=validation_opts,
            output_root=str(output_root) if output_root else None,
            verbosity=verbosity_settings,
        )

        existing_metadata = getattr(lifecycle_result, "execution_metadata", None)
        metadata: dict[str, Any] = existing_metadata.copy() if isinstance(existing_metadata, dict) else {}
        metadata["phase"] = plan_item.name
        lifecycle_result.execution_metadata = metadata
        phase_results.append((plan_item, lifecycle_result))

        if not args.quiet:
            summary_context = {
                "benchmark": args.benchmark,
                "scale_factor": phase_config.scale_factor,
                "platform": args.platform,
                "phase": plan_item.name,
            }
            summary = _make_console_summary(lifecycle_result, summary_context)
            display_results(summary, args.verbose)

        exported = exporter.export_result(lifecycle_result, formats=formats)
        if not args.quiet and exported:
            for _fmt, path in exported.items():
                pinfo(f"✅ Results saved to {path}")

    overall_success = True
    for plan_item, lifecycle_result in phase_results:
        overall_success = overall_success and _phase_succeeded(plan_item, lifecycle_result)
    return overall_success


def _handle_list_commands_main() -> int | None:
    if "--list-platforms" in sys.argv:
        display_platform_list(
            PlatformRegistry.get_platform_availability(),
            PlatformRegistry.get_platform_requirements,
        )
        return 0
    if "--list-benchmarks" in sys.argv:
        display_benchmark_list(_get_benchmark_name_map())
        return 0
    return None


def _parse_main_args() -> tuple[argparse.Namespace, argparse.ArgumentParser]:
    platform = extract_platform_from_argv()
    parser = create_base_parser()
    if platform:
        try:
            PlatformRegistry.add_platform_arguments(parser, platform)
        except ValueError:
            pass
    args = parser.parse_args()

    skip_platform_requirement = (
        args.list_platforms
        or args.list_benchmarks
        or (getattr(args, "compare_baseline", None) and getattr(args, "compare_current", None))
    )

    if not skip_platform_requirement:
        missing = []
        if not args.platform:
            missing.append("--platform")
        if not args.benchmark:
            missing.append("--benchmark")
        if missing:
            parser.error("Missing required option(s): " + ", ".join(missing))

    return args, parser


def _validate_main_preconditions(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> tuple[int | None, list[str]]:
    availability_map = list_available_platforms()
    is_available = availability_map.get(args.platform, PlatformRegistry.is_platform_available(args.platform))
    if not is_available:
        requirements = PlatformRegistry.get_platform_requirements(args.platform)
        if not args.quiet:
            perror(f"Error: Platform '{args.platform}' is not available.")
            pinfo(f"Install required dependencies: {requirements}")
        return 1, []

    phases_to_run = _validate_phases_and_scale(args, parser)

    if args.scale >= 1 and args.scale != int(args.scale):
        if not args.quiet:
            perror(f"Scale factors >= 1 must be whole integers. Got: {args.scale}")
            pinfo("Use values like 1, 2, 10, etc. for large scale factors")
        return 1, phases_to_run

    if not _validate_output_path(args):
        return 1, phases_to_run

    return None, phases_to_run


def _build_main_benchmark_config(
    args: argparse.Namespace,
    config: dict[str, Any],
    verbosity_settings: Any,
    phases_to_run: list[str],
) -> tuple[BenchmarkConfig, dict[str, Any], str | None]:
    benchmark_config_raw = get_benchmark_config(config)
    output_root = benchmark_config_raw.get("output_dir")
    query_subset = _normalize_query_subset(config.get("query_subset") or config.get("queries"))

    benchmark_options: dict[str, Any] = {
        "force_regenerate": bool(
            benchmark_config_raw.get("force_regenerate") or config.get("force_regenerate") or config.get("force")
        ),
        "no_regenerate": bool(config.get("no_regenerate")),
        "enable_preflight_validation": bool(config.get("enable_preflight_validation")),
        "enable_postgen_manifest_validation": bool(config.get("enable_postgen_manifest_validation")),
        "enable_postload_validation": bool(config.get("enable_postload_validation")),
        "seed": getattr(args, "seed", None) or config.get("seed"),
        "verbosity_settings": verbosity_settings,
    }
    if config.get("tuning_config"):
        benchmark_options["unified_tuning_configuration"] = config["tuning_config"]
        benchmark_options["tuning_enabled"] = True
    if query_subset:
        benchmark_options["query_subset"] = query_subset

    benchmark_config = BenchmarkConfig(
        name=args.benchmark,
        display_name=config.get("display_name", args.benchmark.upper()),
        scale_factor=float(config.get("scale_factor", args.scale)),
        queries=query_subset,
        concurrency=int(config.get("streams", getattr(args, "streams", 1))),
        options={k: v for k, v in benchmark_options.items() if v is not None},
        compress_data=bool(benchmark_config_raw.get("compress_data")),
        compression_type=benchmark_config_raw.get("compression_type", config.get("compression_type", "zstd")),
        compression_level=config.get("compression_level"),
        test_execution_type=_determine_test_execution_type(phases_to_run),
    )

    return benchmark_config, benchmark_options, output_root


def _build_main_adapter_and_db(
    args: argparse.Namespace,
    config: dict[str, Any],
    platform_options: dict[str, Any],
    needs_adapter: bool,
) -> tuple[Any | None, Optional[DatabaseConfig]]:
    if not needs_adapter:
        return None, None

    adapter = PlatformRegistry.create_adapter(args.platform, config)
    if args.verbose > 0 and not args.quiet:
        display_verbose_config_feedback(adapter.config, args.platform)

    database_config = DatabaseConfig(
        type=args.platform,
        name=config.get("database_name") or f"{args.platform}_{args.benchmark}",
        options=platform_options,
        driver_package=config.get("driver_package"),
        driver_version=config.get("driver_version"),
        driver_version_resolved=config.get("driver_version_resolved"),
        driver_auto_install=bool(config.get("driver_auto_install")),
    )
    return adapter, database_config


def _execute_main_benchmark(
    args: argparse.Namespace,
    config: dict[str, Any],
    phase_plan: list[Any],
    phases_to_run: list[str],
    verbosity_settings: Any,
    needs_adapter: bool,
) -> int:
    if args.dry_run:
        return _execute_dry_run(args, config, phases_to_run)

    profiler = SystemProfiler()
    system_profile = profiler.get_system_profile()

    platform_options = get_platform_adapter_config(
        args.platform,
        config,
        system_profile=system_profile,
        benchmark_name=args.benchmark,
        scale_factor=config.get("scale_factor", args.scale),
    )
    platform_options = platform_options or {}
    if getattr(args, "force_upload", False):
        platform_options["force_upload"] = True

    benchmark_config, benchmark_options, output_root = _build_main_benchmark_config(
        args, config, verbosity_settings, phases_to_run
    )
    benchmark_instance = config.get("benchmark_instance")

    adapter, database_config = _build_main_adapter_and_db(args, config, platform_options, needs_adapter)

    validation_opts = ValidationOptions(
        enable_preflight_validation=bool(benchmark_options.get("enable_preflight_validation")),
        enable_postgen_manifest_validation=bool(benchmark_options.get("enable_postgen_manifest_validation")),
        enable_postload_validation=bool(benchmark_options.get("enable_postload_validation")),
    )

    formats = [f.strip() for f in (args.formats or "json").split(",") if f.strip()]

    results_dir = Path(args.output_dir) if args.output_dir else resolve_benchmark_runs_dir() / "results"
    from io import StringIO

    from rich.console import Console

    export_console = Console(file=StringIO(), stderr=False) if args.quiet else None
    exporter = ResultExporter(output_dir=results_dir, anonymize=args.anonymize, console=export_console)

    overall_success = _run_phase_plan(
        args=args,
        config=config,
        phase_plan=phase_plan,
        benchmark_config=benchmark_config,
        database_config=database_config,
        adapter=adapter,
        platform_options=platform_options,
        benchmark_instance=benchmark_instance,
        system_profile=system_profile,
        validation_opts=validation_opts,
        verbosity_settings=verbosity_settings,
        output_root=output_root,
        exporter=exporter,
        formats=formats,
    )
    return 0 if overall_success else 1


def main() -> int:
    listing = _handle_list_commands_main()
    if listing is not None:
        return listing

    args, parser = _parse_main_args()

    if (
        hasattr(args, "compare_baseline")
        and hasattr(args, "compare_current")
        and args.compare_baseline
        and args.compare_current
    ):
        return _handle_compare_only(args)

    precondition_exit, phases_to_run = _validate_main_preconditions(args, parser)
    if precondition_exit is not None:
        return precondition_exit

    previous_quiet_state = is_global_quiet()
    if args.quiet:
        set_global_quiet(True)

    _, verbosity_settings = setup_verbose_logging(args.verbose, quiet=args.quiet)

    config = merge_all_configs(
        platform=args.platform,
        benchmark=args.benchmark,
        args=args,
        tuning_mode=args.tuning,
        platform_config_path=getattr(args, "platform_config", None),
        verbose=args.verbose > 0,
    )
    config["phases"] = phases_to_run

    if not args.quiet:
        display_configuration_summary(config, args.verbose)
    phase_plan = _build_phase_execution_plan(phases_to_run)
    needs_adapter = any(item.requires_adapter for item in phase_plan)

    try:
        return _execute_main_benchmark(args, config, phase_plan, phases_to_run, verbosity_settings, needs_adapter)
    except Exception as e:
        if not args.quiet:
            perror(f"Benchmark execution failed: {e}")
        return 1
    finally:
        if args.quiet:
            set_global_quiet(previous_quiet_state)


if __name__ == "__main__":
    exit(main())
