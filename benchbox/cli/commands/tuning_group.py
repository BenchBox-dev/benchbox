# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import logging
from pathlib import Path
from typing import Optional

import click
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from benchbox.cli.shared import console
from benchbox.cli.tuning_resolver import (
    display_tuning_list,
    display_tuning_show,
    is_dataframe_tuning_file,
    resolve_tuning,
)
from benchbox.core.config_utils import load_config_file
from benchbox.core.dataframe.tuning import (
    DataFrameTuningConfiguration,
    detect_system_profile,
    format_issues,
    get_profile_summary,
    get_smart_defaults,
    has_errors,
    load_dataframe_tuning,
    save_dataframe_tuning,
    validate_dataframe_tuning,
)
from benchbox.core.dataframe.tuning.profiles import (
    DATAFRAME_CAPABILITY_ROWS,
    DATAFRAME_PLATFORMS,
    profile_engine_hint,
)
from benchbox.core.platform_manifest import get_platform_aliases
from benchbox.core.tuning.capability_registry import (
    PLATFORM_ALIASES,
    known_registry_platforms,
    resolve_platform_key,
)
from benchbox.core.tuning.interface import UnifiedTuningConfiguration
from benchbox.platforms.adapter_factory import is_dataframe_mode


@click.group(
    "tuning",
    help=(
        "Tuning configuration commands.\n"
        "\n"
        "Create, validate, and inspect tuning configurations for SQL and DataFrame platforms.\n"
        "\n"
        "\b\n"
        "Examples:\n"
        "  benchbox tuning init --platform duckdb\n"
        "  benchbox tuning init --platform polars --mode dataframe\n"
        "  benchbox tuning validate config.yaml --platform polars\n"
        "  benchbox tuning defaults --platform polars"
    ),
)
def tuning_group() -> None:
    pass


@tuning_group.command(
    "init",
    help=(
        "Create a tuning configuration file.\n"
        "\n"
        "Generates a YAML configuration file with platform-specific tuning options.\n"
        "Use --mode to specify SQL (constraints, indexes) or DataFrame (parallelism, memory).\n"
        "\n"
        "\b\n"
        "Examples:\n"
        "  benchbox tuning init --platform duckdb\n"
        "  benchbox tuning init --platform snowflake --output my-tuning.yaml\n"
        "  benchbox tuning init --platform polars --mode dataframe\n"
        "  benchbox tuning init --platform polars --smart-defaults\n"
        "  benchbox tuning init --platform dask --profile memory-constrained"
    ),
)
@click.option(
    "--platform",
    type=str,
    required=True,
    help="Target platform (duckdb, snowflake, polars, pandas, etc.)",
)
@click.option(
    "--mode",
    type=click.Choice(["sql", "dataframe", "auto"], case_sensitive=False),
    default="auto",
    help="Tuning mode: sql, dataframe, or auto (detect from platform)",
)
@click.option(
    "--profile",
    type=click.Choice(["default", "optimized", "streaming", "memory-constrained", "gpu"]),
    default="default",
    help="Configuration profile for DataFrame platforms",
)
@click.option(
    "--output",
    type=str,
    default=None,
    help="Output file path (default: <platform>_tuning.yaml)",
)
@click.option(
    "--smart-defaults",
    is_flag=True,
    help="Use smart defaults based on detected system profile (DataFrame only)",
)
@click.pass_context
def init(
    ctx: click.Context,
    platform: str,
    mode: str,
    profile: str,
    output: Optional[str],
    smart_defaults: bool,
) -> None:
    platform_lower = platform.lower()
    platform_key = get_platform_aliases("cli").get(platform_lower, platform_lower)

    if mode == "auto":
        mode = "dataframe" if is_dataframe_mode(platform_lower) else "sql"

    if mode == "dataframe" and platform_key not in DATAFRAME_PLATFORMS:
        console.print(f"[red]Platform '{platform}' does not support DataFrame mode[/red]")
        console.print(f"[yellow]DataFrame platforms: {', '.join(sorted(DATAFRAME_PLATFORMS))}[/yellow]")
        ctx.exit(1)

    if mode == "sql":
        _init_sql_tuning(ctx, platform_key, output)
    else:
        _init_dataframe_tuning(ctx, platform_key, profile, output, smart_defaults)


