from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "batch-loop-e2e.yml"
E2E_FILE = "tests/integration/worktree/test_batch_loop_e2e.py"


def _load() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers() -> dict[str, Any]:
    workflow = _load()
    return workflow.get("on", workflow.get(True))


def test_lane_runs_on_batch_tooling_changes_and_on_a_schedule() -> None:
    triggers = _triggers()
    assert {"pull_request", "schedule"} <= set(triggers)
    paths = triggers["pull_request"]["paths"]
    for tooling in ("scripts/batch_integration.py", "scripts/theseus_cli.py", "scripts/pr_landing.py", E2E_FILE):
        assert tooling in paths


def test_every_listed_path_exists_or_is_a_glob() -> None:
    for path in _triggers()["pull_request"]["paths"]:
        assert "*" in path or (ROOT / path).exists(), path


def test_lane_runs_the_slow_end_to_end_file_and_fails_when_it_fails() -> None:
    job = _load()["jobs"]["batch-loop-e2e"]
    runs = [step for step in job["steps"] if E2E_FILE in step.get("run", "")]
    assert len(runs) == 1
    step = runs[0]
    assert "-m slow" in step["run"] and "-n 0" in step["run"]
    assert not step.get("continue-on-error")
    assert "|| true" not in step["run"]
    assert not job.get("continue-on-error")
