from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.utilities.posix_shell import run_posix_shell, skip_without_posix_shell

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "nightly-v2.yml"
FINAL_STEP = "Require successful sampled tests"

PASSING = '<testsuite tests="1"><testcase classname="tests.unit.test_ok" name="test_ok" time="0.1"/></testsuite>'
FAILING = (
    '<testsuite tests="2">'
    '<testcase classname="tests.unit.test_ok" name="test_ok" time="0.1"/>'
    '<testcase classname="tests.slow.test_bad" name="test_broken[param]" time="0.2"><failure message="x"/></testcase>'
    '<testcase classname="tests.slow.test_err" name="test_errored" time="0.2"><error message="y"/></testcase>'
    "</testsuite>"
)


def _durations_steps() -> list[dict[str, Any]]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]["durations-refresh"]["steps"]


def _final_step() -> dict[str, Any]:
    return next(step for step in _durations_steps() if step.get("name") == FINAL_STEP)


def _run_final(
    tmp_path: Path, verdicts: dict[str, str | None], junit: dict[str, str | None]
) -> subprocess.CompletedProcess[str]:
    skip_without_posix_shell()
    output = tmp_path / "t3-durations"
    output.mkdir()
    for tier in ("fast", "slow"):
        if verdicts.get(tier) is not None:
            (output / f"pytest-{tier}.exit").write_text(verdicts[tier], encoding="utf-8")
        if junit.get(tier) is not None:
            (output / f"junit-{tier}.xml").write_text(junit[tier], encoding="utf-8")
    return run_posix_shell(
        'set -eo pipefail\nexport PATH="$PWD:$PATH"\n' + _final_step()["run"],
        cwd=tmp_path,
        env={**os.environ},
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_final_step_runs_after_the_artifact_upload_even_on_failure() -> None:
    steps = _durations_steps()
    upload = next(step for step in steps if step.get("name") == "Upload duration artifacts")
    assert steps.index(_final_step()) > steps.index(upload)
    assert "always()" in _final_step()["if"] and "always()" in upload["if"]


def test_exit_zero_passes_without_a_warning(tmp_path: Path) -> None:
    result = _run_final(tmp_path, {"fast": "0", "slow": "0"}, {"fast": PASSING, "slow": PASSING})

    assert result.returncode == 0, result.stdout + result.stderr
    assert "::warning::" not in result.stdout


@pytest.mark.parametrize("failing_tier", ["fast", "slow"])
def test_exit_one_passes_and_warns_with_the_failing_test_ids(tmp_path: Path, failing_tier: str) -> None:
    other = "slow" if failing_tier == "fast" else "fast"
    result = _run_final(
        tmp_path,
        {failing_tier: "1", other: "0"},
        {failing_tier: FAILING, other: PASSING},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    warnings = [line for line in result.stdout.splitlines() if line.startswith("::warning::")]
    assert len(warnings) == 1
    assert failing_tier in warnings[0]
    assert "tests.slow.test_bad::test_broken[param]" in warnings[0]
    assert "tests.slow.test_err::test_errored" in warnings[0]
    assert "tests.unit.test_ok" not in warnings[0]
    assert "::error::" not in result.stdout


@pytest.mark.parametrize("exit_code", ["2", "3", "4", "5", "137", "invalid", ""])
@pytest.mark.parametrize("tier", ["fast", "slow"])
def test_pytest_exit_above_one_or_malformed_fails(tmp_path: Path, tier: str, exit_code: str) -> None:
    other = "slow" if tier == "fast" else "fast"
    result = _run_final(tmp_path, {tier: exit_code, other: "0"}, {"fast": PASSING, "slow": PASSING})

    assert result.returncode != 0, result.stdout + result.stderr
    assert "::error::" in result.stdout


@pytest.mark.parametrize("missing_tier", ["fast", "slow"])
def test_a_missing_verdict_fails_closed(tmp_path: Path, missing_tier: str) -> None:
    present = "slow" if missing_tier == "fast" else "fast"
    result = _run_final(tmp_path, {present: "0"}, {"fast": PASSING, "slow": PASSING})

    assert result.returncode != 0, result.stdout + result.stderr
    assert f"{missing_tier} duration sample has no pytest verdict" in result.stdout


@pytest.mark.parametrize("verdict", ["0", "1"])
@pytest.mark.parametrize("missing_tier", ["fast", "slow"])
def test_a_missing_junit_report_fails(tmp_path: Path, missing_tier: str, verdict: str) -> None:
    present = "slow" if missing_tier == "fast" else "fast"
    result = _run_final(tmp_path, {"fast": verdict, "slow": verdict}, {present: PASSING})

    assert result.returncode != 0, result.stdout + result.stderr
    assert f"{missing_tier} duration sample has no JUnit report" in result.stdout


@pytest.mark.parametrize("verdict", ["0", "1"])
def test_an_unparseable_junit_report_fails(tmp_path: Path, verdict: str) -> None:
    result = _run_final(tmp_path, {"fast": "0", "slow": verdict}, {"fast": PASSING, "slow": "<testsuite"})

    assert result.returncode != 0, result.stdout + result.stderr
    assert "slow duration sample has an unparseable JUnit report" in result.stdout
