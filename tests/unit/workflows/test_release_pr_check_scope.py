from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    return workflow.get("on", workflow.get(True))


def test_development_lanes_skip_release_pull_requests() -> None:
    pull_request = _triggers(_load("ci.yml"))["pull_request"]
    assert pull_request["branches-ignore"] == ["release"]
    assert "branches" not in pull_request


def test_review_event_job_skips_reviews_on_release_pull_requests() -> None:
    job = _load("oracle-review.yml")["jobs"]["review-event"]
    assert job["if"] == "github.event.pull_request.base.ref != 'release'"


@pytest.mark.parametrize(
    ("name", "job"), [("validate-release-pr.yml", "validate-base"), ("test.yml", "release-required-result")]
)
def test_release_pull_requests_keep_their_required_checks(name: str, job: str) -> None:
    workflow = _load(name)
    assert _triggers(workflow)["pull_request"]["branches"] == ["release"]
    assert job in workflow["jobs"]


def test_release_artifact_cleanup_queries_by_name_and_cannot_fail_the_release() -> None:
    job = _load("release.yml")["jobs"]["cleanup-artifacts"]
    assert job["continue-on-error"] is True
    script = job["steps"][0]["with"]["script"]
    assert "github.paginate(github.rest.actions.listArtifactsForRepo" in script
    assert "name," in script
    assert "artifacts.data.artifacts" not in script
