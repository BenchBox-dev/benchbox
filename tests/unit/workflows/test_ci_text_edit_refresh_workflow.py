from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.ci_text_edit_verdict import TEXT_EDIT_RUN_NAME

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/ci-text-edit-refresh.yml"
CI = ROOT / ".github/workflows/ci.yml"
SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")


def _load(path: Path = WORKFLOW) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _job() -> dict[str, Any]:
    return _load()["jobs"]["refresh"]


def test_runs_only_when_a_ci_run_completes() -> None:
    workflow = _load()
    triggers = workflow[True] if True in workflow else workflow["on"]
    assert triggers == {"workflow_run": {"workflows": ["CI"], "types": ["completed"]}}


def test_skips_text_edit_runs_and_other_events_without_a_runner() -> None:
    condition = str(_job()["if"])
    assert "github.event.workflow_run.event == 'pull_request'" in condition
    assert f"github.event.workflow_run.display_title != '{TEXT_EDIT_RUN_NAME}'" in condition


def test_the_ci_workflow_keeps_the_name_this_workflow_listens_for() -> None:
    assert _load(CI)["name"] == _load()[True if True in _load() else "on"]["workflow_run"]["workflows"][0]


def test_permissions_are_minimal() -> None:
    assert _load()["permissions"] == {}
    assert _job()["permissions"] == {"actions": "write", "contents": "read"}


def test_actions_are_pinned_by_sha() -> None:
    uses = [step["uses"] for step in _job()["steps"] if "uses" in step]
    assert uses
    assert all(SHA_PIN.match(pin) for pin in uses)


def test_the_step_calls_the_refresh_command_for_the_completed_head() -> None:
    commands = [step["run"] for step in _job()["steps"] if "run" in step]
    assert len(commands) == 1
    assert "ci_text_edit_verdict.py refresh" in commands[0]
    steps = [step for step in _job()["steps"] if "run" in step]
    assert steps[0]["env"]["HEAD_SHA"] == "${{ github.event.workflow_run.head_sha }}"


def test_the_ci_verdict_job_is_short_and_does_not_poll() -> None:
    job = _load(CI)["jobs"]["text-edit-verdict"]
    assert job["timeout-minutes"] <= 5
    command = " ".join(step["run"] for step in job["steps"] if "run" in step)
    assert "ci_text_edit_verdict.py verdict" in command
    assert "--attempts" not in command
    assert "--interval" not in command
