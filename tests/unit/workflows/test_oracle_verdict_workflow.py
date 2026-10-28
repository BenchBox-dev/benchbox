from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from workflow_expressions import evaluate, render

from _project.scripts.oracle_reviewers import omp_pin
from _project.scripts.oracle_reviewers.commands import build
from _project.scripts.oracle_reviewers.github import RUN_TITLE_PULL
from _project.scripts.oracle_reviewers.omp_pin import pin_from_text
from _project.scripts.oracle_reviewers.policy import SIZE_BUCKETS, load_policy

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "oracle-verdict.yml"
LEGACY = REPO_ROOT / ".github" / "workflows" / "oracle-review.yml"
POLICY = load_policy(REPO_ROOT / ".github" / "oracle-reviewers.yml")
SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")
SECRET_REF = re.compile(r"secrets\.([A-Z0-9_]+)")
OMP_SHA = re.compile(r"^[0-9a-f]{64}$")
DISPATCH_STEP = "Dispatch the gate for the reviewed head"
SETUP_ALLOWANCE_MINUTES = 30
JOB_TIMEOUT_CEILING_MINUTES = 360
UNCHANGED_PREFIX_SHA256 = "0bf8d42d70c7a72d213b971e2e7bb51ea01caea70190e089c43deb3114a7e73e"
REVIEW_CONDITION = (
    "github.event_name == 'workflow_dispatch' ||"
    " (github.event_name == 'pull_request_target' && (github.event.action != 'edited' || github.event.changes.base)) ||"
    " (github.event_name == 'issue_comment' && github.event.issue.pull_request"
    " && startsWith(github.event.comment.body, '/oracle-review')"
    ' && contains(fromJSON(\'["OWNER", "MEMBER", "COLLABORATOR"]\'), github.event.comment.author_association))'
)
STEP_ORDER = [
    "Refuse a dispatch from any ref other than develop",
    "Check out the trusted develop copy",
    "Install uv",
    "Plan the review",
    "Shadow the driver plan",
    "Check out the pull request head for reading only",
    "Classify the change and select the reviewer for attempt 1",
    "Scan the disk for secret values before the reviewer runs",
    "Install omp",
    "Join the tailnet to reach the credential gateway",
    "Scan the disk for secret values after the tailnet join",
    "Verify Linux process isolation",
    "Run the attempt loop",
    *[
        name
        for after in range(1, POLICY.max_attempts)
        for name in (
            f"Classify the invalid attempt after attempt {after}",
            f"Resume the attempt loop after attempt {after}",
        )
    ],
    "Count the attempts",
    "Scan the disk for secret values after the reviewer exits",
    "Validate this run's attempts and decide",
    "Redact every secret value held by the job from the results",
    "Shadow the driver finalize",
    "Log the result when the App is not configured",
    "Mint the App token",
    "Skip a result this head already has",
    "Record the retry attempt",
    "Post the commit status",
    "Post the findings comment",
    "Post the findings review",
    "Upload the retry state",
    *[f"Upload attempt {slot}" for slot in range(1, POLICY.max_attempts + 1)],
    "Upload the captured reviewer event streams",
    DISPATCH_STEP,
    "Compare the driver outputs with the posted outputs",
    "Upload the shadow comparison",
]
SHADOW_PLAN_STEP = "Shadow the driver plan"
SHADOW_FINALIZE_STEP = "Shadow the driver finalize"
SHADOW_COMPARE_STEP = "Compare the driver outputs with the posted outputs"
LOOP_STEP = "Run the attempt loop"
RECOVER_STEPS = tuple(f"Classify the invalid attempt after attempt {after}" for after in range(1, POLICY.max_attempts))
LOOP_STEPS = (
    LOOP_STEP,
    *(f"Resume the attempt loop after attempt {after}" for after in range(1, POLICY.max_attempts)),
)
FINALIZE_STEP = "Validate this run's attempts and decide"
REDACT_STEP = "Redact every secret value held by the job from the results"
REDACTION_ENV = {
    "OMP_GATEWAY_TOKEN": "${{ secrets.ORACLE_GATEWAY_TOKEN }}",
    "TYPESAFE_API_KEY": "${{ secrets.TYPESAFE_API_KEY }}",
    "ORACLE_GATEWAY_URL": "${{ secrets.ORACLE_GATEWAY_URL }}",
    "TS_OAUTH_CI_CLIENT_ID": "${{ secrets.TS_OAUTH_CI_CLIENT_ID }}",
    "TS_OAUTH_CI_SECRET": "${{ secrets.TS_OAUTH_CI_SECRET }}",
    "JOB_TOKEN": "${{ github.token }}",
}
SCAN_STEPS = (
    "Scan the disk for secret values before the reviewer runs",
    "Scan the disk for secret values after the reviewer exits",
)
JOIN_STEP = "Join the tailnet to reach the credential gateway"
JOIN_SCAN_STEP = "Scan the disk for secret values after the tailnet join"
ISOLATION_STEP = "Verify Linux process isolation"


def _text_of_workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _load() -> dict[str, Any]:
    return yaml.safe_load(_text_of_workflow())


def _jobs() -> dict[str, Any]:
    return _load()["jobs"]


def _review() -> dict[str, Any]:
    return _jobs()["review"]


def _steps() -> list[dict[str, Any]]:
    return _review()["steps"]


def _names() -> list[str]:
    return [step["name"] for step in _steps()]


def _step(name: str) -> dict[str, Any]:
    return next(step for step in _steps() if step["name"] == name)


