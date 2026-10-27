from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import click

import benchbox
from benchbox.cli.shared import console
from benchbox.cli.submit_auth import (
    DEFAULT_SERVICE_URL as _DEFAULT_SERVICE_URL,
    SubmissionAuthError,
    refresh_submission_token,
    resolve_submission_token,
)
from benchbox.cli.submit_service import (
    HostedSubmitError,
    HostedSubmitResult,
    HostedSubmitUnauthorized,
    submit_hosted_bundle,
)
from benchbox.core.results.anonymization import (
    AnonymizationConfig,
    AnonymizationManager,
    MissingPublicPseudonymSaltError,
    anonymize_applied_tuning_payload,
    anonymize_plans_payload,
    require_public_pseudonym_salt,
)
from benchbox.core.results.canonical_json import canonical_json_file_bytes, canonical_json_text
from benchbox.core.results.loader import (
    ResultLoadError,
    UnsupportedSchemaError,
    find_latest_result,
    load_result_file,
)
from benchbox.core.results.provenance import FUNDING_SOURCES, normalize_funding
from benchbox.core.results.status import result_non_clean_reason, result_unvalidated_reason
from benchbox.core.results.submission_history import record_hosted_submission
from benchbox.core.results.submit_classification import (
    SubmitTerminalState,
    classify_loaded_result,
)
from benchbox.core.results.subscale_policy import result_compliance_class
from benchbox.validation.bundle import (
    COMPANION_SUFFIXES,
    SUBMISSION_NOTES_MAX_LEN,
    _hash_bytes,
    _hash_file,
    export_anonymized_errors,
    find_public_path_leaks,
    format_summary,
    unanonymized_plans_findings,
    unanonymized_tuning_findings,
    unsatisfied_override_rules,
    validate_bundles,
)

_SUBMISSION_PHASE = 2

_VISIBILITY_CHOICES = ("public", "unlisted", "private")

_CONTRIBUTING_TEXT = """\
# Contributing a Benchmark Result

Thank you for contributing to the BenchBox community results dataset!

This is a packaged checklist. The full guide lives at:
https://benchbox.dev/docs/contributing-results.html

## Quick checklist

1. Fork https://github.com/BenchBox-dev/BenchBox (or use your existing fork).
2. Copy the contents of `bundle/` into `results-data/bundles/` in your fork.
3. Copy the generated `<result>.manifest.json` alongside the bundle files.
4. Regenerate the corpus inventory before you commit:

       uv run -- python scripts/generate_corpus_inventory.py --write

5. (Optional) Validate the bundle locally before pushing:

       uv run -- python scripts/validate_submission.py path/to/bundle.json

6. Open a pull request against the **`published-results`** branch (NOT
   `main`) with the title:
   `results: <benchmark> <platform> sf<scale>`
7. CI (Validate Submission) verifies the bundle hash against the manifest
   and posts a summary comment on your PR.

## Questions?

Open an issue or start a discussion at
https://github.com/BenchBox-dev/BenchBox.

For the full guide (trust labels, quality expectations, troubleshooting),
see https://benchbox.dev/docs/contributing-results.html.
"""


def _get_git_username() -> str:
    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _resolve_submitted_by(explicit: str | None) -> str:
    if explicit:
        return explicit.strip()
    from_git = _get_git_username()
    if from_git:
        return from_git
    console.print(
        "[yellow]warning:[/yellow] submitted_by is empty - "
        "set `git config user.name <name>` or pass `--submitted-by NAME`."
    )
    return ""


def _canonical_submission_file_bytes(file_path: Path) -> bytes:

    try:
        return canonical_json_file_bytes(file_path)
    except PermissionError:
        raise PermissionError(f"Cannot read file for canonicalization: {file_path}") from None
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Submission file is not valid JSON: {file_path}: {exc}") from exc


def _write_canonical_submission_file(source_path: Path, target_path: Path) -> Path:

    target_path.write_bytes(_canonical_submission_file_bytes(source_path))
    return target_path