def _init_sql_tuning(ctx: click.Context, platform: str, output: Optional[str]) -> None:
    console.print(
        Panel.fit(
            Text(f"Creating SQL Tuning Configuration for {platform.title()}", style="bold cyan"),
            style="cyan",
        )
    )

    output_path = Path(output) if output else Path(f"{platform.lower()}_tuning.yaml")

    config_manager = ctx.obj["config"]

    try:
        config_manager.create_sample_unified_tuning_config(output_path, platform)
        console.print("\n[green]Tuning configuration created[/green]")
        console.print(f"File: [cyan]{output_path}[/cyan]")
        console.print(f"Platform: [yellow]{platform}[/yellow]")
        console.print("Mode: [yellow]SQL[/yellow]")
        console.print("\nEdit this file to customize tuning settings for your benchmarks.")
        console.print(f"Use with: [cyan]benchbox run --tuning {output_path}[/cyan]")
    except Exception as e:
        console.print(f"[red]Failed to create tuning configuration: {e}[/red]")
        ctx.exit(1)


def _init_dataframe_tuning(
    ctx: click.Context,
    platform: str,
    profile: str,
    output: Optional[str],
    smart_defaults: bool,
) -> None:
    console.print(
        Panel.fit(
            Text(f"Creating DataFrame Tuning Configuration for {platform.title()}", style="bold cyan"),
            style="cyan",
        )
    )

    if output is None:
        output = f"{platform}_{profile.replace('-', '_')}_tuning.yaml"
    output_path = Path(output)

    try:
        if smart_defaults:
            config = get_smart_defaults(platform)
            console.print("[blue]Using smart defaults based on detected system profile[/blue]")

            sys_profile = detect_system_profile()
            summary = get_profile_summary(sys_profile)
            console.print(f"  CPU cores: {summary['cpu_cores']}")
            console.print(f"  Available memory: {summary['available_memory_gb']:.1f} GB")
            console.print(f"  Memory category: {summary['memory_category']}")
            if summary["has_gpu"]:
                console.print(f"  GPU memory: {summary['gpu_memory_gb']:.1f} GB")
        else:
            config = _create_profile_config(platform, profile)

        save_dataframe_tuning(config, output_path)

        console.print("\n[green]Tuning configuration created[/green]")
        console.print(f"File: [cyan]{output_path}[/cyan]")
        console.print(f"Platform: [yellow]{platform}[/yellow]")
        console.print("Mode: [yellow]DataFrame[/yellow]")
        console.print(f"Profile: [yellow]{profile}[/yellow]")
        engine_hint = None if smart_defaults else profile_engine_hint(platform, profile)
        if engine_hint:
            console.print(engine_hint)

        enabled = config.get_enabled_settings()
        if enabled:
            console.print(f"\nEnabled settings ({len(enabled)}):")
            for setting in sorted(enabled, key=lambda x: x.value):
                console.print(f"  - {setting.value}")
        else:
            console.print("\nNo custom settings enabled (using defaults)")

        console.print("\nEdit this file to customize tuning settings for your benchmarks.")
        console.print(f"Use with: [cyan]benchbox run --tuning {output_path}[/cyan]")

    except Exception as e:
        console.print(f"[red]Failed to create tuning configuration: {e}[/red]")
        raise click.Abort() from e


def _create_profile_config(platform: str, profile: str) -> DataFrameTuningConfiguration:
    if profile == "gpu" and platform != "cudf":
        console.print("[yellow]Warning: GPU profile is only applicable to cuDF[/yellow]")
    from benchbox.core.dataframe.tuning.profiles import create_profile_config as _core_create

    return _core_create(platform, profile)


