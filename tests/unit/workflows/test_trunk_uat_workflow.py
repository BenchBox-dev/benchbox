from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from scripts import release_reuse_uat_evidence as reuse, trunk_uat

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github" / "workflows"
TRUNK_UAT = WORKFLOWS / "trunk-uat.yml"
RELEASE = WORKFLOWS / "release-v2.yml"
STAGES = ("uat-stage1", "uat-stage2", "uat-stage3")
UPLOAD = "actions/upload-artifact@"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _jobs(path: Path = TRUNK_UAT) -> dict:
    return _load(path)["jobs"]


def _uploads(job: dict) -> list[dict]:
    return [step for step in job["steps"] if str(step.get("uses", "")).startswith(UPLOAD)]


def _step(job: dict, name: str) -> dict:
    return next(step for step in job["steps"] if step.get("name") == name)


def test_runs_on_the_nightly_schedule_and_on_dispatch_only():
    triggers = _load(TRUNK_UAT)[True]
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert triggers["schedule"] == [{"cron": "0 2 * * *"}]


def test_runs_never_overlap_and_a_queued_run_never_cancels_a_running_one():
    concurrency = _load(TRUNK_UAT)["concurrency"]
    assert concurrency == {"group": "trunk-uat", "cancel-in-progress": False}


def test_skip_path_set_covers_every_input_the_release_reuse_guards():
    assert set(reuse.GUARDED_TREES) <= set(trunk_uat.SKIP_PATHS)
    assert {"benchbox", "uv.lock", ".github/workflows/trunk-uat.yml", reuse.WORKFLOW_PATH} <= set(trunk_uat.SKIP_PATHS)


@pytest.mark.parametrize("stage", STAGES)
def test_shard_matrix_and_stage_definition_match_release_v2(stage):
    trunk, release = _jobs()[stage], _jobs(RELEASE)[stage]
    assert trunk["strategy"] == release["strategy"]
    assert trunk["timeout-minutes"] == release["timeout-minutes"]
    assert trunk["runs-on"] == release["runs-on"]
    run_step = next(step for step in trunk["steps"] if step.get("name", "").startswith("Run the stage"))
    release_step = next(step for step in release["steps"] if step.get("name", "").startswith("Run the stage"))
    assert run_step["env"] == release_step["env"]
    assert run_step["run"] == release_step["run"]
    assert run_step["timeout-minutes"] == release_step["timeout-minutes"]


def test_workflow_level_env_matches_release_v2():
    assert _load(TRUNK_UAT)["env"] == _load(RELEASE)["env"]


@pytest.mark.parametrize("stage", STAGES)
def test_every_shard_job_runs_the_admitted_trunk_distribution_of_the_selected_commit(stage):
    job = _jobs()[stage]
    assert job["needs"] == ["plan", "admit"]
    assert "needs.admit.result == 'success'" in job["if"]
    assert "!cancelled()" in job["if"]
    checkout = job["steps"][0]
    assert checkout["with"]["ref"] == "${{ needs.plan.outputs.source_sha }}"
    prepare = next(step for step in job["steps"] if step.get("uses") == "./.github/actions/release-uat-env")
    assert prepare["with"] == {
        "tag": "${{ needs.admit.outputs.tag }}",
        "head-sha": "${{ needs.plan.outputs.source_sha }}",
    }


def test_the_workflow_reuses_the_release_harness_and_never_rebuilds_the_distribution():
    text = TRUNK_UAT.read_text(encoding="utf-8")
    assert "make uat-gate-check" in text
    assert text.count("make uat-sweep") == 3
    for build_command in ("uv build", "python -m build", "hatch build", "setup.py sdist", "bdist_wheel"):
        assert build_command not in text
    release_configs = set(re.findall(r"tests/uat/configs/release-gate-[\w-]+\.yaml", RELEASE.read_text("utf-8")))
    assert set(re.findall(r"tests/uat/configs/release-gate-[\w-]+\.yaml", text)) == release_configs
    for config in release_configs:
        assert (ROOT / config).is_file()


def test_evidence_names_include_the_source_sha_survive_a_partial_rerun_and_are_kept_ninety_days():
    jobs = _jobs()
    uploads = [(name, step) for name, job in jobs.items() for step in _uploads(job)]
    evidence = [(name, step) for name, step in uploads if not step["with"]["name"].startswith("admitted-")]
    assert {name for name, _ in evidence} == {*STAGES, "gate", "record"}
    for _, step in evidence:
        assert "${{ needs.plan.outputs.source_sha }}" in step["with"]["name"]
        assert step["with"]["retention-days"] == 90
        assert step["with"]["overwrite"] is True
    prefixes = {re.sub(r"\$\{\{.*?\}\}", "", step["with"]["name"]) for _, step in evidence}
    assert any(prefix.startswith(trunk_uat.RECORD_PREFIX) for prefix in prefixes)


def test_a_skipped_or_deferred_run_stops_after_the_plan_job():
    jobs = _jobs()
    assert jobs["admit"]["needs"] == "plan"
    assert jobs["admit"]["if"] == "${{ needs.plan.outputs.action == 'run' }}"
    for stage in STAGES:
        assert "needs.admit.result == 'success'" in jobs[stage]["if"]
    assert "needs.plan.outputs.action == 'run'" in jobs["record"]["if"]
    plan_commands = "\n".join(step.get("run", "") for step in jobs["plan"]["steps"])
    assert "scripts/trunk_uat.py plan" in plan_commands
    assert "make " not in plan_commands
    assert jobs["plan"]["timeout-minutes"] <= 30


def test_the_record_job_runs_after_cancellation_and_reports_every_upstream_result():
    record = _jobs()["record"]
    assert record["if"].startswith("${{ always()")
    assert set(record["needs"]) == {"plan", "admit", *STAGES, "gate"}
    command = _step(record, "Record the run and append per-cell history")["run"]
    for name in ("admit", *STAGES, "gate"):
        assert f'--job-result "{name}=$' in command
    assert _step(record, "Record the run and append per-cell history")["if"] == "always()"


def test_the_gate_job_checks_every_shard_and_requires_each_stage_to_succeed():
    gate = _jobs()["gate"]
    assert set(gate["needs"]) == {"plan", "admit", *STAGES}
    commands = "\n".join(step.get("run", "") for step in gate["steps"])
    assert commands.index("scripts/release_uat_evidence_check.py") < commands.index("make uat-gate-check")
    assert '--tag "trunk-$SOURCE_SHA"' in commands
    assert "make uat-gate-check" in commands
    assert 'DIST_DIGEST="$(sha256sum "$RELEASE_DIST_DIGEST_FILE" | cut -d\' \' -f1)"' in commands
    require = _step(gate, "Require every shard job to have succeeded")
    assert require["if"] == "always()"
    assert '!= "success"' in require["run"]


def test_admission_installs_the_hash_pinned_requirements_before_verifying_the_distribution():
    admit = _jobs()["admit"]
    commands = [step.get("run", "") for step in admit["steps"]]
    install = next(index for index, text in enumerate(commands) if "--require-hashes" in text)
    verify = next(index for index, text in enumerate(commands) if "scripts/trunk_uat.py admit" in text)
    assert install < verify
    assert "scripts/release_requirements.txt" in commands[install]


def test_every_checkout_is_pinned_to_the_same_action_as_release_v2():
    def pins(path: Path) -> set[str]:
        return set(re.findall(r"actions/checkout@([0-9a-f]{40})", path.read_text(encoding="utf-8")))

    assert pins(TRUNK_UAT) == pins(RELEASE)
    assert len(pins(TRUNK_UAT)) == 1
