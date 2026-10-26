from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOWS_DIR = Path(__file__).resolve().parents[3] / ".github" / "workflows"
NEEDED = "needs.ci-paths.outputs.windows-leg-needed == 'true'"


def _jobs(name: str) -> dict[str, Any]:
    return yaml.safe_load((WORKFLOWS_DIR / name).read_text(encoding="utf-8"))["jobs"]


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    return next(step for step in job["steps"] if step.get("name") == name)


def _marker(command: str) -> str:
    match = re.search(r'-m "([^"]+)"', command)
    assert match, command
    return match.group(1)


def test_windows_leg_runs_only_when_the_selection_needs_it() -> None:
    job = _jobs("ci.yml")["windows-tests"]

    assert job["needs"] == "ci-paths"
    assert job["if"] == "${{ " + NEEDED + " }}"
    assert job["runs-on"] == "windows-latest"
    assert not job.get("continue-on-error")


def test_windows_leg_uses_the_nightly_windows_python_and_marker() -> None:
    job = _jobs("ci.yml")["windows-tests"]
    nightly = _jobs("nightly-v2.yml")["windows"]

    ours = _step(job, "Run the selected and changed test files on Windows")["run"]
    theirs = _step(nightly, "Run fast tests")["run"]
    assert _marker(ours) == _marker(theirs)
    assert "--timeout=120" in ours and "--timeout=120" in theirs
    assert _step(job, "Set up Python 3.12")["with"]["python-version"] == "3.12"
    assert job["timeout-minutes"] < nightly["timeout-minutes"]


def test_windows_leg_runs_the_files_published_by_ci_paths() -> None:
    jobs = _jobs("ci.yml")
    select = _step(jobs["ci-paths"], "Select tests for the Windows leg")
    upload = _step(jobs["ci-paths"], "Upload coverage selection")
    run = _step(jobs["windows-tests"], "Run the selected and changed test files on Windows")
    download = _step(jobs["windows-tests"], "Download coverage selection")

    assert select["id"] == "windows-select"
    assert "windows-targets" in select["run"]
    assert "--selection-in coverage-selection.json" in select["run"]
    assert "--changed-in .ci-paths/changed.txt" in select["run"]
    assert "windows-test-files.txt" in upload["with"]["path"]
    assert upload["with"]["name"] == download["with"]["name"]
    assert "coverage-selection/windows-test-files.txt" in run["run"]
    assert run["shell"] == "bash"
    assert jobs["ci-paths"]["outputs"]["windows-leg-needed"] == "${{ steps.windows-select.outputs.windows-leg-needed }}"


def test_windows_leg_trigger_covers_tests_scripts_project_scripts_and_benchbox_python() -> None:
    select = _step(_jobs("ci.yml")["ci-paths"], "Select tests for the Windows leg")["run"]
    pattern = re.search(r"grep -qE '([^']+)' \.ci-paths/changed\.txt", select)
    assert pattern, select
    trigger = re.compile(pattern.group(1))

    for path in (
        "tests/unit/test_module_state_guard.py",
        "tests/unit/scripts/oracle_reviewers/test_local_review.py",
        "scripts/sqlglot_fork_diff.py",
        "_project/scripts/preflight_targets.py",
        "benchbox/core/runner.py",
    ):
        assert trigger.search(path), path
    for path in ("docs/index.md", "tests/fixtures/data.json", "examples/run.py", ".github/workflows/ci.yml"):
        assert not trigger.search(path), path


def test_windows_leg_gates_the_required_core_aggregate() -> None:
    core = _jobs("ci.yml")["core"]
    run = "\n".join(step.get("run", "") for step in core["steps"])

    assert "windows-tests" in core["needs"]
    assert f"--expect windows-tests=${{{{ {NEEDED} }}}}" in run


def test_windows_leg_has_no_secrets_and_pinned_actions() -> None:
    job = _jobs("ci.yml")["windows-tests"]
    pinned = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")

    for step in job["steps"]:
        if "uses" in step:
            assert pinned.match(step["uses"]), step["uses"]
    assert "secrets." not in yaml.safe_dump(job)
    assert _step(job, "Checkout code")["with"]["persist-credentials"] is False
