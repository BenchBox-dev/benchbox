from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "sqlglot-fork-refresh.yml"


def load() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def jobs() -> dict[str, dict[str, Any]]:
    return load()["jobs"]


def all_steps() -> list[dict[str, Any]]:
    return [step for job in jobs().values() for step in job["steps"]]


def test_the_refresh_runs_nightly_and_on_demand_and_only_from_develop_in_the_main_repository() -> None:
    triggers = load()[True]
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert re.fullmatch(r"\d+ \d+ \* \* \*", triggers["schedule"][0]["cron"])
    for job in jobs().values():
        assert "github.repository == 'BenchBox-dev/BenchBox'" in job["if"]
        assert "github.ref == 'refs/heads/develop'" in job["if"]


def test_every_action_is_pinned_to_a_commit() -> None:
    for step in all_steps():
        if "uses" in step:
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", step["uses"]), step["uses"]


def test_only_the_issue_job_can_write_to_this_repository_and_the_workflow_defaults_to_read() -> None:
    assert load()["permissions"] == {"contents": "read"}
    assert jobs()["prepare"]["permissions"] == {"contents": "read"}
    assert jobs()["open-pr"]["permissions"] == {"contents": "read"}
    assert jobs()["follow-up"]["permissions"] == {"contents": "read", "issues": "write"}


def test_the_fork_token_appears_only_in_the_push_step_of_the_job_that_pushes() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count("SQLGLOT_FORK_REFRESH_TOKEN") == 2
    for name, job in jobs().items():
        for step in job["steps"]:
            holds_token = "SQLGLOT_FORK_REFRESH_TOKEN" in repr(step)
            assert holds_token == (name == "open-pr" and step["name"].startswith("Push the branch")), (
                name,
                step["name"],
            )
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False
    assert "SQLGLOT_FORK_REFRESH_TOKEN" not in repr({key: value for key, value in load().items() if key != "jobs"})
    for name, job in jobs().items():
        assert "SQLGLOT_FORK_REFRESH_TOKEN" not in repr({key: value for key, value in job.items() if key != "steps"}), (
            name
        )


def test_the_fork_is_refreshed_from_its_main_by_trusted_develop_code_without_any_secret() -> None:
    prepare = jobs()["prepare"]
    checkouts = [s["with"] for s in prepare["steps"] if s.get("uses", "").startswith("actions/checkout@")]
    assert checkouts[0]["ref"] == "develop"
    assert checkouts[1]["repository"] == "BenchBox-dev/benchbox-sqlglot"
    assert checkouts[1]["ref"] == "main" and checkouts[1]["path"] == "fork" and checkouts[1]["fetch-depth"] == 0
    run = next(s for s in prepare["steps"] if s.get("id") == "prepare")
    assert "secrets." not in str(run.get("env", {}))
    assert "scripts/sqlglot_fork_refresh.py prepare" in run["run"]


def test_a_pull_request_opens_only_for_a_ready_refresh_and_the_issue_job_runs_even_when_preparation_failed() -> None:
    assert "needs.prepare.outputs.state == 'ready'" in jobs()["open-pr"]["if"]
    follow_up = jobs()["follow-up"]
    assert "!cancelled()" in follow_up["if"] and "needs.prepare.result" not in follow_up["if"]
    assert follow_up["needs"] == ["prepare", "open-pr"]
    for step in follow_up["steps"]:
        if step.get("uses", "").startswith("actions/download-artifact@"):
            assert step["continue-on-error"] is True, step["name"]


def test_runs_never_overlap_and_a_running_refresh_is_never_cancelled() -> None:
    assert load()["concurrency"] == {"group": "sqlglot-fork-refresh", "cancel-in-progress": False}
