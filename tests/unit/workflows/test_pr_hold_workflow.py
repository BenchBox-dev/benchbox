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
    assert step["env"]["HOLD_LABEL"] == _hold_label()
    assert "exit 1" in step["run"]


def test_hold_job_reads_live_labels_with_least_privilege() -> None:
    workflow = _load()
    step = workflow["jobs"]["hold"]["steps"][0]
    assert workflow["permissions"] == {"pull-requests": "read"}
    assert "permissions" not in workflow["jobs"]["hold"]
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert "github.event.pull_request.labels" not in WORKFLOW.read_text(encoding="utf-8")


def test_runbook_requires_the_hold_context() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    block = text.split("Required status checks:", 1)[1].split("```", 2)[1]
    assert "- hold" in block.splitlines()


FAKE_GH = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_GH_LOG"
if [ "${FAKE_GH_FAIL:-}" = "1" ]; then
  echo "gh: HTTP 502" >&2
  exit 1
fi
printf '%s' "${FAKE_GH_LABELS:-}"
"""

EVENT_ENV = {"GH_REPO": "BenchBox-dev/BenchBox", "PR_NUMBER": "42", "GH_TOKEN": "token"}


def _run_hold_step(tmp_path: Path, *, labels: str = "", gh_fails: bool = False) -> subprocess.CompletedProcess[str]:
    step = _load()["jobs"]["hold"]["steps"][0]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8")
    gh.chmod(0o755)
    script = tmp_path / "hold.sh"
    script.write_text("#!/bin/sh\n" + step["run"], encoding="utf-8")
    script.chmod(0o755)
    env = {
        **os.environ,
        **EVENT_ENV,
        "HOLD_LABEL": step["env"]["HOLD_LABEL"],
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_GH_LOG": str(tmp_path / "gh.log"),
        "FAKE_GH_LABELS": labels,
        "FAKE_GH_FAIL": "1" if gh_fails else "0",
    }
    return subprocess.run([str(script)], env=env, capture_output=True, text=True, check=False)


posix_only = pytest.mark.skipif(sys.platform == "win32", reason="the hold step is a POSIX shell script")


@posix_only
def test_hold_step_fails_while_the_label_is_present(tmp_path: Path) -> None:
    result = _run_hold_step(tmp_path, labels=f"bug\n{_hold_label()}\n")
    assert result.returncode == 1
    assert "::error::" in result.stdout
    assert "repos/BenchBox-dev/BenchBox/issues/42/labels" in (tmp_path / "gh.log").read_text(encoding="utf-8")
    assert "--paginate" in (tmp_path / "gh.log").read_text(encoding="utf-8")


@posix_only
@pytest.mark.parametrize("labels", ["", "bug\nperf\n", f"x-{_hold_label()}\n{_hold_label()}-y\n"])
def test_hold_step_passes_when_the_label_is_absent(labels: str, tmp_path: Path) -> None:
    result = _run_hold_step(tmp_path, labels=labels)
    assert result.returncode == 0
    assert "::error::" not in result.stdout


@posix_only
def test_hold_step_fails_closed_when_the_label_read_fails(tmp_path: Path) -> None:
    result = _run_hold_step(tmp_path, gh_fails=True)
    assert result.returncode != 0
    assert "No durable hold label." not in result.stdout


@posix_only
def test_rerunning_a_hold_era_suite_passes_once_the_label_is_removed(tmp_path: Path) -> None:
    pushed_while_held = _run_hold_step(tmp_path, labels=f"{_hold_label()}\n")
    assert pushed_while_held.returncode == 1
    rerun_after_release = _run_hold_step(tmp_path, labels="")
    assert rerun_after_release.returncode == 0
    rerun_after_relabel = _run_hold_step(tmp_path, labels=f"{_hold_label()}\n")
    assert rerun_after_relabel.returncode == 1