@tuning_group.command(
    "validate",
    help=(
        "Validate a tuning configuration file.\n"
        "\n"
        "Checks the configuration for errors and warnings specific to the target platform.\n"
        "DataFrame platforms (datafusion, polars, pandas, dask, cudf) validate a DataFrame\n"
        "tuning file. Any other platform validates a SQL tuning file against the platform\n"
        "capability registry.\n"
        "\n"
        "\b\n"
        "Examples:\n"
        "  benchbox tuning validate polars_tuning.yaml --platform polars\n"
        "  benchbox tuning validate my_config.yaml --platform dask\n"
        "  benchbox tuning validate examples/tunings/duckdb/tpch_tuned.yaml --platform duckdb"
    ),
)
@click.argument("config_file", type=click.Path(exists=True))
@click.option(
    "--platform",
    type=str,
    required=True,
    help="Target platform: a DataFrame platform or a SQL platform in the tuning capability registry",
)
@click.option(
    "--mode",
    type=click.Choice(["sql", "dataframe", "auto"], case_sensitive=False),
    default="auto",
    help="Tuning mode: sql, dataframe, or auto (detect from platform)",
)
def validate_config(config_file: str, platform: str, mode: str) -> None:
    platform_name = platform.lower()
    if (
        platform_name not in DATAFRAME_PLATFORMS
        and resolve_platform_key(platform_name) not in known_registry_platforms()
    ):
        choices = sorted(DATAFRAME_PLATFORMS | known_registry_platforms() | set(PLATFORM_ALIASES))
        raise click.BadParameter(
            f"unknown platform '{platform}'. Choose from: {', '.join(choices)}",
            param_hint="--platform",
        )

    if mode == "auto":
        mode = "dataframe" if platform_name in DATAFRAME_PLATFORMS else "sql"

    console.print(
        Panel.fit(
            Text("Validating Tuning Configuration", style="bold cyan"),
            style="cyan",
        )
    )

    if mode == "dataframe":
        if platform_name not in DATAFRAME_PLATFORMS:
            console.print(f"[red]Platform '{platform}' does not support DataFrame mode[/red]")
            console.print(f"[yellow]DataFrame platforms: {', '.join(sorted(DATAFRAME_PLATFORMS))}[/yellow]")
            raise click.Abort()
        _validate_dataframe_config(config_file, platform_name)
    else:
        _validate_sql_config(config_file, platform_name)


def _validate_dataframe_config(config_file: str, platform: str) -> None:
    try:
        config = load_dataframe_tuning(config_file)
        console.print(f"Loaded: [cyan]{config_file}[/cyan]")

        issues = validate_dataframe_tuning(config, platform)

        if not issues:
            console.print(f"\n[green]Configuration is valid for {platform}[/green]")
        else:
            console.print(f"\n{format_issues(issues)}")

            if has_errors(issues):
                console.print("\n[red]Configuration has errors that must be fixed[/red]")
                raise click.Abort()
            else:
                console.print("\n[yellow]Configuration is valid but has warnings[/yellow]")

        console.print("\n[bold]Configuration Summary:[/bold]")
        summary = config.get_summary()
        console.print(f"  Enabled settings: {summary['setting_count']}")
        console.print(f"  Has streaming: {summary['has_streaming']}")
        console.print(f"  Has GPU: {summary['has_gpu']}")

    except click.Abort:
        raise
    except Exception as e:
        console.print(f"[red]Failed to validate configuration: {e}[/red]")
        raise click.Abort() from e


def _validate_sql_config(config_file: str, platform: str) -> None:
    platform_key = resolve_platform_key(platform)
    try:
        data = load_config_file(config_file)
        if not data:
            raise ValueError("Configuration file is empty")
        if is_dataframe_tuning_file(data):
            raise ValueError(
                f"'{config_file}' is a DataFrame tuning file; validate it with a DataFrame platform "
                f"({', '.join(sorted(DATAFRAME_PLATFORMS))})"
            )
        config = UnifiedTuningConfiguration.from_dict(data)
    except Exception as e:
        console.print(f"[red]Failed to validate configuration: {e}[/red]")
        raise click.Abort() from e

    console.print(f"Loaded: [cyan]{config_file}[/cyan]")

    errors, warnings = config.validate_for_platform_detailed(platform_key)
    enabled = sorted(config.get_enabled_tuning_types(), key=lambda tuning_type: tuning_type.value)

    for message in errors:
        console.print(f"[red]Error: {message}[/red]")
    for message in warnings:
        console.print(f"[yellow]Warning: {message}[/yellow]")

    if errors:
        console.print("\n[red]Configuration has errors that must be fixed[/red]")
        raise click.Abort()
    if warnings:
        console.print("\n[yellow]Configuration is valid but has warnings[/yellow]")
    else:
        console.print(f"\n[green]Configuration is valid for {platform_key}[/green]")

    console.print("\n[bold]Configuration Summary:[/bold]")
    console.print(f"  Enabled tuning types: {len(enabled)}")
    console.print(f"  Table tunings: {len(config.table_tunings)}")


