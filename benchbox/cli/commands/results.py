import json
import sys
from pathlib import Path
from typing import Any

import click
from pydantic import ValidationError
from rich.table import Table

from benchbox.cli.exit_codes import ExitCode, exit_code_help
from benchbox.cli.output import ResultExporter
from benchbox.cli.shared import console
from benchbox.core.results.integrity_validator import (
    CheckStatus,
    IntegrityReport,
    directory_payload,
    report_payload,
    validate_directory,
    validate_file,
)
from benchbox.core.results.submission_history import list_hosted_submissions
from benchbox.core.schemas import ExecutionContext


def _extract_result_fields(data: dict[str, Any]) -> tuple[str | None, str | None, Any, dict[str, Any] | None]:
    platform = None
    benchmark_name = None
    scale_factor = None

    if "benchmark" in data and isinstance(data["benchmark"], dict):
        benchmark_name = data["benchmark"].get("name") or data["benchmark"].get("id")
        scale_factor = data["benchmark"].get("scale_factor")

    if "platform" in data and isinstance(data["platform"], dict):
        platform = data["platform"].get("name")

    if not platform:
        platform = data.get("platform")
    if not benchmark_name:
        benchmark_name = data.get("benchmark_name") or data.get("benchmark_id")
    if not scale_factor:
        scale_factor = data.get("scale_factor")

    execution_context = data.get("execution") or data.get("execution_context")

    return platform, benchmark_name, scale_factor, execution_context


def _reconstruct_cli_command(
    data: dict[str, Any],
    platform: str,
    benchmark_name: str,
    scale_factor: Any,
    execution_context: dict[str, Any] | None,
) -> str:
    base_cmd = f"benchbox run --platform {platform} --benchmark {benchmark_name} --scale {scale_factor}"

    if execution_context:
        ctx = ExecutionContext(**{k: v for k, v in execution_context.items() if k != "invocation_timestamp"})
        cli_args = ctx.to_cli_args()
        if cli_args:
            return f"{base_cmd} {' '.join(cli_args)}"
        return base_cmd

    extra_args = []

    if "run" in data and data["run"].get("query_subset"):
        extra_args.extend(["--queries", ",".join(data["run"]["query_subset"])])

    if extra_args:
        return f"{base_cmd} {' '.join(extra_args)}"

    return base_cmd


@click.group(
    "results",
    invoke_without_command=True,
    help=(
        "Show exported benchmark results and execution history.\n"
        "\n"
        "Displays a summary of recent benchmark executions including performance\n"
        "metrics, execution times, and result file locations.\n"
        "\n"
        "\b\n"
        "Examples:\n"
        "    benchbox results              # Show last 10 results\n"
        "    benchbox results --limit 25   # Show last 25 results\n"
        "    benchbox results --paths      # Print exact result paths for benchbox submit\n"
        "    benchbox results show-cli <file>  # Show CLI command to reproduce a run"
    ),
)
@click.option("--limit", type=int, default=10, help="Number of results to show")
@click.option("--submitted", is_flag=True, help="Show hosted submission history")
@click.option(
    "--paths",
    is_flag=True,
    help="Print copyable result JSON paths to stdout (suitable for piping to xargs)",
)
@click.pass_context
def results(ctx, limit, submitted, paths):
    if ctx.invoked_subcommand is not None:
        return
    if submitted and paths:
        raise click.UsageError("--submitted and --paths are mutually exclusive")
    exporter = ResultExporter()
    if submitted:
        _show_submitted_results(exporter, limit=limit)
    elif paths:
        _show_result_paths(exporter, limit=limit)
    else:
        exporter.show_results_summary()


def _show_result_paths(exporter: ResultExporter, *, limit: int) -> None:
    records = exporter.list_results()
    if not records:
        return

    shown = records[:limit]
    for record in shown:
        path = record.get("file")
        if path is not None:
            click.echo(str(path))
    if len(records) > limit:
        click.echo(
            f"# Showing {len(shown)} of {len(records)}; use --limit to widen.",
            err=True,
        )
    click.echo(
        "# Use with: benchbox submit <path> --output ./submission",
        err=True,
    )


