from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from benchbox.utils import host_lock
from tests.utilities import session_isolation

Handler = Callable[[argparse.Namespace], int]

MAKE_TARGET_SUBCOMMANDS = (
    "cell",
    "docker-cleanup",
    "execute",
    "gate-check",
    "validate",
    "package",
    "report",
    "sweep",
    "stress",
    "verify-tuning-matrix",
)

LIGHT_SUBCOMMANDS = frozenset(
    {
        "preflight",
        "gate-check",
        "report",
        "replay-classify",
        "package",
        "validate",
        "verify-tuning-matrix",
        "docker-cleanup",
    }
)


def _split_csv(value: str | None) -> list[float] | None:
    return [float(part) for part in value.split(",")] if value else None


def _handle_cell(args: argparse.Namespace) -> int:
    from tests.uat import docker_assets
    from tests.uat.runner import run_cell

    result = run_cell(
        platform=args.platform,
        benchmark=args.benchmark,
        scale=args.scale,
        timeout_s=args.timeout_s,
        phases=args.phases,
        compression=args.compression,
        log_dir=Path(args.log_dir) if args.log_dir else None,
        local_managed_platform=docker_assets.is_docker_platform(args.platform),
    )
    print(
        json.dumps(
            {
                "platform": result.platform,
                "benchmark": result.benchmark,
                "scale": result.scale,
                "status": result.status,
                "exit_code": result.exit_code,
                "elapsed_s": round(result.elapsed_s, 2),
                "log_path": str(result.log_path),
                "result_path": str(result.result_path) if result.result_path else None,
                "submit_terminal_state": result.submit_terminal_state,
            },
            indent=2,
        )
    )
    return 0 if result.status == "passed" else 1


def _handle_sweep(args: argparse.Namespace) -> int:
    from tests.uat.orchestrator import run_sweep_from_path

    platform_shard = [part for part in (args.platforms or "").split(",") if part]
    try:
        result = run_sweep_from_path(
            Path(args.config),
            dry_run_override=True if args.dry_run else None,
            platform_shard=platform_shard or None,
        )
    except ValueError as exc:
        print(f"[sweep] ERROR: {exc}", file=sys.stderr)
        return 2
    payload = {
        "name": result.name,
        "log_dir": str(result.log_dir),
        "aborted_phase": result.aborted_phase,
        "abort_reason": result.abort_reason,
        "phase_exit_codes": result.phase_exit_codes,
    }
    if result.plan is not None:
        payload["planned_cells"] = [f"{c.platform}/{c.benchmark}@{c.scale:g}" for c in result.plan.cells]
        payload["planned_compatibility_pruned"] = [
            f"{c.platform}/{c.benchmark}@{c.scale:g}" for c in result.plan.compatibility_pruned
        ]
    print(json.dumps(payload, indent=2))
    return result.exit_code()


def _handle_stress(args: argparse.Namespace) -> int:
    from tests.uat.orchestrator import run_sweep_from_path

    if args.config is None:
        config_path = Path(__file__).resolve().parent / "configs" / "stress-default.yaml"
    else:
        config_path = Path(args.config)
    stress_overrides = {
        "platform": args.platform,
        "benchmark": args.benchmark,
        "scale": args.scale,
    }
    result = run_sweep_from_path(config_path, stress_overrides=stress_overrides)
    print(
        json.dumps(
            {
                "name": result.name,
                "log_dir": str(result.log_dir),
                "aborted_phase": result.aborted_phase,
                "phase_exit_codes": result.phase_exit_codes,
            },
            indent=2,
        )
    )
    return result.exit_code()


