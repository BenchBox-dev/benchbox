# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import math
import re
import statistics
import sys
import warnings
from html import escape as html_escape
from pathlib import Path
from typing import Any

import click
from rich.markup import escape
from rich.prompt import Confirm, Prompt
from rich.table import Table

from benchbox.cli.shared import console
from benchbox.core.results.exporter import ResultExporter
from benchbox.core.results.loader import (
    ResultLoadError,
    UnsupportedSchemaError,
    iter_query_results,
    load_result_file,
)
from benchbox.core.results.regression_policy import is_regression
from benchbox.core.results.schema_policy import is_loader_supported_result_schema
from benchbox.validation.bundle import COMPANION_SUFFIXES


def _escape_html(value: Any) -> str:
    return html_escape(str(value), quote=True)


class ResultFileMetadata:
    def __init__(
        self,
        path: Path,
        benchmark: str,
        benchmark_id: str,
        platform: str,
        scale: float,
        timestamp: str,
        execution_id: str,
    ):
        self.path = path
        self.benchmark = benchmark
        self.benchmark_id = benchmark_id
        self.platform = platform
        self.scale = scale
        self.timestamp = timestamp
        self.execution_id = execution_id

    @property
    def formatted_timestamp(self) -> str:
        try:
            from datetime import datetime

            dt = datetime.fromisoformat(self.timestamp)
            return dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            return self.timestamp[:16] if self.timestamp else "Unknown"

    @property
    def short_path(self) -> str:
        parts = self.path.parts
        if len(parts) > 3:
            return str(Path(*parts[-3:]))
        return str(self.path)


def _discover_result_files_with_metadata(
    search_dirs: list[Path] | None = None,
    max_files: int = 100,
) -> list[ResultFileMetadata]:
    if search_dirs is None:
        search_dirs = [
            Path("benchmark_runs/results"),
            Path("benchmark_runs"),
            Path("results"),
            Path("."),
        ]

    discovered: list[ResultFileMetadata] = []
    seen_paths: set[Path] = set()

    for search_dir in search_dirs:
        if not search_dir.exists() or not search_dir.is_dir():
            continue

        json_files = sorted(
            (f for f in search_dir.glob("**/*.json") if not f.name.endswith(COMPANION_SUFFIXES)),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        for filepath in json_files[:max_files]:
            resolved = filepath.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)

            try:
                with open(filepath, encoding="utf-8") as f:
                    data = json.load(f)

                if not is_loader_supported_result_schema(data):
                    continue

                if "benchmark" not in data:
                    continue

                benchmark_section = data.get("benchmark", {})
                run_section = data.get("run", {})
                platform_section = data.get("platform", {})

                metadata = ResultFileMetadata(
                    path=filepath,
                    benchmark=benchmark_section.get("name", "Unknown"),
                    benchmark_id=benchmark_section.get("id", ""),
                    platform=platform_section.get("name", "Unknown"),
                    scale=benchmark_section.get("scale_factor", 1.0),
                    timestamp=run_section.get("timestamp", ""),
                    execution_id=run_section.get("id", ""),
                )
                discovered.append(metadata)

            except (json.JSONDecodeError, OSError, KeyError):
                continue

    def parse_timestamp(meta: ResultFileMetadata) -> str:
        return meta.timestamp or ""

    discovered.sort(key=parse_timestamp, reverse=True)

    return discovered


@click.command(
    "compare",
    help=(
        "Compare benchmark results, query plans, or run cross-platform benchmarks.\n"
        "\n"
        "Mode is automatically detected:\n"
        "\n"
        "\b\n"
        "RUN MODE (with -p/--platform):\n"
        "  Run benchmarks across multiple platforms and compare.\n"
        "  benchbox compare -p duckdb -p sqlite\n"
        "\n"
        "\b\n"
        "FILE MODE (with file paths):\n"
        "  Compare existing benchmark result files.\n"
        "  benchbox compare baseline.json current.json\n"
        "\n"
        "\b\n"
        "INTERACTIVE MODE (no arguments):\n"
        "  Launch interactive wizard to guide you through comparison.\n"
        "  benchbox compare\n"
        "\n"
        "\b\n"
        "Examples:\n"
        "    # Run SQL platform comparison\n"
        "    benchbox compare -p duckdb -p sqlite -p clickhouse\n"
        "\n"
        "\b\n"
        "    # Run DataFrame platform comparison\n"
        "    benchbox compare -p polars-df -p pandas-df --scale 0.1\n"
        "\n"
        "\b\n"
        "    # Compare two result files\n"
        "    benchbox compare baseline.json current.json\n"
        "\n"
        "\b\n"
        "    # Compare with regression threshold (CI/CD)\n"
        "    benchbox compare baseline.json current.json --fail-on-regression 10%\n"
        "\n"
        "\b\n"
        "    # Ignore query slowdowns of 5 ms or less (CI/CD on small scale factors)\n"
        "    benchbox compare baseline.json current.json --fail-on-regression 10% --min-regression-delta 5ms\n"
        "\n"
        "\b\n"
        "    # Ignore aggregate slowdowns of 80 ms or less (noisy hosted runners)\n"
        "    benchbox compare baseline.json current.json --fail-on-regression 10% --min-aggregate-regression-delta 80ms\n"
        "\b\n"
        "    # Compare files with query plan analysis\n"
        "    benchbox compare baseline.json current.json --include-plans\n"
        "\n"
        "\b\n"
        "    # Show only significant plan changes (< 90% similar)\n"
        "    benchbox compare baseline.json current.json --include-plans --plan-threshold 0.9\n"
        "\n"
        "\b\n"
        "    # List available platforms\n"
        "    benchbox compare --list-platforms\n"
        "\n"
        "\b\n"
        "    # Generate charts with results\n"
        "    benchbox compare -p duckdb -p sqlite -o ./comparison --generate-charts\n"
        "\n"
        "\b\n"
        "    # Non-interactive mode (CI/CD)\n"
        "    benchbox compare --non-interactive -p duckdb -p sqlite"
    ),
)
@click.argument("result_files", nargs=-1, type=click.Path(exists=True))
@click.option(
    "-p",
    "--platform",
    "platforms",
    multiple=True,
    help="Platforms to compare (triggers run mode). Repeatable: -p duckdb -p sqlite",
)
@click.option(
    "--run",
    "run_mode_flag",
    is_flag=True,
    hidden=True,
    help="[DEPRECATED] Run mode is now inferred from -p/--platform",
)
@click.option(
    "--type",
    "platform_type",
    type=click.Choice(["sql", "dataframe", "auto"]),
    default="auto",
    help="Platform type: sql, dataframe, or auto-detect (default: auto)",
)
@click.option(
    "-b",
    "--benchmark",
    default="tpch",
    show_default=True,
    type=click.Choice(["tpch", "tpcds", "ssb", "clickbench"]),
    help="Benchmark to run (run mode)",
)
@click.option(
    "-s",
    "--scale",
    default=0.01,
    show_default=True,
    type=float,
    help="Scale factor (run mode)",
)
@click.option(
    "-q",
    "--queries",
    default=None,
    help="Comma-separated query IDs, e.g., Q1,Q3,Q6 (run mode)",
)
@click.option(
    "--warmup",
    default=1,
    show_default=True,
    type=int,
    help="Warmup iterations (run mode)",
)
@click.option(
    "--iterations",
    default=3,
    show_default=True,
    type=int,
    help="Benchmark iterations (run mode)",
)
@click.option(
    "--data-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Data directory (run mode, for embedded platforms)",
)
@click.option(
    "--list-platforms",
    is_flag=True,
    help="List available platforms and exit",
)
@click.option(
    "--fail-on-regression",
    type=str,
    help='Fail (exit 1) if regression exceeds threshold (e.g., "10%", "0.1", "5%")',
)
@click.option(
    "--min-regression-delta",
    type=str,
    help=(
        "With --fail-on-regression, count a query as regressed only when it also slows by more than this "
        'duration (e.g., "5ms", "0.005s"). Applies to per-query comparisons only.'
    ),
)
@click.option(
    "--min-aggregate-regression-delta",
    type=str,
    help=(
        "With --fail-on-regression, count an aggregate metric as regressed only when it also slows by "
        'more than this duration (e.g., "80ms", "0.08s"). Applies to aggregate metrics only.'
    ),
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json", "markdown", "html"], case_sensitive=False),
    default="text",
    help="Output format (default: text)",
)
@click.option(
    "-o",
    "--output",
    "output_file",
    type=click.Path(),
    help="Save output to file or directory",
)
@click.option(
    "--generate-charts",
    is_flag=True,
    help="Generate ASCII charts in output directory (run mode)",
)
@click.option(
    "--theme",
    type=click.Choice(["light", "dark"]),
    default="light",
    help="Chart theme (with --generate-charts)",
)
@click.option(
    "--show-all-queries",
    is_flag=True,
    help="Show all query comparisons (file mode only)",
)
@click.option(
    "--non-interactive",
    is_flag=True,
    help="Disable interactive prompts (for CI/CD)",
)
@click.option(
    "--include-plans",
    is_flag=True,
    help="Include query plan comparison in file mode (requires captured plans)",
)
@click.option(
    "--plan-threshold",
    type=float,
    default=0.0,
    help="Only show plan changes with similarity below threshold (0.0-1.0)",
)
@click.option(
    "--allow-heterogeneous-comparison",
    is_flag=True,
    help="Allow comparing runs with different execution variants (deployment, engine, gateway)",
)
@click.pass_context
def compare(
    ctx,
    result_files,
    platforms,
    run_mode_flag,
    platform_type,
    benchmark,
    scale,
    queries,
    warmup,
    iterations,
    data_dir,
    list_platforms,
    fail_on_regression,
    min_regression_delta,
    min_aggregate_regression_delta,
    output_format,
    output_file,
    generate_charts,
    theme,
    show_all_queries,
    non_interactive,
    include_plans,
    plan_threshold,
    allow_heterogeneous_comparison: bool = False,
):
    if list_platforms:
        _list_available_platforms()
        return

    if min_regression_delta is not None and not fail_on_regression:
        raise click.UsageError("--min-regression-delta requires --fail-on-regression")

    if min_aggregate_regression_delta is not None and not fail_on_regression:
        raise click.UsageError("--min-aggregate-regression-delta requires --fail-on-regression")

    if min_regression_delta is not None and platforms:
        raise click.UsageError("--min-regression-delta applies to result file comparison, not platform runs")

    if min_aggregate_regression_delta is not None and platforms:
        raise click.UsageError("--min-aggregate-regression-delta applies to result file comparison, not platform runs")

    if run_mode_flag:
        warnings.warn(
            "The --run flag is deprecated and can be omitted. "
            "Run mode is now inferred from -p/--platform options.\n"
            "Use: benchbox compare -p duckdb -p sqlite",
            DeprecationWarning,
            stacklevel=2,
        )
        console.print(
            "[yellow]Note: The --run flag is deprecated. Use: benchbox compare -p duckdb -p sqlite[/yellow]\n"
        )

    has_platforms = bool(platforms)
    has_files = bool(result_files)

    if has_platforms and has_files:
        console.print("[red]Error: Cannot specify both platforms (-p) and result files[/red]")
        console.print("\nChoose one:")
        console.print("  benchbox compare -p duckdb -p sqlite           # Run and compare platforms")
        console.print("  benchbox compare result1.json result2.json     # Compare existing files")
        sys.exit(1)

    if has_platforms:
        _run_platform_comparison(
            platforms=list(platforms),
            platform_type=platform_type,
            benchmark=benchmark,
            scale=scale,
            queries=queries,
            warmup=warmup,
            iterations=iterations,
            data_dir=data_dir,
            output_format=output_format,
            output_file=output_file,
            generate_charts=generate_charts,
            theme=theme,
        )
    elif has_files:
        _run_file_comparison(
            result_files=result_files,
            fail_on_regression=fail_on_regression,
            min_regression_delta=min_regression_delta,
            min_aggregate_regression_delta=min_aggregate_regression_delta,
            output_format=output_format,
            output_file=output_file,
            show_all_queries=show_all_queries,
            include_plans=include_plans,
            plan_threshold=plan_threshold,
            allow_heterogeneous_comparison=allow_heterogeneous_comparison,
        )
    else:
        if non_interactive:
            console.print("[red]Error: No platforms or files specified[/red]")
            console.print("\n[bold]Usage:[/bold]")
            console.print("  benchbox compare -p duckdb -p sqlite           # Compare platforms")
            console.print("  benchbox compare result1.json result2.json     # Compare files")
            console.print("\n[dim]Use --list-platforms to see available platforms[/dim]")
            sys.exit(1)

        if not sys.stdin.isatty() or not sys.stdout.isatty():
            console.print("[red]Error: Interactive mode requires a terminal (TTY)[/red]")
            console.print("\n[bold]Provide arguments for non-interactive usage:[/bold]")
            console.print("  benchbox compare -p duckdb -p sqlite           # Compare platforms")
            console.print("  benchbox compare result1.json result2.json     # Compare files")
            console.print("\n[dim]Or use --non-interactive flag with required options[/dim]")
            sys.exit(1)

        _run_interactive_wizard(
            output_format=output_format,
            output_file=output_file,
            generate_charts=generate_charts,
            theme=theme,
            fail_on_regression=fail_on_regression,
            show_all_queries=show_all_queries,
            min_regression_delta=min_regression_delta,
            min_aggregate_regression_delta=min_aggregate_regression_delta,
        )


