from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from .policy import RetryRules
from .selection import PoolBlock

SUCCESS = "success"
FAILURE = "failure"
PENDING = "pending"
DECISIVE = (SUCCESS, FAILURE)
ALL_ABSENT = "all-absent"
INTEGRITY = "integrity"
UNREPORTED = "unreported"
OVERSIZE = "oversize"
MARKER_LIMIT = "marker-limit"
OMP_INSTALL = "omp-install"
UNPUBLISHED = "unpublished"
MANUAL_ONLY_CAUSES = (OMP_INSTALL, UNPUBLISHED)
PENDING_CAUSES = (ALL_ABSENT, INTEGRITY, UNREPORTED, OVERSIZE, MARKER_LIMIT, OMP_INSTALL, UNPUBLISHED)
WINDOW = timedelta(hours=24)


def _pool_block(value: Any, updated_at: datetime) -> PoolBlock:
    if isinstance(value, str):
        return PoolBlock(datetime.fromisoformat(value), "", updated_at)
    return PoolBlock.from_json(value)


@dataclass(frozen=True)
class State:
    pr: int
    head_sha: str
    outcome: str
    updated_at: datetime
    retries: tuple[datetime, ...] = ()
    pool_blocked_until: Mapping[str, PoolBlock] = field(default_factory=dict)
    pending_cause: str | None = None
    strikes: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "pr": self.pr,
            "head_sha": self.head_sha,
            "outcome": self.outcome,
            "updated_at": self.updated_at.isoformat(),
            "retries": [moment.isoformat() for moment in self.retries],
            "pool_blocked_until": {pool: block.to_json() for pool, block in self.pool_blocked_until.items()},
            "pending_cause": self.pending_cause,
            "strikes": self.strikes,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> State:
        updated_at = datetime.fromisoformat(data["updated_at"])
        return cls(
            pr=int(data["pr"]),
            head_sha=str(data["head_sha"]),
            outcome=str(data["outcome"]),
            updated_at=updated_at,
            retries=tuple(datetime.fromisoformat(item) for item in data.get("retries", [])),
            pool_blocked_until={
                pool: _pool_block(value, updated_at) for pool, value in data.get("pool_blocked_until", {}).items()
            },
            pending_cause=data.get("pending_cause"),
            strikes=int(data.get("strikes") or 0),
        )


@dataclass(frozen=True)
class RerunDecision:
    allowed: bool
    reason: str


def recent_retries(state: State | None, now: datetime) -> tuple[datetime, ...]:
    if state is None:
        return ()
    return tuple(moment for moment in state.retries if now - moment < WINDOW)


def reset_applies(previous: State | None, head_sha: str, reset_at: datetime | None) -> bool:
    return (
        reset_at is not None
        and previous is not None
        and previous.head_sha == head_sha
        and previous.outcome == PENDING
        and previous.pending_cause == ALL_ABSENT
        and reset_at > previous.updated_at
    )


def decide_rerun(
    *,
    manual: bool,
    new_diff: bool,
    head_sha: str,
    previous: State | None,
    now: datetime,
    rules: RetryRules,
    reset_at: datetime | None = None,
) -> RerunDecision:
    same_head = previous is not None and previous.head_sha == head_sha
    if same_head and previous is not None and previous.outcome in DECISIVE and not new_diff and not manual:
        return RerunDecision(False, f"head {head_sha} already has a {previous.outcome} result")
    if same_head and previous is not None and previous.outcome == PENDING and not new_diff:
        if previous.pending_cause != ALL_ABSENT and not (manual and previous.pending_cause in MANUAL_ONLY_CAUSES):
            cause = previous.pending_cause or "unknown"
            return RerunDecision(
                False, f"the last run on head {head_sha} was pending for {cause}, not absent reviewers"
            )
    if not manual:
        return RerunDecision(True, "pull request event")
    if reset_applies(previous, head_sha, reset_at):
        return RerunDecision(True, "retry backoff reset by the owner")
    inherits_budget = previous is None or same_head or previous.outcome in DECISIVE
    retries = recent_retries(previous, now) if inherits_budget else ()
    if len(retries) >= rules.daily_budget:
        return RerunDecision(False, f"the daily retry budget of {rules.daily_budget} is used")
    if same_head and previous is not None:
        wait = timedelta(minutes=rules.backoff_start_minutes * 2 ** len(retries))
        ready_at = previous.updated_at + wait
        if now < ready_at:
            return RerunDecision(False, f"backoff: the next retry is allowed after {ready_at.isoformat()}")
        if previous.outcome in DECISIVE:
            return RerunDecision(True, "manual same-head re-review within budget")
    return RerunDecision(True, "retry within budget")


def next_state(
    *,
    previous: State | None,
    pr: int,
    head_sha: str,
    outcome: str,
    manual: bool,
    now: datetime,
    pool_blocked_until: Mapping[str, PoolBlock],
    pending_cause: str | None = None,
    strikes: int | None = None,
) -> State:
    retries = recent_retries(previous, now) + ((now,) if manual else ())
    cause = pending_cause if outcome == PENDING else None
    kept = max(strikes or 0, previous.strikes if previous else 0)
    return State(pr, head_sha, outcome, now, retries, dict(pool_blocked_until), cause, kept)


def attempt_state(*, previous: State | None, pr: int, head_sha: str, published: State) -> State:
    strikes = previous.strikes if previous is not None else 0
    if previous is not None and previous.head_sha == head_sha:
        return replace(previous, retries=published.retries, updated_at=published.updated_at)
    return State(pr, head_sha, PENDING, published.updated_at, published.retries, {}, UNPUBLISHED, strikes)


def due_for_retry(
    state: State, current_head: str, now: datetime, rules: RetryRules, reset_at: datetime | None = None
) -> bool:
    if state.outcome != PENDING or state.head_sha != current_head or state.pending_cause in MANUAL_ONLY_CAUSES:
        return False
    return decide_rerun(
        manual=True, new_diff=False, head_sha=current_head, previous=state, now=now, rules=rules, reset_at=reset_at
    ).allowed
