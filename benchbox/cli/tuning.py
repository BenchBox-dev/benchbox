# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from pathlib import Path
from typing import Any, Optional

from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from rich.text import Text

from benchbox.core.benchmark_registry import get_presort_table_configs
from benchbox.core.schemas import SystemProfile
from benchbox.core.tuning.interface import TuningType, UnifiedTuningConfiguration
from benchbox.utils.printing import quiet_console

console = quiet_console


def autofill_defaults(system_profile: SystemProfile, platform: str, benchmark: str = "tpch") -> dict[str, Any]:
    defaults = {}

    cpu_cores = getattr(system_profile, "cpu_cores_logical", 4)
    defaults["threads"] = _get_recommended_threads(cpu_cores, platform)

    memory_gb = getattr(system_profile, "memory_total_gb", 8.0)
    defaults["memory_limit"] = _get_recommended_memory_limit(memory_gb, platform)

    defaults["max_recommended_sf"] = _get_recommended_max_scale(memory_gb, benchmark)

    if platform in ["databricks", "bigquery", "snowflake", "redshift"]:
        defaults["tuning_mode"] = "tuned"
        defaults["enable_advanced_features"] = True
        defaults["enable_constraints"] = True
    else:
        defaults["tuning_mode"] = "balanced"
        defaults["enable_advanced_features"] = False
        defaults["enable_constraints"] = True

    if platform == "duckdb":
        defaults["memory_limit_str"] = f"{int(memory_gb * 0.7)}GB"
        defaults["enable_parallel_execution"] = cpu_cores >= 4
    elif platform == "databricks":
        defaults["enable_photon"] = True
        defaults["enable_adaptive_query_execution"] = True
        defaults["enable_z_ordering"] = True
        defaults["enable_auto_optimize"] = True
    elif platform == "snowflake":
        defaults["enable_clustering"] = True
        defaults["result_cache_enabled"] = True
    elif platform == "bigquery":
        defaults["enable_clustering"] = True
        defaults["enable_partitioning"] = True
    elif platform == "redshift":
        defaults["enable_distribution"] = True
        defaults["enable_sort_keys"] = True

    defaults["row_count_validation"] = "auto"
    defaults["validation_enabled"] = True

    return defaults


def _get_recommended_threads(cpu_cores: int, platform: str) -> int:
    platform_max_threads = {
        "duckdb": cpu_cores,
        "sqlite": 1,
        "clickhouse": min(cpu_cores, 16),
        "clickhouse-local": min(cpu_cores, 16),
        "clickhouse-server": min(cpu_cores, 16),
        "databricks": cpu_cores,
        "snowflake": cpu_cores,
        "bigquery": cpu_cores,
        "redshift": cpu_cores,
    }

    max_threads = platform_max_threads.get(platform, cpu_cores)

    if platform in {"duckdb", "sqlite", "clickhouse", "clickhouse-local", "clickhouse-server"}:
        return max(1, min(max_threads, cpu_cores - 1))

    return max_threads


def _get_recommended_memory_limit(memory_gb: float, platform: str) -> Optional[float]:
    if platform == "duckdb":
        return memory_gb * 0.7
    elif platform == "sqlite":
        return min(2.0, memory_gb * 0.3)
    elif platform in {"clickhouse", "clickhouse-local", "clickhouse-server"}:
        return memory_gb * 0.8

    return None


def _get_recommended_max_scale(memory_gb: float, benchmark: str = "tpch") -> float:
    benchmark_lower = benchmark.lower()

    if benchmark_lower == "tpcds":
        memory_gb = memory_gb / 8.0
    elif benchmark_lower == "ssb":
        memory_gb = memory_gb * 1.5

    if memory_gb >= 64:
        return 10.0
    elif memory_gb >= 32:
        return 5.0
    elif memory_gb >= 16:
        return 1.0
    elif memory_gb >= 8:
        return 0.1
    else:
        return 0.01


