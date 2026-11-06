from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "release-v2.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _jobs() -> dict:
    return yaml.safe_load(_text())["jobs"]


def _triggers(path: Path) -> dict:
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    return parsed.get("on", parsed.get(True))


def _step_commands(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job["steps"])


def _tag_shape_regex() -> re.Pattern[str]:
    step = next(s for s in _jobs()["admit"]["steps"] if s.get("name") == "Validate tag shape")
    match = re.search(r"=~ (\^v.*\$) \]\]", step["run"])
    assert match, "tag shape check not found"
    return re.compile(match.group(1).replace("[0-9]", r"\d"))


def test_release_v2_starts_on_version_tag_push_or_dispatch_with_a_tag():
    triggers = _triggers(WORKFLOW)
    assert triggers["push"]["tags"] == ["v*"]
    assert triggers["workflow_dispatch"]["inputs"]["tag"]["required"] is True


@pytest.mark.parametrize(
    ("tag", "valid"),
    [
        ("v0.5.0", True),
        ("v0.5.0rc1", True),
        ("v0.5.0a2", True),
        ("v0.5.0b3", True),
        ("v0.5.0-rc.1", False),
        ("v0.5.0.post1", False),
        ("v0.5", False),
        ("0.5.0", False),
    ],
)
def test_tag_shape_accepts_only_pep_440_release_tags(tag, valid):
    assert bool(_tag_shape_regex().fullmatch(tag)) is valid


def test_only_final_tags_route_to_pypi():
    step = next(s for s in _jobs()["admit"]["steps"] if s.get("id") == "route")
    final = re.search(r"=~ (\^v.*\$) \]\]", step["run"])
    assert final
    pattern = re.compile(final.group(1).replace("[0-9]", r"\d"))
    assert pattern.fullmatch("v0.5.0")
    assert not pattern.fullmatch("v0.5.0rc1")
    assert step["run"].index("target=pypi") < step["run"].index("target=test-pypi")
    publish = _jobs()["publish"]
    assert publish["environment"]["name"] == "${{ needs.admit.outputs.target }}"
    text = _text()
    assert "target == 'test-pypi'" in text and "target == 'pypi'" in text


def test_workflow_admits_and_stages_without_rebuilding():
    text = _text()
    assert "release_artifact_consumer.py admit" in text
    assert "release_admitted_dist.py" in text and "--stage" in text
    for build_command in ("uv build", "python -m build", "hatch build", "setup.py sdist", "bdist_wheel"):
        assert build_command not in text


def test_workflow_never_falls_back_to_an_older_run():
    text = _text()
    assert "--run-id" not in text
    assert "gh run download" not in text
    assert "gh run list" not in text


def test_workflow_requires_an_annotated_tag_before_admission():
    steps = [s.get("name") for s in _jobs()["admit"]["steps"]]
    assert steps.index("Require an annotated tag") < steps.index("Admit the trunk-tested artifacts")
    assert 'git cat-file -t "$RELEASE_TAG"' in _text()


def test_publish_rechecks_the_hosted_tag_after_environment_approval_and_before_staging():
    publish = _jobs()["publish"]
    assert "environment" in publish
    names = [s.get("name") for s in publish["steps"]]
    assert names.index("Re-check the hosted tag after approval") < names.index("Stage only the verified distributions")
    recheck = next(s for s in publish["steps"] if s.get("name") == "Re-check the hosted tag after approval")
    assert "release_tag_recheck.py" in recheck["run"]


def test_publish_stages_then_attests_then_publishes_only_staged_files():
    publish = _jobs()["publish"]
    names = [s.get("name") for s in publish["steps"]]
    assert (
        names.index("Stage only the verified distributions")
        < names.index("Attest the staged distributions")
        < names.index("Publish to TestPyPI")
    )
    for step in publish["steps"]:
        if step.get("name", "").startswith("Publish to"):
            assert step["with"]["packages-dir"] == "staged/"


def test_workflow_covers_the_twelve_cell_install_matrix():
    matrix = _jobs()["install-matrix"]["strategy"]["matrix"]
    assert matrix["os"] == ["ubuntu-latest", "macos-latest", "windows-latest"]
    assert matrix["python-version"] == ["3.11", "3.12", "3.13", "3.14"]


