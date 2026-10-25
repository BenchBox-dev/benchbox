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


PRODUCER_CRON = "17 1,5,9,13,17,21 * * *"
MONITOR_CRON = "47 7 * * *"


def test_produces_maps_on_a_schedule_and_on_demand_but_not_on_push() -> None:
    triggers = _triggers()
    assert "push" not in triggers
    assert "workflow_dispatch" in triggers
    assert "pull_request" not in triggers and "merge_group" not in triggers
    assert {"cron": PRODUCER_CRON} in triggers["schedule"]
    assert {"cron": MONITOR_CRON} in triggers["schedule"]
    assert len(triggers["schedule"]) == 2


def test_six_producer_runs_a_day_and_a_monitor_after_a_finished_producer() -> None:
    minute, hours, *_ = PRODUCER_CRON.split()
    produced = [int(hour) for hour in hours.split(",")]
    assert len(produced) == 6
    monitor_minute, monitor_hour, *_ = MONITOR_CRON.split()
    monitor = int(monitor_hour) * 60 + int(monitor_minute)
    started = [hour * 60 + int(minute) for hour in produced]
    previous = max(start for start in started if start < monitor)
    following = min(start for start in started if start > monitor)
    assert monitor - previous >= 90
    assert following - monitor >= 60


def test_scheduled_producers_and_monitor_do_not_cancel_each_other() -> None:
    concurrency = _workflow()["concurrency"]
    assert concurrency["cancel-in-progress"] is True
    assert concurrency["group"] == "trunk-coverage-${{ github.event_name }}-${{ github.event.schedule }}"


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
    assert job["if"] == "${{ always() && github.event.schedule != '47 7 * * *' }}"
    assert "--expected-shards 4" in "\n".join(step.get("run", "") for step in job["steps"])
    upload = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload["with"]["retention-days"] == 7


def test_map_job_self_fetches_and_fails_without_a_resolving_map() -> None:
    runs = "\n".join(step.get("run", "") for step in _workflow()["jobs"]["medium-coverage-map"]["steps"])
    assert "fetch-map" in runs
    assert "--base-sha" in runs
    assert "self-fetch failed" in runs


def test_map_building_jobs_run_on_scheduled_producer_events() -> None:
    jobs = _workflow()["jobs"]
    for name in ("medium-collect", "medium-test", "medium-coverage-map"):
        condition = jobs[name]["if"]
        assert "github.event.schedule != '47 7 * * *'" in condition, name
        assert "github.event_name" not in condition, name


def test_staleness_job_runs_only_on_the_monitor_schedule() -> None:
    job = _workflow()["jobs"]["coverage-map-staleness"]
    assert job["if"] == "${{ github.event.schedule == '47 7 * * *' }}"


def test_monitor_and_producer_conditions_are_mutually_exclusive_on_every_event() -> None:
    jobs = _workflow()["jobs"]
    monitor = "github.event.schedule == '47 7 * * *'"
    assert jobs["coverage-map-staleness"]["if"].count(monitor) == 1
    for name in ("medium-collect", "medium-test", "medium-coverage-map"):
        assert monitor.replace("==", "!=") in jobs[name]["if"]


def test_self_fetch_reports_the_producer_identity() -> None:
    runs = "\n".join(step.get("run", "") for step in _workflow()["jobs"]["medium-coverage-map"]["steps"])
    assert "payload['run_id']" in runs
    assert "payload['event']" in runs
    assert "payload['artifact']" in runs
