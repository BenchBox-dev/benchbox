from __future__ import annotations

import datetime as _dt
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from tests.uat.phases import PhaseResult
from tests.uat.runner import CellResult, SubmitTerminalState

REPORT_HEADER = (
    "platform\tbenchmark\tscale\tstatus\tterminal_state\telapsed_s\tlog_path\tresult_path\t"
    "submit_terminal_state\tvalidator_status\tsource_commit_sha\tsource_dirty\tthroughput_check"
)

_SKIPPED_STATUSES = frozenset({"skipped"})
_UNREACHABLE_STATUSES = frozenset({"skipped-unreachable", "skipped_unreachable", "unreachable"})


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    try:
        with tmp_path.open("w", encoding=encoding) as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


class SourceInfo(Protocol):
    commit_sha: str
    dirty: bool


@dataclass(frozen=True)
class ReportSummary(PhaseResult):
    tsv_path: Path
    rows: int
    pass_count: int
    fail_count: int
    timeout_count: int
    candidate_count: int
    executed_count: int
    attempted_count: int
    skipped_count: int
    unreachable_count: int
    total_defined_count: int
    compatibility_pruned_count: int
    early_stop_pruned_count: int
    cross_scale_clean_pairs: int
    cross_scale_floor: int | None
    cross_scale_floor_breached: bool
    registry_pruned_count: int = 0
    unreachable_count_is_estimated: bool = False
    startup_failed_count: int = 0
    died_mid_platform_count: int = 0
    unfinalized: bool = False
    unvalidated_count: int = 0

    def exit_code(self) -> int:
        if self.aborted or self.unfinalized:
            return 2
        has_uncleared_cells = (
            self.fail_count > 0
            or self.timeout_count > 0
            or self.unreachable_count > 0
            or self.startup_failed_count > 0
            or self.died_mid_platform_count > 0
        )
        return 1 if has_uncleared_cells or self.cross_scale_floor_breached else 0


def render_row(
    cell: CellResult,
    *,
    validator_status: str = "",
    source_info: SourceInfo | None = None,
) -> str:
    source_commit_sha = source_info.commit_sha if source_info else ""
    source_dirty = str(source_info.dirty).lower() if source_info else ""
    return (
        f"{cell.platform}\t{cell.benchmark}\t{cell.scale}\t"
        f"{cell.status}\t{terminal_state(cell)}\t{cell.elapsed_s:.2f}\t"
        f"{cell.log_path}\t{cell.result_path or ''}\t{cell.submit_terminal_state}\t{validator_status}\t"
        f"{source_commit_sha}\t{source_dirty}\t{cell.throughput_check or ''}"
    )


def terminal_state(cell: CellResult) -> str:
    if cell.status == "passed":
        return "passed"
    if is_skipped_status(cell.status):
        return "skipped"
    if is_unreachable_status(cell.status):
        return "unreachable"
    if cell.status == "timed-out" or cell.exit_code == 124:
        return "timeout"
    if cell.exit_code in {-9, 137}:
        return "killed"
    if cell.result_path is None:
        if cell.exit_code == 0:
            return "no_json_exit_0"
        return "no_json_nonzero"
    if cell.status == "failed":
        return f"failed:{cell.submit_terminal_state}"
    if cell.submit_terminal_state:
        return cell.submit_terminal_state
    return cell.status


def _validator_status_for_path(validator_status_by_path: dict[Path, str], result_path: Path | None) -> str:
    if result_path is None:
        return ""
    return validator_status_by_path.get(result_path, "") or validator_status_by_path.get(result_path.resolve(), "")


def cross_scale_clean_pair_count(
    cells: Iterable[CellResult],
    rungs: list[float],
    validator_status_by_path: dict[Path, str] | None = None,
) -> int:
    if validator_status_by_path is None:
        validator_status_by_path = {}

    by_pb: dict[tuple[str, str], dict[float, CellResult]] = defaultdict(dict)
    for cell in cells:
        by_pb[(cell.platform, cell.benchmark)][cell.scale] = cell

    rungs_set = set(rungs)
    full = 0
    for pb, by_scale in by_pb.items():
        if not rungs_set.issubset(by_scale):
            continue
        ok = True
        for scale in rungs_set:
            cell = by_scale[scale]
            if cell.status != "passed":
                ok = False
                break
            v = _validator_status_for_path(validator_status_by_path, cell.result_path)
            if v and v not in ("clean", "warning_only"):
                ok = False
                break
        if ok:
            full += 1
    return full