def _handle_preflight(args: argparse.Namespace) -> int:
    from tests.uat.config import load_config
    from tests.uat.phases.execute import default_benchmark_runs_dir
    from tests.uat.phases.preflight import preflight_kwargs_from_config, run_preflight

    config = load_config(args.config)
    benchmark_runs_dir = default_benchmark_runs_dir(config)
    result = run_preflight(**preflight_kwargs_from_config(config, benchmark_runs_dir=benchmark_runs_dir))
    if result.disk_budget_summary:
        print(result.disk_budget_summary)
    for line in getattr(result, "free_space_report", ()):
        print(line)
    for warning in result.warnings:
        print(f"[preflight warn] {warning}", file=sys.stderr)
    if result.aborted:
        print(f"[preflight] ABORT: {result.abort_reason}", file=sys.stderr)
        return 2
    return 0


def _handle_report(args: argparse.Namespace) -> int:
    from tests.uat.cells_io import (
        cells_inprogress_path,
        cells_run_incomplete,
        coerce_accounting_count,
        coerce_accounting_count_with_validity,
        read_accounting_sidecar,
        read_cells_jsonl,
    )
    from tests.uat.phases.report import write_report

    cells_path = Path(args.cells_jsonl)
    finalized = not cells_run_incomplete(cells_path)
    if cells_path.exists():
        cells = read_cells_jsonl(cells_path)
    elif cells_inprogress_path(cells_path).exists():
        cells = []
    else:
        raise FileNotFoundError(
            f"cells stream not found: {cells_path} -- no in-progress marker present, "
            f"so this is not an interrupted sweep. Check the --cells-jsonl path; a "
            f"genuinely killed run leaves a {cells_inprogress_path(cells_path).name} marker."
        )
    accounting, sidecar_present = read_accounting_sidecar(Path(args.cells_jsonl))
    skipped_unreachable_count, skipped_unreachable_valid = coerce_accounting_count_with_validity(
        accounting.get("skipped_unreachable_count", 0)
    )
    startup_failed_count, startup_failed_valid = coerce_accounting_count_with_validity(
        accounting.get("startup_failed_count", 0)
    )
    died_mid_platform_count = coerce_accounting_count(accounting.get("died_mid_platform_count", 0))
    compatibility_pruned_count = coerce_accounting_count(accounting.get("compatibility_pruned_count", 0))
    early_stop_pruned_count = coerce_accounting_count(accounting.get("early_stop_pruned_count", 0))
    registry_pruned_count = coerce_accounting_count(accounting.get("registry_pruned_count", 0))
    rungs = _split_csv(args.rungs)
    summary = write_report(
        cells,
        output_path=Path(args.output_tsv),
        rungs=rungs,
        cross_scale_floor=args.cross_scale_floor,
        compatibility_pruned_count=compatibility_pruned_count,
        early_stop_pruned_count=early_stop_pruned_count,
        registry_pruned_count=registry_pruned_count,
        skipped_unreachable_count=skipped_unreachable_count,
        startup_failed_count=startup_failed_count,
        died_mid_platform_count=died_mid_platform_count,
        unreachable_count_is_estimated=not sidecar_present or not (skipped_unreachable_valid and startup_failed_valid),
        finalized=finalized,
    )
    print(
        json.dumps(
            {
                "tsv": str(summary.tsv_path),
                "rows": summary.rows,
                "candidates": summary.candidate_count,
                "attempted": summary.attempted_count,
                "skipped": summary.skipped_count,
                "unreachable": summary.unreachable_count,
                "unreachable_is_estimated": summary.unreachable_count_is_estimated,
                "startup_failed": summary.startup_failed_count,
                "died_mid_platform": summary.died_mid_platform_count,
                "total_defined": summary.total_defined_count,
                "registry_pruned": summary.registry_pruned_count,
                "passed": summary.pass_count,
                "failed": summary.fail_count,
                "timed_out": summary.timeout_count,
                "unvalidated": summary.unvalidated_count,
                "finalized": finalized,
                "cross_scale_clean_pairs": summary.cross_scale_clean_pairs,
                "cross_scale_floor": summary.cross_scale_floor,
                "cross_scale_floor_breached": summary.cross_scale_floor_breached,
            },
            indent=2,
        )
    )
    return summary.exit_code()


