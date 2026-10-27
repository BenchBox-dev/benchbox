from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import yaml

from _project.scripts.oracle_reviewers.policy import Reviewer, load_policy
from _project.scripts.oracle_reviewers.selection import (
    ABSENT,
    FAIL,
    PASS,
    PENDING,
    POOL_PROVIDERS,
    PRIMARY_BLOCK_CAP,
    QUOTA_BACKOFF,
    REVIEW,
    Attempt,
    PoolBlock,
    SelectionInput,
    active_blocks,
    next_step,
    pool_resets,
)

from .conftest import POLICY_PATH

OMP_CONFIG = POLICY_PATH.parent / "oracle-omp-config.yml"

RETRYABLE = ("invalid", "incomplete", "empty", "error")

pytestmark = [pytest.mark.unit, pytest.mark.fast]

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def _reviewer(name: str, **overrides: object) -> Reviewer:
    fields: dict[str, object] = {
        "name": name,
        "harness": "omp",
        "family": "test",
        "pool": f"{name}-pool",
        "model": "model",
        "effort": "",
        "role": "role",
        "read_only": "hard",
        "reads_files": True,
        "timeout_minutes": 10,
        "enabled": True,
        "disabled_reason": "",
    }
    fields.update(overrides)
    return Reviewer(
        name=str(fields["name"]),
        harness=str(fields["harness"]),
        family=str(fields["family"]),
        pool=str(fields["pool"]),
        model=str(fields["model"]),
        effort=str(fields["effort"]),
        role=str(fields["role"]),
        read_only=str(fields["read_only"]),
        reads_files=bool(fields["reads_files"]),
        timeout_minutes=int(fields["timeout_minutes"]),
        enabled=bool(fields["enabled"]),
        disabled_reason=str(fields["disabled_reason"]),
    )


def _input(chain: list[Reviewer], **overrides: Any) -> SelectionInput:
    return replace(
        SelectionInput(chain=chain, brief_mode="inline", max_attempts=5, now=NOW),
        **overrides,
    )


def test_single_reviewer_chain_reviews_first() -> None:
    step = next_step(_input([_reviewer("r1")]), [])
    assert step.kind == REVIEW
    assert step.reviewer is not None and step.reviewer.name == "r1"


def test_multi_reviewer_chain_advances_past_attempted() -> None:
    chain = [_reviewer("r1"), _reviewer("r2")]
    attempts = [Attempt(1, "r1", ABSENT, "timeout")]
    step = next_step(_input(chain), attempts)
    assert step.kind == REVIEW
    assert step.reviewer is not None and step.reviewer.name == "r2"


def test_pass_terminal_wins() -> None:
    chain = [_reviewer("r1"), _reviewer("r2")]
    attempts = [Attempt(1, "r1", PASS), Attempt(2, "r2", ABSENT, "error")]
    step = next_step(_input(chain), attempts)
    assert step.kind == PASS
    assert step.reviewer is not None and step.reviewer.name == "r1"
    assert step.reasons == ("r1: cleared the change",)


def test_fail_terminal_wins() -> None:
    chain = [_reviewer("r1"), _reviewer("r2")]
    attempts = [Attempt(1, "r1", ABSENT, "error"), Attempt(2, "r2", FAIL)]
    step = next_step(_input(chain), attempts)
    assert step.kind == FAIL
    assert step.reviewer is not None and step.reviewer.name == "r2"
    assert step.reasons == ("r2: did not clear the change",)


def test_earliest_terminal_attempt_wins() -> None:
    chain = [_reviewer("r1"), _reviewer("r2")]
    attempts = [Attempt(2, "r1", FAIL), Attempt(1, "r2", PASS)]
    step = next_step(_input(chain), attempts)
    assert step.kind == PASS
    assert step.reviewer is not None and step.reviewer.name == "r2"


def test_attempted_reviewers_show_absent_reasons() -> None:
    chain = [_reviewer("r1"), _reviewer("r2")]
    attempts = [Attempt(1, "r1", ABSENT, "auth", "boom")]
    step = next_step(_input(chain), attempts)
    assert step.kind == REVIEW
    assert step.reviewer is not None and step.reviewer.name == "r2"


