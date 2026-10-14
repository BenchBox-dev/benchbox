# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from pathlib import Path

import click
from rich.markup import escape
from rich.table import Table

from benchbox.cli.shared import console
from benchbox.core.results.database import (
    RankingConfig,
    ResultDatabase,
)
from benchbox.core.results.regression_policy import is_meaningful_improvement


@click.group(
    "report",
    help=(
        "Historical result analysis and platform rankings.\n"
        "\n"
        "Commands for analyzing benchmark results over time, generating platform\n"
        "rankings, and detecting performance regressions.\n"
        "\n"
        "\b\n"
        "Examples:\n"
        "    benchbox report rankings --benchmark TPC-H --scale-factor 1\n"
        "    benchbox report trends --platform DuckDB --benchmark TPC-H\n"
        "    benchbox report regressions\n"
        "    benchbox report import benchmark_runs/results/\n"
        "    benchbox report stats"
    ),
)
def report() -> None:
    pass


@report.command(
    "rankings",
    help=(
        "Generate platform rankings for a benchmark.\n"
        "\n"
        "Ranks platforms based on performance metrics from historical results.\n"
        "Shows trend indicators comparing current to previous period.\n"
        "\n"
        "\b\n"
        "Examples:\n"
        "    benchbox report rankings --benchmark TPC-H --scale-factor 1\n"
        "    benchbox report rankings -b TPC-DS -s 10 --metric power_at_size"
    ),
)
@click.option(
    "--benchmark",
    "-b",
    required=True,
    help="Benchmark name (e.g., TPC-H, TPC-DS)",
)
@click.option(
    "--scale-factor",
    "-s",
    type=float,
    required=True,
    help="Scale factor to rank",
)
@click.option(
    "--metric",
    "-m",
    type=click.Choice(["geometric_mean", "power_at_size", "cost_efficiency"]),
    default="geometric_mean",
    help="Metric to rank by",
)
@click.option(
    "--min-samples",
    type=int,
    default=1,
    help="Minimum samples required to include platform",
)
@click.option(
    "--lookback-days",
    type=int,
    default=90,
    help="Consider results from last N days",
)
@click.option(
    "--db-path",
    type=click.Path(path_type=Path),
    help="Path to result database (default: ~/.benchbox/results.db)",
)
def rankings(
    benchmark: str,
    scale_factor: float,
    metric: str,
    min_samples: int,
    lookback_days: int,
    db_path: Path | None,
) -> None:
    db = ResultDatabase(db_path)
    config = RankingConfig(
        metric=metric,
        min_samples=min_samples,
        lookback_days=lookback_days,
        require_success=True,
    )

    rankings_list = db.calculate_rankings(benchmark, scale_factor, config)

    if not rankings_list:
        console.print(f"[yellow]No results found for {benchmark} at scale factor {scale_factor}[/yellow]")
        return

    table = Table(title=f"{benchmark} Platform Rankings (SF={scale_factor})")
    table.add_column("Rank", style="cyan", justify="right")
    table.add_column("Platform", style="green")
    table.add_column("Version", style="dim")
    table.add_column(_get_metric_header(metric), justify="right")
    table.add_column("Samples", justify="right")
    table.add_column("Trend", justify="center")
    table.add_column("Change", justify="right")
    table.add_column("Last Run", style="dim")

    for r in rankings_list:
        trend_icon = _get_trend_icon(r.trend)
        change_str = f"{r.trend_change:+.1f}%" if r.trend_change is not None else "-"

        if metric == "geometric_mean":
            score_str = f"{r.score:.1f} ms"
        elif metric == "power_at_size":
            score_str = f"{r.score:.0f}"
        else:
            score_str = f"${r.score:.4f}/query"

        table.add_row(
            str(r.rank),
            r.platform,
            r.platform_version or "-",
            score_str,
            str(r.sample_count),
            trend_icon,
            change_str,
            r.latest_timestamp.strftime("%Y-%m-%d"),
        )

    console.print(table)


