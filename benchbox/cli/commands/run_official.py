import sys

import click

from benchbox.cli.commands.run import PlatformOptionParamType, run
from benchbox.cli.composite_params import ValidationConfig
from benchbox.cli.host_lock_optin import host_guarded
from benchbox.cli.shared import console
from benchbox.core.run_service import (
    TPC_ALLOWED_SCALE_FACTORS,
    validate_stream_count,
    validate_tpc_scale_factor,
)
from benchbox.core.tpc_disclaimer import official_mode_disclaimer


@click.command("run-official", hidden=True, deprecated=True)
@click.argument("benchmark", type=click.Choice(["tpch", "tpcds"], case_sensitive=False))
@click.option("--platform", type=str, required=True, help="Platform to run on")
@click.option("--scale", type=float, required=True, help="TPC scale factor")
@click.option("--phases", type=str, required=True, help="Test phases, comma-separated")
@click.option("--streams", type=int, help="Concurrent streams for throughput")
@click.option(
    "--platform-option",
    "platform_option_pairs",
    type=PlatformOptionParamType(),
    multiple=True,
    help="Platform option in KEY=VALUE form (repeatable).",
)
@click.option("--seed", type=int, help="Random seed for reproducible official-mode runs")
@click.option("--output", "output_dir", type=click.Path(), help="Output directory")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
@click.option("-q", "--quiet", is_flag=True, help="Emit the exported result path on the final non-empty stdout line")
@click.option("--validate-results", is_flag=True, help="Enable result validation")
@click.pass_context
@host_guarded("run-official")
def run_official(
    ctx,
    benchmark,
    platform,
    scale,
    phases,
    streams,
    platform_option_pairs,
    seed,
    output_dir,
    verbose,
    quiet,
    validate_results,
):
    try:
        validate_tpc_scale_factor(scale)
    except ValueError:
        console.print(f"[red]Error: Scale factor {scale} is not a TPC-specified scale factor[/red]")
        console.print(f"Allowed scale factors: {sorted(TPC_ALLOWED_SCALE_FACTORS)}")
        sys.exit(1)

    if seed is None and not quiet:
        console.print("[yellow]Warning: No seed specified. TPC official mode requires a random seed.[/yellow]")
        console.print("Use --seed <N> for reproducible results")

    try:
        validate_stream_count(streams, phases)
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        sys.exit(1)

    if not quiet:
        console.print("[bold blue]TPC Official Mode Benchmark Run[/bold blue]")
        console.print(f"[dim]{official_mode_disclaimer(benchmark)}[/dim]")
        for label, value in [
            ("Benchmark", f"TPC-{benchmark.upper()}"),
            ("Platform", platform),
            ("Scale Factor", scale),
            ("Phases", phases),
            ("Streams", streams),
            ("Seed", seed),
        ]:
            if value is not None:
                console.print(f"{label}: {value}")
        console.print("")
        if streams:
            console.print(f"[green]Concurrency: {streams} concurrent stream(s) will run the throughput phase[/green]")

    try:
        ctx.invoke(
            run,
            platform=platform,
            benchmark=benchmark,
            scale=scale,
            phases=phases,
            official=True,
            seed=seed,
            output=output_dir,
            verbose=verbose,
            quiet=quiet,
            validation=ValidationConfig.parse("full") if validate_results else None,
            platform_option_pairs=platform_option_pairs,
            concurrency=streams,
        )
    except Exception as e:
        console.print(f"[red]Benchmark execution failed: {e}[/red]")
        sys.exit(1)