def _run_interactive_wizard(
    output_format: str,
    output_file: str | None,
    generate_charts: bool,
    theme: str,
    fail_on_regression: str | None,
    show_all_queries: bool,
    min_regression_delta: str | None = None,
    min_aggregate_regression_delta: str | None = None,
):
    console.print("\n[bold blue]BenchBox Platform Comparison[/bold blue]")
    console.print("Compare benchmark performance across different platforms.\n")

    console.print("[bold]Step 1:[/bold] What would you like to do?\n")
    console.print("  [cyan]1[/cyan]  Compare platforms (run benchmarks)")
    console.print("  [cyan]2[/cyan]  Compare result files\n")

    mode_choice = Prompt.ask(
        "Select option",
        choices=["1", "2"],
        default="1",
    )

    if mode_choice == "1":
        if min_regression_delta is not None:
            raise click.UsageError("--min-regression-delta applies to result file comparison, not platform runs")
        if min_aggregate_regression_delta is not None:
            raise click.UsageError(
                "--min-aggregate-regression-delta applies to result file comparison, not platform runs"
            )
        _interactive_platform_comparison(
            output_format=output_format,
            output_file=output_file,
            generate_charts=generate_charts,
            theme=theme,
        )
    else:
        _interactive_file_comparison(
            output_format=output_format,
            output_file=output_file,
            fail_on_regression=fail_on_regression,
            show_all_queries=show_all_queries,
            min_regression_delta=min_regression_delta,
            min_aggregate_regression_delta=min_aggregate_regression_delta,
        )


def _interactive_platform_comparison(
    output_format: str,
    output_file: str | None,
    generate_charts: bool,
    theme: str,
):
    from benchbox.platforms import (
        list_available_dataframe_platforms,
        list_available_platforms,
    )

    console.print("\n[bold]Step 2:[/bold] Select platforms to compare\n")

    sql_platforms = list_available_platforms()
    df_platforms_dict = list_available_dataframe_platforms()
    df_platforms = [name for name, available in df_platforms_dict.items() if available]

    table = Table(title="Available Platforms", show_header=True)
    table.add_column("ID", style="cyan bold", width=4, justify="right")
    table.add_column("Platform", style="green", width=20)
    table.add_column("Type", style="blue", width=12)

    all_platforms = []

    for i, platform in enumerate(sorted(sql_platforms), start=1):
        table.add_row(str(i), platform, "SQL")
        all_platforms.append((platform, "sql"))

    for i, platform in enumerate(sorted(df_platforms), start=len(sql_platforms) + 1):
        table.add_row(str(i), platform, "DataFrame")
        all_platforms.append((platform, "dataframe"))

    console.print(table)

    if not all_platforms:
        console.print("[red]No platforms available![/red]")
        console.print("\nInstall platform dependencies:")
        console.print("  pip install benchbox[duckdb]")
        console.print("  pip install benchbox[dataframe]")
        sys.exit(1)

    console.print("\n[dim]Enter platform IDs separated by commas (minimum 2)[/dim]")
    default_selection = "1,2" if len(all_platforms) >= 2 else "1"

    while True:
        selection = Prompt.ask(
            "Select platforms",
            default=default_selection,
        )

        try:
            selected_ids = [int(x.strip()) for x in selection.split(",")]
            if len(selected_ids) < 2:
                console.print("[yellow]Please select at least 2 platforms[/yellow]")
                continue

            if any(id < 1 or id > len(all_platforms) for id in selected_ids):
                console.print(f"[yellow]Invalid ID. Choose from 1-{len(all_platforms)}[/yellow]")
                continue

            break
        except ValueError:
            console.print("[yellow]Enter numbers separated by commas (e.g., 1,2,3)[/yellow]")

    selected_platforms = [all_platforms[id - 1][0] for id in selected_ids]
    console.print(f"\n[green]✓ Selected:[/green] {', '.join(selected_platforms)}")

    console.print("\n[bold]Step 3:[/bold] Configure benchmark\n")

    benchmark = Prompt.ask(
        "Benchmark",
        choices=["tpch", "tpcds", "ssb", "clickbench"],
        default="tpch",
    )

    scale = float(
        Prompt.ask(
            "Scale factor",
            default="0.01",
        )
    )

    iterations = int(
        Prompt.ask(
            "Iterations",
            default="3",
        )
    )

    console.print("\n[bold]Step 4:[/bold] Review configuration\n")
    console.print(f"  Platforms:  {', '.join(selected_platforms)}")
    console.print(f"  Benchmark:  {benchmark}")
    console.print(f"  Scale:      {scale}")
    console.print(f"  Iterations: {iterations}")

    if not Confirm.ask("\nProceed with comparison?", default=True):
        console.print("[yellow]Comparison cancelled[/yellow]")
        return

    _run_platform_comparison(
        platforms=selected_platforms,
        platform_type="auto",
        benchmark=benchmark,
        scale=scale,
        queries=None,
        warmup=1,
        iterations=iterations,
        data_dir=None,
        output_format=output_format,
        output_file=output_file,
        generate_charts=generate_charts,
        theme=theme,
    )


