from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from benchbox.cli.shared import console


@click.group(
    "metrics",
    help=(
        "Calculate benchmark performance metrics.\n"
        "\n"
        "The metrics command group provides tools for calculating\n"
        "TPC Power@Size and Throughput@Size from benchmark results.\n"
        "\n"
        "Available subcommands:\n"
        "\n"
        "\x08\n"
        "  qphh    Calculate Power@Size, Throughput@Size, and eligible QphH@Size\n"
        "\n"
        "Examples:\n"
        "    # Calculate Power@Size and Throughput@Size\n"
        "    benchbox metrics qphh \\\n"
        "      --power-results power.json \\\n"
        "      --throughput-results throughput.json\n"
    ),
)
def metrics_group():
    pass


@metrics_group.command(
    "qphh",
    help=(
        "Calculate Power@Size and Throughput@Size from result files and, when\n"
        "the evidence qualifies, TPC-H QphH@Size. QphH requires official\n"
        "compliance, 22 successful power queries, RF1/RF2 in the power phase,\n"
        "one successful refresh pair per throughput stream on consecutive sets,\n"
        "at least two streams, and no failed query. QphDS remains unsupported.\n"
        "\n"
        "Power@Size uses the final power iteration. Throughput@Size uses the\n"
        "throughput phase wall-clock duration including the refresh stream.\n"
        "\n"
        "\x08\n"
        "Examples:\n"
        "    # Calculate metrics from test results\n"
        "    benchbox metrics qphh \\\n"
        "      --power-results results/power/results.json \\\n"
        "      --throughput-results results/throughput/results.json\n"
        "\n"
        "\x08\n"
        "    # Specify scale factor explicitly\n"
        "    benchbox metrics qphh \\\n"
        "      --power-results power.json \\\n"
        "      --throughput-results throughput.json \\\n"
        "      --scale-factor 100\n"
        "\n"
        "\x08\n"
        "    # Export to JSON\n"
        "    benchbox metrics qphh \\\n"
        "      --power-results power.json \\\n"
        "      --throughput-results throughput.json \\\n"
        "      --format json --output qphh.json\n"
    ),
)
@click.option(
    "--power-results",
    type=click.Path(exists=True),
    required=True,
    help="Path to power test results JSON file",
)
@click.option(
    "--throughput-results",
    type=click.Path(exists=True),
    required=True,
    help="Path to throughput test results JSON file",
)
@click.option(
    "--scale-factor",
    type=float,
    help="Scale factor used (auto-detected from results if not provided)",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"], case_sensitive=False),
    default="text",
    help="Output format (default: text)",
)
@click.option(
    "--output",
    "output_file",
    type=click.Path(),
    help="Save output to file",
)
@click.pass_context
def qphh(ctx, power_results, throughput_results, scale_factor, output_format, output_file):
    result = _compute_qphh_result(power_results, throughput_results, scale_factor)
    _emit_qphh_output(result, output_format, output_file)


def _load_result_files(power_results: str, throughput_results: str) -> tuple[dict, dict]:
    try:
        with open(Path(power_results), encoding="utf-8") as f:
            power_data = json.load(f)
        with open(Path(throughput_results), encoding="utf-8") as f:
            throughput_data = json.load(f)
        return power_data, throughput_data
    except FileNotFoundError as e:
        console.print(f"[red]Error: Result file not found: {e}[/red]")
        sys.exit(1)
    except json.JSONDecodeError as e:
        console.print(f"[red]Error: Invalid JSON in result file: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Unexpected error loading files: {e}[/red]")
        sys.exit(1)


def _resolve_scale_factor(power_data: dict, throughput_data: dict, scale_factor: float | None) -> float:
    from benchbox.core.results.metrics import TPCMetricsCalculator

    try:
        return TPCMetricsCalculator.resolve_scale_factor(power_data, throughput_data, scale_factor)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


def _derive_tpc_metrics(
    power_data: dict, throughput_data: dict, scale_factor: float
) -> tuple[float | None, float | None, float | None, float | None]:
    from benchbox.core.results.metrics import TPCMetricsCalculator

    return TPCMetricsCalculator.derive_tpc_metrics(power_data, throughput_data, scale_factor)


def _compute_qphh_result(power_results: str, throughput_results: str, scale_factor: float | None) -> dict:
    power_data, throughput_data = _load_result_files(power_results, throughput_results)
    from benchbox.core.results.metrics import TPCMetricsCalculator

    try:
        return TPCMetricsCalculator.compute_qphh_result(power_data, throughput_data, scale_factor)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


def _emit_qphh_output(result: dict, output_format: str, output_file: str | None) -> None:
    if output_format == "text":
        content = _format_text_output(result)
    elif output_format == "json":
        content = json.dumps(result, indent=2)
    else:
        return

    if output_file:
        Path(output_file).write_text(content, encoding="utf-8")
        console.print(f"[green]Results saved to {output_file}[/green]")
    else:
        console.print(content)


def _format_text_output(result: dict) -> str:
    lines = []

    lines.append("=" * 70)
    lines.append(f"{result['benchmark']} POWER AND THROUGHPUT METRICS")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"Benchmark:        {result['benchmark']}")
    lines.append(f"Scale Factor:     {result['scale_factor']}")
    lines.append(f"Num Streams:      {result['num_streams']}")
    lines.append("")
    lines.append("-" * 70)
    lines.append("TEST EXECUTION TIMES")
    lines.append("-" * 70)
    power_time = result.get("power_test_time")
    throughput_time = result.get("throughput_test_time")
    if power_time is not None:
        lines.append(f"Power Test:       {power_time:.3f} seconds")
    else:
        lines.append("Power Test:       n/a")
    if throughput_time is not None:
        lines.append(f"Throughput Test:  {throughput_time:.3f} seconds")
    else:
        lines.append("Throughput Test:  n/a")
    lines.append("")
    lines.append("-" * 70)
    lines.append(f"{result['benchmark']} METRICS")
    lines.append("-" * 70)
    lines.append(f"Power@Size:       {result['power_at_size']:,.2f}")
    lines.append(f"Throughput@Size:  {result['throughput_at_size']:,.2f}")
    if result.get("qphh_at_size") is not None:
        lines.append(f"QphH@Size:        {result['qphh_at_size']:,.1f}")
    lines.append(f"Refresh included:  {'yes' if result.get('refresh_included') else 'no'}")
    lines.append("")
    lines.append("=" * 70)
    if result.get("qphh_at_size") is None:
        lines.append("QphDS@Size is not computed; TPC-DS maintenance support is not implemented.")
    else:
        lines.append("QphH@Size = sqrt(Power@Size × Throughput@Size), rounded to 0.1.")
    lines.append("  Power@Size = 3600 × SF / geometric_mean(final iteration's 22 queries, RF1, RF2)")
    lines.append("  Throughput@Size = Queries × 3600 × SF / Throughput_Phase_Wall_Time")
    lines.append("")

    return "\n".join(lines)


__all__ = ["metrics_group", "qphh", "_compute_qphh_result", "_emit_qphh_output"]