@report.command(
    "trends",
    help=(
        "Show performance trends over time.\n"
        "\n"
        "Displays period-over-period performance changes for a platform,\n"
        "highlighting any regressions (>10% slowdown).\n"
        "\n"
        "\b\n"
        "Examples:\n"
        "    benchbox report trends --platform DuckDB --benchmark TPC-H --scale-factor 1"
    ),
)
@click.option("--platform", "-p", required=True, help="Platform name")
@click.option("--benchmark", "-b", required=True, help="Benchmark name")
@click.option("--scale-factor", "-s", type=float, required=True, help="Scale factor")
@click.option("--periods", type=int, default=6, help="Number of periods to analyze")
@click.option("--period-days", type=int, default=30, help="Days per period")
@click.option(
    "--db-path",
    type=click.Path(path_type=Path),
    help="Path to result database",
)
def trends(
    platform: str,
    benchmark: str,
    scale_factor: float,
    periods: int,
    period_days: int,
    db_path: Path | None,
) -> None:
    db = ResultDatabase(db_path)
    trends_list = db.get_performance_trends(platform, benchmark, scale_factor, periods, period_days)

    if not trends_list:
        console.print(f"[yellow]No trend data found for {platform}/{benchmark} SF={scale_factor}[/yellow]")
        return

    table = Table(
        title=f"Performance Trends: {platform} / {benchmark} (SF={scale_factor})",
        caption=escape(f"Variant: {trends_list[0].variant}") if trends_list[0].variant else None,
    )
    table.add_column("Period", style="cyan")
    table.add_column("Avg (ms)", justify="right")
    table.add_column("Min (ms)", justify="right")
    table.add_column("Max (ms)", justify="right")
    table.add_column("Samples", justify="right")
    table.add_column("Change", justify="right")
    table.add_column("Status", justify="center")

    for t in trends_list:
        period_str = f"{t.period_start.strftime('%Y-%m-%d')} to {t.period_end.strftime('%Y-%m-%d')}"
        change_str = f"{t.change_pct:+.1f}%" if t.change_pct is not None else "-"

        if t.is_regression:
            status = "[red]REGRESSION[/red]"
        elif is_meaningful_improvement(t.change_pct):
            status = "[green]IMPROVED[/green]"
        else:
            status = "[blue]STABLE[/blue]"

        table.add_row(
            period_str,
            f"{t.avg_geometric_mean_ms:.1f}",
            f"{t.min_geometric_mean_ms:.1f}",
            f"{t.max_geometric_mean_ms:.1f}",
            str(t.sample_count),
            change_str,
            status,
        )

    console.print(table)


@report.command(
    "regressions",
    help=(
        "Detect performance regressions across all platforms.\n"
        "\n"
        "Scans all platforms and benchmarks for significant performance\n"
        "slowdowns compared to the previous period.\n"
        "\n"
        "\b\n"
        "Examples:\n"
        "    benchbox report regressions\n"
        "    benchbox report regressions --threshold 15 --lookback-days 14"
    ),
)
@click.option("--threshold", type=float, default=10.0, help="Regression threshold percentage")
@click.option("--lookback-days", type=int, default=30, help="Comparison period in days")
@click.option(
    "--db-path",
    type=click.Path(path_type=Path),
    help="Path to result database",
)
def regressions(
    threshold: float,
    lookback_days: int,
    db_path: Path | None,
) -> None:
    db = ResultDatabase(db_path)
    regressions_list = db.detect_regressions(threshold, lookback_days)

    if not regressions_list:
        console.print("[green]No performance regressions detected![/green]")
        return

    table = Table(title="Performance Regressions Detected", style="red")
    table.add_column("Platform", style="yellow")
    table.add_column("Benchmark")
    table.add_column("Scale Factor", justify="right")
    table.add_column("Variant")
    table.add_column("Current (ms)", justify="right")
    table.add_column("Change", justify="right", style="red")
    table.add_column("Period")

    for r in regressions_list:
        period_str = f"{r.period_start.strftime('%Y-%m-%d')} to {r.period_end.strftime('%Y-%m-%d')}"
        change_str = f"+{r.change_pct:.1f}%" if r.change_pct else "-"

        table.add_row(
            r.platform,
            r.benchmark,
            str(r.scale_factor),
            escape(r.variant or "-"),
            f"{r.avg_geometric_mean_ms:.1f}",
            change_str,
            period_str,
        )

    console.print(table)
    console.print(f"\n[yellow]Found {len(regressions_list)} regression(s) exceeding {threshold}% threshold[/yellow]")


@report.command(
    "import",
    help=(
        "Import benchmark results from a directory.\n"
        "\n"
        "Scans a directory for JSON result files and imports them into the\n"
        "historical database for analysis.\n"
        "\n"
        "By default, unofficial TPC-DS results (subscale or non-standard scale factor)\n"
        "are excluded from the database to prevent contamination of comparative rankings.\n"
        "Use --include-unofficial to import them explicitly.\n"
        "\n"
        "\b\n"
        "Examples:\n"
        "    benchbox report import benchmark_runs/results/\n"
        '    benchbox report import ./results --pattern "*.json"\n'
        "    benchbox report import ./results --include-unofficial"
    ),
)
@click.argument("directory", type=click.Path(exists=True, path_type=Path))
@click.option("--pattern", default="**/*.json", help="Glob pattern for result files")
@click.option(
    "--db-path",
    type=click.Path(path_type=Path),
    help="Path to result database",
)
@click.option(
    "--include-unofficial",
    is_flag=True,
    default=False,
    help="Include unofficial TPC-DS results (compliance_class != official). Excluded by default.",
)
def import_results(
    directory: Path,
    pattern: str,
    db_path: Path | None,
    include_unofficial: bool,
) -> None:
    db = ResultDatabase(db_path)

    with console.status("Importing results..."):
        imported, skipped, excluded = db.import_results_from_directory(
            directory, pattern, include_unofficial=include_unofficial
        )

    console.print(f"[green]Imported: {imported}[/green]")
    if excluded > 0:
        console.print(
            f"[yellow]Excluded (unofficial TPC-DS): {excluded}[/yellow] "
            "[dim](use --include-unofficial to import these)[/dim]"
        )
    if skipped > 0:
        console.print(f"[yellow]Skipped (duplicates/invalid): {skipped}[/yellow]")


