from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from _project.scripts.oracle_reviewers.policy import Policy
from _project.scripts.oracle_reviewers.retry import (
    ALL_ABSENT,
    INTEGRITY,
    OMP_INSTALL,
    UNREPORTED,
    State,
    decide_rerun,
    due_for_retry,
    next_state,
    recent_retries,
)
from _project.scripts.oracle_reviewers.selection import QUOTA_BACKOFF, PoolBlock, effective_until

pytestmark = [pytest.mark.unit, pytest.mark.fast]

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
HEAD = "a" * 40
OTHER = "b" * 40


def _state(
    outcome: str,
    *,
    head: str = HEAD,
    age: timedelta = timedelta(hours=2),
    retries: int = 0,
    cause: str | None = ALL_ABSENT,
) -> State:
    return State(
        7,
        head,
        outcome,
        NOW - age,
        tuple(NOW - timedelta(minutes=10 * (index + 1)) for index in range(retries)),
        pending_cause=cause if outcome == "pending" else None,
    )


def _decide(
    policy: Policy,
    previous: State | None,
    *,
    manual: bool = True,
    new_diff: bool = False,
):
    return decide_rerun(
        manual=manual,
        new_diff=new_diff,
        head_sha=HEAD,
        previous=previous,
        now=NOW,
        rules=policy.retry,
    )


def test_rerun_on_the_same_head_needs_every_reviewer_absent(policy: Policy) -> None:
    assert _decide(policy, _state("pending")).allowed


def test_manual_same_head_rereview_is_bounded_by_budget_and_backoff(policy: Policy) -> None:
    for outcome in ("success", "failure"):
        assert not _decide(policy, _state(outcome), manual=False).allowed
        decision = _decide(policy, _state(outcome))
        assert decision.allowed and "same-head re-review" in decision.reason
        assert not _decide(policy, _state(outcome, age=timedelta(minutes=30))).allowed
        exhausted = _decide(policy, _state(outcome, age=timedelta(days=2), retries=policy.retry.daily_budget))
        assert not exhausted.allowed and "budget" in exhausted.reason


def test_new_head_or_new_diff_reruns(policy: Policy) -> None:
    assert _decide(policy, _state("failure", head=OTHER)).allowed
    assert _decide(policy, _state("failure"), manual=False, new_diff=True).allowed
    assert _decide(policy, None).allowed


def test_pull_request_events_do_not_spend_the_budget(policy: Policy) -> None:
    assert _decide(policy, _state("pending", retries=policy.retry.daily_budget), manual=False).allowed


def test_daily_budget_is_shared_by_comments_and_retries(policy: Policy) -> None:
    decision = _decide(policy, _state("pending", age=timedelta(days=2), retries=policy.retry.daily_budget))
    assert not decision.allowed
    assert "budget" in decision.reason


def test_backoff_starts_at_one_hour_and_doubles(policy: Policy) -> None:
    assert policy.retry.backoff_start_minutes == 60
    assert not _decide(policy, _state("pending", age=timedelta(minutes=59))).allowed
    assert _decide(policy, _state("pending", age=timedelta(minutes=61))).allowed
    assert not _decide(policy, _state("pending", age=timedelta(minutes=90), retries=1)).allowed
    assert _decide(policy, _state("pending", age=timedelta(minutes=121), retries=1)).allowed


def test_next_state_records_manual_retries_in_a_rolling_window(policy: Policy) -> None:
    old = State(7, HEAD, "pending", NOW - timedelta(days=2), (NOW - timedelta(days=2), NOW - timedelta(hours=1)))
    state = next_state(
        previous=old, pr=7, head_sha=HEAD, outcome="pending", manual=True, now=NOW, pool_blocked_until={}
    )
    assert state.retries == (NOW - timedelta(hours=1), NOW)
    assert State.from_json(state.to_json()) == state
    event_state = next_state(
        previous=old, pr=7, head_sha=HEAD, outcome="success", manual=False, now=NOW, pool_blocked_until={}
    )
    assert event_state.retries == (NOW - timedelta(hours=1),)


