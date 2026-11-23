from __future__ import annotations

import copy
import itertools
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

from scripts import release_probe_graph as graph, release_probe_verdict as verdict

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github" / "workflows"
RELEASE = WORKFLOWS / "release-v2.yml"
PROBE_WORKFLOW = WORKFLOWS / "release-v2-probe.yml"
REAL_MODE = "${{ github.event_name == 'push' || inputs.probe == 'none' }}"
PROBE_MODE = "${{ github.event_name == 'workflow_dispatch' && inputs.probe != 'none' }}"
PROBE_TAG = "v0.0.0rc0"


def _workflow(path: Path = RELEASE) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture
def live() -> dict:
    return _workflow()


def _scenario(event: str, probe: str, reuse: str, target: str, outcomes: dict[str, str] | None = None):
    inputs = {} if event == "push" else {"probe": probe}
    outputs = {"uat-reuse": {"reuse": reuse}, "admit": {"target": target}}
    return graph.Scenario(event, inputs, outputs, outcomes or {})


def _probe(workflow: dict, probe: str, reuse: str, target: str = "test-pypi", outcomes: dict[str, str] | None = None):
    scenario = _scenario("workflow_dispatch", probe, reuse, target, outcomes)
    return scenario, graph.simulate(workflow, scenario)


def _step_text(step: dict) -> str:
    return " ".join(str(step.get(key, "")) for key in ("uses", "run", "env", "with"))


def test_the_workflow_dispatch_input_defaults_to_a_real_release_and_offers_only_the_three_variants(live):
    probe = live["on" if "on" in live else True]["workflow_dispatch"]["inputs"]["probe"]
    assert probe["default"] == "none"
    assert probe["options"] == ["none", "reuse", "fallback", "guarded-change"]
    assert probe["required"] is False


def test_a_tag_push_has_no_probe_input_and_the_only_dispatch_that_probes_must_choose_a_variant(live):
    triggers = live["on" if "on" in live else True]
    assert triggers["push"] == {"tags": ["v*"]}
    assert set(triggers) == {"push", "workflow_dispatch"}


def test_the_probe_guard_runs_before_any_other_probe_or_release_step_in_admission(live):
    steps = live["jobs"]["admit"]["steps"]
    names = [step.get("name") for step in steps]
    guard = names.index("Classify the run and refuse a probe aimed at a real tag")
    assert "release_probe.py guard" in steps[guard]["run"]
    assert "if" not in steps[guard]
    for later in (
        "Require an annotated tag",
        "Verify the pre-tag state",
        "Check ruleset drift without the readiness override",
        "Refuse open release-blocking issues",
        "Admit the trunk-tested artifacts",
        "Build the probe candidate and admitted distributions and admit them",
    ):
        assert guard < names.index(later), later


def test_every_real_admission_gate_runs_in_real_mode_and_every_probe_step_only_in_probe_mode(live):
    steps = live["jobs"]["admit"]["steps"]
    by_name = {step.get("name"): step for step in steps}
    for name in (
        "Require an annotated tag",
        "Verify the pre-tag state",
        "Check ruleset drift without the readiness override",
        "Refuse open release-blocking issues",
        "Require a fresh green release canary on an ancestor of the tagged commit",
        "Admit the trunk-tested artifacts",
    ):
        assert by_name[name]["if"] == REAL_MODE, name
    for name in (
        "Require a fresh green release canary for the probe's synthetic target",
        "Build the probe candidate and admitted distributions and admit them",
        "Upload the probe candidate distributions",
    ):
        assert by_name[name]["if"] == PROBE_MODE, name


def test_a_probe_still_runs_the_canary_freshness_check_through_the_real_script(live):
    steps = live["jobs"]["admit"]["steps"]
    canary = next(
        s for s in steps if s.get("name") == "Require a fresh green release canary for the probe's synthetic target"
    )
    assert "release_probe.py canary" in canary["run"]
    assert "continue-on-error" not in canary
    names = [step.get("name") for step in steps]
    assert names.index(canary["name"]) < names.index(
        "Build the probe candidate and admitted distributions and admit them"
    )