def _text(value: Any) -> str:
    return yaml.safe_dump(value, sort_keys=True)


def _secrets(value: Any) -> set[str]:
    return set(SECRET_REF.findall(_text(value)))


def _steps_referencing(secret: str) -> list[str]:
    return [step["name"] for step in _steps() if secret in _secrets(step)]


def _run(step_name: str) -> str:
    return _step(step_name)["run"]


def test_triggers() -> None:
    workflow = _load()
    assert workflow["name"] == "oracle-verdict"
    triggers = workflow[True] if True in workflow else workflow.get("on", {})
    assert set(triggers) == {"push", "pull_request_target", "issue_comment", "workflow_dispatch", "schedule"}
    assert "pull_request" not in triggers


def test_existing_gate_is_untouched() -> None:
    legacy = yaml.safe_load(LEGACY.read_text(encoding="utf-8"))
    assert legacy["name"] == "oracle-review"
    assert "context: oracle-review\n" not in _text_of_workflow()


def test_one_review_job_replaces_the_chain_and_the_steps_keep_their_order() -> None:
    assert set(_jobs()) == {"sweep", "review"}
    for name in _jobs():
        assert not re.fullmatch(r"select-\d+|attempt-\d+-omp|post|plan", name), name
    assert _names() == STEP_ORDER
    assert "needs" not in _review()
    assert " ".join(_review()["if"].split()) == REVIEW_CONDITION


def test_the_review_condition_admits_exactly_what_the_plan_job_did() -> None:
    condition = _review()["if"]
    assert "issue_comment" in condition
    assert "author_association" in condition
    assert "review_comment" not in condition
    assert _jobs()["sweep"]["if"] == "github.event_name == 'schedule' || github.event_name == 'push'"


def test_the_sweep_and_the_triggers_and_the_concurrency_keep_their_reviewed_text() -> None:
    text = _text_of_workflow()
    prefix = text[: text.index("\n  review:\n") + 1]
    assert hashlib.sha256(prefix.encode("utf-8")).hexdigest() == UNCHANGED_PREFIX_SHA256
    assert prefix.index("jobs:\n  sweep:") > prefix.index("concurrency:")


def test_workflow_permissions_are_empty_and_the_review_job_holds_exactly_the_union_it_needs() -> None:
    assert _load()["permissions"] == {}
    assert _review()["permissions"] == {
        "actions": "write",
        "contents": "read",
        "pull-requests": "read",
    }
    assert _jobs()["sweep"]["permissions"] == {"actions": "write", "contents": "read", "pull-requests": "read"}
    assert _review()["environment"] == "oracle"


def test_the_job_timeout_is_derived_from_the_policy_and_stays_under_the_platform_limit() -> None:
    longest = max(reviewer.timeout_minutes for reviewer in POLICY.reviewers.values())
    budget = POLICY.max_attempts * longest + SETUP_ALLOWANCE_MINUTES
    assert _review()["timeout-minutes"] == budget
    assert budget < JOB_TIMEOUT_CEILING_MINUTES
    assert _review()["timeout-minutes"] > POLICY.max_attempts * longest


def test_no_run_block_interpolates_a_secret_or_the_job_token() -> None:
    for job_name, job in _jobs().items():
        for step in job["steps"]:
            run = step.get("run", "")
            assert "secrets." not in run, (job_name, step["name"])
            assert "github.token" not in run, (job_name, step["name"])
            assert "GITHUB_TOKEN" not in run, (job_name, step["name"])
    text = _text_of_workflow()
    for line in text.splitlines():
        if "secrets." in line or "github.token" in line:
            assert re.match(r"^\s+[\w-]+: .*\$\{\{", line) or line.lstrip().startswith("if:"), line


def test_the_tailnet_is_joined_once_before_the_attempt_loop() -> None:
    names = _names()
    joins = [step for step in _steps() if str(step.get("uses", "")).startswith("tailscale/github-action@")]
    assert len(joins) == 1
    join = joins[0]
    assert names.index(join["name"]) < names.index(LOOP_STEP)
    assert join["with"]["tags"] == "tag:oracle-ci"
    assert join["with"] == {
        "oauth-client-id": "${{ secrets.TS_OAUTH_CI_CLIENT_ID }}",
        "oauth-secret": "${{ secrets.TS_OAUTH_CI_SECRET }}",
        "tags": "tag:oracle-ci",
    }
    assert "env.HAS_TAILNET == 'true'" in join["if"]
    assert (
        _review()["env"]["HAS_TAILNET"]
        == "${{ secrets.TS_OAUTH_CI_CLIENT_ID != '' && secrets.TS_OAUTH_CI_SECRET != '' }}"
    )


def test_the_review_job_holds_no_oidc_token_and_names_no_federated_credential() -> None:
    text = _text_of_workflow()
    assert "id-token" not in text
    assert "TS_AUDIENCE" not in text
    assert "audience" not in text
    assert re.search(r"\bTS_OAUTH_CLIENT_ID\b", text) is None
    assert "id-token" not in _review()["permissions"]


def test_a_disk_scan_covering_the_oauth_secret_runs_after_the_join_and_before_isolation() -> None:
    names = _names()
    assert names.index(JOIN_STEP) < names.index(JOIN_SCAN_STEP) < names.index(ISOLATION_STEP) < names.index(LOOP_STEP)
    step = _step(JOIN_SCAN_STEP)
    assert step["if"] == "steps.plan.outputs.decision == 'review'"
    assert "continue-on-error" not in step
    assert "--env TS_OAUTH_CI_SECRET" in step["run"]


