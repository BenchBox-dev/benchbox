from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
from rich.panel import Panel
from rich.text import Text

from benchbox.cli.shared import console
from benchbox.core.query_plans.visualization import (
    VisualizationOptions,
    render_plan,
    render_summary,
)
from benchbox.core.results.loader import iter_query_results, load_result_file
from benchbox.core.results.query_normalizer import normalize_query_id


def _find_query(results, query_id: str):
    normalized_target = normalize_query_id(query_id)
    matches = [
        qr for qr in iter_query_results(results) if normalize_query_id(qr.get("query_id", "")) == normalized_target
    ]
    return next(
        (qr for qr in matches if qr.get("query_plan") is not None or qr.get("query_plan_raw") is not None),
        matches[0] if matches else None,
    )


def _print_raw_plan(query_id: str, raw_plan: Any, output_format: str) -> None:
    if output_format == "json":
        console.print(json.dumps(raw_plan, indent=2, default=str))
        return
    text = raw_plan.get("plan_text") if isinstance(raw_plan, dict) else None
    platform = raw_plan.get("platform", "unknown") if isinstance(raw_plan, dict) else "unknown"
    body = text if text else json.dumps(raw_plan, indent=2, default=str)
    console.print(
        Panel(
            Text(body),
            title=Text(f"Raw Query Plan: {query_id} ({platform}; no structured plan available)"),
            border_style="yellow",
        )
    )


@click.command(
    "show-plan",
    help=(
        "Display query plan as ASCII tree.\n"
        "\n"
        "Shows the logical query plan for a specific query from a benchmark run.\n"
        "The plan must have been captured using --capture-plans during the benchmark.\n"
        "\n"
        "\b\n"
        "Examples:\n"
        "    # Show plan as tree\n"
        "    benchbox show-plan --run results.json --query-id q05\n"
        "\n"
        "\b\n"
        "    # Show summary statistics only\n"
        "    benchbox show-plan --run results.json --query-id 1 --format summary\n"
        "\n"
        "\b\n"
        "    # Export plan as JSON\n"
        "    benchbox show-plan --run results.json --query-id q05 --format json\n"
        "\n"
        "\b\n"
        "    # Compact tree view without properties\n"
        "    benchbox show-plan --run results.json --query-id q05 --compact --no-properties"
    ),
)
@click.option(
    "--run",
    "run_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to benchmark results JSON file",
)
@click.option(
    "--query-id",
    required=True,
    help="Query ID to show plan for (e.g., 'q05', '1')",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["tree", "summary", "json"], case_sensitive=False),
    default="tree",
    help="Output format",
)
@click.option(
    "--no-properties",
    is_flag=True,
    help="Hide operator properties in tree view",
)
@click.option(
    "--compact",
    is_flag=True,
    help="Use compact tree format",
)
@click.option(
    "--max-depth",
    type=int,
    help="Maximum tree depth to display",
)
@click.pass_context
def show_plan(
    ctx,
    run_path: Path,
    query_id: str,
    output_format: str,
    no_properties: bool,
    compact: bool,
    max_depth: int | None,
):
    try:
        results, _ = load_result_file(run_path)

        query_exec = _find_query(results, query_id)

        if not query_exec:
            console.print(f"[red]Error:[/red] Query '{query_id}' not found in results")
            ctx.exit(1)

        if query_exec.get("query_plan") is None and query_exec.get("query_plan_raw") is not None:
            _print_raw_plan(query_id, query_exec["query_plan_raw"], output_format)
            return

        if query_exec.get("query_plan") is None:
            plans_load_error = getattr(results, "plans_load_error", None)
            if plans_load_error:
                console.print(f"[red]Error:[/red] plans file exists but failed to load: {plans_load_error}")
            else:
                console.print(f"[yellow]Warning:[/yellow] No query plan captured for query '{query_id}'")
                console.print("Run benchmark with --capture-plans flag to capture query plans")
            ctx.exit(1)

        plan = query_exec["query_plan"]

        if output_format == "json":
            output = json.dumps(plan.to_dict(), indent=2)
            console.print(output)

        elif output_format == "summary":
            output = render_summary(plan)
            console.print(Panel(output, title=f"Query Plan Summary: {query_id}", border_style="cyan"))

        else:
            options = VisualizationOptions(
                show_properties=not no_properties,
                compact=compact,
                max_depth=max_depth,
            )
            output = render_plan(plan, options)
            console.print(Panel(output, title=f"Query Plan: {query_id}", border_style="green"))

    except FileNotFoundError:
        console.print(f"[red]Error:[/red] Results file not found: {run_path}")
        ctx.exit(1)
    except json.JSONDecodeError as e:
        console.print(f"[red]Error:[/red] Invalid JSON in results file: {e}")
        ctx.exit(1)
    except KeyError as e:
        console.print(f"[red]Error:[/red] Missing required field in results: {e}")
        ctx.exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        if ctx.obj and ctx.obj.get("verbose"):
            raise
        ctx.exit(1)


__all__ = ["show_plan"]
