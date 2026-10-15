from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from _project.scripts.oracle_reviewers import selection
from _project.scripts.oracle_reviewers.policy import Policy
from _project.scripts.oracle_reviewers.selection import Attempt, SelectionInput, excluded_families, next_step

pytestmark = [pytest.mark.unit, pytest.mark.fast]

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def _input(
    policy: Policy,
    tier: str,
    labels: tuple[str, ...] = (),
    *,
    brief_mode: str = "inline",
    enable: tuple[str, ...] = ("muse",),
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


def test_missing_or_unknown_author_label_means_claude(policy: Policy) -> None:
    assert excluded_families((), policy) == {"claude"}
    assert excluded_families(("author-family:grok",), policy) == {"claude"}
    assert excluded_families(("author-family:codex",), policy) == {"codex"}
    assert excluded_families(("author-family:codex", "author-family:muse"), policy) == {"codex", "muse"}


def test_low_medium_for_a_claude_author_skips_claude_and_never_reaches_opus(policy: Policy) -> None:
    assert _walk(_input(policy, "low-medium"), {}) == ["luna", "muse", "agy", "sol"]
    assert "opus" not in [reviewer.name for reviewer in policy.chain("low-medium")]


def test_low_medium_for_a_codex_author_falls_back_to_sonnet(policy: Policy) -> None:
    assert _walk(_input(policy, "low-medium", ("author-family:codex",)), {}) == ["muse", "agy", "sonnet"]


def test_medium_high_falls_back_to_low_medium_reviewers_in_order(policy: Policy) -> None:
    order = _walk(_input(policy, "medium-high", ("author-family:muse",), enable=("muse", "agy")), {})
    assert order == ["sonnet", "sol", "luna", "agy"]


def test_very_high_is_opus_then_sol_then_pending(policy: Policy) -> None:
    selection_input = _input(policy, "very-high")
    assert _walk(selection_input, {}) == ["opus", "sol"]
    step = next_step(selection_input, [_absent(1, "opus"), _absent(2, "sol")])
    assert step.kind == selection.PENDING
    assert any(reason.startswith("opus: absent") for reason in step.reasons)


def test_very_high_keeps_opus_for_a_claude_author_but_drops_sol_for_a_codex_author(policy: Policy) -> None:
    assert _walk(_input(policy, "very-high", ("author-family:claude",)), {}) == ["opus", "sol"]
    assert _walk(_input(policy, "very-high", ("author-family:codex",)), {}) == ["opus"]


def test_quota_absence_skips_the_whole_pool(policy: Policy) -> None:
    selection_input = _input(policy, "medium-high", ("author-family:muse",))
    attempts = [_absent(1, "sonnet"), _absent(2, "sol", "quota"), _absent(3, "agy")]
    step = next_step(selection_input, attempts)
    assert step.kind == selection.PENDING
    assert "luna: skipped: codex pool out of quota in this run" in step.reasons


def test_very_high_opus_quota_does_not_try_sonnet(policy: Policy) -> None:
    step = next_step(_input(policy, "very-high"), [_absent(1, "opus", "quota")])
    assert step.reviewer is not None and step.reviewer.name == "sol"


def test_other_absence_lets_pool_peers_try(policy: Policy) -> None:
    selection_input = _input(policy, "medium-high", ("author-family:muse",))
    for kind in ("timeout", "invalid", "auth", "empty", "error"):
        step = next_step(selection_input, [_absent(1, "sonnet"), _absent(2, "sol", kind)])
        assert step.reviewer is not None and step.reviewer.name == "luna", kind


def test_pool_reset_from_an_earlier_run_skips_the_pool_until_it_passes(policy: Policy) -> None:
    later = NOW + timedelta(hours=2)
    step = next_step(_input(policy, "very-high", blocked={"claude": later}), [])
    assert step.reviewer is not None and step.reviewer.name == "sol"
    step = next_step(_input(policy, "very-high", blocked={"claude": NOW - timedelta(minutes=1)}), [])
    assert step.reviewer is not None and step.reviewer.name == "opus"


def test_blocking_verdict_is_terminal(policy: Policy) -> None:
    selection_input = _input(policy, "medium-high", ("author-family:muse",))
    step = next_step(selection_input, [Attempt(1, "sonnet", selection.FAIL)])
    assert step.kind == selection.FAIL
    assert step.reviewer is not None and step.reviewer.name == "sonnet"


def test_clean_verdict_passes_without_another_reviewer(policy: Policy) -> None:
    step = next_step(_input(policy, "low-medium"), [_absent(1, "muse"), Attempt(2, "luna", selection.PASS)])
    assert step.kind == selection.PASS
    assert step.reviewer is not None and step.reviewer.name == "luna"


def test_brief_over_cap_leaves_only_hard_read_only_reviewers(policy: Policy) -> None:
    order = _walk(
        _input(policy, "low-medium", ("author-family:codex",), brief_mode="file-list", enable=("muse", "agy")), {}
    )
    assert order == ["muse", "sonnet"]
    with_inline = _walk(_input(policy, "low-medium", ("author-family:codex",), enable=("muse", "agy")), {})
    assert with_inline == ["muse", "agy", "sonnet"]


def test_a_hard_read_only_reviewer_that_cannot_read_files_skips_a_file_list_brief(policy: Policy) -> None:
    selection_input = _input(policy, "low-medium", ("author-family:codex",), brief_mode="file-list")
    blind = [replace(item, reads_files=False) if item.name == "muse" else item for item in selection_input.chain]
    step = next_step(replace(selection_input, chain=blind), [])
    assert step.reviewer is not None and step.reviewer.name == "sonnet"
    inline = next_step(replace(selection_input, chain=blind, brief_mode="inline"), [])
    assert inline.reviewer is not None and inline.reviewer.name == "muse"


def test_oversize_brief_leaves_everyone_absent(policy: Policy) -> None:
    step = next_step(_input(policy, "very-high", brief_mode="oversize"), [])
    assert step.kind == selection.PENDING
    assert all("size cap" in reason for reason in step.reasons)


def test_disabled_reviewer_is_named_with_its_reason(policy: Policy) -> None:
    selection_input = _input(policy, "low-medium")
    chain = [
        replace(reviewer, enabled=False, disabled_reason="off") if reviewer.name == "agy" else reviewer
        for reviewer in selection_input.chain
    ]
    step = next_step(replace(selection_input, chain=chain), [_absent(1, "muse"), _absent(2, "luna"), _absent(3, "sol")])
    assert step.kind == selection.PENDING
    assert "agy: disabled (off)" in step.reasons
    assert "sonnet: skipped: same family as the author (claude)" in step.reasons


def test_attempt_slots_bound_the_chain(policy: Policy) -> None:
    selection_input = _input(policy, "low-medium", ("author-family:agy",), max_attempts=2)
    step = next_step(selection_input, [_absent(1, "muse"), _absent(2, "luna")])
    assert step.kind == selection.PENDING
    assert "sonnet: skipped: no attempt slot left in this run" in step.reasons


def test_pool_resets_carry_quota_reset_times(policy: Policy) -> None:
    reset = NOW + timedelta(hours=3)
    selection_input = _input(policy, "very-high")
    resets = selection.pool_resets(
        selection_input, [Attempt(1, "opus", selection.ABSENT, "quota", "", reset), _absent(2, "sol")]
    )
    assert resets == {"claude": reset}


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
