from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from ci_unit_result import evaluate

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
DETECT = "fast-baseline-changes"
COLLECT = "collect-fast-tier-ids"
REPRODUCE = "reproduce-fast-baseline"
REPRODUCE_NAME = "Reproduce the fast-tier duration baseline"
GATED_JOBS = (DETECT, COLLECT, REPRODUCE)
CHANGED = "needs.fast-baseline-changes.outputs.changed == 'true'"


def jobs() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def steps(job: str) -> list[dict[str, Any]]:
    return jobs()[job]["steps"]


def step_named(job: str, fragment: str) -> dict[str, Any]:
    return next(step for step in steps(job) if fragment in step["name"])


def test_the_reproduce_job_has_the_exact_attested_name() -> None:
    assert jobs()[REPRODUCE]["name"] == REPRODUCE_NAME


@pytest.mark.parametrize("name", GATED_JOBS)
def test_each_job_runs_on_every_pull_request_regardless_of_changed_paths(name: str) -> None:
    condition = jobs()[name]["if"]
    assert "!cancelled()" in condition
    assert "ci-paths" not in condition
    assert "paths" not in condition
    assert "needs" not in jobs()[name] or "ci-paths" not in jobs()[name]["needs"]


def test_change_detection_fails_closed_on_a_truncated_file_listing() -> None:
    detect = steps(DETECT)[0]
    assert detect["id"] == "changed"
    run = detect["run"]
    assert "pulls/$PR_NUMBER/files" in run
    assert "previous_filename" in run
    assert '"$total" -ge 3000' in run
    assert "exit 1" in run.split("-ge 3000")[1].split("\nfi")[0]
    assert "tests/fixtures/fast_tier_baseline/" in run
    assert jobs()[DETECT]["outputs"]["changed"] == "${{ steps.changed.outputs.changed }}"


@pytest.mark.parametrize("name", [COLLECT, REPRODUCE])
def test_the_expensive_steps_are_no_ops_unless_the_baseline_changed(name: str) -> None:
    gated = [step for step in steps(name) if step.get("if") == CHANGED]
    ungated = [step for step in steps(name) if step.get("if") != CHANGED]
    assert gated
    for step in ungated:
        assert step["name"] == "Require the upstream jobs"


def test_tooling_needs_every_job_and_requires_each_one_to_succeed() -> None:
    tooling = jobs()["tooling"]
    aggregate = next(step for step in tooling["steps"] if step.get("id") == "aggregate")
    for name in GATED_JOBS:
        assert name in tooling["needs"]
        assert f"--always {name}" in aggregate["run"]
        assert f"--expect {name}" not in aggregate["run"]


def test_the_attesting_jobs_and_the_aggregate_cannot_fail_open() -> None:
    tooling = jobs()["tooling"]
    assert "continue-on-error" not in tooling
    for step in tooling["steps"]:
        assert "continue-on-error" not in step, step.get("name")
    for name in GATED_JOBS:
        assert "continue-on-error" not in jobs()[name], name
        for step in steps(name):
            assert "continue-on-error" not in step, step.get("name")
            assert "|| true" not in step.get("run", ""), step.get("name")


def test_a_skipped_or_failed_job_fails_the_tooling_aggregate() -> None:
    for name in GATED_JOBS:

        def tooling_problems(result: str, job: str = name) -> list[str]:
            problems, _cancelled = evaluate({job: {"result": result}}, {}, [job])
            return problems

        assert tooling_problems("success") == []
        assert tooling_problems("skipped")
        assert tooling_problems("failure")


def test_the_reproduce_job_fails_when_an_upstream_job_did_not_succeed() -> None:
    guard = steps(REPRODUCE)[0]
    assert guard["name"] == "Require the upstream jobs"
    assert "needs.fast-baseline-changes.result != 'success'" in guard["if"]
    assert "needs.collect-fast-tier-ids.result != 'success'" in guard["if"]
    assert "needs.fast-baseline-changes.outputs.changed != 'true'" in guard["if"]
    assert "needs.fast-baseline-changes.outputs.changed != 'false'" in guard["if"]
    assert "exit 1" in guard["run"]