def _interactive_file_comparison(
    output_format: str,
    output_file: str | None,
    fail_on_regression: str | None,
    show_all_queries: bool,
    min_regression_delta: str | None = None,
    min_aggregate_regression_delta: str | None = None,
):
    console.print("\n[bold]Step 2:[/bold] How do you want to select files?\n")
    console.print("  [cyan]1[/cyan]  Browse by benchmark/platform/scale (recommended)")
    console.print("  [cyan]2[/cyan]  Enter file paths directly\n")

    selection_mode = Prompt.ask("Select option", choices=["1", "2"], default="1")

    result = _guided_file_selection() if selection_mode == "1" else _direct_file_selection()

    if result is None:
        return

    baseline_path, current_path = result

    console.print(f"\n[green]✓ Baseline:[/green] {baseline_path}")
    console.print(f"[green]✓ Current:[/green]  {current_path}")

    console.print("\n[bold]Step 3:[/bold] Options\n")
    include_plans = Confirm.ask(
        "Include query plan analysis? (requires captured plans)",
        default=False,
    )

    if not Confirm.ask("\nProceed with comparison?", default=True):
        console.print("[yellow]Comparison cancelled[/yellow]")
        return

    _run_file_comparison(
        result_files=(str(baseline_path), str(current_path)),
        fail_on_regression=fail_on_regression,
        output_format=output_format,
        output_file=output_file,
        show_all_queries=show_all_queries,
        include_plans=include_plans,
        plan_threshold=0.0,
        min_regression_delta=min_regression_delta,
        min_aggregate_regression_delta=min_aggregate_regression_delta,
    )


def _guided_file_selection() -> tuple[Path, Path] | None:
    console.print("\n[bold]Discovering result files...[/bold]")
    all_results = _discover_result_files_with_metadata()

    if not all_results:
        console.print("[yellow]No benchmark result files found.[/yellow]")
        console.print("[dim]Run benchmarks first or enter file paths directly.[/dim]")
        return None

    console.print(f"[dim]Found {len(all_results)} result files[/dim]\n")

    selected_benchmark = _select_from_options(
        items=sorted({r.benchmark for r in all_results}),
        label="benchmark",
        count_fn=lambda bench: sum(1 for r in all_results if r.benchmark == bench),
        format_fn=lambda bench: bench,
    )

    filtered = [r for r in all_results if r.benchmark == selected_benchmark]

    selected_platform = _select_from_options(
        items=sorted({r.platform for r in filtered}),
        label="platform",
        count_fn=lambda plat: sum(1 for r in filtered if r.platform == plat),
        format_fn=lambda plat: plat,
        header=f"Select platform for {selected_benchmark}:",
    )

    filtered = [r for r in filtered if r.platform == selected_platform]

    selected_scale = _select_from_options(
        items=sorted({r.scale for r in filtered}),
        label="scale factor",
        count_fn=lambda sc: sum(1 for r in filtered if r.scale == sc),
        format_fn=lambda sc: f"SF {sc}",
        header="Select scale factor:",
    )

    filtered = [r for r in filtered if r.scale == selected_scale]
    filtered = filtered[:10]

    if len(filtered) < 2:
        console.print(f"\n[yellow]Only {len(filtered)} result(s) found for this combination.[/yellow]")
        console.print("[dim]Need at least 2 results to compare.[/dim]")
        return None

    return _select_comparison_files(filtered)


def _select_from_options(
    items: list,
    label: str,
    count_fn: Any,
    format_fn: Any,
    header: str | None = None,
) -> Any:
    if len(items) == 1:
        console.print(f"[dim]Only one {label} found: {items[0]}[/dim]")
        return items[0]

    console.print(f"\n[bold]{header or f'Select {label}:'}[/bold]")
    for i, item in enumerate(items, start=1):
        count = count_fn(item)
        console.print(f"  [cyan]{i}[/cyan]  {format_fn(item)} ({count} results)")

    while True:
        user_input = Prompt.ask(f"\n{label.title()}", default="1")
        if user_input.isdigit():
            idx = int(user_input) - 1
            if 0 <= idx < len(items):
                return items[idx]
        console.print(f"[yellow]Enter 1-{len(items)}[/yellow]")


def _select_comparison_files(filtered: list[ResultFileMetadata]) -> tuple[Path, Path] | None:
    console.print(f"\n[bold]Select files to compare ({len(filtered)} results):[/bold]\n")
    _display_results_table(filtered)

    while True:
        baseline_input = Prompt.ask("\nBaseline file (older/first)", default="2" if len(filtered) >= 2 else "1")
        if baseline_input.isdigit():
            idx = int(baseline_input) - 1
            if 0 <= idx < len(filtered):
                baseline_meta = filtered[idx]
                break
        console.print(f"[yellow]Enter 1-{len(filtered)}[/yellow]")

    while True:
        current_input = Prompt.ask("Current file (newer/second)", default="1")
        if current_input.isdigit():
            idx = int(current_input) - 1
            if 0 <= idx < len(filtered):
                if filtered[idx].path == baseline_meta.path:
                    console.print("[yellow]Cannot compare a file to itself[/yellow]")
                    continue
                current_meta = filtered[idx]
                break
        console.print(f"[yellow]Enter 1-{len(filtered)}[/yellow]")

    return baseline_meta.path, current_meta.path


def _direct_file_selection() -> tuple[Path, Path] | None:
    console.print("\n[bold]Enter file paths:[/bold]")
    console.print("[dim]Enter full or relative paths to result JSON files[/dim]\n")

    while True:
        baseline_input = Prompt.ask("Baseline file path")
        if not baseline_input:
            console.print("[yellow]Path required[/yellow]")
            continue

        baseline_path = Path(baseline_input)
        if not baseline_path.exists():
            console.print(f"[yellow]File not found: {baseline_path}[/yellow]")
            continue
        break

    while True:
        current_input = Prompt.ask("Current file path")
        if not current_input:
            console.print("[yellow]Path required[/yellow]")
            continue

        current_path = Path(current_input)
        if not current_path.exists():
            console.print(f"[yellow]File not found: {current_path}[/yellow]")
            continue

        if current_path == baseline_path:
            console.print("[yellow]Cannot compare a file to itself[/yellow]")
            continue
        break

    return baseline_path, current_path


def _display_results_table(results: list[ResultFileMetadata]) -> None:
    benchmark_colors = {
        "TPC-H": "green",
        "TPC-DS": "blue",
        "SSB": "magenta",
        "ClickBench": "yellow",
    }

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="cyan bold", width=4, justify="right")
    table.add_column("Benchmark", width=12)
    table.add_column("Platform", width=14)
    table.add_column("Scale", width=8, justify="right")
    table.add_column("Timestamp", width=18)
    table.add_column("File", style="dim", width=30)

    for i, meta in enumerate(results, start=1):
        bench_color = benchmark_colors.get(meta.benchmark, "white")
        benchmark_styled = f"[{bench_color}]{meta.benchmark}[/{bench_color}]"

        scale_str = f"SF {meta.scale}"

        table.add_row(
            str(i),
            benchmark_styled,
            meta.platform,
            scale_str,
            meta.formatted_timestamp,
            meta.short_path,
        )

    console.print(table)


