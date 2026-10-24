from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"
NIGHTLY = WORKFLOWS / "comment-policy-nightly.yml"
CI = WORKFLOWS / "ci.yml"


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps() -> list[dict[str, Any]]:
    jobs = _load(NIGHTLY)["jobs"]
    assert list(jobs) == ["whole-tree"]
    return jobs["whole-tree"]["steps"]


def test_nightly_runs_on_a_schedule_and_on_demand_but_never_on_pull_requests() -> None:
    triggers = _load(NIGHTLY).get("on", _load(NIGHTLY).get(True))
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert len(triggers["schedule"]) == 1


def test_nightly_scans_develop_with_read_only_permissions() -> None:
    workflow = _load(NIGHTLY)
    assert workflow["permissions"] == {"contents": "read", "issues": "write"}
    checkout = next(step for step in _steps() if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["ref"] == "develop"
    assert checkout["with"]["persist-credentials"] is False


def test_nightly_runs_the_launcher_over_the_whole_tree_without_a_base() -> None:
    scan = next(step for step in _steps() if step.get("id") == "scan")
    assert scan["run"] == "python -I scripts/run_comment_policy.py --full"


def test_a_failed_scan_alerts_through_an_issue_and_still_fails_the_run() -> None:
    steps = {step["name"]: step for step in _steps() if "name" in step}
    scan = next(step for step in _steps() if step.get("id") == "scan")
    assert scan["continue-on-error"] is True
    alert = steps["Open or update the failure issue"]
    assert alert["if"] == "steps.scan.outcome == 'failure'"
    assert "gh issue create" in alert["run"] and "gh issue comment" in alert["run"]
    fail = steps["Fail the run when the scan failed"]
    assert fail["if"] == "steps.scan.outcome == 'failure'"
    assert fail["run"] == "exit 1"
    close = steps["Close the failure issue after a clean scan"]
    assert close["if"] == "steps.scan.outcome == 'success'"
    for step in (alert, close):
        assert "GITHUB_SHA" not in step["run"]
        assert "git rev-parse HEAD" in step["run"]


def test_nightly_uses_the_same_pinned_actions_as_the_pull_request_job() -> None:
    def pins(path: Path, job: str | None) -> set[str]:
        jobs = _load(path)["jobs"]
        steps = jobs[job]["steps"] if job else [s for j in jobs.values() for s in j["steps"]]
        return {step["uses"] for step in steps if step.get("uses", "").split("/")[0] in {"actions", "astral-sh"}}

    ci_pins = pins(CI, "comment-policy")
    assert pins(NIGHTLY, "whole-tree") <= ci_pins