def test_probe_steps_never_read_a_secret_or_the_real_release_tokens(live):
    probe_names = {
        "admit": {
            "Require a fresh green release canary for the probe's synthetic target",
            "Build the probe candidate and admitted distributions and admit them",
        },
        "uat-reuse": {"Decide from the probe's synthetic trunk evidence"},
    }
    for job_name, names in probe_names.items():
        for step in live["jobs"][job_name]["steps"]:
            if step.get("name") in names:
                assert "secrets." not in _step_text(step), step["name"]
                assert "RULESET_DRIFT_TOKEN" not in _step_text(step)


def test_the_dry_run_job_uses_no_environment_and_holds_only_read_permissions(live):
    job = live["jobs"]["publish-probe"]
    assert job["permissions"] == {"contents": "read"}
    assert "environment" not in job
    names = [step.get("name") for step in job["steps"]]
    assert "Stage only the verified distributions" in names
    assert "Verify the admitted SHA256SUMS" in names
    assert "Check the staged distributions the way the publisher would" in names
    text = " ".join(_step_text(step) for step in job["steps"])
    assert "sha256sum -c SHA256SUMS" in text
    assert "twine check" in text
    assert "twine upload" not in text
    assert "pypi" not in text.lower().replace("pypi-", "")


def test_no_workflow_level_permission_grants_a_publishing_scope(live):
    assert live["permissions"] == {"contents": "read"}
    for name, job in live["jobs"].items():
        if name in {"publish", "github-release"}:
            continue
        permissions = job.get("permissions", {})
        assert permissions.get("id-token") != "write", name
        assert permissions.get("attestations") != "write", name
        assert permissions.get("contents") != "write", name


@pytest.mark.parametrize("probe", graph.VARIANTS)
@pytest.mark.parametrize("reuse", ["true", "false"])
@pytest.mark.parametrize("target", graph.TARGETS)
def test_every_probe_variant_reaches_the_dry_run_with_real_jobs_and_never_the_real_publish(live, probe, reuse, target):
    _, runs = _probe(live, probe, reuse, target)
    assert runs["publish-probe"].result == "success"
    assert runs["publish"].result == "skipped"
    assert runs["github-release"].result == "skipped"
    assert runs["release-uat"].result == "success"
    assert runs["install-matrix"].result == "success"
    assert (runs["probe-reused-evidence"].result == "success") == (reuse == "true")
    assert (runs["probe-uat-stub"].result == "success") == (reuse == "false")
    assert runs["uat-reuse"].result == "success"
    assert runs["release-outcome"].result == "success"


@pytest.mark.parametrize("job", ["admit", "install-matrix", "release-uat"])
def test_a_failing_gate_stops_the_probe_before_the_dry_run(live, job):
    _, runs = _probe(live, "reuse", "false", outcomes={job: "failure"})
    assert runs["publish-probe"].result == "skipped"
    assert runs["publish"].result == "skipped"


def test_the_probe_holds_at_most_four_runners_and_one_install_cell(live):
    for probe, reuse in itertools.product(graph.VARIANTS, ("true", "false")):
        _, runs = _probe(live, probe, reuse)
        assert graph.peak_runners(live, runs) <= 4
        assert runs["install-matrix"].cells == 1


def test_the_probe_skips_every_real_uat_shard_and_the_real_publish(live):
    _, runs = _probe(live, "fallback", "false")
    for name in ("uat-stage1", "uat-stage2", "uat-stage3", "publish", "github-release"):
        assert runs[name].result == "skipped", name


def test_the_probe_install_matrix_is_one_linux_python_3_12_cell(live):
    expression = live["jobs"]["install-matrix"]["strategy"]["matrix"]
    matrix = graph.evaluate(expression, {"github": {"event_name": "workflow_dispatch"}, "inputs": {"probe": "reuse"}})
    assert matrix == {"os": ["ubuntu-latest"], "python-version": ["3.12"]}


