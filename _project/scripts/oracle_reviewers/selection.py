from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .policy import Policy, Reviewer

PASS = "pass"
FAIL = "fail"
ABSENT = "absent"
REVIEW = "review"
PENDING = "pending"
BRIEF_MODES = ("inline", "file-list", "oversize")
QUOTA_BACKOFF = timedelta(hours=1)


@dataclass(frozen=True)
class Attempt:
    slot: int
    reviewer: str
    outcome: str
    absence: str | None = None
    detail: str = ""
    reset_at: datetime | None = None


@dataclass(frozen=True)
class Step:
    kind: str
    reviewer: Reviewer | None = None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class SelectionInput:
    chain: Sequence[Reviewer]
    diversity_exempt: frozenset[str]
    excluded_families: frozenset[str]
    brief_mode: str
    max_attempts: int
    now: datetime
    pool_blocked_until: Mapping[str, datetime] = field(default_factory=dict)

    @classmethod
    def from_plan(cls, plan: Mapping[str, Any], now: datetime) -> SelectionInput:
        return cls(
            chain=[Reviewer.from_json(item) for item in plan["chain"]],
            diversity_exempt=frozenset(plan["diversity_exempt"]),
            excluded_families=frozenset(plan["excluded_families"]),
            brief_mode=plan["brief_mode"],
            max_attempts=int(plan["max_attempts"]),
            now=now,
            pool_blocked_until={
                pool: datetime.fromisoformat(value) for pool, value in plan.get("pool_blocked_until", {}).items()
            },
        )


def excluded_families(labels: Iterable[str], policy: Policy) -> frozenset[str]:
    prefix = policy.author_label_prefix
    named = {label[len(prefix) :] for label in labels if label.startswith(prefix)}
    known = frozenset(name for name in named if name in policy.families)
    return known or frozenset({policy.default_author_family})


def _skip_reason(
    reviewer: Reviewer,
    selection: SelectionInput,
    attempted: Mapping[str, Attempt],
    quota_pools: set[str],
) -> str | None:
    if reviewer.name in attempted:
        attempt = attempted[reviewer.name]
        return f"absent ({attempt.absence}{': ' + attempt.detail if attempt.detail else ''})"
    if not reviewer.enabled:
        return f"disabled ({reviewer.disabled_reason})"
    if reviewer.family in selection.excluded_families and reviewer.name not in selection.diversity_exempt:
        return f"skipped: same family as the author ({reviewer.family})"
    if reviewer.pool in quota_pools:
        return f"skipped: {reviewer.pool} pool out of quota in this run"
    blocked_until = selection.pool_blocked_until.get(reviewer.pool)
    if blocked_until is not None and blocked_until > selection.now:
        return f"skipped: {reviewer.pool} pool out of quota until {blocked_until.isoformat()}"
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
    attempted = {attempt.reviewer: attempt for attempt in ordered}
    quota_pools = {
        by_name[attempt.reviewer].pool
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


def pool_resets(selection: SelectionInput, attempts: Sequence[Attempt]) -> dict[str, datetime]:
    by_name = {reviewer.name: reviewer for reviewer in selection.chain}
    resets = {pool: until for pool, until in selection.pool_blocked_until.items() if until > selection.now}
    for attempt in attempts:
        if attempt.absence == "quota" and attempt.reviewer in by_name:
            pool = by_name[attempt.reviewer].pool
            until = attempt.reset_at or selection.now + QUOTA_BACKOFF
            resets[pool] = max(resets.get(pool, until), until)
    return resets
