from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github/workflows"


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    triggers = workflow[True] if True in workflow else workflow["on"]
    if isinstance(triggers, str):
        return {triggers: None}
    if isinstance(triggers, list):
        return {str(name): None for name in triggers}
    return triggers


def _branch_filter(trigger: Any) -> set[str] | None:
    branches = (trigger or {}).get("branches")
    return None if branches is None else set(branches)


def _matches_all(branches: set[str] | None) -> bool:
    return branches is not None and "**" in branches


def _dispatches_twice(triggers: dict[str, Any]) -> bool:
    if "push" not in triggers or "pull_request" not in triggers:
        return False
    push_branches = _branch_filter(triggers["push"])
    pull_branches = _branch_filter(triggers["pull_request"])
    if push_branches is None or pull_branches is None:
        return True
    if push_branches != pull_branches:
        return True
    return _matches_all(push_branches)


def test_no_workflow_dispatches_twice_for_one_push_to_a_pull_request_branch() -> None:
    offenders = [path.name for path in sorted(WORKFLOWS.glob("*.yml")) if _dispatches_twice(_triggers(_load(path)))]
    assert offenders == []


@pytest.mark.parametrize(
    ("push", "pull_request", "expected"),
    [
        ({"branches": ["**"]}, {"branches": ["**"]}, True),
        ({"branches": ["**"]}, {"branches": ["develop"]}, True),
        ({"branches": ["develop"]}, {"branches": ["**"]}, True),
        (None, {"branches": ["develop"]}, True),
        ({"branches": ["release"]}, {"branches": ["release"]}, False),
        ({"branches": ["release"]}, {"branches": ["develop"]}, True),
        ({"branches": ["develop"]}, None, True),
    ],
)
def test_duplicate_dispatch_predicate_flags_cross_line_and_wildcard_branch_filters(
    push: Any, pull_request: Any, expected: bool
) -> None:
    assert _dispatches_twice({"push": push, "pull_request": pull_request}) is expected


def test_pull_request_workflows_cancel_superseded_runs_per_pull_request_or_ref() -> None:
    offenders = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        workflow = _load(path)
        if "pull_request" not in _triggers(workflow):
            continue
        concurrency = workflow.get("concurrency")
        if not isinstance(concurrency, dict) or concurrency.get("cancel-in-progress") is not True:
            offenders.append(path.name)
            continue
        group = str(concurrency["group"])
        if not any(key in group for key in ("github.ref", "pull_request.number")):
            offenders.append(path.name)
    assert offenders == []