def test_the_isolation_step_checks_the_request_token_through_the_module() -> None:
    assert _step(ISOLATION_STEP)["run"] == "python3 -m _project.scripts.oracle_reviewers.isolation"


def test_the_loop_holds_only_what_the_reviewer_holds_and_the_reviewer_home() -> None:
    for name in LOOP_STEPS:
        step = _step(name)
        assert step["env"] == {
            "OMP_GATEWAY_TOKEN": "${{ secrets.ORACLE_GATEWAY_TOKEN }}",
            "ORACLE_GATEWAY_URL": "${{ secrets.ORACLE_GATEWAY_URL }}",
        }, name
        assert step["working-directory"] == "trusted"
        assert "cli attempts" in step["run"]
        assert "reviewer-home" not in step["run"]
        assert "github.token" not in _text(step)
        assert _secrets(step) == {"ORACLE_GATEWAY_TOKEN", "ORACLE_GATEWAY_URL"}
    text = _text(_review())
    for gone in (
        "OMP_AGENT_CONFIG",
        "OMP_AUTH_BROKER",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "OPENAI_API_KEY",
        "META_API_KEY",
        "GEMINI_API_KEY",
    ):
        assert gone not in text, gone


def test_the_job_checks_out_the_committed_reviewer_config() -> None:
    trusted = _step("Check out the trusted develop copy")
    assert ".github/oracle-omp-config.yml" in trusted["with"]["sparse-checkout"].split()
    assert trusted["with"]["path"] == "trusted"
    assert trusted["with"]["ref"] == "${{ github.sha }}"


def test_the_job_job_relies_on_the_per_pull_request_group_alone() -> None:
    assert "concurrency" not in _review()
    assert "strategy" not in _review()


def test_concurrency_is_per_pull_request_and_ignores_unrelated_events() -> None:
    concurrency = _load()["concurrency"]
    group = concurrency["group"]
    assert "oracle-verdict-" in group
    assert "github.event.changes.base" in group


def test_dispatch_is_refused_outside_develop() -> None:
    guard = _step("Refuse a dispatch from any ref other than develop")
    assert guard["if"] == "github.event_name == 'workflow_dispatch'"
    assert guard["env"]["CURRENT_REF"] == "${{ github.ref }}"
    assert "exit 2" in guard["run"]
    assert _names()[0] == guard["name"]


def test_the_plan_step_holds_only_the_job_token_and_resolves_the_head_by_api() -> None:
    plan = _step("Plan the review")
    assert _secrets(plan) == set()
    assert set(plan["env"]) == {"GH_TOKEN", "PR_NUMBER"}
    assert "cli plan" in plan["run"]
    assert plan["id"] == "plan"


def test_every_bucket_reviewer_resolves() -> None:
    assert set(POLICY.reviewers) == set(SIZE_BUCKETS)
    assert set(POLICY.buckets) == set(SIZE_BUCKETS)
    for bucket in SIZE_BUCKETS:
        assert POLICY.reviewers[bucket].harness == "omp"


def test_classification_is_written_once_before_the_loop_and_read_by_it() -> None:
    classify = _step("Classify the change and select the reviewer for attempt 1")
    assert "cli select" in classify["run"] and "--slot 1" in classify["run"]
    assert classify["env"] == {"TYPESAFE_API_KEY": "${{ secrets.TYPESAFE_API_KEY }}"}
    assert _names().index(classify["name"]) < _names().index(LOOP_STEP)
    assert "oracle-classification/classification.json" in _run(LOOP_STEP)
    assert "oracle-classification/classification.json" in classify["run"]
    assert "actions/upload-artifact" not in _text(classify)


def test_only_the_scans_the_classifier_the_recovery_and_the_redaction_step_hold_the_judgment_key() -> None:
    assert set(_steps_referencing("TYPESAFE_API_KEY")) == {
        "Classify the change and select the reviewer for attempt 1",
        *RECOVER_STEPS,
        *SCAN_STEPS,
        JOIN_SCAN_STEP,
        FINALIZE_STEP,
        REDACT_STEP,
        SHADOW_FINALIZE_STEP,
    }
    for name in _steps_referencing("TYPESAFE_API_KEY"):
        assert name not in LOOP_STEPS


def test_every_recovery_step_runs_between_two_loop_steps_and_holds_only_the_judgment_key() -> None:
    names = _names()
    assert len(RECOVER_STEPS) == POLICY.max_attempts - 1
    for after, name in enumerate(RECOVER_STEPS, start=1):
        step = _step(name)
        assert step["env"] == {"TYPESAFE_API_KEY": "${{ secrets.TYPESAFE_API_KEY }}"}
        assert "cli recover" in step["run"]
        assert "continue-on-error" not in step
        assert "always()" not in step["if"]
        assert names.index(LOOP_STEPS[after - 1]) + 1 == names.index(name)
        assert names.index(name) + 1 == names.index(LOOP_STEPS[after])
        assert step["if"] == _step(LOOP_STEPS[after])["if"]


def test_the_attempt_count_runs_after_every_loop_step_and_feeds_the_uploads() -> None:
    names = _names()
    count = _step("Count the attempts")
    assert count["id"] == "count"
    assert "env" not in count
    assert names.index(count["name"]) == names.index(LOOP_STEPS[-1]) + 1
    assert "steps.loop" not in _text_of_workflow()


