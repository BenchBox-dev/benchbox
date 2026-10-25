from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "site-inputs.yml"


def _text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_parent_resolution_invokes_tested_script() -> None:
    assert "scripts/site_inputs_parent.py" in _text()


def test_parent_resolution_has_no_inline_site_inputs_run_list() -> None:
    assert "gh run list --workflow site-inputs.yml" not in _text()


def test_certification_has_no_inline_trunk_run_list() -> None:
    assert "gh run list --workflow trunk.yml" not in _text()
    assert "certified_by=${RUN_ID" not in _text()
    assert "--run-id" in _text()


def test_workflow_keeps_dispatch_parent_input_and_bundle_outputs() -> None:
    workflow = yaml.safe_load(_text())
    triggers = workflow.get("on") or workflow.get(True)
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert "parent_core_sha" in inputs
    resolve = None
    for step in workflow["jobs"]["build"]["steps"]:
        if step.get("id") == "shas":
            resolve = step
            break
    assert resolve is not None
    assert resolve["env"]["DISPATCH_PARENT"] == "${{ inputs.parent_core_sha }}"
    assert "scripts/site_inputs_parent.py" in str(resolve.get("run", ""))


REPORTER_TITLE = "Site-inputs bundle build failed"


def _jobs() -> dict:
    return yaml.safe_load(_text())["jobs"]


def test_build_publishes_core_sha_and_failure_class_outputs() -> None:
    outputs = _jobs()["build"]["outputs"]
    assert outputs["core_sha"] == "${{ steps.shas.outputs.core_sha }}"
    assert outputs["failure_class"] == "${{ steps.shas.outputs.failure_class }}"


def test_reporter_honors_the_expected_rejection_input() -> None:
    workflow = yaml.safe_load(_text())
    triggers = workflow.get("on") or workflow.get(True)
    assert "expected_rejection" in triggers["workflow_dispatch"]["inputs"]
    report = _jobs()["report-failure"]["steps"][0]
    assert report["env"]["EXPECTED_REJECTION"] == "${{ inputs.expected_rejection }}"
    assert report["env"]["FAILURE_CLASS"] == "${{ needs.build.outputs.failure_class }}"


def test_reporter_suppresses_repeat_comments_with_a_hidden_marker() -> None:
    script = _jobs()["report-failure"]["steps"][0]["run"]
    assert "site-bundle-reporter class=" in script
    assert 'gh issue view "$NUMBER"' in script


def test_close_job_closes_only_the_core_label_on_build_success() -> None:
    close = _jobs()["close-failure"]
    assert close["needs"] == "build"
    assert close["if"] == "success()"
    assert close["permissions"] == {"issues": "write"}
    step = close["steps"][0]
    assert step["env"]["EXPECTED_REJECTION"] == "${{ inputs.expected_rejection }}"
    assert (
        step["env"]["CORE_SHA"]
        == "${{ needs.build.outputs.core_sha || github.event.workflow_run.head_sha || github.sha }}"
    )
    script = step["run"]
    assert 'gh issue list --repo "$REPO" --label site-bundle-failed --state open' in script
    assert 'gh issue view "$NUMBER"' in script
    assert f'if [ "$TITLE" != "{REPORTER_TITLE}" ]' in script
    assert 'gh issue comment "$NUMBER"' in script
    assert 'gh issue close "$NUMBER"' in script


def test_core_reporter_never_uses_the_relay_label() -> None:
    assert "site-bundle-undeployed" not in _text()


def test_reporter_jobs_add_no_permission_or_secret() -> None:
    workflow = yaml.safe_load(_text())
    assert workflow.get("permissions") == {"contents": "read", "actions": "read"}
    assert "permissions" not in _jobs()["build"]
    for name in ("report-failure", "close-failure"):
        job = _jobs()[name]
        assert job["permissions"] == {"issues": "write"}
        assert "secrets." not in str(job)


def test_workflow_never_selects_a_candidate_profile() -> None:
    text = _text()
    assert "--profile" not in text
    assert "candidate" not in text
    assert "site-inputs-candidate" not in text
