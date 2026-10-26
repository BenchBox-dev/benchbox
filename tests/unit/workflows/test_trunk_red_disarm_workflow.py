from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]


def _workflow() -> dict:
    return yaml.safe_load((ROOT / ".github/workflows/trunk-red-disarm.yml").read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    return _workflow()["jobs"]["disarm"]["steps"]


def test_listens_only_for_completed_trunk_runs() -> None:
    assert _workflow()[True] == {"workflow_run": {"workflows": ["Trunk"], "types": ["completed"]}}


def test_runs_only_for_develop_pushes() -> None:
    condition = _workflow()["jobs"]["disarm"]["if"]
    assert "workflow_run.event == 'push'" in condition
    assert "workflow_run.head_branch == 'develop'" in condition


def test_permissions_are_exactly_the_disarm_set() -> None:
    assert _workflow()["permissions"] == {"pull-requests": "write", "actions": "read", "contents": "write"}


def test_the_disarm_step_authenticates_gh_with_the_workflow_token() -> None:
    step = next(step for step in _steps() if "trunk_red_disarm.py" in step.get("run", ""))
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"


def test_checkout_has_full_history_for_the_fix_forward_path_check() -> None:
    checkout = next(step for step in _steps() if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == 0
    assert "persist-credentials" not in checkout["with"]


def test_no_secrets_are_referenced() -> None:
    assert "secrets." not in (ROOT / ".github/workflows/trunk-red-disarm.yml").read_text(encoding="utf-8")