def test_install_matrix_installs_the_staged_wheel_not_the_checkout():
    run = _step_commands(_jobs()["install-matrix"])
    assert "release_admitted_dist.py" in run
    assert "pip install staged/*.whl" in run


def test_admit_job_runs_every_pre_publish_gate():
    admit = _jobs()["admit"]
    run = _step_commands(admit)
    assert "make release-check" in run
    assert "ruleset_drift_check.py" in run
    assert "release_blocker_check.py" in run
    assert "unset RELEASE_READINESS_OVERRIDE_SHA RELEASE_READINESS_OVERRIDE_REASON" in run
    assert "RELEASE_READINESS_OVERRIDE" not in "".join(str(s.get("env", "")) for s in admit["steps"])


def test_publish_needs_every_gate_job():
    assert set(_jobs()["publish"]["needs"]) == {"admit", "install-matrix", "release-uat"}


ENV_ACTION = Path(__file__).resolve().parents[3] / ".github" / "actions" / "release-uat-env" / "action.yml"
UAT_STAGE_JOBS = ("uat-stage1", "uat-stage2", "uat-stage3")


def _env_action_commands() -> str:
    steps = yaml.safe_load(ENV_ACTION.read_text(encoding="utf-8"))["runs"]["steps"]
    return "\n".join(step.get("run", "") for step in steps)


def test_release_uat_environment_installs_the_admitted_wheel_and_records_its_digest():
    run = _env_action_commands()
    assert 'uv pip install --python .venv/bin/python "$RUNNER_TEMP"/staged/*.whl' in run
    assert 'RELEASE_DIST_DIGEST_FILE=$RUNNER_TEMP/dist-digest.txt" >> "$GITHUB_ENV"' in run
    assert "benchbox.checkout" in run


def test_release_uat_stages_start_together_and_each_shards_one_stage_config():
    jobs = _jobs()
    for number, name in enumerate(UAT_STAGE_JOBS, start=1):
        job = jobs[name]
        assert set(job["needs"]) == {"admit", "uat-reuse"}
        run = _step_commands(job)
        assert f"release-gate-0{number}-" in "\n".join(
            str(step.get("env", {}).get("UAT_CONFIG", "")) for step in job["steps"]
        )
        assert 'make uat-sweep CONFIG="$UAT_CONFIG" PLATFORMS="$SHARD_PLATFORMS"' in run
        assert "site-packages" in run
        assert job["strategy"]["fail-fast"] is False
        assert job["timeout-minutes"] <= 360


def test_release_uat_shards_always_upload_their_evidence():
    for name in UAT_STAGE_JOBS:
        steps = _jobs()[name]["steps"]
        upload = next(s for s in steps if "upload-artifact" in s.get("uses", ""))
        assert upload["if"] == "always()"
        assert upload["with"]["name"].startswith("release-uat-${{ inputs.tag || github.ref_name }}-stage")
        assert "${{ matrix.shard }}" in upload["with"]["name"]
        sweep = next(s for s in steps if "uat-sweep" in s.get("run", ""))
        assert sweep["timeout-minutes"] < _jobs()[name]["timeout-minutes"]


def test_stages_run_independently_of_each_other_and_skip_only_when_reuse_is_verified():
    for name in UAT_STAGE_JOBS:
        condition = _jobs()[name]["if"]
        assert "!cancelled()" in condition
        assert "needs.admit.result == 'success'" in condition
        assert "needs.uat-reuse.outputs.reuse != 'true'" in condition
        assert "needs.uat-stage" not in condition


def test_release_uat_gate_checks_every_shard_against_the_admitted_digest():
    gate = _jobs()["release-uat"]
    assert set(gate["needs"]) == {"admit", "uat-reuse", *UAT_STAGE_JOBS}
    assert "!cancelled()" in gate["if"]
    normal = next(s for s in gate["steps"] if s.get("name") == "Check the combined release gate over every shard")
    assert normal["if"] == "${{ needs.uat-reuse.outputs.reuse != 'true' }}"
    assert "make uat-gate-check" in normal["run"]
    assert 'DIST_DIGEST="$(sha256sum "$RELEASE_DIST_DIGEST_FILE" | cut -d\' \' -f1)"' in normal["run"]
    for number in (1, 2, 3):
        assert f"-stage{number}-*/run" in normal["run"]
    download = next(s for s in gate["steps"] if s.get("name") == "Download every shard's evidence")
    assert download["if"] == "${{ needs.uat-reuse.outputs.reuse != 'true' }}"
    assert download["with"]["pattern"] == "release-uat-${{ inputs.tag || github.ref_name }}-stage*"
    assert "run-id" not in download["with"]
    assert not any("uat-sweep" in step.get("run", "") for step in gate["steps"])


