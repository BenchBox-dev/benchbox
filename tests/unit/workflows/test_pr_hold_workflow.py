from __future__ import annotations

import json
import os
import re
import shutil
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


def test_hold_job_reads_live_labels_not_the_event_payload() -> None:
    job = _load()["jobs"]["hold"]
    step = job["steps"][0]
    assert "github.event.pull_request.labels" not in WORKFLOW.read_text(encoding="utf-8")
    assert "gh api" in step["run"]
    assert _load()["permissions"] == {"pull-requests": "read"}


def test_runbook_requires_the_hold_context() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    block = text.split("Required status checks:", 1)[1].split("```", 2)[1]
    assert "- hold" in block.splitlines()


_GH_SERVES_THE_PR_JSON = """endpoint=""
filter=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    api) shift; endpoint="$1" ;;
    --jq) shift; filter="$1" ;;
  esac
  shift
done
printf '%s\\n' "$endpoint" > "$GH_ENDPOINT_FILE"
printf '%s' "$PR_JSON" | jq -r "$filter"
"""

needs_posix_shell_and_jq = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("jq") is None,
    reason="the hold step is a POSIX shell script that applies a jq filter on ubuntu",
)


def _hold_step(tmp_path: Path, gh_body: str, labels: list[str]) -> tuple[subprocess.CompletedProcess[str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    gh = bin_dir / "gh"
    gh.write_text("#!/bin/sh\n" + gh_body, encoding="utf-8")
    gh.chmod(0o755)
    script = tmp_path / "hold.sh"
    script.write_text("#!/bin/sh\n" + _load()["jobs"]["hold"]["steps"][0]["run"], encoding="utf-8")
    script.chmod(0o755)
    endpoint = tmp_path / "endpoint"
    pr_json = json.dumps({"number": 7, "labels": [{"id": 10 + i, "name": name} for i, name in enumerate(labels)]})
    result = subprocess.run(
        [str(script)],
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "REPO": "owner/repo",
            "PR": "7",
            "HOLD_LABEL": _hold_label(),
            "PR_JSON": pr_json,
            "GH_ENDPOINT_FILE": str(endpoint),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return result, endpoint


@needs_posix_shell_and_jq
@pytest.mark.parametrize(("held", "expected_code"), [("true", 1), ("false", 0)])
def test_hold_step_fails_only_while_the_label_is_present(held: str, expected_code: int, tmp_path: Path) -> None:
    labels = ["bug", _hold_label(), "enhancement"] if held == "true" else ["bug", "enhancement"]
    result, endpoint = _hold_step(tmp_path, _GH_SERVES_THE_PR_JSON, labels)
    assert result.returncode == expected_code
    assert ("::error::" in result.stdout) is (held == "true")
    assert endpoint.read_text(encoding="utf-8").strip() == "repos/owner/repo/pulls/7"


@needs_posix_shell_and_jq
def test_hold_step_ignores_labels_that_merely_contain_the_hold_label(tmp_path: Path) -> None:
    result, _ = _hold_step(tmp_path, _GH_SERVES_THE_PR_JSON, [f"{_hold_label()}-not", f"x-{_hold_label()}"])
    assert result.returncode == 0


@needs_posix_shell_and_jq
def test_hold_step_matches_the_hold_label_case_insensitively(tmp_path: Path) -> None:
    result, _ = _hold_step(tmp_path, _GH_SERVES_THE_PR_JSON, [_hold_label().upper()])
    assert result.returncode == 1


@needs_posix_shell_and_jq
def test_hold_step_passes_once_the_label_is_removed_on_a_rerun_of_the_labeled_run(tmp_path: Path) -> None:
    held, _ = _hold_step(tmp_path / "while-held", _GH_SERVES_THE_PR_JSON, [_hold_label()])
    released, _ = _hold_step(tmp_path / "after-release", _GH_SERVES_THE_PR_JSON, [])
    assert held.returncode == 1
    assert released.returncode == 0


@needs_posix_shell_and_jq
def test_hold_step_fails_closed_when_the_label_lookup_fails(tmp_path: Path) -> None:
    result, _ = _hold_step(tmp_path, "echo boom >&2\nexit 1\n", [])
    assert result.returncode != 0
    assert "No durable hold label." not in result.stdout