def test_the_collection_job_runs_pull_request_code_without_any_token() -> None:
    job = jobs()[COLLECT]
    assert job["permissions"] == {"contents": "read"}
    assert "env" not in job
    for step in job["steps"]:
        assert "GH_TOKEN" not in step.get("env", {}) and "GITHUB_TOKEN" not in step.get("env", {})
        assert "secrets" not in str(step)
        if step.get("uses", "").startswith("actions/checkout"):
            assert step["with"]["ref"] == "${{ github.event.pull_request.head.sha }}"
            assert step["with"]["persist-credentials"] is False
    collect = step_named(COLLECT, "Collect the current fast tier")
    assert '-m "fast and not (slow or stress or resource_heavy or live_integration)" tests/' in collect["run"]
    upload = step_named(COLLECT, "Upload the collected ids")
    assert upload["with"]["name"] == "fast-baseline-collected-ids"


def test_the_reproduce_job_never_runs_pull_request_code() -> None:
    job = jobs()[REPRODUCE]
    assert job["permissions"] == {"actions": "read", "contents": "read", "pull-requests": "read"}
    for step in job["steps"]:
        assert "uv " not in step.get("run", "") and "pytest" not in step.get("run", "")
        assert "setup-uv" not in step.get("uses", "")
        if step.get("uses", "").startswith("actions/checkout"):
            assert step["with"]["persist-credentials"] is False
    head = step_named(REPRODUCE, "Checkout the pull request head")
    assert head["with"]["path"] == "head"
    assert head["with"]["sparse-checkout"].strip() == "tests/fixtures/fast_tier_baseline/"
    base = step_named(REPRODUCE, "Checkout the base commit")
    assert base["with"]["ref"] == "${{ github.event.pull_request.base.sha }}"
    assert "scripts/build_fast_duration_baseline.py" in base["with"]["sparse-checkout"]


def test_the_reproduction_runs_the_base_script_and_downloads_artifacts_itself() -> None:
    reproduce = step_named(REPRODUCE, "Reproduce the baseline from its source runs")
    assert reproduce["working-directory"] == "base"
    run = reproduce["run"]
    assert "scripts.build_fast_duration_baseline reproduce" in run
    assert '--baseline "../head/$BASELINE_DIR"' in run
    assert '--previous "$BASELINE_DIR"' in run
    assert '--workdir "$RUNNER_TEMP/reproduce"' in run
    assert "--collected-ids" in run
    assert "--ceiling quality/fast-duration-ceiling.json" in run
    assert '--not-after "$EVENT_UPDATED_AT"' in run
    assert reproduce["env"]["EVENT_UPDATED_AT"] == "${{ github.event.pull_request.updated_at }}"
    text = "\n".join(step.get("run", "") for step in steps(REPRODUCE))
    assert "unzip" not in text
    assert "python3 -" not in text.replace("python3 -m", "")
    assert "actions/artifacts" not in text


def test_the_fast_junit_sources_are_kept_as_long_as_the_generated_at_window() -> None:
    uploads = [
        step
        for job in jobs().values()
        for step in job.get("steps", [])
        if str(step.get("with", {}).get("name", "")).startswith("fast-junit-")
        and "upload-artifact" in step.get("uses", "")
    ]
    assert uploads
    for step in uploads:
        assert step["with"]["retention-days"] == 30


def test_the_files_the_fast_job_writes_into_fast_junit_are_the_allowed_artifact_members() -> None:
    from scripts.build_fast_duration_baseline import ARTIFACT_MEMBERS

    producers = [
        job
        for job in jobs().values()
        if any(
            str(step.get("with", {}).get("name", "")).startswith("fast-junit-")
            and "upload-artifact" in step.get("uses", "")
            for step in job.get("steps", [])
        )
    ]
    assert len(producers) == 1
    written = {
        name
        for step in producers[0]["steps"]
        for name in re.findall(r"fast-junit/([A-Za-z0-9_.-]+)", step.get("run", ""))
    }
    assert written == set(ARTIFACT_MEMBERS)


EARLY_EXIT_CONSUMER = re.compile(r"\|\s*(grep\b[^|\n]*(\s-[a-zA-Z]*[qm]|--quiet|--max-count)|head\b|sed\s+-n\s+\S*q)")


def test_the_new_jobs_have_no_early_exit_consumer_under_pipefail() -> None:
    refresh = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "fast-duration-baseline-refresh.yml").read_text(encoding="utf-8")
    )["jobs"]["refresh"]["steps"]
    scripts = [(job, step["name"], step.get("run", "")) for job in GATED_JOBS for step in steps(job)]
    scripts += [("refresh", step["name"], step.get("run", "")) for step in refresh]
    for job, name, run in scripts:
        if "pipefail" in run:
            assert not EARLY_EXIT_CONSUMER.search(run), f"{job}: {name}"