def test_release_uat_reuse_path_regates_the_release_candidates_shards_with_its_admitted_digest():
    gate = _jobs()["release-uat"]
    download = next(s for s in gate["steps"] if s.get("name") == "Download the release candidate's shard evidence")
    assert download["if"] == "${{ needs.uat-reuse.outputs.reuse == 'true' }}"
    assert download["with"]["run-id"] == "${{ needs.uat-reuse.outputs.rc_run_id }}"
    assert download["with"]["github-token"] == "${{ github.token }}"
    assert download["with"]["pattern"] == "release-uat-${{ needs.uat-reuse.outputs.rc_tag }}-stage*"
    verify = next(s for s in gate["steps"] if s.get("name", "").startswith("Re-verify the release candidate"))
    assert verify["if"] == "${{ needs.uat-reuse.outputs.reuse == 'true' }}"
    assert verify["env"]["RC_DIST_DIGEST"] == "${{ needs.uat-reuse.outputs.rc_dist_digest }}"
    assert 'DIST_DIGEST="$RC_DIST_DIGEST"' in verify["run"]
    assert "make uat-gate-check" in verify["run"]
    assert "release_reuse_uat_evidence.py verify-gate" in verify["run"]
    for number in (1, 2, 3):
        assert f'release-uat-"$RC_TAG"-stage{number}-*/run' in verify["run"]


def test_release_uat_gate_fails_unless_every_shard_job_succeeded_or_was_replaced_by_verified_reuse():
    gate = _jobs()["release-uat"]
    step = next(s for s in gate["steps"] if s.get("name", "").startswith("Require every shard job"))
    assert step["if"] == "always()"
    for number in (1, 2, 3):
        assert step["env"][f"STAGE{number}_RESULT"] == f"${{{{ needs.uat-stage{number}.result }}}}"
    assert step["env"]["REUSE"] == "${{ needs.uat-reuse.outputs.reuse }}"
    assert step["env"]["REUSE_RESULT"] == "${{ needs.uat-reuse.result }}"
    run = step["run"]
    assert 'if [ "$REUSE" = "true" ]; then\n  expected=skipped' in run
    assert "expected=success" in run
    assert '!= "$expected"' in run
    assert '"$REUSE_RESULT" != "success"' in run
    assert 'exit "$status"' in run


def test_uat_reuse_runs_only_for_final_tags_and_only_after_admission():
    job = _jobs()["uat-reuse"]
    assert job["needs"] == "admit"
    assert job["if"] == "${{ needs.admit.outputs.target == 'pypi' }}"
    assert job["permissions"] == {"contents": "read", "actions": "read"}
    assert set(job["outputs"]) == {"reuse", "rc_tag", "rc_sha", "rc_run_id", "rc_dist_digest"}
    for value in job["outputs"].values():
        assert value.startswith("${{ steps.decide.outputs.")


