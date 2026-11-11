# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table
from rich.text import Text

from benchbox.cli.logo import rich_logo
from benchbox.utils.printing import quiet_console

console = quiet_console


def check_and_run_first_time_setup() -> bool:
    import sys

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return False

    marker_path = _get_first_run_marker_path()

    try:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        with open(marker_path, "x", encoding="utf-8") as f:
            f.write(f"First run completed at {datetime.now().isoformat()}\n")
    except FileExistsError:
        return False

    _show_welcome_message()

    if Confirm.ask("\nWould you like a quick tour of BenchBox?", default=True):
        _run_interactive_tour()

    return True


def _get_first_run_marker_path() -> Path:
    return Path.home() / ".benchbox" / "first_run_complete"


def _show_welcome_message() -> None:
    welcome_text = Text()
    welcome_text.append("Welcome to ", style="white")
    welcome_text.append("BenchBox", style="bold cyan")
    welcome_text.append("!\n\n", style="white")

    welcome_text.append(
        "BenchBox helps you benchmark OLAP databases with industry-standard workloads.\n\n", style="white"
    )

    welcome_text.append("Quick Start:\n", style="bold yellow")
    welcome_text.append("  1. Select a ", style="white")
    welcome_text.append("database", style="cyan")
    welcome_text.append(" (DuckDB, Snowflake, BigQuery, etc.)\n", style="white")

    welcome_text.append("  2. Choose a ", style="white")
    welcome_text.append("benchmark", style="cyan")
    welcome_text.append(" (TPC-H, TPC-DS, ClickBench, etc.)\n", style="white")

    welcome_text.append("  3. Configure ", style="white")
    welcome_text.append("tuning options", style="cyan")
    welcome_text.append(" (constraints, indexes, partitioning)\n", style="white")

    welcome_text.append("  4. Run and analyze ", style="white")
    welcome_text.append("results", style="cyan")
    welcome_text.append("\n\n", style="white")

    welcome_text.append("Documentation: ", style="dim")
    welcome_text.append("https://benchbox.dev/docs/", style="cyan underline")

    if (logo := rich_logo()) is not None:
        console.print(logo, end="\n\n")
    console.print(Panel(welcome_text, title="🎯 Getting Started", border_style="cyan", padding=(1, 2)))


def _run_interactive_tour() -> None:
    console.print()

    _show_key_concepts()

    if not Confirm.ask("\nContinue to benchmarks overview?", default=True):
        return

    _show_benchmarks_overview()

    if not Confirm.ask("\nContinue to tuning modes explanation?", default=True):
        return

    _show_tuning_modes()

    if not Confirm.ask("\nContinue to scale factor guide?", default=True):
        return

    _show_scale_factor_guide()

    console.print("\n[bold green]✓ Tour complete![/bold green]")
    console.print("[dim]You're ready to run your first benchmark.[/dim]\n")


def _show_key_concepts() -> None:
    concepts_table = Table(show_header=True, box=None, padding=(0, 1))
    concepts_table.add_column("Term", style="cyan bold", width=18)
    concepts_table.add_column("Description", style="white", width=60)

    concepts_table.add_row(
        "Benchmark", "A standardized workload for testing database performance (e.g., TPC-H, TPC-DS)"
    )
    concepts_table.add_row("Scale Factor (SF)", "Dataset size multiplier (0.01 = ~10MB, 1.0 = ~1GB, 10.0 = ~10GB)")
    concepts_table.add_row("Tuning", "Database optimizations like indexes, partitioning, clustering, and constraints")
    concepts_table.add_row("Power Test", "Runs queries sequentially to measure single-query performance")
    concepts_table.add_row("Throughput Test", "Runs queries concurrently to measure multi-user performance")
    concepts_table.add_row("Validation", "Verifies query results match expected values (row counts, checksums)")

    console.print(Panel(concepts_table, title="📚 Key Concepts", border_style="blue"))


def _show_benchmarks_overview() -> None:
    benchmarks_table = Table(show_header=True, box=None)
    benchmarks_table.add_column("Benchmark", style="green bold", width=15)
    benchmarks_table.add_column("Type", style="cyan", width=20)
    benchmarks_table.add_column("Best For", style="white", width=45)

    benchmarks_table.add_row(
        "TPC-H", "Decision Support", "General OLAP testing, query optimization, analytical workloads"
    )
    benchmarks_table.add_row("TPC-DS", "Decision Support", "Complex queries, advanced features, realistic BI scenarios")
    benchmarks_table.add_row("ClickBench", "Analytics", "Real-world web analytics, columnar databases, fast scans")
    benchmarks_table.add_row("SSB", "Star Schema", "Simple star schema queries, dimensional modeling validation")
    benchmarks_table.add_row("TPC-DI", "Data Integration", "ETL pipeline testing, data loading performance")

    console.print(Panel(benchmarks_table, title="🎯 Popular Benchmarks", border_style="green"))
    console.print("[dim]Tip: Start with TPC-H at SF=0.01 for a quick first run (2-5 minutes)[/dim]")


