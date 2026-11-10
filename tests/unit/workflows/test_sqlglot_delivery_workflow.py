from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "sqlglot-delivery.yml"
APP_SECRETS = {"SQLGLOT_DELIVERY_APP_ID", "SQLGLOT_DELIVERY_APP_PRIVATE_KEY"}


def load() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def job(name: str) -> dict[str, Any]:
    return load()["jobs"][name]


def used(target: dict[str, Any]) -> list[str]:
    return [step["uses"] for step in target["steps"] if "uses" in step]


def job_text(name: str) -> str:
    return yaml.safe_dump(job(name))


@pytest.mark.parametrize("name", ["prepare", "publish", "arm"])
def test_every_job_runs_only_in_the_main_repository_on_develop(name: str) -> None:
    condition = job(name)["if"]
    assert "github.repository == 'BenchBox-dev/BenchBox'" in condition
    assert "github.ref == 'refs/heads/develop'" in condition


def test_runs_are_serialized_and_queued_rather_than_cancelled() -> None:
    assert load()["concurrency"] == {"group": "sqlglot-delivery", "cancel-in-progress": False, "queue": "max"}


def test_the_prepare_job_holds_no_secret_and_no_write_permission() -> None:
    assert load()["permissions"] == {"contents": "read"}
    assert job("prepare")["permissions"] == {"contents": "read", "packages": "read"}
    assert "secrets." not in job_text("prepare")
    assert "app-token" not in job_text("prepare")


@pytest.mark.parametrize("name", ["publish", "arm"])
def test_only_trusted_mutation_jobs_hold_the_scoped_app_token(name: str) -> None:
    assert set(re.findall(r"secrets\.(\w+)", WORKFLOW.read_text(encoding="utf-8"))) == APP_SECRETS
    assert set(re.findall(r"secrets\.(\w+)", job_text(name))) == APP_SECRETS
    minters = [step for step in job(name)["steps"] if "create-github-app-token" in step.get("uses", "")]
    assert len(minters) == 1
    assert minters[0]["with"]["permission-contents"] == "write"
    assert minters[0]["with"]["permission-pull-requests"] == "write"
    assert {key for key in minters[0]["with"] if key.startswith("permission-")} == {
        "permission-contents",
        "permission-pull-requests",
    }
    assert job(name)["permissions"] == {"contents": "read"}


def test_a_missing_app_credential_fails_the_job_before_a_token_is_minted() -> None:
    names = [step.get("name") for step in job("publish")["steps"]]
    guard = next(step for step in job("publish")["steps"] if step.get("name") == "Require the delivery App credentials")
    assert "exit 1" in guard["run"]
    assert names.index("Require the delivery App credentials") < names.index("Mint the App token")


def test_publication_needs_the_kill_switch_a_real_run_and_an_adoption() -> None:
    condition = job("publish")["if"]
    assert "vars.SQLGLOT_DELIVERY_ENABLED == 'true'" in condition
    assert "needs.prepare.outputs.state == 'adopt'" in condition
    assert "inputs.dry_run == false" in condition
    assert job("publish")["needs"] == "prepare"


def test_every_action_is_pinned_to_a_commit_already_used_elsewhere_in_the_repository() -> None:
    others = "\n".join(path.read_text(encoding="utf-8") for path in WORKFLOWS.glob("*.yml") if path != WORKFLOW)
    for name in ("prepare", "publish", "arm"):
        for reference in used(job(name)):
            action, _, sha = reference.partition("@")
            assert re.fullmatch(r"[0-9a-f]{40}", sha), reference
            assert f"{action}@{sha}" in others, reference


def test_checkouts_are_of_develop_without_persisted_credentials() -> None:
    for name in ("prepare", "publish", "arm"):
        checkouts = [step for step in job(name)["steps"] if step.get("uses", "").startswith("actions/checkout@")]
        assert checkouts
        for step in checkouts:
            assert step["with"]["ref"] == "develop"
            assert step["with"]["persist-credentials"] is False


def test_the_publish_job_never_executes_the_prepared_artifact() -> None:
    steps = job("publish")["steps"]
    names = [step.get("name") for step in steps]
    assert names.index("Checkout develop") < names.index("Fetch the prepared adoption")
    for step in steps:
        command = re.sub(r'--prepared "\$PREPARED"', "", str(step.get("run", "")))
        assert "prepared" not in command.lower()
        assert "sqlglot-delivery" not in command
        if "run" in step and "scripts.sqlglot_delivery" in step["run"]:
            assert "uv run -- python -m scripts.sqlglot_delivery" in step["run"]


def test_arming_goes_through_the_delivery_script_with_the_app_token() -> None:
    arm = next(step for step in job("publish")["steps"] if "sqlglot_delivery arm" in step.get("run", ""))
    assert arm["env"]["GH_TOKEN"] == "${{ steps.app-token.outputs.token }}"
    assert "pr_arm" not in arm["run"] and "gh pr merge" not in arm["run"]


@pytest.mark.parametrize(
    ("event", "conclusion", "source_repo", "enabled", "ref", "expected"),
    [
        ("workflow_run", "success", "BenchBox-dev/BenchBox", "true", "refs/heads/develop", True),
        ("workflow_run", "failure", "BenchBox-dev/BenchBox", "true", "refs/heads/develop", False),
        ("workflow_run", "success", "outsider/BenchBox", "true", "refs/heads/develop", False),
        ("workflow_run", "success", "BenchBox-dev/BenchBox", "false", "refs/heads/develop", False),
        ("workflow_run", "success", "BenchBox-dev/BenchBox", "", "refs/heads/develop", False),
        ("workflow_dispatch", "success", "BenchBox-dev/BenchBox", "true", "refs/heads/develop", False),
        ("workflow_run", "success", "BenchBox-dev/BenchBox", "true", "refs/heads/untrusted", False),
    ],
)
def test_completion_arming_refuses_untrusted_events_and_disabled_runs(
    event: str, conclusion: str, source_repo: str, enabled: str, ref: str, expected: bool
) -> None:
    from tests.unit.workflows.workflow_expressions import evaluate

    context = {
        "github": {
            "repository": "BenchBox-dev/BenchBox",
            "ref": ref,
            "event_name": event,
            "event": {"workflow_run": {"conclusion": conclusion, "head_repository": {"full_name": source_repo}}},
        },
        "vars": {"SQLGLOT_DELIVERY_ENABLED": enabled},
    }
    assert bool(evaluate(job("arm")["if"], context)) is expected
