# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

import click
from rich.markup import escape

from benchbox.cli.output import ResultExporter
from benchbox.cli.pseudonym_salt import ensure_publication_salt
from benchbox.cli.shared import console
from benchbox.core.publishing.admission import publish_admission
from benchbox.core.publishing.bundle_publisher import COMPANION_SUFFIXES, VALID_LABELS, BundlePublisher
from benchbox.core.publishing.store import PublicationStore
from benchbox.core.results.anonymization import AnonymizationConfig, MissingPublicPseudonymSaltError
from benchbox.core.results.loader import ResultLoadError, UnsupportedSchemaError, load_result_file
from benchbox.validation.bundle import OVERRIDE_SUFFIX, unanonymized_plans_findings, unanonymized_tuning_findings


@click.group(
    "publish",
    help=(
        "Publish and track schema-v2 result bundles.\n"
        "\n"
        "Copies an already-exported result bundle to a storage destination and\n"
        "records a durable reference in the publication history. Distinct from\n"
        "'benchbox export', which serialises live benchmark results to disk.\n"
        "\n"
        "\b\n"
        "Backends and reference types:\n"
        "  local path         -> file:///abs/path/to/bundle.json\n"
        "  s3://bucket/prefix -> s3://bucket/prefix/bundle.json\n"
        "  gs://bucket/prefix -> gs://bucket/prefix/bundle.json\n"
        "  abfss://...        -> abfss://.../bundle.json\n"
        "\n"
        "\b\n"
        "Examples:\n"
        "  benchbox publish results/tpch_sf1_duckdb.json\n"
        "  benchbox publish results/tpch_sf1_duckdb.json --target /mnt/shared/benchbox\n"
        "  benchbox publish results/tpch_sf1_duckdb.json --target s3://my-bucket/benchbox\n"
        "  benchbox publish list\n"
        "  benchbox publish show abc123def456\n"
        "  benchbox publish remove abc123def456"
    ),
)
def publish() -> None:
    pass


@publish.command(
    "run",
    help=(
        "Publish a schema-v2 result bundle to a storage destination.\n"
        "\n"
        "RESULT_FILE: Path to the primary .json result file (optional; use --last for auto-select)."
    ),
)
@click.argument("result_file", required=False, type=click.Path())
@click.option(
    "--target",
    default="benchmark_runs/published",
    show_default=True,
    help="Destination directory or cloud URI (s3://, gs://, abfss://).",
)
@click.option(
    "--label",
    type=click.Choice(list(VALID_LABELS), case_sensitive=False),
    default="maintainer-run",
    show_default=True,
    help="Trust / provenance label for this publication.",
)
@click.option(
    "--last",
    is_flag=True,
    help="Publish the most recent result file.",
)
@click.option("--benchmark", type=str, help="Filter by benchmark name when using --last.")
@click.option("--platform", type=str, help="Filter by platform name when using --last.")
@click.option("--dry-run", is_flag=True, help="Preview without publishing.")
@click.pass_context
def publish_run(ctx, result_file, target, label, last, benchmark, platform, dry_run):
    source_path = _resolve_source(result_file, last, benchmark, platform)
    if source_path is None:
        return

    try:
        _result, _ = load_result_file(source_path)
        _decision = publish_admission(_result, label)
        if not _decision.allowed:
            if _decision.code == "unofficial_compliance":
                console.print(
                    f"\n[red]❌ Publish refused: compliance_class={_decision.reason}[/red]\n"
                    "   This result was produced with an unofficial TPC-DS configuration.\n"
                    "   To publish anyway, add: [bold]--label unofficial-research[/bold]\n"
                    "   Note: unofficial results must not be used for comparative benchmarking.\n"
                    "   See _sources/tpcds-subscale-contract.md for details."
                )
            else:
                console.print(
                    f"\n[red]❌ Publish refused: result is not a clean pass ({_decision.reason})[/red]\n"
                    "   Partial runs remain available as local artifacts but are not publishable."
                )
            raise SystemExit(1)
    except (ResultLoadError, UnsupportedSchemaError, FileNotFoundError):
        pass

    if dry_run:
        console.print(f"[bold]Dry run - would publish:[/bold] {source_path}")
        console.print(f"  Target:  {target}")
        console.print(f"  Label:   {label}")
        companion_count = _count_companions(source_path)
        if companion_count:
            console.print(f"  + {companion_count} companion file(s)")
        return

    publish_source, scratch = _redacted_publish_source(source_path)
    if publish_source is None:
        console.print("[red]Publish refused:[/red] could not produce a complete anonymized bundle.")
        raise SystemExit(1)
    companion_count = _count_companions(publish_source)
    try:
        store = PublicationStore()
        publisher = BundlePublisher(destination=target, store=store, label=label)
        result = publisher.publish(publish_source, record_source=source_path)
    finally:
        if scratch is not None:
            scratch.cleanup()
    if not result.success:
        for err in result.errors:
            console.print(f"[red]Error:[/red] {err}")
        raise SystemExit(1)

    if result.errors:
        for err in result.errors:
            console.print(f"[yellow]Warning:[/yellow] {err}")

    console.print(f"[green]Published:[/green] {publish_source.name}")
    if result.record:
        console.print(f"  ID:        {result.record.pub_id}")
    console.print(f"  Reference: {result.reference}")
    console.print(f"  Label:     {label}")

    if companion_count:
        console.print(f"  + {companion_count} companion file(s) also published")