def _stage_summary_path(raw: str) -> Path:
    from tests.uat.gate_summary import GATE_SUMMARY_FILENAME

    path = Path(raw).expanduser()
    if path.is_dir():
        return path / GATE_SUMMARY_FILENAME
    return path


def _stage_file_digest(stage_dir: Path, name: str) -> str | None:
    mapping = {
        "cells_jsonl": stage_dir / "cells.jsonl",
        "accounting_sidecar": stage_dir / "cells.jsonl.accounting.json",
        "lifecycle_log": stage_dir / "uat_lifecycle.log",
    }
    try:
        return hashlib.sha256(mapping[name].read_bytes()).hexdigest()
    except OSError:
        return None


def _repeated_directory_violations(stage_paths: list[list[Path]]) -> list[str]:
    violations: list[str] = []
    for paths in stage_paths:
        resolved = [path.parent.resolve() for path in paths]
        for repeated in sorted({d for d in resolved if resolved.count(d) > 1}):
            violations.append(f"run directory {repeated} is listed more than once for the same stage")
    return violations


def _artifact_digest_violations(stages: list, stage_paths: list[list[Path]]) -> list[str]:
    from tests.uat import gate_summary

    reasons: list[str] = []
    for summaries, paths in zip(stages, stage_paths):
        for summary, path in zip(summaries, paths):
            if summary.artifact_digests is None:
                continue
            stage_dir = path.parent
            description = f"stage {summary.config_name!r}"
            if summary.shard_platforms is not None:
                description += f" shard [{gate_summary.shard_label(summary)}]"
            for key, expected in summary.artifact_digests.items():
                actual = _stage_file_digest(stage_dir, key)
                if actual != expected:
                    actual_label = actual if actual is not None else "absent"
                    expected_label = expected if expected is not None else "absent"
                    reasons.append(
                        f"{description} artifact digest mismatch for "
                        f"{key}: expected {expected_label}, got {actual_label} — "
                        f"regenerate evidence from {stage_dir}"
                    )
    return reasons


def _lifecycle_ordering_violations(stages: list, stage_paths: list[list[Path]]) -> list[str] | None:
    import datetime as _dt

    from tests.uat.phases.report import release_gate_ordering_violations

    violations: list[str] = []
    lifecycle_texts: list[list[str]] = []
    for stage_label, paths in zip(("stage2", "stage3"), stage_paths[1:]):
        texts = []
        for path in paths:
            lifecycle = path.parent / "uat_lifecycle.log"
            if lifecycle.is_file():
                texts.append(lifecycle.read_text(encoding="utf-8"))
            else:
                texts.append("")
                violations.append(
                    f"{stage_label}: docker stage missing lifecycle log ({lifecycle}); ordering not verifiable"
                )
        lifecycle_texts.append(texts)
    try:
        stage1_completed_at = max(_dt.datetime.fromisoformat(s.completed_at) for s in stages[0])
        stage2_completed_at = max(_dt.datetime.fromisoformat(s.completed_at) for s in stages[1])
    except (TypeError, ValueError) as exc:
        print(f"[gate-check] ERROR: unparseable completed_at in stage summaries: {exc}", file=sys.stderr)
        return None
    violations.extend(
        f"stage1 boundary: {violation}"
        for violation in release_gate_ordering_violations(
            [text for texts in lifecycle_texts for text in texts], native_stage_completed_at=stage1_completed_at
        )
    )
    violations.extend(
        f"stage2 boundary: {violation}"
        for violation in release_gate_ordering_violations(
            lifecycle_texts[1], native_stage_completed_at=stage2_completed_at
        )
    )
    return violations


