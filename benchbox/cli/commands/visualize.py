from __future__ import annotations

from collections.abc import Sequence

import click

from benchbox.cli.shared import console
from benchbox.core.runtime_paths import resolve_results_dir_from_env
from benchbox.core.visualization import (
    ResultPlotter,
    list_templates,
)
from benchbox.core.visualization.chart_types import ALL_CHART_TYPES
from benchbox.core.visualization.orchestration import render_chart_set

SUPPORTED_CHART_TYPES = ALL_CHART_TYPES


def _render_ascii_charts(
    ctx: click.Context,
    sources: Sequence[str],
    chart_types: Sequence[str],
    theme: str,
    use_color: bool = True,
    use_unicode: bool = True,
    template_name: str | None = None,
) -> None:
    from rich.text import Text

    from benchbox.core.visualization.ascii_api import ChartOptions

    def print_ansi(content: str) -> None:
        console.print(Text.from_ansi(content))

    if template_name:
        types_to_render = []
        strict_pairwise_validation = True
    else:
        types_to_render, strict_pairwise_validation = _resolve_chart_types(ctx, chart_types)

    if not types_to_render and template_name is None:
        types_to_render = ["performance_bar"]

    source_paths = _resolve_source_paths(ctx, sources)

    try:
        plotter = ResultPlotter.from_sources(source_paths, theme=theme)
        results = plotter.results
    except Exception as e:
        console.print(f"[red]Error loading results: {e}[/red]")
        ctx.exit(1)

    opts = ChartOptions(use_color=use_color, use_unicode=use_unicode, theme=theme)

    try:
        outcome = render_chart_set(
            results,
            types_to_render,
            template_name=template_name,
            options=opts,
            strict_pairwise_validation=strict_pairwise_validation,
        )
    except Exception as e:
        console.print(f"[yellow]Warning: Could not render charts: {e}[/yellow]")
        return

    if outcome.validation_error:
        console.print(f"[red]Error: {outcome.validation_error.message}[/red]")
        if "actual_result_count" in outcome.validation_error.details:
            console.print(
                f"[yellow]Provided result count: {outcome.validation_error.details['actual_result_count']}[/yellow]"
            )
        ctx.exit(1)

    for chart in outcome.rendered:
        print_ansi(chart.content)
        console.print()

    for skipped in outcome.skipped:
        if skipped.reason == "renderer failed":
            message = skipped.details.get("message") or skipped.details.get("exception_type") or skipped.reason
            console.print(f"[yellow]Warning: Could not render {skipped.chart_type}: {message}[/yellow]")
        elif template_name:
            console.print(f"[yellow]Skipped {skipped.chart_type}: {skipped.reason}.[/yellow]")


def _resolve_chart_types(
    ctx: click.Context,
    chart_types: Sequence[str],
) -> tuple[list[str], bool]:
    strict_pairwise_validation = True

    lowered = [c.lower() for c in chart_types]
    if "auto" in lowered or "all" in lowered:
        return list(SUPPORTED_CHART_TYPES), False

    invalid = [c for c in lowered if c not in SUPPORTED_CHART_TYPES]
    if invalid:
        valid = ", ".join(SUPPORTED_CHART_TYPES)
        console.print(f"[red]Error: Unknown chart type(s): {', '.join(invalid)}[/red]")
        console.print(f"[yellow]Valid chart types: {valid}[/yellow]")
        ctx.exit(1)
    return list(lowered), strict_pairwise_validation


def _resolve_source_paths(ctx: click.Context, sources: Sequence[str]) -> list[str]:
    source_paths = list(sources) if sources else []

    if not source_paths:
        results_dir = resolve_results_dir_from_env()
        if results_dir.exists():
            json_files = sorted(results_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            source_paths = [str(f) for f in json_files[:5]]

    if not source_paths:
        console.print("[yellow]No result files found. Specify sources or run a benchmark first.[/yellow]")
        ctx.exit(1)

    return source_paths


@click.command("visualize", help=("Generate ASCII charts from BenchBox results."))
@click.argument("sources", nargs=-1, type=click.Path(), required=False)
@click.option(
    "--chart-type",
    "chart_types",
    multiple=True,
    default=("auto",),
    show_default=True,
    help=f"Chart types: auto|all|{'|'.join(SUPPORTED_CHART_TYPES)}.",
)
@click.option(
    "--template",
    "template_name",
    type=click.Choice([t.name for t in list_templates()], case_sensitive=False),
    help="Named template to use (overrides chart-type selection).",
)
@click.option(
    "--theme",
    type=click.Choice(["light", "dark"], case_sensitive=False),
    default="light",
    show_default=True,
)
@click.option(
    "--no-color",
    is_flag=True,
    default=False,
    help="Disable ANSI colors in ASCII output (for piping to files or plain terminals).",
)
@click.option(
    "--no-unicode",
    is_flag=True,
    default=False,
    help="Use ASCII-only characters (for terminals without Unicode support).",
)
@click.pass_context
def visualize(
    ctx: click.Context,
    sources: Sequence[str],
    chart_types: Sequence[str],
    template_name: str | None,
    theme: str,
    no_color: bool,
    no_unicode: bool,
):
    _render_ascii_charts(
        ctx,
        sources,
        chart_types,
        theme,
        use_color=not no_color,
        use_unicode=not no_unicode,
        template_name=template_name,
    )


__all__ = ["visualize"]