def _list_available_platforms():
    from benchbox.platforms import (
        list_available_dataframe_platforms,
        list_available_platforms,
    )

    console.print("\n[bold]Available Platforms[/bold]\n")

    console.print("[green]SQL Platforms:[/green]")
    sql_platforms = list_available_platforms()
    for platform in sorted(sql_platforms):
        console.print(f"  {platform}")

    console.print()

    console.print("[cyan]DataFrame Platforms:[/cyan]")
    df_platforms = list_available_dataframe_platforms()

    installed = [name for name, available in df_platforms.items() if available]
    not_installed = [name for name, available in df_platforms.items() if not available]

    if installed:
        console.print("  [green]Installed:[/green]")
        for platform in sorted(installed):
            console.print(f"    {platform}")

    if not_installed:
        console.print("  [dim]Not installed:[/dim]")
        for platform in sorted(not_installed):
            console.print(f"    {platform}")
        console.print("\n  [dim]Install with: pip install benchbox[dataframe-<name>][/dim]")

    console.print()
    console.print("[bold]Usage:[/bold]")
    console.print("  benchbox compare -p duckdb -p sqlite           # SQL comparison")
    console.print("  benchbox compare -p polars-df -p pandas-df     # DataFrame comparison")
    console.print("  benchbox compare                               # Interactive wizard")


def _build_platform_runner():
    from benchbox.cli.database import DatabaseManager
    from benchbox.cli.orchestrator import BenchmarkOrchestrator
    from benchbox.cli.system import SystemProfiler
    from benchbox.core.benchmark_registry import get_benchmark_metadata
    from benchbox.core.schemas import BenchmarkConfig

    def run_one_platform(
        *,
        platform: str,
        benchmark: str,
        scale_factor: float,
        query_ids: list[str] | None,
        iterations: int,
        data_dir,
    ):
        try:
            info = get_benchmark_metadata(benchmark) or {}
        except Exception:  # pragma: no cover
            info = {}

        options: dict[str, Any] = {}
        if iterations:
            options["power_iterations"] = iterations

        benchmark_config = BenchmarkConfig(
            name=benchmark,
            display_name=info.get("display_name", benchmark.upper()),
            scale_factor=scale_factor,
            queries=query_ids,
            options=options,
        )

        orchestrator = BenchmarkOrchestrator()
        if data_dir is not None:
            orchestrator.set_custom_output_dir(str(data_dir))

        return orchestrator.execute_benchmark(
            benchmark_config,
            SystemProfiler().get_system_profile(),
            DatabaseManager().create_config(platform),
            ["data", "schema", "load", "power"],
        )

    return run_one_platform


def _run_platform_comparison(
    platforms: list[str],
    platform_type: str,
    benchmark: str,
    scale: float,
    queries: str | None,
    warmup: int,
    iterations: int,
    data_dir: Path | None,
    output_format: str,
    output_file: str | None,
    generate_charts: bool,
    theme: str,
):
    from benchbox.core.comparison import (
        PlatformType,
        UnifiedBenchmarkConfig,
        UnifiedBenchmarkSuite,
        UnifiedComparisonPlotter,
    )

    if not platforms:
        console.print("[red]Error: Specify platforms with -p/--platform[/red]")
        console.print("\n[bold]Examples:[/bold]")
        console.print("  benchbox compare --run -p duckdb -p sqlite")
        console.print("  benchbox compare --run -p polars-df -p pandas-df")
        console.print("\nUse --list-platforms to see available platforms")
        sys.exit(1)

    if len(platforms) < 2:
        console.print("[red]Error: At least 2 platforms required for comparison[/red]")
        sys.exit(1)

    query_ids = None
    if queries:
        query_ids = [q.strip() for q in queries.split(",")]

    type_map = {
        "sql": PlatformType.SQL,
        "dataframe": PlatformType.DATAFRAME,
        "auto": PlatformType.AUTO,
    }
    ptype = type_map[platform_type]

    config = UnifiedBenchmarkConfig(
        platform_type=ptype,
        scale_factor=scale,
        benchmark=benchmark,
        query_ids=query_ids,
        warmup_iterations=warmup,
        benchmark_iterations=iterations,
    )

    suite = UnifiedBenchmarkSuite(config=config, platform_runner=_build_platform_runner())

    console.print("\n[bold]Cross-Platform Benchmark Comparison[/bold]")
    console.print(f"Platforms: {', '.join(platforms)}")
    console.print(f"Benchmark: {benchmark}")
    console.print(f"Scale factor: {scale}")
    console.print(f"Queries: {query_ids or 'all'}")
    console.print(f"Iterations: {iterations}")
    console.print()

    console.print("[dim]Running benchmarks...[/dim]")
    try:
        results = suite.run_comparison(platforms=platforms, data_dir=data_dir)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Benchmark failed: {e}[/red]")
        sys.exit(1)

    summary = suite.get_summary(results)

    _output_comparison_results(output_format, output_file, suite, config, results, summary)

    if generate_charts and output_file:
        _generate_comparison_charts(output_file, results, theme, UnifiedComparisonPlotter)

    _exit_on_comparison_failure(results, summary)


def _exit_on_comparison_failure(results: list, summary: Any) -> None:
    failed = [r for r in results if r.success_rate <= 0]

    if not summary.is_comparable:
        console.print(
            "[red]Comparison failed: no platform produced a usable timing, so there is nothing to compare.[/red]"
        )
        for result in failed:
            reason = next(
                (q.error_message for q in result.query_results if q.error_message),
                "no successful queries",
            )
            console.print(f"  [red]{result.platform}[/red]: {reason}")
        sys.exit(1)

    if failed:
        names = ", ".join(r.platform for r in failed)
        console.print(f"[yellow]Warning: no successful queries on {names}.[/yellow]")
        sys.exit(1)


def _output_comparison_results(
    output_format: str,
    output_file: str | None,
    suite: Any,
    config: Any,
    results: list,
    summary: Any,
) -> None:
    if output_format == "text":
        content = suite._generate_text_report(results)
        _write_or_print(content, output_file, "comparison.txt")

    elif output_format == "json":
        output_path = Path(output_file) if output_file else None
        if output_path:
            if not output_path.suffix:
                output_path.mkdir(parents=True, exist_ok=True)
                output_path = output_path / "comparison.json"
            suite.export_results(results, output_path, format="json")
            console.print(f"[green]Results saved to {output_path}[/green]")
        else:
            data = {
                "config": {
                    "platform_type": config.platform_type.value,
                    "scale_factor": config.scale_factor,
                    "benchmark": config.benchmark,
                    "query_ids": config.query_ids,
                    "iterations": config.benchmark_iterations,
                },
                "results": [r.to_dict() for r in results],
                "summary": summary.to_dict(),
            }
            console.print(json.dumps(data, indent=2))

    elif output_format == "markdown":
        content = suite._generate_markdown_report(results)
        _write_or_print(content, output_file, "comparison.md")

    elif output_format == "html":
        content = suite._generate_text_report(results)
        html_content = f"<pre>{_escape_html(content)}</pre>" if output_file else content
        _write_or_print(html_content if output_file else content, output_file, "comparison.html")
        if not output_file:
            console.print(content)
            return


def _write_or_print(content: str, output_file: str | None, default_filename: str) -> None:
    if output_file:
        output_path = Path(output_file)
        if not output_path.suffix:
            output_path.mkdir(parents=True, exist_ok=True)
            output_path = output_path / default_filename
        output_path.write_text(content, encoding="utf-8")
        console.print(f"[green]Results saved to {output_path}[/green]")
    else:
        console.print(content)


def _generate_comparison_charts(output_file: str, results: list, theme: str, plotter_class: type) -> None:
    try:
        output_dir = Path(output_file)
        if output_dir.suffix:
            output_dir = output_dir.parent
        output_dir.mkdir(parents=True, exist_ok=True)

        plotter = plotter_class(results, theme=theme)
        charts_dir = output_dir / "charts"
        exports = plotter.generate_charts(output_dir=charts_dir)
        if exports:
            chart_list = ", ".join(f"{ct}.txt" for ct in exports)
            console.print(f"[green]Charts generated in {charts_dir}: {chart_list}[/green]")
        else:
            console.print("[yellow]No charts generated (insufficient data)[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Chart generation failed: {e}[/yellow]")


