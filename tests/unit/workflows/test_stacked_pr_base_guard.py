from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.utilities.posix_shell import run_posix_shell, skip_without_posix_shell

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "ci.yml"
JOB = "ci-paths"
STEP_NAME = "Check base branch"
TEXT_EDIT_SKIP = "${{ !(github.event.action == 'edited' && !github.event.changes.base) }}"

INTEGRATION_BRANCHES = {"develop", "release", "published-results"}


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    triggers = workflow.get("on", workflow.get(True))
    assert triggers is not None, "workflow has no `on:` block"
    return triggers


def _load() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _job() -> dict[str, Any]:
    jobs = _load()["jobs"]
    assert JOB in jobs, f"ci.yml has no {JOB!r} job; a stacked PR would present no base-policy failure"
    return jobs[JOB]


def test_stacked_pr_base_guard_lives_in_ci_workflow() -> None:
    _job()


def test_stacked_pr_base_guard_has_no_branch_filter() -> None:
    pull_request = _triggers(_load())["pull_request"] or {}
    assert "branches" not in pull_request, (
        "ci.yml filters on branches, so the stacked PRs base-guard exists to catch would not trigger it"
    )
    assert "paths" not in pull_request, "ci.yml is path-filtered, so a stacked PR could still report nothing"


def test_stacked_pr_base_guard_reevaluates_when_a_pr_is_retargeted() -> None:
    types = (_triggers(_load())["pull_request"] or {}).get("types", [])
    assert "edited" in types, "guard does not re-evaluate on retarget; a repointed PR keeps a stale pass"
    assert "opened" in types


def test_stacked_pr_base_guard_reevaluates_when_draft_status_changes() -> None:
    types = (_triggers(_load())["pull_request"] or {}).get("types", [])
    assert "ready_for_review" in types, "guard does not re-evaluate when a draft becomes ready"
    assert "converted_to_draft" not in types, (
        "a draft cannot merge and converting to draft only relaxes the guard, so it must not re-run every lane"
    )
    assert sorted(types) == sorted(["opened", "synchronize", "reopened", "edited", "ready_for_review"]), (
        "ci.yml pull_request types changed; every listed event re-runs the guard and the CI lanes"
    )


def test_stacked_pr_base_guard_names_integration_branches_and_can_fail() -> None:
    job = _job()
    assert job.get("if") == TEXT_EDIT_SKIP, "ci-paths must run on every PR event except a title or body edit"
    assert job["steps"][0].get("name") == STEP_NAME, "the base check must run before any PR code is checked out"
    step = job["steps"][0]
    body = str(step.get("run", ""))
    accepted = {branch for branch in INTEGRATION_BRANCHES if branch in body}
    assert accepted == INTEGRATION_BRANCHES, (
        f"guard does not name every integration branch: missing {INTEGRATION_BRANCHES - accepted}"
    )
    assert "exit 1" in body, "guard never fails, so a stacked PR would still show an all-green check list"


def test_stacked_pr_base_guard_feeds_the_tooling_result() -> None:
    workflow = _load()
    assert "base-guard" not in workflow["jobs"]
    for unit in ("core", "results-data", "tooling"):
        job = workflow["jobs"][f"{unit}-summary"]
        assert JOB in job["needs"], f"{unit} result does not aggregate {JOB}; a stacked-PR failure would not gate merge"
        run_text = "\n".join(str(step.get("run", "")) for step in job.get("steps", []))
        assert f"--always {JOB}" in run_text, f"{unit} result does not require {JOB} to succeed"


def _run_guard(base_ref: str, *, draft: bool) -> subprocess.CompletedProcess[str]:
    step = next(s for s in _job()["steps"] if s.get("name") == STEP_NAME)
    env = step.get("env", {})
    assert env.get("BASE_REF") == "${{ github.base_ref }}"
    assert env.get("IS_DRAFT") == "${{ github.event.pull_request.draft }}"
    skip_without_posix_shell()
    return run_posix_shell(
        step["run"],
        env={"PATH": "/usr/bin:/bin", "BASE_REF": base_ref, "IS_DRAFT": "true" if draft else "false"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


@pytest.mark.parametrize("draft", [True, False])
@pytest.mark.parametrize("base", sorted(INTEGRATION_BRANCHES))
def test_stacked_pr_base_guard_passes_integration_bases(base: str, draft: bool) -> None:
    assert _run_guard(base, draft=draft).returncode == 0


def test_stacked_pr_base_guard_passes_a_draft_on_a_feature_base() -> None:
    result = _run_guard("fix/parent-branch", draft=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "::error::" not in result.stdout


def test_stacked_pr_base_guard_fails_a_ready_pr_on_a_feature_base() -> None:
    result = _run_guard("fix/parent-branch", draft=False)
    assert result.returncode != 0
    assert "::error::" in result.stdout
    assert "fix/parent-branch" in result.stdout
    assert "retarget this PR at develop" in result.stdout


def test_stacking_rule_is_stated_consistently_in_agent_and_policy_docs() -> None:
    policy = (REPO_ROOT / "docs" / "internal" / "pr-base-branch-policy.md").read_text(encoding="utf-8")
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    index = (REPO_ROOT / "docs" / "development" / "index.md").read_text(encoding="utf-8")
    assert "unsupported" not in index.lower()
    assert "do not support" not in policy.lower()
    assert "never open a pr with `--base`" not in policy.lower()
    for text in (policy, agents):
        assert "git rebase --onto origin/develop <old parent tip>" in text
    assert "converted_to_draft" in policy
    assert "gh pr ready" in policy
    assert "git rebase --onto <new B tip> <old B tip>" in policy
    assert "mark ready" in agents
    allowed = policy.split("## Allowed bases")[1].split("\n## ")[0]
    assert "draft PR in a stack" in allowed
    assert "oracle review" in policy
