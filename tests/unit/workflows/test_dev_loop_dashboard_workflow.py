from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOW = Path(__file__).resolve().parents[3] / ".github/workflows/dev-loop-dashboard.yml"
PINNED = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_runs_weekly_and_on_demand(workflow):
    triggers = workflow[True]
    assert len(triggers["schedule"]) == 1
    assert triggers["schedule"][0]["cron"].split()[2:] == ["*", "*", "1"]
    assert "days" in triggers["workflow_dispatch"]["inputs"]


def test_holds_read_only_permissions_and_one_runner(workflow):
    assert workflow["permissions"] == {"actions": "read", "contents": "read", "pull-requests": "read"}
    jobs = workflow["jobs"]
    assert list(jobs) == ["dashboard"]
    assert "permissions" not in jobs["dashboard"] and "strategy" not in jobs["dashboard"]
    assert workflow["concurrency"]["cancel-in-progress"] is False


def test_every_action_is_pinned_to_a_commit(workflow):
    uses = [step["uses"] for step in workflow["jobs"]["dashboard"]["steps"] if "uses" in step]
    assert uses and all(PINNED.match(value) for value in uses)


def test_collects_with_the_default_token_and_uploads_the_report(workflow):
    steps = workflow["jobs"]["dashboard"]["steps"]
    collect = next(step for step in steps if "dev_loop_dashboard.py" in step.get("run", ""))
    assert collect["env"]["GITHUB_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"
    upload = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert "--max-api-calls 900" in collect["run"]
    assert upload["with"]["name"] == "dev-loop-dashboard-${{ github.run_id }}"
    assert upload["with"]["if-no-files-found"] == "error"
