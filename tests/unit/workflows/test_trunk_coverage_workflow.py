from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.medium]
ROOT = Path(__file__).resolve().parents[3]


def _workflow() -> dict:
    return yaml.safe_load((ROOT / ".github/workflows/trunk-coverage.yml").read_text(encoding="utf-8"))


def _triggers() -> dict:
    return _workflow()[True]


def _shard_job() -> dict:
    return _workflow()["jobs"]["medium-test"]


def test_runs_on_develop_push_and_on_demand_with_a_daily_staleness_tick() -> None:
    triggers = _triggers()
    assert triggers["push"] == {"branches": ["develop"]}
    assert "workflow_dispatch" in triggers
    assert "pull_request" not in triggers and "merge_group" not in triggers
    assert {"cron": "17 7 * * *"} in triggers["schedule"]


def test_coverage_runs_cancel_each_other() -> None:
    concurrency = _workflow()["concurrency"]
    assert concurrency["cancel-in-progress"] is True
    assert concurrency["group"] == "trunk-coverage-${{ github.event_name }}"


def test_is_read_only_by_default() -> None:
    assert _workflow()["permissions"] == {"contents": "read", "actions": "read"}


def test_only_the_staleness_job_may_touch_issues() -> None:
    jobs = _workflow()["jobs"]
    assert jobs["coverage-map-staleness"]["permissions"] == {"contents": "read", "actions": "read", "issues": "write"}
    for name, job in jobs.items():
        if name != "coverage-map-staleness":
            assert "issues" not in job.get("permissions", {}), name


def test_four_coverage_shards_use_ctrace_and_test_phase_contexts() -> None:
    job = _shard_job()
    assert job["strategy"]["matrix"]["shard_index"] == [0, 1, 2, 3]
    runs = "\n".join(step.get("run", "") for step in job["steps"])
    envs = [step.get("env", {}) for step in job["steps"] if isinstance(step.get("env"), dict)]
    assert any(env.get("COVERAGE_CORE") == "ctrace" for env in envs)
    assert "--cov-context=test" in runs
    assert "scripts/coverage_map.ini" in runs
    assert "--junitxml" in runs
    assert "-o junit_family=xunit1" in runs


def test_map_job_requires_all_four_shards_and_declares_it() -> None:
    job = _workflow()["jobs"]["medium-coverage-map"]
    assert job["if"] == "${{ always() && github.event_name != 'schedule' }}"
    assert "--expected-shards 4" in "\n".join(step.get("run", "") for step in job["steps"])
    upload = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload["with"]["retention-days"] == 7


def test_map_job_self_fetches_and_fails_without_a_resolving_map() -> None:
    runs = "\n".join(step.get("run", "") for step in _workflow()["jobs"]["medium-coverage-map"]["steps"])
    assert "fetch-map" in runs
    assert "--base-sha" in runs
    assert "self-fetch failed" in runs


def test_staleness_job_runs_only_on_the_schedule() -> None:
    job = _workflow()["jobs"]["coverage-map-staleness"]
    assert job["if"] == "${{ github.event_name == 'schedule' }}"
