from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from _project.scripts.oracle_reviewers import selection
from _project.scripts.oracle_reviewers.policy import Policy
from _project.scripts.oracle_reviewers.selection import Attempt, SelectionInput, excluded_families, next_step

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture
def policy(codex_enabled_policy: Policy) -> Policy:
    return codex_enabled_policy


NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def _input(
    policy: Policy,
    tier: str,
    labels: tuple[str, ...] = (),
    *,
    brief_mode: str = "inline",
    enable: tuple[str, ...] = (),
    blocked: dict[str, datetime] | None = None,
    max_attempts: int = 5,
) -> SelectionInput:
    chain = [
        replace(reviewer, enabled=True) if reviewer.name in enable else reviewer for reviewer in policy.chain(tier)
    ]
    return SelectionInput(
        chain=chain,
        diversity_exempt=frozenset(policy.tiers[tier].diversity_exempt),
        excluded_families=excluded_families(labels, policy),
        brief_mode=brief_mode,
        max_attempts=max_attempts,
        now=NOW,
        pool_blocked_until=blocked or {},
    )


def _without(selection_input: SelectionInput, *names: str) -> SelectionInput:
    chain = [
        replace(reviewer, enabled=False, disabled_reason="off") if reviewer.name in names else reviewer
        for reviewer in selection_input.chain
    ]
    return replace(selection_input, chain=chain)


def _absent(slot: int, reviewer: str, kind: str = "error") -> Attempt:
    return Attempt(slot, reviewer, selection.ABSENT, kind)


def _walk(selection_input: SelectionInput, absences: dict[str, str]) -> list[str]:
    attempts: list[Attempt] = []
    order: list[str] = []
    while True:
        step = next_step(selection_input, attempts)
        if step.kind != selection.REVIEW:
            return order
        assert step.reviewer is not None
        order.append(step.reviewer.name)
        attempts.append(_absent(len(attempts) + 1, step.reviewer.name, absences.get(step.reviewer.name, "error")))


def test_missing_or_unknown_author_label_means_muse(policy: Policy) -> None:
    assert excluded_families((), policy) == {"muse"}
    assert excluded_families(("author-family:grok",), policy) == {"muse"}
    assert excluded_families(("author-family:codex",), policy) == {"codex"}
    assert excluded_families(("author-family:codex", "author-family:muse"), policy) == {"codex", "muse"}


def test_low_medium_for_a_claude_author_skips_claude(policy: Policy) -> None:
    assert _walk(_input(policy, "low-medium", ("author-family:claude",)), {}) == [
        "codex-deep",
        "muse-routine",
        "gemini-routine",
        "codex-routine",
    ]


def test_low_medium_for_a_codex_author_falls_back_to_claude_routine(policy: Policy) -> None:
    assert _walk(_input(policy, "low-medium", ("author-family:codex",)), {}) == [
        "claude-routine",
        "muse-routine",
        "gemini-routine",
    ]


def test_medium_high_for_a_muse_author_skips_muse_deep(policy: Policy) -> None:
    assert _walk(_input(policy, "medium-high", ("author-family:muse",)), {}) == [
        "codex-deep",
        "claude-deep",
        "codex-routine",
    ]


def test_very_high_for_a_claude_author_falls_back_to_muse_deep(policy: Policy) -> None:
    assert _walk(_input(policy, "very-high", ("author-family:claude",)), {}) == ["codex-deep", "muse-deep"]


def test_very_high_for_a_codex_author_stays_with_claude_deep(policy: Policy) -> None:
    assert _walk(_input(policy, "very-high", ("author-family:codex",)), {}) == ["claude-deep", "muse-deep"]


def test_very_high_for_a_claude_author_without_codex_reaches_muse_deep(policy: Policy) -> None:
    selection_input = _without(_input(policy, "very-high", ("author-family:claude",)), "codex-deep")
    assert _walk(selection_input, {}) == ["muse-deep"]


