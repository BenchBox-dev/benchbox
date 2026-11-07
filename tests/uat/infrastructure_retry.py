from __future__ import annotations

import dataclasses
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path

from benchbox.utils.clock import elapsed_seconds, mono_time
from tests.uat.cells_io import cell_log_tail, failed_query_details
from tests.uat.failure_signature import FailureEvidence, classify_failure
from tests.uat.gate_summary import InfrastructureRetryAccounting
from tests.uat.phases.report import terminal_state
from tests.uat.runner import CellResult

RETRY_PASSED = "retry_passed"
RETRY_FAILED = "retry_failed"
RETRY_SKIPPED_INSUFFICIENT_TIME = "skipped_insufficient_time"

_RETRYABLE_STATUSES = frozenset({"failed", "timed-out"})

CellRunner = Callable[..., CellResult]


def evidence_from_cell(cell: CellResult) -> FailureEvidence:
    failed = failed_query_details(cell)
    return FailureEvidence(
        status=cell.status,
        terminal_state=terminal_state(cell),
        submit_terminal_state=cell.submit_terminal_state,
        failed_query_errors=tuple(entry["error"] for entry in failed),
        failure_text=cell_log_tail(Path(cell.log_path)),
    )


def infrastructure_signature(cell: CellResult) -> str | None:
    if cell.status not in _RETRYABLE_STATUSES:
        return None
    failure = classify_failure(evidence_from_cell(cell))
    if failure is None or not failure.is_infrastructure:
        return None
    return failure.signature


def with_infrastructure_retry(
    runner: CellRunner,
    *,
    job_budget_s: int,
    clock: Callable[[], float] = mono_time,
    started: float | None = None,
) -> CellRunner:
    started_at = clock() if started is None else started

    def run(platform: str, benchmark: str, scale: float, **kwargs) -> CellResult:
        first = runner(platform, benchmark, scale, **kwargs)
        signature = infrastructure_signature(first)
        if signature is None:
            return first
        remaining_s = job_budget_s - elapsed_seconds(started_at, clock())
        if remaining_s < kwargs["timeout_s"]:
            return dataclasses.replace(first, retry_signature=signature, retry_outcome=RETRY_SKIPPED_INSUFFICIENT_TIME)
        second = runner(platform, benchmark, scale, **kwargs)
        return dataclasses.replace(
            second,
            attempt=2,
            retry_signature=signature,
            retry_outcome=RETRY_PASSED if second.status == "passed" else RETRY_FAILED,
            first_attempt_status=first.status,
            first_attempt_log_path=first.log_path,
            first_attempt_elapsed_s=first.elapsed_s,
        )

    return run


def retry_accounting(results: Iterable[CellResult], *, job_budget_s: int | None) -> InfrastructureRetryAccounting:
    outcomes = Counter()
    by_signature: Counter[str] = Counter()
    for cell in results:
        if cell.retry_outcome is None:
            continue
        outcomes[cell.retry_outcome] += 1
        if cell.retry_outcome != RETRY_SKIPPED_INSUFFICIENT_TIME and cell.retry_signature:
            by_signature[cell.retry_signature] += 1
    retry_passed = outcomes[RETRY_PASSED]
    retry_failed = outcomes[RETRY_FAILED]
    skipped = outcomes[RETRY_SKIPPED_INSUFFICIENT_TIME]
    return InfrastructureRetryAccounting(
        job_budget_s=job_budget_s,
        triggered=retry_passed + retry_failed + skipped,
        retried=retry_passed + retry_failed,
        retry_passed=retry_passed,
        retry_failed=retry_failed,
        skipped_insufficient_time=skipped,
        retried_by_signature=dict(sorted(by_signature.items())),
    )