def _shard_union_reasons(stages: list, stage_paths: list[list[Path]], configs_dir: Path) -> list[str]:
    from tests.uat import gate_summary

    reasons: list[str] = []
    for summaries, paths in zip(stages, stage_paths):
        if len(summaries) < 2 and all(s.shard_platforms is None for s in summaries):
            continue
        stage_name = summaries[0].config_name
        config_path = configs_dir / f"{stage_name}.yaml"
        if not config_path.is_file():
            reasons.append(f"stage {stage_name!r} has no config at {config_path}; shard union not verifiable")
            continue
        shard_cells: list[gate_summary.ShardCells] = []
        for summary, path in zip(summaries, paths):
            try:
                cells = gate_summary.read_cell_keys(path.parent / gate_summary.CELLS_FILENAME)
                pruned_path = path.parent / gate_summary.COMPATIBILITY_PRUNED_FILENAME
                pruned = gate_summary.read_cell_keys(pruned_path) if pruned_path.is_file() else ()
            except (OSError, ValueError, KeyError, TypeError) as exc:
                reasons.append(f"stage {stage_name!r} cells unreadable in {path.parent}: {exc}")
                continue
            shard_cells.append(
                gate_summary.ShardCells(
                    label=gate_summary.shard_label(summary),
                    platforms=tuple(summary.shard_platforms) if summary.shard_platforms is not None else None,
                    cells=cells,
                    pruned=pruned,
                )
            )
        reasons.extend(
            gate_summary.shard_union_violations(stage_name, shard_cells, gate_summary.load_stage_plan(config_path))
        )
    return reasons


def _handle_gate_check(args: argparse.Namespace) -> int:
    import datetime as _dt

    from tests.uat import gate_summary

    stage_groups = (args.stage1, args.stage2, args.stage3)
    stage_paths: list[list[Path]] = [[_stage_summary_path(raw) for raw in group] for group in stage_groups]
    stages: list[list[gate_summary.GateSummary]] = []
    for paths in stage_paths:
        summaries = []
        for path in paths:
            if not path.is_file():
                print(f"[gate-check] ERROR: stage summary not found: {path}", file=sys.stderr)
                return 2
            try:
                summaries.append(gate_summary.read_gate_summary(path))
            except (ValueError, TypeError) as exc:
                print(f"[gate-check] ERROR: unreadable stage summary {path}: {exc}", file=sys.stderr)
                return 2
        stages.append(summaries)

    shard_violations = _repeated_directory_violations(stage_paths)
    ordering_violations = _lifecycle_ordering_violations(stages, stage_paths)
    if ordering_violations is None:
        return 2
    ordering_violations.extend(_artifact_digest_violations(stages, stage_paths))
    shard_violations.extend(_shard_union_reasons(stages, stage_paths, Path(args.configs_dir).expanduser()))

    evidence = gate_summary.build_combined_evidence(
        stages,
        ordering_violations=ordering_violations,
        generated_at=_dt.datetime.now(),
        expected_distribution_digest=args.expected_distribution_digest,
        shard_violations=shard_violations,
    )
    output = Path(args.output).expanduser()
    gate_summary.write_combined_evidence(output, evidence)
    print(
        json.dumps(
            {
                "verdict": evidence.verdict,
                "source_commit_sha": evidence.source_commit_sha,
                "source_dirty": evidence.source_dirty,
                "completed_at": evidence.completed_at,
                "stage_verdicts": evidence.stage_verdicts,
                "ordering_violations": list(evidence.ordering_violations),
                "reasons": list(evidence.reasons),
                "evidence_path": str(output),
            },
            indent=2,
        )
    )
    if evidence.verdict != gate_summary.VERDICT_GREEN:
        for reason in evidence.reasons:
            print(f"[gate-check] HOLD: {reason}", file=sys.stderr)
        return 1
    print("[gate-check] APPROVE: all release-gate stages green; review and commit the evidence file.")
    return 0


