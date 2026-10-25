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


def test_release_identity_exemption_is_wired_from_the_select_step() -> None:
    step = _step()
    assert step["env"]["RELEASE_IDENTITY_CHANGED"] == "${{ steps.release.outputs.release-identity-changed }}"
    assert "--release-identity-changed" in step["run"]
    assert any(s.get("id") == "release" for s in _steps())


def test_changelog_step_guards_unreleased_and_reads_the_classified_changed_list() -> None:
    run = _step()["run"]
    assert "scripts/changelog_fragments.py check" in run
    assert "--require-empty-unreleased" in run
    assert "--changed-files-from .ci-paths/changed.txt" in run


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