def _build_submission_manifest(
    *,
    source_path: Path,
    companions: list[Path],
    result,
    submitted_by: str | None,
    submission_path: str,
    funding: str | None = None,
    submission_notes: str | None = None,
) -> dict:

    resolved_funding = normalize_funding(funding or getattr(result, "funding", None))

    manifest = {
        "submission_tool_version": f"benchbox/{benchbox.__version__}",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "bundle_file": source_path.name,
        "bundle_hash": _hash_file(source_path),
        "companion_hashes": {comp.name: _hash_file(comp) for comp in companions},
        "benchmark": result.benchmark_name,
        "platform": result.platform,
        "scale_factor": result.scale_factor,
        "phase": _SUBMISSION_PHASE,
        "submission_path": submission_path,
        "submitted_by": _resolve_submitted_by(submitted_by),
        "result_source": "community",
        "funding": resolved_funding,
    }
    if submission_notes:
        manifest["submission_notes"] = submission_notes
    return manifest


def _require_public_pseudonym_salt(ctx: click.Context) -> str:
    try:
        return require_public_pseudonym_salt()
    except MissingPublicPseudonymSaltError as exc:
        console.print(f"\n[red]❌ Submission refused: {exc}[/red]")
        ctx.exit(1)


def _anonymize_bundle_payload(payload: dict[str, object], manager: AnonymizationManager) -> dict[str, object]:
    working = dict(payload)
    export = working.pop("export", None)
    anonymized = manager.anonymize_result_payload(working)
    anonymized["export"] = {**(export if isinstance(export, dict) else {}), "anonymized": True}
    errors = export_anonymized_errors(anonymized)
    if errors:
        raise click.ClickException(f"Bundle anonymization failed: {'; '.join(errors)}")
    findings = [f"private path leak at {leak}" for leak in sorted(set(find_public_path_leaks(anonymized)))]
    findings += unanonymized_plans_findings(anonymized) + unanonymized_tuning_findings(anonymized)
    if findings:
        raise click.ClickException(f"Bundle anonymization failed: {'; '.join(findings)}")
    return anonymized


