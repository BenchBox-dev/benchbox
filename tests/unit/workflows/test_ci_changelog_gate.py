from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
STEP_NAME = "Check changelog fragments"


def _steps() -> list[dict[str, Any]]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]["ci-paths"]["steps"]


def _step() -> dict[str, Any]:
    matches = [step for step in _steps() if step.get("name") == STEP_NAME]
    assert len(matches) == 1
    return matches[0]


def test_changelog_step_runs_after_classification_and_before_flag_step() -> None:
    names = [step.get("name") for step in _steps()]
    assert names.index("Classify changed paths") < names.index(STEP_NAME)


def test_title_reaches_the_script_through_the_environment_only() -> None:
    step = _step()
    assert step["env"]["PR_TITLE"] == "${{ github.event.pull_request.title }}"
    assert "${{" not in step["run"]
    assert '--title "$PR_TITLE"' in step["run"]


def test_release_identity_is_derived_from_the_base_commit_not_from_pull_request_code() -> None:
    step = _step()
    assert '--release-base-sha "$EVENT_BASE_SHA"' in step["run"]
    assert "RELEASE_IDENTITY_CHANGED" not in step["env"]
    assert "release_flow.py" not in step["run"]


def test_changelog_step_guards_unreleased_and_diffs_against_the_merge_base() -> None:
    run = _step()["run"]
    assert "--require-empty-unreleased" in run
    assert '"$EVENT_BASE_SHA...HEAD"' in run
    assert "--changed-files-from" in run
    assert ".ci-paths/changed.txt" not in run


def test_changelog_gate_runs_the_base_commit_copy_of_its_own_code() -> None:
    run = _step()["run"]
    assert 'git show "$EVENT_BASE_SHA:scripts/changelog_fragments.py"' in run
    assert 'git show "$EVENT_BASE_SHA:_project/scripts/soundness_paths.py"' in run
    assert '"$trusted/scripts/changelog_fragments.py"' in run
    assert "python scripts/changelog_fragments.py" not in run
    assert "cp scripts/changelog_fragments.py" not in run


def test_changelog_step_is_not_conditional() -> None:
    assert "if" not in _step()


def test_title_edit_lane_rechecks_the_entry_rule_with_the_title_in_the_environment() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["text-edit-verdict"]["steps"]
    matches = [step for step in steps if step.get("name") == "Re-check the changelog entry against the edited title"]
    assert len(matches) == 1
    step = matches[0]
    assert step["env"]["PR_TITLE"] == "${{ github.event.pull_request.title }}"
    assert "github.event.pull_request.title" not in step["run"]
    assert '--title "$PR_TITLE"' in step["run"]
    assert "--require-empty-unreleased" in step["run"]


def test_title_edit_lane_diffs_against_the_merge_base_like_the_main_lane() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["text-edit-verdict"]["steps"]
    step = next(s for s in steps if s.get("name") == "Re-check the changelog entry against the edited title")
    assert '"$EVENT_BASE_SHA...HEAD"' in step["run"]


def test_title_edit_lane_also_runs_the_base_commit_copy_of_the_gate() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["text-edit-verdict"]["steps"]
    step = next(s for s in steps if s.get("name") == "Re-check the changelog entry against the edited title")
    assert 'git show "$EVENT_BASE_SHA:scripts/changelog_fragments.py"' in step["run"]
    assert "python scripts/changelog_fragments.py" not in step["run"]
    assert "release_flow.py" not in step["run"]
    assert '--release-base-sha "$EVENT_BASE_SHA"' in step["run"]


def test_both_lanes_list_changed_paths_without_rename_detection() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    edit_steps = workflow["jobs"]["text-edit-verdict"]["steps"]
    edit = next(s for s in edit_steps if s.get("name") == "Re-check the changelog entry against the edited title")
    for run in (_step()["run"], edit["run"]):
        assert "--name-only --no-renames --diff-filter=ACDMRT" in run
        assert "--name-only --diff-filter=A " in run