def _prompt_save_config(config: UnifiedTuningConfiguration, platform: str, benchmark: str) -> None:
    console.print()
    if not Confirm.ask("Would you like to save this configuration for future use?", default=True):
        return

    default_filename = f"{platform}_{benchmark}_tuned.yaml"

    from benchbox.utils.path_utils import resolve_benchmark_runs_dir

    benchmark_runs_dir = resolve_benchmark_runs_dir()
    benchmark_runs_dir.mkdir(parents=True, exist_ok=True)

    default_path = benchmark_runs_dir / default_filename

    save_path_str = Prompt.ask("Save configuration to", default=str(default_path))

    save_path = Path(save_path_str)

    save_path.parent.mkdir(parents=True, exist_ok=True)

    from benchbox.core.config_utils import save_config_file

    try:
        config_data = config.to_dict()

        from datetime import datetime

        config_data["_metadata"] = {
            "version": "2.0",
            "format": "unified_tuning",
            "created": datetime.now().isoformat(),
            "generated_by": "benchbox-cli-wizard",
            "platform": platform,
            "benchmark": benchmark,
        }

        save_config_file(config_data, save_path, "yaml")
        console.print(f"[green]✅ Tuning configuration saved to {save_path}[/green]")

        console.print("\n[dim]You can reuse this configuration with:[/dim]")
        console.print(f"[dim]  benchbox run --platform {platform} --benchmark {benchmark} --tuning {save_path}[/dim]")

    except Exception as e:
        console.print(f"[red]❌ Failed to save tuning configuration: {e}[/red]")
        console.print("[yellow]Configuration will still be used for this run.[/yellow]")


def run_tuning_wizard(
    benchmark: str,
    platform: str,
    system_profile: SystemProfile,
    interactive: bool = True,
) -> UnifiedTuningConfiguration:
    console.print()
    console.print(
        Panel.fit(
            Text("Tuning Configuration Wizard", style="bold cyan"),
            style="cyan",
        )
    )

    defaults = autofill_defaults(system_profile, platform, benchmark)

    config = UnifiedTuningConfiguration()

    if not interactive:
        return _apply_defaults_to_config(config, defaults, platform, benchmark)

    console.print("\n[bold cyan]Step 1: Tuning Mode[/bold cyan]")
    console.print("1. Simple (Recommended) - Smart defaults based on your system")
    console.print("2. Advanced - Full control over all optimization settings")
    console.print("3. Baseline - No optimizations (for performance comparison)")

    mode_choice = Prompt.ask("Select tuning mode", choices=["1", "2", "3"], default="1")

    if mode_choice == "3":
        config.disable_all_constraints()
        console.print("[green]✓ Baseline mode: All optimizations disabled[/green]")
        _prompt_save_config(config, platform, benchmark)
        return config

    elif mode_choice == "1":
        config = _run_simple_wizard(config, defaults, platform, benchmark, system_profile)
        _prompt_save_config(config, platform, benchmark)
        return config

    else:
        config = _run_advanced_wizard(config, defaults, platform, benchmark, system_profile)
        _prompt_save_config(config, platform, benchmark)
        return config


def _run_simple_wizard(
    config: UnifiedTuningConfiguration,
    defaults: dict[str, Any],
    platform: str,
    benchmark: str,
    system_profile: SystemProfile,
) -> UnifiedTuningConfiguration:
    console.print("\n[bold cyan]Step 2: Optimization Objective[/bold cyan]")
    console.print("1. Throughput - Maximize query throughput (parallel execution)")
    console.print("2. Latency - Minimize individual query latency")
    console.print("3. Balanced - Balance between throughput and latency (recommended)")

    objective_choice = Prompt.ask("Select objective", choices=["1", "2", "3"], default="3")

    objective_map = {"1": "throughput", "2": "latency", "3": "balanced"}
    objective = objective_map[objective_choice]

    if objective == "throughput":
        config.enable_all_constraints()
        if platform in ["databricks", "snowflake", "bigquery"]:
            console.print("[cyan]→ Enabling parallel execution optimizations[/cyan]")
    elif objective == "latency":
        config.enable_primary_keys()
        config.disable_foreign_keys()
        console.print("[cyan]→ Enabling latency-focused optimizations[/cyan]")
    else:
        config.enable_all_constraints()
        console.print("[cyan]→ Enabling balanced optimizations[/cyan]")

    if platform == "databricks" and defaults.get("enable_z_ordering"):
        if Confirm.ask("Enable Z-Ordering for improved query performance?", default=True):
            config.enable_platform_optimization(TuningType.Z_ORDERING)
            console.print("[green]✓ Z-Ordering enabled[/green]")

    elif platform == "snowflake" and defaults.get("enable_clustering"):
        if Confirm.ask("Enable clustering keys for improved query performance?", default=True):
            _apply_table_layout(config, TuningType.CLUSTERING, "Clustering", benchmark, platform)

    elif platform == "bigquery":
        if Confirm.ask("Enable partitioning and clustering?", default=True):
            _apply_table_layout(config, TuningType.PARTITIONING, "Partitioning", benchmark, platform)
            _apply_table_layout(config, TuningType.CLUSTERING, "Clustering", benchmark, platform)

    elif platform == "redshift":
        if Confirm.ask("Enable distribution and sort keys?", default=True):
            _apply_table_layout(config, TuningType.DISTRIBUTION, "Distribution keys", benchmark, platform)
            _apply_table_layout(config, TuningType.SORTING, "Sort keys", benchmark, platform)

    _show_simple_summary(config, defaults, platform)

    return config


