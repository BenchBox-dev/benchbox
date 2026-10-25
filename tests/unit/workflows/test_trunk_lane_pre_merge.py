from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github" / "workflows"

SHARED_PATHS = (
    "scripts/release_canary_sharding.py",
    "scripts/pytest_shard_evidence.py",
    "tests/fixtures/medium_shard_durations.json",
)
PLAIN_PATHS = (".github/workflows/trunk.yml", *SHARED_PATHS)
COVERAGE_PATHS = (
    ".github/workflows/trunk-coverage.yml",
    "scripts/pytest_coverage_timeout.py",
    "scripts/coverage_map.ini",
    *SHARED_PATHS,
)

LANES = {
    "trunk-lane-plain": ("trunk.yml", "trunk-lane-plain-needed", "trunk-lane-plain-", PLAIN_PATHS),
    "trunk-lane-coverage": ("trunk-coverage.yml", "trunk-lane-coverage-needed", "trunk-lane-coverage-", COVERAGE_PATHS),
}
MEDIUM_JOBS = ("medium-collect", "medium-test")
INERT_PATHS = (
    "benchbox/base.py",
    ".github/workflows/ci.yml",
    ".github/workflows/test.yml",
    "scripts/release_canary_sharding_extra.py",
    "tests/fixtures/medium_shard_durations.json.bak",
    "docs/internal/merge-queue-governance.md",
)


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _flag_command() -> str:
    steps = _load("ci.yml")["jobs"]["ci-paths"]["steps"]
    (step,) = [step for step in steps if step.get("id") == "path-flags"]
    return step["run"]


def _flag_pattern(variable: str) -> re.Pattern[str]:
    match = re.search(rf"grep -qxE '([^']+)' \.ci-paths/trunk-lane-changed\.txt; then {variable}=true", _flag_command())
    assert match, f"no path pattern sets {variable}"
    return re.compile(match.group(1))


def _flag_variable(output: str) -> str:
    match = re.search(rf'echo "{output}=\$(\w+)" >> "\$GITHUB_OUTPUT"', _flag_command())
    assert match, f"{output} is not exported"
    return match.group(1)


@pytest.mark.parametrize("lane", LANES)
def test_the_trigger_paths_are_exactly_the_lane_inputs(lane: str) -> None:
    _, output, _, paths = LANES[lane]
    pattern = _flag_pattern(_flag_variable(output))
    for path in paths:
        assert pattern.fullmatch(path), path
    for path in INERT_PATHS:
        assert not pattern.fullmatch(path), path


def test_the_changed_path_list_names_the_source_of_a_rename() -> None:
    command = _flag_command()
    assert 'git diff --name-only --no-renames --diff-filter=ACDMRT "$EVENT_BASE_SHA...HEAD"' in command
    steps = _load("ci.yml")["jobs"]["ci-paths"]["steps"]
    (step,) = [step for step in steps if step.get("id") == "path-flags"]
    assert step["env"]["EVENT_BASE_SHA"] == "${{ github.event.pull_request.base.sha }}"


def test_each_workflow_change_triggers_only_its_own_lane_and_shared_inputs_trigger_both() -> None:
    plain = _flag_pattern(_flag_variable("trunk-lane-plain-needed"))
    coverage = _flag_pattern(_flag_variable("trunk-lane-coverage-needed"))
    assert plain.fullmatch(".github/workflows/trunk.yml") and not coverage.fullmatch(".github/workflows/trunk.yml")
    assert coverage.fullmatch(".github/workflows/trunk-coverage.yml")
    assert not plain.fullmatch(".github/workflows/trunk-coverage.yml")
    for path in ("scripts/pytest_coverage_timeout.py", "scripts/coverage_map.ini"):
        assert coverage.fullmatch(path) and not plain.fullmatch(path)
    for path in SHARED_PATHS:
        assert plain.fullmatch(path) and coverage.fullmatch(path)


@pytest.mark.parametrize("lane", LANES)
def test_the_path_flag_is_a_job_output_read_by_the_lane(lane: str) -> None:
    _, output, _, _ = LANES[lane]
    outputs = _load("ci.yml")["jobs"]["ci-paths"]["outputs"]
    assert outputs[output] == f"${{{{ steps.path-flags.outputs.{output} }}}}"
    assert _load("ci.yml")["jobs"][lane]["if"] == f"${{{{ needs.ci-paths.outputs.{output} == 'true' }}}}"


@pytest.mark.parametrize("lane", LANES)
def test_the_lane_calls_the_real_trunk_workflow_in_medium_only_mode(lane: str) -> None:
    filename, _, prefix, _ = LANES[lane]
    job = _load("ci.yml")["jobs"][lane]
    assert job["uses"] == f"./.github/workflows/{filename}"
    assert job["needs"] == "ci-paths"
    assert job["with"] == {"medium-only": True, "artifact-prefix": prefix}
    assert "steps" not in job and "runs-on" not in job and "secrets" not in job
    assert not job.get("continue-on-error")


def test_the_lane_permissions_cover_what_the_called_workflows_request() -> None:
    jobs = _load("ci.yml")["jobs"]
    assert jobs["trunk-lane-plain"]["permissions"] == {"contents": "read", "actions": "read"}
    assert jobs["trunk-lane-coverage"]["permissions"] == {"contents": "read", "actions": "read", "issues": "write"}
    for lane, (filename, _, _, _) in LANES.items():
        called = _load(filename)
        granted = jobs[lane]["permissions"]
        requested = [called.get("permissions") or {}]
        requested += [job.get("permissions") or {} for job in called["jobs"].values()]
        for permissions in requested:
            for scope, level in permissions.items():
                assert granted.get(scope) == level, f"{lane} must grant {scope}: {level}"


