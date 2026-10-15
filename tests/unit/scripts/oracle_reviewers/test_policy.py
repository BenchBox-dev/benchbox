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


@pytest.mark.parametrize(
    ("name", "harness", "pool", "model", "effort", "read_only"),
    [
        ("opus", "claude", "claude", "claude-opus-5-5", "medium", "hard"),
        ("sonnet", "claude", "claude", "claude-sonnet-5-5", "medium", "hard"),
        ("sol", "codex", "codex", "gpt-6.1-sol", "medium", "hard"),
        ("luna", "codex", "codex", "gpt-6-luna", "high", "hard"),
        ("muse", "muse", "muse", "muse-spark-1.3-contributor", "medium", "hard"),
        ("agy", "agy", "agy", "gemini-3.8-flash-medium", "medium", "soft"),
    ],
)
def test_reviewer_pins(
    policy: Policy, name: str, harness: str, pool: str, model: str, effort: str, read_only: str
) -> None:
    reviewer = policy.reviewers[name]
    assert (reviewer.harness, reviewer.pool, reviewer.model, reviewer.effort, reviewer.read_only) == (
        harness,
        pool,
        model,
        effort,
        read_only,
    )
    assert 0 < reviewer.timeout_minutes <= 30


def test_quota_pools_are_claude_codex_muse_and_agy(policy: Policy) -> None:
    assert set(policy.pools) == {"claude", "codex", "muse", "agy"}
    assert policy.reviewers["opus"].pool == policy.reviewers["sonnet"].pool
    assert policy.reviewers["sol"].pool == policy.reviewers["luna"].pool


def test_agy_is_enabled(policy: Policy) -> None:
    agy = policy.reviewers["agy"]
    assert agy.enabled is True
    assert agy.disabled_reason == ""


def test_muse_is_enabled_second_in_low_medium(policy: Policy) -> None:
    assert policy.reviewers["muse"].enabled is True
    assert policy.tiers["low-medium"].order[:2] == ("luna", "muse")
    enabled = {name for name, reviewer in policy.reviewers.items() if reviewer.enabled}
    assert enabled == {"opus", "sonnet", "sol", "luna", "muse", "agy"}


def test_max_attempts_must_cover_the_longest_enabled_chain() -> None:
    raw = _raw()
    raw["max_attempts"] = 4
    with pytest.raises(PolicyError, match=r"longest enabled chain \(5\)"):
        parse_policy(raw)
    raw["reviewers"]["agy"].update(enabled=False, disabled_reason="off")
    raw["max_attempts"] = 3
    with pytest.raises(PolicyError, match=r"longest enabled chain \(4\)"):
        parse_policy(raw)


def test_tier_orders(policy: Policy) -> None:
    assert policy.tiers["low-medium"].order == ("luna", "muse", "agy", "sonnet", "sol")
    assert policy.tiers["medium-high"].order == ("sonnet", "sol", "luna", "muse", "agy")
    assert policy.tiers["very-high"].order == ("opus", "sol")
    assert policy.tiers["very-high"].diversity_exempt == ("opus",)
    assert policy.tiers["low-medium"].diversity_exempt == ()
    assert policy.tiers["medium-high"].diversity_exempt == ()


def test_protocol_limits(policy: Policy) -> None:
    assert (policy.protocol.max_defects, policy.protocol.max_do_not_ship) == (10, 3)


def test_only_agy_does_not_read_files(policy: Policy) -> None:
    assert {name for name, reviewer in policy.reviewers.items() if not reviewer.reads_files} == {"agy"}


def test_opus_never_serves_the_lower_tiers(policy: Policy) -> None:
    assert policy.reviewers["opus"].allowed_tiers == ("very-high",)
    raw = _raw()
    raw["tiers"]["low-medium"]["order"].append("opus")
    with pytest.raises(PolicyError, match="opus is not allowed in tier low-medium"):
        parse_policy(raw)


def test_very_high_never_falls_back_to_a_low_medium_reviewer(policy: Policy) -> None:
    raw = _raw()
    raw["tiers"]["very-high"]["order"].append("muse")
    with pytest.raises(PolicyError, match="muse is not allowed in tier very-high"):
        parse_policy(raw)


def test_brief_cap_fits_one_argument(policy: Policy) -> None:
    assert policy.brief_max_bytes <= 120_000
    raw = _raw()
    raw["brief_max_bytes"] = 200_000
    with pytest.raises(PolicyError, match="brief_max_bytes"):
        parse_policy(raw)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda raw: raw["reviewers"]["agy"].update(enabled=False), "needs a disabled_reason"),
        (lambda raw: raw["reviewers"]["sol"].update(pool="openai"), "unknown pool"),
        (lambda raw: raw["reviewers"]["sol"].update(harness="gemini"), "unknown harness"),
        (lambda raw: raw["tiers"]["very-high"].update(blocking=["Critical"]), "never gates"),
        (lambda raw: raw["reviewers"]["sol"].update(reads_files="yes"), "reads_files must be a boolean"),
        (lambda raw: raw["reviewers"]["sol"].pop("reads_files"), "reads_files must be a boolean"),
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
