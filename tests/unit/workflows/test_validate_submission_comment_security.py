from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "validate-submission-comment.yml"
PRODUCER_PATH = REPO_ROOT / ".github" / "workflows" / "validate-submission.yml"


def _comment_step_script() -> str:
    workflow_yaml = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = workflow_yaml["jobs"]["comment"]["steps"]
    for step in steps:
        if step.get("name") == "Post or update the comment":
            return step["with"]["script"]
    raise AssertionError("could not find the 'Post or update the comment' step")


def test_comment_target_pr_is_not_read_from_the_untrusted_artifact() -> None:
    script = _comment_step_script()
    assert "readFileSync('artifact/pr_number.txt'" not in script, (
        "the comment-target PR number must not be read from the artifact -- "
        "it is attacker-influenced (produced by a PR-supplied-code run)"
    )


def test_comment_target_pr_is_derived_from_trusted_head_sha() -> None:
    script = _comment_step_script()
    assert "context.payload.workflow_run.head_sha" in script
    assert "listPullRequestsAssociatedWithCommit" in script
    assert "associatedPRs.length !== 1" in script


def test_producer_no_longer_stages_a_pr_number_in_the_artifact() -> None:
    text = PRODUCER_PATH.read_text(encoding="utf-8")
    assert "pr_number.txt" not in text


def test_comment_job_runs_for_pull_request_target_runs() -> None:
    workflow_yaml = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    cond = workflow_yaml["jobs"]["comment"].get("if", "")
    assert "pull_request_target" in cond