def _handle_replay_classify(args: argparse.Namespace) -> int:
    from tests.uat.runner import SubmitTerminalState, classify_for_submit

    coverage_cells = Path(args.coverage_cells).expanduser()
    results_root = Path(args.results_root).expanduser()
    counts: Counter[str] = Counter()
    total = 0
    with coverage_cells.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            if row.get("status") != "passed" or not row.get("result_path"):
                continue
            total += 1
            result_path = Path(row["result_path"])
            if not result_path.exists():
                result_path = results_root / result_path.name
            counts[classify_for_submit(result_path).value] += 1

    submittable = counts[SubmitTerminalState.submittable.value]
    non_submittable = total - submittable
    split = ", ".join(
        f"{state.value}={counts[state.value]}"
        for state in SubmitTerminalState
        if state is not SubmitTerminalState.submittable
    )
    print(
        f"{total} passed-result paths; {submittable} submittable; "
        f"{non_submittable} non-submittable split by classifier: {split}"
    )
    return 0


def _handle_package(args: argparse.Namespace) -> int:
    from tests.uat.config import load_config
    from tests.uat.phases.package import run_package

    config = load_config(args.config)
    result = run_package(
        config,
        result_paths=[Path(p) for p in args.result],
        submissions_dir=Path(args.submissions_dir),
    )
    print(
        json.dumps(
            {
                "terminal_state": result.terminal_state,
                "submissions_dir": str(result.submissions_dir),
                "success": result.success_count,
                "failure": result.failure_count,
            },
            indent=2,
        )
    )
    return result.exit_code()


def _handle_validate(args: argparse.Namespace) -> int:
    from tests.uat.phases.validate import run_validate

    result = run_validate(
        Path(args.results_dir),
        output_tsv=Path(args.output_tsv),
        floor=args.floor,
    )
    print(
        json.dumps(
            {
                "rollup_tsv": str(result.rollup_tsv_path),
                "clean": result.clean_count,
                "warning_only": result.warning_count,
                "error": result.error_count,
                "refused_by_cli": result.refused_count,
                "clean_rate": round(result.clean_rate, 4),
                "floor": result.floor,
                "floor_breached": result.floor_breached,
            },
            indent=2,
        )
    )
    return result.exit_code()


def _handle_verify_tuning_matrix(args: argparse.Namespace) -> int:
    from benchbox.core.tuning.coverage import (
        parse_runtime_tuning_logs,
        read_tuning_coverage_tsv,
        runtime_mismatches,
    )
    from tests.uat.matrix import PLATFORM_GROUPS, load_benchmarks, resolve_benchmarks

    benchmarks = load_benchmarks()
    logs_dir = Path(args.logs).expanduser()
    observations = parse_runtime_tuning_logs(
        logs_dir,
        platforms=PLATFORM_GROUPS["all"],
        benchmarks=resolve_benchmarks(groups=["all"], benchmarks=benchmarks),
    )
    if not observations:
        print(f"No tuning observations parsed from UAT logs under {logs_dir}", file=sys.stderr)
        return 1
    matrix_rows = read_tuning_coverage_tsv(Path(args.matrix))
    mismatches = runtime_mismatches(matrix_rows, observations)
    if mismatches:
        print("Tuning matrix mismatches:", file=sys.stderr)
        for mismatch in mismatches:
            print(f"  - {mismatch}", file=sys.stderr)
        return 1
    print(json.dumps({"observations": len(observations), "mismatches": 0}, indent=2))
    return 0