def test_uat_reuse_decides_from_the_admitted_final_distribution_and_the_git_history():
    job = _jobs()["uat-reuse"]
    checkout = job["steps"][0]
    assert checkout["with"]["ref"] == "${{ needs.admit.outputs.head_sha }}"
    assert checkout["with"]["fetch-depth"] == 0
    stage = next(s for s in job["steps"] if s.get("name") == "Stage the admitted distributions")
    assert "release_admitted_dist.py" in stage["run"] and '--stage "$RUNNER_TEMP/final-dist"' in stage["run"]
    decide = next(s for s in job["steps"] if s.get("id") == "decide")
    assert decide["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert "release_reuse_uat_evidence.py decide" in decide["run"]
    assert '--final-dist "$RUNNER_TEMP/final-dist"' in decide["run"]
    names = [s.get("name") for s in job["steps"]]
    assert names.index("Stage the admitted distributions") < names.index(decide["name"])


def test_only_the_reuse_and_gate_jobs_read_other_runs():
    for name, job in _jobs().items():
        actions = job.get("permissions", {}).get("actions")
        assert (actions == "read") == (name in {"admit", "uat-reuse", "release-uat"}), name
    assert yaml.safe_load(_text())["permissions"] == {"contents": "read"}


def test_publish_runs_on_gate_results_so_a_skipped_reuse_job_cannot_skip_it():
    publish = _jobs()["publish"]
    assert {"install-matrix", "release-uat"} <= set(publish["needs"])
    assert "uat-reuse" not in publish["needs"]
    condition = publish["if"]
    assert condition.startswith("${{ !cancelled() &&")
    for need in publish["needs"]:
        assert f"needs.{need}.result == 'success'" in condition


def test_github_release_runs_only_after_a_successful_pypi_publish():
    condition = _jobs()["github-release"]["if"]
    assert condition.startswith("${{ !cancelled() &&")
    assert "needs.admit.outputs.target == 'pypi'" in condition
    assert "needs.publish.result == 'success'" in condition


def test_install_matrix_does_not_depend_on_reuse():
    assert _jobs()["install-matrix"]["needs"] == "admit"


def test_release_uat_jobs_use_the_local_environment_action_from_the_admitted_checkout():
    for name in (*UAT_STAGE_JOBS, "release-uat"):
        steps = _jobs()[name]["steps"]
        names = [s.get("uses", "") for s in steps]
        assert "actions/checkout" in names[0]
        assert names[1] == "./.github/actions/release-uat-env"


def test_release_uat_jobs_always_restore_the_checkout_package():
    for name in (*UAT_STAGE_JOBS, "release-uat"):
        steps = _jobs()[name]["steps"]
        restore = next(s for s in steps if s.get("name") == "Restore the checkout package")
        assert restore["if"] == "always()"


def test_workflow_attests_the_staged_distributions():
    publish = _jobs()["publish"]
    assert publish["permissions"]["attestations"] == "write"
    assert publish["permissions"]["id-token"] == "write"
    attest = next(s for s in publish["steps"] if "attest-build-provenance" in s.get("uses", ""))
    assert "staged/*.whl" in attest["with"]["subject-path"]
    assert "staged/*.tar.gz" in attest["with"]["subject-path"]


def test_only_admission_checks_out_the_mutable_tag_ref():
    for name, job in _jobs().items():
        for step in job["steps"]:
            if "actions/checkout" not in step.get("uses", ""):
                continue
            ref = step.get("with", {}).get("ref")
            if name == "admit":
                assert ref == "${{ inputs.tag || github.ref_name }}"
            else:
                assert ref == "${{ needs.admit.outputs.head_sha }}", f"{name} must check out the admitted commit"


def _env_action_steps() -> list[dict]:
    return yaml.safe_load(ENV_ACTION.read_text(encoding="utf-8"))["runs"]["steps"]


def test_release_uat_environment_pins_a_pyspark_compatible_jdk():
    java = next(s for s in _env_action_steps() if "actions/setup-java" in s.get("uses", ""))
    assert java["with"]["java-version"] in {"17", "21"}
    names = [s.get("name") for s in _env_action_steps()]
    assert names.index(java["name"]) < names.index("Install the admitted wheel and record its digest")


def test_release_uat_environment_installs_the_extra_of_every_shard_platform():
    from tests.uat.matrix import PLATFORM_UV_EXTRA

    sync = next(line for line in _env_action_commands().splitlines() if line.startswith("uv sync"))
    installed = set(re.findall(r"--extra (\S+)", sync))
    for name in UAT_STAGE_JOBS:
        for entry in _jobs()[name]["strategy"]["matrix"]["include"]:
            for platform in entry["platforms"].split(","):
                extra = PLATFORM_UV_EXTRA.get(platform)
                assert extra is None or extra in installed, f"{name} shard {entry['shard']}: {platform} needs {extra}"


REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER_MEMORY_GIB = 16


def _requirement_name(requirement: str) -> str:
    return re.split(r"[\s\[<>=!~;]", requirement.strip(), maxsplit=1)[0].lower().replace("_", "-")


def _installed_distributions() -> set[str]:
    import tomllib

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    sync = next(line for line in _env_action_commands().splitlines() if line.startswith("uv sync"))
    extras = re.findall(r"--extra (\S+)", sync)
    requirements = list(pyproject["dependency-groups"]["dev"]) + list(pyproject["project"]["dependencies"])
    for extra in extras:
        requirements += pyproject["project"]["optional-dependencies"][extra]
    return {_requirement_name(item) for item in requirements if isinstance(item, str)}


def _manifest_requirements(platform: str) -> tuple[str, ...]:
    from benchbox.core.platform_manifest import get_platform_manifest_entry

    entry = get_platform_manifest_entry(platform) or get_platform_manifest_entry(platform.removesuffix("-df"))
    assert entry is not None, f"{platform} has no platform manifest entry"
    return tuple(item for item in entry.metadata.get("requirements", ()) if "(built-in)" not in item)


def _stage_configs() -> dict[str, Path]:
    configs = {}
    for name in UAT_STAGE_JOBS:
        job = _jobs()[name]
        relative = next(step["env"]["UAT_CONFIG"] for step in job["steps"] if "UAT_CONFIG" in step.get("env", {}))
        configs[name] = REPO_ROOT / relative
    return configs


@pytest.mark.parametrize("stage", UAT_STAGE_JOBS)
def test_release_uat_shards_cover_the_config_platforms_and_install_their_requirements(stage):
    from tests.uat.config import load_config
    from tests.uat.phases.enumerate import resolved_platforms

    config_platforms = set(resolved_platforms(load_config(_stage_configs()[stage])))
    shard_platforms = {
        platform
        for entry in _jobs()[stage]["strategy"]["matrix"]["include"]
        for platform in entry["platforms"].split(",")
    }
    assert shard_platforms == config_platforms
    installed = _installed_distributions()
    for platform in sorted(config_platforms):
        missing = [item for item in _manifest_requirements(platform) if _requirement_name(item) not in installed]
        assert not missing, f"{stage}: {platform} requires {missing} that the shard environment does not install"


def test_stage_one_bounds_per_cell_memory_below_the_hosted_runner():
    from tests.uat.config import load_config

    config = load_config(_stage_configs()["uat-stage1"])
    assert _jobs()["uat-stage1"]["runs-on"] == "ubuntu-latest"
    assert 0 < config.execute.per_cell_memory_limit_gib <= RUNNER_MEMORY_GIB - 2


def test_stage_one_watchdog_budget_matches_the_per_cell_memory_limit():
    from tests.uat.config import load_config

    config = load_config(_stage_configs()["uat-stage1"])
    step = next(s for s in _jobs()["uat-stage1"]["steps"] if "UAT_CONFIG" in s.get("env", {}))
    assert float(step["env"]["BENCHBOX_HEAVY_MEMORY_BUDGET_GIB"]) == config.execute.per_cell_memory_limit_gib


def test_stage_two_clickhouse_memory_leaves_runner_headroom():
    from tests.uat import docker_assets

    assert _jobs()["uat-stage2"]["runs-on"] == "ubuntu-latest"
    step = next(s for s in _jobs()["uat-stage2"]["steps"] if "UAT_CONFIG" in s.get("env", {}))
    limit = docker_assets.parse_memory_bytes(step["env"]["CLICKHOUSE_MEMORY_LIMIT"]) / 1024**3
    assert 8 < limit <= RUNNER_MEMORY_GIB - 4


def test_every_uat_job_frees_runner_disk_through_the_shared_environment_action():
    action = yaml.safe_load(
        (WORKFLOW.parents[1] / "actions" / "release-uat-env" / "action.yml").read_text(encoding="utf-8")
    )
    first = action["runs"]["steps"][0]
    assert first["name"] == "Free runner disk space"
    assert "/usr/share/dotnet" in first["run"] and "/usr/local/lib/android" in first["run"]
    for name in ("uat-stage1", "uat-stage2", "uat-stage3"):
        uses = [step.get("uses") for step in _jobs()[name]["steps"]]
        assert "./.github/actions/release-uat-env" in uses


OUTCOME_JOBS = ("admit", "install-matrix", "release-uat", "publish", "github-release")
PYPI_SUCCESS = dict.fromkeys(OUTCOME_JOBS, "success")
TEST_PYPI_SUCCESS = {**PYPI_SUCCESS, "github-release": "skipped"}
RUN_41949185392_ATTEMPT_2 = {**TEST_PYPI_SUCCESS, "publish": "skipped"}


def _outcome_step() -> dict:
    return _jobs()["release-outcome"]["steps"][0]


def _run_outcome(target: str, results: dict[str, str]) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    assert bash is not None
    step = _outcome_step()
    env = {"PATH": os.environ["PATH"], "TARGET": target}
    for name, value in step["env"].items():
        if name.endswith("_RESULT"):
            job = next(job for job in OUTCOME_JOBS if f"needs.{job}.result" in value)
            env[name] = results[job]
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / "step.sh").write_text(step["run"], encoding="utf-8", newline="\n")
        return subprocess.run(
            [bash, "step.sh"], cwd=directory, env=env, capture_output=True, text=True, timeout=30, check=False
        )


