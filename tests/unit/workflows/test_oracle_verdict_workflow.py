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
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "oracle-verdict.yml"
LOCK = REPO_ROOT / ".github" / "theseus-lock.json"
CALLEE = re.compile(r"^joeharris76/theseus/\.github/workflows/review\.yml@([0-9a-f]{40})$")
ENGINE_PATHS = ("oracle_reviewers", "oracle_gate", "oracle_review_check", "uv run", "setup-uv")
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
    assert workflow["name"] == "oracle-verdict"
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


def test_the_caller_passes_the_environment_and_the_develop_base_and_nothing_else() -> None:
    assert _job()["with"] == {
        "environment": "oracle",
        "theseus_ref": _job()["with"]["theseus_ref"],
        "gated_base": "develop",
    }


def test_the_calling_job_grants_the_documented_permission_ceiling() -> None:
    assert _job()["permissions"] == {"actions": "write", "contents": "read", "pull-requests": "read"}


def test_the_caller_has_no_steps_secrets_or_in_tree_engine_references() -> None:
    assert set(_job()) == {"permissions", "uses", "with"}
    text = _text()
    assert "secrets:" not in text and "inherit" not in text
    for needle in ENGINE_PATHS:
        assert needle not in text, needle


def _cancels(event_name: str) -> Any:
    expression = str(_load()["concurrency"]["cancel-in-progress"]).removeprefix("${{").removesuffix("}}")
    return evaluate(expression, _context(event_name))


def test_the_concurrency_group_keeps_the_verdict_prefix() -> None:
    group = _load()["concurrency"]["group"]
    assert group.startswith("oracle-verdict-${{")
    assert "github.event.changes.base" in group


def test_push_and_schedule_sweeps_share_one_group_that_never_cancels() -> None:
    group = _load()["concurrency"]["group"]
    pushed = render(group, _context("push"))
    assert pushed == render(group, _context("schedule")) == "oracle-verdict-sweep"
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
    assert groups == {"oracle-verdict-pr-7"}
    assert _cancels("workflow_dispatch") is False


def test_a_title_edit_does_not_share_the_pull_request_group() -> None:
    group = _load()["concurrency"]["group"]
    edited = {"action": "edited", "pull_request": {"number": 7}}
    assert render(group, _context("pull_request_target", edited)) == "oracle-verdict-run-55"
    rebased = {**edited, "changes": {"base": {"ref": {"from": "main"}}}}
    assert render(group, _context("pull_request_target", rebased)) == "oracle-verdict-pr-7"


def _run_title(event_name: str, event: dict[str, Any] | None = None, inputs: dict[str, Any] | None = None) -> str:
    context = _context(event_name, event, inputs)
    return re.sub(r"\$\{\{(.*?)\}\}", lambda match: str(evaluate(match.group(1), context) or ""), _load()["run-name"])


@pytest.mark.parametrize(
    ("event_name", "event", "inputs", "title"),
    [
        ("workflow_dispatch", {}, {"pr": "7", "head": SHA}, f"oracle-verdict (workflow_dispatch pr=7 head={SHA})"),
        ("workflow_dispatch", {}, {"pr": "7"}, "oracle-verdict (workflow_dispatch pr=7)"),
        (
            "pull_request_target",
            {"pull_request": {"number": 9, "head": {"sha": SHA}}},
            None,
            f"oracle-verdict (pull_request_target pr=9 head={SHA})",
        ),
        ("issue_comment", {"issue": {"number": 10}}, None, "oracle-verdict (issue_comment pr=10)"),
        ("schedule", {}, None, "oracle-verdict (schedule pr=sweep)"),
    ],
)
def test_the_run_title_records_the_head_the_run_targets(
    event_name: str, event: dict[str, Any], inputs: dict[str, Any] | None, title: str
) -> None:
    rendered = _run_title(event_name, event, inputs)
    assert rendered == title
    assert (RUN_TITLE_PULL.search(rendered) is not None) == (event_name != "schedule")