def write_report(
    cells: Iterable[CellResult],
    *,
    output_path: Path,
    rungs: list[float] | None = None,
    cross_scale_floor: int | None = None,
    validator_status_by_path: dict[Path, str] | None = None,
    compatibility_pruned_count: int = 0,
    early_stop_pruned_count: int = 0,
    skipped_unreachable_count: int = 0,
    startup_failed_count: int = 0,
    registry_pruned_count: int = 0,
    died_mid_platform_count: int = 0,
    unreachable_count_is_estimated: bool = False,
    source_info: SourceInfo | None = None,
    run_status: str = "COMPLETED",
    abort_phase: str | None = None,
    abort_reason: str | None = None,
    finalized: bool = True,
) -> ReportSummary:
    if not finalized and run_status == "COMPLETED":
        run_status = "INCOMPLETE"
    rows = list(cells)
    executed_count = len(rows)

    pass_count = sum(1 for r in rows if r.status == "passed")
    fail_count = sum(1 for r in rows if r.status == "failed")
    timeout_count = sum(1 for r in rows if r.status == "timed-out")
    row_skipped_count = sum(1 for r in rows if is_skipped_status(r.status))
    row_unreachable_count = sum(1 for r in rows if is_unreachable_status(r.status))
    attempted_count = executed_count - row_skipped_count - row_unreachable_count
    skipped_count = row_skipped_count + compatibility_pruned_count + early_stop_pruned_count + registry_pruned_count
    unreachable_count = row_unreachable_count + skipped_unreachable_count
    total_defined_count = (
        attempted_count + skipped_count + unreachable_count + startup_failed_count + died_mid_platform_count
    )
    candidate_count = total_defined_count
    unvalidated_count = sum(
        1 for r in rows if r.status == "passed" and r.submit_terminal_state == SubmitTerminalState.unvalidated.value
    )

    lines: list[str] = [REPORT_HEADER + "\n"]
    for cell in rows:
        v = _validator_status_for_path(validator_status_by_path, cell.result_path) if validator_status_by_path else ""
        lines.append(render_row(cell, validator_status=v, source_info=source_info) + "\n")
    lines.append(
        "# "
        f"rows={len(rows)} "
        f"candidates={candidate_count} "
        f"executed={executed_count} "
        f"compatibility_pruned={compatibility_pruned_count} "
        f"early_stop_pruned={early_stop_pruned_count} "
        f"attempted={attempted_count} "
        f"skipped={skipped_count} "
        f"unreachable={unreachable_count} "
        f"startup_failed={startup_failed_count} "
        f"died_mid_platform={died_mid_platform_count} "
        f"total_defined={total_defined_count} "
        f"passed={pass_count} "
        f"failed={fail_count} "
        f"timed_out={timeout_count} "
        f"registry_pruned={registry_pruned_count}\n"
    )
    lines.append(
        "# "
        f"release_accounting passed={pass_count} failed={fail_count} timed_out={timeout_count} "
        f"attempted={attempted_count} skipped={skipped_count} unreachable={unreachable_count} "
        f"startup_failed={startup_failed_count} died_mid_platform={died_mid_platform_count} "
        f"total_defined={total_defined_count} "
        f"registry_pruned={registry_pruned_count}\n"
    )
    if unreachable_count or unreachable_count_is_estimated:
        attention = "required" if unreachable_count else "not_required"
        lines.append(
            f"# UNREACHABLE_CELLS={unreachable_count} release_gate_attention={attention} "
            f"unreachable_is_estimated={str(unreachable_count_is_estimated).lower()}\n"
        )
    if startup_failed_count:
        lines.append(f"# STARTUP_FAILED_CELLS={startup_failed_count} release_gate_attention=required\n")
    if died_mid_platform_count:
        lines.append(f"# DIED_MID_PLATFORM_CELLS={died_mid_platform_count} release_gate_attention=required\n")
    if unvalidated_count:
        lines.append(f"# UNVALIDATED_CELLS={unvalidated_count} release_gate_attention=required\n")
    footer = f"# run_status={run_status}"
    if source_info is not None:
        footer += f" source_commit_sha={source_info.commit_sha} source_dirty={str(source_info.dirty).lower()}"
    if abort_phase:
        footer += f" abort_phase={abort_phase}"
    if abort_reason:
        footer += f" abort_reason={_footer_value(abort_reason)}"
    lines.append(footer + "\n")

    atomic_write_text(output_path, "".join(lines))

    if rungs:
        clean_pairs = cross_scale_clean_pair_count(rows, rungs, validator_status_by_path=validator_status_by_path)
    else:
        clean_pairs = 0

    floor_breached = cross_scale_floor is not None and clean_pairs < cross_scale_floor

    return ReportSummary(
        phase="report",
        tsv_path=output_path,
        rows=len(rows),
        pass_count=pass_count,
        fail_count=fail_count,
        timeout_count=timeout_count,
        candidate_count=candidate_count,
        executed_count=executed_count,
        attempted_count=attempted_count,
        skipped_count=skipped_count,
        unreachable_count=unreachable_count,
        total_defined_count=total_defined_count,
        compatibility_pruned_count=compatibility_pruned_count,
        early_stop_pruned_count=early_stop_pruned_count,
        cross_scale_clean_pairs=clean_pairs,
        cross_scale_floor=cross_scale_floor,
        cross_scale_floor_breached=floor_breached,
        registry_pruned_count=registry_pruned_count,
        unreachable_count_is_estimated=unreachable_count_is_estimated,
        startup_failed_count=startup_failed_count,
        aborted=run_status in {"ABORTED", "BLOCKED"},
        abort_reason=abort_reason,
        died_mid_platform_count=died_mid_platform_count,
        unfinalized=not finalized,
        unvalidated_count=unvalidated_count,
    )