def _show_submitted_results(exporter: ResultExporter, *, limit: int) -> None:
    records = list_hosted_submissions(Path(exporter.output_dir), limit=limit)
    if not records:
        console.print("[yellow]No hosted submissions found[/yellow]")
        return

    console.print(f"\n[bold]Hosted Submissions ({len(records)} shown)[/bold]")
    console.print(f"Output directory: [cyan]{exporter.output_dir}[/cyan]")

    table = Table()
    table.add_column("Submitted", style="dim")
    table.add_column("Benchmark", style="green")
    table.add_column("Platform", style="blue")
    table.add_column("Scale", style="cyan")
    table.add_column("Status", style="yellow")
    table.add_column("URL / Submission", style="magenta")

    for record in records:
        submitted = str(record.get("submitted_at", ""))[:19].replace("T", " ")
        url_or_submission = record.get("public_url") or record.get("submission_id") or ""
        table.add_row(
            submitted,
            str(record.get("benchmark") or ""),
            str(record.get("platform") or ""),
            str(record.get("scale_factor") or ""),
            str(record.get("status") or ""),
            str(url_or_submission),
        )

    console.print(table)


@results.command(
    "show-cli",
    help=(
        "Reconstruct the CLI command from a benchmark result file.\n"
        "\n"
        "Reads a benchmark result JSON file and reconstructs the CLI command\n"
        "that can be used to reproduce the benchmark run.\n"
        "\n"
        "\b\n"
        "Examples:\n"
        "    benchbox results show-cli benchmark_runs/results/tpch_sf001_duckdb_20240101_120000.json\n"
        "    benchbox results show-cli ./result.json --full"
    ),
)
@click.argument("result_file", type=click.Path(exists=True))
@click.option("--full", is_flag=True, help="Show full command with all options")
def show_cli(result_file: str, full: bool) -> None:
    try:
        result_path = Path(result_file)
        with result_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        platform, benchmark_name, scale_factor, execution_context = _extract_result_fields(data)

        if not all([platform, benchmark_name, scale_factor is not None]):
            console.print(
                "[red]❌ Could not extract required fields (platform, benchmark, scale_factor) from result file[/red]"
            )
            return

        full_cmd = _reconstruct_cli_command(data, platform, benchmark_name, scale_factor, execution_context)

        console.print("\n[bold]Reconstructed CLI Command:[/bold]")
        console.print(full_cmd, style="green", soft_wrap=True, markup=False, highlight=False)

        _display_provenance_info(execution_context)

        if full and execution_context:
            _display_execution_context_details(execution_context)

    except json.JSONDecodeError as e:
        console.print(f"[red]❌ Invalid JSON file: {e}[/red]")
    except (FileNotFoundError, PermissionError, IsADirectoryError) as e:
        console.print(f"[red]❌ Cannot read file: {e}[/red]")
    except (KeyError, TypeError) as e:
        console.print(f"[red]❌ Invalid result file format: {e}[/red]")
    except ValidationError as e:
        console.print(f"[red]❌ Invalid execution context in result file: {e}[/red]")


VALIDATE_EXIT_CODES = (ExitCode.SUCCESS, ExitCode.ERROR, ExitCode.VALIDATION_FAILED)


