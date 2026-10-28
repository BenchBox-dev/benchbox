from __future__ import annotations

import json

import pytest

from _project.scripts.oracle_reviewers import omp_bump, omp_pin

from .fake_github import now_text
from .harness import CHECKER, POLICY, REPO_ROOT, Harness

pytestmark = [pytest.mark.integration, pytest.mark.medium]

FIXTURES = REPO_ROOT / "tests" / "fixtures"
CHECK_RUNS = FIXTURES / "oracle_reviewers" / "check_runs_full_ci_in_progress.json"
RULESET = FIXTURES / "ruleset-develop-squash-only.json"


def _ci_in_progress(harness: Harness, head: str) -> None:
    captured = json.loads(CHECK_RUNS.read_text(encoding="utf-8"))["check_runs"]
    harness.github.check_runs[head] = [{**item, "head_sha": head} for item in captured]
    harness.github.rulesets = json.loads(RULESET.read_text(encoding="utf-8"))["rules"]


def test_arming_while_the_full_ci_run_is_in_progress_waits_without_arming_or_rerunning(harness: Harness) -> None:
    head = harness.branch("feat/arm", {CHECKER: "def checker(a, b):\n    return a == b  \n"}, "trailing space")
    harness.github.open_pull(71, "feat/arm")
    _ci_in_progress(harness, head)

    arm = harness.arm(71, "feat/arm")

    assert arm.code == 2, arm.stdout + arm.stderr
    assert (
        f"pr-arm: refusing to arm PR #71: no verify-fast-duration verdict is recorded for {head[:9]}: "
        f"the core job that records it has not finished for {head[:9]}; arm again once core completes"
    ) in arm.stderr
    assert "pr-rerun" not in arm.stdout + arm.stderr
    assert harness.github.auto_merges == []
    assert harness.github.dispatches == []


def _bumped_policy(harness: Harness, version: str) -> tuple[str, str, str]:
    text = harness.policy_file.read_text(encoding="utf-8")
    pin = omp_pin.pin_from_text(text)
    digests = {target: f"{index:02x}" * 32 for index, target in enumerate(sorted(pin.builds), start=10)}
    bumped = omp_pin.Pin(version, {t: omp_pin.Build(b.url, digests[t]) for t, b in pin.builds.items()})
    return omp_bump.replace_pin(text, bumped), digests["darwin-arm64"], omp_pin.pin_digest(bumped)


def test_arming_a_pin_bump_is_refused_until_the_owner_posts_the_mac_evidence_for_its_own_pin(
    harness: Harness,
) -> None:
    bumped, mac_digest, pin_digest = _bumped_policy(harness, "18.99.0")
    head = harness.branch("chore/omp-bump", {POLICY: bumped}, "bump the pinned omp build")
    harness.github.open_pull(75, "chore/omp-bump")
    _ci_in_progress(harness, head)

    refused = harness.arm(75, "chore/omp-bump")

    assert refused.code == 2, refused.stdout + refused.stderr
    assert "omp pin bump to 18.99.0 has no owner evidence" in refused.stderr
    assert "make omp-candidate-check VERSION=18.99.0" in refused.stderr
    assert harness.github.auto_merges == []

    line = f"Omp candidate check: version=18.99.0 platform=darwin-arm64 sha256={mac_digest} pin={pin_digest} head={head} result=releasable"
    harness.github.comments[75].append(
        {
            "id": 9001,
            "body": line,
            "user": {"login": "joeharris76", "type": "User"},
            "author_association": "OWNER",
            "created_at": now_text(),
            "updated_at": now_text(),
        }
    )

    evidenced = harness.arm(75, "chore/omp-bump")

    assert "omp pin bump" not in evidenced.stderr
    assert harness.github.auto_merges == []


def test_the_gate_holds_an_unevidenced_pin_bump_even_after_a_stand_in_attestation(harness: Harness) -> None:
    bumped, mac_digest, pin_digest = _bumped_policy(harness, "18.99.0")
    head = harness.branch("chore/omp-bump-gate", {POLICY: bumped}, "bump the pinned omp build")
    harness.github.open_pull(76, "chore/omp-bump-gate")
    harness.github.comments[76].append(
        {
            "id": 9002,
            "body": f"Stand-in oracle review: APPROVE {head}",
            "user": {"login": "joeharris76", "type": "User"},
            "author_association": "OWNER",
            "created_at": now_text(),
            "updated_at": now_text(),
        }
    )

    held = harness.gate(76, head)

    assert held.code == 3, held.stdout + held.stderr
    assert "omp pin bump refused" in held.stdout
    assert [item["state"] for item in harness.github.gate_statuses(head)] == ["failure"]
    assert "omp pin bump to 18.99.0 has no owner evidence" in harness.github.gate_statuses(head)[0]["description"]

    line = f"Omp candidate check: version=18.99.0 platform=darwin-arm64 sha256={mac_digest} pin={pin_digest} head={head} result=releasable"
    harness.github.comments[76].append(
        {
            "id": 9003,
            "body": line,
            "user": {"login": "joeharris76", "type": "User"},
            "author_association": "OWNER",
            "created_at": now_text(),
            "updated_at": now_text(),
        }
    )

    released = harness.gate(76, head)

    assert released.code == 0, released.stdout + released.stderr
    assert [item["state"] for item in harness.github.gate_statuses(head)] == ["failure", "success"]
