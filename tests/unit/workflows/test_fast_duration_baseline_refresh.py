from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "fast-duration-baseline-refresh.yml"


def load() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def job() -> dict[str, Any]:
    return load()["jobs"]["refresh"]


def all_run_text() -> str:
    return "\n".join(step.get("run", "") for step in job()["steps"] if "run" in step)


def test_refresh_runs_weekly_and_on_demand() -> None:
    triggers = load()[True]
    assert "schedule" in triggers
    assert "workflow_dispatch" in triggers
    assert len(triggers["schedule"]) == 1
    assert re.fullmatch(r"\d+ \d+ \* \* \d", triggers["schedule"][0]["cron"])


def test_refresh_holds_only_contents_and_pull_request_write() -> None:
    permissions = job()["permissions"]
    assert permissions["contents"] == "write"
    assert permissions["pull-requests"] == "write"
    for scope, access in permissions.items():
        if scope in ("contents", "pull-requests"):
            continue
        assert access != "write", scope


def test_refresh_never_pushes_to_develop_or_merges() -> None:
    text = all_run_text()
    assert "git push origin develop" not in text
    assert "gh pr merge" not in text
    assert "--base develop" in text
    assert "chore/fast-baseline-" in text


def test_refresh_opens_at_most_one_pr() -> None:
    text = all_run_text()
    assert len(re.findall(r"gh pr create", text)) == 1
    assert 'gh pr list --head "$BRANCH"' in text


def test_refresh_bounds_and_logs_api_calls() -> None:
    text = all_run_text()
    assert "API_CALL_BUDGET" in text
    assert "MAX_SOURCE_RUNS" in text
    assert "MAX_RUNS_SCAN" in text
    assert "api-calls.log" in text


def test_refresh_rebuilds_and_gate_checks_the_baseline() -> None:
    text = all_run_text()
    assert "scripts.build_fast_duration_baseline build" in text
    assert "scripts.build_fast_duration_baseline check" in text
    assert "tests/fixtures/fast_tier_baseline" in text
    assert "--collected-ids" in text


def test_refresh_reports_medium_shard_drift() -> None:
    text = all_run_text()
    assert "scripts.build_fast_duration_baseline medium-drift" in text
    assert "tests/fixtures/medium_shard_durations.json" in text
    assert "t2-medium-shard-" in text


def test_refresh_only_commits_the_baseline() -> None:
    text = all_run_text()
    assert "git add tests/fixtures/fast_tier_baseline" in text


def test_refresh_only_accepts_successful_source_runs() -> None:
    assert 'run.get("conclusion") == "success"' in all_run_text()
