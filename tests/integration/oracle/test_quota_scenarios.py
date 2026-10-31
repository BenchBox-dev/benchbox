from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from .harness import CHECKER, Harness

pytestmark = [pytest.mark.integration, pytest.mark.medium]

FIRST = "def checker(a, b):\n    return bool(a == b)\n"
SECOND = "def checker(a, b):\n    return bool(a == b) or a is b\n"
SHIP = {"decision": "SHIP", "summary": "The change keeps checker semantics; no defects.", "files_examined": [CHECKER]}


def _quota(provider: str) -> dict:
    return {
        "kind": "error",
        "status": 429,
        "provider": provider,
        "message": "rate_limit_error: 429 Too Many Requests. Resets in 5d.",
    }


def _blocked_claude(harness: Harness, pr: int, provider: str) -> str:
    head = harness.branch("feat/quota", {CHECKER: FIRST}, "wrap in bool")
    harness.github.open_pull(pr, "feat/quota")
    harness.script_omp([_quota(provider), SHIP])
    run = harness.review(pr)
    assert run.steps["finalize"].outputs["state"] == "pending"
    assert [item["state"] for item in harness.github.oracle_statuses(head)] == ["pending"]
    return head


def _recorded_block(harness: Harness, pr: int) -> dict:
    return harness.github.artifact_json(f"oracle-verdict-state-{pr}", "state.json")["pool_blocked_until"]["claude"]


def test_a_fallback_providers_quota_reset_does_not_block_the_next_head(harness: Harness) -> None:
    _blocked_claude(harness, 91, "openai-codex")
    block = _recorded_block(harness, 91)
    assert harness.omp_calls() == 1
    head = harness.push("feat/quota", {CHECKER: SECOND}, "short-circuit on identity")

    run = harness.review(91)

    assert harness.omp_calls() == 2
    assert run.plan["pool_blocked_until"] == {}
    assert [item["state"] for item in harness.github.oracle_statuses(head)] == ["success"]
    assert block["provider"] == "openai-codex"
    assert datetime.fromisoformat(block["until"]) - datetime.fromisoformat(block["recorded_at"]) <= timedelta(hours=1)


def test_a_primary_providers_quota_reset_blocks_the_pool_for_at_most_the_backoff(harness: Harness) -> None:
    _blocked_claude(harness, 92, "anthropic")
    block = _recorded_block(harness, 92)
    head = harness.push("feat/quota", {CHECKER: SECOND}, "short-circuit on identity")

    run = harness.review(92)

    assert harness.omp_calls() == 1
    assert list(run.plan["pool_blocked_until"]) == ["claude"]
    [status] = harness.github.oracle_statuses(head)
    assert status["state"] == "pending"
    assert "claude pool blocked until" in status["description"] and "anthropic" in status["description"]
    assert block["provider"] == "anthropic"
    held = datetime.fromisoformat(block["until"]) - datetime.fromisoformat(block["recorded_at"])
    assert timedelta(minutes=59) < held <= timedelta(hours=1)


def test_a_primary_providers_block_is_cleared_once_the_backoff_passes_and_the_provider_answers(
    harness: Harness,
) -> None:
    _blocked_claude(harness, 93, "anthropic")
    harness.github.age(harness.github.runs[-1], 61)
    head = harness.push("feat/quota", {CHECKER: SECOND}, "short-circuit on identity")

    run = harness.review(93)

    assert harness.omp_calls() == 2
    assert run.plan["pool_blocked_until"] == {}
    assert [item["state"] for item in harness.github.oracle_statuses(head)] == ["success"]
    assert harness.github.artifact_json("oracle-verdict-state-93", "state.json")["pool_blocked_until"] == {}


def test_a_manual_dispatch_during_the_backoff_leaves_the_recorded_block_unchanged(harness: Harness) -> None:
    _blocked_claude(harness, 94, "anthropic")
    before = _recorded_block(harness, 94)

    run = harness.review(94, event_name="workflow_dispatch", inputs={"reason": "manual"})

    assert harness.omp_calls() == 1
    assert run.steps["prepare"].outputs["decision"] != "review"
    assert _recorded_block(harness, 94) == before