def _run_advanced_wizard(
    config: UnifiedTuningConfiguration,
    defaults: dict[str, Any],
    platform: str,
    benchmark: str,
    system_profile: SystemProfile,
) -> UnifiedTuningConfiguration:
    console.print("\n[bold cyan]Step 2: Schema Constraints[/bold cyan]")
    console.print("Constraints can improve query performance but may slow data loading.")

    if Confirm.ask("Enable primary keys?", default=True):
        config.enable_primary_keys()
        console.print("[green]✓ Primary keys enabled[/green]")

    if Confirm.ask("Enable foreign keys?", default=True):
        config.enable_foreign_keys()
        console.print("[green]✓ Foreign keys enabled[/green]")

    if Confirm.ask("Enable unique constraints?", default=False):
        config.unique_constraints.enabled = True
        console.print("[green]✓ Unique constraints enabled[/green]")

    console.print("\n[bold cyan]Step 3: Platform-Specific Optimizations[/bold cyan]")

    if platform == "databricks":
        _configure_databricks_optimizations(config)
    elif platform == "snowflake":
        _configure_snowflake_optimizations(config, benchmark)
    elif platform == "bigquery":
        _configure_bigquery_optimizations(config, benchmark)
    elif platform == "redshift":
        _configure_redshift_optimizations(config, benchmark)
    elif platform == "duckdb":
        _configure_duckdb_optimizations(config, defaults, benchmark)
    elif platform in {"clickhouse", "clickhouse-local", "clickhouse-server"}:
        _configure_clickhouse_optimizations(config, benchmark)

    console.print("\n[bold cyan]Step 4: Data Validation[/bold cyan]")
    if Confirm.ask("Enable row count validation after load?", default=True):
        console.print("[green]✓ Row count validation will be enabled[/green]")

    render_tuning_summary(config, platform)

    return config


def _apply_table_layout(
    config: UnifiedTuningConfiguration,
    tuning_type: TuningType,
    label: str,
    benchmark: str,
    platform: str,
) -> bool:
    if config.enable_platform_optimization(tuning_type, benchmark=benchmark, platform=platform):
        console.print(f"[green]✓ {label} enabled[/green]")
        return True
    unapplied = UnifiedTuningConfiguration.layout_unapplied_reason(platform, tuning_type)
    if unapplied is not None:
        reason = f"{platform} does not apply {tuning_type.value} at execution: {unapplied}"
    elif tuning_type == TuningType.PARTITIONING:
        reason = f"no default partitioning profile exists for {benchmark}"
    else:
        reason = f"no {platform} {benchmark} template defines {tuning_type.value} for any table"
    console.print(f"[yellow]⚠ {label} not enabled: {reason}[/yellow]")
    return False


def _confirm_table_layout(
    config: UnifiedTuningConfiguration,
    prompt: str,
    tuning_type: TuningType,
    label: str,
    benchmark: str = "tpch",
    default: bool = True,
    platform: str = "duckdb",
) -> None:
    if Confirm.ask(prompt, default=default):
        _apply_table_layout(config, tuning_type, label, benchmark, platform)