def _run_file_comparison(
    result_files: tuple[str, ...],
    fail_on_regression: str | None,
    output_format: str,
    output_file: str | None,
    show_all_queries: bool,
    include_plans: bool = False,
    plan_threshold: float = 0.0,
    min_regression_delta: str | None = None,
    min_aggregate_regression_delta: str | None = None,
    allow_heterogeneous_comparison: bool = False,
):
    if len(result_files) < 2:
        console.print("[red]Error: At least 2 result files required for comparison[/red]")
        console.print("\n[bold]Usage:[/bold]")
        console.print("  benchbox compare baseline.json current.json")
        console.print("  benchbox compare file1.json file2.json file3.json")
        console.print("\n[bold]Or use --run mode:[/bold]")
        console.print("  benchbox compare --run -p duckdb -p sqlite")
        sys.exit(1)

    regression_threshold = _validate_regression_threshold(fail_on_regression)
    min_delta_ms = _validate_min_regression_delta(min_regression_delta)
    min_aggregate_delta_ms = _validate_min_regression_delta(min_aggregate_regression_delta)

    if len(result_files) > 2:
        console.print("[yellow]Note: Multi-file comparison not yet supported. Comparing first 2 files only.[/yellow]\n")

    baseline_path = Path(result_files[0])
    current_path = Path(result_files[1])

    baseline, baseline_raw, current, current_raw = _load_comparison_files(baseline_path, current_path)
    if not allow_heterogeneous_comparison:
        from benchbox.core.results.execution_variant import IncomparableVariantsError, bundle_variant_differences

        differences = bundle_variant_differences(baseline_raw, current_raw)
        if differences:
            console.print(
                f"[red]Error: {escape(str(IncomparableVariantsError(differences)))} "
                "Use --allow-heterogeneous-comparison to override.[/red]"
            )
            sys.exit(1)

    comparison = _perform_comparison(
        baseline, current, baseline_path, current_path, include_plans, plan_threshold, output_format
    )

    _output_file_comparison(comparison, baseline, current, output_format, output_file, show_all_queries)

    _check_regression_threshold(comparison, regression_threshold, min_delta_ms, min_aggregate_delta_ms)


def _validate_min_regression_delta(min_regression_delta: str | None) -> float | None:
    if min_regression_delta is None:
        return None
    min_delta_ms = _parse_duration_ms(min_regression_delta)
    if min_delta_ms is None:
        console.print(
            f'[red]Error: Invalid duration "{min_regression_delta}". '
            'Use milliseconds (e.g., "5ms") or seconds (e.g., "0.005s")[/red]'
        )
        sys.exit(1)
    return min_delta_ms


def _validate_regression_threshold(fail_on_regression: str | None) -> float | None:
    if not fail_on_regression:
        return None
    regression_threshold = _parse_threshold(fail_on_regression)
    if regression_threshold is None:
        console.print(
            f'[red]Error: Invalid threshold format "{fail_on_regression}". '
            'Use percentage (e.g., "10%") or decimal (e.g., "0.1")[/red]'
        )
        sys.exit(1)
    return regression_threshold


def _load_comparison_files(baseline_path: Path, current_path: Path) -> tuple[Any, dict[str, Any], Any, dict[str, Any]]:
    try:
        baseline, baseline_raw = load_result_file(baseline_path)
        current, current_raw = load_result_file(current_path)
        return baseline, baseline_raw, current, current_raw
    except FileNotFoundError as e:
        console.print(f"[red]Error: Result file not found: {e}[/red]")
        sys.exit(1)
    except UnsupportedSchemaError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
    except ResultLoadError as e:
        console.print(f"[red]Error loading result file: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Unexpected error loading files: {e}[/red]")
        sys.exit(1)


def _perform_comparison(
    baseline: Any,
    current: Any,
    baseline_path: Path,
    current_path: Path,
    include_plans: bool,
    plan_threshold: float,
    output_format: str = "text",
) -> dict[str, Any]:
    exporter = ResultExporter(anonymize=True)
    comparison = exporter.compare_results(baseline_path, current_path)

    if "error" in comparison:
        console.print(f"[red]Comparison failed: {comparison['error']}[/red]")
        sys.exit(1)

    if output_format != "json":
        generation = comparison.get("generation_compatibility") or {}
        if generation.get("warning"):
            if generation.get("status") == "incompatible":
                console.print(f"[yellow]Warning: {generation['warning']}[/yellow]")
            else:
                console.print(f"[dim]Note: {generation['warning']}[/dim]")

    if include_plans:
        plan_comparison = _compare_plans(baseline, current, plan_threshold)
        if plan_comparison:
            comparison["plan_comparison"] = plan_comparison
        else:
            console.print("[yellow]Note: No query plans found in result files.[/yellow]")
            console.print("[dim]Plans must be captured with --capture-plans during benchmark runs.[/dim]\n")

    return comparison


def _output_file_comparison(
    comparison: dict[str, Any],
    baseline: Any,
    current: Any,
    output_format: str,
    output_file: str | None,
    show_all_queries: bool,
) -> None:
    format_dispatch = {
        "text": lambda: _format_text_comparison(comparison, baseline, current, show_all_queries),
        "json": lambda: json.dumps(comparison, indent=2),
        "html": lambda: _format_html_comparison(comparison, baseline, current),
        "markdown": lambda: _format_markdown_comparison(comparison, baseline, current, show_all_queries),
    }

    formatter = format_dispatch.get(output_format)
    if formatter is None:
        return

    content = formatter()
    if output_file:
        Path(output_file).write_text(content, encoding="utf-8")
        console.print(f"[green]Comparison saved to {output_file}[/green]")
    elif output_format == "json":
        click.echo(content)
    else:
        console.print(content)


def _check_regression_threshold(
    comparison: dict[str, Any],
    regression_threshold: float | None,
    min_delta_ms: float | None = None,
    min_aggregate_delta_ms: float | None = None,
) -> None:
    if regression_threshold is None:
        return
    criteria = f"threshold: {regression_threshold * 100:.1f}%"
    if min_delta_ms is not None:
        criteria += f", minimum query slowdown: {min_delta_ms:g} ms"
    if min_aggregate_delta_ms is not None:
        criteria += f", minimum aggregate slowdown: {min_aggregate_delta_ms:g} ms"
    has_regression = _check_regression(comparison, regression_threshold, min_delta_ms, min_aggregate_delta_ms)
    if has_regression:
        console.print(f"\n[bold red]❌ Performance regression detected ({criteria})[/bold red]")
        sys.exit(1)
    else:
        console.print(f"\n[bold green]✓ No performance regression ({criteria})[/bold green]")


def _build_execution_map(results: Any) -> dict[str, Any]:
    execution_map: dict[str, Any] = {}
    for execution in iter_query_results(results):
        query_id = execution.get("query_id")
        if query_id is not None:
            execution_map.setdefault(query_id, execution)
    return execution_map


def _compare_plans(
    baseline: Any,
    current: Any,
    plan_threshold: float = 0.0,
) -> dict[str, Any] | None:
    from benchbox.core.query_plans.comparison import (
        QueryPlanComparator,
        generate_plan_comparison_summary,
    )

    try:
        summary = generate_plan_comparison_summary(
            baseline,
            current,
            regression_threshold_pct=20.0,
        )
    except Exception as e:
        console.print(f"[yellow]Warning: Plan comparison failed: {e}[/yellow]")
        return None

    if summary.plans_compared == 0:
        return None

    plan_comparisons = []
    comparator = QueryPlanComparator()

    baseline_map = _build_execution_map(baseline)
    current_map = _build_execution_map(current)

    common_queries = set(baseline_map.keys()) & set(current_map.keys())

    for query_id in sorted(common_queries):
        baseline_exec = baseline_map[query_id]
        current_exec = current_map[query_id]

        baseline_plan = baseline_exec.get("query_plan")
        current_plan = current_exec.get("query_plan")

        if not baseline_plan or not current_plan:
            continue

        comparison = comparator.compare_plans(baseline_plan, current_plan)
        similarity = comparison.similarity.overall_similarity

        if plan_threshold > 0 and similarity >= plan_threshold:
            continue

        baseline_time = baseline_exec.get("execution_time_ms", 0.0) or 0.0
        current_time = current_exec.get("execution_time_ms", 0.0) or 0.0
        perf_change_pct = 0.0
        if baseline_time > 0:
            perf_change_pct = ((current_time - baseline_time) / baseline_time) * 100

        plan_comparisons.append(
            {
                "query_id": query_id,
                "similarity": similarity,
                "plans_identical": comparison.plans_identical,
                "type_mismatches": comparison.similarity.type_mismatches,
                "property_mismatches": comparison.similarity.property_mismatches,
                "structure_mismatches": comparison.similarity.structure_mismatches,
                "summary": comparison.summary,
                "baseline_time_ms": baseline_time,
                "current_time_ms": current_time,
                "perf_change_pct": perf_change_pct,
                "is_regression": (not comparison.plans_identical) and perf_change_pct > 20.0,
            }
        )

    regressions = [p for p in plan_comparisons if p["is_regression"]]

    return {
        "plans_compared": summary.plans_compared,
        "plans_unchanged": summary.plans_unchanged,
        "plans_changed": summary.plans_changed,
        "regressions_detected": len(regressions),
        "threshold_applied": plan_threshold,
        "query_plans": plan_comparisons,
        "regressions": regressions,
    }


def _parse_threshold(threshold_str: str) -> float | None:
    threshold_str = threshold_str.strip()

    if threshold_str.endswith("%"):
        try:
            value = float(threshold_str[:-1])
            return value / 100.0
        except ValueError:
            return None

    try:
        return float(threshold_str)
    except ValueError:
        return None