def test_the_probe_runs_the_release_uat_gate_job_and_its_real_combined_gate_commands(live):
    gate = live["jobs"]["release-uat"]
    text = "\n".join(step.get("run", "") for step in gate["steps"])
    assert "make uat-gate-check" in text
    assert "release_uat_evidence_check.py" in text
    assert "release_reuse_uat_evidence.py verify-gate" in text
    assert set(gate["needs"]) >= {"probe-uat-stub", "probe-reused-evidence"}
    for stub in ("probe-uat-stub", "probe-reused-evidence"):
        assert PROBE_MODE[4:-3] in live["jobs"][stub]["if"]
        assert "uat-sweep" not in " ".join(step.get("run", "") for step in live["jobs"][stub]["steps"])


def _outcome_env(workflow: dict, scenario, runs: dict) -> dict[str, str]:
    needs = {
        name: {"result": run.result, "outputs": scenario.outputs.get(name, {}) if run.result != "skipped" else {}}
        for name, run in runs.items()
    }
    context = {"needs": needs, "github": {"event_name": scenario.event}, "inputs": scenario.inputs}
    step = workflow["jobs"]["release-outcome"]["steps"][0]
    return {name: graph.evaluate_template(str(value), context) for name, value in step["env"].items()}


def _run_outcome(workflow: dict, scenario, runs: dict) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    assert bash is not None
    step = workflow["jobs"]["release-outcome"]["steps"][0]
    env = {"PATH": os.environ["PATH"], **_outcome_env(workflow, scenario, runs)}
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / "step.sh").write_text(step["run"], encoding="utf-8", newline="\n")
        return subprocess.run(
            [bash, "step.sh"], cwd=directory, env=env, capture_output=True, text=True, timeout=30, check=False
        )


def _issue_3947_graph(workflow: dict) -> dict:
    broken = copy.deepcopy(workflow)
    broken["jobs"]["uat-reuse"]["if"] = "${{ needs.admit.outputs.target == 'pypi' }}"
    del broken["jobs"]["publish"]["if"]
    del broken["jobs"]["publish-probe"]["if"]
    return broken


@pytest.mark.parametrize("probe", graph.VARIANTS)
def test_the_issue_3947_graph_turns_the_probe_red_through_the_publish_skipped_detector(live, probe):
    broken = _issue_3947_graph(live)
    scenario, runs = _probe(broken, probe, "false")
    assert runs["uat-reuse"].result == "skipped"
    assert runs["release-uat"].result == "success"
    assert runs["install-matrix"].result == "success"
    assert runs["publish-probe"].result == "skipped"
    outcome = _run_outcome(broken, scenario, runs)
    assert outcome.returncode == 1, outcome.stdout
    assert "publish finished as 'skipped'" in outcome.stdout
    assert runs["release-outcome"].result == "success"


@pytest.mark.parametrize("probe", graph.VARIANTS)
@pytest.mark.parametrize("reuse", ["true", "false"])
def test_the_live_graph_keeps_the_probe_green_in_the_normal_case(live, probe, reuse):
    scenario, runs = _probe(live, probe, reuse)
    outcome = _run_outcome(live, scenario, runs)
    assert outcome.returncode == 0, outcome.stdout + outcome.stderr


def test_a_probe_dry_run_that_did_not_run_fails_the_outcome_even_when_every_other_job_is_green(live):
    scenario, runs = _probe(live, "reuse", "true")
    runs["publish-probe"] = graph.JobRun("skipped", 0)
    outcome = _run_outcome(live, scenario, runs)
    assert outcome.returncode == 1
    assert "publish finished as 'skipped'" in outcome.stdout


