from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "oracle-gate.yml"
VERDICT = REPO_ROOT / ".github" / "workflows" / "oracle-verdict.yml"
SCRIPT = "_project/scripts/oracle_gate.py"
SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")
KNOWN_SHAS = {
    match.group(0).split("@")[1]
    for match in re.finditer(
        r"[\w.-]+/[\w.-]+@([0-9a-f]{40})", (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
} | {
    "fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09",
    "bcd2ba49218906704ab6c1aa796996da409d3eb1",
}


def _load() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers() -> dict[str, Any]:
    workflow = _load()
    return workflow[True] if True in workflow else workflow.get("on", {})


def _jobs() -> dict[str, Any]:
    return _load()["jobs"]


def _steps(name: str) -> list[dict[str, Any]]:
    return _jobs()[name]["steps"]


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_triggers_are_trusted_only() -> None:
    triggers = _triggers()
    assert set(triggers) == {"pull_request_target", "issue_comment", "workflow_run", "workflow_dispatch", "schedule"}
    assert "pull_request" not in triggers
    assert "pull_request_review" not in triggers


def test_pull_request_target_covers_open_push_reopen_ready_and_edit() -> None:
    types = _triggers()["pull_request_target"]["types"]
    assert set(types) == {"opened", "synchronize", "reopened", "ready_for_review", "edited"}


def test_issue_comment_covers_created_standins() -> None:
    assert _triggers()["issue_comment"]["types"] == ["created"]
    assert "Stand-in oracle review: APPROVE" in _jobs()["gate"]["if"]


def test_workflow_run_bridges_both_posting_workflows() -> None:
    bridge = _triggers()["workflow_run"]
    assert bridge["types"] == ["completed"]
    assert set(bridge["workflows"]) == {"oracle-verdict", "oracle-review"}


def test_dispatch_takes_a_pr_number_and_schedule_sweeps_hourly() -> None:
    assert _triggers()["workflow_dispatch"]["inputs"]["pr"]["required"] is True
    assert len(_triggers()["schedule"]) == 1


def test_no_step_checks_out_pull_request_code() -> None:
    text = _text()
    assert "github.head_ref" not in text
    assert "github.event.pull_request.head" not in text
    assert "pull/" not in text
    for name, job in _jobs().items():
        for step in job["steps"]:
            with_section = step.get("with", {})
            if "actions/checkout" in step.get("uses", ""):
                assert with_section.get("ref") == "develop", name
                assert with_section.get("persist-credentials") is False, name
                assert "_project/scripts/oracle_gate.py" in with_section.get("sparse-checkout", ""), name
            assert "pull_request.head" not in step.get("run", ""), name


def test_sparse_checkout_stays_in_the_gate_and_its_predicate() -> None:
    for name in _jobs():
        for step in _steps(name):
            if "actions/checkout" in step.get("uses", ""):
                sparse = step["with"]["sparse-checkout"]
                assert "_project/scripts/oracle_review_check.py" in sparse
                assert "_project/scripts/soundness_paths.py" in sparse
                assert ".github/soundness-paths.txt" in sparse
                assert ".github/oracle-reviewers.yml" in sparse
                assert "_project/scripts/oracle_reviewers" in sparse


def test_each_job_installs_uv_before_running_the_script() -> None:
    for name in _jobs():
        names = [step.get("name", "") for step in _steps(name)]
        script_at = next(index for index, step in enumerate(_steps(name)) if SCRIPT in step.get("run", ""))
        assert "Install uv" in names, name
        assert names.index("Install uv") < script_at, name


def test_gate_runs_the_script_with_pinned_pyyaml() -> None:
    for name in _jobs():
        for step in _steps(name):
            if SCRIPT in step.get("run", ""):
                assert "uv run --isolated --no-project --python 3.12 --with pyyaml==6.0.3 -- python" in step["run"], (
                    name,
                    step.get("name"),
                )


def test_every_gate_script_step_exports_the_posting_token() -> None:
    for name in _jobs():
        for step in _steps(name):
            if SCRIPT not in step.get("run", ""):
                continue
            env = step.get("env", {})
            assert "GITHUB_TOKEN" in env, (name, step.get("name"))
            assert "GH_TOKEN" in env, (name, step.get("name"))


def test_posting_steps_export_the_app_token() -> None:
    post = next(step for step in _steps("gate") if step["name"] == "Post the gate for the head")
    assert post["env"]["GITHUB_TOKEN"] == "${{ steps.app.outputs.token }}"
    log = next(step for step in _steps("gate") if step["name"].startswith("Log the gate without posting"))
    assert log["env"]["GITHUB_TOKEN"] == "${{ github.token }}"
    sweep = next(step for step in _steps("sweep") if step["name"].startswith("Gate every open"))
    assert sweep["env"]["GITHUB_TOKEN"] == "${{ steps.app.outputs.token || github.token }}"


def test_dispatch_is_refused_outside_develop() -> None:
    guard = next(step for step in _steps("gate") if step["name"].startswith("Refuse a dispatch"))
    assert guard["if"] == "github.event_name == 'workflow_dispatch'"
    assert "refs/heads/develop" in guard["run"]
    assert "exit 2" in guard["run"]


def test_workflow_run_head_resolves_to_an_open_develop_pull_request() -> None:
    resolve = next(step for step in _steps("gate") if step["name"] == "Resolve the pull request")
    assert "github.event.workflow_run.pull_requests" in str(resolve["env"])
    assert "github.event.workflow_run.head_sha" in str(resolve["env"])
    assert 'select(.base.ref == "develop"' in resolve["run"]


def test_app_token_mint_matches_the_verdict_post_job() -> None:
    verdict_text = VERDICT.read_text(encoding="utf-8")
    assert "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1" in verdict_text
    for name in _jobs():
        mint = [step for step in _steps(name) if "create-github-app-token" in step.get("uses", "")]
        assert len(mint) == 1, name
        assert mint[0]["with"] == {
            "app-id": "${{ secrets.ORACLE_APP_ID }}",
            "private-key": "${{ secrets.ORACLE_APP_PRIVATE_KEY }}",
            "permission-statuses": "write",
            "permission-pull-requests": "write",
        }, name
        assert _jobs()[name]["environment"] == "oracle", name


def test_gate_runs_the_script_with_the_resolved_pr() -> None:
    post = next(step for step in _steps("gate") if step["name"] == "Post the gate for the head")
    assert SCRIPT in post["run"]
    assert '--pr "$PR_NUMBER"' in post["run"]
    assert post["env"]["PR_NUMBER"] == "${{ steps.resolve.outputs.pr }}"


def test_sweep_covers_every_open_develop_pull_request() -> None:
    sweep = next(step for step in _steps("sweep") if step["name"].startswith("Gate every open"))
    assert "--base develop" in sweep["run"]
    assert SCRIPT in sweep["run"]


def test_sweep_fails_visibly_when_a_pull_request_errors() -> None:
    sweep = next(step for step in _steps("sweep") if step["name"].startswith("Gate every open"))
    assert 'FAILED=""' in sweep["run"]
    assert "::warning::oracle-gate failed for" in sweep["run"]
    assert "::error::oracle-gate failed for" in sweep["run"]
    assert "exit 1" in sweep["run"]


def test_concurrency_is_one_group_per_pull_request() -> None:
    group = _load()["concurrency"]["group"]
    assert "github.event.pull_request.number" in group
    assert "github.event.issue.number" in group
    assert "inputs.pr" in group
    assert "github.event.workflow_run.pull_requests" in group
    assert "github.event.workflow_run.head_sha" not in group
    assert "github.run_id" in group
    assert _load()["concurrency"]["cancel-in-progress"] is True


def test_workflow_permissions_are_empty_and_jobs_stay_minimal() -> None:
    assert _load()["permissions"] == {}
    allowed = {"contents", "pull-requests", "actions", "issues"}
    for name, job in _jobs().items():
        assert set(job.get("permissions", {})) <= allowed, name


def test_actions_are_pinned_to_known_shas() -> None:
    for name, job in _jobs().items():
        for step in job["steps"]:
            if "uses" in step:
                assert SHA_PIN.match(step["uses"]), (name, step["uses"])
                assert step["uses"].split("@")[1] in KNOWN_SHAS, (name, step["uses"])


def test_no_other_workflow_can_post_the_gate_context() -> None:
    variable_context = re.compile(r"context[\"']?\s*[=:]\s*[\"']?\$")
    offenders = []
    for workflow in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        if workflow.name == "oracle-gate.yml":
            continue
        data = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        for job_name, job in (data.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                run = step.get("run", "")
                if "oracle-gate" in str(step) or ("statuses/" in run and variable_context.search(run)):
                    offenders.append((workflow.name, job_name, step.get("name")))
    assert offenders == []
    gate_py = (REPO_ROOT / "_project/scripts/oracle_gate.py").read_text(encoding="utf-8")
    assert 'GATE_CONTEXT = "oracle-gate"' in gate_py


def test_workflow_has_no_comments() -> None:
    assert not any(line.lstrip().startswith("#") for line in _text().splitlines())