_DURATION_PATTERN = re.compile(r"(?P<number>[0-9]+(?:\.[0-9]+)?)(?P<unit>ms|s)")


def _parse_duration_ms(duration_str: str) -> float | None:
    match = _DURATION_PATTERN.fullmatch(duration_str.strip().lower())
    if match is None:
        return None
    value_ms = float(match["number"]) * (1000.0 if match["unit"] == "s" else 1.0)
    return value_ms if math.isfinite(value_ms) else None


def _query_slowdown_exceeds(query: dict[str, Any], min_delta_ms: float | None) -> bool:
    if min_delta_ms is None:
        return True
    baseline_ms = query.get("baseline_time_ms")
    current_ms = query.get("current_time_ms")
    if baseline_ms is None or current_ms is None:
        return True
    diff_ms = current_ms - baseline_ms
    if math.isclose(diff_ms, min_delta_ms, rel_tol=1e-9, abs_tol=1e-9):
        return False
    return diff_ms > min_delta_ms


def _metric_slowdown_exceeds(metric_data: dict[str, Any], min_aggregate_delta_ms: float | None) -> bool:
    if min_aggregate_delta_ms is None:
        return True
    baseline_s = metric_data.get("baseline")
    current_s = metric_data.get("current")
    if baseline_s is None or current_s is None:
        return True
    diff_ms = (current_s - baseline_s) * 1000.0
    if math.isclose(diff_ms, min_aggregate_delta_ms, rel_tol=1e-9, abs_tol=1e-9):
        return False
    return diff_ms > min_aggregate_delta_ms


def _check_regression(
    comparison: dict[str, Any],
    threshold: float,
    min_delta_ms: float | None = None,
    min_aggregate_delta_ms: float | None = None,
) -> bool:
    threshold_percent = threshold * 100

    perf_changes = comparison.get("performance_changes", {})
    for _metric_name, metric_data in perf_changes.items():
        if not isinstance(metric_data, dict):
            continue
        if is_regression(metric_data.get("change_percent", 0), threshold_percent) and _metric_slowdown_exceeds(
            metric_data, min_aggregate_delta_ms
        ):
            return True

    query_comparisons = comparison.get("query_comparisons", [])
    for query in query_comparisons:
        if is_regression(query.get("change_percent", 0), threshold_percent) and _query_slowdown_exceeds(
            query, min_delta_ms
        ):
            return True

    return False


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _format_markdown_comparison(comparison: dict[str, Any], baseline: Any, current: Any, show_all: bool) -> str:
    lines = [
        "# Benchmark Comparison Report",
        "",
        f"- **Baseline:** {_markdown_cell(comparison['baseline_file'])}",
        f"- **Current:** {_markdown_cell(comparison['current_file'])}",
        f"- **Benchmark:** {_markdown_cell(baseline.benchmark_name)}",
        f"- **Platform:** {_markdown_cell(baseline.platform)}",
        f"- **Scale:** {_markdown_cell(baseline.scale_factor)}",
        "",
    ]

    generation = comparison.get("generation_compatibility") or {}
    if generation.get("warning"):
        status = str(generation.get("status") or "unknown")
        lines.extend(
            [
                f"> **Data generation {status}:** {_markdown_cell(str(generation['warning']))}",
                "",
            ]
        )

    summary = comparison.get("summary", {})
    if summary:
        lines.extend(
            [
                "## Summary",
                "",
                "| Metric | Value |",
                "| --- | ---: |",
                f"| Total Queries | {_markdown_cell(summary.get('total_queries_compared', 0))} |",
                f"| Improved | {_markdown_cell(summary.get('improved_queries', 0))} |",
                f"| Regressed | {_markdown_cell(summary.get('regressed_queries', 0))} |",
                f"| Unchanged | {_markdown_cell(summary.get('unchanged_queries', 0))} |",
                f"| Assessment | {_markdown_cell(summary.get('overall_assessment', 'unknown'))} |",
                "",
            ]
        )

    performance_changes = comparison.get("performance_changes", {})
    if performance_changes:
        lines.extend(
            ["## Performance Metrics", "", "| Metric | Baseline | Current | Change |", "| --- | ---: | ---: | ---: |"]
        )
        for metric, values in performance_changes.items():
            if not isinstance(values, dict):
                continue
            lines.append(
                f"| {_markdown_cell(str(metric).replace('_', ' ').title())} | "
                f"{values.get('baseline', 0):.3f} | {values.get('current', 0):.3f} | "
                f"{values.get('change_percent', 0):+.2f}% |"
            )
        lines.append("")

    query_comparisons = comparison.get("query_comparisons", [])
    if query_comparisons:
        lines.extend(
            [
                "## Query Comparison",
                "",
                "| Query | Baseline (ms) | Current (ms) | Change | Severity |",
                "| --- | ---: | ---: | ---: | --- |",
            ]
        )
        for query in sorted(query_comparisons, key=lambda item: item.get("change_percent", 0), reverse=True):
            change_pct = query["change_percent"]
            if not show_all and abs(change_pct) < 1.0:
                continue
            severity, change_str = _query_severity_and_change(query["improved"], change_pct)
            lines.append(
                f"| {_markdown_cell(query['query_id'])} | {query['baseline_time_ms']:.2f} | "
                f"{query['current_time_ms']:.2f} | {change_str} | {_markdown_cell(severity)} |"
            )
        lines.append("")

    plan_comparison = comparison.get("plan_comparison")
    if plan_comparison:
        lines.extend(
            [
                "## Query Plan Analysis",
                "",
                f"- Plans compared: {_markdown_cell(plan_comparison.get('plans_compared', 0))}",
                f"- Unchanged: {_markdown_cell(plan_comparison.get('plans_unchanged', 0))}",
                f"- Changed: {_markdown_cell(plan_comparison.get('plans_changed', 0))}",
                "",
            ]
        )
        query_plans = plan_comparison.get("query_plans", [])
        if query_plans:
            lines.extend(
                [
                    "| Query | Similarity | Type Δ | Prop Δ | Perf Δ | Status |",
                    "| --- | ---: | ---: | ---: | ---: | --- |",
                ]
            )
            for plan in sorted(query_plans, key=lambda item: item.get("similarity", 1.0)):
                status = _plan_status_label(
                    plan.get("plans_identical", False),
                    plan.get("similarity", 1.0),
                    plan.get("is_regression", False),
                )
                lines.append(
                    f"| {_markdown_cell(plan.get('query_id', '?'))} | {plan.get('similarity', 1.0):.1%} | "
                    f"{plan.get('type_mismatches', 0)} | {plan.get('property_mismatches', 0)} | "
                    f"{plan.get('perf_change_pct', 0.0):+.1f}% | {_markdown_cell(status)} |"
                )
            lines.append("")

        regressions = plan_comparison.get("regressions", [])
        if regressions:
            lines.extend(["### Plan-Correlated Regressions", ""])
            for regression in regressions:
                lines.append(
                    f"- **{_markdown_cell(regression.get('query_id', '?'))}**: "
                    f"plan similarity {regression.get('similarity', 0.0):.0%}, "
                    f"performance {regression.get('perf_change_pct', 0.0):+.1f}%"
                )
            lines.append("")

    return "\n".join(lines)


def _format_generation_section(lines: list[str], comparison: dict[str, Any]) -> None:
    generation = comparison.get("generation_compatibility") or {}
    warning = generation.get("warning")
    if not warning:
        return
    status = str(generation.get("status") or "unknown").upper()
    lines.append("-" * 80)
    lines.append(f"DATA GENERATION: {status}")
    lines.append(str(warning))
    lines.append("")


def _format_text_comparison(comparison: dict[str, Any], baseline: Any, current: Any, show_all: bool) -> str:
    lines: list[str] = []

    _format_header_section(lines, comparison, baseline)
    _format_generation_section(lines, comparison)
    _format_summary_section(lines, comparison)
    _format_performance_metrics_section(lines, comparison)

    query_comparisons = comparison.get("query_comparisons", [])
    _format_geometric_mean_section(lines, query_comparisons)
    _format_query_details_section(lines, query_comparisons, show_all)
    _format_plan_analysis_section(lines, comparison)

    lines.append("=" * 80)

    return "\n".join(lines)


def _format_header_section(lines: list[str], comparison: dict[str, Any], baseline: Any) -> None:
    lines.append("=" * 80)
    lines.append("BENCHMARK COMPARISON REPORT")
    lines.append("=" * 80)
    lines.append("")
    lines.append(f"Baseline: {comparison['baseline_file']}")
    lines.append(f"Current:  {comparison['current_file']}")
    lines.append("")
    lines.append(f"Benchmark:  {baseline.benchmark_name}")
    lines.append(f"Platform:   {baseline.platform}")
    lines.append(f"Scale:      {baseline.scale_factor}")
    lines.append("")