def test_release_outcome_job_waits_for_every_release_job_and_always_runs():
    job = _jobs()["release-outcome"]
    assert set(job["needs"]) == set(OUTCOME_JOBS)
    assert job["if"] == "${{ always() }}"
    assert job["permissions"] == {}
    assert len(job["steps"]) == 1
    assert _outcome_step()["env"]["TARGET"] == "${{ needs.admit.outputs.target }}"


def test_release_outcome_stays_green_for_a_complete_pypi_release():
    result = _run_outcome("pypi", PYPI_SUCCESS)
    assert result.returncode == 0, result.stdout + result.stderr


def test_release_outcome_stays_green_for_a_complete_test_pypi_release_where_github_release_is_skipped():
    result = _run_outcome("test-pypi", TEST_PYPI_SUCCESS)
    assert result.returncode == 0, result.stdout + result.stderr


def test_release_outcome_fails_the_run_that_went_green_with_publish_skipped():
    result = _run_outcome("test-pypi", RUN_41949185392_ATTEMPT_2)
    assert result.returncode == 1
    assert "publish finished as 'skipped', not 'success'" in result.stdout
    assert result.stdout.count("::error::") == 1


@pytest.mark.parametrize("job", ["admit", "install-matrix", "release-uat", "publish"])
@pytest.mark.parametrize("result", ["skipped", "failure", "cancelled", ""])
@pytest.mark.parametrize("target", ["pypi", "test-pypi"])
def test_release_outcome_cannot_be_bypassed_by_a_job_that_did_not_succeed(target, result, job):
    results = {**(PYPI_SUCCESS if target == "pypi" else TEST_PYPI_SUCCESS), job: result}
    outcome = _run_outcome(target, results)
    assert outcome.returncode == 1
    assert f"{job} finished as" in outcome.stdout


