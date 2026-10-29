from __future__ import annotations

import json
import sys
from pathlib import Path

import click

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from _project.scripts.explorer_pipeline.contract import (
    EXPLORER_BUILD_CONTRACT,
    EXPLORER_READ_MODEL_VERSION,
    SELECTABLE_READ_MODEL_VERSIONS,
)
from benchbox.cli.shared import console

EXPLORER_PUBLISH_HELP = "Manage the Results Explorer static build pipeline."


EXPLORER_BUILD_HELP = (
    "Build the results explorer static dataset from schema-v2 bundles.\n"
    "\n"
    "Scans DATA_DIR/bundles/ for result JSON files, transforms them into the\n"
    "explorer read model, and writes the output to OUTPUT_DIR:\n"
    "\n"
    "\x08\n"
    "- results.duckdb          DuckDB-WASM queryable snapshot\n"
    "- bundles/{id}.json       copied source bundles for download/audit links\n"
    "\n"
    "Examples:\n"
    "\n"
    "\x08\n"
    "  uv run -- python _project/scripts/explorer_publish.py build \\\n"
    "    --data-dir results-data/ \\\n"
    "    --output explorer-data/\n"
    "\n"
    "\x08\n"
    "  uv run -- python _project/scripts/explorer_publish.py build \\\n"
    "    --data-dir results-data/ \\\n"
    "    --output explorer-data/ \\\n"
    "    --trust-label community-submission \\\n"
    "    --visibility public-self-reported"
)


EXPLORER_BUILD_CONTRACT_HELP = "Emit the stable contract metadata for explorer-build integrations."


@click.group(help=EXPLORER_PUBLISH_HELP)
def explorer_publish() -> None:
    pass


@explorer_publish.command("build", help=EXPLORER_BUILD_HELP)
@click.option(
    "--data-dir",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Root data directory containing bundles/ sub-directory.",
)
@click.option(
    "--output",
    "output_dir",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Output directory for results.duckdb and copied bundles.",
)
@click.option(
    "--trust-label",
    default="maintainer-run",
    show_default=True,
    help="Trust label to attach to every result entry.",
)
@click.option(
    "--visibility",
    default="public-curated",
    show_default=True,
    help="Visibility label to attach to every result entry.",
)
@click.option(
    "--read-model-version",
    type=click.Choice([str(version) for version in SELECTABLE_READ_MODEL_VERSIONS]),
    default=str(EXPLORER_READ_MODEL_VERSION),
    show_default=True,
    help="Read model to emit. 18 adds the per-run SQL translator columns.",
)
def explorer_build(
    data_dir: Path,
    output_dir: Path,
    trust_label: str,
    visibility: str,
    read_model_version: str,
) -> None:
    from _project.scripts.explorer_pipeline.pipeline import ExplorerPipeline

    console.print("[bold]Explorer build[/bold]")
    console.print(f"  Data dir:    [cyan]{data_dir}[/cyan]")
    console.print(f"  Output dir:  [cyan]{output_dir}[/cyan]")
    console.print(f"  Trust label: [dim]{trust_label}[/dim]")
    console.print(f"  Visibility:  [dim]{visibility}[/dim]")
    console.print()

    pipeline = ExplorerPipeline()
    try:
        stats = pipeline.run(
            data_dir=data_dir,
            output_dir=output_dir,
            trust_label=trust_label,
            visibility=visibility,
            read_model_version=int(read_model_version),
        )
        skipped_note = f" ({stats.skipped} skipped)" if stats.skipped else ""
        console.print(
            f"[green]Done.[/green] Processed {stats.processed} result(s){skipped_note} "
            f"across {stats.cohorts} cohort(s) into {stats.output_dir}"
        )
    except Exception as exc:
        console.print(f"[red]Pipeline failed: {exc}[/red]")
        raise SystemExit(1) from exc


@explorer_publish.command("build-contract", hidden=True, help=EXPLORER_BUILD_CONTRACT_HELP)
def explorer_build_contract() -> None:

    click.echo(json.dumps(EXPLORER_BUILD_CONTRACT))


def main() -> None:
    explorer_publish()


if __name__ == "__main__":
    main()


__all__ = ["explorer_build", "explorer_build_contract", "explorer_publish", "main"]
