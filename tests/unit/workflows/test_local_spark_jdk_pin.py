from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"
ENV_ACTION = Path(__file__).resolve().parents[3] / ".github" / "actions" / "release-uat-env" / "action.yml"

PINNED_JOBS = {
    "ci.yml": {"code-test", "medium-test", "correctness-gate"},
    "trunk-coverage.yml": {"medium-test"},
    "trunk.yml": {"fast", "medium-test", "correctness-gate", "required-local-cases"},
    "nightly-v2.yml": {"quarantine", "durations-refresh", "integration-smoke", "integration"},
}


def _java_step(steps: list[dict]) -> dict | None:
    return next((step for step in steps if str(step.get("uses", "")).startswith("actions/setup-java@")), None)


def _release_env_java_pin() -> str:
    step = _java_step(yaml.safe_load(ENV_ACTION.read_text(encoding="utf-8"))["runs"]["steps"])
    assert step is not None
    return step["uses"]


@pytest.mark.parametrize(("workflow", "job"), [(wf, job) for wf, jobs in PINNED_JOBS.items() for job in sorted(jobs)])
def test_jobs_running_local_spark_tests_pin_temurin_21(workflow: str, job: str):
    steps = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))["jobs"][job]["steps"]
    java = _java_step(steps)

    assert java is not None, f"{workflow}:{job} relies on the runner default Java"
    assert java["uses"] == _release_env_java_pin()
    assert java["with"] == {"distribution": "temurin", "java-version": "21"}
    names = [step.get("name", "") for step in steps]
    assert names.index(java["name"]) < next(i for i, name in enumerate(names) if name.startswith("Install uv"))