@results.command(
    "validate",
    help=(
        "Check result files for integrity, completeness and believability.\n"
        "\n"
        "Validates one result JSON file or every result file in a directory and reports\n"
        "PASS, INFO, WARN and FAIL findings. The findings are the ones the MCP\n"
        "validate_results tool returns for the same input.\n"
        "\n"
        "\b\n"
        "Examples:\n"
        "    benchbox results validate benchmark_runs/results/tpch_sf001_duckdb_sql.json\n"
        "    benchbox results validate benchmark_runs/results --json\n"
        "    benchbox results validate ./bundles --fail-on-warn --benchmark tpch\n"
        "\n"
        "\b\n"
        "JSON output (--json):\n"
        "    One file: an object with file, benchmark_id, platform, scale_factor,\n"
        "    overall_status, summary (count per status) and checks (category, name,\n"
        "    status, message, details; PASS checks only with --verbose).\n"
        "    Directory: an object with total, pass, warn, fail, files (file, benchmark_id,\n"
        "    platform, overall_status) and reports (one file object per result file).\n"
        "\n"
        "\b\n"
        "Exit status:\n"
        f"{exit_code_help(VALIDATE_EXIT_CODES)}\n"
        "    An input that cannot be read (missing path, or a file that is not readable JSON) exits 1."
    ),
)
@click.argument("path", type=click.Path(path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="Print the findings as JSON instead of a table")
@click.option("--verbose", is_flag=True, help="Include PASS checks in the output")
@click.option("--fail-on-warn", is_flag=True, help="Exit 5 when any file has a WARN finding, as for a FAIL")
@click.option("--benchmark", default=None, help="Only report files whose benchmark id or platform contains this text")
def validate_cli(path: Path, as_json: bool, verbose: bool, fail_on_warn: bool, benchmark: str | None) -> None:
    if not path.exists():
        click.echo(f"Error: {path} not found", err=True)
        sys.exit(ExitCode.ERROR)

    reports = [validate_file(path)] if path.is_file() else validate_directory(path)
    if benchmark:
        needle = benchmark.lower()
        reports = [r for r in reports if needle in r.benchmark_id.lower() or needle in r.platform.lower()]

    if as_json:
        payload = (
            report_payload(reports[0], verbose=verbose)
            if path.is_file() and reports
            else {**directory_payload(reports), "reports": [report_payload(r, verbose=verbose) for r in reports]}
        )
        click.echo(json.dumps(payload, indent=2))
    elif not reports:
        click.echo("No result files found.")
    else:
        for report in reports:
            _print_validation_report(report, verbose=verbose)
        if len(reports) > 1:
            _print_validation_totals(reports)

    sys.exit(_validation_exit_status(reports, fail_on_warn=fail_on_warn))


def _validation_exit_status(reports: list[IntegrityReport], *, fail_on_warn: bool) -> ExitCode:
    if any(c.name == "file_readable" and c.status == CheckStatus.FAIL for r in reports for c in r.checks):
        return ExitCode.ERROR
    if any(r.overall_status == CheckStatus.FAIL for r in reports):
        return ExitCode.VALIDATION_FAILED
    if fail_on_warn and any(r.overall_status == CheckStatus.WARN for r in reports):
        return ExitCode.VALIDATION_FAILED
    return ExitCode.SUCCESS


def _print_validation_report(report: IntegrityReport, *, verbose: bool) -> None:
    click.echo(f"File: {Path(report.file).name}")
    click.echo(
        f"Benchmark: {report.benchmark_id} | Platform: {report.platform} | "
        f"SF: {report.scale_factor} | Status: {report.overall_status.value}"
    )
    click.echo()
    click.echo(f"{'CATEGORY':<16} {'CHECK':<30} {'STATUS':<6} MESSAGE")
    for check in report.checks:
        if verbose or check.status != CheckStatus.PASS:
            click.echo(f"{check.category.value:<16} {check.name:<30} {check.status.value:<6} {check.message}")
    click.echo()


def _print_validation_totals(reports: list[IntegrityReport]) -> None:
    counts = directory_payload(reports)
    click.echo("=" * 60)
    click.echo(
        f"Aggregate: {counts['total']} files - {counts['pass']} PASS, {counts['warn']} WARN, {counts['fail']} FAIL"
    )
    click.echo("=" * 60)


def _display_provenance_info(execution_context: dict[str, Any] | None) -> None:
    if not execution_context:
        return
    entry_point = execution_context.get("entry_point", "unknown")
    timestamp = execution_context.get("timestamp") or execution_context.get("invocation_timestamp")
    console.print(f"\n[dim]Entry point: {entry_point}[/dim]")
    if timestamp:
        console.print(f"[dim]Executed: {timestamp}[/dim]")


_CONTEXT_DEFAULT_VALUES: dict[str, Any] = {
    "phases": ["power"],
    "mode": "sql",
    "compression_type": "none",
    "compression_enabled": False,
}

_CONTEXT_SKIP_KEYS = {"invocation_timestamp", "entry_point"}


def _display_execution_context_details(execution_context: dict[str, Any]) -> None:
    console.print("\n[bold]Execution Context Details:[/bold]")
    for key, value in execution_context.items():
        if key in _CONTEXT_SKIP_KEYS or value is None:
            continue
        if key in _CONTEXT_DEFAULT_VALUES and value == _CONTEXT_DEFAULT_VALUES[key]:
            continue
        if isinstance(value, bool) and not value:
            continue
        console.print(f"  {key}: {value}")


__all__ = ["results"]
