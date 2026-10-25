from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOWS_DIR = Path(__file__).resolve().parents[3] / ".github" / "workflows"

_JUNIT_FLAGS = ("-o junit_duration_report=total", "--junitxml=fast-junit/junit.xml")

_CASES = [
    pytest.param(
        "ci.yml",
        "code-test",
        "fast-junit-${{ github.event.pull_request.number }}-${{ github.event.pull_request.head.sha }}",
        id="pr",
    ),
    pytest.param("trunk.yml", "fast", "fast-junit-trunk-${{ github.sha }}", id="trunk"),
]


def _steps(workflow: str, job: str) -> list[dict[str, Any]]:
    data = yaml.safe_load((WORKFLOWS_DIR / workflow).read_text(encoding="utf-8"))
    return data["jobs"][job]["steps"]


def _step(steps: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(step for step in steps if step.get("name") == name)


@pytest.mark.parametrize(("workflow", "job", "artifact"), _CASES)
def test_fast_pytest_step_writes_total_duration_junit(workflow: str, job: str, artifact: str) -> None:
    steps = _steps(workflow, job)
    run = _step(steps, "Run fast tests")["run"]

    for flag in _JUNIT_FLAGS:
        assert flag in run
    assert len(re.findall(r"--junitxml", run)) == 1


@pytest.mark.parametrize(("workflow", "job", "artifact"), _CASES)
def test_junit_is_scoped_to_the_fast_tier_invocation(workflow: str, job: str, artifact: str) -> None:
    for step in _steps(workflow, job):
        if step.get("name") == "Run fast tests":
            continue
        assert "--junitxml" not in step.get("run", "")
        assert "junit_duration_report" not in step.get("run", "")


@pytest.mark.parametrize(("workflow", "job", "artifact"), _CASES)
def test_junit_artifact_uploaded_with_fourteen_day_retention(workflow: str, job: str, artifact: str) -> None:
    steps = _steps(workflow, job)
    upload = _step(steps, "Upload fast-tier junit")

    assert upload["uses"].startswith("actions/upload-artifact@")
    assert upload["if"] == "always()"
    assert upload["with"]["name"] == artifact
    assert upload["with"]["path"] == "fast-junit/junit.xml"
    assert upload["with"]["retention-days"] == 14
    assert steps.index(upload) > steps.index(_step(steps, "Run fast tests"))


@pytest.mark.parametrize(("workflow", "job", "artifact"), _CASES)
def test_junit_parse_check_uses_duration_policy_and_cannot_fail_the_job(workflow: str, job: str, artifact: str) -> None:
    steps = _steps(workflow, job)
    check = _step(steps, "Check fast-tier junit parses")

    assert "collect_junit_durations" in check["run"]
    assert "fast-junit/junit.xml" in check["run"]
    assert check["continue-on-error"] is True
    assert check["if"] == "always()"
