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
    allowed = {"contents", "pull-requests"} | ({"issues"} if name == "refresh" else set())
    for scope, access in permissions.items():
        if scope in allowed:
            continue
        assert access != "write", scope


def test_only_the_refresh_job_may_write_issues() -> None:
    assert job("refresh")["permissions"]["issues"] == "write"
    assert "issues" not in job("medium")["permissions"]
    assert "issues" not in load()["permissions"]


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


def test_medium_pr_supersedes_earlier_open_refresh_prs_after_opening_the_new_one() -> None:
    text = all_run_text("medium")
    assert text.index("gh pr create") < text.index("gh pr close")
    assert 'startswith(\\"chore/medium-durations-\\")' in text
    assert 'select(.headRefName != \\"$BRANCH\\")' in text
    assert "Superseded by #$NEW_NUMBER" in text
    assert text.count("gh pr close") == 1


@pytest.mark.parametrize(
    ("name", "create", "prefix"),
    [
        ("refresh", "Create refresh PR", "chore/fast-baseline-"),
        ("medium", "Create medium durations PR", "chore/medium-durations-"),
    ],
)
def test_a_run_that_found_an_earlier_open_refresh_fails_after_its_own_work(name: str, create: str, prefix: str) -> None:
    find_index = step_index("Find earlier refresh pull requests left open", name)
    fail_index = step_index("Fail when an earlier refresh was left unmerged", name)
    assert find_index < step_index(create, name) < fail_index == len(job(name)["steps"]) - 1

    find = job(name)["steps"][find_index]
    assert "if" not in find
    assert find["env"]["HEAD_PREFIX"] == prefix
    assert 'startswith(\\"$HEAD_PREFIX\\")' in find["run"]
    assert "gh pr close" not in find["run"] and "gh pr create" not in find["run"]
    assert 'echo "open=$OPEN" >> "$GITHUB_OUTPUT"' in find["run"]

    fail = job(name)["steps"][fail_index]
    assert fail["if"] == "steps.earlier.outputs.open != ''"
    assert fail["env"]["EARLIER"] == "${{ steps.earlier.outputs.open }}"
    assert "::error::" in fail["run"] and "$EARLIER" in fail["run"]
    assert fail["run"].rstrip().endswith("exit 1")


CEILING_PATH = "quality/fast-duration-ceiling.json"
OVER_TITLE = "Fast-tier duration is over its ceiling"


def test_refresh_pr_is_gated_on_the_ceiling_output() -> None:
    assert "steps.ceiling.outputs.over != 'true'" in step_named("Create refresh PR")["if"]
    ceiling = step_named("Check the refreshed baseline against the ceiling")
    assert ceiling["id"] == "ceiling"
    assert step_index("Gate-check the refreshed baseline") < step_index(
        "Check the refreshed baseline against the ceiling"
    )
    assert step_index("Check the refreshed baseline against the ceiling") < step_index("Create refresh PR")
    assert "ceiling-check" in ceiling["run"]
    assert "--previous fast-refresh/previous-baseline" in ceiling["run"]
    assert "over=true" in ceiling["run"]


def test_previous_baseline_is_copied_before_the_build_overwrites_it() -> None:
    build = step_named("Build the refreshed baseline")["run"]
    assert build.index("fast-refresh/previous-baseline") < build.index("scripts.build_fast_duration_baseline build")


def test_over_ceiling_opens_or_updates_one_issue_then_fails() -> None:
    step = step_named("Report the over-ceiling baseline")
    assert step["if"] == "steps.ceiling.outputs.over == 'true'"
    assert step["env"]["GH_TOKEN"] == "${{ secrets.CROSS_SURFACE_BASELINE_PR_TOKEN || github.token }}"
    text = step["run"]
    assert text.count("gh issue create") == 1
    assert "gh issue edit" in text
    assert f'select(.title == "{OVER_TITLE}")' in text
    assert OVER_TITLE in step["env"]["ISSUE_TITLE"]
    assert "::error::" in text
    assert "exit 1" in text
    assert step_index("Check the refreshed baseline against the ceiling") < step_index(
        "Report the over-ceiling baseline"
    )


def test_refresh_never_writes_the_ceiling_file() -> None:
    for name in ("refresh", "medium"):
        for line in all_run_text(name).splitlines():
            if CEILING_PATH in line:
                assert "--ceiling" in line or "::error::" in line, line
            if line.strip().startswith("git add"):
                assert CEILING_PATH not in line
    assert not re.search(r">>?\s*\S*fast-duration-ceiling", all_run_text())


def test_summary_is_written_and_linted_after_the_build_and_before_the_commit() -> None:
    step = step_named("Write and lint the refresh summary")
    assert step["if"] == "steps.check.outputs.changed == 'true'"
    assert step_index("Check for baseline changes") < step_index("Write and lint the refresh summary")
    assert step_index("Write and lint the refresh summary") < step_index("Create refresh PR")
    run = step["run"]
    assert "scripts.build_fast_duration_baseline summary" in run
    assert "--previous fast-refresh/previous-baseline" in run
    assert "--baseline tests/fixtures/fast_tier_baseline" in run
    assert "markdownlint-cli" in run
    assert "tests/fixtures/fast_tier_baseline/SUMMARY.md" in run
    assert "--fix" not in run


def test_refresh_commits_the_whole_baseline_directory_with_its_summary() -> None:
    assert (
        "git add tests/fixtures/fast_tier_baseline quality/docs-reference-inventory.json"
        in (step_named("Create refresh PR")["run"])
    )


def test_refresh_only_keeps_runs_that_are_the_final_head_of_a_merged_same_repository_pull_request() -> None:
    text = all_run_text()
    assert "gh run list --workflow ci.yml --event pull_request" in text
    assert "headSha" in text
    assert "--json state,headRefOid,isCrossRepository" in text
    assert '[ "$state" = "MERGED" ] || continue' in text
    assert '[ "$cross" = "false" ] || continue' in text
    assert '[ "$sha" = "$pr_head" ] || continue' in text
    assert '[ "$sha" = "$run_sha" ] || continue' in text


def test_refresh_mirrors_the_merge_time_and_head_repository_rules_of_the_reproduction() -> None:
    text = all_run_text()
    assert "createdAt" in text
    assert "mergedAt" in text
    assert '[[ "$run_created" > "$merged_at" ]] && continue' in text
    assert "-q .head_repository.full_name" in text
    assert '[ "${run_repo,,}" = "${GITHUB_REPOSITORY,,}" ] || continue' in text


def test_refresh_takes_its_run_bounds_from_the_script_and_fails_below_the_floor() -> None:
    fetch = step_named("Fetch recent merged-PR fast-junit artifacts")
    assert "MAX_SOURCE_RUNS" not in fetch["env"]
    run = fetch["run"]
    assert "from scripts.build_fast_duration_baseline import MAX_SOURCE_RUNS, MIN_SOURCE_RUNS" in run
    assert '[ "$kept" -lt "$MIN_SOURCE_RUNS" ]' in run
    assert "exit 1" in run.split('"$kept" -lt "$MIN_SOURCE_RUNS"')[1].split("\n          fi")[0].split("\nfi")[0]