def test_sweep_retries_only_pending_runs_on_the_current_head(policy: Policy) -> None:
    assert due_for_retry(_state("pending"), HEAD, NOW, policy.retry)
    assert not due_for_retry(_state("pending"), OTHER, NOW, policy.retry)
    assert not due_for_retry(_state("success"), HEAD, NOW, policy.retry)
    assert not due_for_retry(_state("pending", age=timedelta(minutes=5)), HEAD, NOW, policy.retry)


@pytest.mark.parametrize("cause", [INTEGRITY, UNREPORTED, None])
def test_same_head_rerun_is_refused_unless_every_reviewer_was_absent(policy: Policy, cause: str | None) -> None:
    previous = _state("pending", cause=cause)
    for manual in (True, False):
        decision = _decide(policy, previous, manual=manual)
        assert not decision.allowed
        assert "not absent reviewers" in decision.reason
    assert _decide(policy, previous, manual=False, new_diff=True).allowed
    assert not due_for_retry(previous, HEAD, NOW, policy.retry)
    assert _decide(policy, _state("pending", head=OTHER, cause=cause)).allowed


def test_all_absent_pending_is_retryable(policy: Policy) -> None:
    previous = _state("pending", cause=ALL_ABSENT)
    assert _decide(policy, previous).allowed
    assert due_for_retry(previous, HEAD, NOW, policy.retry)


def test_pending_cause_round_trips_and_clears_on_decisive_results(policy: Policy) -> None:
    pending = next_state(
        previous=None,
        pr=7,
        head_sha=HEAD,
        outcome="pending",
        manual=False,
        now=NOW,
        pool_blocked_until={},
        pending_cause=INTEGRITY,
    )
    assert State.from_json(pending.to_json()).pending_cause == INTEGRITY
    decided = next_state(
        previous=pending,
        pr=7,
        head_sha=HEAD,
        outcome="success",
        manual=False,
        now=NOW,
        pool_blocked_until={},
        pending_cause=INTEGRITY,
    )
    assert decided.pending_cause is None


def test_strikes_survive_pending_runs_and_never_go_down() -> None:
    previous = State(7, OTHER, "failure", NOW, strikes=2)
    pending = next_state(
        previous=previous,
        pr=7,
        head_sha=HEAD,
        outcome="pending",
        manual=False,
        now=NOW,
        pool_blocked_until={},
        pending_cause=ALL_ABSENT,
    )
    assert pending.strikes == 2
    lower = next_state(
        previous=pending,
        pr=7,
        head_sha=HEAD,
        outcome="success",
        manual=False,
        now=NOW,
        pool_blocked_until={},
        strikes=1,
    )
    assert lower.strikes == 2
    higher = next_state(
        previous=lower, pr=7, head_sha=HEAD, outcome="failure", manual=False, now=NOW, pool_blocked_until={}, strikes=3
    )
    assert higher.strikes == 3
    assert State.from_json(higher.to_json()) == higher
    legacy = {key: value for key, value in higher.to_json().items() if key != "strikes"}
    assert State.from_json({**legacy, "reviewed": {"head_sha": OTHER}}).strikes == 0


def test_the_owner_reset_covers_one_attempt_without_restoring_the_budget(policy: Policy) -> None:
    inside = _state("pending", age=timedelta(minutes=10), retries=policy.retry.daily_budget)
    assert not _decide(policy, inside).allowed
    reset_at = NOW - timedelta(minutes=5)
    reset = decide_rerun(
        manual=True, new_diff=False, head_sha=HEAD, previous=inside, now=NOW, rules=policy.retry, reset_at=reset_at
    )
    assert reset.allowed and "reset by the owner" in reset.reason
    assert due_for_retry(inside, HEAD, NOW, policy.retry, reset_at)
    recorded = next_state(
        previous=inside,
        pr=7,
        head_sha=HEAD,
        outcome="pending",
        manual=True,
        now=NOW,
        pool_blocked_until={},
        pending_cause=ALL_ABSENT,
    )
    assert recorded.retries == inside.retries + (NOW,)
    spent = decide_rerun(
        manual=True, new_diff=False, head_sha=HEAD, previous=recorded, now=NOW, rules=policy.retry, reset_at=reset_at
    )
    assert not spent.allowed and "budget" in spent.reason
    assert not due_for_retry(recorded, HEAD, NOW, policy.retry, reset_at)


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_the_owner_reset_never_rerolls_a_decisive_result(policy: Policy, outcome: str) -> None:
    decided = _state(outcome, age=timedelta(minutes=10))
    reset_at = NOW - timedelta(minutes=5)
    rerun = decide_rerun(
        manual=True, new_diff=False, head_sha=HEAD, previous=decided, now=NOW, rules=policy.retry, reset_at=reset_at
    )
    assert not rerun.allowed and "backoff" in rerun.reason
    assert "reset" not in rerun.reason
    assert not due_for_retry(decided, HEAD, NOW, policy.retry, reset_at)


