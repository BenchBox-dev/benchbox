from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from ci_unit_result import evaluate

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "ci.yml"
JOB = "reproduce-fast-baseline"
JOB_NAME = "Reproduce the fast-tier duration baseline"
CHANGED = "steps.changed.outputs.changed == 'true'"


def jobs() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def steps() -> list[dict[str, Any]]:
    return jobs()[JOB]["steps"]


def test_the_job_has_the_exact_attested_name() -> None:
    assert jobs()[JOB]["name"] == JOB_NAME


def test_the_job_runs_on_every_pull_request_regardless_of_changed_paths() -> None:
    job = jobs()[JOB]
    condition = job["if"]
    assert "!cancelled()" in condition
    assert "ci-paths" not in condition
    assert "paths" not in condition
    assert "needs" not in job


def test_the_job_is_a_no_op_unless_the_baseline_directory_changed() -> None:
    detect, *rest = steps()
    assert detect["id"] == "changed"
    assert "if" not in detect
    assert "tests/fixtures/fast_tier_baseline" in json.dumps(jobs()[JOB]["env"])
    assert "pulls/$PR_NUMBER/files" in detect["run"]
    assert "previous_filename" in detect["run"]
    assert "-ge 3000" in detect["run"]
    assert rest
    for step in rest:
        assert step["if"] == CHANGED, step["name"]


def test_tooling_needs_the_job_and_requires_its_success() -> None:
    tooling = jobs()["tooling"]
    assert JOB in tooling["needs"]
    aggregate = next(step for step in tooling["steps"] if step.get("id") == "aggregate")
    assert "--always reproduce-fast-baseline" in aggregate["run"]
    assert "--expect reproduce-fast-baseline" not in aggregate["run"]


def test_a_skipped_or_failed_job_fails_the_tooling_aggregate() -> None:
    def tooling_problems(result: str) -> list[str]:
        problems, _cancelled = evaluate({JOB: {"result": result}}, {}, [JOB])
        return problems

    assert tooling_problems("success") == []
    assert tooling_problems("skipped")
    assert tooling_problems("failure")


def test_the_job_reads_only_and_keeps_credentials_away_from_pull_request_code() -> None:
    job = jobs()[JOB]
    assert job["permissions"] == {"actions": "read", "contents": "read", "pull-requests": "read"}
    assert "GH_TOKEN" not in job["env"]
    for step in steps():
        if step.get("uses", "").startswith("actions/checkout"):
            assert step["with"]["persist-credentials"] is False
        if step.get("working-directory") == "head":
            assert "GH_TOKEN" not in step.get("env", {})


def test_the_reproduction_runs_the_script_from_the_base_commit_against_the_base_baseline() -> None:
    checkouts = {step["with"]["path"]: step["with"]["ref"] for step in steps() if "path" in step.get("with", {})}
    assert checkouts["base"] == "${{ github.event.pull_request.base.sha }}"
    assert checkouts["head"] == "${{ github.event.pull_request.head.sha }}"
    reproduce = next(step for step in steps() if step["name"] == "Reproduce the baseline from its source runs")
    assert reproduce["working-directory"] == "base"
    run = reproduce["run"]
    assert "scripts.build_fast_duration_baseline reproduce" in run
    assert '--baseline "../head/$BASELINE_DIR"' in run
    assert '--previous "$BASELINE_DIR"' in run
    assert "--collected-ids" in run
    assert "--ceiling quality/fast-duration-ceiling.json" in run


def test_the_artifacts_are_downloaded_by_id_into_the_refresh_layout() -> None:
    download = next(step for step in steps() if step["name"].startswith("Download the source run artifacts"))
    assert "actions/artifacts/$artifact_id/zip" in download["run"]
    assert "base/fast-refresh/runs/run-$run_id" in download["run"]


def test_collection_uses_the_same_fast_tier_selection_as_the_refresh_workflow() -> None:
    collect = next(step for step in steps() if step["name"] == "Collect the current fast tier")
    assert collect["working-directory"] == "head"
    assert '-m "fast and not (slow or stress or resource_heavy or live_integration)" tests/' in collect["run"]
