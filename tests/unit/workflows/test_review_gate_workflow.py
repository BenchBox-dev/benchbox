from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from workflow_expressions import render as _render

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "review-gate.yml"
LOCK = REPO_ROOT / ".github" / "theseus-lock.json"
CALLEE = re.compile(r"^joeharris76/theseus/\.github/workflows/gate\.yml@([0-9a-f]{40})$")
ENGINE_PATHS = ("oracle_reviewers", "oracle_gate", "oracle_review_check", "uv run", "setup-uv")
EXPECTED_SECRETS = {"REVIEW_APP_ID": "ORACLE_APP_ID", "REVIEW_APP_PRIVATE_KEY": "ORACLE_APP_PRIVATE_KEY"}
SHA = "0123456789abcdef0123456789abcdef01234567"
STANDIN = "Stand-in review: APPROVE"


def _load() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers() -> dict[str, Any]:
    workflow = _load()
    return workflow[True] if True in workflow else workflow.get("on", {})


def _job() -> dict[str, Any]:
    return _load()["jobs"]["gate"]


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_the_workflow_keeps_the_protocol_name_one_caller_job_and_the_posting_check() -> None:
    workflow = _load()
    assert workflow["name"] == "review-gate"
    assert list(workflow["jobs"]) == ["gate", "posting-check"]
    assert workflow["permissions"] == {}


def test_triggers_are_trusted_only_and_the_push_trigger_has_no_path_filter() -> None:
    triggers = _triggers()
    assert set(triggers) == {
        "push",
        "pull_request_target",
        "issue_comment",
        "workflow_run",
        "workflow_dispatch",
        "schedule",
    }
    assert triggers["push"] == {"branches": ["develop"]}
    assert "pull_request" not in triggers
    assert "pull_request_review" not in triggers


def test_pull_request_comment_bridge_dispatch_and_schedule_triggers_keep_their_shape() -> None:
    triggers = _triggers()
    assert set(triggers["pull_request_target"]["types"]) == {
        "opened",
        "synchronize",
        "reopened",
        "ready_for_review",
        "edited",
    }
    assert triggers["issue_comment"]["types"] == ["created", "edited", "deleted"]
    assert triggers["workflow_run"] == {"workflows": ["review-request"], "types": ["completed"]}
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert inputs["pr"]["required"] is True
    assert inputs["head_sha"]["required"] is False
    assert triggers["schedule"] == [{"cron": "41 * * * *"}]


def test_the_caller_calls_the_pinned_theseus_gate_workflow_with_the_same_commit_as_the_lock() -> None:
    job = _job()
    match = CALLEE.fullmatch(job["uses"])
    assert match, job["uses"]
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    assert match.group(1) == job["with"]["theseus_ref"] == lock["workflow_sha"]
    assert len(re.findall(r"\b[0-9a-f]{40}\b", _text())) == 2


def test_the_caller_passes_the_environment_the_develop_base_and_the_policy_path_and_nothing_else() -> None:
    assert _job()["with"] == {
        "environment": "oracle",
        "theseus_ref": _job()["with"]["theseus_ref"],
        "gated_base": "develop,release/*",
        "policy_path": ".github/review.yml",
    }


def test_the_calling_job_grants_the_documented_permission_ceiling() -> None:
    assert _job()["permissions"] == {"actions": "write", "contents": "read", "pull-requests": "read"}


def test_the_caller_has_no_steps_and_passes_named_secrets_with_no_in_tree_engine_references() -> None:
    job = _job()
    assert set(job) == {"permissions", "uses", "with", "secrets"}
    text = _text()
    assert _job()["secrets"] == {
        name: "${{ secrets." + name.replace("REVIEW_", "ORACLE_") + " }}" for name in EXPECTED_SECRETS
    }
    assert "inherit" not in text
    assert text.count("secrets:") == 1
    for needle in ENGINE_PATHS:
        assert needle not in text, needle


def test_the_concurrency_group_keeps_the_gate_prefix_and_never_cancels_a_run_in_flight() -> None:
    concurrency = _load()["concurrency"]
    assert concurrency["group"].startswith("review-gate-${{")
    assert concurrency["cancel-in-progress"] is False


def _group(event_name: str, event: dict[str, Any], inputs: dict[str, Any] | None = None) -> str:
    context = {"github": {"event_name": event_name, "event": event, "run_id": "55"}, "inputs": inputs or {}}
    return _render(_load()["concurrency"]["group"], context)


def _comment(action: str, body: str, previous: str | None = None) -> dict[str, Any]:
    event: dict[str, Any] = {"action": action, "issue": {"number": 8}, "comment": {"body": body}}
    if previous is not None:
        event["changes"] = {"body": {"from": previous}}
    return event


def test_push_sweeps_share_a_group_apart_from_pull_request_gates() -> None:
    assert _group("push", {}) == "review-gate-push-sweep"
    for number in (7, 8):
        assert _group("pull_request_target", {"pull_request": {"number": number}}) == f"review-gate-{number}"


