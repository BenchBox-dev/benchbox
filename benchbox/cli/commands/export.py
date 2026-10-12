from __future__ import annotations

from pathlib import Path

import click

from benchbox.cli.output import ResultExporter
from benchbox.cli.shared import console
from benchbox.core.constants import EXPORT_FORMATS
from benchbox.core.results.loader import (
    ResultLoadError,
    UnsupportedSchemaError,
    find_latest_result,
    load_result_file,
)


@click.command(
    "export",
    help=(
        "Export benchmark results to various formats.\n"
        "\n"
        "Re-export existing benchmark results in different formats (JSON, CSV, HTML)\n"
        "without re-running the benchmark. Useful for sharing results, generating\n"
        "reports, or converting to spreadsheet-friendly formats.\n"
        "\n"
        "RESULT_FILE: Path to result JSON file to export (optional)\n"
        "\n"
        "\b\n"
        "Examples:\n"
        "    # Export specific result to CSV\n"
        "    benchbox export results/tpch_sf1_duckdb.json --format csv\n"
        "\n"
        "\b\n"
        "    # Export to multiple formats\n"
        "    benchbox export results/tpcds_sf10.json --format csv --format html\n"
        "\n"
        "\b\n"
        "    # Export most recent result\n"
        "    benchbox export --last --format html\n"
        "\n"
        "\b\n"
        "    # Export latest TPC-H result to all formats\n"
        "    benchbox export --last --benchmark tpc_h --format json --format csv --format html\n"
        "\n"
        "\b\n"
        "    # Export to custom directory\n"
        "    benchbox export --last --format csv --output-dir ./reports/"
    ),
)
@click.argument("result_file", required=False, type=click.Path(exists=True))
@click.option(
    "--format",
    "formats",
    multiple=True,
    type=click.Choice(list(EXPORT_FORMATS), case_sensitive=False),
    help="Export format(s) - can specify multiple (default: json)",
)
@click.option(
    "--output-dir",
    type=str,
    help="Output directory (default: benchmark_runs/results/)",
)
@click.option(
    "--last",
    is_flag=True,
    help="Export most recent result file",
)
@click.option(
    "--benchmark",
    type=str,
    help="Filter by benchmark name when using --last",
)
@click.option(
    "--platform",
    type=str,
    help="Filter by platform name when using --last",
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite existing files without prompting",
)
@click.pass_context
def export(ctx, result_file, formats, output_dir, last, benchmark, platform, force):
    if not formats:
        formats = ["json"]

    source_path = _resolve_export_source(result_file, last, benchmark, platform)
    if source_path is None:
        return

    result = _load_export_result(source_path)
    if result is None:
        return

    output_directory = Path(output_dir) if output_dir else Path("benchmark_runs/results")
    output_directory.mkdir(parents=True, exist_ok=True)

    if not force and not _confirm_overwrite(source_path, output_directory, formats):
        return

    try:
        exporter = ResultExporter(output_dir=output_directory, anonymize=True)
        console.print(f"\n[bold]Exporting to {len(formats)} format(s)...[/bold]")
        exported = exporter.export_result(result, formats=list(formats))
        _print_export_summary(exported, output_directory)
    except Exception as e:
        console.print(f"\n[red]Export failed: {e}[/red]")
        raise click.ClickException(f"Export failed: {e}") from e


def _resolve_export_source(result_file, last, benchmark, platform):
    if result_file:
        return Path(result_file)

    if last:
        default_results_dir = Path("benchmark_runs/results")
        source_path = find_latest_result(default_results_dir, benchmark=benchmark, platform=platform)
        if not source_path:
            console.print("[yellow]No results found[/yellow]")
            if benchmark or platform:
                filters = []
                if benchmark:
                    filters.append(f"benchmark={benchmark}")
                if platform:
                    filters.append(f"platform={platform}")
                console.print(f"  Filters: {', '.join(filters)}")
            console.print("\n[dim]Tip: Run a benchmark first or check benchmark_runs/results/[/dim]")
            return None
        console.print(f"[blue]Using latest result:[/blue] {source_path.name}")
        return source_path

    console.print("[yellow]Please specify a result file or use --last[/yellow]")
    console.print("\n[bold]Examples:[/bold]")
    console.print("  benchbox export result.json --format csv")
    console.print("  benchbox export --last --format html")
    console.print("  benchbox export --last --benchmark tpc_h --format csv")
    console.print("\n[dim]Tip: Use 'benchbox results' to see available result files[/dim]")
    return None


def _load_export_result(source_path):
    try:
        result, _raw_data = load_result_file(source_path)
    except FileNotFoundError as e:
        console.print(f"[red]Error: Result file not found: {source_path}[/red]")
        raise click.ClickException(f"Result file not found: {source_path}") from e
    except UnsupportedSchemaError as e:
        console.print(f"[red]Error: {e}[/red]")
        console.print("[dim]Only schema version 1.0 is currently supported[/dim]")
        raise click.ClickException(str(e)) from e
    except ResultLoadError as e:
        console.print(f"[red]Error loading result file: {e}[/red]")
        console.print("[dim]The file may be corrupted or in an invalid format[/dim]")
        raise click.ClickException(f"Error loading result file: {e}") from e
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        raise click.ClickException(f"Unexpected error: {e}") from e

    console.print(f"[green]✓[/green] Loaded: {result.benchmark_name} ({result.platform})")
    console.print(
        f"  Scale: {result.scale_factor}, Queries: {result.total_queries}, Duration: {result.duration_seconds:.2f}s"
    )
    return result


def _confirm_overwrite(source_path, output_directory, formats) -> bool:
    base_name = source_path.stem
    conflicts = [
        fmt
        for fmt in formats
        if (output_directory / f"{base_name}.{fmt}").exists()
        and (output_directory / f"{base_name}.{fmt}") != source_path
    ]
    if not conflicts:
        return True

    console.print("\n[yellow]Warning: The following files will be overwritten:[/yellow]")
    for fmt in conflicts:
        console.print(f"  • {base_name}.{fmt}")

    if not click.confirm("\nProceed with export?", default=True):
        console.print("[yellow]Export cancelled[/yellow]")
        return False
    return True


def _print_export_summary(exported: dict, output_directory: Path) -> None:
    console.print("\n[bold green]✓ Export complete![/bold green]")
    console.print(f"Exported {len(exported)} format(s):")
    for fmt, path in exported.items():
        try:
            size_kb = path.stat().st_size / 1024
            console.print(f"  • {fmt.upper()}: {path.name} ({size_kb:.1f} KB)")
        except OSError:
            console.print(f"  • {fmt.upper()}: {path.name}")
    console.print(f"\n[dim]Output directory: {output_directory.absolute()}[/dim]")


__all__ = ["export"]