def _format_summary_section(lines: list[str], comparison: dict[str, Any]) -> None:
    summary = comparison.get("summary", {})
    if not summary:
        return
    lines.append("-" * 80)
    lines.append("OVERALL SUMMARY")
    lines.append("-" * 80)
    lines.append(f"Total queries compared: {summary.get('total_queries_compared', 0)}")
    lines.append(f"  Improved:  {summary.get('improved_queries', 0)}")
    lines.append(f"  Regressed: {summary.get('regressed_queries', 0)}")
    lines.append(f"  Unchanged: {summary.get('unchanged_queries', 0)}")
    lines.append(f"Assessment: {summary.get('overall_assessment', 'unknown').upper()}")
    lines.append("")


def _format_performance_metrics_section(lines: list[str], comparison: dict[str, Any]) -> None:
    perf_changes = comparison.get("performance_changes", {})
    if not perf_changes:
        return
    lines.append("-" * 80)
    lines.append("PERFORMANCE METRICS")
    lines.append("-" * 80)

    for metric_name, metric_data in perf_changes.items():
        if not isinstance(metric_data, dict):
            continue

        baseline_val = metric_data.get("baseline", 0)
        current_val = metric_data.get("current", 0)
        change_pct = metric_data.get("change_percent", 0)
        improved = metric_data.get("improved", False)

        display_name = metric_name.replace("_", " ").title()

        if improved:
            indicator = "🟢"
            change_str = f"-{abs(change_pct):.2f}%"
        elif change_pct > 10:
            indicator = "🔴"
            change_str = f"+{change_pct:.2f}%"
        elif change_pct > 0:
            indicator = "🟡"
            change_str = f"+{change_pct:.2f}%"
        else:
            indicator = "⚪"
            change_str = "0.00%"

        lines.append(f"{indicator} {display_name:25s} {baseline_val:10.3f}s → {current_val:10.3f}s ({change_str})")

    lines.append("")


def _format_geometric_mean_section(lines: list[str], query_comparisons: list[dict[str, Any]]) -> None:
    if not query_comparisons:
        return

    baseline_times = [q["baseline_time_ms"] for q in query_comparisons if q["baseline_time_ms"] > 0]
    current_times = [q["current_time_ms"] for q in query_comparisons if q["current_time_ms"] > 0]

    if not baseline_times or not current_times:
        return

    baseline_geomean = statistics.geometric_mean(baseline_times)
    current_geomean = statistics.geometric_mean(current_times)
    geomean_change = ((current_geomean - baseline_geomean) / baseline_geomean * 100) if baseline_geomean else 0

    lines.append("-" * 80)
    lines.append("GEOMETRIC MEAN (Query Times)")
    lines.append("-" * 80)

    if geomean_change < 0:
        indicator = "🟢 IMPROVED"
        change_str = f"-{abs(geomean_change):.2f}%"
    elif geomean_change > 10:
        indicator = "🔴 REGRESSED"
        change_str = f"+{geomean_change:.2f}%"
    elif geomean_change > 0:
        indicator = "🟡 SLOWER"
        change_str = f"+{geomean_change:.2f}%"
    else:
        indicator = "⚪ UNCHANGED"
        change_str = "0.00%"

    lines.append(f"Baseline: {baseline_geomean:.3f} ms")
    lines.append(f"Current:  {current_geomean:.3f} ms")
    lines.append(f"Change:   {change_str} {indicator}")
    lines.append("")


def _format_query_details_section(lines: list[str], query_comparisons: list[dict[str, Any]], show_all: bool) -> None:
    if not query_comparisons:
        return

    lines.append("-" * 80)
    lines.append("PER-QUERY COMPARISON")
    lines.append("-" * 80)
    lines.append(f"{'Query':10s} {'Baseline':>12s} {'Current':>12s} {'Change':>12s}  {'Severity':10s}")
    lines.append("-" * 80)

    sorted_queries = sorted(query_comparisons, key=lambda q: q.get("change_percent", 0), reverse=True)

    for query in sorted_queries:
        change_pct = query["change_percent"]
        if not show_all and abs(change_pct) < 1.0:
            continue

        query_id = str(query["query_id"])
        baseline_time = query["baseline_time_ms"]
        current_time = query["current_time_ms"]
        improved = query["improved"]

        severity, change_str = _query_severity_and_change(improved, change_pct)

        lines.append(f"{query_id:10s} {baseline_time:10.2f} ms {current_time:10.2f} ms {change_str:>12s}  {severity}")

    lines.append("")


def _query_severity_and_change(improved: bool, change_pct: float) -> tuple[str, str]:
    if improved:
        return "✓ FASTER", f"-{abs(change_pct):.2f}%"
    if change_pct > 50:
        return "🔴 CRITICAL", f"+{change_pct:.2f}%"
    if change_pct > 25:
        return "🔴 MAJOR", f"+{change_pct:.2f}%"
    if change_pct > 10:
        return "🟡 MINOR", f"+{change_pct:.2f}%"
    if change_pct > 1:
        return "⚪ SLIGHT", f"+{change_pct:.2f}%"
    return "⚪ SAME", "0.00%"


def _format_plan_analysis_section(lines: list[str], comparison: dict[str, Any]) -> None:
    plan_comparison = comparison.get("plan_comparison")
    if not plan_comparison:
        return

    lines.append("-" * 80)
    lines.append("QUERY PLAN ANALYSIS")
    lines.append("-" * 80)

    plans_compared = plan_comparison.get("plans_compared", 0)
    plans_unchanged = plan_comparison.get("plans_unchanged", 0)
    plans_changed = plan_comparison.get("plans_changed", 0)
    threshold = plan_comparison.get("threshold_applied", 0.0)

    lines.append(f"Plans Compared: {plans_compared}")
    lines.append(f"  Unchanged:    {plans_unchanged}")
    lines.append(f"  Changed:      {plans_changed}")
    if threshold > 0:
        lines.append(f"  (Filtered by similarity < {threshold:.0%})")
    lines.append("")

    query_plans = plan_comparison.get("query_plans", [])
    if query_plans:
        lines.append(f"{'Query':10s} {'Similarity':>12s} {'Type Δ':>8s} {'Prop Δ':>8s} {'Perf Δ':>10s}  {'Status':20s}")
        lines.append("-" * 80)

        sorted_plans = sorted(query_plans, key=lambda p: p.get("similarity", 1.0))

        for plan in sorted_plans:
            query_id = plan.get("query_id", "?")
            similarity = plan.get("similarity", 1.0)
            type_diff = plan.get("type_mismatches", 0)
            prop_diff = plan.get("property_mismatches", 0)
            perf_change = plan.get("perf_change_pct", 0.0)
            is_regression = plan.get("is_regression", False)

            status = _plan_status_label(plan.get("plans_identical", False), similarity, is_regression)

            perf_str = f"{perf_change:+.1f}%" if perf_change != 0 else "-"
            type_str = str(type_diff) if type_diff > 0 else "-"
            prop_str = str(prop_diff) if prop_diff > 0 else "-"

            lines.append(
                f"{query_id:10s} {similarity:11.1%} {type_str:>8s} {prop_str:>8s} {perf_str:>10s}  {status:20s}"
            )

        lines.append("")

    regressions = plan_comparison.get("regressions", [])
    if regressions:
        lines.append(f"⚠️  PLAN-CORRELATED REGRESSIONS: {len(regressions)}")
        lines.append("    (Plan changed AND performance degraded >20%)")
        for reg in regressions:
            query_id = reg.get("query_id", "?")
            perf_change = reg.get("perf_change_pct", 0.0)
            similarity = reg.get("similarity", 0.0)
            lines.append(f"    • {query_id}: plan similarity {similarity:.0%}, perf {perf_change:+.1f}%")
        lines.append("")
    else:
        lines.append("✓ No plan-correlated regressions detected")
        lines.append("")


def _plan_status_label(plans_identical: bool, similarity: float, is_regression: bool) -> str:
    if plans_identical:
        return "✓ Identical"
    if similarity >= 0.95:
        return "≈ Nearly Identical"
    if similarity >= 0.75:
        return "~ Similar"
    if is_regression:
        return "🔴 REGRESSION"
    return "✗ Different"


def _append_html_generation_section(html: list[str], comparison: dict[str, Any]) -> None:
    generation = comparison.get("generation_compatibility") or {}
    warning = generation.get("warning")
    if not warning:
        return
    status = _escape_html(str(generation.get("status") or "unknown"))
    html.append(f"<p><strong>Data generation ({status}):</strong> {_escape_html(str(warning))}</p>")


