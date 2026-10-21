from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
SYNC = WORKFLOWS / "sync-results-data-to-published.yml"

STATUS_API_FRAGMENT = "statuses/"
OPEN_PR_STEP_NAME = "Open or update draft PR"
VALIDATE_STEP_NAME = "Validate mirrored corpus content"
STATUS_STEP_NAME = "Report validation as a commit status on the mirror PR"


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    triggers = workflow.get("on", workflow.get(True))
    assert triggers is not None, "workflow has no `on:` block"
    return triggers


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps() -> list[dict[str, Any]]:
    workflow = _load(SYNC)
    return workflow["jobs"]["mirror"]["steps"]


def _step(name: str) -> dict[str, Any]:
    for step in _steps():
        if step.get("name") == name:
            return step
    raise AssertionError(f"sync-results-data-to-published.yml has no step named {name!r}")


def test_published_results_base_mirror_pr_reports_a_commit_status() -> None:
    step = _step(STATUS_STEP_NAME)
    run = step.get("run", "")
    assert STATUS_API_FRAGMENT in run, (
        "no step posts to the Statuses API; a mirror PR still ends up with an empty statusCheckRollup"
    )
    workflow = _load(SYNC)
    assert workflow["permissions"].get("statuses") == "write", (
        "workflow lacks statuses: write, so a status-posting call would 403 even if attempted"
    )


def test_published_results_base_mirror_pr_status_condition_matches_pr_creation() -> None:
    open_pr_condition = _step(OPEN_PR_STEP_NAME).get("if")
    status_condition = _step(STATUS_STEP_NAME).get("if")
    assert status_condition == open_pr_condition, (
        f"status step condition {status_condition!r} does not match the PR-opening "
        f"step condition {open_pr_condition!r}; some mirror PRs would still end up uncovered"
    )


def test_published_results_base_mirror_pr_status_reflects_real_validation() -> None:
    status_step = _step(STATUS_STEP_NAME)
    status_env = status_step.get("env", {})
    assert status_env.get("STATE") == "${{ steps.validate.outputs.state }}", (
        "status step does not read steps.validate.outputs.state; it cannot be reporting a real result"
    )

    validate_run = _step(VALIDATE_STEP_NAME).get("run", "")
    assert "scripts/validate_submission.py" in validate_run, (
        "validate step never invokes scripts/validate_submission.py; the reported check validates nothing"
    )
    assert "scripts/generate_corpus_inventory.py" in validate_run and "--check" in validate_run, (
        "validate step never checks corpus-inventory.json freshness"
    )
    assert 'STATE="failure"' in validate_run, (
        "validate step has no failure path; a broken bundle would still report state=success"
    )


def test_published_results_base_and_pr_validation_share_trusted_partial_policy() -> None:
    creator_run = _step(VALIDATE_STEP_NAME).get("run", "")
    assert "--allow-partial-validation" in creator_run

    submission = _load(WORKFLOWS / "validate-submission.yml")
    submission_steps = submission["jobs"]["validate"]["steps"]
    pr_run = next(step.get("run", "") for step in submission_steps if step.get("name") == "Validate bundles")
    assert 'ALLOW_PARTIAL_VALIDATION=""' in pr_run
    assert '[ "$PR_AUTHOR" = "github-actions[bot]" ]' in pr_run
    trusted_block = pr_run.split("auto/results-mirror-*)", maxsplit=1)[1].split(";;", maxsplit=1)[0]
    assert 'ALLOW_PARTIAL_VALIDATION="--allow-partial-validation"' in trusted_block
    assert "$REQUIRE_MANIFEST $ALLOW_PARTIAL_VALIDATION" in pr_run


def test_submission_self_green_guard_covers_shared_query_status_policy() -> None:
    submission = _load(WORKFLOWS / "validate-submission.yml")
    steps = submission["jobs"]["validate"]["steps"]
    guard = next(
        step.get("run", "")
        for step in steps
        if step.get("name") == "Reject validator or workflow changes in a submission PR"
    )
    assert "benchbox/core/results/query_status.py" in guard


def test_submission_self_green_guard_covers_shared_schema_policy() -> None:
    submission = _load(WORKFLOWS / "validate-submission.yml")
    steps = submission["jobs"]["validate"]["steps"]
    guard = next(
        step.get("run", "")
        for step in steps
        if step.get("name") == "Reject validator or workflow changes in a submission PR"
    )
    assert "benchbox/core/results/schema_policy.py" in guard


def test_published_results_base_mirror_validation_runs_before_the_pr_is_opened() -> None:
    names = [step.get("name") for step in _steps()]
    assert names.index(VALIDATE_STEP_NAME) < names.index(OPEN_PR_STEP_NAME), (
        "validation step runs after the PR is opened; the PR could exist before its content is checked"
    )
    assert names.index(STATUS_STEP_NAME) < names.index(OPEN_PR_STEP_NAME), (
        "status is posted after the PR is opened rather than before/alongside it"
    )


def test_published_results_base_mirror_stays_a_corpus_check_not_develops_matrix() -> None:
    run_bodies = "\n".join(step.get("run", "") for step in _steps())
    for forbidden in ("ruff check", "ruff format", "ty check", "pytest", "uv sync --group dev"):
        assert forbidden not in run_bodies, (
            f"{forbidden!r} appears in a sync workflow run step; this imports develop's CI matrix onto a corpus mirror"
        )


def test_published_results_base_guard_still_has_no_branch_filter() -> None:
    guard = WORKFLOWS / "ci.yml"
    pull_request = _triggers(_load(guard))["pull_request"] or {}
    assert "branches" not in pull_request
    assert "paths" not in pull_request


def test_published_results_base_mirror_survives_a_failure_in_its_own_validation_steps() -> None:
    for step_name in (STATUS_STEP_NAME, OPEN_PR_STEP_NAME):
        condition = _step(step_name).get("if", "")
        assert "always()" in condition, (
            f"{step_name!r} does not use always(), so a failure in the validation steps above it "
            "would skip it and the mirror PR would silently not exist"
        )


def test_published_results_base_mirror_never_reports_success_without_a_verdict() -> None:
    body = _step(STATUS_STEP_NAME)["run"]
    assert 'STATE="${STATE:-error}"' in body, (
        "the status step does not default an absent verdict to `error`; an empty state would be posted as-is "
        "or, worse, silently treated as success"
    )
    assert "STATE:-success" not in body, "an absent verdict defaults to success"


def test_published_results_base_mirror_run_is_red_unless_validation_passed() -> None:
    condition = _step("Fail the run if mirrored content failed validation").get("if", "")
    assert "!= 'success'" in condition, (
        f"failure gate is {condition!r}; a run that could not determine the verdict would still be green"
    )