def test_the_only_step_referencing_the_app_key_mints_after_the_loop_and_the_redaction() -> None:
    assert _steps_referencing("ORACLE_APP_PRIVATE_KEY") == ["Mint the App token"]
    names = _names()
    mint = names.index("Mint the App token")
    assert mint > names.index(LOOP_STEP)
    assert mint > names.index(REDACT_STEP)
    assert mint > names.index(SCAN_STEPS[1])
    assert "ORACLE_APP_PRIVATE_KEY" not in _text(_review()["env"])


def _sweep_steps() -> list[dict[str, Any]]:
    return _jobs()["sweep"]["steps"]


def _sweep_step(name: str) -> dict[str, Any]:
    return next(step for step in _sweep_steps() if step["name"] == name)


def test_the_sweep_mints_an_app_token_for_failure_statuses_in_the_oracle_environment() -> None:
    assert _jobs()["sweep"]["environment"] == "oracle"
    assert _jobs()["sweep"]["env"] == {"HAS_APP": "${{ secrets.ORACLE_APP_ID != '' }}"}
    mint = _sweep_step("Mint the App token for failure statuses")
    assert mint["id"] == "app"
    assert mint["if"] == "env.HAS_APP == 'true'"
    assert mint["uses"] == "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1"
    assert mint["with"]["permission-statuses"] == "write"
    assert "permission-pull-requests" not in mint["with"]
    assert _text_of_workflow().count("ORACLE_APP_PRIVATE_KEY") == 2
    dispatch = _sweep_step("Dispatch due retries")
    assert dispatch["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert dispatch["env"]["SURFACE_TOKEN"] == "${{ steps.app.outputs.token }}"
    assert "cli sweep" in dispatch["run"]


def test_every_secret_is_referenced_only_where_it_is_needed() -> None:
    allowed = {
        "ORACLE_GATEWAY_TOKEN": {*LOOP_STEPS, FINALIZE_STEP, REDACT_STEP, SHADOW_FINALIZE_STEP},
        "ORACLE_GATEWAY_URL": {*LOOP_STEPS, FINALIZE_STEP, REDACT_STEP, SHADOW_FINALIZE_STEP},
        "TS_OAUTH_CI_CLIENT_ID": {JOIN_STEP, FINALIZE_STEP, REDACT_STEP, SHADOW_FINALIZE_STEP},
        "TS_OAUTH_CI_SECRET": {
            JOIN_STEP,
            JOIN_SCAN_STEP,
            *SCAN_STEPS,
            FINALIZE_STEP,
            REDACT_STEP,
            SHADOW_FINALIZE_STEP,
        },
        "ORACLE_APP_ID": {"Mint the App token"},
    }
    for secret, steps in allowed.items():
        assert set(_steps_referencing(secret)) == steps, secret
    declared = set().union(*(_secrets(step) for step in _steps()))
    assert declared == {*allowed, "TYPESAFE_API_KEY", "ORACLE_APP_PRIVATE_KEY"}


def test_the_shadow_steps_never_post_never_fail_the_job_and_hold_only_what_the_posting_path_holds() -> None:
    names = _names()
    for name in (SHADOW_PLAN_STEP, SHADOW_FINALIZE_STEP, SHADOW_COMPARE_STEP):
        step = _step(name)
        assert step["continue-on-error"] is True
        assert "gh " not in step["run"] and "ORACLE_APP" not in _text(step)
        assert "drive" in step["run"]
    assert _step(SHADOW_PLAN_STEP)["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "PR_NUMBER": "${{ github.event.pull_request.number || github.event.issue.number || inputs.pr }}",
    }
    assert _step(SHADOW_FINALIZE_STEP)["env"] == {
        **REDACTION_ENV,
        "OMP_INSTALL_STATE": "${{ steps.omp.outputs.state }}",
    }
    assert not _secrets(_step(SHADOW_COMPARE_STEP)) and "github.token" not in _text(_step(SHADOW_COMPARE_STEP))
    assert names.index("Plan the review") + 1 == names.index(SHADOW_PLAN_STEP)
    assert names.index(REDACT_STEP) + 1 == names.index(SHADOW_FINALIZE_STEP)
    assert names.index(SHADOW_FINALIZE_STEP) < names.index("Mint the App token")
    assert names.index(DISPATCH_STEP) < names.index(SHADOW_COMPARE_STEP)
    assert names.index(SHADOW_COMPARE_STEP) + 1 == names.index("Upload the shadow comparison")


def test_the_shadow_comparison_names_the_outcome_of_every_posting_step_and_the_guard() -> None:
    assert _step(SHADOW_COMPARE_STEP)["env"] == {
        "GUARD_SKIP": "${{ steps.guard.outputs.skip }}",
        "GUARD_OUTCOME": "${{ steps.guard.outcome }}",
        "POSTED_STATUS": "${{ steps.status.outcome }}",
        "POSTED_COMMENT": "${{ steps.comment.outcome }}",
        "POSTED_REVIEW": "${{ steps.review.outcome }}",
        "POSTED_GATE": "${{ steps.dispatch.outcome }}",
    }
    for name, ident in (
        ("Post the commit status", "status"),
        ("Post the findings comment", "comment"),
        ("Post the findings review", "review"),
        (DISPATCH_STEP, "dispatch"),
    ):
        assert _step(name)["id"] == ident


def test_the_redaction_step_holds_every_secret_value_the_job_held() -> None:
    step = _step(REDACT_STEP)
    assert step["env"] == REDACTION_ENV
    assert "cli redact-check" in step["run"]
    assert "--env JOB_TOKEN" in step["run"]
    assert (
        '--path "$RUNNER_TEMP/oracle-attempts"' in step["run"] and '--path "$RUNNER_TEMP/oracle-final"' in step["run"]
    )
    names = _names()
    assert names.index(LOOP_STEP) < names.index("Validate this run's attempts and decide") < names.index(step["name"])
    assert names.index(step["name"]) < names.index("Mint the App token")
    assert step["if"] == "steps.plan.outputs.post == 'true'"


def test_the_finalize_step_holds_every_secret_value_so_the_marker_is_built_from_redacted_findings() -> None:
    step = _step(FINALIZE_STEP)
    assert {key: value for key, value in step["env"].items() if key != "OMP_INSTALL_STATE"} == REDACTION_ENV
    assert "--env JOB_TOKEN" in step["run"]


def test_no_step_summary_write_reaches_the_summary_before_the_redaction_step_except_the_omp_version() -> None:
    names = _names()
    redact = names.index(REDACT_STEP)
    writers = [step["name"] for step in _steps()[:redact] if "GITHUB_STEP_SUMMARY" in _text(step)]
    assert writers == ["Install omp"]
    run = _run(REDACT_STEP)
    assert '--summary-file "$RUNNER_TEMP/oracle-attempts/summary.json"' in run
    assert '--summary-file "$RUNNER_TEMP/oracle-final/summary.json"' in run


def test_the_disk_scans_bracket_the_reviewer_and_cover_the_declared_scratch_roots() -> None:
    names = _names()
    before, after = (_step(name) for name in SCAN_STEPS)
    assert names.index(before["name"]) < names.index("Install omp") < names.index(LOOP_STEP)
    assert names.index(after["name"]) > names.index(LOOP_STEPS[-1])
    assert names.index(before["name"]) > names.index("Classify the change and select the reviewer for attempt 1")
    for step in (before, after, _step(JOIN_SCAN_STEP)):
        assert step["env"] == {
            "TYPESAFE_API_KEY": "${{ secrets.TYPESAFE_API_KEY }}",
            "TS_OAUTH_CI_SECRET": "${{ secrets.TS_OAUTH_CI_SECRET }}",
            "JOB_TOKEN": "${{ github.token }}",
        }
        for root in ('"$HOME"', '"$RUNNER_TEMP"', '"$GITHUB_WORKSPACE/trusted"', '"$GITHUB_WORKSPACE/workspace"'):
            assert f"--root {root}" in step["run"], (step["name"], root)
        for root in ("/tmp", "/var/tmp", "/dev/shm", '"${TMPDIR:-/tmp}"'):
            assert f"--root {root}" in step["run"]
        assert "--env TYPESAFE_API_KEY --env TS_OAUTH_CI_SECRET --env JOB_TOKEN" in step["run"]
        assert "continue-on-error" not in step


def test_the_job_token_is_never_present_while_the_reviewer_runs() -> None:
    holders = [step["name"] for step in _steps() if "github.token" in _text(step)]
    for name in LOOP_STEPS:
        assert name not in holders
    for name in holders:
        step = _step(name)
        assert name not in LOOP_STEPS
        assert "github.token" in _text(step.get("env", {})), name
        assert "github.token" not in _text({key: value for key, value in step.items() if key != "env"}), name
    assert "github.token" not in _text(_review()["env"])
    assert _load()["permissions"] == {}


def test_the_pull_request_head_is_data_and_never_executed() -> None:
    checkouts = [step for step in _steps() if str(step.get("uses", "")).startswith("actions/checkout@")]
    trusted, head = checkouts
    assert trusted["with"]["ref"] == "${{ github.sha }}"
    assert trusted["with"]["path"] == "trusted"
    assert head["with"]["ref"] == "${{ steps.plan.outputs.head_sha }}"
    assert head["with"]["path"] == "workspace"
    assert head["if"] == "steps.plan.outputs.decision == 'review'"
    for step in _steps():
        if "run" in step:
            assert step.get("working-directory") in (None, "trusted"), step["name"]
            assert "bash -c" not in step["run"]
            assert "workspace/" not in step["run"].replace("$GITHUB_WORKSPACE/workspace", "")
    assert '--workspace "$GITHUB_WORKSPACE/workspace"' in _run(LOOP_STEP)


def test_every_checkout_disables_persisted_credentials() -> None:
    checkouts = [step for step in _steps() if str(step.get("uses", "")).startswith("actions/checkout@")]
    assert len(checkouts) == 2
    for step in checkouts:
        assert step["with"]["persist-credentials"] is False
    sweep = [step for step in _jobs()["sweep"]["steps"] if str(step.get("uses", "")).startswith("actions/checkout@")]
    assert all(step["with"]["persist-credentials"] is False for step in sweep)


def test_omp_is_installed_once_pinned_and_checked() -> None:
    text = _text_of_workflow()
    assert "npm install" not in text
    assert "setup-node" not in text
    assert "dev.meta.ai" not in text
    assert "antigravity" not in text
    assert "codex login" not in text
    assert "sysctl" not in text
    assert "CODEX_HOME" not in text
    assert "AGY_CLI_DISABLE_AUTO_UPDATE" not in text
    install = _step("Install omp")
    pin = yaml.safe_load((REPO_ROOT / ".github/oracle-reviewers.yml").read_text(encoding="utf-8"))["omp"]
    linux = pin["builds"]["linux-x64"]
    assert "env" not in install
    assert re.fullmatch(r"\d+\.\d+\.\d+", pin["version"])
    assert linux["url"].startswith("https://build.stencil.so/d/omp/") and linux["url"].endswith("/omp-linux-x64")
    assert re.fullmatch(r"[0-9a-f]{64}", linux["sha256"])
    assert install["working-directory"] == "trusted"
    command = (
        "uv run --isolated --no-project --python 3.12 --with pyyaml==6.0.3 -- "
        "python -m _project.scripts.oracle_reviewers.cli install-omp --policy .github/oracle-reviewers.yml"
    )
    assert f"binary=$({command})" in install["run"]
    assert 'version=$("$binary" --version)' in install["run"]
    assert 'echo "$version" | tee -a "$GITHUB_STEP_SUMMARY"' in install["run"]
    assert 'echo "ORACLE_OMP_BINARY=$binary" >> "$GITHUB_ENV"' in install["run"]
    assert "curl " not in text
    assert "OMP_URL" not in text and "OMP_SHA256" not in text and "OMP_VERSION" not in text
    assert "--dangerously" not in text and "--yolo" not in text


def test_the_pin_file_keeps_the_linux_build_the_workflow_used_to_install() -> None:
    pin = pin_from_text((REPO_ROOT / ".github" / "oracle-reviewers.yml").read_text(encoding="utf-8"))
    assert re.fullmatch(r"\d+\.\d+\.\d+", pin.version)
    assert pin.builds["linux-x64"].url.startswith("https://build.stencil.so/d/omp/")
    assert pin.builds["linux-x64"].url.endswith("/omp-linux-x64")
    assert re.fullmatch(r"[0-9a-f]{64}", pin.builds["linux-x64"].sha256)


TOLERATED_FAILURES = (
    "Check out the pull request head for reading only",
    "Classify the change and select the reviewer for attempt 1",
    "Join the tailnet to reach the credential gateway",
)
FAIL_CLOSED_STEPS = (
    *SCAN_STEPS,
    JOIN_SCAN_STEP,
    *LOOP_STEPS,
    *RECOVER_STEPS,
    REDACT_STEP,
    "Validate this run's attempts and decide",
)


def test_setup_failures_become_absences_instead_of_ending_the_review() -> None:
    for name in TOLERATED_FAILURES:
        assert _step(name)["continue-on-error"] is True, name
    assert "if" in _step(LOOP_STEP) and "always()" not in _step(LOOP_STEP)["if"]
    assert "cancelled()" not in _step(LOOP_STEP)["if"]


def _run_install_step(tmp_path: Path, uv_body: str) -> tuple[subprocess.CompletedProcess[str], str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(f"#!/bin/sh\n{uv_body}\n", encoding="utf-8")
    uv.chmod(0o755)
    github_output = tmp_path / "github-output"
    github_env = tmp_path / "github-env"
    for path in (github_output, github_env):
        path.write_text("", encoding="utf-8")
    result = subprocess.run(
        ["bash", "-e", "-s"],
        input=_step("Install omp")["run"],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "GITHUB_OUTPUT": str(github_output),
            "GITHUB_ENV": str(github_env),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return result, github_output.read_text(encoding="utf-8"), github_env.read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform == "win32", reason="the step is a POSIX shell script")
def test_a_failed_install_reports_its_state_and_exports_no_binary(tmp_path: Path) -> None:
    body = "echo 'oracle-verdict: error: the omp download failed: HTTPError' >&2\nexit 1"
    result, output, env = _run_install_step(tmp_path, body)
    assert result.returncode == 0
    assert output == "state=failed\n"
    assert env == ""
    assert "the omp download failed: HTTPError" in result.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="the step is a POSIX shell script")
def test_a_binary_that_installs_but_does_not_run_counts_as_a_failed_install(tmp_path: Path) -> None:
    broken = tmp_path / "omp"
    broken.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    broken.chmod(0o755)
    result, output, env = _run_install_step(tmp_path, f"echo {broken}")
    assert result.returncode == 0
    assert output == "state=failed\n"
    assert env == ""


@pytest.mark.skipif(sys.platform == "win32", reason="the step is a POSIX shell script")
def test_a_working_install_exports_the_binary_and_reports_installed(tmp_path: Path) -> None:
    working = tmp_path / "omp"
    working.write_text("#!/bin/sh\necho 18.12.1\n", encoding="utf-8")
    working.chmod(0o755)
    result, output, env = _run_install_step(tmp_path, f"echo {working}")
    assert result.returncode == 0
    assert output == "state=installed\n"
    assert env == f"ORACLE_OMP_BINARY={working}\n"


def test_a_failed_install_stops_the_run_before_any_attempt_and_is_reported_to_finalize() -> None:
    install = _step("Install omp")
    assert install["id"] == "omp"
    assert "continue-on-error" not in install
    assert "steps.omp.outputs.state == 'installed'" in _step(LOOP_STEP)["if"]
    finalize = _step("Validate this run's attempts and decide")
    assert finalize["env"]["OMP_INSTALL_STATE"] == "${{ steps.omp.outputs.state }}"
    assert '--omp-install "${OMP_INSTALL_STATE:-installed}"' in finalize["run"]


def test_the_security_checks_and_the_loop_fail_the_job_closed() -> None:
    for name in FAIL_CLOSED_STEPS:
        step = _step(name)
        assert "continue-on-error" not in step, name
        assert "always()" not in step.get("if", ""), name
        assert "cancelled()" not in step.get("if", ""), name


def test_linux_isolation_runs_immediately_before_the_loop_and_fails_closed() -> None:
    name = "Verify Linux process isolation"
    step = _step(name)
    assert _names()[_names().index(LOOP_STEP) - 1] == name
    assert step["if"] == "steps.plan.outputs.decision == 'review'"
    assert step["working-directory"] == "trusted"
    assert step["run"] == "python3 -m _project.scripts.oracle_reviewers.isolation"
    assert "continue-on-error" not in step


def test_attempt_and_retry_state_artifacts_keep_their_names_and_retention() -> None:
    names = _names()
    posts = max(
        names.index(name)
        for name in ("Post the commit status", "Post the findings comment", "Post the findings review")
    )
    for slot in range(1, POLICY.max_attempts + 1):
        upload = _step(f"Upload attempt {slot}")
        assert upload["with"]["name"] == f"oracle-verdict-attempt-{slot}"
        assert upload["with"]["path"] == f"${{{{ runner.temp }}}}/oracle-attempts/attempt-{slot}.json"
        assert upload["with"]["retention-days"] == 7
        assert names.index(upload["name"]) > names.index(LOOP_STEP)
        assert "steps.redact.outcome == 'success'" in upload["if"]
        assert f"steps.count.outputs.attempts >= {slot}" in upload["if"]
        assert upload["if"].startswith("${{ always()")
    state = _step("Upload the retry state")
    assert state["with"]["name"] == "oracle-verdict-state-${{ steps.plan.outputs.pr }}"
    assert state["with"]["retention-days"] == 7
    assert names.index(state["name"]) > posts
    assert "always()" not in state["if"]
    assert state["if"] == "steps.status.outcome == 'success' && steps.finalize.outputs.has_state == 'true'"
    assert _step("Post the commit status")["id"] == "status"


def test_the_attempt_loop_and_redaction_steps_have_the_ids_the_uploads_read() -> None:
    assert _step("Count the attempts")["id"] == "count"
    assert _step(REDACT_STEP)["id"] == "redact"
    assert _step("Validate this run's attempts and decide")["id"] == "finalize"


def test_the_capture_upload_waits_for_the_redaction_step_and_keeps_its_name_and_retention() -> None:
    upload = _step("Upload the captured reviewer event streams")
    assert upload["with"]["name"] == "oracle-verdict-capture"
    assert upload["with"]["path"] == "${{ runner.temp }}/oracle-attempts/captures/"
    assert upload["with"]["retention-days"] == 30
    assert _names().index(upload["name"]) > _names().index(REDACT_STEP)
    assert upload["if"].startswith("${{ always()")
    assert "steps.redact.outcome == 'success'" in upload["if"]
    assert "steps.count.outputs.attempts >= 1" in upload["if"]


def test_the_post_steps_mint_the_app_token_and_tolerate_missing_secrets() -> None:
    env = _review()["env"]
    assert env["HAS_APP"] == "${{ secrets.ORACLE_APP_ID != '' }}"
    mint = _step("Mint the App token")
    assert mint["uses"] == "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1"
    assert mint["if"] == "steps.plan.outputs.post == 'true' && env.HAS_APP == 'true'"
    assert mint["with"]["permission-statuses"] == "write"
    assert mint["with"]["permission-pull-requests"] == "write"
    absent = _step("Log the result when the App is not configured")
    assert absent["if"] == "steps.plan.outputs.post == 'true' && env.HAS_APP != 'true'"
    for name in ("Skip a result this head already has", "Post the commit status", "Post the findings comment"):
        step = _step(name)
        assert step["env"]["GH_TOKEN"] == "${{ steps.app.outputs.token }}"
        assert "env.HAS_APP == 'true'" in step["if"]
    assert "cli finalize" in _run("Validate this run's attempts and decide")
    assert not any(str(step.get("uses", "")).startswith("actions/download-artifact@") for step in _steps())


def test_every_post_step_requires_the_plan_to_post() -> None:
    for name in (
        "Skip a result this head already has",
        "Post the commit status",
        "Post the findings comment",
        "Post the findings review",
        DISPATCH_STEP,
        "Mint the App token",
    ):
        assert "steps.plan.outputs.post == 'true'" in _step(name)["if"], name
        assert "cancelled()" not in _step(name)["if"], name


def test_status_is_posted_on_the_api_resolved_head() -> None:
    step = _step("Post the commit status")
    assert step["env"]["HEAD_SHA"] == "${{ steps.plan.outputs.head_sha }}"
    assert "repos/$REPO/statuses/$HEAD_SHA" in step["run"]
    assert _review()["env"]["REPO"] == "${{ github.repository }}"


def test_actions_are_pinned_to_full_shas() -> None:
    for name, job in _jobs().items():
        for step in job["steps"]:
            if "uses" in step:
                assert SHA_PIN.match(step["uses"]), (name, step["uses"])


def test_workflow_has_no_comments() -> None:
    assert not any(line.lstrip().startswith("#") for line in _text_of_workflow().splitlines())


def test_reviewer_commands_keep_read_only_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(omp_pin.BINARY_ENV, sys.executable)
    for name, reviewer in POLICY.reviewers.items():
        argv = build(reviewer, tmp_path, "prompt", tmp_path).argv
        assert argv[argv.index("--tools") + 1] == "read,grep,glob", name
        assert argv[argv.index("--approval-mode") + 1] == "always-ask", name
        assert reviewer.model in argv, name


def test_the_post_steps_recheck_the_review_list_before_posting() -> None:
    assert "Skip a result this head already has" in _names()
    for name in ("Post the commit status", "Post the findings comment", "Post the findings review", DISPATCH_STEP):
        assert "steps.guard.outputs.skip != 'true'" in _step(name)["if"], name


def test_no_step_lists_or_reruns_oracle_review_runs() -> None:
    text = _text_of_workflow()
    assert "oracle-review.yml" not in text
    assert "gh run list" not in text
    assert "gh run rerun" not in text


def test_the_retry_state_is_stored_only_after_every_review_post_succeeds() -> None:
    names = _names()
    upload = names.index("Upload the retry state")
    for name in ("Post the commit status", "Post the findings comment", "Post the findings review"):
        assert names.index(name) < upload, name


def test_the_retry_attempt_is_recorded_before_any_post_without_the_strike() -> None:
    names = _names()
    record = _step("Record the retry attempt")
    for name in ("Post the commit status", "Post the findings comment", "Post the findings review"):
        assert names.index("Record the retry attempt") < names.index(name), name
    assert record["with"]["path"].endswith("oracle-final/attempt/state.json")
    published = _step("Upload the retry state")["with"]["name"]
    assert record["with"]["name"] == "oracle-verdict-state-attempt-${{ steps.plan.outputs.pr }}"
    assert record["with"]["name"] != published
    assert "steps.guard.outputs.skip != 'true'" in record["if"]
    assert "always()" in record["if"]
    assert "steps.redact.outcome == 'success'" in record["if"]
    assert "HAS_APP" not in record["if"]
    assert record["with"]["overwrite"] is True and record["with"]["if-no-files-found"] == "ignore"


def test_the_strike_state_is_published_beside_the_attempt_state_and_needs_a_posted_status() -> None:
    state = _step("Upload the retry state")
    assert "steps.status.outcome == 'success'" in state["if"]
    assert state["with"]["overwrite"] is True
    assert state["with"]["path"].endswith("oracle-final/state/state.json")


def test_only_sweep_and_review_hold_actions_write() -> None:
    holders = {name for name, job in _jobs().items() if job.get("permissions", {}).get("actions") == "write"}
    assert holders == {"sweep", "review"}


def test_the_gate_is_dispatched_last_and_only_after_the_review_is_posted() -> None:
    names = _names()
    assert names[-3:] == [DISPATCH_STEP, SHADOW_COMPARE_STEP, "Upload the shadow comparison"]
    for name in ("Post the findings review", "Post the commit status", "Post the findings comment"):
        assert names.index(DISPATCH_STEP) > names.index(name)
    step = _step(DISPATCH_STEP)
    assert (
        step["if"] == "steps.plan.outputs.post == 'true' && env.HAS_APP == 'true' && steps.guard.outputs.skip != 'true'"
    )
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"


def test_the_dispatch_targets_develop_with_the_reviewed_head() -> None:
    run = _run(DISPATCH_STEP)
    assert 'gh api "repos/$REPO/pulls/$PR_NUMBER" --jq .base.ref' in run
    assert 'if [ "$base" = develop ]; then' in run
    assert (
        'gh workflow run oracle-gate.yml --repo "$REPO" --ref develop -f pr="$PR_NUMBER" -f head_sha="$HEAD_SHA"' in run
    )
    assert run.index("if [") < run.index("gh workflow run")
    assert "statuses/" not in run


def test_only_the_review_job_dispatches_the_gate() -> None:
    dispatchers = [
        (name, step["name"])
        for name, job in _jobs().items()
        for step in job["steps"]
        if "gh workflow run oracle-gate.yml" in step.get("run", "")
    ]
    assert dispatchers == [("review", DISPATCH_STEP)]


def _context(
    event_name: str, event: dict[str, Any] | None = None, inputs: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {"github": {"event_name": event_name, "event": event or {}, "run_id": "55"}, "inputs": inputs or {}}


def _cancels(event_name: str) -> Any:
    expression = str(_load()["concurrency"]["cancel-in-progress"]).removeprefix("${{").removesuffix("}}")
    return evaluate(expression, _context(event_name))


def test_develop_push_runs_the_sweep_and_no_review() -> None:
    context = _context("push")
    assert evaluate(_jobs()["sweep"]["if"], context)
    assert not evaluate(_review()["if"], context)


def test_push_and_schedule_sweeps_share_one_group_that_never_cancels() -> None:
    group = _load()["concurrency"]["group"]
    pushed = render(group, _context("push"))
    assert pushed == render(group, _context("schedule"))
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


def test_the_dispatch_reason_input_is_optional_and_unset_by_default() -> None:
    inputs = _load()[True]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"pr", "head", "reason"}
    assert inputs["pr"]["required"] is True
    assert inputs["head"]["required"] is False
    assert inputs["reason"]["required"] is False


def _run_title(event_name: str, event: dict[str, Any] | None = None, inputs: dict[str, Any] | None = None) -> str:
    context = _context(event_name, event, inputs)
    return re.sub(r"\$\{\{(.*?)\}\}", lambda match: str(evaluate(match.group(1), context) or ""), _load()["run-name"])


SHA = "c" * 40


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
    match = RUN_TITLE_PULL.search(rendered)
    assert (match is not None) == (event_name != "schedule")


def test_pull_request_groups_keep_one_group_per_number() -> None:
    group = _load()["concurrency"]["group"]
    assert render(group, _context("workflow_dispatch", inputs={"pr": "7"})) == "oracle-verdict-pr-7"
    event = {"action": "synchronize", "pull_request": {"number": 7}}
    assert render(group, _context("pull_request_target", event)) == "oracle-verdict-pr-7"
