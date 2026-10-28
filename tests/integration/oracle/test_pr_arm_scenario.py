from __future__ import annotations

import json

import pytest

from .harness import CHECKER, REPO_ROOT, Harness

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