def _show_tuning_modes() -> None:
    tuning_text = Text()

    tuning_text.append("Tuning Mode Options:\n\n", style="bold cyan")

    tuning_text.append("• ", style="white")
    tuning_text.append("notuning", style="yellow bold")
    tuning_text.append(" (Baseline)\n", style="white")
    tuning_text.append("  No optimizations applied. Use this to establish a performance baseline.\n", style="dim")
    tuning_text.append("  Best for: Comparing tuned vs untuned performance\n\n", style="dim")

    tuning_text.append("• ", style="white")
    tuning_text.append("tuned", style="green bold")
    tuning_text.append(" (Optimized)\n", style="white")
    tuning_text.append("  Applies recommended optimizations for your platform.\n", style="dim")
    tuning_text.append("  Best for: Realistic performance testing, production-like scenarios\n\n", style="dim")

    tuning_text.append("• ", style="white")
    tuning_text.append("custom", style="cyan bold")
    tuning_text.append(" (Interactive)\n", style="white")
    tuning_text.append("  Guided wizard to configure specific optimizations.\n", style="dim")
    tuning_text.append("  Best for: Experimenting with specific tuning strategies\n\n", style="dim")

    tuning_text.append("• ", style="white")
    tuning_text.append("<file-path>", style="magenta bold")
    tuning_text.append(" (Custom Config)\n", style="white")
    tuning_text.append("  Load tuning configuration from a YAML file.\n", style="dim")
    tuning_text.append("  Best for: Repeatable benchmarks, CI/CD integration\n", style="dim")

    console.print(Panel(tuning_text, title="⚙️ Tuning Modes", border_style="yellow"))


def _show_scale_factor_guide() -> None:
    scale_table = Table(show_header=True, box=None)
    scale_table.add_column("Scale Factor", style="cyan bold", width=15)
    scale_table.add_column("Dataset Size", style="yellow", width=15)
    scale_table.add_column("Memory Needed", style="magenta", width=15)
    scale_table.add_column("Runtime", style="green", width=12)
    scale_table.add_column("Use Case", style="white", width=25)

    scale_table.add_row("0.01", "~10 MB", "1-2 GB", "2-5 min", "Development, testing")
    scale_table.add_row("0.1", "~100 MB", "2-4 GB", "5-15 min", "CI/CD, local validation")
    scale_table.add_row("1.0", "~1 GB", "4-8 GB", "10-30 min", "Small-scale benchmarking")
    scale_table.add_row("10.0", "~10 GB", "16-32 GB", "30-90 min", "Production-like testing")
    scale_table.add_row("100.0", "~100 GB", "64-128 GB", "2-6 hours", "Large-scale benchmarking")

    console.print(Panel(scale_table, title="📊 Scale Factor Guide", border_style="magenta"))
    console.print("[dim]Tip: Start small (SF=0.01) and scale up as needed[/dim]")


def show_contextual_help(context: str) -> None:
    help_content = _get_help_content(context)

    if help_content:
        console.print()
        console.print(Panel(help_content, title=f"Help: {context.replace('_', ' ').title()}", border_style="blue"))
        console.print()


def _get_help_content(context: str) -> Optional[Text]:
    help_texts = {
        "benchmark_selection": _create_benchmark_help(),
        "scale_factor": _create_scale_factor_help(),
        "tuning_mode": _create_tuning_help(),
        "concurrency": _create_concurrency_help(),
    }

    return help_texts.get(context)


def _create_benchmark_help() -> Text:
    text = Text()
    text.append("Choosing a Benchmark:\n\n", style="bold")
    text.append("• ", style="white")
    text.append("TPC-H", style="cyan")
    text.append(": Best for general OLAP testing (22 queries, medium complexity)\n", style="white")
    text.append("• ", style="white")
    text.append("TPC-DS", style="cyan")
    text.append(": Comprehensive testing with complex queries (99 queries, high complexity)\n", style="white")
    text.append("• ", style="white")
    text.append("ClickBench", style="cyan")
    text.append(": Real-world analytics workload (43 queries)\n", style="white")
    text.append("\nNot sure? Start with TPC-H at SF=0.01", style="dim italic")
    return text


def _create_scale_factor_help() -> Text:
    text = Text()
    text.append("Scale Factor Selection:\n\n", style="bold")
    text.append("The scale factor controls dataset size:\n", style="white")
    text.append("• ", style="white")
    text.append("0.01", style="cyan")
    text.append(" = ~10 MB (quick testing, < 5 minutes)\n", style="white")
    text.append("• ", style="white")
    text.append("1.0", style="cyan")
    text.append(" = ~1 GB (realistic testing, 10-30 minutes)\n", style="white")
    text.append("• ", style="white")
    text.append("10.0", style="cyan")
    text.append(" = ~10 GB (production-like, 30+ minutes)\n", style="white")
    text.append("\nLarger scale factors provide more realistic results but take longer to run.", style="dim italic")
    return text


def _create_tuning_help() -> Text:
    text = Text()
    text.append("Tuning Modes:\n\n", style="bold")
    text.append("• ", style="white")
    text.append("notuning", style="yellow")
    text.append(": No optimizations (baseline performance)\n", style="white")
    text.append("• ", style="white")
    text.append("tuned", style="green")
    text.append(": Recommended optimizations for your platform\n", style="white")
    text.append("• ", style="white")
    text.append("wizard", style="cyan")
    text.append(": Interactive configuration of specific optimizations\n", style="white")
    text.append("\nStart with 'tuned' to see optimized performance.", style="dim italic")
    return text


def _create_concurrency_help() -> Text:
    text = Text()
    text.append("Concurrent Streams:\n\n", style="bold")
    text.append("Concurrency simulates multiple users running queries simultaneously:\n", style="white")
    text.append("• ", style="white")
    text.append("1 stream", style="cyan")
    text.append(": Sequential execution (Power Test)\n", style="white")
    text.append("• ", style="white")
    text.append("2-4 streams", style="cyan")
    text.append(": Light concurrency (typical for small systems)\n", style="white")
    text.append("• ", style="white")
    text.append("8+ streams", style="cyan")
    text.append(": Heavy concurrency (multi-user scenarios)\n", style="white")
    text.append("\nMore streams = higher system load. Match your expected workload.", style="dim italic")
    return text


__all__ = [
    "check_and_run_first_time_setup",
    "show_contextual_help",
]
