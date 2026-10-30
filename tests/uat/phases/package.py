from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from tests.uat.config import VALID_TERMINAL_STATES, UATConfig
from tests.uat.phases import PhaseResult
from tests.uat.runner import SubmitTerminalState, classify_for_submit

PR_STUB_TERMINAL_STATES: frozenset[str] = frozenset({"draft-pr", "merged-to-published-results"})


@dataclass(frozen=True)
class PackageResult(PhaseResult):
    terminal_state: str
    submissions_dir: Path
    invocations: tuple[tuple[str, ...], ...]
    success_count: int
    failure_count: int

    def exit_code(self) -> int:
        if self.aborted:
            return 2
        return 0 if self.failure_count == 0 else 1


class PackagePhaseError(RuntimeError):
    pass


def _resolve_state(config: UATConfig) -> str:
    state = config.package.submit_terminal_state
    if state is None:
        raise PackagePhaseError("package.submit_terminal_state is required when the package phase is enabled")
    if state not in VALID_TERMINAL_STATES:
        raise PackagePhaseError(f"submit_terminal_state {state!r} not in {sorted(VALID_TERMINAL_STATES)}")
    return state


def _resolve_service(config: UATConfig, state: str) -> str | None:
    service = config.package.service
    if state == "cloud-uploaded" and not service:
        raise PackagePhaseError("submit_terminal_state=cloud-uploaded requires package.service")
    return service


def _build_submit_argv(
    state: str,
    *,
    result_path: Path,
    submissions_dir: Path,
    service: str | None,
) -> list[str]:
    if state in ("local-stage", "draft-pr", "merged-to-published-results"):
        return ["benchbox", "publish", "run", str(result_path), "--target", str(submissions_dir)]
    if state == "cloud-uploaded":
        assert service is not None
        return ["benchbox", "submit", str(result_path), "--service", service]
    raise PackagePhaseError(f"Unhandled terminal state {state!r}")


def run_package(
    config: UATConfig,
    *,
    result_paths: list[Path],
    submissions_dir: Path,
    runner=subprocess.run,
    warn=None,
    classify_results: bool = True,
) -> PackageResult:
    if warn is None:

        def warn(msg: str) -> None:
            print(msg, file=sys.stderr)

    try:
        state = _resolve_state(config)
        service = _resolve_service(config, state)
    except PackagePhaseError as exc:
        return PackageResult(
            phase="package",
            aborted=True,
            abort_reason=str(exc),
            terminal_state=config.package.submit_terminal_state or "",
            submissions_dir=submissions_dir,
            invocations=(),
            success_count=0,
            failure_count=0,
        )
    if state in PR_STUB_TERMINAL_STATES:
        warn(
            f"[package] WARNING: submit_terminal_state={state!r} is a stub — "
            "this run will produce a local stage at "
            f"{submissions_dir}; PR-opening to published-results is not yet "
            "implemented. See _project/specs/uat-framework.md."
        )
    submissions_dir.mkdir(parents=True, exist_ok=True)

    invocations: list[tuple[str, ...]] = []
    success = 0
    failure = 0
    for result_path in result_paths:
        if classify_results and result_path.exists():
            submit_state = classify_for_submit(result_path)
            if submit_state in {
                SubmitTerminalState.unofficial,
                SubmitTerminalState.unvalidated,
            }:
                warn(
                    f"[package] skipping {submit_state.value} result {result_path}: "
                    f"submit_terminal_state={submit_state.value}"
                )
                continue
            if submit_state is not SubmitTerminalState.submittable:
                warn(
                    f"[package] refusing unsubmittable result {result_path}: submit_terminal_state={submit_state.value}"
                )
                failure += 1
                continue
        argv = _build_submit_argv(
            state,
            result_path=result_path,
            submissions_dir=submissions_dir,
            service=service,
        )
        invocations.append(tuple(argv))
        completed = runner(argv, check=False)
        if getattr(completed, "returncode", 0) == 0:
            success += 1
        else:
            failure += 1

    return PackageResult(
        phase="package",
        terminal_state=state,
        submissions_dir=submissions_dir,
        invocations=tuple(invocations),
        success_count=success,
        failure_count=failure,
    )