def test_marker_comments_and_marker_edits_gate_in_the_pull_request_group() -> None:
    assert _group("issue_comment", _comment("created", STANDIN)) == "review-gate-8"
    assert _group("issue_comment", _comment("deleted", STANDIN)) == "review-gate-8"
    assert _group("issue_comment", _comment("created", "Review adjudication: WITHDRAW D1 - done")) == "review-gate-8"
    assert _group("issue_comment", _comment("edited", "withdrawn", STANDIN)) == "review-gate-8"
    assert _group("issue_comment", _comment("edited", STANDIN, "draft")) == "review-gate-8"
    assert (
        _group("issue_comment", _comment("edited", "withdrawn", "Review adjudication: WITHDRAW D1 - x"))
        == "review-gate-8"
    )


@pytest.mark.parametrize("body", ["Stand-in oracle review: APPROVE", "Oracle adjudication: WITHDRAW D1 - done"])
def test_retired_legacy_comments_never_share_the_pull_request_group(body: str) -> None:
    assert _group("issue_comment", _comment("created", body)) == "review-gate-ignored-55"
    assert _group("issue_comment", _comment("deleted", body)) == "review-gate-ignored-55"
    assert _group("issue_comment", _comment("edited", "withdrawn", body)) == "review-gate-ignored-55"


def test_unrelated_comments_run_in_a_group_of_their_own() -> None:
    assert _group("issue_comment", _comment("created", "looks good")) == "review-gate-ignored-55"
    assert _group("issue_comment", _comment("edited", "fixed", "typo")) == "review-gate-ignored-55"
    assert _group("issue_comment", _comment("edited", "fixed", "typo")) == "review-gate-ignored-55"


def test_a_workflow_run_bridge_is_keyed_by_the_head_sha() -> None:
    assert _group("workflow_run", {"workflow_run": {"head_sha": SHA}}) == f"review-gate-bridge-{SHA}"


def _run_name(event_name: str, event: dict[str, Any], inputs: dict[str, Any] | None = None) -> str:
    context = {"github": {"event_name": event_name, "event": event}, "inputs": inputs or {}}
    return _render(_load()["run-name"], context)


def test_only_the_scheduled_and_push_runs_are_titled_sweep() -> None:
    assert _run_name("schedule", {}) == "review-gate (schedule sweep)"
    assert _run_name("push", {}) == "review-gate (push sweep)"
    titles = [
        _run_name("workflow_run", {"workflow_run": {"head_sha": SHA}}),
        _run_name("pull_request_target", {"pull_request": {"number": 7}}),
        _run_name("issue_comment", {"issue": {"number": 8}}),
        _run_name("workflow_dispatch", {}, {"pr": "9"}),
    ]
    assert all("sweep" not in title for title in titles)


def test_titles_carry_the_pull_request_number_or_the_source_head_sha() -> None:
    assert _run_name("workflow_run", {"workflow_run": {"head_sha": SHA}}) == f"review-gate (workflow_run sha={SHA})"
    assert _run_name("pull_request_target", {"pull_request": {"number": 7}}) == "review-gate (pull_request_target pr=7)"
    assert _run_name("issue_comment", {"issue": {"number": 8}}) == "review-gate (issue_comment pr=8)"
    assert _run_name("workflow_dispatch", {}, {"pr": "9"}) == "review-gate (workflow_dispatch pr=9)"


CALLER_FILES = {"review-gate.yml": "gate.yml", "review-verdict.yml": "review.yml"}
STATUS_ACTION = re.compile(r"commit-status|create-status|set-status", re.IGNORECASE)


def test_only_the_theseus_caller_jobs_can_post_the_gate_context() -> None:
    offenders = []
    for workflow in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        data = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        for job_name, job in (data.get("jobs") or {}).items():
            if workflow.name in CALLER_FILES and "steps" not in job:
                callee = re.fullmatch(
                    rf"joeharris76/theseus/\.github/workflows/{re.escape(CALLER_FILES[workflow.name])}@[0-9a-f]{{40}}",
                    str(job.get("uses", "")),
                )
                if not callee:
                    offenders.append((workflow.name, job_name, "caller job does not call the pinned Theseus workflow"))
                continue
            if "uses" in job and "steps" not in job:
                continue
            for step in job.get("steps") or []:
                run = step.get("run", "")
                text = str(step)
                posts = bool(STATUS_ACTION.search(str(step.get("uses", "")))) or "statuses/" in run
                variable_context = re.search(r"context[\"']?\s*[=:]\s*[\"']?\$", text)
                if "oracle-gate" in text or "review-gate" in text or (posts and variable_context):
                    offenders.append((workflow.name, job_name, step.get("name")))
    assert offenders == []
    assert set(CALLER_FILES) == {
        name for name in CALLER_FILES if (REPO_ROOT / ".github" / "workflows" / name).is_file()
    }