def _configure_databricks_optimizations(config: UnifiedTuningConfiguration) -> None:
    if Confirm.ask("Enable Z-Ordering?", default=True):
        config.enable_platform_optimization(TuningType.Z_ORDERING)
        console.print("[green]✓ Z-Ordering enabled[/green]")

    if Confirm.ask("Enable Auto Optimize?", default=True):
        config.enable_platform_optimization(TuningType.AUTO_OPTIMIZE)
        console.print("[green]✓ Auto Optimize enabled[/green]")

    if Confirm.ask("Enable Auto Compact?", default=False):
        config.enable_platform_optimization(TuningType.AUTO_COMPACT)
        console.print("[green]✓ Auto Compact enabled[/green]")


def _configure_snowflake_optimizations(config: UnifiedTuningConfiguration, benchmark: str = "tpch") -> None:
    _confirm_table_layout(
        config, "Enable clustering keys?", TuningType.CLUSTERING, "Clustering keys", benchmark, platform="snowflake"
    )


def _configure_bigquery_optimizations(config: UnifiedTuningConfiguration, benchmark: str = "tpch") -> None:
    _confirm_table_layout(
        config, "Enable table partitioning?", TuningType.PARTITIONING, "Partitioning", benchmark, platform="bigquery"
    )
    _confirm_table_layout(
        config, "Enable clustering?", TuningType.CLUSTERING, "Clustering", benchmark, platform="bigquery"
    )


def _configure_redshift_optimizations(config: UnifiedTuningConfiguration, benchmark: str = "tpch") -> None:
    _confirm_table_layout(
        config,
        "Enable distribution keys?",
        TuningType.DISTRIBUTION,
        "Distribution keys",
        benchmark,
        platform="redshift",
    )
    _confirm_table_layout(config, "Enable sort keys?", TuningType.SORTING, "Sort keys", benchmark, platform="redshift")


def _configure_duckdb_optimizations(
    config: UnifiedTuningConfiguration, defaults: dict[str, Any], benchmark: str = "tpch"
) -> None:
    memory_limit = defaults.get("memory_limit_str", "4GB")
    threads = defaults.get("threads", 4)

    console.print(f"[dim]System recommendations: {threads} threads, {memory_limit} memory limit[/dim]")
    console.print("[dim]Note: Runtime settings (threads, memory) are configured automatically[/dim]\n")

    if Confirm.ask("Enable partitioning for large tables?", default=False):
        if _apply_table_layout(config, TuningType.PARTITIONING, "Partitioning", benchmark, "duckdb"):
            console.print("[dim]  Tables will be partitioned by appropriate columns (e.g., date)[/dim]")

    if Confirm.ask("Enable sorting (ORDER BY) for query optimization?", default=True):
        if _apply_table_layout(config, TuningType.SORTING, "Sorting", benchmark, "duckdb"):
            console.print("[dim]  Tables will be sorted by frequently queried columns[/dim]")


def _configure_clickhouse_optimizations(config: UnifiedTuningConfiguration, benchmark: str = "tpch") -> None:
    _confirm_table_layout(
        config, "Enable partitioning?", TuningType.PARTITIONING, "Partitioning", benchmark, platform="clickhouse"
    )
    _confirm_table_layout(
        config, "Enable sorting (ORDER BY)?", TuningType.SORTING, "Sorting", benchmark, platform="clickhouse"
    )


def _apply_defaults_to_config(
    config: UnifiedTuningConfiguration,
    defaults: dict[str, Any],
    platform: str,
    benchmark: str = "tpch",
) -> UnifiedTuningConfiguration:
    config.enable_all_constraints()

    if platform == "databricks":
        config.enable_platform_optimization(TuningType.Z_ORDERING)
        config.enable_platform_optimization(TuningType.AUTO_OPTIMIZE)
    elif platform == "snowflake":
        config.enable_platform_optimization(TuningType.CLUSTERING, benchmark=benchmark, platform=platform)
    elif platform == "bigquery":
        config.enable_platform_optimization(TuningType.PARTITIONING, benchmark=benchmark, platform=platform)
        config.enable_platform_optimization(TuningType.CLUSTERING, benchmark=benchmark, platform=platform)
    elif platform == "redshift":
        config.enable_platform_optimization(TuningType.DISTRIBUTION, benchmark=benchmark, platform=platform)
        config.enable_platform_optimization(TuningType.SORTING, benchmark=benchmark, platform=platform)

    return config