def _handle_execute(args: argparse.Namespace) -> int:
    from dataclasses import replace

    from tests.uat.config import load_config
    from tests.uat.orchestrator import run_sweep
    from tests.uat.phases.execute import default_benchmark_runs_dir

    config = load_config(args.config)
    benchmark_runs_dir = default_benchmark_runs_dir(config)
    databases_root = Path(args.databases_root).expanduser() if args.databases_root else benchmark_runs_dir / "databases"

    phases = tuple(phase for phase in ("preflight", "execute") if phase in config.phases)
    if "execute" not in phases:
        phases = (*phases, "execute")
    cleanup_enabled = not args.no_cleanup and config.cleanup.prune_databases
    scoped_config = replace(
        config,
        phases=phases,
        cleanup=replace(config.cleanup, prune_databases=cleanup_enabled),
    )

    result = run_sweep(scoped_config, databases_root=databases_root)

    if result.aborted_phase == "preflight":
        print(f"[preflight] ABORT: {result.abort_reason}", file=sys.stderr)
        return 2

    outcome = result.execute_outcome
    if outcome is None:
        aborted = result.aborted_phase is not None
        summary = {
            "name": config.name,
            "log_dir": str(result.log_dir),
            "passed": 0,
            "failed": 0,
            "timed_out": 0,
            "pruned": 0,
            "compatibility_pruned": 0,
            "skipped_unreachable": 0,
            "startup_failed": 0,
            "died_mid_platform": 0,
            "docker_events": 0,
            "aborted": aborted,
            "abort_reason": result.abort_reason,
        }
        print(json.dumps(summary, indent=2))
        if aborted:
            print(f"[execute] ABORT: {result.abort_reason}", file=sys.stderr)
        return result.exit_code()

    summary = {
        "name": config.name,
        "log_dir": str(result.log_dir),
        "passed": sum(1 for r in outcome.results if r.status == "passed"),
        "failed": sum(1 for r in outcome.results if r.status == "failed"),
        "timed_out": sum(1 for r in outcome.results if r.status == "timed-out"),
        "pruned": len(outcome.pruned),
        "compatibility_pruned": len(getattr(outcome, "compatibility_pruned", ())),
        "skipped_unreachable": len(outcome.skipped_unreachable),
        "startup_failed": len(getattr(outcome, "startup_failed", ())),
        "died_mid_platform": len(getattr(outcome, "died_mid_platform", ())),
        "docker_events": len(outcome.docker_events),
        "aborted": outcome.aborted,
        "abort_reason": outcome.abort_reason,
    }
    print(json.dumps(summary, indent=2))
    if outcome.aborted:
        print(f"[execute] ABORT: {outcome.abort_reason}", file=sys.stderr)
    return outcome.exit_code()


def _handle_docker_cleanup(args: argparse.Namespace) -> int:
    if args.engine == "container":
        return _handle_container_cleanup(args)

    from tests.uat import docker_cleanup

    try:
        report = docker_cleanup.recover_abandoned_uat_docker_usage(
            project_prefix=args.prefix,
            apply=args.apply,
        )
    except docker_cleanup.DockerCleanupError as exc:
        print(f"[uat-docker-cleanup] ERROR: {exc}", file=sys.stderr)
        return 2
    print(docker_cleanup.format_cleanup_report(report))
    return 0


def _handle_container_cleanup(args: argparse.Namespace) -> int:
    from tests.uat import container_cleanup

    try:
        report = container_cleanup.reclaim_container_usage(
            project_prefix=args.prefix,
            mode=args.mode,
            apply=args.apply,
        )
    except container_cleanup.ContainerCleanupError as exc:
        print(f"[uat-docker-cleanup] ERROR: {exc}", file=sys.stderr)
        return 2
    print(container_cleanup.format_container_cleanup_report(report))
    return 0


