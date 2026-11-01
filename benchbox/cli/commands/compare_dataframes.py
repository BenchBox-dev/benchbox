import json
import sys
from pathlib import Path

import click

from benchbox.cli.host_lock_optin import host_guarded
from benchbox.cli.shared import console


@click.command(
    "compare-dataframes",
    hidden=True,
    deprecated=True,
    help=("Compare DataFrame platform performance. Deprecated; use `benchbox compare`."),
)
@click.option("--platforms", "-p", multiple=True, help="DataFrame platforms to compare. Repeatable.")
@click.option("--benchmark", "-b", default="tpch", show_default=True, type=click.Choice(["tpch"]))
@click.option("--scale", "-s", default=0.01, show_default=True, type=float)
@click.option("--queries", "-q", default=None, help="Comma-separated query IDs")
@click.option("--data-dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path))
@click.option("--format", "output_format", type=click.Choice(["json", "markdown", "text"]), default="text")
@click.option("--vs-sql", type=click.Choice(["duckdb", "sqlite"]), default=None)
@click.option("--warmup", default=1, show_default=True, type=int)
@click.option("--iterations", default=3, show_default=True, type=int)
@click.option("--generate-charts", is_flag=True)
@click.option("--theme", type=click.Choice(["light", "dark"]), default="light")
@click.option("--list-platforms", is_flag=True)
@host_guarded("compare-dataframes", skip=lambda kwargs: bool(kwargs.get("list_platforms")))
def compare_dataframes(
    platforms,
    benchmark,
    scale,
    queries,
    data_dir,
    output,
    output_format,
    vs_sql,
    warmup,
    iterations,
    generate_charts,
    theme,
    list_platforms,
):
    if list_platforms:
        _list_available_platforms()
        return

    console.print("[yellow]⚠ DEPRECATED: This command is deprecated. Use 'benchbox compare' instead.[/yellow]\n")

    if not platforms and not vs_sql:
        console.print("[red]Error: Specify at least one platform with --platforms or use --vs-sql[/red]")
        console.print("\nRun with --list-platforms to see available platforms")
        sys.exit(1)

    data_dir = data_dir or Path(f"benchmark_runs/{benchmark}/sf{str(scale).replace('.', '')}/data")
    if not data_dir.exists():
        console.print(f"[red]Error: Data directory not found: {data_dir}[/red]")
        console.print("\n[dim]Generate data first with:[/dim]")
        console.print(f"  benchbox run --platform duckdb --benchmark {benchmark} --scale {scale}")
        sys.exit(1)

    from benchbox.core.dataframe.benchmark_suite import BenchmarkConfig

    config = BenchmarkConfig(
        scale_factor=scale,
        query_ids=[q.strip() for q in queries.split(",")] if queries else None,
        warmup_iterations=warmup,
        benchmark_iterations=iterations,
    )
    if vs_sql:
        source, plotter_class = _run_sql_vs_dataframe(
            config, vs_sql, platforms[0] if platforms else "polars-df", data_dir, output, output_format
        )
    else:
        source, plotter_class = _run_platform_comparison(config, list(platforms), data_dir, output, output_format)

    _generate_charts(plotter_class, source, output, generate_charts, theme)


def _list_available_platforms() -> None:
    from benchbox.core.dataframe.benchmark_suite import PLATFORM_CAPABILITIES
    from benchbox.platforms import list_available_dataframe_platforms

    console.print("\n[bold]DataFrame Platforms[/bold]\n")
    feature_attrs = [
        ("lazy", "supports_lazy"),
        ("streaming", "supports_streaming"),
        ("gpu", "supports_gpu"),
        ("distributed", "supports_distributed"),
    ]
    for platform, available in list_available_dataframe_platforms().items():
        cap = PLATFORM_CAPABILITIES.get(platform)
        status = "installed" if available else "not installed"
        family = cap.family if cap else "unknown"
        category = cap.category.value if cap else "unknown"
        features = ", ".join(name for name, attr in feature_attrs if cap and getattr(cap, attr)) or "standard"
        console.print(f"  {platform:15s} ({status}, {family}, {category}) [{features}]")


def _run_platform_comparison(config, platforms: list[str], data_dir: Path, output_dir: Path | None, output_format: str):
    from benchbox.core.dataframe.benchmark_suite import DataFrameBenchmarkSuite, DataFrameComparisonPlotter

    console.print("\n[bold]DataFrame Platform Comparison[/bold]")
    suite = DataFrameBenchmarkSuite(config=config)
    available = suite.get_available_platforms()
    for platform in platforms:
        if platform not in available:
            console.print(f"[yellow]Warning: Platform {platform} not available, skipping[/yellow]")
    platforms = [p for p in platforms if p in available]
    if not platforms:
        console.print("[red]Error: No available platforms to compare[/red]")
        sys.exit(1)

    try:
        console.print("[dim]Running benchmarks...[/dim]")
        results = suite.run_comparison(platforms=platforms, data_dir=data_dir)
    except Exception as e:
        console.print(f"[red]Benchmark failed: {e}[/red]")
        sys.exit(1)

    summary = suite.get_summary(results)
    if output_format == "json":
        payload = {
            "config": {
                "scale_factor": config.scale_factor,
                "query_ids": config.query_ids,
                "iterations": config.benchmark_iterations,
            },
            "results": [r.to_dict() for r in results],
            "summary": summary.to_dict(),
        }
        _write_output(json.dumps(payload, indent=2), output_dir, "comparison.json", "Results saved to")
    elif output_format == "markdown":
        _write_output(suite._generate_markdown_report(results), output_dir, "comparison.md", "Report saved to")
    else:
        _print_text_summary(results, summary)
    return results, DataFrameComparisonPlotter


def _run_sql_vs_dataframe(
    config, sql_platform: str, df_platform: str, data_dir: Path, output_dir: Path | None, fmt: str
):
    from benchbox.core.dataframe.benchmark_suite import SQLVsDataFrameBenchmark, SQLVsDataFramePlotter

    console.print("\n[bold]SQL vs DataFrame Comparison[/bold]")
    console.print(f"SQL Platform: {sql_platform}")
    console.print(f"DataFrame Platform: {df_platform}")
    benchmark = SQLVsDataFrameBenchmark(config=config)
    try:
        console.print("[dim]Running benchmarks...[/dim]")
        summary = benchmark.run_comparison(sql_platform=sql_platform, df_platform=df_platform, data_dir=data_dir)
    except Exception as e:
        console.print(f"[red]Benchmark failed: {e}[/red]")
        sys.exit(1)

    if fmt == "json":
        _write_output(json.dumps(summary.to_dict(), indent=2), output_dir, "sql_vs_dataframe.json", "Results saved to")
    elif fmt == "markdown":
        _write_output(benchmark.generate_report(summary), output_dir, "sql_vs_dataframe.md", "Report saved to")
    else:
        _print_sql_vs_df_summary(summary)
    return summary, SQLVsDataFramePlotter


def _write_output(content: str, output_dir: Path | None, filename: str, label: str) -> None:
    if not output_dir:
        console.print(content)
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename
    output_path.write_text(content, encoding="utf-8")
    console.print(f"[green]{label} {output_path}[/green]")


def _generate_charts(plotter_class, source, output_dir: Path | None, enabled: bool, theme: str) -> None:
    if not enabled or not output_dir:
        return
    try:
        charts_dir = output_dir / "charts"
        exports = plotter_class(source, theme=theme).generate_charts(output_dir=charts_dir)
        message = f"[green]Charts generated in {charts_dir}: {', '.join(f'{ct}.txt' for ct in exports)}[/green]"
        console.print(message if exports else "[yellow]No charts generated (insufficient data)[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Chart generation failed: {e}[/yellow]")


def _print_text_summary(results, summary) -> None:
    console.print("\n[bold]RESULTS[/bold]")
    console.print(
        f"Fastest: [green]{summary.fastest_platform}[/green] | Slowest: [red]{summary.slowest_platform}[/red]"
    )
    for result in sorted(results, key=lambda r: r.geometric_mean_ms or float("inf")):
        geomean = f"{result.geometric_mean_ms:.2f}" if result.geometric_mean_ms else "N/A"
        total = f"{result.total_time_ms:.2f}" if result.total_time_ms else "N/A"
        console.print(f"{result.platform}: geomean={geomean}ms total={total}ms success={result.success_rate:.0f}%")
    if summary.query_winners:
        console.print("\n[bold]Query Winners:[/bold]")
        for query_id, winner in sorted(summary.query_winners.items()):
            console.print(f"  {query_id}: {winner}")


def _print_sql_vs_df_summary(summary) -> None:
    console.print("\n[bold]SQL vs DataFrame RESULTS[/bold]")
    console.print(f"SQL Platform: {summary.sql_platform} | DataFrame Platform: {summary.df_platform}")
    console.print(
        f"DataFrame faster: [green]{summary.df_faster_count}[/green] queries ({summary.df_wins_percentage:.1f}%)"
    )
    console.print(f"SQL faster: [yellow]{summary.sql_faster_count}[/yellow] queries")
    console.print(f"Average speedup: [bold]{summary.average_speedup:.2f}x[/bold]")
    for result in summary.query_results:
        if result.status == "SUCCESS":
            console.print(
                f"{result.query_id}: sql={result.sql_time_ms:.2f}ms df={result.df_time_ms:.2f}ms speedup={result.speedup:.2f}x"
            )
        else:
            console.print(f"{result.query_id}: [red]ERROR[/red]")
