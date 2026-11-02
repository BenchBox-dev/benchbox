from __future__ import annotations

import json
from typing import Any

import pytest

from _project.scripts.soundness_paths import any_soundness_path

from .fake_github import ACCOUNTS, APP_TOKEN, now_text
from .harness import REPO_ROOT, Harness

pytestmark = [pytest.mark.integration, pytest.mark.medium]

RULESET = REPO_ROOT / "tests" / "fixtures" / "ruleset-develop-squash-only.json"
CI_CONTEXTS = ("core", "explorer", "results-data", "docs", "tooling", "hold")


def _check_run(name: str, head: str, index: int) -> dict[str, Any]:
    return {
        "id": 9000 + index,
        "name": name,
        "head_sha": head,
        "status": "completed",
        "conclusion": "success",
        "started_at": "2026-10-30T12:00:00Z",
        "completed_at": "2026-10-30T12:05:00Z",
        "details_url": f"https://github.localhost/o/r/actions/runs/{index}",
        "check_suite": {"id": 8000 + index},
    }


def _green_ci(harness: Harness, head: str) -> list[dict[str, Any]]:
    runs = [_check_run(name, head, index) for index, name in enumerate(CI_CONTEXTS)]
    harness.github.check_runs[head] = runs
    harness.github.rulesets = json.loads(RULESET.read_text(encoding="utf-8"))["rules"]
    return [{key: run[key] for key in ("name", "head_sha", "status", "conclusion", "started_at")} for run in runs]


def _gate_status(harness: Harness, head: str, state: str) -> None:
    harness.github.statuses[head].append(
        {
            "id": next(harness.github.ids),
            "state": state,
            "context": "oracle-gate",
            "description": "oracle-gate: scripted",
            "target_url": None,
            "creator": ACCOUNTS[APP_TOKEN],
            "created_at": now_text(),
            "updated_at": now_text(),
        }
    )


def _soundness_pull(harness: Harness, number: int, branch: str) -> tuple[str, dict[str, Any]]:
    assert any_soundness_path(["Makefile"])
    head = harness.branch(branch, {"Makefile": "all:\n\t@true\n"}, "change a soundness path")
    harness.github.open_pull(number, branch)
    checks = _green_ci(harness, head)
    evidence = {"delivery_mode": "serial", "review_decision": "", "dispositions_complete": True, "check_runs": checks}
    return head, evidence


@pytest.mark.parametrize("state", ["failure", "pending"])
def test_a_soundness_pr_without_a_passing_oracle_gate_is_not_armed(harness: Harness, state: str) -> None:
    head, evidence = _soundness_pull(harness, 81, "feat/soundness-ungated")
    _gate_status(harness, head, state)

    step = harness.landing_ready(81, "feat/soundness-ungated", evidence)

    assert step.code == 1, step.stdout + step.stderr
    assert "NOT READY" in step.stdout
    assert "oracle-gate" in step.stdout
    assert harness.github.auto_merges == []


def test_a_soundness_pr_with_no_oracle_gate_status_is_not_armed(harness: Harness) -> None:
    _, evidence = _soundness_pull(harness, 82, "feat/soundness-no-gate")

    step = harness.landing_ready(82, "feat/soundness-no-gate", evidence)

    assert step.code == 1, step.stdout + step.stderr
    assert "oracle-gate" in step.stdout
    assert harness.github.auto_merges == []


def test_a_soundness_pr_is_armed_without_an_approving_review_once_the_oracle_gate_passes(harness: Harness) -> None:
    head, evidence = _soundness_pull(harness, 83, "feat/soundness-gated")
    _gate_status(harness, head, "success")

    step = harness.landing_ready(83, "feat/soundness-gated", evidence)

    assert step.code == 0, step.stdout + step.stderr
    assert [(item["pr"], item["head"]) for item in harness.github.auto_merges] == [(83, head)]
