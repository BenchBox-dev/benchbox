from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "oracle-review.yml"
FORBIDDEN_COMMANDS = re.compile(r"(?<![\w-])(gh|curl|python3?|uv|uvx|pip)(?![\w-])")


def _load() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers() -> dict[str, Any]:
    workflow = _load()
    triggers = workflow.get("on", workflow.get(True))
    assert triggers is not None, "workflow has no `on:` block"
    return triggers


def _job() -> dict[str, Any]:
    return _load()["jobs"]["review-event"]


def test_workflow_name_is_kept_for_the_gate_bridge() -> None:
    assert _load()["name"] == "oracle-review"


def test_only_review_events_trigger_the_workflow() -> None:
    triggers = _triggers()
    assert set(triggers) == {"pull_request_review"}
    assert triggers["pull_request_review"]["types"] == ["edited", "dismissed"]


def test_workflow_has_no_token_permissions() -> None:
    workflow = _load()
    assert workflow["permissions"] == {}
    assert "permissions" not in _job()


def test_one_job_named_review_event_produces_no_oracle_review_check() -> None:
    workflow = _load()
    assert list(workflow["jobs"]) == ["review-event"]
    assert _job()["name"] == "review-event"
    assert "oracle-review" not in {workflow["jobs"]["review-event"]["name"], *workflow["jobs"]}


def test_job_skips_release_pull_requests() -> None:
    assert _job()["if"] == "github.event.pull_request.base.ref != 'release'"


def test_job_is_a_single_no_op_step() -> None:
    steps = _job()["steps"]
    assert len(steps) == 1
    assert "uses" not in steps[0]
    assert steps[0]["run"] == "true"


def test_workflow_uses_no_actions_secrets_or_tools() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "actions/checkout" not in text
    assert "uses:" not in text
    assert "secrets" not in text
    assert "github.token" not in text
    assert "GITHUB_TOKEN" not in text
    assert "GH_TOKEN" not in text
    for step in _job()["steps"]:
        assert not FORBIDDEN_COMMANDS.search(step.get("run", "")), step
        assert "env" not in step


def test_workflow_has_no_concurrency_cancellation() -> None:
    workflow = _load()
    assert "concurrency" not in workflow
    assert "concurrency" not in _job()