def test_very_high_without_an_eligible_reviewer_fails_closed(policy: Policy) -> None:
    selection_input = _input(policy, "very-high", ("author-family:claude", "author-family:codex", "author-family:muse"))
    step = next_step(selection_input, [])
    assert step.kind == selection.PENDING
    assert step.reviewer is None
    assert "codex-deep: skipped: same family as the author (codex)" in step.reasons
    assert "claude-deep: skipped: same family as the author (claude)" in step.reasons
    assert "muse-deep: skipped: same family as the author (muse)" in step.reasons


def test_quota_absence_skips_the_whole_pool(policy: Policy) -> None:
    selection_input = _input(policy, "medium-high", ("author-family:muse",))
    attempts = [_absent(1, "claude-deep"), _absent(2, "codex-deep", "quota")]
    step = next_step(selection_input, attempts)
    assert step.kind == selection.PENDING
    assert "codex-routine: skipped: codex pool out of quota in this run" in step.reasons


def test_very_high_codex_quota_still_reaches_muse_deep(policy: Policy) -> None:
    step = next_step(_input(policy, "very-high", ("author-family:claude",)), [_absent(1, "codex-deep", "quota")])
    assert step.reviewer is not None and step.reviewer.name == "muse-deep"


def test_other_absence_lets_pool_peers_try(policy: Policy) -> None:
    selection_input = _input(policy, "medium-high", ("author-family:muse",))
    for kind in ("timeout", "invalid", "auth", "empty", "error"):
        step = next_step(selection_input, [_absent(1, "claude-deep"), _absent(2, "codex-deep", kind)])
        assert step.reviewer is not None and step.reviewer.name == "codex-routine", kind


def test_pool_reset_from_an_earlier_run_skips_the_pool_until_it_passes(policy: Policy) -> None:
    later = NOW + timedelta(hours=2)
    labels = ("author-family:codex",)
    selection_input = _without(_input(policy, "very-high", labels), "codex-deep")
    step = next_step(replace(selection_input, pool_blocked_until={"claude": later}), [])
    assert step.reviewer is not None and step.reviewer.name == "muse-deep"
    step = next_step(replace(selection_input, pool_blocked_until={"claude": NOW - timedelta(minutes=1)}), [])
    assert step.reviewer is not None and step.reviewer.name == "claude-deep"


def test_blocking_verdict_is_terminal(policy: Policy) -> None:
    selection_input = _input(policy, "medium-high", ("author-family:muse",))
    step = next_step(selection_input, [Attempt(1, "claude-deep", selection.FAIL)])
    assert step.kind == selection.FAIL
    assert step.reviewer is not None and step.reviewer.name == "claude-deep"


def test_clean_verdict_passes_without_another_reviewer(policy: Policy) -> None:
    step = next_step(
        _input(policy, "low-medium"), [_absent(1, "muse-routine"), Attempt(2, "gemini-routine", selection.PASS)]
    )
    assert step.kind == selection.PASS
    assert step.reviewer is not None and step.reviewer.name == "gemini-routine"


def test_brief_over_cap_leaves_only_hard_read_only_reviewers(policy: Policy) -> None:
    order = _walk(
        _input(policy, "low-medium", ("author-family:codex",), brief_mode="file-list", enable=("muse-deep",)), {}
    )
    assert order == ["claude-routine", "muse-routine"]
    with_inline = _walk(_input(policy, "low-medium", ("author-family:codex",)), {})
    assert with_inline == ["claude-routine", "muse-routine", "gemini-routine"]


def test_a_hard_read_only_reviewer_that_cannot_read_files_skips_a_file_list_brief(policy: Policy) -> None:
    selection_input = _input(policy, "low-medium", ("author-family:codex",), brief_mode="file-list")
    blind = [
        replace(item, reads_files=False) if item.name == "claude-routine" else item for item in selection_input.chain
    ]
    step = next_step(replace(selection_input, chain=blind), [])
    assert step.reviewer is not None and step.reviewer.name == "muse-routine"
    inline = next_step(replace(selection_input, chain=blind, brief_mode="inline"), [])
    assert inline.reviewer is not None and inline.reviewer.name == "claude-routine"