def _show_simple_summary(
    config: UnifiedTuningConfiguration,
    defaults: dict[str, Any],
    platform: str,
) -> None:
    console.print("\n[bold green]Configuration Summary[/bold green]")

    summary = Table(show_header=False, box=None)
    summary.add_column("Setting", style="cyan", min_width=20)
    summary.add_column("Value", style="white")

    constraint_status = []
    if config.primary_keys.enabled:
        constraint_status.append("PK")
    if config.foreign_keys.enabled:
        constraint_status.append("FK")
    if config.unique_constraints.enabled:
        constraint_status.append("UNIQUE")
    if config.check_constraints.enabled:
        constraint_status.append("CHECK")

    if constraint_status:
        summary.add_row("Constraints:", ", ".join(constraint_status))
    else:
        summary.add_row("Constraints:", "Disabled")

    enabled_opts = config.get_enabled_tuning_types()
    platform_opts = [
        opt
        for opt in enabled_opts
        if opt
        in [
            TuningType.Z_ORDERING,
            TuningType.AUTO_OPTIMIZE,
            TuningType.AUTO_COMPACT,
            TuningType.CLUSTERING,
            TuningType.PARTITIONING,
            TuningType.DISTRIBUTION,
            TuningType.SORTING,
            TuningType.BLOOM_FILTERS,
            TuningType.MATERIALIZED_VIEWS,
        ]
    ]

    if platform_opts:
        opt_names = [opt.value.replace("_", " ").title() for opt in platform_opts]
        summary.add_row("Platform Options:", ", ".join(opt_names))

    console.print(summary)
    console.print()


def render_tuning_summary(config: UnifiedTuningConfiguration, platform: str) -> Table:
    console.print("\n[bold cyan]Detailed Configuration Summary[/bold cyan]")

    table = Table(title=f"Tuning Configuration for {platform.upper()}", show_header=True)
    table.add_column("Category", style="cyan bold", width=20)
    table.add_column("Setting", style="green", width=25)
    table.add_column("Status", style="white", width=15)

    table.add_row("Schema Constraints", "Primary Keys", "✓ Enabled" if config.primary_keys.enabled else "✗ Disabled")
    table.add_row("", "Foreign Keys", "✓ Enabled" if config.foreign_keys.enabled else "✗ Disabled")
    table.add_row("", "Unique Constraints", "✓ Enabled" if config.unique_constraints.enabled else "✗ Disabled")
    table.add_row("", "Check Constraints", "✓ Enabled" if config.check_constraints.enabled else "✗ Disabled")

    enabled_types = config.get_enabled_tuning_types()

    platform_specific = [
        (TuningType.Z_ORDERING, "Z-Ordering"),
        (TuningType.AUTO_OPTIMIZE, "Auto Optimize"),
        (TuningType.AUTO_COMPACT, "Auto Compact"),
        (TuningType.CLUSTERING, "Clustering"),
        (TuningType.PARTITIONING, "Partitioning"),
        (TuningType.DISTRIBUTION, "Distribution Keys"),
        (TuningType.SORTING, "Sort Keys"),
        (TuningType.BLOOM_FILTERS, "Bloom Filters"),
        (TuningType.MATERIALIZED_VIEWS, "Materialized Views"),
    ]

    platform_optimizations = [
        (label, tuning_type in enabled_types)
        for tuning_type, label in platform_specific
        if tuning_type.is_compatible_with_platform(platform)
    ]

    if platform_optimizations:
        for i, (label, enabled) in enumerate(platform_optimizations):
            category = "Platform Features" if i == 0 else ""
            status = "✓ Enabled" if enabled else "- Available"
            table.add_row(category, label, status)

    console.print(table)
    console.print()

    return table