def _anonymize_companion_file(path: Path, manager: AnonymizationManager) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise click.ClickException(f"Companion file is not valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise click.ClickException(f"Companion file is not a JSON object: {path.name}")
    if path.name.endswith(".tuning.json"):
        anonymized = manager.anonymize_tuning_payload(payload)
    elif path.name.endswith(".applied.json"):
        anonymized = anonymize_applied_tuning_payload(payload)
    elif path.name.endswith(".plans.json"):
        anonymized = anonymize_plans_payload(payload)
    else:
        anonymized = manager.anonymize_result_payload(payload)
    findings = [f"private path leak at {leak}" for leak in sorted(set(find_public_path_leaks(anonymized)))]
    findings += unanonymized_plans_findings(anonymized) + unanonymized_tuning_findings(anonymized)
    if findings:
        raise click.ClickException(f"{path.name}: {'; '.join(findings)}")
    return anonymized


def _stage_submission_bundle(
    source_path: Path,
    companions: list[Path],
    manager: AnonymizationManager,
    staging_dir: Path,
) -> tuple[Path, list[Path]]:
    try:
        raw_payload = json.loads(source_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise click.ClickException(f"Result file is not valid JSON: {source_path}: {exc}") from exc
    if not isinstance(raw_payload, dict):
        raise click.ClickException(f"Result file is not a JSON object: {source_path}")

    staged_source_path = staging_dir / source_path.name
    anonymized_payload = _anonymize_bundle_payload(raw_payload, manager)
    staged_source_path.write_text(canonical_json_text(anonymized_payload), encoding="utf-8")

    staged_companions: list[Path] = []
    for comp in companions:
        staged_comp = staging_dir / comp.name
        comp_anonymized = _anonymize_companion_file(comp, manager)
        staged_comp.write_text(canonical_json_text(comp_anonymized), encoding="utf-8")
        staged_companions.append(staged_comp)

    return staged_source_path, staged_companions


def _refuse_non_submittable_result(ctx: click.Context, result: object, submit_state: SubmitTerminalState) -> None:
    if submit_state is SubmitTerminalState.unvalidated:
        unvalidated_reason = result_unvalidated_reason(result) or result_non_clean_reason(result)
        status = None
        if unvalidated_reason and unvalidated_reason.startswith("validation_status="):
            status = unvalidated_reason.split("=", 1)[1]
        if status == "uncertain":
            detail = (
                "   Validation completed with an uncertain correctness claim; the result is\n"
                "   not eligible for public submission until validation produces a clean pass."
            )
        elif status in {"not_run", "not_validated", "unknown"}:
            detail = (
                "   Validation never executed (or was skipped); the result is not eligible\n"
                "   for public submission until validation produces a clean pass."
            )
        else:
            detail = (
                "   The result is not a clean validation pass and is not eligible for public\n"
                "   submission until validation produces a clean pass."
            )
        console.print(f"\n[red]❌ Submission refused: result is unvalidated ({unvalidated_reason})[/red]\n{detail}")
    else:
        non_clean_reason = result_non_clean_reason(result)
        console.print(
            f"\n[red]❌ Submission refused: result is not a clean pass ({non_clean_reason})[/red]\n"
            "   Query-level failures remain visible in the result artifact, but\n"
            "   partial runs are not eligible for public submission or leaderboard display."
        )
    ctx.exit(1)


def _validate_submission_bundle(ctx: click.Context, source_path: Path) -> None:

    try:
        validation_results = validate_bundles([source_path])
    except Exception as exc:
        console.print(f"\n[red]Submission validation failed to run:[/red] {exc}")
        ctx.exit(1)
        return

    if not validation_results:
        console.print(f"\n[red]Submission validation failed:[/red] no bundle file validated for {source_path}.")
        ctx.exit(1)
        return

    if any(not result.ok for result in validation_results):
        console.print("\n[red]Submission validation failed:[/red]")
        console.print(format_summary(validation_results))
        ctx.exit(1)
        return

    pending = unsatisfied_override_rules(validation_results)
    if pending:
        console.print("\n[red]Submission validation failed:[/red]")
        for path, rule_ids in sorted(pending.items()):
            console.print(
                f"ERROR: {path} requires overrides: {', '.join(sorted(rule_ids))}",
                soft_wrap=True,
            )
        console.print(format_summary(validation_results))
        ctx.exit(1)
        return


def _dispatch_service_mode(
    ctx: click.Context,
    *,
    source_path: Path,
    companions: list[Path],
    result,
    service_url: str,
    visibility: str,
    idempotency_key: str | None,
    wait: bool,
    dry_run: bool,
    submitted_by: str | None,
    funding: str | None = None,
    submission_notes: str | None = None,
    original_result_file: Path | None = None,
) -> None:
    bundle_bytes = _canonical_submission_file_bytes(source_path)
    companion_bytes = {comp.name: _canonical_submission_file_bytes(comp) for comp in companions}
    bundle_size = len(bundle_bytes)
    bundle_hash = _hash_bytes(bundle_bytes)
    companion_hashes = {name: _hash_bytes(data) for name, data in companion_bytes.items()}

    if dry_run:
        console.print("\n[bold]Dry-run - would upload:[/bold]")
        console.print(f"  Service URL:      {service_url}")
        console.print(f"  Bundle file:      {source_path.name} ({bundle_size:,} bytes)")
        console.print(f"  Bundle hash:      {bundle_hash}")
        if companions:
            console.print("  Companions:")
            for comp in companions:
                console.print(f"    {comp.name}  {companion_hashes[comp.name]}")
        else:
            console.print("  Companions:       (none)")
        console.print(f"  Visibility:       {visibility}")
        console.print(f"  Idempotency key:  {idempotency_key or '(auto-generated at upload time)'}")
        console.print(f"  Wait for accept:  {wait}")
        console.print("\n[yellow]Dry-run complete - no bytes sent.[/yellow]")
        return

    try:
        token = resolve_submission_token(service_url)
    except SubmissionAuthError as exc:
        console.print(f"\n[red]Authentication required:[/red] {exc}")
        ctx.exit(1)
        return

    with TemporaryDirectory(prefix="benchbox-submit-") as tmp_dir:
        canonical_dir = Path(tmp_dir)
        upload_source_path = canonical_dir / source_path.name
        upload_source_path.write_bytes(bundle_bytes)
        upload_companions: list[Path] = []
        for comp in companions:
            upload_companion = canonical_dir / comp.name
            upload_companion.write_bytes(companion_bytes[comp.name])
            upload_companions.append(upload_companion)

        manifest = _build_submission_manifest(
            source_path=upload_source_path,
            companions=upload_companions,
            result=result,
            submitted_by=submitted_by,
            funding=funding,
            submission_notes=submission_notes,
            submission_path="hosted-service",
        )
        bundle_hash = manifest["bundle_hash"]

        console.print("\n[bold]Uploading submission bundle:[/bold]")
        console.print(f"  Service URL:      {service_url}")
        console.print(f"  Bundle file:      {source_path.name} ({bundle_size:,} bytes)")
        console.print(f"  Bundle hash:      {bundle_hash}")
        console.print(f"  Visibility:       {visibility}")

        try:
            upload_result = submit_hosted_bundle(
                service_url=service_url,
                token=token.token,
                source_path=upload_source_path,
                companions=upload_companions,
                manifest=manifest,
                bundle_hash=bundle_hash,
                visibility=visibility,
                idempotency_key=idempotency_key,
                wait=wait,
            )
        except HostedSubmitUnauthorized:
            try:
                refreshed_token = refresh_submission_token(service_url)
                upload_result = submit_hosted_bundle(
                    service_url=service_url,
                    token=refreshed_token.token,
                    source_path=upload_source_path,
                    companions=upload_companions,
                    manifest=manifest,
                    bundle_hash=bundle_hash,
                    visibility=visibility,
                    idempotency_key=idempotency_key,
                    wait=wait,
                )
            except (HostedSubmitError, SubmissionAuthError) as exc:
                console.print(f"\n[red]Hosted submission failed:[/red] {exc}")
                ctx.exit(1)
                return
        except HostedSubmitError as exc:
            console.print(f"\n[red]Hosted submission failed:[/red] {exc}")
            ctx.exit(1)
            return

    try:
        history_path = record_hosted_submission(
            result_file=original_result_file or source_path,
            service_url=service_url,
            manifest=manifest,
            status=upload_result.status,
            idempotency_key=upload_result.idempotency_key,
            submission_id=upload_result.submission_id,
            public_result_id=upload_result.public_result_id,
            public_url=upload_result.public_url,
        )
    except OSError as exc:
        console.print(f"[yellow]warning:[/yellow] could not save hosted submission history: {exc}")
        history_path = None

    _print_service_result(upload_result, wait=wait)
    if history_path is not None:
        console.print(f"  History:          {history_path}")


def _print_service_result(result: HostedSubmitResult, *, wait: bool) -> None:
    is_terminal = result.status in {"already_published", "published"} or result.public_url is not None
    if is_terminal:
        console.print("\n[bold green]Hosted submission complete.[/bold green]")
    else:
        console.print("\n[bold green]Hosted submission accepted.[/bold green]")
    console.print(f"  Status:           {result.status}")
    if result.submission_id:
        console.print(f"  Submission ID:    {result.submission_id}")
    console.print(f"  Idempotency key:  {result.idempotency_key}")
    if result.status_history:
        console.print(f"  Status history:   {' -> '.join(result.status_history)}")
    if result.public_url:
        console.print(f"  Public URL:       {result.public_url}")
    elif not wait and result.submission_id:
        console.print("  Next step:        rerun with --wait to poll for the public URL")


def _print_submission_summary(
    *,
    bundle_dir: Path,
    source_path: Path,
    companions: list[Path],
    manifest_path: Path,
    contributing_path: Path,
    output_path: Path,
    result,
    dry_run: bool,
) -> None:
    if dry_run:
        console.print("\n[bold]Dry-run preview — would create:[/bold]")
    else:
        console.print("\n[bold green]✓ Submission package created![/bold green]")

    bundle_filename = source_path.name
    bundle_target = f"results-data/bundles/{bundle_filename}"

    console.print(f"  {bundle_dir / bundle_filename}")
    for comp in companions:
        console.print(f"  {bundle_dir / comp.name}")
    console.print(f"  {manifest_path}")
    console.print(f"  {contributing_path}")

    if not dry_run:
        console.print(f"\n[dim]Output: {output_path.absolute()}[/dim]")

    pr_title = f"results: {result.benchmark_name} {result.platform} sf{result.scale_factor}"
    console.print("\n[bold]Next steps:[/bold]")
    console.print(f"  1. cp [cyan]{bundle_dir / bundle_filename}[/cyan] [cyan]{bundle_target}[/cyan]")
    for comp in companions:
        console.print(f"     cp [cyan]{bundle_dir / comp.name}[/cyan] [cyan]results-data/bundles/{comp.name}[/cyan]")
    console.print(f"     cp [cyan]{manifest_path}[/cyan] [cyan]results-data/bundles/{manifest_path.name}[/cyan]")
    console.print("  2. uv run -- python scripts/generate_corpus_inventory.py --write")
    console.print(f"  3. uv run -- python scripts/validate_submission.py {bundle_target}")
    console.print(f"  4. PR title:  [cyan]{pr_title}[/cyan]")
    console.print("     PR target: [cyan]published-results[/cyan]")
    console.print("[dim]Full guide: https://benchbox.dev/docs/contributing-results.html[/dim]")

    if dry_run:
        console.print("\n[yellow](dry run; no files written)[/yellow]")


@click.command(
    "submit",
    help=(
        "Submit a benchmark result bundle to the BenchBox results platform.\n"
        "\n"
        "Two modes, selected by the flag set:\n"
        "\n"
        "  --output PATH (Phase 2, default)\n"
        "    Package the canonical bundle + submission manifest into PATH ready\n"
        "    for opening a PR against the BenchBox repository's results-data/\n"
        "    directory. No network. No credentials. Existing v0.2.x behavior.\n"
        "\n"
        "  --service [URL] (Phase 3)\n"
        "    Upload the canonical bundle to a hosted ingest API. Requires\n"
        "    authentication via 'benchbox auth login'. With --dry-run, validates\n"
        "    the bundle and prints what would be uploaded - no credentials\n"
        "    needed for the dry-run path.\n"
        "\n"
        "RESULT_FILE: Path to result JSON file (optional; with --last, picked\n"
        "from history).\n"
        "\n"
        "\b\n"
        "Examples:\n"
        "    # Package most recent result for PR contribution (Phase 2; default)\n"
        "    benchbox submit --last\n"
        "\n"
        "\b\n"
        "    # Print exact result paths, then package one for PR contribution\n"
        "    benchbox results --paths\n"
        "    benchbox submit benchmark_runs/results/tpch_sf001_duckdb_20260401_120000.json --output ./submission\n"
        "\n"
        "\b\n"
        "    # Submit most recent result to the hosted platform (Phase 3)\n"
        "    benchbox submit --last --service\n"
        "\n"
        "\b\n"
        "    # Submit a specific bundle to a non-default service URL\n"
        "    benchbox submit results/tpch_sf01_duckdb.json --service https://staging.benchbox.dev/v1\n"
        "\n"
        "\b\n"
        "    # Preview what would be uploaded without sending bytes\n"
        "    benchbox submit --last --service --dry-run\n"
        "\n"
        "Note: benchbox submit shares results publicly. To copy a result to\n"
        "storage you control (local path, S3, etc.), use 'benchbox publish'."
    ),
)
@click.argument("result_file", required=False, type=click.Path(exists=True))
@click.option(
    "--last",
    is_flag=True,
    help="Use most recent result file",
)
@click.option(
    "--benchmark",
    type=str,
    help="Filter by benchmark name (with --last)",
)
@click.option(
    "--platform",
    type=str,
    help="Filter by platform name (with --last)",
)
@click.option(
    "--output",
    "output_dir",
    type=click.Path(),
    default="submission",
    show_default=True,
    help="Phase 2 mode: output directory for the PR-package",
)
@click.option(
    "--service",
    "service_url",
    is_flag=False,
    flag_value=_DEFAULT_SERVICE_URL,
    default=None,
    help=(
        "Phase 3 mode: submit to the hosted ingest API. Without a value, "
        f"uses {_DEFAULT_SERVICE_URL}. Without this flag, --output runs."
    ),
)
@click.option(
    "--visibility",
    type=click.Choice(_VISIBILITY_CHOICES),
    default="public",
    show_default=True,
    help="Phase 3 only: visibility of the submitted result.",
)
@click.option(
    "--idempotency-key",
    type=str,
    default=None,
    help="Phase 3 only: override the auto-generated key. Useful for resumable retries.",
)
@click.option(
    "--wait/--no-wait",
    default=True,
    help="Phase 3 only: wait for ingest to finish and print the public URL. [default: --wait]",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview what would be packaged or uploaded without writing files / sending bytes.",
)
@click.option(
    "--submitted-by",
    "submitted_by",
    type=str,
    default=None,
    help=(
        "Override the manifest's submitted_by field. "
        "Precedence: this flag > git config user.name > empty (with warning)."
    ),
)
@click.option(
    "--funding",
    type=click.Choice(FUNDING_SOURCES, case_sensitive=False),
    default=None,
    help=(
        "Disclose how this run was funded. Precedence: this flag > the bundle's "
        "declared provenance.funding > 'unspecified'."
    ),
)
@click.option(
    "--notes",
    "submission_notes",
    type=str,
    default=None,
    help=f"Optional free-text submission notes (max {SUBMISSION_NOTES_MAX_LEN} characters).",
)
@click.pass_context
def submit(
    ctx,
    result_file,
    last,
    benchmark,
    platform,
    output_dir,
    service_url,
    visibility,
    idempotency_key,
    wait,
    dry_run,
    submitted_by,
    funding,
    submission_notes,
):
    if submission_notes is not None and len(submission_notes) > SUBMISSION_NOTES_MAX_LEN:
        console.print(
            f"[red]--notes exceeds {SUBMISSION_NOTES_MAX_LEN} characters "
            f"({len(submission_notes)}). Shorten the note and retry.[/red]"
        )
        ctx.exit(1)
        return

    if result_file:
        source_path = Path(result_file)
    elif last:
        default_results_dir = Path("benchmark_runs/results")
        source_path = find_latest_result(
            default_results_dir,
            benchmark=benchmark,
            platform=platform,
        )

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
            ctx.exit(1)
            return

        console.print(f"[blue]Using latest result:[/blue] {source_path.name}")
    else:
        console.print("[yellow]Please specify a result file or use --last[/yellow]")
        console.print("\n[bold]Examples:[/bold]")
        console.print("  benchbox submit result.json")
        console.print("  benchbox submit --last")
        console.print("  benchbox submit --last --benchmark tpch --dry-run")
        console.print("\n[dim]Tip: Use 'benchbox results' to see available result files[/dim]")
        ctx.exit(1)
        return

    try:
        result, _raw = load_result_file(source_path)
        console.print(f"[green]✓[/green] Loaded: {result.benchmark_name} ({result.platform})")
        console.print(
            f"  Scale: {result.scale_factor}, Queries: {result.total_queries}, Duration: {result.duration_seconds:.2f}s"
        )
    except FileNotFoundError:
        console.print(f"[red]Error: Result file not found: {source_path}[/red]")
        ctx.exit(1)
        return
    except UnsupportedSchemaError as e:
        console.print(f"[red]Error: {e}[/red]")
        ctx.exit(1)
        return
    except ResultLoadError as e:
        console.print(f"[red]Error loading result file: {e}[/red]")
        console.print("[dim]The file may be corrupted or in an invalid format[/dim]")
        ctx.exit(1)
        return
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        ctx.exit(1)
        return

    salt = _require_public_pseudonym_salt(ctx)
    submit_state = classify_loaded_result(result)

    if submit_state is SubmitTerminalState.unofficial:
        _compliance_class = result_compliance_class(result)
        console.print(
            f"\n[red]❌ Submission refused: compliance_class={_compliance_class}[/red]\n"
            "   This result was produced with an unofficial TPC-DS configuration and\n"
            "   must not be submitted as a comparable result. Only results with\n"
            "   compliance_class=official may be submitted.\n"
            "   See _sources/tpcds-subscale-contract.md for details."
        )
        ctx.exit(1)
        return

    if submit_state is not SubmitTerminalState.submittable:
        _refuse_non_submittable_result(ctx, result, submit_state)
        return

    companions = [
        p for suffix in COMPANION_SUFFIXES if (p := source_path.with_name(source_path.stem + suffix)).exists()
    ]
    manager = AnonymizationManager(AnonymizationConfig(machine_id_salt=salt))

    with TemporaryDirectory(prefix="benchbox-submit-staging-") as tmpdir:
        staging_dir = Path(tmpdir)
        staged_source_path, staged_companions = _stage_submission_bundle(
            source_path=source_path,
            companions=companions,
            manager=manager,
            staging_dir=staging_dir,
        )
        _validate_submission_bundle(ctx, staged_source_path)

        if service_url is not None:
            _dispatch_service_mode(
                ctx,
                source_path=staged_source_path,
                companions=staged_companions,
                result=result,
                service_url=service_url,
                visibility=visibility,
                idempotency_key=idempotency_key,
                wait=wait,
                dry_run=dry_run,
                submitted_by=submitted_by,
                funding=funding,
                submission_notes=submission_notes,
                original_result_file=source_path,
            )
            return

        output_path = Path(output_dir)
        bundle_dir = output_path / "bundle"
        manifest_path = output_path / f"{source_path.stem}.manifest.json"
        contributing_path = output_path / "CONTRIBUTING.md"

        if dry_run:
            _print_submission_summary(
                bundle_dir=bundle_dir,
                source_path=staged_source_path,
                companions=staged_companions,
                manifest_path=manifest_path,
                contributing_path=contributing_path,
                output_path=output_path,
                result=result,
                dry_run=True,
            )
            return

        bundle_dir.mkdir(parents=True, exist_ok=True)

        packaged_source_path = _write_canonical_submission_file(staged_source_path, bundle_dir / source_path.name)
        packaged_companions = []
        for comp in staged_companions:
            packaged_companions.append(_write_canonical_submission_file(comp, bundle_dir / comp.name))

        manifest = _build_submission_manifest(
            source_path=packaged_source_path,
            companions=packaged_companions,
            result=result,
            submitted_by=submitted_by,
            funding=funding,
            submission_notes=submission_notes,
            submission_path="PR-based",
        )

        manifest_path.write_text(canonical_json_text(manifest), encoding="utf-8")
        contributing_path.write_text(_CONTRIBUTING_TEXT, encoding="utf-8")

        _print_submission_summary(
            bundle_dir=bundle_dir,
            source_path=packaged_source_path,
            companions=packaged_companions,
            manifest_path=manifest_path,
            contributing_path=contributing_path,
            output_path=output_path,
            result=result,
            dry_run=False,
        )


__all__ = ["submit"]
