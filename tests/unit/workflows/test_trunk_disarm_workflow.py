from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "trunk-disarm.yml"
TRUNK = REPO_ROOT / ".github" / "workflows" / "trunk.yml"
SCRIPT = REPO_ROOT / "scripts" / "trunk_revert.py"
SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")


def _load() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _job() -> dict[str, Any]:
    return _load()["jobs"]["disarm"]


def _runs() -> str:
    return "\n".join(step.get("run", "") for step in _job()["steps"] if "run" in step)


def test_runs_when_a_trunk_run_on_develop_completes() -> None:
    workflow = _load()
    triggers = workflow[True] if True in workflow else workflow["on"]
    assert triggers == {"workflow_run": {"workflows": ["Trunk"], "types": ["completed"]}}
    assert yaml.safe_load(TRUNK.read_text(encoding="utf-8"))["name"] == triggers["workflow_run"]["workflows"][0]
    assert "develop" in str(_job()["if"])
    assert "head_branch" in str(_job()["if"])


def test_permissions_cover_the_disable_mutation_and_nothing_else() -> None:
    assert _load()["permissions"] == {}
    assert _job()["permissions"] == {"actions": "read", "contents": "write", "pull-requests": "write"}


def test_actions_are_pinned_by_sha() -> None:
    uses = [step["uses"] for step in _job()["steps"] if "uses" in step]
    assert uses
    assert all(SHA_PIN.match(pin) for pin in uses)


def test_the_job_checks_out_the_trusted_develop_copy() -> None:
    checkout = next(step for step in _job()["steps"] if "uses" in step)
    assert checkout["with"]["ref"] == "develop"
    assert checkout["with"]["persist-credentials"] is False
    assert "scripts/trunk_revert.py" in checkout["with"]["sparse-checkout"]


def test_selection_reuses_the_arm_gate_decision() -> None:
    assert "trunk_revert.py disarm" in _runs()
    source = SCRIPT.read_text(encoding="utf-8")
    assert "def disarm_plan" in source
    assert "streak_refusal" in source


def test_the_job_disables_but_never_enables_auto_merge() -> None:
    runs = _runs()
    assert "gh pr merge" in runs and "--disable-auto" in runs
    assert "--auto" not in runs.replace("--disable-auto", "")


def test_a_lookup_failure_disarms_nothing() -> None:
    runs = _runs()
    assert "set -euo pipefail" in runs
    assert "selected.txt" in runs


def test_a_green_completion_changes_nothing() -> None:
    runs = _runs()
    assert "nothing to disarm" in runs


def test_the_job_only_disables_and_reverifies() -> None:
    runs = _runs()
    for forbidden in (
        "--add-label",
        "--remove-label",
        "issue comment",
        "issue create",
        "pr close",
        "pr comment",
        "pr review",
        "pr merge --auto",
        "rebase",
        "run rerun",
        "workflow rerun",
    ):
        assert forbidden not in runs
    assert "gh pr view" in runs