def _footer_value(value: str) -> str:
    return value.replace("\t", " ").replace("\n", " ")


def _normalized_status(status: str) -> str:
    return status.strip().lower()


def is_skipped_status(status: str) -> bool:
    return _normalized_status(status) in _SKIPPED_STATUSES


def is_unreachable_status(status: str) -> bool:
    return _normalized_status(status) in _UNREACHABLE_STATUSES


def parse_docker_up_events(lifecycle_log_text: str) -> list[tuple[_dt.datetime, str]]:
    events: list[tuple[_dt.datetime, str]] = []
    for raw in lifecycle_log_text.splitlines():
        line = raw.strip()
        if "[docker]" not in line or "action=up" not in line:
            continue
        timestamp_token = line.split(" ", 1)[0]
        try:
            timestamp = _dt.datetime.fromisoformat(timestamp_token)
        except ValueError:
            continue
        platform = "unknown"
        for token in line.split():
            if token.startswith("platform="):
                platform = token.split("=", 1)[1]
                break
        events.append((timestamp, platform))
    return events


def release_gate_ordering_violations(
    docker_stage_lifecycle_logs: Iterable[str],
    *,
    native_stage_completed_at: _dt.datetime,
) -> list[str]:
    violations: list[str] = []
    for log_text in docker_stage_lifecycle_logs:
        for timestamp, platform in parse_docker_up_events(log_text):
            if timestamp.tzinfo is None and native_stage_completed_at.tzinfo is not None:
                timestamp = timestamp.replace(tzinfo=native_stage_completed_at.tzinfo)
            if timestamp <= native_stage_completed_at:
                violations.append(
                    f"Docker stack '{platform}' started at {timestamp.isoformat()} "
                    f"at/before native+dataframe stage completion "
                    f"{native_stage_completed_at.isoformat()}"
                )
    return violations


def parse_lifecycle_host_id(lifecycle_log_text: str) -> str | None:
    for raw in lifecycle_log_text.splitlines():
        _, _, rest = raw.partition("[host] ")
        if not rest:
            continue
        for token in rest.split():
            if token.startswith("id="):
                return token.split("=", 1)[1] or None
    return None


def _same_host(left: str | None, right: str | None) -> bool:
    return left is None or right is None or left == right


def host_scoped_ordering_violations(
    docker_stage_lifecycle_logs: Iterable[str],
    *,
    earlier_stages: Iterable[tuple[_dt.datetime, str | None]],
) -> list[str]:
    earlier = list(earlier_stages)
    violations: list[str] = []
    for log_text in docker_stage_lifecycle_logs:
        host_id = parse_lifecycle_host_id(log_text)
        completions = [completed_at for completed_at, earlier_host in earlier if _same_host(host_id, earlier_host)]
        if not completions:
            continue
        violations.extend(release_gate_ordering_violations([log_text], native_stage_completed_at=max(completions)))
    return violations
