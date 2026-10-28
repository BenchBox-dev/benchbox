from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pr-hold.yml"
RUNBOOK = REPO_ROOT / "docs" / "internal" / "repo-admin-settings.md"


def _load() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _hold_label() -> str:
    source = (REPO_ROOT / "scripts" / "pr_landing.py").read_text(encoding="utf-8")
    match = re.search(r'^HOLD_LABEL = "([^"]+)"$', source, re.MULTILINE)
    assert match is not None
    return match.group(1)


def test_hold_check_reevaluates_on_label_changes_and_new_heads() -> None:
    workflow = _load()
    triggers = workflow.get("on", workflow.get(True))
    types = set(triggers["pull_request"]["types"])
    assert {"labeled", "unlabeled", "synchronize", "opened", "reopened"} <= types
    assert "branches" not in triggers["pull_request"], "a branch filter would leave some PRs without a hold result"


def test_hold_job_reports_under_the_required_context_name() -> None:
    job = _load()["jobs"]["hold"]
    assert job["name"] == "hold"


def test_hold_job_checks_the_label_that_arming_refuses() -> None:
    step = _load()["jobs"]["hold"]["steps"][0]
    labels = re.findall(r"labels\.\*\.name, '([^']+)'", step["env"]["HELD"])
    assert labels == [_hold_label()]
    assert "exit 1" in step["run"]


def test_runbook_requires_the_hold_context() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    block = text.split("Required status checks:", 1)[1].split("```", 2)[1]
    assert "- hold" in block.splitlines()


@pytest.mark.skipif(sys.platform == "win32", reason="the hold step is a POSIX shell script that runs on ubuntu")
@pytest.mark.parametrize(("held", "expected_code"), [("true", 1), ("false", 0)])
def test_hold_step_fails_only_while_the_label_is_present(held: str, expected_code: int, tmp_path: Path) -> None:
    script = tmp_path / "hold.sh"
    script.write_text("#!/bin/sh\n" + _load()["jobs"]["hold"]["steps"][0]["run"], encoding="utf-8")
    script.chmod(0o755)
    result = subprocess.run(
        [str(script)],
        env={**os.environ, "HELD": held},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == expected_code
    assert ("::error::" in result.stdout) is (held == "true")
