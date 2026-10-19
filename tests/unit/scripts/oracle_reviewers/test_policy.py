from __future__ import annotations

import copy
from typing import Any

import pytest
import yaml

from _project.scripts.oracle_reviewers.policy import Policy, PolicyError, parse_policy

from .conftest import POLICY_PATH

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _raw() -> dict[str, Any]:
    return yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))


def test_policy_enforces_with_its_own_context(policy: Policy) -> None:
    assert policy.mode == "enforce"
    assert policy.status_context == "oracle-verdict"
    assert policy.findings_delivery == "review"
    assert policy.bot_login == "benchbox-oracle"


def test_codex_reviewers_are_disabled_with_a_reason(policy: Policy) -> None:
    for name in ("codex-deep", "codex-routine"):
        assert policy.reviewers[name].enabled is False
        assert "no quota" in policy.reviewers[name].disabled_reason


def test_gemini_deep_is_disabled_until_hosted_completion_is_verified(policy: Policy) -> None:
    reviewer = policy.reviewers["gemini-deep"]
    assert reviewer.enabled is False
    assert "unverified" in reviewer.disabled_reason


@pytest.mark.parametrize("tier", ["low-medium", "medium-high", "very-high"])
@pytest.mark.parametrize("author", ["claude", "codex", "muse", "agy"])
@pytest.mark.parametrize("brief_mode", ["inline", "file-list"])
def test_every_tier_has_an_eligible_reviewer_for_every_author(
    policy: Policy, tier: str, author: str, brief_mode: str
) -> None:
    exempt = set(policy.tiers[tier].diversity_exempt)
    eligible = [
        reviewer.name
        for reviewer in policy.chain(tier)
        if reviewer.enabled
        and (reviewer.family != author or reviewer.name in exempt)
        and (brief_mode == "inline" or (reviewer.read_only == "hard" and reviewer.reads_files))
    ]
    assert eligible, (tier, author, brief_mode)


def test_max_attempts_must_cover_the_longest_enabled_chain() -> None:
    raw = _raw()
    for name in ("codex-deep", "codex-routine"):
        raw["reviewers"][name]["enabled"] = True
    raw["max_attempts"] = 4
    with pytest.raises(PolicyError, match=r"longest enabled chain \(5\)"):
        parse_policy(raw)
    raw["reviewers"]["gemini-routine"].update(enabled=False, disabled_reason="off")
    raw["max_attempts"] = 3
    with pytest.raises(PolicyError, match=r"longest enabled chain \(4\)"):
        parse_policy(raw)


def test_claude_deep_never_serves_the_routine_tier(policy: Policy) -> None:
    assert policy.reviewers["claude-deep"].allowed_tiers == ("medium-high", "very-high")
    raw = _raw()
    raw["tiers"]["low-medium"]["order"].append("claude-deep")
    with pytest.raises(PolicyError, match="claude-deep is not allowed in tier low-medium"):
        parse_policy(raw)


def test_very_high_never_falls_back_to_a_routine_reviewer(policy: Policy) -> None:
    raw = _raw()
    raw["tiers"]["very-high"]["order"].append("muse-routine")
    with pytest.raises(PolicyError, match="muse-routine is not allowed in tier very-high"):
        parse_policy(raw)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda raw: raw["reviewers"]["gemini-routine"].update(enabled=False), "needs a disabled_reason"),
        (lambda raw: raw["reviewers"]["codex-routine"].update(pool="openai"), "unknown pool"),
        (lambda raw: raw["reviewers"]["codex-routine"].update(harness="gemini"), "unknown harness"),
        (lambda raw: raw["reviewers"]["codex-routine"].update(role="worker"), "unknown role"),
        (lambda raw: raw["reviewers"]["codex-routine"].pop("role"), "unknown role"),
        (lambda raw: raw["tiers"]["very-high"].update(blocking=["Critical"]), "never gates"),
        (lambda raw: raw["reviewers"]["codex-routine"].update(reads_files="yes"), "reads_files must be a boolean"),
        (lambda raw: raw["reviewers"]["codex-routine"].pop("reads_files"), "reads_files must be a boolean"),
        (lambda raw: raw["protocol"].update(max_defects=0), "protocol limits must be positive"),
        (lambda raw: raw.pop("protocol"), "protocol limits must be positive"),
        (lambda raw: raw["tiers"].pop("medium-high"), "tiers must be exactly"),
        (lambda raw: raw.update(version=2), "version must be 1"),
    ],
)
def test_invalid_policies_are_rejected(mutate: Any, message: str) -> None:
    raw = copy.deepcopy(_raw())
    mutate(raw)
    with pytest.raises(PolicyError, match=message):
        parse_policy(raw)
