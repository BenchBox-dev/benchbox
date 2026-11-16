from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner
from packaging.version import Version

from benchbox.cli.app import cli
from benchbox.cli.exit_codes import ExitCode

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WORKFLOW = REPO_ROOT / "examples" / "ci" / "perf-regression.yml"
BASELINE_BUILDER = REPO_ROOT / "examples" / "ci" / "build-perf-baseline.sh"
NIGHTLY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "nightly-v2.yml"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "ci_perf_example"
MIN_RELEASE_WITH_EXIT_CODE_CONTRACT = "0.5.2"
MIN_RELEASE_REASON = "compare exit codes 3 and 4 and host-load first ship in 0.5.2; 0.5.1 lacks them"
COMPARE_OPTIONS = ("--fail-on-regression", "--min-regression-delta", "--min-aggregate-regression-delta")


def _load(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _steps(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    jobs = workflow["jobs"]
    assert len(jobs) == 1
    return next(iter(jobs.values()))["steps"]


def _compare_step(workflow: dict[str, Any]) -> dict[str, Any]:
    matches = [step for step in _steps(workflow) if "benchbox compare" in step.get("run", "")]
    assert len(matches) == 1
    return matches[0]


def _workflow_gate_options() -> list[str]:
    workflow = _load(EXAMPLE_WORKFLOW)
    env = {key: str(value) for key, value in workflow["env"].items()}
    script = _compare_step(workflow)["run"].replace("\\\n", " ")
    tokens = shlex.split(script[script.index("benchbox compare") :].splitlines()[0])
    options: list[str] = []
    for index, token in enumerate(tokens):
        if token in COMPARE_OPTIONS:
            value = tokens[index + 1]
            match = re.fullmatch(r"\$([A-Z_]+)", value)
            options.extend([token, env[match.group(1)] if match else value])
    assert options[::2] == list(COMPARE_OPTIONS)
    return options


def _compare(baseline: str, current: str) -> Any:
    return CliRunner().invoke(
        cli,
        ["compare", str(FIXTURES / baseline), str(FIXTURES / current), *_workflow_gate_options(), "--non-interactive"],
    )


def _total_ms(name: str) -> float:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["summary"]["timing"]["total_ms"]


class TestExampleWorkflowFile:
    def test_workflow_has_the_documented_steps(self):
        workflow = _load(EXAMPLE_WORKFLOW)
        steps = _steps(workflow)
        runs = "\n".join(step.get("run", "") for step in steps)
        uses = [step["uses"].split("@")[0] for step in steps if "uses" in step]

        assert uses == ["actions/checkout", "actions/setup-python", "actions/upload-artifact"]
        assert "benchbox run" in runs
        assert "--platform duckdb" in runs
        assert "--benchmark tpch" in runs
        assert "--scale 0.01" in runs
        assert steps[-1]["if"] == "always()"

    def test_actions_are_pinned_to_the_same_shas_as_the_nightly(self):
        nightly_pins = {
            step["uses"].split("@")[0]: step["uses"].split("@")[1]
            for job in _load(NIGHTLY_WORKFLOW)["jobs"].values()
            for step in job.get("steps", [])
            if "uses" in step
        }
        for step in _steps(_load(EXAMPLE_WORKFLOW)):
            if "uses" in step:
                name, _, ref = step["uses"].partition("@")
                assert re.fullmatch(r"[0-9a-f]{40}", ref)
                assert nightly_pins[name] == ref

    def test_gate_options_match_the_nightly_option_shapes(self):
        options = dict(zip(_workflow_gate_options()[::2], _workflow_gate_options()[1::2], strict=True))
        nightly = NIGHTLY_WORKFLOW.read_text(encoding="utf-8")

        assert re.fullmatch(r"\d+%", options["--fail-on-regression"])
        assert re.fullmatch(r"\d+ms", options["--min-regression-delta"])
        assert re.fullmatch(r"\d+ms", options["--min-aggregate-regression-delta"])
        assert "--fail-on-regression 10%" in nightly

    def test_exit_status_branches_use_the_real_compare_exit_codes(self):
        script = _compare_step(_load(EXAMPLE_WORKFLOW))["run"]
        branches = dict(re.findall(r"^\s+(\d|\*)\)\n\s+echo \"::error::([^\n]*)", script, flags=re.MULTILINE))

        assert "regression" in branches[str(int(ExitCode.REGRESSION))].lower()
        assert "not comparable" in branches[str(int(ExitCode.NOT_COMPARABLE))].lower()
        assert "options" in branches[str(int(ExitCode.USAGE))].lower()
        assert "read" in branches[str(int(ExitCode.ERROR))].lower()
        assert "unexpected" in branches["*"].lower()
        assert re.search(rf"^\s+{int(ExitCode.SUCCESS)}\)", script, flags=re.MULTILINE)

    def test_baseline_builder_pins_a_commit_and_does_not_reimplement_the_median(self):
        text = BASELINE_BUILDER.read_text(encoding="utf-8")

        assert re.search(r'^BUILDER_REF="[0-9a-f]{40}"$', text, flags=re.MULTILINE)
        assert "scripts/perf_smoke_baseline.py" in text
        assert "statistics" not in text
        assert (REPO_ROOT / "scripts" / "perf_smoke_baseline.py").exists()

    def test_baseline_builder_replays_only_in_baseline_runs_and_reports_failures(self):
        text = BASELINE_BUILDER.read_text(encoding="utf-8")

        assert "select(.in_baseline) | .workflow_run_id" in text
        assert "replay of run $run_id failed the gate" in text
        assert ">/dev/null" not in text


class TestExampleWorkflowGateAgainstFixtures:
    def test_within_noise_result_passes(self):
        result = _compare("baseline.json", "current-pass.json")

        assert result.exit_code == ExitCode.SUCCESS, result.output
        assert "No performance regression" in result.output

    def test_single_query_regression_fails_with_the_regression_exit_code(self):
        result = _compare("baseline.json", "current-query-regressed.json")

        assert result.exit_code == ExitCode.REGRESSION, result.output
        assert "Performance regression detected" in result.output
        assert _total_ms("current-query-regressed.json") - _total_ms("baseline.json") == pytest.approx(60.0)

    def test_aggregate_only_regression_fails_with_the_regression_exit_code(self):
        result = _compare("baseline.json", "current-aggregate-regressed.json")

        assert result.exit_code == ExitCode.REGRESSION, result.output
        assert "Performance regression detected" in result.output
        assert _total_ms("current-aggregate-regressed.json") - _total_ms("baseline.json") == pytest.approx(87.7)

    def test_small_query_that_doubles_stays_under_the_per_query_floor(self):
        result = _compare("baseline.json", "current-small-query-doubles.json")

        assert result.exit_code == ExitCode.SUCCESS, result.output
        assert "4.40 ms" in result.output
        assert "8.80 ms" in result.output
        assert "+100.00%" in result.output
        assert _total_ms("current-small-query-doubles.json") - _total_ms("baseline.json") == pytest.approx(13.8)

    def test_gate_fails_only_with_the_gate_options(self):
        result = CliRunner().invoke(
            cli,
            [
                "compare",
                str(FIXTURES / "baseline.json"),
                str(FIXTURES / "current-aggregate-regressed.json"),
                "--non-interactive",
            ],
        )

        assert result.exit_code == ExitCode.SUCCESS, result.output


def test_example_pins_a_release_that_ships_the_exit_code_contract() -> None:
    pinned = Version(str(_load(EXAMPLE_WORKFLOW)["env"]["BENCHBOX_VERSION"]))
    assert pinned >= Version(MIN_RELEASE_WITH_EXIT_CODE_CONTRACT), MIN_RELEASE_REASON