EXPECTED_HEAD = "a" * 40
OTHER_HEAD = "b" * 40


def _run_detection(
    tmp_path: Path,
    listing: str,
    gh_exit: int = 0,
    *,
    head_changes_at: int = 99,
    head_lookup_fails: bool = False,
) -> tuple[int, str]:
    if sys.platform == "win32" or shutil.which("bash") is None:
        pytest.skip("needs POSIX bash")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    data = tmp_path / "listing.tsv"
    data.write_text(listing, encoding="utf-8")
    gh = bin_dir / "gh"
    counter = tmp_path / "head-lookups"
    head_lookup = "exit 1" if head_lookup_fails else f'echo "$n" > "{counter}"'
    gh.write_text(
        "#!/bin/sh\n"
        f"if [ {gh_exit} -ne 0 ]; then exit {gh_exit}; fi\n"
        'case "$*" in\n'
        "  *--jq*)\n"
        f'    n=$(( $(cat "{counter}" 2>/dev/null || echo 0) + 1 ))\n'
        f"    {head_lookup}\n"
        f'    if [ "$n" -ge {head_changes_at} ]; then echo {OTHER_HEAD}; else echo {EXPECTED_HEAD}; fi\n'
        "    exit 0;;\n"
        "esac\n"
        f'cat "{data}"\n',
        encoding="utf-8",
    )
    gh.chmod(0o755)
    output = tmp_path / "github-output"
    output.write_text("", encoding="utf-8")
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_OUTPUT": str(output),
        "PR_NUMBER": "1",
        "REPO": "o/r",
    }
    env["EXPECTED_HEAD"] = EXPECTED_HEAD
    script = tmp_path / "detect.sh"
    script.write_text(steps(DETECT)[0]["run"], encoding="utf-8")
    result = subprocess.run(
        ["bash", "detect.sh"], cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", check=False
    )
    return result.returncode, output.read_text(encoding="utf-8")


BASELINE_ROW = "tests/fixtures/fast_tier_baseline/manifest.json\t\n"


def listing_after(first: str, rows: int) -> str:
    filler = "".join(f"pkg/section-{index:04d}/{'x' * 60}.md\t\n" for index in range(rows))
    return first + filler


def test_detection_sees_the_baseline_path_first_in_a_listing_larger_than_a_pipe_buffer(tmp_path: Path) -> None:
    listing = listing_after(BASELINE_ROW, 2500)
    assert len(listing) > 128 * 1024
    code, output = _run_detection(tmp_path, listing)
    assert code == 0
    assert output == "changed=true\n"


def test_detection_sees_a_baseline_path_in_the_rename_column(tmp_path: Path) -> None:
    code, output = _run_detection(tmp_path, listing_after("pkg/new.md\ttests/fixtures/fast_tier_baseline/x.json\n", 5))
    assert (code, output) == (0, "changed=true\n")


def test_detection_reports_no_change_when_the_baseline_is_absent(tmp_path: Path) -> None:
    code, output = _run_detection(tmp_path, listing_after("", 2500))
    assert (code, output) == (0, "changed=false\n")


def test_detection_fails_the_step_instead_of_reporting_no_change(tmp_path: Path) -> None:
    code, output = _run_detection(tmp_path, listing_after(BASELINE_ROW, 3000))
    assert code != 0
    assert output == ""
    code, output = _run_detection(tmp_path / "gh-failure", BASELINE_ROW, gh_exit=1)
    assert code != 0
    assert output == ""


@pytest.mark.parametrize("changes_at", [1, 2], ids=["before-listing", "after-listing"])
def test_detection_fails_when_the_pull_request_head_differs_from_the_run(tmp_path: Path, changes_at: int) -> None:
    code, output = _run_detection(tmp_path, listing_after("", 5), head_changes_at=changes_at)
    assert code != 0
    assert output == ""


def test_detection_fails_when_the_head_lookup_fails(tmp_path: Path) -> None:
    code, output = _run_detection(tmp_path, listing_after("", 5), head_lookup_fails=True)
    assert code != 0
    assert output == ""
