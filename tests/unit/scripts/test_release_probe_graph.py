from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
SPEC = importlib.util.spec_from_file_location("release_probe_graph_under_test", SCRIPTS / "release_probe_graph.py")
assert SPEC is not None and SPEC.loader is not None
graph = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = graph
SPEC.loader.exec_module(graph)


def _evaluate(expression: str, context: dict | None = None, **status: bool):
    return graph.evaluate(expression, context or {}, {"success": True, "failure": False, "cancelled": False, **status})


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1 == 1", True),
        ("'a' == 'A'", True),
        ("'a' != 'b'", True),
        ("null == ''", True),
        ("true && 'x'", "x"),
        ("false || 'y'", "y"),
        ("!false", True),
        ("(true || false) && !true", False),
        ("contains('Release v2', 'v2')", True),
        ("startsWith('release/0.5.x', 'release/')", True),
        ("format('{0}-{1}', 'a', 'b')", "a-b"),
        ('fromJSON(\'{"os": ["x"]}\').os[0]', "x"),
        ("2 < 10", True),
    ],
)
def test_expressions_follow_the_github_semantics_the_workflow_relies_on(expression, expected):
    assert _evaluate(expression) == expected


def test_a_missing_context_property_is_null_and_compares_equal_to_empty():
    assert _evaluate("inputs.probe == ''", {"inputs": {}}) is True
    assert _evaluate("inputs.probe != 'none'", {"inputs": {}}) is True
    assert _evaluate("needs.a.outputs.reuse != 'true'", {"needs": {}}) is True


def test_status_functions_read_the_supplied_ancestor_state():
    assert _evaluate("success()") is True
    assert _evaluate("success()", success=False) is False
    assert _evaluate("failure()", failure=True) is True
    assert _evaluate("!cancelled()") is True


def test_a_wrapped_template_and_a_bare_expression_parse_alike():
    assert graph.parse("${{ 1 == 1 }}") == graph.parse("1 == 1")
    assert graph.evaluate_template("a-${{ 'b' }}-c", {}) == "a-b-c"


def test_unsupported_syntax_is_an_error_not_a_silent_false():
    with pytest.raises(graph.ExpressionError):
        graph.parse("1 +")
    with pytest.raises(graph.ExpressionError):
        _evaluate("hashFiles('x')")


WORKFLOW = {
    "jobs": {
        "gate": {},
        "skipped": {"needs": "gate", "if": "${{ false }}"},
        "implicit": {"needs": ["gate", "skipped"]},
        "explicit": {"needs": ["gate", "skipped"], "if": "${{ !cancelled() && needs.gate.result == 'success' }}"},
        "always-run": {"needs": ["skipped"], "if": "${{ always() }}"},
        "matrix": {"needs": "gate", "strategy": {"matrix": {"os": ["a", "b"], "py": [1, 2, 3]}}},
        "include": {"needs": "gate", "strategy": {"matrix": {"include": [{"s": 1}, {"s": 2}]}}},
        "excluded": {
            "needs": "gate",
            "strategy": {"matrix": {"os": ["a", "b"], "py": [1, 2], "exclude": [{"os": "a", "py": 1}]}},
        },
    }
}


def _run(workflow=WORKFLOW, **kwargs):
    return graph.simulate(workflow, graph.Scenario("push", **kwargs))


def test_a_job_with_a_skipped_ancestor_is_skipped_unless_its_condition_names_a_status_function():
    runs = _run()
    assert runs["skipped"].result == "skipped"
    assert runs["implicit"].result == "skipped"
    assert runs["explicit"].result == "success"
    assert runs["always-run"].result == "success"


def test_a_failed_ancestor_skips_a_plain_dependent_but_not_an_always_job():
    runs = _run(outcomes={"gate": "failure"})
    assert runs["matrix"].result == "skipped"
    assert runs["explicit"].result == "skipped"
    assert runs["always-run"].result == "success"


def test_matrix_expansion_counts_axes_includes_and_excludes():
    runs = _run()
    assert runs["matrix"].cells == 6
    assert runs["include"].cells == 2
    assert runs["excluded"].cells == 3


def test_peak_runners_counts_only_jobs_that_can_overlap():
    runs = _run()
    assert graph.peak_runners(WORKFLOW, runs) == 6 + 2 + 3 + 1 + 1


def test_the_live_workflow_satisfies_every_publish_gating_invariant():
    assert graph.check(graph.load_workflow(graph.DEFAULT_WORKFLOW)) == []


def test_the_live_probe_never_holds_more_than_four_runners():
    workflow = graph.load_workflow(graph.DEFAULT_WORKFLOW)
    for variant in graph.VARIANTS:
        for reuse in ("true", "false"):
            outputs = {"uat-reuse": {"reuse": reuse}, "admit": {"target": "test-pypi"}}
            runs = graph.simulate(workflow, graph.Scenario("workflow_dispatch", {"probe": variant}, outputs))
            assert graph.peak_runners(workflow, runs) <= 4


def test_the_live_probe_runs_the_install_matrix_as_one_cell():
    workflow = graph.load_workflow(graph.DEFAULT_WORKFLOW)
    outputs = {"uat-reuse": {"reuse": "false"}, "admit": {"target": "test-pypi"}}
    runs = graph.simulate(workflow, graph.Scenario("workflow_dispatch", {"probe": "reuse"}, outputs))
    assert runs["install-matrix"].cells == 1


def test_a_real_release_keeps_the_full_install_matrix_and_every_uat_shard():
    workflow = graph.load_workflow(graph.DEFAULT_WORKFLOW)
    outputs = {"uat-reuse": {"reuse": "false"}, "admit": {"target": "pypi"}}
    runs = graph.simulate(workflow, graph.Scenario("push", {}, outputs))
    assert runs["install-matrix"].cells == 12
    assert (runs["uat-stage1"].cells, runs["uat-stage2"].cells, runs["uat-stage3"].cells) == (9, 3, 4)
    assert runs["publish"].result == "success"
    assert runs["publish-probe"].result == "skipped"
    assert runs["probe-uat-stub"].result == "skipped"


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


def test_the_dry_run_job_as_written_has_no_publishing_capability(live):
    assert not graph.job_can_publish(live["jobs"]["publish-probe"])
    assert "publish-probe" not in graph.publishing_jobs(live)


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
