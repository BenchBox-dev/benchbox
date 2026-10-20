from __future__ import annotations

import copy
from typing import Any, Callable

import pytest
import yaml

from _project.scripts.oracle_reviewers.policy import HARNESSES, SIZE_BUCKETS, Policy, PolicyError, parse_policy

from .conftest import POLICY_PATH

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _raw() -> dict[str, Any]:
    return yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))


def test_live_config_is_version_two(policy: Policy) -> None:
    assert _raw()["version"] == 2
    assert policy.mode == "enforce"
    assert policy.status_context == "oracle-verdict"
    assert policy.findings_delivery == "review"
    assert policy.bot_login == "benchbox-oracle"


def test_size_buckets_and_harness_constants() -> None:
    assert SIZE_BUCKETS == ("trivial", "small", "medium", "large", "huge", "max")
    assert HARNESSES == ("omp",)


def test_live_reviewers_match_buckets_one_to_one(policy: Policy) -> None:
    assert set(policy.reviewers) == set(SIZE_BUCKETS)
    assert set(policy.buckets) == set(SIZE_BUCKETS)
    for bucket in SIZE_BUCKETS:
        assert policy.buckets[bucket] in policy.reviewers
    for reviewer in policy.reviewers.values():
        assert reviewer.harness == "omp"


def test_chain_returns_single_bucket_reviewer(policy: Policy) -> None:
    for bucket in SIZE_BUCKETS:
        assert [reviewer.name for reviewer in policy.chain(bucket)] == [policy.buckets[bucket]]


def test_empty_effort_inherits_role_level(policy: Policy) -> None:
    assert policy.reviewers["trivial"].effort == ""


def test_version_one_is_rejected() -> None:
    raw = _raw()
    raw["version"] = 1
    with pytest.raises(PolicyError, match="policy version must be 2"):
        parse_policy(raw)


def test_missing_bucket_is_rejected() -> None:
    raw = _raw()
    del raw["buckets"]["max"]
    with pytest.raises(PolicyError, match=r"buckets must be exactly"):
        parse_policy(raw)


def test_extra_bucket_is_rejected() -> None:
    raw = _raw()
    raw["buckets"]["bonus"] = "trivial"
    with pytest.raises(PolicyError, match=r"buckets must be exactly"):
        parse_policy(raw)


def test_bucket_naming_unknown_reviewer_is_rejected() -> None:
    raw = _raw()
    raw["buckets"]["max"] = "ghost"
    with pytest.raises(PolicyError, match="names unknown reviewer"):
        parse_policy(raw)


def test_max_attempts_must_cover_longest_enabled_chain() -> None:
    raw = _raw()
    raw["max_attempts"] = 0
    with pytest.raises(PolicyError, match=r"must cover the longest enabled chain \(1\)"):
        parse_policy(raw)