def test_the_real_release_outcome_still_reads_the_real_publish_job(live):
    scenario = _scenario("push", "", "false", "pypi")
    runs = graph.simulate(live, scenario)
    env = _outcome_env(live, scenario, runs)
    assert env["PUBLISH_RESULT"] == runs["publish"].result == "success"
    assert _run_outcome(live, scenario, runs).returncode == 0
    runs["publish"] = graph.JobRun("skipped", 0)
    assert _run_outcome(live, scenario, runs).returncode == 1


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_a_real_release_never_runs_a_probe_job(live, event):
    inputs = {} if event == "push" else {"probe": "none"}
    for target in graph.TARGETS:
        outputs = {"uat-reuse": {"reuse": "false"}, "admit": {"target": target}}
        runs = graph.simulate(live, graph.Scenario(event, inputs, outputs))
        for name in ("probe-uat-stub", "probe-reused-evidence", "publish-probe"):
            assert runs[name].result == "skipped", name


def test_a_real_release_runs_publish_after_the_gates_even_when_evidence_is_reused(live):
    for event, inputs in (("push", {}), ("workflow_dispatch", {"probe": "none"})):
        outputs = {"uat-reuse": {"reuse": "true"}, "admit": {"target": "test-pypi"}}
        runs = graph.simulate(live, graph.Scenario(event, inputs, outputs))
        assert runs["publish"].result == "success"
        assert runs["uat-stage1"].result == "skipped"


def test_the_concurrency_group_keeps_a_probe_apart_from_a_release_of_any_tag(live):
    group = live["concurrency"]["group"]
    probing = {
        "github": {"event_name": "workflow_dispatch", "ref_name": "feat/x"},
        "inputs": {"probe": "reuse", "tag": PROBE_TAG},
    }
    releasing = {"github": {"event_name": "push", "ref_name": "v0.5.0"}, "inputs": {}}
    assert graph.evaluate(group, probing) == "release-v2-probe-feat/x-reuse"
    assert graph.evaluate(group, releasing) == "release-v2-v0.5.0"
    assert graph.evaluate(live["concurrency"]["cancel-in-progress"], probing) is True
    assert graph.evaluate(live["concurrency"]["cancel-in-progress"], releasing) is False


def test_the_probe_distributions_are_built_with_the_commands_the_trunk_producer_uses():
    trunk = _workflow(WORKFLOWS / "trunk.yml")["jobs"]["dist-artifact"]
    producer = "\n".join(step.get("run", "") for step in trunk["steps"])
    source = (ROOT / "scripts" / "release_probe.py").read_text(encoding="utf-8")
    for command in (
        "bundled_binary_manifest.py",
        "verify_distribution_binaries.py",
        "verify_sqlglot_runtime_distribution",
    ):
        assert command in producer
        assert command in source
    assert "uv build" in producer
    assert '"uv", "build"' in source


def test_the_dispatch_workflow_runs_on_pull_requests_with_statuses_write_and_never_a_publishing_scope():
    workflow = _workflow(PROBE_WORKFLOW)
    triggers = workflow["on" if "on" in workflow else True]
    assert set(triggers) == {"pull_request"}
    job = workflow["jobs"]["probe"]
    assert job["name"] == "release-v2 probe dispatch"
    assert job["permissions"] == {
        "contents": "read",
        "actions": "write",
        "statuses": "write",
        "pull-requests": "read",
    }
    assert "environment" not in job
    assert job["timeout-minutes"] <= 15
    text = PROBE_WORKFLOW.read_text(encoding="utf-8")
    assert "secrets." not in text
    assert "id-token" not in text
    assert "scripts.release_probe_launch" in text
    assert "release_probe_graph.py" in text


def test_the_launcher_workflow_runs_for_every_pull_request_so_a_required_check_always_reports():
    triggers = _workflow(PROBE_WORKFLOW)
    assert "paths" not in str(triggers["on" if "on" in triggers else True])
    assert "paths-ignore" not in PROBE_WORKFLOW.read_text(encoding="utf-8")
    assert "if" not in triggers["jobs"]["probe"]