def _format_html_comparison(comparison: dict[str, Any], baseline: Any, current: Any) -> str:
    html: list[str] = []
    _append_html_header(html)
    _append_html_metadata(html, comparison, baseline)
    _append_html_generation_section(html, comparison)
    _append_html_summary_table(html, comparison)
    _append_html_query_comparison(html, comparison)
    _append_html_plan_section(html, comparison)
    html.append("</body>")
    html.append("</html>")
    return "\n".join(html)


def _append_html_header(html: list[str]) -> None:
    html.append("<!DOCTYPE html>")
    html.append("<html>")
    html.append("<head>")
    html.append("<title>Benchmark Comparison Report</title>")
    html.append("<style>")
    html.append("body { font-family: Arial, sans-serif; margin: 20px; }")
    html.append("h1, h2 { color: #333; }")
    html.append("table { border-collapse: collapse; width: 100%; margin: 20px 0; }")
    html.append("th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }")
    html.append("th { background-color: #f2f2f2; }")
    html.append(".improved { color: green; font-weight: bold; }")
    html.append(".regressed { color: red; font-weight: bold; }")
    html.append(".critical { background-color: #ffcccc; }")
    html.append(".major { background-color: #ffe6cc; }")
    html.append(".minor { background-color: #ffffcc; }")
    html.append("</style>")
    html.append("</head>")
    html.append("<body>")


def _append_html_metadata(html: list[str], comparison: dict[str, Any], baseline: Any) -> None:
    html.append("<h1>Benchmark Comparison Report</h1>")
    html.append(f"<p><strong>Baseline:</strong> {_escape_html(comparison['baseline_file'])}</p>")
    html.append(f"<p><strong>Current:</strong> {_escape_html(comparison['current_file'])}</p>")
    html.append(
        f"<p><strong>Benchmark:</strong> {_escape_html(baseline.benchmark_name)} | "
        f"<strong>Platform:</strong> {_escape_html(baseline.platform)} | "
        f"<strong>Scale:</strong> {_escape_html(baseline.scale_factor)}</p>"
    )


def _append_html_summary_table(html: list[str], comparison: dict[str, Any]) -> None:
    summary = comparison.get("summary", {})
    if not summary:
        return
    html.append("<h2>Summary</h2>")
    html.append("<table>")
    html.append("<tr><th>Metric</th><th>Value</th></tr>")
    html.append(f"<tr><td>Total Queries</td><td>{_escape_html(summary.get('total_queries_compared', 0))}</td></tr>")
    html.append(
        f"<tr><td>Improved</td><td class='improved'>{_escape_html(summary.get('improved_queries', 0))}</td></tr>"
    )
    html.append(
        f"<tr><td>Regressed</td><td class='regressed'>{_escape_html(summary.get('regressed_queries', 0))}</td></tr>"
    )
    html.append(f"<tr><td>Unchanged</td><td>{_escape_html(summary.get('unchanged_queries', 0))}</td></tr>")
    html.append("</table>")


def _html_query_row_class_and_severity(improved: bool, change_pct: float) -> tuple[str, str]:
    if improved:
        return "", "Improved"
    if change_pct > 50:
        return ' class="critical"', "Critical"
    if change_pct > 25:
        return ' class="major"', "Major"
    if change_pct > 10:
        return ' class="minor"', "Minor"
    return "", "None"


def _append_html_query_comparison(html: list[str], comparison: dict[str, Any]) -> None:
    query_comparisons = comparison.get("query_comparisons", [])
    if not query_comparisons:
        return

    html.append("<h2>Query Comparison</h2>")
    html.append("<table>")
    html.append("<tr><th>Query</th><th>Baseline (ms)</th><th>Current (ms)</th><th>Change</th><th>Severity</th></tr>")

    sorted_queries = sorted(query_comparisons, key=lambda q: q.get("change_percent", 0), reverse=True)

    for query in sorted_queries:
        row_class, severity = _html_query_row_class_and_severity(query["improved"], query["change_percent"])
        change_str = f"{query['change_percent']:+.2f}%"
        html.append(f"<tr{row_class}>")
        html.append(f"<td>{_escape_html(query['query_id'])}</td>")
        html.append(f"<td>{query['baseline_time_ms']:.2f}</td>")
        html.append(f"<td>{query['current_time_ms']:.2f}</td>")
        html.append(f"<td>{change_str}</td>")
        html.append(f"<td>{severity}</td>")
        html.append("</tr>")

    html.append("</table>")


def _html_plan_status(plan: dict[str, Any]) -> tuple[str, str]:
    if plan.get("plans_identical"):
        return "Identical", "improved"
    similarity = plan.get("similarity", 1.0)
    if similarity >= 0.95:
        return "Nearly Identical", "improved"
    if similarity >= 0.75:
        return "Similar", ""
    if plan.get("is_regression", False):
        return "REGRESSION", "regressed"
    return "Different", "minor"


def _append_html_plan_section(html: list[str], comparison: dict[str, Any]) -> None:
    plan_comparison = comparison.get("plan_comparison")
    if not plan_comparison:
        return

    html.append("<h2>Query Plan Analysis</h2>")

    plans_compared = plan_comparison.get("plans_compared", 0)
    plans_unchanged = plan_comparison.get("plans_unchanged", 0)
    plans_changed = plan_comparison.get("plans_changed", 0)
    regressions_detected = plan_comparison.get("regressions_detected", 0)

    html.append("<div style='display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin: 20px 0;'>")
    html.append(
        f"<div style='background: #f8f9fa; padding: 15px; border-radius: 8px; text-align: center;'><strong>{_escape_html(plans_compared)}</strong><br>Plans Compared</div>"
    )
    html.append(
        f"<div style='background: #d4edda; padding: 15px; border-radius: 8px; text-align: center;'><strong>{_escape_html(plans_unchanged)}</strong><br>Unchanged</div>"
    )
    html.append(
        f"<div style='background: #fff3cd; padding: 15px; border-radius: 8px; text-align: center;'><strong>{_escape_html(plans_changed)}</strong><br>Changed</div>"
    )
    regression_style = "background: #f8d7da;" if regressions_detected > 0 else "background: #d4edda;"
    html.append(
        f"<div style='{regression_style} padding: 15px; border-radius: 8px; text-align: center;'><strong>{_escape_html(regressions_detected)}</strong><br>Regressions</div>"
    )
    html.append("</div>")

    query_plans = plan_comparison.get("query_plans", [])
    if query_plans:
        html.append("<table>")
        html.append(
            "<tr><th>Query</th><th>Similarity</th><th>Type Δ</th><th>Prop Δ</th><th>Perf Δ</th><th>Status</th></tr>"
        )

        for plan in sorted(query_plans, key=lambda p: p.get("similarity", 1.0)):
            status, status_class = _html_plan_status(plan)
            is_regression = plan.get("is_regression", False)
            row_class = ' class="critical"' if is_regression else ""
            type_diff = plan.get("type_mismatches", 0)
            prop_diff = plan.get("property_mismatches", 0)
            perf_change = plan.get("perf_change_pct", 0.0)
            html.append(f"<tr{row_class}>")
            html.append(f"<td>{_escape_html(plan.get('query_id', '?'))}</td>")
            html.append(f"<td>{plan.get('similarity', 1.0):.1%}</td>")
            html.append(f"<td>{type_diff if type_diff > 0 else '-'}</td>")
            html.append(f"<td>{prop_diff if prop_diff > 0 else '-'}</td>")
            html.append(f"<td>{perf_change:+.1f}%</td>")
            html.append(f"<td class='{status_class}'>{status}</td>")
            html.append("</tr>")

        html.append("</table>")

    regressions = plan_comparison.get("regressions", [])
    if regressions:
        html.append(
            "<div style='background: #f8d7da; color: #721c24; padding: 15px; border-radius: 8px; margin-top: 20px;'>"
        )
        html.append(f"<strong>⚠️ Plan-Correlated Regressions: {len(regressions)}</strong>")
        html.append("<p>These queries have both plan changes AND performance degradation >20%:</p>")
        html.append("<ul>")
        for reg in regressions:
            html.append(
                f"<li><strong>{_escape_html(reg.get('query_id', '?'))}</strong>: plan similarity {reg.get('similarity', 0.0):.0%}, performance {reg.get('perf_change_pct', 0.0):+.1f}%</li>"
            )
        html.append("</ul>")
        html.append("</div>")
    else:
        html.append(
            "<div style='background: #d4edda; color: #155724; padding: 15px; border-radius: 8px; margin-top: 20px;'>"
        )
        html.append("<strong>✓ No plan-correlated regressions detected</strong>")
        html.append("</div>")


__all__ = ["compare"]