def test_oversize_brief_leaves_everyone_absent(policy: Policy) -> None:
    selection_input = replace(
        _input(policy, "very-high", brief_mode="oversize", enable=("gemini-deep",)),
        excluded_families=frozenset(),
    )
    step = next_step(selection_input, [])
    assert step.kind == selection.PENDING
    assert all("size cap" in reason for reason in step.reasons)


def test_disabled_reviewer_is_named_with_its_reason(policy: Policy) -> None:
    selection_input = _input(policy, "low-medium", ("author-family:claude",))
    chain = [
        replace(reviewer, enabled=False, disabled_reason="off") if reviewer.name == "gemini-routine" else reviewer
        for reviewer in selection_input.chain
    ]
    attempts = [_absent(1, "muse-routine"), _absent(2, "codex-deep"), _absent(3, "codex-routine")]
    step = next_step(replace(selection_input, chain=chain), attempts)
    assert step.kind == selection.PENDING
    assert "gemini-routine: disabled (off)" in step.reasons
    assert "claude-routine: skipped: same family as the author (claude)" in step.reasons


def test_attempt_slots_bound_the_chain(policy: Policy) -> None:
    selection_input = _input(policy, "low-medium", ("author-family:agy",), max_attempts=2)
    step = next_step(selection_input, [_absent(1, "muse-routine"), _absent(2, "codex-deep")])
    assert step.kind == selection.PENDING
    assert "claude-routine: skipped: no attempt slot left in this run" in step.reasons


def test_pool_resets_carry_quota_reset_times(policy: Policy) -> None:
    reset = NOW + timedelta(hours=3)
    selection_input = _input(policy, "very-high")
    resets = selection.pool_resets(
        selection_input, [Attempt(1, "claude-deep", selection.ABSENT, "quota", "", reset), _absent(2, "codex-deep")]
    )
    assert resets == {"claude": reset}


def test_a_quota_absence_without_a_reset_time_backs_the_pool_off(policy: Policy) -> None:
    selection_input = _input(policy, "medium-high", ("author-family:muse",))
    resets = selection.pool_resets(selection_input, [_absent(1, "codex-deep"), _absent(2, "codex-routine", "quota")])
    assert resets == {"codex": NOW + selection.QUOTA_BACKOFF}
    later = replace(selection_input, now=NOW + timedelta(minutes=30), pool_blocked_until=resets)
    step = next_step(later, [_absent(1, "codex-deep"), _absent(2, "claude-deep")])
    assert step.kind == selection.PENDING
    assert step.reasons[-1].startswith("codex-routine: skipped: codex pool out of quota until")


def test_a_reported_reset_time_wins_over_the_default_back_off(policy: Policy) -> None:
    reset = NOW + timedelta(minutes=10)
    selection_input = _input(policy, "medium-high", ("author-family:muse",))
    resets = selection.pool_resets(selection_input, [Attempt(1, "codex-routine", selection.ABSENT, "quota", "", reset)])
    assert resets == {"codex": reset}


@pytest.mark.parametrize("tier", ["low-medium", "medium-high", "very-high"])
@pytest.mark.parametrize("label", [(), ("author-family:codex",), ("author-family:muse",), ("author-family:agy",)])
def test_every_enabled_chain_is_fully_reachable(policy: Policy, tier: str, label: tuple[str, ...]) -> None:
    excluded = excluded_families(label, policy)
    exempt = set(policy.tiers[tier].diversity_exempt)
    expected = [
        reviewer.name
        for reviewer in policy.chain(tier)
        if reviewer.enabled and (reviewer.family not in excluded or reviewer.name in exempt)
    ]
    selection_input = SelectionInput(
        chain=policy.chain(tier),
        diversity_exempt=frozenset(exempt),
        excluded_families=excluded,
        brief_mode="inline",
        max_attempts=policy.max_attempts,
        now=NOW,
    )
    assert _walk(selection_input, {}) == expected
    attempts = [_absent(index + 1, name) for index, name in enumerate(expected)]
    step = next_step(selection_input, attempts)
    assert step.kind == selection.PENDING
    assert not any("no attempt slot left" in reason for reason in step.reasons)


def test_policy_slots_cover_the_longest_enabled_chain(policy: Policy) -> None:
    assert policy.max_attempts == policy.longest_enabled_chain()