@tuning_group.command(
    "defaults",
    help=(
        "Show smart defaults for a platform based on system profile.\n"
        "\n"
        "Analyzes the current system (CPU, memory, GPU) and shows recommended\n"
        "tuning settings for the specified platform.\n"
        "\n"
        "\b\n"
        "Examples:\n"
        "  benchbox tuning defaults --platform polars\n"
        "  benchbox tuning defaults --platform cudf"
    ),
)
@click.option(
    "--platform",
    type=click.Choice(sorted(DATAFRAME_PLATFORMS), case_sensitive=False),
    required=True,
    help="Target DataFrame platform",
)
def show_defaults(platform: str) -> None:
    console.print(
        Panel.fit(
            Text(f"Smart Defaults for {platform.title()}", style="bold cyan"),
            style="cyan",
        )
    )

    sys_profile = detect_system_profile()
    summary = get_profile_summary(sys_profile)

    console.print("\n[bold]Detected System Profile:[/bold]")
    table = Table(show_header=False, box=None)
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="yellow")

    table.add_row("CPU Cores", str(summary["cpu_cores"]))
    table.add_row("Available Memory", f"{summary['available_memory_gb']:.1f} GB")
    table.add_row("Memory Category", summary["memory_category"])
    if summary["has_gpu"]:
        table.add_row("GPU Available", "Yes")
        table.add_row("GPU Memory", f"{summary['gpu_memory_gb']:.1f} GB")
        table.add_row("GPU Count", str(summary["gpu_device_count"]))
    else:
        table.add_row("GPU Available", "No")

    console.print(table)

    config = get_smart_defaults(platform)

    console.print(f"\n[bold]Recommended Settings for {platform.title()}:[/bold]")
    _display_config_settings_table(config)

    mode_option = " --mode dataframe" if platform.lower() == "datafusion" else ""
    console.print(f"\n[dim]To use these settings: benchbox run --platform {platform}{mode_option} --tuning auto[/dim]")


def _display_config_settings_table(config: DataFrameTuningConfiguration) -> None:
    settings_table = Table(show_header=True)
    settings_table.add_column("Category", style="cyan")
    settings_table.add_column("Setting", style="white")
    settings_table.add_column("Value", style="yellow")

    for attr, label in [
        ("thread_count", "thread_count"),
        ("worker_count", "worker_count"),
        ("threads_per_worker", "threads_per_worker"),
    ]:
        val = getattr(config.parallelism, attr)
        if val is not None:
            settings_table.add_row("Parallelism", label, str(val))

    if config.memory.memory_limit is not None:
        settings_table.add_row("Memory", "memory_limit", config.memory.memory_limit)
    if config.memory.chunk_size is not None:
        settings_table.add_row("Memory", "chunk_size", str(config.memory.chunk_size))
    if config.memory.spill_to_disk:
        settings_table.add_row("Memory", "spill_to_disk", "True")

    if config.execution.streaming_mode:
        settings_table.add_row("Execution", "streaming_mode", "True")
    if config.execution.engine_affinity is not None:
        settings_table.add_row("Execution", "engine_affinity", config.execution.engine_affinity)

    if config.data_types.dtype_backend != "numpy_nullable":
        settings_table.add_row("Data Types", "dtype_backend", config.data_types.dtype_backend)
    if config.data_types.auto_categorize_strings:
        settings_table.add_row("Data Types", "auto_categorize_strings", "True")

    if config.io.memory_map:
        settings_table.add_row("I/O", "memory_map", "True")

    if config.gpu.enabled:
        settings_table.add_row("GPU", "enabled", "True")
        settings_table.add_row("GPU", "device_id", str(config.gpu.device_id))
        settings_table.add_row("GPU", "pool_type", config.gpu.pool_type)
        if config.gpu.spill_to_host:
            settings_table.add_row("GPU", "spill_to_host", "True")

    if settings_table.row_count == 0:
        console.print("  [dim]Using default settings (no custom configuration needed)[/dim]")
    else:
        console.print(settings_table)


@tuning_group.command(
    "list",
    help=(
        "List available tuning templates.\n"
        "\n"
        "Shows all tuning templates in examples/tunings/, optionally filtered\n"
        "by platform and/or benchmark.\n"
        "\n"
        "\b\n"
        "Examples:\n"
        "  benchbox tuning list\n"
        "  benchbox tuning list --platform duckdb\n"
        "  benchbox tuning list --platform duckdb --benchmark tpch\n"
        "  benchbox tuning list --benchmark tpcds"
    ),
)
@click.option(
    "--platform",
    type=str,
    default=None,
    help="Filter to specific platform (e.g., duckdb, snowflake)",
)
@click.option(
    "--benchmark",
    type=str,
    default=None,
    help="Filter to specific benchmark (e.g., tpch, tpcds)",
)
def list_templates(platform: Optional[str], benchmark: Optional[str]) -> None:
    display_tuning_list(console, platform, benchmark)


