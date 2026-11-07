from __future__ import annotations

import importlib.util
import re
import sys
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


def _load_support() -> Any:
    path = ROOT / "tests" / "integration" / "worktree" / "batch_loop_support.py"
    spec = importlib.util.spec_from_file_location("batch_loop_support_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _glob_regex(pattern: str) -> re.Pattern[str]:
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def _covered(path: str, patterns: list[str]) -> bool:
    probe = f"{path}/probe" if (ROOT / path).is_dir() else path
    return any(_glob_regex(pattern).fullmatch(probe) for pattern in patterns)


def test_trigger_paths_cover_every_file_the_world_copies() -> None:
    patterns = _triggers()["pull_request"]["paths"]
    uncovered = [name for name in _load_support().TRACKED_COPY if not _covered(name, patterns)]
    assert not uncovered, uncovered


def test_trigger_matcher_follows_github_glob_semantics() -> None:
    assert _covered("make", ["make/**"])
    assert not _covered("make", ["make/*.mk"])
    assert not _covered("scripts/pr_landing.py", ["scripts/*/pr_landing.py"])
    assert not _covered("scripts/pr_landing.py", ["scripts/pr_landing.pyc"])
