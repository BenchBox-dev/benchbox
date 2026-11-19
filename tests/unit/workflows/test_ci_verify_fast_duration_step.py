from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "ci.yml"
STEP_NAME = "verify-fast-duration"
DOWNLOAD_NAME = "Download fast-tier junit for duration verification"


def core() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["core"]


def named(name: str) -> dict[str, Any]:
    return next(item for item in core()["steps"] if item.get("name") == name)


def test_verify_step_blocks_core_on_regression() -> None:
    assert named(STEP_NAME).get("continue-on-error") is not True
    assert "code-tests-needed == 'true'" in named(STEP_NAME)["if"]
    assert named(DOWNLOAD_NAME)["continue-on-error"] is True


def test_verify_step_is_time_bounded_inside_the_core_timeout() -> None:
    assert named(STEP_NAME)["timeout-minutes"] < core()["timeout-minutes"]


def test_verify_step_reads_the_pr_artifact_and_writes_the_job_summary() -> None:
    run = named(STEP_NAME)["run"]
    assert "scripts.build_fast_duration_baseline verify" in run
    assert "--baseline tests/fixtures/fast_tier_baseline" in run
    assert "--run-dir fast-junit" in run
    assert '--summary "$GITHUB_STEP_SUMMARY"' in run
    download = named(DOWNLOAD_NAME)["with"]
    assert download["name"].startswith("fast-junit-${{ github.event.pull_request.number }}-")
    assert download["path"] == "fast-junit"


def test_verdict_artifact_is_bound_to_the_pr_head_and_uploads_on_failure() -> None:
    assert "--report-out fast-duration-verdict.json" in named(STEP_NAME)["run"]
    upload = named("Upload fast-duration verdict")
    assert upload["continue-on-error"] is True
    assert "always()" in upload["if"]
    assert upload["with"]["name"] == (
        "fast-duration-verdict-${{ github.event.pull_request.number }}-${{ github.event.pull_request.head.sha }}"
    )
    assert upload["with"]["path"] == "fast-duration-verdict.json"
    assert upload["with"]["overwrite"] is True


def test_a_verdict_is_uploaded_even_when_no_fast_tier_tests_ran() -> None:
    record = named("Record fast-duration verdict not applicable")
    assert record["continue-on-error"] is True
    assert "code-tests-needed == 'false'" in record["if"]
    assert '"verdict": "not-applicable"' in record["run"]
    assert "fast-duration-verdict.json" in record["run"]
    assert "code-tests-needed" not in named("Upload fast-duration verdict")["if"]
    names = [item.get("name") for item in core()["steps"]]
    assert names.index(STEP_NAME) < names.index(record["name"]) < names.index("Upload fast-duration verdict")


def test_verify_steps_run_after_the_aggregate_and_do_not_feed_it() -> None:
    names = [item.get("name") for item in core()["steps"]]
    assert names.index("Aggregate core result") < names.index(DOWNLOAD_NAME) < names.index(STEP_NAME)
    aggregate = named("Aggregate core result")
    assert "verify-fast-duration" not in aggregate["run"]
    assert "verify-fast-duration" not in str(core()["needs"])


def test_core_checkout_includes_the_verify_script_inputs() -> None:
    sparse = named("Checkout the result helper")["with"]["sparse-checkout"].split()
    for path in (
        "scripts/build_fast_duration_baseline.py",
        "tests/__init__.py",
        "tests/duration_policy.py",
        "tests/fixtures/fast_tier_baseline/",
    ):
        assert path in sparse


def test_the_attempt_that_measures_and_the_attempt_that_verifies_are_both_recorded() -> None:
    run = named(STEP_NAME)["run"]
    assert '--run-id "${{ github.run_id }}"' in run
    assert '--run-attempt "${{ github.run_attempt }}"' in run
    fast = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["code-test"]
    calibration = next(step for step in fast["steps"] if step.get("name") == "Record fast-tier runner calibration")
    assert '--run-attempt "${{ github.run_attempt }}"' in calibration["run"]


def workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_the_reproduce_job_always_runs_and_core_always_expects_it() -> None:
    jobs = workflow()["jobs"]
    job = jobs["fast-baseline-reproduce"]
    assert "fast-baseline-changed" not in job["if"]
    assert job["if"] == "${{ !cancelled() && !(github.event.action == 'edited' && !github.event.changes.base) }}"
    assert job["needs"] == "fast-baseline-collect"
    assert job["permissions"]["actions"] == "read"
    assert "fast-baseline-reproduce" in jobs["core"]["needs"]
    aggregate = named("Aggregate core result")["run"]
    assert "--expect fast-baseline-reproduce=true \\" in aggregate
    assert "continue-on-error" not in job
    assert all(step.get("continue-on-error") is not True for step in job["steps"])