def test_attempted_reason_recorded_when_chain_pending() -> None:
    chain = [_reviewer("r1", enabled=False, disabled_reason="off"), _reviewer("r2")]
    attempts = [Attempt(1, "r2", ABSENT, "auth", "boom")]
    step = next_step(_input(chain, max_attempts=1), attempts)
    assert step.kind == PENDING
    assert "r2: absent (auth: boom)" in step.reasons


def test_attempted_reason_without_detail() -> None:
    chain = [_reviewer("r1")]
    attempts = [Attempt(1, "r1", ABSENT, "timeout")]
    step = next_step(_input(chain, max_attempts=0), attempts)
    assert step.kind == PENDING
    assert step.reasons == ("r1: absent (timeout)",)


def test_disabled_reviewers_skipped_with_reason() -> None:
    chain = [_reviewer("r1", enabled=False, disabled_reason="broken"), _reviewer("r2")]
    step = next_step(_input(chain), [])
    assert step.kind == REVIEW
    assert step.reviewer is not None and step.reviewer.name == "r2"


def test_disabled_reason_recorded_when_pending() -> None:
    step = next_step(_input([_reviewer("r1", enabled=False, disabled_reason="broken")]), [])
    assert step.kind == PENDING
    assert step.reasons == ("r1: disabled (broken)",)


def test_quota_absence_skips_whole_pool() -> None:
    chain = [_reviewer("a", pool="pool-a"), _reviewer("b", pool="pool-a"), _reviewer("c", pool="pool-c")]
    attempts = [Attempt(1, "a", ABSENT, "quota")]
    step = next_step(_input(chain), attempts)
    assert step.kind == REVIEW
    assert step.reviewer is not None and step.reviewer.name == "c"


def test_quota_pool_pending_reasons_name_the_provider() -> None:
    chain = [_reviewer("a", pool="pool-a"), _reviewer("b", pool="pool-a")]
    attempts = [Attempt(1, "a", ABSENT, "quota", provider="google-antigravity")]
    step = next_step(_input(chain), attempts)
    assert step.kind == PENDING
    assert step.reasons == (
        "a: absent (quota)",
        "b: skipped: pool-a pool blocked in this run, google-antigravity returned a quota error",
    )


def test_backoff_skip_until_block_expires() -> None:
    block = PoolBlock(NOW + timedelta(minutes=50), "google-antigravity", NOW - timedelta(minutes=5))
    chain = [_reviewer("a", pool="pool-a"), _reviewer("b", pool="pool-b")]
    step = next_step(_input(chain, pool_blocked_until={"pool-a": block}), [])
    assert step.kind == REVIEW
    assert step.reviewer is not None and step.reviewer.name == "b"
    expired = PoolBlock(NOW - timedelta(minutes=1), "google-antigravity", NOW - timedelta(minutes=30))
    later = next_step(_input(chain, pool_blocked_until={"pool-a": expired}), [])
    assert later.kind == REVIEW
    assert later.reviewer is not None and later.reviewer.name == "a"


def test_backoff_reason_names_the_provider_and_when_the_block_was_recorded() -> None:
    recorded = NOW - timedelta(minutes=10)
    block = PoolBlock(NOW + timedelta(minutes=30), "google-antigravity", recorded)
    step = next_step(_input([_reviewer("a", pool="pool-a")], pool_blocked_until={"pool-a": block}), [])
    assert step.kind == PENDING
    assert step.reasons == (
        f"a: skipped: pool-a pool blocked until {block.until.isoformat()}, "
        f"google-antigravity returned a quota error at {recorded.isoformat()}",
    )
    unknown = PoolBlock(NOW + timedelta(minutes=30), "", recorded)
    reasons = next_step(_input([_reviewer("a", pool="pool-a")], pool_blocked_until={"pool-a": unknown}), []).reasons
    assert "an unknown provider returned a quota error" in reasons[0]


def test_a_carried_block_older_than_the_cap_is_ignored() -> None:
    chain = [_reviewer("a", pool="claude")]
    stale = PoolBlock(NOW + timedelta(days=6), "anthropic", NOW - PRIMARY_BLOCK_CAP - timedelta(minutes=1))
    step = next_step(_input(chain, pool_blocked_until={"claude": stale}), [])
    assert step.kind == REVIEW
    assert pool_resets(_input(chain, pool_blocked_until={"claude": stale}), []) == {}
    fresh = PoolBlock(NOW + timedelta(days=6), "anthropic", NOW - timedelta(hours=1))
    assert next_step(_input(chain, pool_blocked_until={"claude": fresh}), []).kind == PENDING


