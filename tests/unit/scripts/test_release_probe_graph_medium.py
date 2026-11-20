from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
SPEC = importlib.util.spec_from_file_location("release_probe_graph_under_test", SCRIPTS / "release_probe_graph.py")
assert SPEC is not None and SPEC.loader is not None
graph = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = graph
SPEC.loader.exec_module(graph)


def test_the_live_workflow_satisfies_every_publish_gating_invariant():
    assert graph.check(graph.load_workflow(graph.DEFAULT_WORKFLOW)) == []


@pytest.fixture
def live():
    return graph.load_workflow(graph.DEFAULT_WORKFLOW)


def test_the_gating_check_reports_a_publish_job_that_loses_its_explicit_status_condition(live):
    broken = copy.deepcopy(live)
    del broken["jobs"]["publish"]["if"]
    messages = graph.check(broken)
    assert any(message.startswith("publish is skipped after green") for message in messages)


def test_the_gating_check_reports_a_publish_job_that_runs_after_a_failed_gate(live):
    broken = copy.deepcopy(live)
    broken["jobs"]["publish"]["if"] = "${{ !cancelled() && needs.admit.result == 'success' }}"
    assert any("runs although a gate did not succeed" in message for message in graph.check(broken))


def test_the_probe_check_reports_a_publishing_job_that_can_run_in_probe_mode(live):
    broken = copy.deepcopy(live)
    broken["jobs"]["publish"]["if"] = "${{ !cancelled() && needs.admit.result == 'success' }}"
    assert "publish can run in probe mode although it can publish" in graph.check(broken)


@pytest.mark.parametrize("job", ["github-release", "publish"])
def test_every_job_that_can_publish_must_be_unreachable_in_a_probe(live, job):
    broken = copy.deepcopy(live)
    broken["jobs"][job]["if"] = "${{ !cancelled() }}"
    assert any(f"{job} can run in probe mode" in message for message in graph.check(broken))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda job: job.update(permissions={"contents": "read", "id-token": "write"}),
        lambda job: job.update(permissions={"contents": "read", "attestations": "write"}),
        lambda job: job.update(permissions={"contents": "write"}),
        lambda job: job.update(environment="pypi"),
        lambda job: job["steps"].append({"uses": "pypa/gh-action-pypi-publish@abc"}),
        lambda job: job["steps"].append({"uses": "actions/attest-build-provenance@abc"}),
        lambda job: job["steps"].append({"run": "uvx twine upload staged/*"}),
        lambda job: job["steps"].append({"run": "gh release create v1"}),
        lambda job: job["steps"].append({"run": "git push origin HEAD"}),
    ],
)
def test_the_dry_run_job_is_flagged_the_moment_it_gains_any_publishing_capability(live, mutation):
    broken = copy.deepcopy(live)
    mutation(broken["jobs"]["publish-probe"])
    assert graph.job_can_publish(broken["jobs"]["publish-probe"])
    assert "publish-probe can run in probe mode although it can publish" in graph.check(broken)


def test_the_dry_run_job_must_exist_and_run_after_green_gates(live):
    missing = copy.deepcopy(live)
    del missing["jobs"]["publish-probe"]
    assert any("no publish-probe job" in message for message in graph.check(missing))
    gated = copy.deepcopy(live)
    gated["jobs"]["publish-probe"]["if"] = "${{ needs.admit.result == 'success' }}"
    assert any("publish-probe is skipped after green gates" in message for message in graph.check(gated))


def test_the_main_command_fails_on_a_broken_workflow(tmp_path, live, capsys):
    import yaml

    broken = copy.deepcopy(live)
    del broken["jobs"]["publish"]["if"]
    path = tmp_path / "release-v2.yml"
    path.write_text(yaml.safe_dump(broken), encoding="utf-8")
    assert graph.main(["--workflow", str(path)]) == 1
    assert "publish is skipped after green" in capsys.readouterr().err
    assert graph.main([]) == 0
