from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "release-v2.yml"
LEGACY = WORKFLOWS / "release.yml"


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


def test_legacy_release_is_dispatch_only_so_a_tag_push_starts_one_publisher():
    triggers = _triggers(LEGACY)
    assert set(triggers) == {"workflow_dispatch"}
    assert "push" not in _triggers(LEGACY)
    assert "push" in _triggers(WORKFLOW)


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


def test_release_uat_stages_run_in_order_and_each_shards_one_stage_config():
    jobs = _jobs()
    assert jobs["uat-stage1"]["needs"] == "admit"
    assert set(jobs["uat-stage2"]["needs"]) == {"admit", "uat-stage1"}
    assert set(jobs["uat-stage3"]["needs"]) == {"admit", "uat-stage2"}
    for number, name in enumerate(UAT_STAGE_JOBS, start=1):
        job = jobs[name]
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


def test_later_stages_run_after_a_failed_earlier_stage_so_one_run_surfaces_every_failure():
    for name in ("uat-stage2", "uat-stage3"):
        assert "!cancelled()" in _jobs()[name]["if"]
        assert "needs.admit.result == 'success'" in _jobs()[name]["if"]


def test_release_uat_gate_checks_every_shard_against_the_admitted_digest():
    gate = _jobs()["release-uat"]
    assert set(gate["needs"]) == {"admit", *UAT_STAGE_JOBS}
    assert "!cancelled()" in gate["if"]
    run = _step_commands(gate)
    assert "make uat-gate-check" in run
    assert "DIST_DIGEST=" in run
    for number in (1, 2, 3):
        assert f"-stage{number}-*/run" in run
    download = next(s for s in gate["steps"] if "download-artifact" in s.get("uses", "") and "pattern" in s["with"])
    assert download["with"]["pattern"].endswith("-stage*")
    assert not any("uat-sweep" in step.get("run", "") for step in gate["steps"])


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
