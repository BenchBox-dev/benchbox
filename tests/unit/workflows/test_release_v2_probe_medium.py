from __future__ import annotations

import copy
import itertools
from pathlib import Path

import pytest
import yaml

from scripts import release_probe_graph as graph

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github" / "workflows"
RELEASE = WORKFLOWS / "release-v2.yml"
PUBLISH_ACTIONS = ("pypa/gh-action-pypi-publish", "actions/attest")


def _workflow(path: Path = RELEASE) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture
def live() -> dict:
    return _workflow()


def _reachable_in_probe(workflow: dict) -> set[str]:
    reachable: set[str] = set()
    for probe, reuse, target in itertools.product(graph.VARIANTS, ("true", "false"), graph.TARGETS):
        for scenario in graph.scenarios(workflow, "workflow_dispatch", {"probe": probe}):
            if scenario.outputs["uat-reuse"]["reuse"] != reuse or scenario.outputs["admit"]["target"] != target:
                continue
            reachable |= {name for name, run in graph.simulate(workflow, scenario).items() if run.result != "skipped"}
    return reachable


def test_no_job_that_runs_in_a_probe_can_publish_attest_or_release(live):
    reachable = _reachable_in_probe(live)
    assert "publish-probe" in reachable
    assert not reachable & {"publish", "github-release"}
    for name in reachable:
        job = live["jobs"][name]
        permissions = job.get("permissions", live["permissions"])
        assert permissions.get("id-token") != "write", name
        assert permissions.get("attestations") != "write", name
        assert permissions.get("contents") != "write", name
        assert "environment" not in job, name
        for step in job["steps"]:
            assert not any(action in step.get("uses", "") for action in PUBLISH_ACTIONS), (name, step.get("name"))
            for command in ("twine upload", "gh release", "git push", "gh attestation"):
                assert command not in step.get("run", ""), (name, step.get("name"), command)


def _issue_3947_graph(workflow: dict) -> dict:
    broken = copy.deepcopy(workflow)
    broken["jobs"]["uat-reuse"]["if"] = "${{ needs.admit.outputs.target == 'pypi' }}"
    del broken["jobs"]["publish"]["if"]
    del broken["jobs"]["publish-probe"]["if"]
    return broken


def test_the_issue_3947_graph_is_also_caught_by_the_static_graph_check(live):
    messages = graph.check(_issue_3947_graph(live))
    assert any("publish is skipped after green" in message for message in messages)
    assert any("publish-probe is skipped after green" in message for message in messages)


def test_the_probe_report_job_runs_only_in_probe_mode_and_even_after_a_failure(live):
    job = live["jobs"]["report-probe"]
    assert job["if"] == "${{ always() && github.event_name == 'workflow_dispatch' && inputs.probe != 'none' }}"
    for event, inputs in (("push", {}), ("workflow_dispatch", {"probe": "none"})):
        for scenario in graph.scenarios(live, event, inputs):
            assert graph.simulate(live, scenario)["report-probe"].result == "skipped"
    for probe in graph.VARIANTS:
        for scenario in graph.scenarios(live, "workflow_dispatch", {"probe": probe}):
            assert graph.simulate(live, scenario)["report-probe"].result != "skipped"


def test_the_graph_check_rejects_a_missing_or_misplaced_report_job(live):
    assert graph.probe_report_violations(live) == []
    missing = copy.deepcopy(live)
    del missing["jobs"]["report-probe"]
    assert graph.probe_report_violations(missing)
    partial = copy.deepcopy(live)
    partial["jobs"]["report-probe"]["needs"] = ["admit"]
    assert any("does not need" in problem for problem in graph.probe_report_violations(partial))
    always = copy.deepcopy(live)
    always["jobs"]["report-probe"]["if"] = "${{ always() }}"
    assert any("outside probe mode" in problem for problem in graph.probe_report_violations(always))
    never = copy.deepcopy(live)
    never["jobs"]["report-probe"]["if"] = "${{ success() && inputs.probe != 'none' }}"
    assert any("skipped in probe mode" in problem for problem in graph.probe_report_violations(never))