def test_a_fallback_block_never_outlives_the_one_hour_backoff() -> None:
    chain = [_reviewer("a", pool="claude")]
    recorded = NOW - QUOTA_BACKOFF - timedelta(minutes=1)
    block = PoolBlock(NOW + timedelta(days=6), "google-antigravity", recorded)
    assert next_step(_input(chain, pool_blocked_until={"claude": block}), []).kind == REVIEW


def test_pool_resets_from_the_primary_provider_keep_a_parsed_reset_up_to_the_cap() -> None:
    chain = [_reviewer("a", pool="claude")]
    reset = NOW + timedelta(hours=3)
    resets = pool_resets(_input(chain), [Attempt(1, "a", ABSENT, "quota", reset_at=reset, provider="anthropic")])
    assert resets == {"claude": PoolBlock(reset, "anthropic", NOW)}
    far = NOW + timedelta(days=6)
    resets = pool_resets(_input(chain), [Attempt(1, "a", ABSENT, "quota", reset_at=far, provider="anthropic")])
    assert resets == {"claude": PoolBlock(NOW + PRIMARY_BLOCK_CAP, "anthropic", NOW)}


def test_pool_resets_from_a_fallback_provider_ignore_the_reset_and_last_one_hour() -> None:
    chain = [_reviewer("a", pool="claude")]
    far = NOW + timedelta(days=6)
    resets = pool_resets(_input(chain), [Attempt(1, "a", ABSENT, "quota", reset_at=far, provider="google-antigravity")])
    assert resets == {"claude": PoolBlock(NOW + QUOTA_BACKOFF, "google-antigravity", NOW)}


def test_pool_resets_from_a_fallback_provider_never_end_before_the_backoff() -> None:
    chain = [_reviewer("a", pool="claude")]
    soon = NOW + timedelta(minutes=5)
    resets = pool_resets(
        _input(chain), [Attempt(1, "a", ABSENT, "quota", reset_at=soon, provider="google-antigravity")]
    )
    assert resets == {"claude": PoolBlock(NOW + QUOTA_BACKOFF, "google-antigravity", NOW)}


def test_a_new_quota_block_replaces_a_recorded_block_that_ends_at_the_same_moment() -> None:
    chain = [_reviewer("a", pool="claude")]
    recorded = PoolBlock(NOW + QUOTA_BACKOFF, "other-provider", NOW)
    attempt = Attempt(1, "a", ABSENT, "quota", provider="google-antigravity")
    resets = pool_resets(_input(chain, pool_blocked_until={"claude": recorded}), [attempt])
    assert resets == {"claude": PoolBlock(NOW + QUOTA_BACKOFF, "google-antigravity", NOW)}


def test_pool_resets_default_backoff_without_reported_provider() -> None:
    chain = [_reviewer("a", pool="claude")]
    far = NOW + timedelta(days=6)
    resets = pool_resets(_input(chain), [Attempt(1, "a", ABSENT, "quota", reset_at=far)])
    assert resets == {"claude": PoolBlock(NOW + QUOTA_BACKOFF, "", NOW)}


def test_pool_resets_keep_future_blocks() -> None:
    block = PoolBlock(NOW + timedelta(minutes=40), "google-antigravity", NOW - timedelta(minutes=20))
    chain = [_reviewer("a", pool="pool-a")]
    resets = pool_resets(_input(chain, pool_blocked_until={"pool-a": block}), [])
    assert resets == {"pool-a": block}


def test_active_blocks_on_a_new_head_keep_only_primary_provider_blocks() -> None:
    primary = PoolBlock(NOW + timedelta(hours=3), "anthropic", NOW)
    fallback = PoolBlock(NOW + timedelta(minutes=30), "google-antigravity", NOW)
    blocks = {"claude": primary, "codex": fallback}
    assert active_blocks(blocks, NOW, same_head=False) == {"claude": primary}
    assert active_blocks(blocks, NOW, same_head=True) == blocks


def test_pool_providers_match_the_reviewer_roles_in_the_omp_config() -> None:
    roles = yaml.safe_load(OMP_CONFIG.read_text(encoding="utf-8"))["modelRoles"]
    policy = load_policy(POLICY_PATH)
    for reviewer in policy.reviewers.values():
        provider = roles[reviewer.model.removeprefix("@")].split("/", 1)[0]
        assert POOL_PROVIDERS[reviewer.pool] == provider
    assert set(POOL_PROVIDERS) == set(policy.pools)