@publish.command("list", help=("List published artifacts from the publication history."))
@click.option("--benchmark", type=str, help="Filter by benchmark name.")
@click.option("--platform", type=str, help="Filter by platform name.")
@click.option("--label", type=str, help="Filter by label.")
def publish_list(benchmark, platform, label):
    store = PublicationStore()
    records = store.list_all()

    if benchmark:
        records = [r for r in records if benchmark.lower() in r.benchmark.lower()]
    if platform:
        records = [r for r in records if platform.lower() in r.platform.lower()]
    if label:
        records = [r for r in records if r.label == label]

    if not records:
        console.print("[yellow]No publications found.[/yellow]")
        console.print("[dim]Tip: Use 'benchbox publish results/your-result.json' to publish a result.[/dim]")
        return

    console.print(f"\n[bold]Published artifacts ({len(records)} total)[/bold]")
    console.print(f"Store: [dim]{store.store_path}[/dim]\n")

    try:
        from rich.table import Table

        table = Table(show_header=True)
        table.add_column("ID", style="cyan", width=14)
        table.add_column("Benchmark", style="green")
        table.add_column("Platform", style="blue")
        table.add_column("Label", style="dim")
        table.add_column("Published At", style="dim")
        table.add_column("Reference", style="dim")

        for rec in records:
            ts = rec.published_at[:19].replace("T", " ") if rec.published_at else ""
            ref = rec.reference
            if len(ref) > 60:
                ref = "..." + ref[-57:]
            table.add_row(rec.pub_id, rec.benchmark, rec.platform, rec.label, ts, ref)

        console.print(table)
    except Exception:
        for rec in records:
            ts = rec.published_at[:19].replace("T", " ") if rec.published_at else ""
            console.print(f"  {rec.pub_id}  {rec.benchmark}/{rec.platform}  {ts}")
            console.print(f"    -> {rec.reference}")


@publish.command("show", help=("Show details of a published artifact by ID."))
@click.argument("pub_id")
def publish_show(pub_id):
    store = PublicationStore()
    rec = store.get(pub_id)

    if rec is None:
        console.print(f"[red]No publication found with ID: {pub_id}[/red]")
        raise SystemExit(1)

    console.print(f"\n[bold]Publication {rec.pub_id}[/bold]")
    console.print(f"  Benchmark:    {rec.benchmark or '(unknown)'}")
    console.print(f"  Platform:     {rec.platform or '(unknown)'}")
    console.print(f"  Scale factor: {rec.scale_factor}")
    console.print(f"  Label:        {rec.label}")
    console.print(f"  Published at: {rec.published_at}")
    console.print(f"  Source:       {rec.source_path}")
    console.print(f"  Destination:  {rec.destination}")
    console.print(f"  Reference:    {rec.reference}")


