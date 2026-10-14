from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "site-inputs.yml"


def _text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_parent_resolution_invokes_tested_script() -> None:
    assert "scripts/site_inputs_parent.py" in _text()


def test_parent_resolution_has_no_inline_site_inputs_run_list() -> None:
    assert "gh run list --workflow site-inputs.yml" not in _text()


def test_certification_has_no_inline_trunk_run_list() -> None:
    assert "gh run list --workflow trunk.yml" not in _text()
    assert "certified_by=${RUN_ID" not in _text()
    assert "--run-id" in _text()


def test_workflow_keeps_dispatch_parent_input_and_bundle_outputs() -> None:
    workflow = yaml.safe_load(_text())
    triggers = workflow.get("on") or workflow.get(True)
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert "parent_core_sha" in inputs
    resolve = None
    for step in workflow["jobs"]["build"]["steps"]:
        if step.get("id") == "shas":
            resolve = step
            break
    assert resolve is not None
    assert resolve["env"]["DISPATCH_PARENT"] == "${{ inputs.parent_core_sha }}"
    assert "scripts/site_inputs_parent.py" in str(resolve.get("run", ""))
