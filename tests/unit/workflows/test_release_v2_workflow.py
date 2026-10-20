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


def test_release_uat_runs_the_admitted_wheel_and_checks_the_combined_gate():
    uat = _jobs()["release-uat"]
    run = _step_commands(uat)
    assert 'uv pip install --python .venv/bin/python "$RUNNER_TEMP"/staged/*.whl' in run
    assert "release-gate-*.yaml" in run
    assert "site-packages" in run
    assert "benchbox.checkout" in run
    gate_check = run.index("make uat-gate-check")
    assert run.index("make uat-sweep") < gate_check
    assert "DIST_DIGEST=" in run
    assert 'RELEASE_DIST_DIGEST_FILE=$RUNNER_TEMP/dist-digest.txt" >> "$GITHUB_ENV"' in run


def test_release_uat_hides_the_checkout_package_before_running_and_always_restores_it():
    steps = _jobs()["release-uat"]["steps"]
    names = [s.get("name") for s in steps]
    assert names.index("Hide the checkout package so UAT imports the admitted wheel") < names.index(
        "Run the release-gate UAT configs against the admitted distribution"
    )
    restore = steps[names.index("Restore the checkout package")]
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