@publish.command(
    "remove",
    help=(
        "Remove a publication record.\n"
        "\n"
        "This removes the metadata entry only. The underlying artifact files are\n"
        "NOT deleted from the destination.\n"
        "\n"
        "PUB_ID: The publication ID to remove (from 'benchbox publish list')."
    ),
)
@click.argument("pub_id")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
def publish_remove(pub_id, yes):
    store = PublicationStore()
    rec = store.get(pub_id)

    if rec is None:
        console.print(f"[red]No publication found with ID: {pub_id}[/red]")
        raise SystemExit(1)

    console.print("[bold]Publication to remove:[/bold]")
    console.print(f"  ID:        {rec.pub_id}")
    console.print(f"  Benchmark: {rec.benchmark}/{rec.platform}")
    console.print(f"  Reference: {rec.reference}")
    console.print("[dim]Note: artifact files are NOT deleted from the destination.[/dim]")

    if not yes:
        if not click.confirm("\nRemove this publication record?", default=False):
            console.print("[yellow]Cancelled.[/yellow]")
            return

    store.remove(pub_id)
    console.print(f"[green]Removed publication {pub_id}[/green]")


def _resolve_source(
    result_file: str | None,
    last: bool,
    benchmark: str | None,
    platform: str | None,
) -> Path | None:
    if result_file:
        path = Path(result_file)
        if not path.exists():
            console.print(f"[red]File not found: {result_file}[/red]")
            return None
        return path

    if last:
        from benchbox.core.results.loader import find_latest_result

        default_results_dir = Path("benchmark_runs/results")
        path = find_latest_result(default_results_dir, benchmark=benchmark, platform=platform)
        if not path:
            console.print("[yellow]No results found.[/yellow]")
            if benchmark or platform:
                filters = []
                if benchmark:
                    filters.append(f"benchmark={benchmark}")
                if platform:
                    filters.append(f"platform={platform}")
                console.print(f"  Filters: {', '.join(filters)}")
            return None
        console.print(f"[blue]Using latest result:[/blue] {path.name}")
        return path

    console.print("[yellow]Please specify a result file or use --last.[/yellow]")
    console.print("\n[bold]Examples:[/bold]")
    console.print("  benchbox publish run results/tpch_sf1_duckdb.json")
    console.print("  benchbox publish run --last --benchmark tpch")
    console.print("  benchbox publish list")
    return None