def run_dataframe_write_wizard(
    platform: str,
    benchmark: str = "tpch",
    interactive: bool = True,
) -> Optional[Any]:
    from benchbox.core.dataframe.tuning import (
        DataFrameWriteConfiguration,
        PartitionColumn,
        PartitionStrategy,
        SortColumn,
        get_platform_write_capabilities,
    )

    platform_lower = platform.lower().replace("-df", "")
    caps = get_platform_write_capabilities(platform_lower)

    if not interactive:
        return None

    console.print("\n[bold cyan]DataFrame Write Layout Configuration[/bold cyan]")
    console.print("Configure how data is physically organized when written to Parquet files.")
    console.print("[dim]This affects query performance, compression, and parallel processing.[/dim]\n")

    console.print(f"[bold]Platform capabilities for {platform_lower}:[/bold]")
    cap_table = Table(show_header=False, box=None, padding=(0, 2))
    cap_table.add_column("Feature", style="cyan")
    cap_table.add_column("Supported", style="white")

    cap_table.add_row("Sorted writes", "✓ Yes" if caps.get("sort_by") else "✗ No")
    cap_table.add_row("Partitioned writes", "✓ Yes" if caps.get("partition_by") else "✗ No")
    cap_table.add_row("Repartitioning", "✓ Yes" if caps.get("repartition_count") else "✗ No")
    cap_table.add_row("Row group size control", "✓ Yes" if caps.get("row_group_size") else "✗ No")
    console.print(cap_table)
    console.print()

    if not Confirm.ask("Would you like to configure write layout options?", default=False):
        return None

    sort_by = _wizard_sorting_step(caps, benchmark, platform_lower, SortColumn)
    partition_by = _wizard_partitioning_step(caps, platform_lower, PartitionColumn, PartitionStrategy)
    row_group_size = _wizard_row_group_step(caps)
    repartition_count = _wizard_repartition_step(caps)
    compression_level = _wizard_compression_step()

    if sort_by or partition_by or row_group_size or repartition_count or compression_level:
        config = DataFrameWriteConfiguration(
            sort_by=sort_by,
            partition_by=partition_by,
            row_group_size=row_group_size,
            repartition_count=repartition_count,
            compression_level=compression_level,
        )

        _show_dataframe_write_summary(config, platform_lower)

        return config

    return None


def _wizard_sorting_step(caps: dict, benchmark: str, platform_lower: str, SortColumn: type) -> list:
    sort_by: list = []
    if caps.get("sort_by"):
        console.print("\n[bold cyan]Step 1: Sorting[/bold cyan]")
        console.print("Sorted data improves compression and enables skip-scanning.")
        presort_configs = get_presort_table_configs(benchmark)
        if presort_configs:
            hint_parts: list[str] = []
            for table, cols in presort_configs.items():
                col_names = ", ".join(
                    c.get("name") if isinstance(c, dict) else getattr(c, "name", str(c)) for c in cols
                )
                hint_parts.append(f"{col_names} (for {table})")
            hint = ", ".join(hint_parts)
            console.print(f"[dim]Recommended for {benchmark.upper()}: {hint}[/dim]")
            first_table = next(iter(presort_configs))
            first_cols = presort_configs[first_table]
            default_sort = ""
            if first_cols:
                first = first_cols[0]
                default_sort = first.get("name") if isinstance(first, dict) else getattr(first, "name", "")
        else:
            console.print(f"[dim]Recommended for {benchmark.upper()}: sort by date column if applicable[/dim]")
            default_sort = ""

        if Confirm.ask("Enable sorting?", default=True):
            sort_cols_str = Prompt.ask(
                "Enter column names to sort by (comma-separated)",
                default=default_sort,
            )
            if sort_cols_str:
                for col_name in sort_cols_str.split(","):
                    col_name = col_name.strip()
                    if col_name:
                        order = Prompt.ask(f"Sort order for '{col_name}'", choices=["asc", "desc"], default="asc")
                        sort_by.append(SortColumn(name=col_name, order=order))
                        console.print(f"[green]✓ Added sort column: {col_name} ({order})[/green]")
    else:
        console.print(f"\n[dim]Sorting not supported by {platform_lower} - skipping[/dim]")
    return sort_by


