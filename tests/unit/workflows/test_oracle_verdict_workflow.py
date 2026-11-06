from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from workflow_expressions import evaluate, render

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "review-verdict.yml"
LOCK = REPO_ROOT / ".github" / "theseus-lock.json"
CALLEE = re.compile(r"^joeharris76/theseus/\.github/workflows/review\.yml@([0-9a-f]{40})$")
ENGINE_PATHS = ("oracle_reviewers", "oracle_gate", "oracle_review_check", "uv run", "setup-uv")
EXPECTED_SECRETS = (
    "REVIEW_APP_ID",
    "REVIEW_APP_PRIVATE_KEY",
    "REVIEW_GATEWAY_TOKEN",
    "REVIEW_GATEWAY_URL",
    "TS_OAUTH_CI_CLIENT_ID",
    "TS_OAUTH_CI_SECRET",
    "TYPESAFE_API_KEY",
)
RUN_TITLE_PULL = re.compile(r"\bpr=(\d+)")
SHA = "c" * 40


def _load() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers() -> dict[str, Any]:
    workflow = _load()
    return workflow[True] if True in workflow else workflow.get("on", {})


def _job() -> dict[str, Any]:
    return _load()["jobs"]["verdict"]


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _context(
    event_name: str, event: dict[str, Any] | None = None, inputs: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {"github": {"event_name": event_name, "event": event or {}, "run_id": "55"}, "inputs": inputs or {}}


def test_the_workflow_keeps_the_protocol_name_and_a_single_caller_job() -> None:
    workflow = _load()
    assert workflow["name"] == "review-verdict"
    assert list(workflow["jobs"]) == ["verdict"]
    assert workflow["permissions"] == {}


def test_triggers_are_trusted_only() -> None:
    triggers = _triggers()
    assert set(triggers) == {"push", "pull_request_target", "issue_comment", "workflow_dispatch", "schedule"}
    assert triggers["push"] == {"branches": ["develop"]}
    assert set(triggers["pull_request_target"]["types"]) == {
        "opened",
        "synchronize",
        "reopened",
        "ready_for_review",
        "edited",
    }
    assert triggers["issue_comment"]["types"] == ["created"]
    assert triggers["schedule"] == [{"cron": "23 * * * *"}]
    assert "pull_request" not in triggers


def test_the_dispatch_inputs_are_a_required_pull_request_and_two_optional_strings() -> None:
    inputs = _triggers()["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"pr", "head", "reason"}
    assert inputs["pr"]["required"] is True
    assert inputs["head"]["required"] is False
    assert inputs["reason"]["required"] is False


def test_the_caller_calls_the_pinned_theseus_review_workflow_with_the_same_commit_as_the_lock() -> None:
    job = _job()
    match = CALLEE.fullmatch(job["uses"])
    assert match, job["uses"]
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    assert match.group(1) == job["with"]["theseus_ref"] == lock["workflow_sha"]
    assert len(re.findall(r"\b[0-9a-f]{40}\b", _text())) == 2


def test_the_caller_passes_the_environment_base_policy_path_and_tailscale_tag_and_nothing_else() -> None:
    assert _job()["with"] == {
        "environment": "oracle",
        "theseus_ref": _job()["with"]["theseus_ref"],
        "gated_base": "develop",
        "policy_path": ".github/oracle-reviewers.yml",
        "tailscale_tag": "tag:oracle-ci",
    }


def test_the_calling_job_grants_the_documented_permission_ceiling() -> None:
    assert _job()["permissions"] == {"actions": "write", "contents": "read", "pull-requests": "read"}


def test_the_secrets_map_the_callee_names_onto_the_held_oracle_secrets_by_name() -> None:
    secrets = _job()["secrets"]
    for name in ("APP_ID", "APP_PRIVATE_KEY", "GATEWAY_TOKEN", "GATEWAY_URL"):
        assert secrets[f"REVIEW_{name}"] == "${{ secrets.ORACLE_" + name + " }}"


def test_the_caller_has_no_steps_and_passes_named_secrets_with_no_in_tree_engine_references() -> None:
    assert set(_job()) == {"permissions", "uses", "with", "secrets"}
    text = _text()
    assert set(_job()["secrets"]) == set(EXPECTED_SECRETS)
    assert "inherit" not in text
    assert text.count("secrets:") == 1
    for needle in ENGINE_PATHS:
        assert needle not in text, needle


def _cancels(event_name: str) -> Any:
    expression = str(_load()["concurrency"]["cancel-in-progress"]).removeprefix("${{").removesuffix("}}")
    return evaluate(expression, _context(event_name))


def test_the_concurrency_group_keeps_the_verdict_prefix() -> None:
    group = _load()["concurrency"]["group"]
    assert group.startswith("review-verdict-${{")
    assert "github.event.changes.base" in group


def test_push_and_schedule_sweeps_share_one_group_that_never_cancels() -> None:
    group = _load()["concurrency"]["group"]
    pushed = render(group, _context("push"))
    assert pushed == render(group, _context("schedule")) == "review-verdict-sweep"
    pull_groups = {render(group, _context("workflow_dispatch", inputs={"pr": str(number)})) for number in (7, 8)} | {
        render(group, _context("pull_request_target", {"action": "synchronize", "pull_request": {"number": 9}})),
        render(group, _context("issue_comment", {"issue": {"number": 10}})),
    }
    assert pushed not in pull_groups
    assert _cancels("push") is False
    assert _cancels("schedule") is False


@pytest.mark.parametrize(
    ("event_name", "cancels"),
    [
        ("pull_request_target", True),
        ("workflow_dispatch", False),
        ("issue_comment", False),
        ("schedule", False),
        ("push", False),
    ],
)
def test_only_a_pull_request_event_cancels_a_run_in_flight(event_name: str, cancels: bool) -> None:
    assert _cancels(event_name) is cancels


def test_a_dispatch_run_queues_in_the_pull_request_group_instead_of_cancelling() -> None:
    group = _load()["concurrency"]["group"]
    pull = {"action": "synchronize", "pull_request": {"number": 7}}
    groups = {
        render(group, _context("workflow_dispatch", inputs={"pr": "7"})),
        render(group, _context("pull_request_target", pull)),
    }
    assert groups == {"review-verdict-pr-7"}
    assert _cancels("workflow_dispatch") is False


def test_a_title_edit_does_not_share_the_pull_request_group() -> None:
    group = _load()["concurrency"]["group"]
    edited = {"action": "edited", "pull_request": {"number": 7}}
    assert render(group, _context("pull_request_target", edited)) == "review-verdict-run-55"
    rebased = {**edited, "changes": {"base": {"ref": {"from": "main"}}}}
    assert render(group, _context("pull_request_target", rebased)) == "review-verdict-pr-7"


def _run_title(event_name: str, event: dict[str, Any] | None = None, inputs: dict[str, Any] | None = None) -> str:
    context = _context(event_name, event, inputs)
    return re.sub(r"\$\{\{(.*?)\}\}", lambda match: str(evaluate(match.group(1), context) or ""), _load()["run-name"])


@pytest.mark.parametrize(
    ("event_name", "event", "inputs", "title"),
    [
        ("workflow_dispatch", {}, {"pr": "7", "head": SHA}, f"review-verdict (workflow_dispatch pr=7 head={SHA})"),
        ("workflow_dispatch", {}, {"pr": "7"}, "review-verdict (workflow_dispatch pr=7)"),
        (
            "pull_request_target",
            {"pull_request": {"number": 9, "head": {"sha": SHA}}},
            None,
            f"review-verdict (pull_request_target pr=9 head={SHA})",
        ),
        ("issue_comment", {"issue": {"number": 10}}, None, "review-verdict (issue_comment pr=10)"),
        ("schedule", {}, None, "review-verdict (schedule pr=sweep)"),
    ],
)
def test_the_run_title_records_the_head_the_run_targets(
    event_name: str, event: dict[str, Any], inputs: dict[str, Any] | None, title: str
) -> None:
    rendered = _run_title(event_name, event, inputs)
    assert rendered == title
    assert (RUN_TITLE_PULL.search(rendered) is not None) == (event_name != "schedule")