def _set_handler(parser: argparse.ArgumentParser, handler: Handler) -> None:
    parser.set_defaults(handler=handler)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tests.uat._cli",
        description="Developer-only UAT framework entrypoints.",
    )
    subparsers = parser.add_subparsers(dest="cmd", required=False)

    cell = subparsers.add_parser("cell", help="run one UAT matrix cell")
    cell.add_argument("--platform", required=True)
    cell.add_argument("--benchmark", required=True)
    cell.add_argument("--scale", required=True, type=float)
    cell.add_argument("--timeout-s", type=int, default=600)
    cell.add_argument("--phases", default="load,power")
    cell.add_argument("--compression", default=None)
    cell.add_argument("--log-dir", default=None)
    _set_handler(cell, _handle_cell)

    sweep = subparsers.add_parser("sweep", help="run a multi-phase UAT sweep from YAML")
    sweep.add_argument("--config", required=True)
    sweep.add_argument("--dry-run", action="store_true", help="Override the YAML config and skip workload phases")
    sweep.add_argument(
        "--platforms",
        default=None,
        help="Comma-separated platform subset of the config's platform set; the run records itself as one shard",
    )
    _set_handler(sweep, _handle_sweep)

    stress = subparsers.add_parser("stress", help="run the canned stress preset")
    stress.add_argument("--config", default=None, help="Defaults to tests/uat/configs/stress-default.yaml")
    stress.add_argument("--platform", default=None)
    stress.add_argument("--benchmark", default=None)
    stress.add_argument("--scale", type=float, default=None)
    _set_handler(stress, _handle_stress)

    preflight = subparsers.add_parser("preflight", help="print advisory disk and platform preflight status")
    preflight.add_argument("--config", required=True)
    _set_handler(preflight, _handle_preflight)

    gate_check = subparsers.add_parser(
        "gate-check",
        help="aggregate the 3 release-gate stage summaries into committed release evidence",
    )
    for stage_number in (1, 2, 3):
        gate_check.add_argument(
            f"--stage{stage_number}",
            required=True,
            nargs="+",
            help=f"Stage-{stage_number} run dir or uat_gate_summary.json path; repeat the path per shard",
        )
    gate_check.add_argument(
        "--configs-dir",
        default=str(Path(__file__).resolve().parent / "configs"),
        help="Directory holding the stage configs used to verify that shard cells cover the stage plan",
    )
    gate_check.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parents[2] / "_project" / "release-evidence" / "uat-gate-summary.json"),
        help="Combined release-evidence output path (default: _project/release-evidence/uat-gate-summary.json)",
    )
    gate_check.add_argument(
        "--expected-distribution-digest",
        default=None,
        help="SHA-256 of the admitted distribution digest file every stage must have recorded",
    )
    _set_handler(gate_check, _handle_gate_check)

    report = subparsers.add_parser("report", help="write a TSV report from a cells JSONL")
    report.add_argument("--cells-jsonl", required=True)
    report.add_argument("--output-tsv", required=True)
    report.add_argument("--rungs", default=None, help="Comma-separated rung scales for cross-scale coverage")
    report.add_argument("--cross-scale-floor", type=int, default=None)
    _set_handler(report, _handle_report)

    replay = subparsers.add_parser("replay-classify", help="classify passed coverage cells for submission readiness")
    replay.add_argument("--coverage-cells", required=True)
    replay.add_argument("--results-root", required=True)
    _set_handler(replay, _handle_replay_classify)

    package = subparsers.add_parser("package", help="package UAT result bundles")
    package.add_argument("--config", required=True)
    package.add_argument("--submissions-dir", required=True)
    package.add_argument("--result", action="append", required=True, help="Result JSON path; repeat for multiple")
    _set_handler(package, _handle_package)

    validate = subparsers.add_parser("validate", help="validate published result bundles")
    validate.add_argument("--results-dir", required=True)
    validate.add_argument("--output-tsv", required=True)
    validate.add_argument("--floor", type=float, default=0.80)
    _set_handler(validate, _handle_validate)

    tuning = subparsers.add_parser("verify-tuning-matrix", help="compare tuned-template coverage to observed logs")
    tuning.add_argument("--logs", required=True, help="Directory containing per-cell UAT command logs")
    tuning.add_argument(
        "--matrix",
        default=str(Path(__file__).resolve().parent / "data" / "tuning_coverage.tsv"),
        help="Checked-in tuning coverage TSV",
    )
    _set_handler(tuning, _handle_verify_tuning_matrix)

    execute = subparsers.add_parser("execute", help="run the UAT execute phase")
    execute.add_argument("--config", required=True)
    execute.add_argument(
        "--databases-root",
        default=None,
        help="Optional override for ~/Developer/benchmark_runs/databases",
    )
    execute.add_argument("--no-cleanup", action="store_true", help="Disable reuse-aware database cleanup")
    _set_handler(execute, _handle_execute)

    docker = subparsers.add_parser(
        "docker-cleanup", help="report or remove abandoned UAT-owned Docker / Apple container resources"
    )
    from tests.uat import container_cleanup, docker_cleanup

    docker.add_argument(
        "--engine",
        choices=("docker", "container"),
        default="docker",
        help="Cleanup engine: 'docker' (default) or 'container' (Apple container store).",
    )
    docker.add_argument(
        "--mode",
        choices=container_cleanup.CONTAINER_CLEANUP_MODES,
        default="owned",
        help="Apple-container breadth ladder: owned (default) < images < max. Ignored for --engine docker.",
    )
    docker.add_argument(
        "--prefix",
        default=docker_cleanup.DEFAULT_UAT_PROJECT_PREFIX,
        help="Compose project prefix that marks UAT-owned resources",
    )
    docker.add_argument(
        "--apply",
        action="store_true",
        help="Remove owned resources. Without this flag the command only reports the plan.",
    )
    _set_handler(docker, _handle_docker_cleanup)

    return parser