def test_the_reproduce_job_detects_the_change_itself_before_any_other_step() -> None:
    steps = workflow()["jobs"]["fast-baseline-reproduce"]["steps"]
    assert [step["name"] for step in steps[:2]] == ["Checkout code", "Detect a baseline change"]
    detect = steps[1]
    assert detect["id"] == "detect"
    assert "diff=$(/usr/bin/git diff --name-only --no-renames" in detect["run"]
    assert 'if [ -n "$diff" ]' in detect["run"]
    assert "set -eu" in detect["run"]
    assert '"$BASE_SHA...$HEAD_SHA" -- tests/fixtures/fast_tier_baseline/' in detect["run"]
    assert detect["env"]["HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"
    assert steps[0]["with"]["fetch-depth"] == 0
    for step in steps[2:]:
        assert step["if"] == "steps.detect.outputs.changed == 'true'"
    reproduce = steps[-1]["run"]
    assert "[ ! -s collected-ids/collected-ids.txt ]" in reproduce
    assert reproduce.index("[ ! -s collected-ids") < reproduce.index("git show")


def test_the_reproduce_job_runs_the_base_branch_copy_of_the_script_against_the_head_fixture() -> None:
    jobs = workflow()["jobs"]
    steps = {step["name"]: step for step in jobs["fast-baseline-reproduce"]["steps"]}
    collect = {step["name"]: step for step in jobs["fast-baseline-collect"]["steps"]}["Collect the current fast tier"]
    assert "--collect-only" in collect["run"]
    assert jobs["fast-baseline-collect"]["if"] == "${{ needs.ci-paths.outputs.fast-baseline-changed == 'true' }}"
    assert "fast-baseline-collect" in jobs["fast-baseline-reproduce"]["needs"]
    assert "uv run" not in str(jobs["fast-baseline-reproduce"]["steps"])
    assert "pytest" not in str(jobs["fast-baseline-reproduce"]["steps"])
    assert "GH_TOKEN" not in str(jobs["fast-baseline-collect"])
    assert "actions" not in jobs["fast-baseline-collect"]["permissions"]
    reproduce = steps["Reproduce the committed baseline from its source artifacts"]
    run = reproduce["run"]
    assert 'git show "$BASE_SHA:$path"' in run
    for path in (
        "scripts/build_fast_duration_baseline.py",
        "scripts/release_canary_sharding.py",
        "tests/__init__.py",
        "tests/duration_policy.py",
    ):
        assert path in run
    assert "python -I -c" in run
    assert "build_fast_duration_baseline reproduce" not in run
    assert '"$trusted" reproduce' in run
    assert "--baseline-dir tests/fixtures/fast_tier_baseline" in run
    assert '--checked-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"' in run
    assert "--collected-ids collected-ids/collected-ids.txt" in run
    assert "::notice" in run and run.index("grep -q") < run.index("python -I")
    assert reproduce["env"]["BASE_SHA"] == "${{ github.event.pull_request.base.sha }}"
    assert reproduce["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert "landing-branch" not in str(workflow()["jobs"]["fast-baseline-reproduce"])


def test_the_baseline_flag_is_derived_from_the_changed_paths_whatever_the_inert_routing() -> None:
    paths = workflow()["jobs"]["ci-paths"]
    assert paths["outputs"]["fast-baseline-changed"] == "${{ steps.path-flags.outputs.fast-baseline-changed }}"
    flags = next(step for step in paths["steps"] if step.get("id") == "path-flags")
    assert "tests/fixtures/fast_tier_baseline/" in flags["run"]
    assert "fast_tier_baseline/' .ci-paths/trunk-lane-changed.txt" in flags["run"]
    assert "--no-renames" in flags["run"]
    assert flags["run"].index("--no-renames") < flags["run"].index("fast_tier_baseline/' .ci-paths/trunk")


def test_the_bootstrap_pass_needs_the_pr_to_add_reproduce_and_otherwise_fails() -> None:
    steps = {step["name"]: step for step in workflow()["jobs"]["fast-baseline-reproduce"]["steps"]}
    run = steps["Reproduce the committed baseline from its source artifacts"]["run"]
    missing = run.index("if ! grep -q '\"reproduce\"'")
    added = run.index('git show "$HEAD_SHA:scripts/build_fast_duration_baseline.py"', missing)
    notice = run.index("::notice", added)
    error = run.index("::error", notice)
    assert "grep -q '\"reproduce\"'" in run[added:notice]
    assert "exit 0" in run[notice:error]
    assert "exit 1" in run[error : run.index("python -I")]
    assert run.index("python -I") > error
