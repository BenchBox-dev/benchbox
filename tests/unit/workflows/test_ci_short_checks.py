from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "ci.yml"
JOB = "short-checks"
GATE_CHECKS = {
    "tooling": {"always": {"comment-policy"}, "expect": {}},
    "docs": {
        "always": set(),
        "expect": {
            "example-validation": "needs.ci-paths.outputs.unit-docs == 'true'",
            "spellcheck": "needs.ci-paths.outputs.unit-docs == 'true'",
        },
    },
    "results-data": {
        "always": set(),
        "expect": {"results-data-check": "needs.ci-paths.outputs.unit-results-data == 'true'"},
    },
}
CHECK_STEPS = {
    "comment-policy": ["comment-policy", "notice-drift"],
    "spellcheck": ["spellcheck"],
    "results-data-check": [
        "results-python",
        "results-install",
        "results-inventory",
        "results-corpus",
        "results-validator",
        "results-tests",
    ],
    "example-validation": ["docs-python", "docs-install", "docs-validate", "docs-urls", "docs-generate"],
}
PATH_CONDITIONS = {
    "spellcheck": "needs.ci-paths.outputs.unit-docs == 'true'",
    "results-python": "needs.ci-paths.outputs.unit-results-data == 'true'",
    "docs-python": "needs.ci-paths.outputs.unit-docs == 'true'",
}


def _jobs() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _steps() -> dict[str, dict[str, Any]]:
    return {step["id"]: step for step in _jobs()[JOB]["steps"] if "id" in step}


@pytest.mark.parametrize("gate", sorted(GATE_CHECKS))
def test_each_gate_enforces_exactly_its_own_checks(gate: str) -> None:
    job = _jobs()[gate]
    assert JOB in job["needs"]
    run = next(step["run"] for step in job["steps"] if step.get("id") == "aggregate")
    always = set(re.findall(rf"--always {JOB}\.([a-z-]+)", run))
    expect = dict(re.findall(rf"--expect {JOB}\.([a-z-]+)=\$\{{\{{ (.+?) \}}\}}", run))
    assert always == GATE_CHECKS[gate]["always"]
    assert expect == GATE_CHECKS[gate]["expect"]
    assert f"--always {JOB} " not in run and f"--expect {JOB}=" not in run


def test_every_check_reports_an_output_built_from_all_of_its_steps() -> None:
    outputs = _jobs()[JOB]["outputs"]
    assert set(outputs) == set(CHECK_STEPS)
    for check, step_ids in CHECK_STEPS.items():
        for step_id in step_ids:
            assert f"steps.{step_id}.conclusion" in outputs[check], f"{check} ignores {step_id}"
    assert set(_steps()) >= {step_id for step_ids in CHECK_STEPS.values() for step_id in step_ids}


def test_comment_policy_runs_whatever_the_path_filter_decides() -> None:
    job = _jobs()[JOB]
    assert "!cancelled()" in job["if"] and "always()" not in job["if"]
    steps = _steps()
    assert "if" not in steps["comment-policy"] and "if" not in steps["notice-drift"]


def test_path_gated_checks_keep_their_filters_and_run_after_another_check_fails() -> None:
    steps = _steps()
    for step_id, condition in PATH_CONDITIONS.items():
        assert condition in steps[step_id]["if"]
    gated = [step_id for check in CHECK_STEPS if check != "comment-policy" for step_id in CHECK_STEPS[check]]
    for step_id in gated:
        assert steps[step_id]["if"].startswith("${{ !cancelled() && steps."), step_id
    assert "submission-validator-changed == 'true'" in steps["results-validator"]["if"]


def test_only_the_advisory_spellcheck_tolerates_failure() -> None:
    tolerant = {step.get("id") for step in _jobs()[JOB]["steps"] if step.get("continue-on-error")}
    assert tolerant == {"spellcheck"}
    assert "continue-on-error" not in _jobs()[JOB]


def test_each_check_keeps_its_python_version() -> None:
    steps = _steps()
    assert steps["results-python"]["with"]["python-version"] == "3.12"
    assert steps["docs-python"]["with"]["python-version"] == "3.11"
    for step_id in CHECK_STEPS["results-data-check"][1:]:
        assert steps[step_id]["env"]["UV_PYTHON"] == "3.12", step_id
    for step_id in CHECK_STEPS["example-validation"][1:]:
        assert steps[step_id]["env"]["UV_PYTHON"] == "3.11", step_id
