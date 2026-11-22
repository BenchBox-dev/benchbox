from __future__ import annotations

import sys
import types
from typing import Any

import click
from rich.prompt import Confirm

from benchbox.cli.benchmarks import BenchmarkManager
from benchbox.cli.cloud_storage import prompt_cloud_output_location
from benchbox.cli.commands import run as _run
from benchbox.cli.commands.run import (
    DATA_ORGANIZATION_ENV,
    _build_benchmark_config,
    _build_execution_context_from_plan,
    _capture_resolved_run_plan,
    _derive_execution_type,
    _emit_load_failure_marker,
    _force_recreate_requested,
    _resolve_data_organization,
    _resolve_run_request,
    _tuning_override_entries,
    _warn_unofficial_subscale,
)
from benchbox.cli.composite_params import (
    CompressionConfig,
    ForceConfig,
)
from benchbox.cli.database import DatabaseManager
from benchbox.cli.orchestrator import BenchmarkOrchestrator, resolved_deployment_mode
from benchbox.cli.platform import (
    normalize_platform_name,
)
from benchbox.cli.platform_checks import check_and_setup_platform_credentials
from benchbox.cli.presentation.system import display_system_recommendations
from benchbox.cli.run_platform_resolution import (
    _interactive_resolve_mode_for_selected_platform,
    _reject_external_tuned,
    _tuning_arg_is_tuning_bearing,
)
from benchbox.cli.run_resolution import (
    ResolvedRunPlan,
    current_run_request as _current_run_request,
    resolve_quick_restart_atomically,
)
from benchbox.cli.shared import console
from benchbox.cli.tuning_resolver import (
    TuningMode,
    TuningResolution,
    TuningSource,
    promote_tuning_provenance,
)
from benchbox.cli.tuning_runtime import (
    build_baseline_unified_config,
    infer_runtime_tuning_mode,
)
from benchbox.cli.vendor_provenance_options import vendor_provenance_kwargs
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.utils.verbosity import VerbositySettings

_GUIDED_CREDENTIAL_PLATFORMS = frozenset(
    {"athena", "bigquery", "databricks", "motherduck", "redshift", "singlestore", "snowflake"}
)


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
    profiler = _run.SystemProfiler()
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
    db_manager = _run.DatabaseManager()
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

    bench_manager = _run.BenchmarkManager()
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
    db_manager = _run.DatabaseManager()
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
    bench_manager = _run.BenchmarkManager()
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
        console.print("[dim]TPC official mode requires a seed for reproducibility.[/dim]")
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
        s.df_tuning_config = _run.resolve_dataframe_tuning_config(
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
    if getattr(s, "allow_greybeam_inline_load", False):
        s.benchmark_config.options["allow_greybeam_inline_load"] = True
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

    orchestrator = _run.BenchmarkOrchestrator(non_interactive=s.non_interactive)
    orchestrator.set_verbosity(s.verbosity_settings)

    if s.output:
        normalized_output = _run.normalize_output_root(
            s.output, s.benchmark_config.name, s.benchmark_config.scale_factor
        )
        assert normalized_output is not None
        orchestrator.set_custom_output_dir(normalized_output)

    result = _run._execute_orchestrated_run(
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
        timeout=getattr(s, "timeout", None),
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
        exported_files = _run._export_orchestrated_result(
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
            **vendor_provenance_kwargs(s),
        )

        if s.quiet:
            for _format_name, filepath in exported_files.items():
                click.echo(filepath)
        else:
            console.print(f"\n[green]✅ Benchmark completed with status: {result.validation_status}[/green]")
            console.print(f"Result ID: [cyan]{result.execution_id}[/cyan]")
            for format_name, filepath in exported_files.items():
                console.print(f"{format_name.upper()}: [dim]{filepath}[/dim]")

        _run._render_post_run_charts(result, console, s.quiet)

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
