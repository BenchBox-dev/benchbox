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


def job(name: str = "refresh") -> dict[str, Any]:
    return load()["jobs"][name]


def all_run_text(name: str = "refresh") -> str:
    return "\n".join(step.get("run", "") for step in job(name)["steps"] if "run" in step)


def test_refresh_runs_weekly_and_on_demand() -> None:
    triggers = load()[True]
    assert "schedule" in triggers
    assert "workflow_dispatch" in triggers
    assert len(triggers["schedule"]) == 1
    assert re.fullmatch(r"\d+ \d+ \* \* \d", triggers["schedule"][0]["cron"])


@pytest.mark.parametrize("name", ["refresh", "medium"])
def test_refresh_holds_only_contents_and_pull_request_write(name: str) -> None:
    permissions = job(name)["permissions"]
    assert permissions["contents"] == "write"
    assert permissions["pull-requests"] == "write"
    for scope, access in permissions.items():
        if scope in ("contents", "pull-requests"):
            continue
        assert access != "write", scope


@pytest.mark.parametrize(
    ("name", "prefix"), [("refresh", "chore/fast-baseline-"), ("medium", "chore/medium-durations-")]
)
def test_refresh_never_pushes_to_develop_or_merges(name: str, prefix: str) -> None:
    text = all_run_text(name)
    assert "git push origin develop" not in text
    assert "gh pr merge" not in text
    assert "--base develop" in text
    assert prefix in text


@pytest.mark.parametrize("name", ["refresh", "medium"])
def test_refresh_opens_at_most_one_pr(name: str) -> None:
    text = all_run_text(name)
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


def test_refresh_job_no_longer_touches_the_medium_durations() -> None:
    text = all_run_text()
    assert "medium" not in text
    assert "tests/fixtures/medium_shard_durations.json" not in text


def test_refresh_only_commits_the_baseline() -> None:
    text = all_run_text()
    assert "git add tests/fixtures/fast_tier_baseline quality/docs-reference-inventory.json" in text


def test_medium_job_only_commits_the_medium_durations() -> None:
    text = all_run_text("medium")
    assert "git add tests/fixtures/medium_shard_durations.json quality/docs-reference-inventory.json" in text
    assert "fast_tier_baseline" not in text


def test_jobs_are_independent_so_a_medium_failure_cannot_stop_the_fast_pr() -> None:
    for name in ("refresh", "medium"):
        assert "needs" not in job(name)


def test_medium_job_scans_only_successful_develop_push_trunk_runs() -> None:
    text = all_run_text("medium")
    assert "gh run list --workflow trunk.yml --branch develop --event push --status success" in text
    assert '--limit "$MAX_RUNS_SCAN"' in text
    assert step_named("Fetch the five newest", "medium")["env"]["MAX_RUNS_SCAN"] == "30"


def test_medium_job_keeps_five_runs_with_every_unexpired_medium_artifact() -> None:
    text = all_run_text("medium")
    assert step_named("Fetch the five newest", "medium")["env"]["SOURCE_RUNS"] == "5"
    assert "select(.expired | not)" in text
    for shard in range(4):
        assert f"t2-medium-shard-{shard}-" in text
    assert "t2-medium-nodeids-" in text


def test_medium_job_filters_the_refresh_by_the_newest_collected_ids() -> None:
    text = all_run_text("medium")
    assert "scripts.build_fast_duration_baseline medium-refresh" in text
    assert "--collected fast-refresh/medium/collected.txt" in text
    assert "scripts.build_fast_duration_baseline medium-drift" in text
    assert "--projected-durations" in text


def test_medium_pr_body_reports_drift_and_the_before_after_ratio() -> None:
    text = all_run_text("medium")
    assert "cat fast-refresh/drift.md" in text
    assert "max_to_mean_shard_seconds" in text
    assert "projected_max_to_mean_shard_seconds" in text


def test_refresh_only_accepts_successful_source_runs() -> None:
    assert 'run.get("conclusion") == "success"' in all_run_text()


def step_index(name_fragment: str, name: str = "refresh") -> int:
    for index, step in enumerate(job(name)["steps"]):
        if name_fragment in step.get("name", ""):
            return index
    raise AssertionError(name_fragment)


def step_named(name_fragment: str, name: str = "refresh") -> dict[str, Any]:
    return job(name)["steps"][step_index(name_fragment, name)]


def test_medium_inventory_is_regenerated_after_the_rebuild_and_before_the_commit() -> None:
    regenerate = step_index("Regenerate the docs reference inventory", "medium")
    verify = step_index("Verify the docs reference inventory is current", "medium")
    assert step_index("Rebuild the medium-shard durations", "medium") < step_index("Check for medium", "medium")
    assert step_index("Check for medium", "medium") < regenerate < verify
    assert verify < step_index("Create medium durations PR", "medium")
    for fragment in ("Regenerate the docs reference inventory", "Verify the docs reference inventory is current"):
        assert step_named(fragment, "medium")["if"] == "steps.check.outputs.changed == 'true'"


def test_medium_pr_uses_the_existing_pr_token_so_runs_start_unapproved() -> None:
    expected = "${{ secrets.CROSS_SURFACE_BASELINE_PR_TOKEN || github.token }}"
    assert step_named("Checkout code", "medium")["with"]["token"] == expected
    assert step_named("Create medium durations PR", "medium")["env"]["GH_TOKEN"] == expected


def test_inventory_is_regenerated_after_the_shards_and_before_the_commit() -> None:
    regenerate = step_index("Regenerate the docs reference inventory")
    assert step_index("Build the refreshed baseline") < regenerate
    assert step_index("Check for baseline changes") < regenerate
    assert regenerate < step_index("Create refresh PR")
    assert step_named("Regenerate the docs reference inventory")["run"].strip() == (
        "uv run -- python scripts/doc_reference_inventory.py"
    )


def test_stale_inventory_fails_the_refresh_with_a_clear_message() -> None:
    verify = step_named("Verify the docs reference inventory is current")
    assert step_index("Regenerate the docs reference inventory") < step_index(
        "Verify the docs reference inventory is current"
    )
    assert step_index("Verify the docs reference inventory is current") < step_index("Create refresh PR")
    assert "scripts/doc_reference_inventory.py --check" in verify["run"]
    assert "::error::" in verify["run"]
    assert "exit 1" in verify["run"]


def test_inventory_steps_only_run_when_the_baseline_changed() -> None:
    for fragment in ("Regenerate the docs reference inventory", "Verify the docs reference inventory is current"):
        assert step_named(fragment)["if"] == "steps.check.outputs.changed == 'true'"


def test_refresh_pr_uses_the_existing_pr_token_so_runs_start_unapproved() -> None:
    expected = "${{ secrets.CROSS_SURFACE_BASELINE_PR_TOKEN || github.token }}"
    assert step_named("Checkout code")["with"]["token"] == expected
    assert step_named("Create refresh PR")["env"]["GH_TOKEN"] == expected
