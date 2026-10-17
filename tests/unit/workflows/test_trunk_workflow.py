from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.medium]
ROOT = Path(__file__).resolve().parents[3]


def _workflow() -> dict:
    return yaml.safe_load((ROOT / ".github/workflows/trunk.yml").read_text(encoding="utf-8"))


def _triggers() -> dict:
    return _workflow()[True]


def test_runs_after_each_push_to_develop_and_on_demand() -> None:
    triggers = _triggers()
    assert triggers["push"] == {"branches": ["develop"]}
    assert "workflow_dispatch" in triggers
    assert "pull_request" not in triggers and "merge_group" not in triggers


def test_every_push_gets_its_own_queued_run() -> None:
    concurrency = _workflow()["concurrency"]
    assert "github.ref" in concurrency["group"], "a manual run on another ref would replace the pending run for develop"
    assert concurrency["cancel-in-progress"] is False
    assert concurrency["queue"] == "max", (
        "without a queue a newer push replaces the pending run, so intermediate commits get no result"
    )


def test_is_read_only() -> None:
    assert _workflow()["permissions"] == {"contents": "read"}


def test_shard_evidence_is_labelled_with_this_workflow() -> None:
    commands = [
        step["run"]
        for job in _workflow()["jobs"].values()
        for step in job["steps"]
        if "release_canary_sharding.py" in step.get("run", "")
    ]
    assert commands
    for command in commands:
        assert "--workflow trunk.yml" in command
        assert "--workflow ci.yml" not in command


def test_required_local_cases_run_after_each_merge() -> None:
    job = _workflow()["jobs"]["required-local-cases"]
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] == 20
    assert "needs" not in job and "if" not in job and "strategy" not in job
    runs = [step["run"] for step in job["steps"] if "run" in step]
    assert runs.count("make test-required-local-cases") == 1
    assert not job.get("continue-on-error")
    assert not any(step.get("continue-on-error") for step in job["steps"])


def test_builds_the_release_distribution_on_each_push_to_develop() -> None:
    job = _workflow()["jobs"]["dist-artifact"]
    assert job["name"] == "dist-artifact"
    assert job["if"] == "${{ github.event_name == 'push' }}"
    assert "needs" not in job
    upload = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload["with"]["name"] == "dist-${{ github.sha }}-attempt-${{ github.run_attempt }}"
    assert upload["with"]["retention-days"] == 90
    assert upload["with"]["if-no-files-found"] == "error"


def test_distribution_job_alone_reads_actions_and_holds_no_write_permission() -> None:
    workflow = _workflow()
    assert workflow["jobs"]["dist-artifact"]["permissions"] == {"contents": "read", "actions": "read"}
    for name, job in workflow["jobs"].items():
        if name != "dist-artifact":
            assert "permissions" not in job
    granted = [permission for job in workflow["jobs"].values() for permission in job.get("permissions", {}).values()]
    assert set(granted) == {"read"}


def test_distribution_job_verifies_exact_distributions_before_binding_and_upload() -> None:
    steps = _workflow()["jobs"]["dist-artifact"]["steps"]
    runs = [step.get("run", "") for step in steps]
    source = next(index for index, run in enumerate(runs) if "bundled_binary_manifest.py" in run)
    build = next(index for index, run in enumerate(runs) if "uv build" in run)
    bind = next(index for index, run in enumerate(runs) if "release_artifact_consumer.py producer --dist dist" in run)
    upload = next(
        index for index, step in enumerate(steps) if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert source < build < bind < upload
    assert "scripts/verify_distribution_binaries.py dist/*.whl dist/*.tar.gz" in runs[build]
    assert runs[build].index("verify_distribution_binaries.py") < runs[build].index("sha256sum")
    assert "sha256sum -- *.whl *.tar.gz > SHA256SUMS" in runs[build]
    assert runs[bind].startswith("python -I -S ")
    assert steps[bind]["env"] == {"GH_TOKEN": "${{ github.token }}"}
    assert [index for index, step in enumerate(steps) if "GH_TOKEN" in str(step.get("env", {}))] == [bind]
    path = steps[upload]["with"]["path"]
    for entry in ("dist/producer-receipt.json", "dist/SHA256SUMS", "dist/*.whl", "dist/*.tar.gz"):
        assert entry in path


def test_tpcds_repeat_and_draw_checks_run_after_each_merge() -> None:
    commands = [step.get("run", "") for step in _workflow()["jobs"]["tpcds-parity-draws"]["steps"]]
    assert "make tpcds-cross-surface-draws-report" in commands
