from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOW_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "measurement.yml"


def test_piped_measurement_steps_fail_when_the_harness_fails() -> None:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    default_shell = workflow.get("defaults", {}).get("run", {}).get("shell")
    piped = [step for job in workflow["jobs"].values() for step in job["steps"] if "| tee" in step.get("run", "")]

    assert piped
    for step in piped:
        assert step.get("shell", default_shell) == "bash", step["name"]
