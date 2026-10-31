from __future__ import annotations

import click

from benchbox.cli.commands.run import run
from benchbox.cli.host_lock_optin import host_guarded
from benchbox.cli.shared import console
from benchbox.core.run_service import resolve_lifecycle_phases


@click.command(
    "datagen",
    help=(
        "Generate benchmark data without running queries.\n"
        "\n"
        "Standalone data generation command that generates benchmark data files\n"
        "without loading or executing queries. Useful for pre-generating data\n"
        "that can be reused across multiple benchmark runs.\n"
        "\n"
        "This is a convenience wrapper for: benchbox run --phases generate\n"
        "\n"
        "\b\n"
        "Examples:\n"
        "    # Generate TPC-H data at scale factor 0.1\n"
        "    benchbox datagen --benchmark tpch --scale 0.1 --output ./data/tpch_0.1\n"
        "\n"
        "\b\n"
        "    # Generate TPC-DS data with specific seed\n"
        "    benchbox datagen --benchmark tpcds --scale 1 --seed 42 --output ./data/tpcds_1\n"
        "\n"
        "\b\n"
        "    # Generate ClickBench data\n"
        "    benchbox datagen --benchmark clickbench --scale 1 --output ./data/clickbench\n"
        "\n"
        "\b\n"
        "    # Generate with verbose logging\n"
        "    benchbox datagen --benchmark tpch --scale 0.01 --output ./data --verbose"
    ),
)
@click.option(
    "--benchmark",
    type=str,
    required=True,
    help="Benchmark name (e.g., tpch, tpcds, clickbench)",
)
@click.option(
    "--scale",
    type=float,
    required=True,
    help="Scale factor for data generation",
)
@click.option(
    "--output",
    "output_dir",
    type=click.Path(),
    help="Output directory for generated data",
)
@click.option(
    "--format",
    "data_format",
    type=click.Choice(["parquet", "csv", "json"], case_sensitive=False),
    default="parquet",
    help="Data format (default: parquet)",
)
@click.option(
    "--seed",
    type=int,
    help="Random seed for reproducible data generation",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Enable verbose logging",
)
@click.pass_context
@host_guarded("datagen")
def datagen(ctx, benchmark, scale, output_dir, data_format, seed, verbose):
    console.print("[bold blue]Running data generation...[/bold blue]")
    console.print(f"Benchmark: {benchmark}, Scale: {scale}")

    if output_dir:
        console.print(f"Output: {output_dir}")

    if data_format and data_format != "parquet":
        console.print(
            f"[yellow]Note: Format '{data_format}' requested but may not be supported. "
            "Default format will be used.[/yellow]"
        )

    _phases = resolve_lifecycle_phases(["generate"])
    assert _phases.generate, "generate phase must be enabled for datagen"

    ctx.invoke(
        run,
        platform=None,
        benchmark=benchmark,
        scale=scale,
        output=output_dir,
        phases="generate",
        queries=None,
        tuning="notuning",
        table_mode="native",
        sorted_ingestion_mode=None,
        sorted_ingestion_method=None,
        dry_run=None,
        force=None,
        verbose=1 if verbose else 0,
        quiet=False,
        non_interactive=True,
        official=False,
        capture_plans=False,
        analyze_plans=None,
        show_plans=False,
        strict_translation=False,
        plan_config=None,
        normalize_plan_literals=False,
        stats_reset=None,
        stats_per_table_timing=False,
        compression=None,
        table_format=None,
        presort=None,
        validation=None,
        platform_option_pairs=(),
        benchmark_option_pairs=(),
        mode=None,
        seed=seed,
        concurrency=None,
        iterations=None,
        no_monitoring=False,
        no_progress=False,
        ignore_memory_warnings=False,
        global_cache=False,
        publish=False,
        publish_target="benchmark_runs/published",
        publish_label="maintainer-run",
        funding=None,
        result_source=None,
    )


def _parse_run_args(args: list[str]) -> dict:
    parsed: dict[str, object] = {}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("--"):
            key = arg[2:].replace("-", "_")
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                value = args[i + 1]
                i += 2
                if key == "scale":
                    parsed[key] = float(value)
                elif key == "seed":
                    parsed[key] = int(value)
                else:
                    parsed[key] = value
            else:
                parsed[key] = True
                i += 1
        else:
            i += 1
    return parsed


__all__ = ["datagen"]