def test_fully_disabled_policy_needs_no_attempts() -> None:
    raw = _raw()
    for reviewer in raw["reviewers"].values():
        reviewer.update(enabled=False, disabled_reason="off")
    raw["max_attempts"] = 0
    assert parse_policy(raw).longest_enabled_chain() == 0


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda raw: raw.update(version=1), r"policy version must be 2"),
        (lambda raw: raw.pop("version"), r"policy version must be 2"),
        (lambda raw: raw.update(version="2"), r"policy version must be 2"),
        (lambda raw: raw.update(pools="claude"), r"pools must be a string list"),
        (lambda raw: raw.update(pools=[42]), r"pools must be a string list"),
        (lambda raw: raw.update(reviewers={}), r"reviewers must be a mapping"),
        (lambda raw: raw.pop("reviewers"), r"reviewers must be a mapping"),
        (lambda raw: raw["reviewers"].update({"ghost": "nope"}), r"reviewer ghost must be a mapping"),
        (lambda raw: raw["reviewers"]["small"].update(harness="codex"), r"unknown harness"),
        (lambda raw: raw["reviewers"]["small"].pop("harness"), r"unknown harness"),
        (lambda raw: raw["reviewers"]["small"].update(role="worker"), r"unknown role"),
        (lambda raw: raw["reviewers"]["small"].pop("role"), r"unknown role"),
        (lambda raw: raw["reviewers"]["small"].update(pool="openai"), r"unknown pool"),
        (lambda raw: raw["reviewers"]["small"].update(family=""), r"needs a family label"),
        (lambda raw: raw["reviewers"]["small"].pop("family"), r"needs a family label"),
        (lambda raw: raw["reviewers"]["small"].update(model=""), r"needs a model"),
        (lambda raw: raw["reviewers"]["small"].update(effort="turbo"), r"unknown effort"),
        (lambda raw: raw["reviewers"]["small"].update(read_only="full"), r"unknown read_only"),
        (lambda raw: raw["reviewers"]["small"].update(reads_files="yes"), r"reads_files must be a boolean"),
        (lambda raw: raw["reviewers"]["small"].pop("reads_files"), r"reads_files must be a boolean"),
        (lambda raw: raw["reviewers"]["small"].update(timeout_minutes=0), r"timeout_minutes must be between 1 and 60"),
        (lambda raw: raw["reviewers"]["small"].update(timeout_minutes=61), r"timeout_minutes must be between 1 and 60"),
        (lambda raw: raw["reviewers"]["small"].update(enabled="yes"), r"enabled must be a boolean"),
        (lambda raw: raw["reviewers"]["small"].update(enabled=False), r"needs a disabled_reason"),
        (lambda raw: raw["buckets"].pop("small"), r"buckets must be exactly"),
        (lambda raw: raw.update(buckets={"small": "small"}), r"buckets must be exactly"),
        (lambda raw: raw["buckets"].update({"small": "ghost"}), r"names unknown reviewer"),
        (lambda raw: raw["classifier"].update(gate_paths="x"), r"classifier gate_paths must be a string list"),
        (lambda raw: raw["classifier"].pop("logic_paths"), r"classifier logic_paths must be a string list"),
        (lambda raw: raw["classifier"].update(data_paths=[42]), r"classifier data_paths must be a string list"),
        (lambda raw: raw.update(mode="audit"), r"mode must be shadow or enforce"),
        (lambda raw: raw.update(status_context=""), r"status_context is required"),
        (lambda raw: raw.update(findings_delivery="pr"), r"findings_delivery must be one of"),
        (lambda raw: raw.update(bot_login="BenchBox!"), r"bot_login must be an App slug"),
        (lambda raw: raw.update(bot_login="-lead"), r"bot_login must be an App slug"),
        (lambda raw: raw.update(brief_max_bytes=0), r"brief_max_bytes must fit in one command-line argument"),
        (lambda raw: raw.update(brief_max_bytes=120001), r"brief_max_bytes must fit in one command-line argument"),
        (lambda raw: raw["protocol"].update(max_defects=0), r"protocol limits must be positive"),
        (lambda raw: raw.pop("protocol"), r"protocol limits must be positive"),
        (lambda raw: raw["protocol"].update(max_do_not_ship=0), r"protocol limits must be positive"),
        (lambda raw: raw["retry"].update(daily_budget=0), r"retry rules must be positive"),
        (lambda raw: raw["retry"].update(backoff_start_minutes=0), r"retry rules must be positive"),
        (lambda raw: raw.pop("retry"), r"retry rules must be positive"),
        (lambda raw: raw.update(max_attempts=0), r"must cover the longest enabled chain"),
    ],
)
def test_invalid_policies_are_rejected(mutate: Callable[[dict[str, Any]], None], message: str) -> None:
    raw = copy.deepcopy(_raw())
    mutate(raw)
    with pytest.raises(PolicyError, match=message):
        parse_policy(raw)


@pytest.mark.parametrize("effort", ["off", "minimal", "low", "medium", "high", "xhigh", "max", "auto", ""])
def test_known_effort_levels_are_accepted(effort: str) -> None:
    raw = _raw()
    raw["reviewers"]["small"]["effort"] = effort
    assert parse_policy(raw).reviewers["small"].effort == effort


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
def test_shadow_and_enforce_modes_are_accepted(mode: str) -> None:
    raw = _raw()
    raw["mode"] = mode
    assert parse_policy(raw).mode == mode