def _argv_with_default_cell(argv: list[str]) -> list[str]:
    if argv and argv[0] in {"-h", "--help"}:
        return argv
    if not argv or argv[0].startswith("-"):
        return ["cell", *argv]
    return argv


def _exit_code_from_system_exit(exc: SystemExit) -> int:
    if exc.code is None:
        return 0
    if isinstance(exc.code, int):
        return exc.code
    print(exc.code, file=sys.stderr)
    return 1


def _needs_heavy_guard(args: argparse.Namespace) -> bool:
    if session_isolation.active() or getattr(args, "dry_run", False):
        return False
    return getattr(args, "cmd", None) not in LIGHT_SUBCOMMANDS


def _log_refusal(message: str) -> None:
    sys.stderr.write(f"{host_lock.LOG_PREFIX} {message}\n")
    sys.stderr.flush()


def _run_heavy(args: argparse.Namespace, handler: Handler, raw_argv: list[str]) -> int:
    try:
        handle = host_lock.acquire(
            host_lock.lock_path(),
            phase="uat",
            gate=str(args.cmd),
            command=" ".join(["tests.uat._cli", *raw_argv]),
            admit=True,
            guard=True,
        )
    except host_lock.LockOwnershipError as exc:
        _log_refusal(str(exc))
        return host_lock.EXIT_OWNERSHIP
    except host_lock.LockUnavailable as exc:
        _log_refusal(f"BLOCKED: {exc}")
        return host_lock.EXIT_REFUSED
    except host_lock.AdmissionRefused as exc:
        _log_refusal(f"REFUSED: host is not ready for heavy work; lock released\n{exc.report}")
        return host_lock.EXIT_ADMISSION
    except KeyboardInterrupt:
        return 130
    previous_owner = os.environ.get(host_lock.OWNER_ENV)
    if not handle.inherited:
        os.environ[host_lock.OWNER_ENV] = host_lock.process_identity()
    watcher = handle.guard
    try:
        code = handler(args)
    finally:
        if previous_owner is None:
            os.environ.pop(host_lock.OWNER_ENV, None)
        else:
            os.environ[host_lock.OWNER_ENV] = previous_owner
        handle.release()
    if watcher is not None and watcher.breached:
        _log_refusal(f"watchdog stopped the process tree after a breach; exiting {host_lock.EXIT_WATCHDOG}")
        return host_lock.EXIT_WATCHDOG
    return code


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parser.parse_args(_argv_with_default_cell(raw_argv))
    except SystemExit as exc:
        return _exit_code_from_system_exit(exc)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    if _needs_heavy_guard(args):
        return _run_heavy(args, handler, raw_argv)
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
