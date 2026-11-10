from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.utilities.posix_shell import run_posix_shell, skip_without_posix_shell

pytestmark = [pytest.mark.unit, pytest.mark.medium]

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "ci.yml"


def _guard_step() -> dict[str, Any]:
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    return next(step for step in jobs["ci-paths"]["steps"] if step.get("name") == "Check base branch")


def _run_guard(base_ref: str, *, draft: bool) -> subprocess.CompletedProcess[str]:
    skip_without_posix_shell()
    return run_posix_shell(
        _guard_step()["run"],
        env={"PATH": "/usr/bin:/bin", "BASE_REF": base_ref, "IS_DRAFT": "true" if draft else "false"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


@pytest.mark.parametrize("draft", [True, False])
@pytest.mark.parametrize("base", ["release/0.5.x", "release/10.12.x"])
def test_a_maintenance_line_is_an_integration_base(base: str, draft: bool) -> None:
    result = _run_guard(base, draft=draft)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "maintenance line" in result.stdout


@pytest.mark.parametrize(
    "base", ["release", "release/0.5", "release/0.5.x/extra", "release/a.b.x", "release/0.5.y", "xrelease/0.5.x"]
)
def test_only_a_well_formed_maintenance_line_passes_a_ready_pr(base: str) -> None:
    result = _run_guard(base, draft=False)
    assert result.returncode != 0
    assert "::error::" in result.stdout and base in result.stdout


def test_the_base_error_names_the_maintenance_lines() -> None:
    result = _run_guard("fix/parent-branch", draft=False)
    assert "release/X.Y.x" in result.stdout