def test_pool_resets_drop_a_block_that_ends_now() -> None:
    chain = [_reviewer("a", pool="claude")]
    block = PoolBlock(NOW, "anthropic", NOW - timedelta(hours=1))
    assert pool_resets(_input(chain, pool_blocked_until={"claude": block}), []) == {}


@pytest.mark.parametrize("absence", RETRYABLE)
def test_pool_resets_ignore_absences_other_than_quota(absence: str) -> None:
    chain = [_reviewer("a", pool="pool-a")]
    assert pool_resets(_input(chain), [Attempt(1, "a", ABSENT, absence, reset_at=NOW + timedelta(hours=3))]) == {}


@pytest.mark.parametrize(("stored", "reported"), [(3, 1), (1, 3)])
def test_pool_resets_keep_the_later_of_a_stored_block_and_a_new_quota_reset(stored: int, reported: int) -> None:
    chain = [_reviewer("a", pool="claude")]
    kept = PoolBlock(NOW + timedelta(hours=stored), "anthropic", NOW - timedelta(minutes=5))
    attempt = Attempt(1, "a", ABSENT, "quota", reset_at=NOW + timedelta(hours=reported), provider="anthropic")
    later = kept if stored > reported else PoolBlock(NOW + timedelta(hours=reported), "anthropic", NOW)
    assert pool_resets(_input(chain, pool_blocked_until={"claude": kept}), [attempt]) == {"claude": later}


def test_oversize_brief_skips_everything() -> None:
    chain = [_reviewer("r1"), _reviewer("r2")]
    step = next_step(_input(chain, brief_mode="oversize"), [])
    assert step.kind == PENDING
    assert step.reasons == tuple(
        f"{name}: skipped: the brief exceeds the size cap even without the diff" for name in ("r1", "r2")
    )


def test_file_list_brief_skips_non_hard_non_reading_reviewers() -> None:
    soft = _reviewer("soft", read_only="soft", reads_files=True)
    non_reader = _reviewer("non-reader", read_only="hard", reads_files=False)
    hard = _reviewer("hard", read_only="hard", reads_files=True)
    step = next_step(_input([soft, non_reader, hard], brief_mode="file-list"), [])
    assert step.kind == REVIEW
    assert step.reviewer is not None and step.reviewer.name == "hard"


def test_file_list_brief_all_skipped_is_pending() -> None:
    chain = [_reviewer("soft", read_only="soft", reads_files=True)]
    step = next_step(_input(chain, brief_mode="file-list"), [])
    assert step.kind == PENDING
    assert "only hard read-only reviewers that read files may review it" in step.reasons[0]


def test_max_attempts_caps_chain() -> None:
    chain = [_reviewer("r1"), _reviewer("r2")]
    attempts = [Attempt(1, "r9", ABSENT, "error"), Attempt(2, "r8", ABSENT, "error")]
    step = next_step(_input(chain, max_attempts=2), attempts)
    assert step.kind == PENDING
    assert all("no attempt slot left in this run" in reason for reason in step.reasons)


def test_max_attempts_allows_last_slot() -> None:
    step = next_step(_input([_reviewer("r1")], max_attempts=1), [])
    assert step.kind == REVIEW
    assert step.reviewer is not None and step.reviewer.name == "r1"


def test_empty_chain_is_pending() -> None:
    step = next_step(_input([]), [])
    assert step.kind == PENDING
    assert step.reviewer is None
    assert step.reasons == ()


def test_from_plan_builds_without_family_keys() -> None:
    until = NOW + timedelta(hours=2)
    first = _reviewer("r1")
    second = _reviewer("r2", pool="other")
    plan = {
        "chain": [first.to_json(), second.to_json()],
        "brief_mode": "inline",
        "max_attempts": 3,
        "pool_blocked_until": {"other": PoolBlock(until, "", NOW).to_json()},
    }
    selection = SelectionInput.from_plan(plan, NOW)
    assert [reviewer.name for reviewer in selection.chain] == ["r1", "r2"]
    assert selection.brief_mode == "inline"
    assert selection.max_attempts == 3
    assert selection.pool_blocked_until == {"other": PoolBlock(until, "", NOW)}
    step = next_step(selection, [])
    assert step.kind == REVIEW
    assert step.reviewer is not None and step.reviewer.name == "r1"