def test_a_pending_earlier_heads_retries_do_not_refuse_the_new_heads_retry(policy: Policy) -> None:
    spent_elsewhere = _state("pending", head=OTHER, age=timedelta(minutes=10), retries=policy.retry.daily_budget)
    assert _decide(policy, spent_elsewhere).allowed


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_a_decisive_earlier_heads_spent_budget_still_refuses_a_manual_rerun_on_a_new_head(
    policy: Policy, outcome: str
) -> None:
    spent = _state(outcome, head=OTHER, age=timedelta(minutes=10), retries=policy.retry.daily_budget)
    decision = _decide(policy, spent)
    assert not decision.allowed
    assert "daily retry budget" in decision.reason
    assert _decide(policy, spent, manual=False).allowed


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_a_new_head_carries_the_decisive_earlier_heads_retries_forward(policy: Policy, outcome: str) -> None:
    old = _state(outcome, head=OTHER, retries=3)
    pushed = next_state(
        previous=old, pr=7, head_sha=HEAD, outcome="pending", manual=False, now=NOW, pool_blocked_until={}
    )
    assert pushed.retries == old.retries
    assert recent_retries(None, NOW) == ()


def test_an_owner_reset_for_another_head_or_before_the_state_changes_nothing(policy: Policy) -> None:
    inside = _state("pending", age=timedelta(minutes=10))
    older = NOW - timedelta(minutes=30)
    assert not due_for_retry(inside, HEAD, NOW, policy.retry, older)
    other = decide_rerun(
        manual=True, new_diff=False, head_sha=HEAD, previous=_state("pending", head=OTHER), now=NOW, rules=policy.retry
    )
    assert other.allowed and "reset" not in other.reason


def test_pool_blocks_round_trip_through_the_state_json() -> None:
    block = PoolBlock(NOW + timedelta(hours=2), "anthropic", NOW - timedelta(minutes=5))
    state = State(7, HEAD, "pending", NOW, (), {"claude": block}, ALL_ABSENT)
    assert State.from_json(state.to_json()) == state
    assert state.to_json()["pool_blocked_until"] == {"claude": block.to_json()}


def test_a_legacy_pool_block_loads_with_an_unknown_provider() -> None:
    until = NOW + timedelta(days=6)
    data = {
        "pr": 7,
        "head_sha": HEAD,
        "outcome": "pending",
        "updated_at": NOW.isoformat(),
        "pool_blocked_until": {"claude": until.isoformat()},
    }
    block = State.from_json(data).pool_blocked_until["claude"]
    assert block == PoolBlock(until, "", NOW)
    assert effective_until("claude", block) == NOW + QUOTA_BACKOFF


def test_a_failed_install_is_not_re_dispatched_by_the_sweep_but_a_manual_rerun_may_retry_it(policy: Policy) -> None:
    previous = _state("pending", cause=OMP_INSTALL)
    assert not due_for_retry(previous, HEAD, NOW, policy.retry)
    assert not _decide(policy, previous, manual=False).allowed
    assert _decide(policy, previous, manual=True).allowed
    spent = _state("pending", cause=OMP_INSTALL, age=timedelta(minutes=5))
    assert not _decide(policy, spent, manual=True).allowed