@pytest.mark.parametrize("result", ["skipped", "failure", "cancelled", ""])
def test_release_outcome_requires_the_github_release_of_a_pypi_publish(result):
    outcome = _run_outcome("pypi", {**PYPI_SUCCESS, "github-release": result})
    assert outcome.returncode == 1
    assert "github-release finished as" in outcome.stdout


def test_release_outcome_refuses_a_github_release_for_a_test_pypi_publish():
    outcome = _run_outcome("test-pypi", {**TEST_PYPI_SUCCESS, "github-release": "success"})
    assert outcome.returncode == 1


@pytest.mark.parametrize("target", ["", "unknown"])
def test_release_outcome_fails_when_the_admitted_target_is_not_a_tag_target(target):
    outcome = _run_outcome(target, PYPI_SUCCESS)
    assert outcome.returncode == 1
    assert "neither 'pypi' nor 'test-pypi'" in outcome.stdout


def test_release_uat_requires_executed_cells_before_the_combined_gate_on_both_paths():
    steps = _jobs()["release-uat"]["steps"]
    names = [step.get("name") for step in steps]
    fresh = steps[names.index("Require executed cells in every stage's shard evidence")]
    reused = steps[names.index("Require executed cells in the release candidate's shard evidence")]
    assert fresh["if"] == "${{ needs.uat-reuse.outputs.reuse != 'true' }}"
    assert reused["if"] == "${{ needs.uat-reuse.outputs.reuse == 'true' }}"
    assert fresh["env"]["RELEASE_TAG"] == "${{ inputs.tag || github.ref_name }}"
    assert reused["env"]["RC_TAG"] == "${{ needs.uat-reuse.outputs.rc_tag }}"
    for step, tag in ((fresh, "RELEASE_TAG"), (reused, "RC_TAG")):
        assert f'scripts/release_uat_evidence_check.py --root "$RUNNER_TEMP/uat-evidence" --tag "${tag}"' in step["run"]
    assert names.index(fresh["name"]) < names.index("Check the combined release gate over every shard")
    gate_check = "Re-verify the release candidate's evidence with the combined release gate"
    assert names.index(reused["name"]) < names.index(gate_check)