@tuning_group.command(
    "show",
    help=(
        "Show resolved tuning configuration.\n"
        "\n"
        "Displays the tuning configuration that would be used for a given\n"
        "--tuning argument, platform, and benchmark combination.\n"
        "\n"
        "TUNING_ARG can be: tuned, notuning, auto, or a file path.\n"
        "\n"
        "\b\n"
        "Examples:\n"
        "  benchbox tuning show tuned --platform duckdb --benchmark tpch\n"
        "  benchbox tuning show ./my-tuning.yaml\n"
        "  benchbox tuning show auto --platform polars\n"
        "  benchbox tuning show notuning"
    ),
)
@click.argument("tuning_arg", default="tuned")
@click.option(
    "--platform",
    type=str,
    default=None,
    help="Target platform (for template discovery)",
)
@click.option(
    "--benchmark",
    type=str,
    default=None,
    help="Target benchmark (for template discovery)",
)
@click.pass_context
def show_tuning(
    ctx: click.Context,
    tuning_arg: str,
    platform: Optional[str],
    benchmark: Optional[str],
) -> None:
    config_manager = ctx.obj["config"]

    try:
        resolution = resolve_tuning(
            tuning_arg=tuning_arg,
            platform=platform,
            benchmark=benchmark,
            config_manager=config_manager,
            console=console,
            quiet=False,
            non_interactive=True,
        )

        loaded_config = None
        if resolution.config_file:
            try:
                loaded_config = config_manager.load_unified_tuning_config(resolution.config_file, platform)
            except Exception as e:
                console.print(f"[yellow]Warning: Could not load config: {e}[/yellow]")
                logger = logging.getLogger(__name__)
                logger.debug(f"Failed to load tuning config from {resolution.config_file}: {e}", exc_info=True)

        display_tuning_show(console, loaded_config, resolution)

    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        ctx.exit(1)


def _sql_platform_capability_rows() -> list[tuple[str, str, str]]:
    from benchbox.core.tuning.capability_registry import PLATFORM_TUNING_CAPABILITIES

    rows = []
    for platform in sorted(PLATFORM_TUNING_CAPABILITIES):
        capabilities = PLATFORM_TUNING_CAPABILITIES[platform]
        tuning_types = ", ".join(sorted(tuning_type.value for tuning_type in capabilities))

        gaps = sorted(
            tuning_type.value for tuning_type, capability in capabilities.items() if capability.rendered_via == "none"
        )
        notes = f"gap (not yet rendered): {', '.join(gaps)}" if gaps else "fully rendered"

        rows.append((platform, tuning_types, notes))
    return rows


@tuning_group.command(
    "platforms",
    help=(
        "List platforms and their tuning capabilities.\n\nShows which platforms support tuning and their key features."
    ),
)
def list_platforms() -> None:
    console.print(
        Panel.fit(
            Text("Platform Tuning Capabilities", style="bold cyan"),
            style="cyan",
        )
    )

    console.print("\n[bold]SQL Platforms:[/bold]")
    sql_table = Table(show_header=True)
    sql_table.add_column("Platform", style="cyan")
    sql_table.add_column("Compatible Tuning Types", style="yellow")
    sql_table.add_column("Rendering", style="white")

    for name, tuning_types, notes in _sql_platform_capability_rows():
        sql_table.add_row(name, tuning_types, notes)

    console.print(sql_table)

    console.print("\n[bold]DataFrame Platforms:[/bold]")
    df_table = Table(show_header=True)
    df_table.add_column("Platform", style="cyan")
    df_table.add_column("Family", style="white")
    df_table.add_column("Key Features", style="yellow")
    df_table.add_column("GPU", style="green")

    for name, family, features, gpu in DATAFRAME_CAPABILITY_ROWS:
        df_table.add_row(name, family, features, gpu)

    console.print(df_table)

    console.print("\n[dim]Use 'benchbox tuning defaults --platform <name>' for platform-specific recommendations[/dim]")


__all__ = ["tuning_group"]
