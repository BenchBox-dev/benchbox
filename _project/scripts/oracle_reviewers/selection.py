from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .absence import EMPTY, ERROR, INCOMPLETE, INVALID
from .policy import Reviewer
from .recovery import RETRY, STOP, Recovery

PASS = "pass"
FAIL = "fail"
ABSENT = "absent"
REVIEW = "review"
PENDING = "pending"
BRIEF_MODES = ("inline", "file-list", "oversize")
QUOTA_BACKOFF = timedelta(hours=1)
PRIMARY_BLOCK_CAP = timedelta(hours=24)
POOL_PROVIDERS = {"claude": "anthropic", "codex": "openai-codex"}
JOB_FAILURE_DETAIL = "the reviewer job did not finish"
RETRYABLE_ABSENCES = frozenset({INCOMPLETE, EMPTY, ERROR})
MAX_ATTEMPTS_PER_REVIEWER = 2


@dataclass(frozen=True)
class Attempt:
    slot: int
    reviewer: str
    outcome: str
    absence: str = ""
    detail: str = ""
    reset_at: datetime | None = None
    provider: str = ""
    unread: tuple[str, ...] = ()
    recovery: Recovery | None = None


@dataclass(frozen=True)
class PoolBlock:
    until: datetime
    provider: str
    recorded_at: datetime

    def to_json(self) -> dict[str, str]:
        return {
            "until": self.until.isoformat(),
            "provider": self.provider,
            "recorded_at": self.recorded_at.isoformat(),
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> PoolBlock:
        return cls(
            datetime.fromisoformat(data["until"]),
            str(data.get("provider") or ""),
            datetime.fromisoformat(data["recorded_at"]),
        )


@dataclass(frozen=True)
class Step:
    kind: str
    reviewer: Reviewer | None = None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class SelectionInput:
    chain: Sequence[Reviewer]
    brief_mode: str
    max_attempts: int
    now: datetime
    pool_blocked_until: Mapping[str, PoolBlock] = field(default_factory=dict)

    @classmethod
    def from_plan(cls, plan: Mapping[str, Any], now: datetime) -> SelectionInput:
        return cls(
            chain=[Reviewer.from_json(item) for item in plan["chain"]],
            brief_mode=plan["brief_mode"],
            max_attempts=int(plan["max_attempts"]),
            now=now,
            pool_blocked_until={
                pool: PoolBlock.from_json(value) for pool, value in plan.get("pool_blocked_until", {}).items()
            },
        )


def _source(provider: str) -> str:
    return provider or "an unknown provider"


def _from_primary(pool: str, provider: str) -> bool:
    return bool(provider) and POOL_PROVIDERS.get(pool) == provider


def effective_until(pool: str, block: PoolBlock) -> datetime:
    cap = PRIMARY_BLOCK_CAP if _from_primary(pool, block.provider) else QUOTA_BACKOFF
    return min(block.until, block.recorded_at + cap)


def active_blocks(blocks: Mapping[str, PoolBlock], now: datetime, *, same_head: bool = True) -> dict[str, PoolBlock]:
    return {
        pool: block
        for pool, block in blocks.items()
        if effective_until(pool, block) > now and (same_head or _from_primary(pool, block.provider))
    }


def needs_recovery(attempt: Attempt) -> bool:
    return attempt.outcome == ABSENT and attempt.absence == INVALID and attempt.detail != JOB_FAILURE_DETAIL


def stop_reason(attempt: Attempt) -> str:
    detail = " ".join(attempt.detail.split())
    return f"stopped: the process is blocked ({detail})"


def _may_retry(history: Sequence[Attempt]) -> bool:
    last = history[-1]
    if len(history) >= MAX_ATTEMPTS_PER_REVIEWER or last.outcome != ABSENT or last.detail == JOB_FAILURE_DETAIL:
        return False
    if needs_recovery(last):
        return last.recovery is not None and last.recovery.choice == RETRY
    return last.absence in RETRYABLE_ABSENCES


def retry_unread(attempts: Sequence[Attempt], reviewer: str, slot: int) -> tuple[str, ...]:
    history = sorted(
        (item for item in attempts if item.reviewer == reviewer and item.slot < slot), key=lambda a: a.slot
    )
    if not history or not _may_retry(history):
        return ()
    return history[-1].unread if history[-1].absence == INCOMPLETE else ()


def _skip_reason(
    reviewer: Reviewer,
    selection: SelectionInput,
    attempted: Mapping[str, Sequence[Attempt]],
    quota_pools: Mapping[str, str],
) -> str | None:
    history = attempted.get(reviewer.name, ())
    if history and not _may_retry(history):
        attempt = history[-1]
        return f"absent ({attempt.absence}{': ' + attempt.detail if attempt.detail else ''})"
    if not reviewer.enabled:
        return f"disabled ({reviewer.disabled_reason})"
    if reviewer.pool in quota_pools:
        return f"skipped: {reviewer.pool} pool blocked in this run, {_source(quota_pools[reviewer.pool])} returned a quota error"
    block = selection.pool_blocked_until.get(reviewer.pool)
    if block is not None and effective_until(reviewer.pool, block) > selection.now:
        return (
            f"skipped: {reviewer.pool} pool blocked until {effective_until(reviewer.pool, block).isoformat()}, "
            f"{_source(block.provider)} returned a quota error at {block.recorded_at.isoformat()}"
        )
    if selection.brief_mode == "oversize":
        return "skipped: the brief exceeds the size cap even without the diff"
    if selection.brief_mode == "file-list" and not (reviewer.read_only == "hard" and reviewer.reads_files):
        return "skipped: the diff exceeds the brief cap and only hard read-only reviewers that read files may review it"
    return None


def next_step(selection: SelectionInput, attempts: Sequence[Attempt]) -> Step:
    ordered = sorted(attempts, key=lambda attempt: attempt.slot)
    by_name = {reviewer.name: reviewer for reviewer in selection.chain}
    for attempt in ordered:
        if attempt.outcome == FAIL:
            return Step(FAIL, by_name.get(attempt.reviewer), (f"{attempt.reviewer}: did not clear the change",))
        if attempt.outcome == PASS:
            return Step(PASS, by_name.get(attempt.reviewer), (f"{attempt.reviewer}: cleared the change",))
    if ordered and needs_recovery(ordered[-1]):
        last = ordered[-1]
        if last.recovery is not None and last.recovery.choice == STOP:
            return Step(PENDING, None, (stop_reason(last),))
        if last.recovery is None and len(ordered) < selection.max_attempts:
            return Step(PENDING, None, (f"{last.reviewer}: absent (invalid): the recovery is not recorded",))
    attempted: dict[str, list[Attempt]] = {}
    for attempt in ordered:
        attempted.setdefault(attempt.reviewer, []).append(attempt)
    quota_pools = {
        by_name[attempt.reviewer].pool: attempt.provider
        for attempt in ordered
        if attempt.absence == "quota" and attempt.reviewer in by_name
    }
    reasons: list[str] = []
    for reviewer in selection.chain:
        reason = _skip_reason(reviewer, selection, attempted, quota_pools)
        if reason is None and len(ordered) >= selection.max_attempts:
            reason = "skipped: no attempt slot left in this run"
        if reason is not None:
            reasons.append(f"{reviewer.name}: {reason}")
            continue
        return Step(REVIEW, reviewer)
    return Step(PENDING, None, tuple(reasons))


def pool_resets(selection: SelectionInput, attempts: Sequence[Attempt]) -> dict[str, PoolBlock]:
    by_name = {reviewer.name: reviewer for reviewer in selection.chain}
    resets = active_blocks(selection.pool_blocked_until, selection.now)
    for attempt in attempts:
        if attempt.absence == "quota" and attempt.reviewer in by_name:
            pool = by_name[attempt.reviewer].pool
            primary = _from_primary(pool, attempt.provider)
            requested = attempt.reset_at if primary and attempt.reset_at else selection.now + QUOTA_BACKOFF
            cap = PRIMARY_BLOCK_CAP if primary else QUOTA_BACKOFF
            block = PoolBlock(min(requested, selection.now + cap), attempt.provider, selection.now)
            kept = resets.get(pool)
            if kept is None or block.until >= kept.until:
                resets[pool] = block
    return resets