def test_the_dispatch_job_never_waits_for_the_probe_runs():
    workflow = _workflow(PROBE_WORKFLOW)
    assert set(workflow["jobs"]) == {"probe"}
    text = PROBE_WORKFLOW.read_text(encoding="utf-8")
    assert "--timeout-seconds" not in text
    assert "wait_for" not in text
    source = (ROOT / "scripts" / "release_probe_launch.py").read_text(encoding="utf-8")
    assert "time.monotonic" not in source
    assert "deadline" not in source


def test_a_fork_pull_request_is_refused_before_any_other_step_and_the_scripts_run_as_modules():
    steps = _workflow(PROBE_WORKFLOW)["jobs"]["probe"]["steps"]
    assert steps[0]["if"] == "${{ github.event.pull_request.head.repo.full_name != github.repository }}"
    assert "exit 1" in steps[0]["run"]
    text = PROBE_WORKFLOW.read_text(encoding="utf-8")
    assert "python3 scripts/release_probe_launch.py" not in text
    assert text.count("python3 -m scripts.release_probe_launch") == 2


def test_the_launch_script_runs_from_the_base_revision_while_the_graph_check_reads_the_head():
    job = _workflow(PROBE_WORKFLOW)["jobs"]["probe"]
    steps = job["steps"]
    checkouts = [step for step in steps if str(step.get("uses", "")).startswith("actions/checkout@")]
    assert [step["with"]["ref"] for step in checkouts] == [
        "${{ github.event.pull_request.head.sha }}",
        "${{ github.event.pull_request.base.sha }}",
    ]
    assert "path" not in checkouts[0]["with"]
    base_path = checkouts[1]["with"]["path"]
    assert base_path
    graph_step = next(step for step in steps if step.get("id") == "graph")
    assert "working-directory" not in graph_step
    assert "scripts/release_probe_graph.py" in graph_step["run"]
    launchers = [step for step in steps if "scripts.release_probe_launch" in step.get("run", "")]
    assert len(launchers) == 2
    assert {step["working-directory"] for step in launchers} == {base_path}
    assert steps.index(checkouts[1]) < steps.index(launchers[0])
    assert "checks" not in job["permissions"]
    assert job["permissions"]["statuses"] == "write"


def test_the_probe_report_job_is_the_last_job_and_needs_every_other_job(live):
    jobs = live["jobs"]
    assert set(jobs["report-probe"]["needs"]) == set(jobs) - {"report-probe"}
    assert not [name for name, job in jobs.items() if "report-probe" in graph.needs_of(job)]
    assert jobs["report-probe"]["name"] == verdict.REPORT_JOB_NAME


def test_the_probe_report_job_holds_only_statuses_write_and_actions_read_and_cannot_publish(live):
    job = live["jobs"]["report-probe"]
    assert job["permissions"] == {"contents": "read", "actions": "read", "statuses": "write"}
    assert "environment" not in job
    assert not graph.job_can_publish(job)
    text = " ".join(_step_text(step) for step in job["steps"])
    assert "secrets." not in text
    assert "id-token" not in text
    assert "scripts.release_probe_verdict" in text
    assert "toJSON(needs)" in text


def test_the_release_outcome_job_is_a_descendant_of_every_job_the_report_judges(live):
    jobs = live["jobs"]
    assert jobs["release-outcome"]["name"] == verdict.OUTCOME_JOB_NAME
    family = graph.ancestors(jobs)
    assert set(jobs) - {"report-probe", "release-outcome"} == family["release-outcome"]


def test_the_dead_workflow_run_verdict_workflow_is_gone():
    assert not (WORKFLOWS / "release-v2-probe-verdict.yml").exists()


@pytest.mark.parametrize("workflow_name", ["release-v2.yml", "trunk-uat.yml"])
@pytest.mark.parametrize(("stage", "cap"), [("uat-stage1", 4), ("uat-stage2", 2), ("uat-stage3", 3)])
def test_every_uat_stage_matrix_caps_its_parallelism(workflow_name, stage, cap):
    strategy = _workflow(WORKFLOWS / workflow_name)["jobs"][stage]["strategy"]
    assert strategy["max-parallel"] == cap
    assert strategy["fail-fast"] is False
