from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import signal
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from benchbox.utils.resource_limits import free_space_gib
from tests.uat import cells_io, docker_assets, gate_summary, preflight_budget
from tests.uat.config import UATConfig, disk_gate_disabled_warning, load_config, memory_gate_disabled_warning
from tests.uat.phases import (
    enumerate as enumerate_phase,
    execute as exec_phase,
    preflight as preflight_phase,
    report as report_phase,
)
from tests.uat.runner import CellResult, SubmitTerminalState


@dataclass(frozen=True)
class SweepResult:
    name: str
    log_dir: Path
    aborted_phase: str | None
    abort_reason: str | None
    phase_exit_codes: dict[str, int]
    preflight: Any = None
    execute_outcome: Any = None
    plan: Any = None

    def exit_code(self) -> int:
        if self.aborted_phase is not None:
            return 2
        return max((c for c in self.phase_exit_codes.values()), default=0)


@dataclass(frozen=True)
class RunSourceInfo:
    commit_sha: str
    commit_short_sha: str
    dirty: bool


CellRunner = Callable[..., CellResult]


class DiskFloorAbort(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SweepCancelled(KeyboardInterrupt):
    def __init__(self, signal_name: str) -> None:
        super().__init__(signal_name)
        self.signal_name = signal_name


_SHIM_NOT_INSTALLED = object()


def _install_sweep_sigterm_shim(log_dir: Path, phase_holder: list[str | None]) -> object | None:

    def _raise_cancelled(signum: int, _frame: object) -> None:
        signal_name = signal.Signals(signum).name
        exec_phase.append_lifecycle_log(
            log_dir,
            f"[cancel] signal={signal_name} phase={phase_holder[0]} "
            "status=tearing-down unfinalized (no finalize marker will be written)",
        )
        raise SweepCancelled(signal_name)

    try:
        return signal.signal(signal.SIGTERM, _raise_cancelled)
    except (ValueError, OSError):
        return _SHIM_NOT_INSTALLED


def _restore_sweep_sigterm_shim(previous: object | None) -> None:
    if previous is _SHIM_NOT_INSTALLED:
        return
    restore_to = signal.SIG_DFL if previous is None else previous
    try:
        signal.signal(signal.SIGTERM, restore_to)
    except (ValueError, OSError, TypeError):
        pass


def capture_run_source_info(repo_root: Path | None = None) -> RunSourceInfo:
    root = repo_root or Path(__file__).resolve().parents[2]
    commit_sha = _git_output(root, "rev-parse", "HEAD") or "unknown"
    commit_short_sha = _git_output(root, "rev-parse", "--short", "HEAD") or commit_sha[:12]
    dirty_output = _git_output(root, "status", "--porcelain", "--untracked-files=normal")
    return RunSourceInfo(
        commit_sha=commit_sha,
        commit_short_sha=commit_short_sha,
        dirty=bool(dirty_output),
    )


def _git_output(repo_root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ("git", *args),
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _cell_datagen_dir(benchmark_runs_dir: Path | str | None, benchmark: str, scale: float) -> Path | None:
    if benchmark_runs_dir is None:
        return None
    try:
        from benchbox.utils.output_path import normalize_output_root

        normalized = normalize_output_root(str(Path(benchmark_runs_dir) / "datagen"), benchmark, scale)
    except Exception:
        return None
    return Path(normalized) if normalized else None


def _datagen_cache_complete(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        manifest_path = path / "_datagen_manifest.json"
        if not manifest_path.is_file():
            return False
        try:
            import json as _json

            from benchbox.utils.datagen_version import manifest_datagen_is_current

            manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return bool(manifest_datagen_is_current(manifest))
    except OSError:
        return False


def _build_disk_floor_runner(
    base_runner: CellRunner,
    *,
    attempted_cells: list[CellResult],
    watch_disk_floor: bool,
    free_space_path: str | Path,
    free_space_min_gib: float,
    budget_table: preflight_budget.BudgetTable | None = None,
    free_space_reader: Callable[[str | Path], float] | None = None,
    cell_stream: Callable[[CellResult], None] | None = None,
    benchmark_runs_dir: Path | str | None = None,
    datagen_cache_probe: Callable[[Path | None], bool] | None = None,
) -> CellRunner:

    seen_datagen_dirs: set[Path] = set()
    read_free_space = free_space_reader or free_space_gib
    datagen_complete = datagen_cache_probe or _datagen_cache_complete

    def runner(platform: str, benchmark: str, scale: float, **kwargs) -> CellResult:
        prediction = None
        datagen_dir = _cell_datagen_dir(benchmark_runs_dir, benchmark, scale)
        if watch_disk_floor and budget_table is not None:
            already_present = (datagen_dir is not None and datagen_dir in seen_datagen_dirs) or datagen_complete(
                datagen_dir
            )
            prediction = preflight_budget.predict_cell_disk_growth(
                platform,
                benchmark,
                scale,
                table=budget_table,
                datagen_already_present=already_present,
            )
            if prediction is not None:
                free_gib = read_free_space(free_space_path)
                required_gib = free_space_min_gib + prediction.known_growth_gib
                exec_phase.append_lifecycle_log(
                    kwargs.get("log_dir"),
                    f"[free-space] before cell {platform}/{benchmark} scale={scale:g}: "
                    f"{free_gib:.2f} GiB free; reserving {prediction.known_growth_gib:.2f} GiB "
                    f"predicted lower-bound growth + {free_space_min_gib:.2f} GiB floor "
                    f"(database_measured={prediction.database_measured})",
                )
                if free_gib < required_gib:
                    coverage = "complete" if prediction.database_measured else "lower-bound"
                    raise DiskFloorAbort(
                        f"predictive disk check failed before cell {platform}/{benchmark} scale={scale:g}: "
                        f"free space {free_gib:.1f} GiB < {required_gib:.1f} GiB required "
                        f"({prediction.known_growth_gib:.1f} GiB {coverage} predicted cell growth + "
                        f"{free_space_min_gib:.1f} GiB floor) at {free_space_path}"
                    )
            else:
                exec_phase.append_lifecycle_log(
                    kwargs.get("log_dir"),
                    f"[free-space] before cell {platform}/{benchmark} scale={scale:g}: "
                    "no inventory row for predictive growth; post-cell floor remains the backstop",
                )

        result = base_runner(platform, benchmark, scale, **kwargs)
        attempted_cells.append(result)
        if cell_stream is not None:
            cell_stream(result)
        if watch_disk_floor:
            free_gib = read_free_space(free_space_path)
            if free_gib < free_space_min_gib:
                raise DiskFloorAbort(
                    f"free space {free_gib:.1f} GiB < cutoff {free_space_min_gib:.1f} GiB at {free_space_path}"
                )
        if prediction is not None and datagen_dir is not None and datagen_complete(datagen_dir):
            seen_datagen_dirs.add(datagen_dir)
        return result

    return runner


def _record_container_engine_identity(log_dir: Path) -> str | None:
    try:
        binary, version = docker_assets.container_engine_identity()
    except docker_assets.DockerAssetError as exc:
        exec_phase.append_lifecycle_log(log_dir, f"[engine] resolution failed: {exc}")
        return None
    exec_phase.append_lifecycle_log(log_dir, f"[engine] resolved_container_cli={binary} version={version}")
    return binary


def run_sweep(
    config: UATConfig,
    *,
    log_dir_override: Path | None = None,
    databases_root: Path | None = None,
    shard_platforms: tuple[str, ...] | None = None,
) -> SweepResult:
    now = _dt.datetime.now()
    log_dir = log_dir_override or exec_phase.reserve_default_log_dir(config, now=now)
    benchmark_runs_dir = exec_phase.default_benchmark_runs_dir(config, now=now)
    log_dir.mkdir(parents=True, exist_ok=True)
    container_engine = _record_container_engine_identity(log_dir)

    if databases_root is None:
        databases_root = benchmark_runs_dir / "databases"

    source_info = capture_run_source_info()
    phase_holder: list[str | None] = [None]
    sigterm_prev = _install_sweep_sigterm_shim(log_dir, phase_holder)
    try:
        return _run_sweep_phases(
            config,
            now=now,
            log_dir=log_dir,
            benchmark_runs_dir=benchmark_runs_dir,
            databases_root=databases_root,
            container_engine=container_engine,
            source_info=source_info,
            phase_holder=phase_holder,
            shard_platforms=shard_platforms,
        )
    finally:
        _restore_sweep_sigterm_shim(sigterm_prev)


def _print_disabled_gate_warnings(config: UATConfig) -> None:
    if config.dry_run or "execute" not in config.phases:
        return
    for gate_warning in (disk_gate_disabled_warning(config), memory_gate_disabled_warning(config)):
        if gate_warning is not None:
            print(gate_warning, file=sys.stderr)


def _print_preflight_diagnostics(result: Any) -> None:
    disk_budget_summary = getattr(result, "disk_budget_summary", None)
    if disk_budget_summary:
        print(disk_budget_summary, file=sys.stderr)
    for line in getattr(result, "free_space_report", ()):
        print(line, file=sys.stderr)
    for warning in getattr(result, "warnings", ()):
        print(f"[preflight warn] {warning}", file=sys.stderr)


def _run_sweep_phases(
    config: UATConfig,
    *,
    now: _dt.datetime,
    log_dir: Path,
    benchmark_runs_dir: Path,
    databases_root: Path,
    container_engine: str | None,
    source_info: RunSourceInfo,
    phase_holder: list[str | None],
    shard_platforms: tuple[str, ...] | None = None,
) -> SweepResult:
    phase_exit_codes: dict[str, int] = {}
    aborted_phase: str | None = None
    abort_reason: str | None = None
    abort_kind: str | None = None

    cells_jsonl = log_dir / "cells.jsonl"
    compatibility_pruned_jsonl = log_dir / "compatibility_pruned.jsonl"
    execute_outcome = None
    preflight_result = None
    validator_rollup_tsv: Path | None = None
    submissions_dir: Path | None = None
    validate_result: Any = None
    report_summary: Any = None

    _print_disabled_gate_warnings(config)

    for phase in config.phases:
        phase_holder[0] = phase
        if config.dry_run:
            phase_exit_codes[phase] = 0
            continue
        if phase == "preflight":
            result = preflight_phase.run_preflight(
                **preflight_phase.preflight_kwargs_from_config(config, benchmark_runs_dir=benchmark_runs_dir)
            )
            preflight_result = result
            _print_preflight_diagnostics(result)
            phase_exit_codes[phase] = result.exit_code()
            if result.aborted:
                aborted_phase = phase
                abort_reason = result.abort_reason
                abort_kind = getattr(result, "abort_kind", None)
                report_summary = _emit_abort_artifacts(
                    config=config,
                    log_dir=log_dir,
                    attempted=(),
                    execute_outcome=execute_outcome,
                    source_info=source_info,
                    aborted_phase=phase,
                    abort_reason=abort_reason,
                    container_engine=container_engine,
                )
                break
        elif phase == "execute":
            attempted_cells: list[CellResult] = []
            predictive_budget_table = preflight_budget.load_budget_table() if config.disk_gate_enabled else None
            cell_stream_writer = cells_io.CellStreamWriter(cells_jsonl, source_info=source_info)
            execute_kwargs: dict[str, Any] = {
                "log_dir": log_dir,
                "benchmark_runs_dir": benchmark_runs_dir,
                "databases_root": databases_root,
                "cleanup_enabled": config.cleanup.prune_databases,
                "free_space_checks_enabled": config.disk_gate_enabled,
                "runner": _build_disk_floor_runner(
                    exec_phase.run_cell,
                    attempted_cells=attempted_cells,
                    watch_disk_floor=config.disk_gate_enabled,
                    free_space_path=config.preflight.free_space_path or str(benchmark_runs_dir),
                    free_space_min_gib=config.preflight.free_space_min_gib,
                    budget_table=predictive_budget_table,
                    cell_stream=cell_stream_writer.append,
                    benchmark_runs_dir=benchmark_runs_dir,
                ),
            }
            try:
                execute_outcome = exec_phase.run_execute(config, **execute_kwargs)
            except DiskFloorAbort as exc:
                phase_exit_codes[phase] = 2
                aborted_phase = phase
                abort_reason = exc.reason
                abort_kind = "disk_floor"
                execute_outcome = exec_phase.ExecuteOutcome(
                    phase="execute",
                    results=tuple(attempted_cells),
                    pruned=(),
                    skipped_unreachable=(),
                    startup_failed=(),
                    died_mid_platform=(),
                    compatibility_pruned=getattr(exc, "compatibility_pruned", ()) or (),
                    aborted=True,
                    abort_reason=abort_reason,
                    abort_kind="disk_floor",
                )
                report_summary = _emit_abort_artifacts(
                    config=config,
                    log_dir=log_dir,
                    attempted=attempted_cells,
                    execute_outcome=execute_outcome,
                    source_info=source_info,
                    aborted_phase=phase,
                    abort_reason=abort_reason,
                    skipped_unreachable_count=getattr(exc, "skipped_unreachable_count", 0),
                    startup_failed_count=getattr(exc, "startup_failed_count", 0),
                    died_mid_platform_count=getattr(exc, "died_mid_platform_count", 0),
                    container_engine=container_engine,
                )
                break
            compat_rule_pruned_count, registry_pruned_count = enumerate_phase.count_pruned_by_kind(
                getattr(execute_outcome, "compatibility_pruned", ())
            )
            cells_io.write_cells_jsonl(
                cells_jsonl,
                execute_outcome.results,
                source_info=source_info,
                skipped_unreachable_count=len(getattr(execute_outcome, "skipped_unreachable", ())),
                startup_failed_count=len(getattr(execute_outcome, "startup_failed", ())),
                died_mid_platform_count=len(getattr(execute_outcome, "died_mid_platform", ())),
                compatibility_pruned_count=compat_rule_pruned_count,
                early_stop_pruned_count=len(getattr(execute_outcome, "pruned", ())),
                registry_pruned_count=registry_pruned_count,
                disk_gate_disabled=not config.disk_gate_enabled,
                memory_gate_disabled=not config.memory_gate_enabled,
                container_engine=container_engine,
            )
            _write_compatibility_pruned_jsonl(
                compatibility_pruned_jsonl,
                getattr(execute_outcome, "compatibility_pruned", ()),
            )
            if execute_outcome.aborted:
                phase_exit_codes[phase] = 2
                aborted_phase = phase
                abort_reason = execute_outcome.abort_reason
                abort_kind = getattr(execute_outcome, "abort_kind", None)
                report_summary = _emit_abort_artifacts(
                    config=config,
                    log_dir=log_dir,
                    attempted=(),
                    execute_outcome=execute_outcome,
                    source_info=source_info,
                    aborted_phase=phase,
                    abort_reason=abort_reason,
                    container_engine=container_engine,
                )
                break
            phase_exit_codes[phase] = execute_outcome.exit_code()
        elif phase == "validate":
            from tests.uat.phases.validate import run_validate

            if execute_outcome is None:
                phase_exit_codes[phase] = 2
                aborted_phase = phase
                abort_reason = "validate phase requires execute phase to have run"
                report_summary = _emit_abort_artifacts(
                    config=config,
                    log_dir=log_dir,
                    attempted=(),
                    execute_outcome=execute_outcome,
                    source_info=source_info,
                    aborted_phase=phase,
                    abort_reason=abort_reason,
                    container_engine=container_engine,
                )
                break
            result_paths = [r.result_path for r in execute_outcome.results if r.result_path]
            output_tsv = log_dir / "validator_rollup.tsv"
            vr = run_validate(
                result_paths,
                output_tsv=output_tsv,
                floor=config.validate.validator_clean_rate_floor,
            )
            phase_exit_codes[phase] = vr.exit_code()
            validate_result = vr
            validator_rollup_tsv = vr.rollup_tsv_path
            if vr.aborted:
                aborted_phase = phase
                abort_reason = vr.abort_reason
                report_summary = _emit_abort_artifacts(
                    config=config,
                    log_dir=log_dir,
                    attempted=(),
                    execute_outcome=execute_outcome,
                    source_info=source_info,
                    aborted_phase=phase,
                    abort_reason=abort_reason,
                    container_engine=container_engine,
                )
                break
        elif phase == "package":
            from tests.uat.phases.package import run_package

            if execute_outcome is None:
                phase_exit_codes[phase] = 2
                aborted_phase = phase
                abort_reason = "package phase requires execute phase to have run"
                report_summary = _emit_abort_artifacts(
                    config=config,
                    log_dir=log_dir,
                    attempted=(),
                    execute_outcome=execute_outcome,
                    source_info=source_info,
                    aborted_phase=phase,
                    abort_reason=abort_reason,
                    container_engine=container_engine,
                )
                break
            result_paths = [r.result_path for r in execute_outcome.results if r.result_path and r.status == "passed"]
            submissions_dir = exec_phase.default_submissions_dir(config, now=now)
            pr = run_package(
                config,
                result_paths=result_paths,
                submissions_dir=submissions_dir,
            )
            phase_exit_codes[phase] = pr.exit_code()
            if pr.aborted:
                aborted_phase = phase
                abort_reason = pr.abort_reason
                report_summary = _emit_abort_artifacts(
                    config=config,
                    log_dir=log_dir,
                    attempted=(),
                    execute_outcome=execute_outcome,
                    source_info=source_info,
                    aborted_phase=phase,
                    abort_reason=abort_reason,
                    container_engine=container_engine,
                )
                break
        elif phase == "report":
            if execute_outcome is None:
                phase_exit_codes[phase] = 2
                aborted_phase = phase
                abort_reason = "report phase requires execute phase to have run"
                report_summary = _emit_abort_artifacts(
                    config=config,
                    log_dir=log_dir,
                    attempted=(),
                    execute_outcome=execute_outcome,
                    source_info=source_info,
                    aborted_phase=phase,
                    abort_reason=abort_reason,
                    container_engine=container_engine,
                )
                break
            tsv_path = log_dir / config.report.matrix_summary_tsv
            cells = execute_outcome.results
            validator_status_by_path = _validator_status_by_path(validator_rollup_tsv)
            report_compat_pruned_count, report_registry_pruned_count = enumerate_phase.count_pruned_by_kind(
                getattr(execute_outcome, "compatibility_pruned", ())
            )
            summary = report_phase.write_report(
                cells,
                output_path=tsv_path,
                rungs=list(config.scales.requested_rungs),
                cross_scale_floor=None if shard_platforms else config.report.cross_scale_coverage_min_pairs,
                validator_status_by_path=validator_status_by_path,
                compatibility_pruned_count=report_compat_pruned_count,
                early_stop_pruned_count=len(getattr(execute_outcome, "pruned", ())),
                registry_pruned_count=report_registry_pruned_count,
                skipped_unreachable_count=len(getattr(execute_outcome, "skipped_unreachable", ())),
                startup_failed_count=len(getattr(execute_outcome, "startup_failed", ())),
                died_mid_platform_count=len(getattr(execute_outcome, "died_mid_platform", ())),
                source_info=source_info,
            )
            report_summary = summary
            phase_exit_codes[phase] = summary.exit_code()

    completed_at = _dt.datetime.now().astimezone()
    _write_gate_summary_artifact(
        config=config,
        log_dir=log_dir,
        source_info=source_info,
        container_engine=container_engine,
        completed_at=completed_at,
        aborted_phase=aborted_phase,
        abort_reason=abort_reason,
        abort_kind=abort_kind,
        phase_exit_codes=phase_exit_codes,
        execute_outcome=execute_outcome,
        report_summary=report_summary,
        validate_result=validate_result,
        shard_platforms=shard_platforms,
    )

    return SweepResult(
        name=config.name,
        log_dir=log_dir,
        aborted_phase=aborted_phase,
        abort_reason=abort_reason,
        phase_exit_codes=phase_exit_codes,
        preflight=preflight_result,
        execute_outcome=execute_outcome,
        plan=enumerate_phase.enumerate_cells_with_pruning(config) if config.dry_run else None,
    )


def _validator_status_by_path(validator_rollup_tsv: Path | None) -> dict[Path, str] | None:
    if validator_rollup_tsv is None or not validator_rollup_tsv.exists():
        return None
    from tests.uat.phases.validate import parse_validator_status_by_path

    return parse_validator_status_by_path(validator_rollup_tsv)


def _emit_abort_artifacts(
    *,
    config: UATConfig,
    log_dir: Path,
    attempted: Iterable[CellResult],
    execute_outcome: Any,
    source_info: RunSourceInfo,
    aborted_phase: str,
    abort_reason: str | None,
    skipped_unreachable_count: int | None = None,
    startup_failed_count: int | None = None,
    died_mid_platform_count: int | None = None,
    container_engine: str | None = None,
) -> report_phase.ReportSummary:
    cells = tuple(getattr(execute_outcome, "results", ())) if execute_outcome is not None else tuple(attempted)
    compatibility_pruned = (
        tuple(getattr(execute_outcome, "compatibility_pruned", ()))
        if execute_outcome is not None
        else _compatibility_pruned_for_config(config)
    )
    early_stop_pruned_count = len(getattr(execute_outcome, "pruned", ())) if execute_outcome is not None else 0
    if skipped_unreachable_count is None:
        skipped_unreachable_count = (
            len(getattr(execute_outcome, "skipped_unreachable", ())) if execute_outcome is not None else 0
        )
    if startup_failed_count is None:
        startup_failed_count = len(getattr(execute_outcome, "startup_failed", ())) if execute_outcome is not None else 0
    if died_mid_platform_count is None:
        died_mid_platform_count = (
            len(getattr(execute_outcome, "died_mid_platform", ())) if execute_outcome is not None else 0
        )
    compat_rule_pruned_count, registry_pruned_count = enumerate_phase.count_pruned_by_kind(compatibility_pruned)
    cells_io.write_cells_jsonl(
        log_dir / "cells.jsonl",
        cells,
        source_info=source_info,
        skipped_unreachable_count=skipped_unreachable_count,
        startup_failed_count=startup_failed_count,
        died_mid_platform_count=died_mid_platform_count,
        compatibility_pruned_count=compat_rule_pruned_count,
        early_stop_pruned_count=early_stop_pruned_count,
        registry_pruned_count=registry_pruned_count,
        disk_gate_disabled=not config.disk_gate_enabled,
        memory_gate_disabled=not config.memory_gate_enabled,
        container_engine=container_engine,
    )
    _write_compatibility_pruned_jsonl(log_dir / "compatibility_pruned.jsonl", compatibility_pruned)
    return report_phase.write_report(
        cells,
        output_path=_partial_report_path(log_dir / config.report.matrix_summary_tsv),
        rungs=list(config.scales.requested_rungs),
        cross_scale_floor=config.report.cross_scale_coverage_min_pairs,
        compatibility_pruned_count=compat_rule_pruned_count,
        early_stop_pruned_count=early_stop_pruned_count,
        registry_pruned_count=registry_pruned_count,
        skipped_unreachable_count=skipped_unreachable_count,
        startup_failed_count=startup_failed_count,
        died_mid_platform_count=died_mid_platform_count,
        source_info=source_info,
        run_status="ABORTED",
        abort_phase=aborted_phase,
        abort_reason=abort_reason,
    )


def _accounting_for_gate_summary(report_summary: Any, execute_outcome: Any) -> gate_summary.PhaseAccounting:
    if report_summary is not None:
        return gate_summary.PhaseAccounting(
            attempted=report_summary.attempted_count,
            passed=report_summary.pass_count,
            failed=report_summary.fail_count,
            timed_out=report_summary.timeout_count,
            unreachable=report_summary.unreachable_count,
            startup_failed=report_summary.startup_failed_count,
            died_mid_platform=report_summary.died_mid_platform_count,
            skipped=report_summary.skipped_count,
            compatibility_pruned=report_summary.compatibility_pruned_count,
            early_stop_pruned=report_summary.early_stop_pruned_count,
            registry_pruned=report_summary.registry_pruned_count,
            total_defined=report_summary.total_defined_count,
            unvalidated=report_summary.unvalidated_count,
        )
    if execute_outcome is None:
        return gate_summary.PhaseAccounting()
    results = tuple(getattr(execute_outcome, "results", ()))
    passed = sum(1 for r in results if r.status == "passed")
    failed = sum(1 for r in results if r.status == "failed")
    timed_out = sum(1 for r in results if r.status == "timed-out")
    row_skipped = sum(1 for r in results if report_phase.is_skipped_status(r.status))
    row_unreachable = sum(1 for r in results if report_phase.is_unreachable_status(r.status))
    unvalidated = sum(
        1 for r in results if r.status == "passed" and r.submit_terminal_state == SubmitTerminalState.unvalidated.value
    )
    compatibility_pruned, registry_pruned = enumerate_phase.count_pruned_by_kind(
        getattr(execute_outcome, "compatibility_pruned", ())
    )
    early_stop_pruned = len(getattr(execute_outcome, "pruned", ()))
    unreachable = row_unreachable + len(getattr(execute_outcome, "skipped_unreachable", ()))
    startup_failed = len(getattr(execute_outcome, "startup_failed", ()))
    died_mid_platform = len(getattr(execute_outcome, "died_mid_platform", ()))
    attempted = len(results) - row_skipped - row_unreachable
    skipped = row_skipped + compatibility_pruned + registry_pruned + early_stop_pruned
    return gate_summary.PhaseAccounting(
        attempted=attempted,
        passed=passed,
        failed=failed,
        timed_out=timed_out,
        unreachable=unreachable,
        startup_failed=startup_failed,
        died_mid_platform=died_mid_platform,
        skipped=skipped,
        compatibility_pruned=compatibility_pruned,
        early_stop_pruned=early_stop_pruned,
        registry_pruned=registry_pruned,
        total_defined=attempted + skipped + unreachable + startup_failed + died_mid_platform,
        unvalidated=unvalidated,
    )


def _artifact_digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _distribution_digest() -> str | None:
    digest_file = os.environ.get("RELEASE_DIST_DIGEST_FILE")
    if not digest_file:
        return None
    return _artifact_digest(Path(digest_file))


def _collect_artifact_digests(log_dir: Path) -> dict[str, str | None]:
    return {
        "cells_jsonl": _artifact_digest(log_dir / "cells.jsonl"),
        "accounting_sidecar": _artifact_digest(log_dir / "cells.jsonl.accounting.json"),
        "lifecycle_log": _artifact_digest(log_dir / "uat_lifecycle.log"),
    }


def _write_gate_summary_artifact(
    *,
    config: UATConfig,
    log_dir: Path,
    source_info: RunSourceInfo,
    container_engine: str | None,
    completed_at: _dt.datetime,
    aborted_phase: str | None,
    abort_reason: str | None,
    abort_kind: str | None,
    phase_exit_codes: dict[str, int],
    execute_outcome: Any,
    report_summary: Any,
    validate_result: Any,
    shard_platforms: tuple[str, ...] | None = None,
) -> None:
    cross_scale_floor = report_summary.cross_scale_floor if report_summary is not None else None
    cross_scale_floor_breached = report_summary.cross_scale_floor_breached if report_summary is not None else None
    if shard_platforms and report_summary is not None:
        cross_scale_floor = config.report.cross_scale_coverage_min_pairs
        cross_scale_floor_breached = None
    summary = gate_summary.GateSummary(
        config_name=config.name,
        source_commit_sha=source_info.commit_sha,
        source_dirty=source_info.dirty,
        container_engine=container_engine,
        completed_at=completed_at.isoformat(),
        dry_run=config.dry_run,
        aborted=aborted_phase is not None,
        abort_phase=aborted_phase,
        abort_reason=abort_reason,
        abort_kind=abort_kind,
        phase_exit_codes=dict(phase_exit_codes),
        accounting=_accounting_for_gate_summary(report_summary, execute_outcome),
        unreachable_is_estimated=bool(getattr(report_summary, "unreachable_count_is_estimated", False)),
        validator_clean_rate=(validate_result.clean_rate if validate_result is not None else None),
        validator_clean_rate_floor=(validate_result.floor if validate_result is not None else None),
        validator_floor_breached=(validate_result.floor_breached if validate_result is not None else None),
        cross_scale_clean_pairs=(report_summary.cross_scale_clean_pairs if report_summary is not None else None),
        cross_scale_floor=cross_scale_floor,
        cross_scale_floor_breached=cross_scale_floor_breached,
        shard_platforms=sorted(shard_platforms) if shard_platforms else None,
        artifact_digests=_collect_artifact_digests(log_dir),
        distribution_digest=_distribution_digest(),
        verdict=gate_summary.derive_verdict(
            dry_run=config.dry_run,
            aborted=aborted_phase is not None,
            phase_exit_codes=phase_exit_codes,
        ),
    )
    gate_summary.write_gate_summary(log_dir, summary)


def _partial_report_path(path: Path) -> Path:
    if path.suffix:
        return path.with_name(f"{path.stem}.partial{path.suffix}")
    return path.with_name(f"{path.name}.partial")


def _compatibility_pruned_for_config(config: UATConfig) -> tuple[Any, ...]:
    return tuple(exec_phase.enumerate_cells_with_pruning(config).compatibility_pruned)


def _write_compatibility_pruned_jsonl(path: Path, cells: Iterable[Any]) -> None:
    lines: list[str] = []
    for cell in cells:
        lines.append(
            json.dumps(
                {
                    "platform": cell.platform,
                    "benchmark": cell.benchmark,
                    "scale": cell.scale,
                    "status": "compatibility-pruned",
                    "rule_id": cell.rule_id,
                    "rule_status": cell.status,
                    "reason": cell.reason,
                    "evidence": cell.evidence,
                }
            )
            + "\n"
        )
    report_phase.atomic_write_text(path, "".join(lines))


def shard_config(config: UATConfig, platforms: tuple[str, ...]) -> UATConfig:
    planned_platforms = enumerate_phase.resolved_platforms(config)
    unknown = [platform for platform in platforms if platform not in planned_platforms]
    if unknown:
        raise ValueError(
            f"platforms {unknown} are not in the platform set of config {config.name!r}: {planned_platforms}"
        )
    return replace(config, platforms=replace(config.platforms, groups=(), include=platforms))


def run_sweep_from_path(
    config_path: Path,
    *,
    stress_overrides: dict[str, str | float | None] | None = None,
    dry_run_override: bool | None = None,
    platform_shard: Iterable[str] | None = None,
) -> SweepResult:
    config = load_config(config_path)
    shard_platforms: tuple[str, ...] | None = None
    if platform_shard:
        shard_platforms = tuple(dict.fromkeys(platform_shard))
        config = shard_config(config, shard_platforms)
    if stress_overrides:
        platform = stress_overrides.get("platform")
        benchmark = stress_overrides.get("benchmark")
        scale = stress_overrides.get("scale")
        if platform is not None:
            config = replace(config, platforms=replace(config.platforms, groups=(), include=(str(platform),)))
        if benchmark is not None:
            config = replace(config, benchmarks=replace(config.benchmarks, groups=(), include=(str(benchmark),)))
        if scale is not None:
            config = replace(config, scales=replace(config.scales, override=float(scale)))
    if dry_run_override is not None:
        config = replace(config, dry_run=dry_run_override)
    return run_sweep(config, shard_platforms=shard_platforms)
