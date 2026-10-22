from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from _project.scripts.oracle_reviewers.policy import Reviewer
from _project.scripts.oracle_reviewers.selection import (
    ABSENT,
    FAIL,
    PASS,
    PENDING,
    REVIEW,
    Attempt,
    SelectionInput,
    next_step,
    pool_resets,
)

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


def test_quota_pool_pending_reasons() -> None:
    chain = [_reviewer("a", pool="pool-a"), _reviewer("b", pool="pool-a")]
    attempts = [Attempt(1, "a", ABSENT, "quota")]
    step = next_step(_input(chain), attempts)
    assert step.kind == PENDING
    assert step.reasons == ("a: absent (quota)", "b: skipped: pool-a pool out of quota in this run")


def test_backoff_skip_until_block_expires() -> None:
    until = NOW + timedelta(hours=2)
    chain = [_reviewer("a", pool="pool-a"), _reviewer("b", pool="pool-b")]
    blocked = _input(chain, pool_blocked_until={"pool-a": until})
    step = next_step(blocked, [])
    assert step.kind == REVIEW
    assert step.reviewer is not None and step.reviewer.name == "b"
    expired = _input(chain, pool_blocked_until={"pool-a": NOW - timedelta(minutes=1)})
    later = next_step(expired, [])
    assert later.kind == REVIEW
    assert later.reviewer is not None and later.reviewer.name == "a"


def test_backoff_reason_names_reset_time() -> None:
    until = NOW + timedelta(hours=2)
    step = next_step(_input([_reviewer("a", pool="pool-a")], pool_blocked_until={"pool-a": until}), [])
    assert step.kind == PENDING
    assert step.reasons == (f"a: skipped: pool-a pool out of quota until {until.isoformat()}",)


def test_pool_resets_carry_quota_reset_times() -> None:
    reset = NOW + timedelta(hours=3)
    chain = [_reviewer("a", pool="pool-a"), _reviewer("b", pool="pool-b")]
    selection = _input(chain)
    resets = pool_resets(selection, [Attempt(1, "a", ABSENT, "quota", reset_at=reset)])
    assert resets == {"pool-a": reset}


def test_pool_resets_default_backoff_without_reported_time() -> None:
    chain = [_reviewer("a", pool="pool-a")]
    resets = pool_resets(_input(chain), [Attempt(1, "a", ABSENT, "quota")])
    assert resets == {"pool-a": NOW + timedelta(hours=1)}


def test_pool_resets_keep_future_blocks() -> None:
    until = NOW + timedelta(hours=2)
    chain = [_reviewer("a", pool="pool-a")]
    resets = pool_resets(_input(chain, pool_blocked_until={"pool-a": until}), [])
    assert resets == {"pool-a": until}


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
        "pool_blocked_until": {"other": until.isoformat()},
    }
    selection = SelectionInput.from_plan(plan, NOW)
    assert [reviewer.name for reviewer in selection.chain] == ["r1", "r2"]
    assert selection.brief_mode == "inline"
    assert selection.max_attempts == 3
    assert selection.pool_blocked_until == {"other": until}
    step = next_step(selection, [])
    assert step.kind == REVIEW
    assert step.reviewer is not None and step.reviewer.name == "r1"