def test_core_waits_for_both_lanes_and_expects_them_exactly_when_their_paths_changed() -> None:
    core = _load("ci.yml")["jobs"]["core"]
    assert {"trunk-lane-plain", "trunk-lane-coverage"} <= set(core["needs"])
    aggregate = next(step for step in core["steps"] if step.get("id") == "aggregate")["run"]
    for lane, (_, output, _, _) in LANES.items():
        assert f"--expect {lane}=${{{{ needs.ci-paths.outputs.{output} == 'true' }}}}" in aggregate


@pytest.mark.parametrize("filename", ["trunk.yml", "trunk-coverage.yml"])
def test_the_trunk_workflow_accepts_the_medium_only_call(filename: str) -> None:
    workflow = _load(filename)
    triggers = workflow[True]
    if filename == "trunk.yml":
        assert triggers["push"] == {"branches": ["develop"]}
    else:
        assert "push" not in triggers
    assert "workflow_dispatch" in triggers
    assert triggers["workflow_call"]["inputs"] == {
        "medium-only": {"type": "boolean", "default": False},
        "artifact-prefix": {"type": "string", "default": ""},
    }


@pytest.mark.parametrize("filename", ["trunk.yml", "trunk-coverage.yml"])
def test_medium_only_runs_only_the_medium_jobs_and_skips_everything_else(filename: str) -> None:
    jobs = _load(filename)["jobs"]
    for name in MEDIUM_JOBS:
        assert "medium-only" not in str(jobs[name].get("if", "")), name
    for name, job in jobs.items():
        condition = str(job.get("if", ""))
        if name not in MEDIUM_JOBS:
            skipped = "!inputs.medium-only" in condition or condition in {
                "${{ github.event_name == 'push' }}",
                "${{ github.event_name == 'schedule' }}",
            }
            assert skipped, f"{filename} {name} must skip in medium-only mode"


@pytest.mark.parametrize("filename", ["trunk.yml", "trunk-coverage.yml"])
def test_every_medium_artifact_name_takes_the_caller_prefix(filename: str) -> None:
    names = []
    for name in MEDIUM_JOBS:
        for step in _load(filename)["jobs"][name]["steps"]:
            if str(step.get("uses", "")).startswith(("actions/upload-artifact@", "actions/download-artifact@")):
                names.append(step["with"]["name"])
    assert len(names) == 3
    for name in names:
        assert name.startswith("${{ inputs.artifact-prefix }}t2-medium-"), name


@pytest.mark.parametrize("lane", LANES)
def test_lane_evidence_never_matches_the_pr_medium_evidence_pattern(lane: str) -> None:
    prefix = LANES[lane][2]
    core = _load("ci.yml")["jobs"]["core"]
    pattern = next(
        step["with"]["pattern"]
        for step in core["steps"]
        if step.get("name") == "Download complete medium shard evidence"
    )
    glob = re.compile("^" + re.escape(pattern).replace(r"\*", ".*").replace(r"\$\{\{\ github\.sha\ \}\}", "S") + "$")
    assert glob.fullmatch("t2-medium-shard-0-S")
    assert not glob.fullmatch(f"{prefix}t2-medium-shard-0-S")
    assert not glob.fullmatch(f"{prefix}t2-medium-nodeids-S")


def test_the_plain_lane_runs_the_medium_tier_uninstrumented() -> None:
    runs = "\n".join(step.get("run", "") for step in _load("trunk.yml")["jobs"]["medium-test"]["steps"])
    assert "--cov" not in runs and "COVERAGE_" not in runs and "pytest_coverage_timeout" not in runs
    assert "--timeout=60" in runs


def test_the_coverage_lane_runs_the_medium_tier_instrumented_with_scaled_timeouts() -> None:
    job = _load("trunk-coverage.yml")["jobs"]["medium-test"]
    run_step = next(step for step in job["steps"] if step.get("name") == "Run medium speed tier")
    assert run_step["env"]["COVERAGE_CORE"] == "ctrace"
    assert "-p scripts.pytest_coverage_timeout" in run_step["run"]
    assert "--cov-config=scripts/coverage_map.ini" in run_step["run"]
    assert job["timeout-minutes"] == 33


@pytest.mark.parametrize("filename", ["trunk.yml", "trunk-coverage.yml"])
def test_the_lane_schedules_shards_like_the_push_run(filename: str) -> None:
    jobs = _load(filename)["jobs"]
    assert jobs["medium-test"]["strategy"]["matrix"]["shard_index"] == [0, 1, 2, 3]
    text = "\n".join(str(step.get("run", "")) for name in MEDIUM_JOBS for step in jobs[name]["steps"])
    assert text.count("--durations tests/fixtures/medium_shard_durations.json") == 2
    assert text.count("--shard-count 4") == 2
    assert "--dist loadscope" in text
    assert f"--workflow {filename}" in text


def test_the_coverage_lane_never_shares_a_concurrency_group_across_pull_requests() -> None:
    group = _load("trunk-coverage.yml")["concurrency"]["group"]
    assert "inputs.medium-only" in group and "github.ref" in group
    assert _load("trunk-coverage.yml")["concurrency"]["cancel-in-progress"] is True
