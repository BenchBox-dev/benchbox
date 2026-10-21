from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"

VALID_EVENTS = frozenset(
    {
        "branch_protection_rule",
        "check_run",
        "check_suite",
        "create",
        "delete",
        "deployment",
        "deployment_status",
        "discussion",
        "discussion_comment",
        "fork",
        "gollum",
        "image_version",
        "issue_comment",
        "issues",
        "label",
        "merge_group",
        "milestone",
        "page_build",
        "public",
        "pull_request",
        "pull_request_review",
        "pull_request_review_comment",
        "pull_request_target",
        "push",
        "registry_package",
        "release",
        "repository_dispatch",
        "schedule",
        "status",
        "watch",
        "workflow_call",
        "workflow_dispatch",
        "workflow_run",
    }
)


def _events(workflow: dict[str, Any]) -> set[str]:
    triggers = workflow.get("on", workflow.get(True))
    assert triggers is not None, "workflow has no `on:` block"
    if isinstance(triggers, str):
        return {triggers}
    if isinstance(triggers, list):
        return set(triggers)
    return set(triggers)


def _invalid_events(workflow: dict[str, Any]) -> set[str]:
    return _events(workflow) - VALID_EVENTS


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda path: path.name)
def test_every_workflow_listens_only_for_real_github_actions_events(path: Path) -> None:
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert _invalid_events(workflow) == set()


def test_a_webhook_without_a_workflow_event_is_rejected() -> None:
    workflow = yaml.safe_load("on:\n  pull_request_review_thread:\n    types: [resolved]\n")
    assert _invalid_events(workflow) == {"pull_request_review_thread"}


@pytest.mark.parametrize("on", ["push", ["push", "workflow_dispatch"], {"pull_request": {"types": ["opened"]}}])
def test_valid_event_shapes_are_accepted(on: Any) -> None:
    assert _invalid_events({"on": on}) == set()