def _wizard_partitioning_step(caps: dict, platform_lower: str, PartitionColumn: type, PartitionStrategy: type) -> list:
    partition_by: list = []
    if caps.get("partition_by"):
        console.print("\n[bold cyan]Step 2: Partitioning[/bold cyan]")
        console.print("Hive-style partitioning creates directory structure for partition pruning.")
        console.print("[dim]Best for: large datasets with date-based filtering[/dim]")

        if Confirm.ask("Enable partitioning?", default=False):
            part_cols_str = Prompt.ask("Enter partition column names (comma-separated)", default="")
            if part_cols_str:
                for col_name in part_cols_str.split(","):
                    col_name = col_name.strip()
                    if col_name:
                        strategy_choice = Prompt.ask(
                            f"Partition strategy for '{col_name}'",
                            choices=["value", "date_year", "date_month", "date_day"],
                            default="value",
                        )
                        strategy = PartitionStrategy(strategy_choice)
                        partition_by.append(PartitionColumn(name=col_name, strategy=strategy))
                        console.print(f"[green]✓ Added partition column: {col_name} ({strategy_choice})[/green]")
    else:
        console.print(f"\n[dim]Partitioning not supported by {platform_lower} - skipping[/dim]")
    return partition_by


def _wizard_row_group_step(caps: dict) -> int | None:
    if caps.get("row_group_size"):
        console.print("\n[bold cyan]Step 3: Row Group Size[/bold cyan]")
        console.print("Row groups affect read parallelism and compression. Default: ~128MB worth of rows.")
        console.print("[dim]Larger groups = better compression, smaller groups = more parallel reads[/dim]")

        if Confirm.ask("Customize row group size?", default=False):
            row_group_size = IntPrompt.ask("Rows per group", default=1000000)
            console.print(f"[green]✓ Row group size: {row_group_size:,} rows[/green]")
            return row_group_size
    return None


def _wizard_repartition_step(caps: dict) -> int | None:
    if caps.get("repartition_count"):
        console.print("\n[bold cyan]Step 4: Output File Count[/bold cyan]")
        console.print("Control the number of output files for parallel processing.")
        console.print("[dim]More files = more parallelism, fewer files = less overhead[/dim]")

        if Confirm.ask("Specify output file count?", default=False):
            repartition_count = IntPrompt.ask("Number of output files", default=8)
            console.print(f"[green]✓ Output file count: {repartition_count}[/green]")
            return repartition_count
    return None


def _wizard_compression_step() -> int | None:
    console.print("\n[bold cyan]Step 5: Compression Level[/bold cyan]")
    console.print("Higher levels = better compression but slower writes. Default: platform default.")

    if Confirm.ask("Customize compression level?", default=False):
        compression_level = IntPrompt.ask("Compression level (1-22 for zstd)", default=3)
        console.print(f"[green]✓ Compression level: {compression_level}[/green]")
        return compression_level
    return None


def _show_dataframe_write_summary(config: Any, platform: str) -> None:
    console.print("\n[bold green]DataFrame Write Configuration Summary[/bold green]")

    summary = Table(show_header=False, box=None)
    summary.add_column("Setting", style="cyan", min_width=20)
    summary.add_column("Value", style="white")

    if config.sort_by:
        sort_cols = [f"{col.name} ({col.order})" for col in config.sort_by]
        summary.add_row("Sort By:", ", ".join(sort_cols))

    if config.partition_by:
        part_cols = [f"{col.name} ({col.strategy.value})" for col in config.partition_by]
        summary.add_row("Partition By:", ", ".join(part_cols))

    if config.row_group_size:
        summary.add_row("Row Group Size:", f"{config.row_group_size:,}")

    if config.repartition_count:
        summary.add_row("Output Files:", str(config.repartition_count))

    if config.compression_level:
        summary.add_row("Compression Level:", str(config.compression_level))

    console.print(summary)
    console.print()


__all__ = [
    "run_tuning_wizard",
    "run_dataframe_write_wizard",
    "autofill_defaults",
    "render_tuning_summary",
]