def _source_bundle_is_clean_and_complete(source: Path, payload: object) -> bool | None:
    if not isinstance(payload, dict):
        return False
    export_data = payload.get("export")
    if (
        not isinstance(export_data, dict)
        or export_data.get("anonymized") is not True
        or unanonymized_tuning_findings(payload)
        or unanonymized_plans_findings(payload)
    ):
        return False
    for suffix in COMPANION_SUFFIXES:
        companion = source.parent / (source.stem + suffix)
        if not companion.exists():
            continue
        try:
            companion_payload = json.loads(companion.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(companion_payload, dict):
            return None
        if unanonymized_tuning_findings(companion_payload) or unanonymized_plans_findings(companion_payload):
            return False
    return True


def _redacted_publish_source(source_bundle: Path) -> tuple[Path | None, TemporaryDirectory[str] | None]:
    source = Path(source_bundle)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    source_status = _source_bundle_is_clean_and_complete(source, payload)
    if source_status is None:
        return None, None
    if source_status:
        return source, None
    try:
        loaded, _ = load_result_file(source_bundle)
    except (ResultLoadError, UnsupportedSchemaError, FileNotFoundError, OSError, ValueError):
        return None, None
    try:
        salt = ensure_publication_salt()
    except MissingPublicPseudonymSaltError as exc:
        console.print(f"[red]Publish refused:[/red] {escape(str(exc))}")
        return None, None
    scratch = TemporaryDirectory(prefix="benchbox-publish-")
    try:
        loaded.output_filename = Path(source_bundle).name
        exporter = ResultExporter(
            output_dir=scratch.name,
            anonymize=True,
            anonymization_config=AnonymizationConfig(machine_id_salt=salt),
        )
        exported = exporter.export_result(loaded, ["json"])
    except Exception:
        scratch.cleanup()
        return None, None
    redacted = exported.get("json")
    if redacted is None:
        scratch.cleanup()
        return None, None
    redacted_path = Path(redacted)
    if not _redacted_bundle_is_complete_and_clean(source, redacted_path):
        scratch.cleanup()
        return None, None
    return redacted_path, scratch


def _redacted_bundle_is_complete_and_clean(source: Path, redacted_path: Path) -> bool:
    for suffix in COMPANION_SUFFIXES:
        source_companion = source.parent / (source.stem + suffix)
        if not source_companion.exists():
            continue
        output_companion = redacted_path.parent / (redacted_path.stem + suffix)
        if suffix == OVERRIDE_SUFFIX:
            if source_companion.is_symlink() or not source_companion.is_file():
                return False
            try:
                shutil.copyfile(source_companion, output_companion)
            except OSError:
                return False
            continue
        if output_companion.is_file():
            continue
        if suffix in (".plans.json", ".tuning.json", ".applied.json"):
            if _copy_clean_companion(source_companion, output_companion) is not True:
                return False
        else:
            return False
    if not _redacted_output_is_clean(redacted_path, require_anonymized_marker=True):
        return False
    for suffix in COMPANION_SUFFIXES:
        if suffix == OVERRIDE_SUFFIX:
            continue
        output_companion = redacted_path.parent / (redacted_path.stem + suffix)
        if output_companion.exists() and not _redacted_output_is_clean(output_companion):
            return False
    return True


def _copy_clean_companion(source: Path, destination: Path) -> bool | None:
    if source.is_symlink() or not source.is_file():
        return None
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if unanonymized_tuning_findings(payload) or unanonymized_plans_findings(payload):
        return False
    try:
        shutil.copyfile(source, destination)
    except OSError:
        return None
    return True


def _redacted_output_is_clean(path: Path, *, require_anonymized_marker: bool = False) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    if require_anonymized_marker:
        export_data = payload.get("export")
        if not isinstance(export_data, dict) or export_data.get("anonymized") is not True:
            return False
    return not unanonymized_tuning_findings(payload) and not unanonymized_plans_findings(payload)


def _count_companions(source: Path) -> int:
    stem = source.stem
    return sum(1 for s in COMPANION_SUFFIXES if (source.parent / (stem + s)).exists())


def publish_bundle(
    source_bundle: Path,
    target: str = "benchmark_runs/published",
    label: str = "maintainer-run",
    quiet: bool = False,
) -> str | None:
    if label not in VALID_LABELS:
        console.print(
            f"[red]Publish refused:[/red] invalid trust label '{label}'. Must be one of: {', '.join(VALID_LABELS)}."
        )
        return None

    try:
        _result, _ = load_result_file(source_bundle)
        _decision = publish_admission(_result, label)
        if not _decision.allowed:
            if _decision.code == "unofficial_compliance":
                console.print(
                    "[red]Publish refused:[/red] This result has compliance_class "
                    f"'{_decision.reason}' and cannot be published under label "
                    f"'{label}'. Add [bold]--label unofficial-research[/bold] to "
                    "publish unofficial TPC-DS results."
                )
            else:
                console.print(
                    "[red]Publish refused:[/red] This result is not a clean pass "
                    f"({_decision.reason}) and cannot be published."
                )
            return None
    except (ResultLoadError, UnsupportedSchemaError, FileNotFoundError):
        pass

    publish_source, scratch = _redacted_publish_source(Path(source_bundle))
    if publish_source is None:
        console.print("[red]Publish refused:[/red] could not produce a complete anonymized bundle.")
        return None
    try:
        store = PublicationStore()
        publisher = BundlePublisher(destination=target, store=store, label=label)
        result = publisher.publish(publish_source, record_source=Path(source_bundle))
    finally:
        if scratch is not None:
            scratch.cleanup()

    if not result.success:
        for err in result.errors:
            console.print(f"[red]Publish error:[/red] {err}")
        return None

    if result.errors and not quiet:
        for err in result.errors:
            console.print(f"[yellow]Publish warning:[/yellow] {err}")

    if not quiet:
        console.print(f"[green]Published:[/green] {result.reference}")

    return result.reference


__all__ = ["publish", "publish_bundle"]
