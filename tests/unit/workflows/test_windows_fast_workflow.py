from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOWS_DIR = Path(__file__).resolve().parents[3] / ".github" / "workflows"
WINDOWS_FAST = WORKFLOWS_DIR / "windows-fast.yml"
NIGHTLY = WORKFLOWS_DIR / "nightly-v2.yml"

_PINNED_ACTION = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict)
    return triggers


def _job() -> dict[str, Any]:
    jobs = _load(WINDOWS_FAST)["jobs"]
    assert list(jobs) == ["windows"]
    return jobs["windows"]


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    return next(step for step in job["steps"] if step.get("name") == name)


def _marker_and_flags(command: str) -> tuple[str, list[str]]:
    marker = re.search(r'-m "([^"]+)"', command)
    assert marker, command
    flags = re.findall(r"\s(--[\w-]+(?:=\S+)?)", command)
    return marker.group(1), flags


def test_windows_fast_workflow_is_dispatch_only_with_optional_path_and_ref_inputs() -> None:
    triggers = _triggers(_load(WINDOWS_FAST))

    assert set(triggers) == {"workflow_dispatch"}
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"test_path", "ref"}
    assert all(not spec.get("required") for spec in inputs.values())
    assert inputs["test_path"]["default"] == "tests"


def test_windows_fast_workflow_runs_the_same_suite_as_the_nightly_windows_domain() -> None:
    ours = _step(_job(), "Run fast tests")["run"]
    nightly = _step(_load(NIGHTLY)["jobs"]["windows"], "Run fast tests")["run"]

    assert _marker_and_flags(ours) == _marker_and_flags(nightly)
    assert "python -m pytest" in ours
    assert _job()["runs-on"] == _load(NIGHTLY)["jobs"]["windows"]["runs-on"] == "windows-latest"
    assert _job()["timeout-minutes"] == _load(NIGHTLY)["jobs"]["windows"]["timeout-minutes"]


def test_windows_fast_workflow_passes_the_test_path_through_the_environment() -> None:
    step = _step(_job(), "Run fast tests")

    assert step["env"]["TEST_PATH"] == "${{ inputs.test_path }}"
    assert "${{" not in step["run"]
    assert '"${TEST_PATH:-tests}"' in step["run"]


def test_windows_fast_workflow_checks_out_the_optional_ref_without_persisting_credentials() -> None:
    checkout = _step(_job(), "Checkout code")

    assert checkout["with"]["ref"] == "${{ inputs.ref }}"
    assert checkout["with"]["persist-credentials"] is False


def test_windows_fast_workflow_has_read_only_permissions_and_no_secrets() -> None:
    workflow = _load(WINDOWS_FAST)

    assert workflow["permissions"] == {"contents": "read"}
    assert "permissions" not in _job()
    assert "secrets." not in WINDOWS_FAST.read_text(encoding="utf-8")


def test_windows_fast_workflow_pins_every_action_to_the_nightly_pins() -> None:
    nightly_pins = {
        step["uses"].split("@")[0]: step["uses"].split("@")[1]
        for job in _load(NIGHTLY)["jobs"].values()
        for step in job.get("steps", [])
        if "uses" in step
    }

    for step in _job()["steps"]:
        if "uses" not in step:
            continue
        assert _PINNED_ACTION.match(step["uses"]), step["uses"]
        action, sha = step["uses"].split("@")
        assert nightly_pins[action] == sha