@report.command(
    "stats",
    help=(
        "Show database summary statistics.\n"
        "\n"
        "Displays overview of stored results including counts, platforms,\n"
        "and date ranges.\n"
        "\n"
        "\b\n"
        "Examples:\n"
        "    benchbox report stats"
    ),
)
@click.option(
    "--db-path",
    type=click.Path(path_type=Path),
    help="Path to result database",
)
def stats(db_path: Path | None) -> None:
    db = ResultDatabase(db_path)
    summary = db.get_summary_stats()

    table = Table(title="Result Database Statistics")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    table.add_row("Total Results", str(summary["total_results"]))
    table.add_row("Total Query Records", str(summary["total_queries"]))
    table.add_row("Unique Platforms", str(summary["unique_platforms"]))
    table.add_row("Unique Benchmarks", str(summary["unique_benchmarks"]))

    if summary["earliest_result"]:
        table.add_row("Earliest Result", summary["earliest_result"][:10])
    if summary["latest_result"]:
        table.add_row("Latest Result", summary["latest_result"][:10])

    table.add_row("Database Path", summary["database_path"])
    table.add_row("Schema Version", str(summary["schema_version"]))

    console.print(table)

    platforms = db.get_platforms()
    if platforms:
        console.print(f"\n[cyan]Platforms:[/cyan] {', '.join(platforms)}")

    benchmarks = db.get_benchmarks()
    if benchmarks:
        console.print(f"[cyan]Benchmarks:[/cyan] {', '.join(benchmarks)}")


@report.command(
    "list",
    help=(
        "List stored benchmark results.\n"
        "\n"
        "Shows recent results with optional platform and benchmark filters.\n"
        "\n"
        "\b\n"
        "Examples:\n"
        "    benchbox report list\n"
        "    benchbox report list --platform DuckDB --limit 10"
    ),
)
@click.option("--platform", "-p", help="Filter by platform")
@click.option("--benchmark", "-b", help="Filter by benchmark")
@click.option("--limit", type=int, default=20, help="Maximum results to show")
@click.option(
    "--db-path",
    type=click.Path(path_type=Path),
    help="Path to result database",
)
def list_results(
    platform: str | None,
    benchmark: str | None,
    limit: int,
    db_path: Path | None,
) -> None:
    db = ResultDatabase(db_path)
    results = db.query_results(platform=platform, benchmark=benchmark, limit=limit)

    if not results:
        console.print("[yellow]No results found matching filters[/yellow]")
        return

    table = Table(title="Stored Benchmark Results")
    table.add_column("Date", style="dim")
    table.add_column("Platform", style="green")
    table.add_column("Benchmark")
    table.add_column("SF", justify="right")
    table.add_column("Queries", justify="right")
    table.add_column("Geomean (ms)", justify="right")
    table.add_column("Status", justify="center")
    table.add_column("Execution ID", style="dim")

    for r in results:
        status_style = "green" if r.validation_status == "PASSED" else "red"
        geomean_str = f"{r.geometric_mean_ms:.1f}" if r.geometric_mean_ms else "-"

        table.add_row(
            r.timestamp.strftime("%Y-%m-%d %H:%M"),
            r.platform,
            r.benchmark,
            str(r.scale_factor),
            f"{r.successful_queries}/{r.total_queries}",
            geomean_str,
            f"[{status_style}]{r.validation_status}[/{status_style}]",
            r.execution_id[:12],
        )

    console.print(table)


def _get_metric_header(metric: str) -> str:
    headers = {
        "geometric_mean": "Geomean",
        "power_at_size": "Power@Size",
        "cost_efficiency": "Cost/Query",
    }
    return headers.get(metric, metric)


def _get_trend_icon(trend: str) -> str:
    icons = {
        "up": "[green]\u2191[/green]",
        "down": "[red]\u2193[/red]",
        "stable": "[blue]\u2194[/blue]",
        "new": "[yellow]NEW[/yellow]",
    }
    return icons.get(trend, "-")


__all__ = ["report"]
